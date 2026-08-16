from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .closure import (
    DEFAULT_CLOSED_EDGE,
    DEFAULT_CLOSURE_TIME_S,
    ClosureMetrics,
    _load_sumo_modules,
    _read_summary,
    _read_trips,
    run_closure,
)
from .commands import MissingSumoError, find_sumo_binary
from .config import SimulationConfig
from .demand import generate_demand
from .metrics import Summary, collect_results, write_results, write_summary
from .network import sha256_file


VEHICLE_COUNTS = (1_000, 2_000, 4_000, 6_000)
STEP4_VEHICLE_COUNTS = (2_500, 2_750, 3_000, 3_250, 3_500)


@dataclass(frozen=True)
class BaselineMetrics:
    summary: Summary
    max_congestion: int
    teleports: int


def _teleports(path: Path) -> int:
    root = ET.parse(path).getroot()
    element = root.find("teleports")
    return 0 if element is None else int(element.get("total", "0"))


def _queue_edges(network: object, edge_id: str) -> tuple[str, ...]:
    edge = network.getEdge(edge_id)  # type: ignore[attr-defined]
    return tuple(dict.fromkeys([edge_id, *(item.getID() for item in edge.getIncoming())]))


def _run_observed_baseline(config: SimulationConfig) -> BaselineMetrics:
    traci, sumolib = _load_sumo_modules()
    network = sumolib.net.readNet(str(config.network_file))
    observed_edges = _queue_edges(network, DEFAULT_CLOSED_EDGE)
    config.run_dir.mkdir(parents=True, exist_ok=True)
    command = [
        find_sumo_binary("sumo"),
        "--net-file", str(config.network_file),
        "--route-files", str(config.routes_file),
        "--seed", str(config.seed),
        "--threads", "1",
        "--end", str(config.simulation_end_s),
        "--tripinfo-output", str(config.tripinfo_file),
        "--tripinfo-output.write-unfinished", "true",
        "--tripinfo-output.write-undeparted", "true",
        "--statistic-output", str(config.statistics_file),
        "--no-step-log", "true",
        "--duration-log.statistics", "true",
        "--no-warnings", "true",
    ]
    max_congestion = 0
    connection = None
    try:
        traci.start(command, label="shield-calibration-baseline", stdout=subprocess.PIPE)
        connection = traci.getConnection("shield-calibration-baseline")
        while (
            connection.simulation.getMinExpectedNumber() > 0
            and connection.simulation.getTime() < config.simulation_end_s
        ):
            connection.simulationStep()
            occupancy = sum(
                connection.edge.getLastStepVehicleNumber(edge) for edge in observed_edges
            )
            max_congestion = max(max_congestion, occupancy)
    finally:
        if connection is not None:
            connection.close()

    trips = _read_trips(config.routes_file)
    results, summary = collect_results(trips, config.tripinfo_file)
    write_results(config.result_file, results)
    write_summary(
        config.summary_file,
        summary,
        {
            "seed": config.seed,
            "demand_duration_seconds": config.demand_duration_s,
            "simulation_end_seconds": config.simulation_end_s,
            "maximum_congestion_near_target": max_congestion,
            "teleports": _teleports(config.statistics_file),
            "network_sha256": sha256_file(config.network_file),
            "demand_sha256": sha256_file(config.routes_file),
        },
    )
    return BaselineMetrics(summary, max_congestion, _teleports(config.statistics_file))


def _prepare_scenario(root: Path, vehicles: int) -> SimulationConfig:
    scenario = root / "outputs" / "calibration" / f"vehicles-{vehicles}"
    data_link = scenario / "data"
    scenario.mkdir(parents=True, exist_ok=True)
    if not data_link.exists():
        data_link.symlink_to(root / "data", target_is_directory=True)
    return SimulationConfig(project_dir=scenario, vehicles=vehicles, seed=42)


def _summary_from_csv(path: Path) -> tuple[Summary, dict[str, str]]:
    values = _read_summary(path)
    return (
        Summary(
            vehicles=int(values["vehicles"]),
            completed=int(values["completed_trips"]),
            unfinished=int(values["unfinished_or_stranded_trips"]),
            mean_travel_time_s=float(values["mean_travel_time"]),
            median_travel_time_s=float(values["median_travel_time"]),
        ),
        values,
    )


def run_calibration(
    root: Path,
    resume: bool = False,
    vehicle_counts: tuple[int, ...] = VEHICLE_COUNTS,
    output_name: str = "density_calibration.csv",
    baseline_only: bool = False,
) -> Path:
    rows: list[dict[str, object]] = []
    for vehicles in vehicle_counts:
        config = _prepare_scenario(root, vehicles)
        print(f"\n[{vehicles:,} vehicles] Generating fixed-seed demand...", flush=True)
        if not resume or not config.routes_file.exists():
            trips = generate_demand(config)
            if len(trips) != vehicles:
                raise RuntimeError(f"generated {len(trips)} trips, expected {vehicles}")
        demand_hash = sha256_file(config.routes_file)

        if resume and config.summary_file.exists():
            baseline_summary, baseline_values = _summary_from_csv(config.summary_file)
            baseline = BaselineMetrics(
                baseline_summary,
                int(baseline_values["maximum_congestion_near_target"]),
                int(baseline_values["teleports"]),
            )
            print(f"[{vehicles:,} vehicles] Reusing completed baseline.", flush=True)
        else:
            print(f"[{vehicles:,} vehicles] Running observed baseline...", flush=True)
            baseline = _run_observed_baseline(config)

        if baseline_only:
            rows.append(
                {
                    "vehicles": vehicles,
                    "seed": config.seed,
                    "demand_sha256": demand_hash,
                    "mean_travel_time_s": baseline.summary.mean_travel_time_s,
                    "median_travel_time_s": baseline.summary.median_travel_time_s,
                    "completed_trips": baseline.summary.completed,
                    "unfinished_trips": baseline.summary.unfinished,
                    "completion_percent": 100 * baseline.summary.completed / vehicles,
                    "maximum_congestion": baseline.max_congestion,
                    "teleports": baseline.teleports,
                }
            )
            print(
                f"[{vehicles:,} vehicles] baseline completion="
                f"{baseline.summary.completed}/{vehicles} "
                f"teleports={baseline.teleports}",
                flush=True,
            )
            continue

        closure_summary_file = config.project_dir / "results" / "closure_run_summary.csv"
        if resume and closure_summary_file.exists():
            closure, closure_values = _summary_from_csv(closure_summary_file)
            extra = ClosureMetrics(
                int(closure_values["vehicles_directly_affected"]),
                int(closure_values["rerouted_vehicles"]),
                int(closure_values["maximum_queue_near_closure"]),
                tuple(closure_values["queue_edge_ids"].split()),
                int(closure_values["maximum_congestion_on_alternative_routes"]),
                tuple(closure_values["alternative_edge_ids"].split()),
            )
            print(f"[{vehicles:,} vehicles] Reusing completed closure.", flush=True)
        else:
            print(f"[{vehicles:,} vehicles] Running t=900s closure...", flush=True)
            closure, extra = run_closure(config)
        closure_statistics = (
            config.project_dir / "outputs" / "seed-42-closure" / "statistics.xml"
        )
        closure_teleports = _teleports(closure_statistics)
        impact = (
            None
            if baseline.summary.mean_travel_time_s is None
            or closure.mean_travel_time_s is None
            else 100
            * (closure.mean_travel_time_s - baseline.summary.mean_travel_time_s)
            / baseline.summary.mean_travel_time_s
        )
        rows.append(
            {
                "vehicles": vehicles,
                "seed": config.seed,
                "demand_sha256": demand_hash,
                "baseline_mean_travel_time_s": baseline.summary.mean_travel_time_s,
                "closure_mean_travel_time_s": closure.mean_travel_time_s,
                "closure_impact_percent": impact,
                "baseline_median_travel_time_s": baseline.summary.median_travel_time_s,
                "closure_median_travel_time_s": closure.median_travel_time_s,
                "baseline_completed_trips": baseline.summary.completed,
                "closure_completed_trips": closure.completed,
                "rerouted_vehicles": extra.rerouted,
                "baseline_maximum_congestion": baseline.max_congestion,
                "closure_maximum_congestion": extra.max_queue_vehicles,
                "baseline_teleports": baseline.teleports,
                "closure_teleports": closure_teleports,
            }
        )
        print(
            f"[{vehicles:,} vehicles] impact={impact:.2f}% "
            f"completed={baseline.summary.completed}/{closure.completed}",
            flush=True,
        )

    output = root / "results" / output_name
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Step 3 density calibration.")
    parser.add_argument("--resume", action="store_true", help="reuse completed paired runs")
    parser.add_argument(
        "--step4",
        action="store_true",
        help="run the requested 2,500–3,500 vehicle intermediate sweep",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="generate weighted demand and run baselines without a closure",
    )
    parser.add_argument(
        "--counts",
        help="comma-separated vehicle counts (overrides the default sweep)",
    )
    args = parser.parse_args(argv)
    try:
        if args.counts:
            counts = tuple(int(item.strip()) for item in args.counts.split(","))
            if not counts or any(item <= 0 for item in counts):
                raise ValueError("counts must be positive integers")
        else:
            counts = STEP4_VEHICLE_COUNTS if args.step4 else VEHICLE_COUNTS
        output_name = (
            "density_baseline_calibration.csv"
            if args.baseline_only
            else "density_calibration_step4.csv"
            if args.step4
            else "density_calibration.csv"
        )
        output = run_calibration(
            Path.cwd(),
            resume=args.resume,
            vehicle_counts=counts,
            output_name=output_name,
            baseline_only=args.baseline_only,
        )
    except (ValueError, RuntimeError, MissingSumoError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"\nCalibration complete: {output.relative_to(Path.cwd())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
