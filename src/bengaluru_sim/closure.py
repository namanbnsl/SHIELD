from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from importlib.metadata import distribution
from pathlib import Path
from typing import Any

from .commands import MissingSumoError, find_sumo_binary
from .config import SimulationConfig
from .demand import Trip
from .metrics import Summary, collect_results, write_results, write_summary
from .network import sha256_file


DEFAULT_CLOSED_EDGE = "377105483#0"
DEFAULT_CLOSURE_TIME_S = 900.0


@dataclass(frozen=True)
class ClosureMetrics:
    directly_affected: int
    rerouted: int
    max_queue_vehicles: int
    queue_edge_ids: tuple[str, ...]
    max_alternative_congestion: int
    alternative_edge_ids: tuple[str, ...]


def _load_sumo_modules() -> tuple[Any, Any]:
    tools = distribution("eclipse-sumo").locate_file("sumo/tools")
    if str(tools) not in sys.path:
        sys.path.append(str(tools))
    import sumolib  # type: ignore[import-not-found]
    import traci  # type: ignore[import-not-found]

    return traci, sumolib


def _read_trips(path: Path) -> list[Trip]:
    trips: list[Trip] = []
    for vehicle in ET.parse(path).getroot().findall("vehicle"):
        route = vehicle.find("route")
        edges = tuple(route.get("edges", "").split()) if route is not None else ()
        if not edges:
            continue
        trips.append(
            Trip(
                vehicle_id=vehicle.get("id", ""),
                scheduled_departure_s=float(vehicle.get("depart", "0")),
                origin_edge=edges[0],
                destination_edge=edges[-1],
                route_edges=edges,
            )
        )
    return trips


def _comparison_file(project_dir: Path) -> Path:
    return project_dir / "results" / "closure_comparison.csv"


def _write_comparison(
    path: Path,
    baseline: dict[str, str],
    closure: Summary,
    extra: ClosureMetrics,
    closure_teleports: int,
) -> None:
    rows = [
        ("vehicles", baseline.get("vehicles", ""), closure.vehicles, "count"),
        ("completed_trips", baseline.get("completed_trips", ""), closure.completed, "count"),
        ("teleports", baseline.get("teleports", ""), closure_teleports, "count"),
        (
            "unfinished_or_stranded_trips",
            baseline.get("unfinished_or_stranded_trips", ""),
            closure.unfinished,
            "count",
        ),
        (
            "mean_travel_time",
            baseline.get("mean_travel_time", ""),
            "" if closure.mean_travel_time_s is None else f"{closure.mean_travel_time_s:.2f}",
            "seconds",
        ),
        (
            "median_travel_time",
            baseline.get("median_travel_time", ""),
            "" if closure.median_travel_time_s is None else f"{closure.median_travel_time_s:.2f}",
            "seconds",
        ),
        ("vehicles_directly_affected", "0", extra.directly_affected, "count"),
        ("rerouted_vehicles", "0", extra.rerouted, "count"),
        (
            "maximum_queue_near_closure",
            baseline.get("maximum_congestion_near_target", ""),
            extra.max_queue_vehicles,
            "vehicles",
        ),
        (
            "maximum_congestion_on_alternative_routes",
            "",
            extra.max_alternative_congestion,
            "vehicles",
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "baseline", "road_closure", "unit"])
        writer.writerows(rows)


def _read_summary(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["metric"]: row["value"] for row in csv.DictReader(handle)}


def run_closure(
    config: SimulationConfig,
    edge_id: str = DEFAULT_CLOSED_EDGE,
    closure_time_s: float = DEFAULT_CLOSURE_TIME_S,
) -> tuple[Summary, ClosureMetrics]:
    config.validate()
    if not config.network_file.exists() or not config.routes_file.exists():
        raise RuntimeError("Baseline inputs are missing. Run 'uv run shield-sim' first.")
    if not config.summary_file.exists():
        raise RuntimeError("Baseline summary is missing. Run 'uv run shield-sim' first.")
    if not 0 < closure_time_s < config.simulation_end_s:
        raise ValueError("closure time must fall within the simulation")

    trips = _read_trips(config.routes_file)
    original_routes = {trip.vehicle_id: trip.route_edges for trip in trips}
    traci, sumolib = _load_sumo_modules()
    network = sumolib.net.readNet(str(config.network_file))
    try:
        closed_edge = network.getEdge(edge_id)
    except KeyError as error:
        raise ValueError(f"unknown closure edge: {edge_id}") from error
    queue_edges = tuple(
        dict.fromkeys([edge_id, *(edge.getID() for edge in closed_edge.getIncoming())])
    )

    closure_dir = config.project_dir / "outputs" / f"seed-{config.seed}-closure"
    closure_dir.mkdir(parents=True, exist_ok=True)
    tripinfo_file = closure_dir / "tripinfo.xml"
    statistics_file = closure_dir / "statistics.xml"
    log_file = closure_dir / "sumo.log"
    command = [
        find_sumo_binary("sumo"),
        "--net-file", str(config.network_file),
        "--route-files", str(config.routes_file),
        "--seed", str(config.seed),
        "--threads", "1",
        "--end", str(config.simulation_end_s),
        "--tripinfo-output", str(tripinfo_file),
        "--tripinfo-output.write-unfinished", "true",
        "--tripinfo-output.write-undeparted", "true",
        "--statistic-output", str(statistics_file),
        "--no-step-log", "true",
        "--duration-log.statistics", "true",
        "--no-warnings", "true",
    ]

    directly_affected: set[str] = set()
    rerouted: set[str] = set()
    reroute_attempted: set[str] = set()
    alternative_edge_counts: Counter[str] = Counter()
    max_queue = 0
    max_alternative_congestion = 0
    closed = False
    connection = None
    try:
        traci.start(command, label="shield-closure", stdout=subprocess.PIPE)
        connection = traci.getConnection("shield-closure")
        while (
            connection.simulation.getMinExpectedNumber() > 0
            and connection.simulation.getTime() < config.simulation_end_s
        ):
            connection.simulationStep()
            now = connection.simulation.getTime()
            newly_closed = False
            if not closed and now >= closure_time_s:
                for lane in closed_edge.getLanes():
                    connection.lane.setMaxSpeed(lane.getID(), 0.01)
                closed = True
                newly_closed = True

            if closed:
                vehicle_ids = (
                    connection.vehicle.getIDList()
                    if newly_closed
                    else connection.simulation.getDepartedIDList()
                )
                for vehicle_id in vehicle_ids:
                    original = original_routes.get(vehicle_id, ())
                    if edge_id not in original:
                        continue
                    route = tuple(connection.vehicle.getRoute(vehicle_id))
                    route_index = connection.vehicle.getRouteIndex(vehicle_id)
                    original_index = original.index(edge_id)
                    if route == original and route_index <= original_index:
                        directly_affected.add(vehicle_id)
                        if vehicle_id in reroute_attempted:
                            continue
                        reroute_attempted.add(vehicle_id)
                        before = route
                        try:
                            connection.vehicle.rerouteTraveltime(vehicle_id)
                        except traci.TraCIException:
                            continue
                        after = tuple(connection.vehicle.getRoute(vehicle_id))
                        if after != before and edge_id not in after[max(route_index, 0):]:
                            rerouted.add(vehicle_id)

                        original_remaining = set(original[max(route_index, 0):])
                        alternative_edge_counts.update(
                            candidate
                            for candidate in after[max(route_index, 0):]
                            if not candidate.startswith(":")
                            and candidate not in original_remaining
                        )

                queued = sum(connection.edge.getLastStepVehicleNumber(edge) for edge in queue_edges)
                max_queue = max(max_queue, queued)
                alternative_edges = [
                    edge
                    for edge, _ in alternative_edge_counts.most_common(32)
                ]
                alternative_congestion = sum(
                    connection.edge.getLastStepVehicleNumber(edge)
                    for edge in alternative_edges
                )
                max_alternative_congestion = max(
                    max_alternative_congestion, alternative_congestion
                )
    finally:
        if connection is not None:
            connection.close()

    log_file.write_text("TraCI-controlled run completed.\n", encoding="utf-8")
    results, summary = collect_results(trips, tripinfo_file)
    statistics_root = ET.parse(statistics_file).getroot()
    teleports_node = statistics_root.find("teleports")
    closure_teleports = (
        0 if teleports_node is None else int(teleports_node.get("total", "0"))
    )
    result_file = config.project_dir / "results" / "closure_run.csv"
    summary_file = config.project_dir / "results" / "closure_run_summary.csv"
    write_results(result_file, results)
    extra = ClosureMetrics(
        len(directly_affected),
        len(rerouted),
        max_queue,
        queue_edges,
        max_alternative_congestion,
        tuple(edge for edge, _ in alternative_edge_counts.most_common(32)),
    )
    metadata = {
        "seed": config.seed,
        "demand_duration_seconds": config.demand_duration_s,
        "simulation_end_seconds": config.simulation_end_s,
        "closure_time_seconds": closure_time_s,
        "closed_edge_id": edge_id,
        "closed_edge_name": closed_edge.getName(),
        "queue_edge_ids": " ".join(queue_edges),
        "vehicles_directly_affected": extra.directly_affected,
        "rerouted_vehicles": extra.rerouted,
        "teleports": closure_teleports,
        "maximum_queue_near_closure": extra.max_queue_vehicles,
        "maximum_congestion_on_alternative_routes": extra.max_alternative_congestion,
        "alternative_edge_ids": " ".join(extra.alternative_edge_ids),
        "network_sha256": sha256_file(config.network_file),
        "demand_sha256": sha256_file(config.routes_file),
    }
    write_summary(summary_file, summary, metadata)
    _write_comparison(
        _comparison_file(config.project_dir),
        _read_summary(config.summary_file),
        summary,
        extra,
        closure_teleports,
    )
    return summary, extra


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the controlled Step 2 road closure.")
    parser.add_argument("--edge", default=DEFAULT_CLOSED_EDGE)
    parser.add_argument("--closure-time", type=float, default=DEFAULT_CLOSURE_TIME_S)
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--vehicles", type=int, default=1_000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = SimulationConfig(project_dir=args.project_dir, vehicles=args.vehicles)
    try:
        summary, extra = run_closure(config, args.edge, args.closure_time)
    except (ValueError, RuntimeError, MissingSumoError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    mean = "n/a" if summary.mean_travel_time_s is None else f"{summary.mean_travel_time_s:.2f}s"
    print("Road-closure simulation complete.")
    print(f"Completed trips: {summary.completed}")
    print(f"Mean travel time: {mean}")
    print(f"Vehicles directly affected: {extra.directly_affected}")
    print(f"Rerouted vehicles: {extra.rerouted}")
    print(f"Maximum queue near closure: {extra.max_queue_vehicles} vehicles")
    print(
        "Maximum congestion on alternative routes: "
        f"{extra.max_alternative_congestion} vehicles"
    )
    print("Comparison saved to results/closure_comparison.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
