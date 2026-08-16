from __future__ import annotations

import argparse
import csv
import statistics
import subprocess
import sys
from pathlib import Path

from .closure import (
    DEFAULT_CLOSED_EDGE,
    DEFAULT_CLOSURE_TIME_S,
    ClosureMetrics,
    run_closure,
)
from .commands import MissingSumoError
from .config import DEFAULT_SEED, SimulationConfig, project_result_path
from .demand import generate_demand, read_trips
from .experiments import prepare_scenario, run_tasks, scenario_dir
from .metrics import (
    Summary,
    collect_results,
    mean_or_zero,
    median_or_zero,
    quartile,
    read_summary,
    read_teleports,
    write_dict_rows,
)
from .network import sha256_file


DEFAULT_START_SEED = DEFAULT_SEED
DEFAULT_SEED_COUNT = 20
DEFAULT_FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)


def _fraction_label(fraction: float) -> str:
    return f"{round(fraction * 100):03d}"


def _prepare_scenario(project_dir: Path, vehicles: int, seed: int) -> SimulationConfig:
    config = prepare_scenario(project_dir, "information-penetration", vehicles, seed)
    if not config.routes_file.exists():
        source_config = SimulationConfig(
            project_dir=scenario_dir(
                project_dir, "information-sweep", vehicles, seed
            ),
            vehicles=vehicles,
            seed=seed,
        )
        source_run = source_config.run_dir
        if source_run.exists():
            config.run_dir.parent.mkdir(parents=True, exist_ok=True)
            config.run_dir.symlink_to(source_run, target_is_directory=True)
        else:
            print(f"[{seed}] Generating weighted demand...", flush=True)
            trips = generate_demand(config)
            if len(trips) != vehicles:
                raise RuntimeError(
                    f"seed {seed}: generated {len(trips)} trips, expected {vehicles}"
                )
    trips = read_trips(config.routes_file)
    if len(trips) != vehicles:
        raise RuntimeError(
            f"seed {seed}: existing demand has {len(trips)} trips, expected {vehicles}"
        )
    return config


def _prepare_task(task: tuple[Path, int, int]) -> None:
    _prepare_scenario(*task)


def _read_comparison(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _summary_from_saved_partial(
    config: SimulationConfig, fraction: float
) -> tuple[Summary, ClosureMetrics, int]:
    label = _fraction_label(fraction)
    output_prefix = f"information-penetration-{label}"
    _, summary_path = config.named_result_paths(output_prefix)
    values = read_summary(summary_path)
    trips = read_trips(config.routes_file)
    _, summary = collect_results(
        trips,
        config.output_dir(output_prefix) / "tripinfo.xml",
        simulation_end_s=config.simulation_end_s,
    )
    extra = ClosureMetrics(
        int(values.get("vehicles_directly_affected", "0")),
        int(values.get("rerouted_vehicles", "0")),
        int(values.get("maximum_queue_near_closure", "0")),
        tuple(values.get("queue_edge_ids", "").split()),
        int(values.get("maximum_congestion_on_alternative_routes", "0")),
        tuple(values.get("alternative_edge_ids", "").split()),
        int(values.get("alternative_routes_observed", "0")),
        int(values.get("alternative_unique_edge_count", "0")),
        float(values.get("alternative_top_edge_share", "0")),
        float(values.get("alternative_edge_hhi", "0")),
        float(values.get("alternative_top_route_share", "0")),
    )
    return (
        summary,
        extra,
        read_teleports(config.output_dir(output_prefix) / "statistics.xml"),
    )


def _row_from_metrics(
    config: SimulationConfig,
    edge_id: str,
    closure_time_s: float,
    fraction: float,
    policy: str,
    summary: Summary,
    extra: ClosureMetrics,
    teleports: int,
) -> dict[str, object]:
    return {
        "seed": config.seed,
        "vehicles": summary.vehicles,
        "informed_fraction": fraction,
        "informed_percent": fraction * 100,
        "policy": policy,
        "informed_vehicles": round(fraction * summary.vehicles),
        "closure_time_s": closure_time_s,
        "closed_edge_id": edge_id,
        "network_sha256": sha256_file(config.network_file),
        "demand_sha256": sha256_file(config.routes_file),
        "total_time_loss_s": summary.total_time_loss_s,
        "mean_time_loss_s": summary.mean_time_loss_s,
        "mean_travel_time_s": summary.mean_travel_time_s,
        "median_travel_time_s": summary.median_travel_time_s,
        "completed_trips": summary.completed,
        "teleports": teleports,
        "directly_affected_vehicles": extra.directly_affected,
        "rerouted_vehicles": extra.rerouted,
        "maximum_queue_near_closure": extra.max_queue_vehicles,
        "maximum_congestion_on_alternative_routes": extra.max_alternative_congestion,
        "alternative_edge_count": len(extra.alternative_edge_ids),
    }


def _source_endpoint_row(
    project_dir: Path,
    vehicles: int,
    seed: int,
    edge_id: str,
    closure_time_s: float,
    fraction: float,
) -> dict[str, object] | None:
    source_project = scenario_dir(project_dir, "information-sweep", vehicles, seed)
    source = project_result_path(source_project, "information_comparison.csv")
    if not source.exists():
        return None
    rows = _read_comparison(source)
    expected_policy = "minimal" if fraction == 0.0 else "global"
    selected = next(
        (row for row in rows if row.get("strategy") == expected_policy), None
    )
    if selected is None:
        return None
    if (
        int(selected["vehicles"]) != vehicles
        or float(selected["closure_time_s"]) != closure_time_s
        or selected["closed_edge_id"] != edge_id
        or "total_time_loss_s" not in selected
    ):
        return None
    return {
        "seed": seed,
        "vehicles": vehicles,
        "informed_fraction": fraction,
        "informed_percent": fraction * 100,
        "policy": expected_policy,
        "informed_vehicles": 0 if fraction == 0.0 else vehicles,
        "closure_time_s": closure_time_s,
        "closed_edge_id": edge_id,
        "network_sha256": selected["network_sha256"],
        "demand_sha256": selected["demand_sha256"],
        "total_time_loss_s": float(selected["total_time_loss_s"]),
        "mean_time_loss_s": float(selected["mean_time_loss_s"]),
        "mean_travel_time_s": float(selected["mean_travel_time_s"]),
        "median_travel_time_s": float(selected["median_travel_time_s"]),
        "completed_trips": int(selected["completed_trips"]),
        "teleports": int(selected["teleports"]),
        "directly_affected_vehicles": int(selected["directly_affected_vehicles"]),
        "rerouted_vehicles": int(selected["rerouted_vehicles"]),
        "maximum_queue_near_closure": int(selected["maximum_queue_near_closure"]),
        "maximum_congestion_on_alternative_routes": int(
            selected["maximum_congestion_on_alternative_routes"]
        ),
        "alternative_edge_count": int(selected["alternative_edge_count"]),
    }


def _run_seed_fraction(
    task: tuple[Path, int, int, float, str, float, bool]
) -> dict[str, object]:
    project_dir, vehicles, seed, fraction, edge_id, closure_time_s, resume = task
    config = _prepare_scenario(project_dir, vehicles, seed)

    if fraction in (0.0, 1.0):
        source_row = _source_endpoint_row(
            project_dir, vehicles, seed, edge_id, closure_time_s, fraction
        )
        if source_row is not None:
            print(
                f"[{seed} @ {fraction:.0%}] Reusing matched endpoint result.",
                flush=True,
            )
            return source_row

    label = _fraction_label(fraction)
    output_prefix = f"information-penetration-{label}"
    _, summary_path = config.named_result_paths(output_prefix)
    tripinfo_path = config.output_dir(output_prefix) / "tripinfo.xml"
    if resume and summary_path.exists() and tripinfo_path.exists():
        summary, extra, teleports = _summary_from_saved_partial(config, fraction)
    else:
        print(f"[{seed} @ {fraction:.0%}] Running partial-information policy...", flush=True)
        summary, extra = run_closure(
            config,
            edge_id=edge_id,
            closure_time_s=closure_time_s,
            strategy="partial",
            informed_fraction=fraction,
            output_prefix=output_prefix,
            write_comparison=False,
        )
        teleports = read_teleports(config.output_dir(output_prefix) / "statistics.xml")
    return _row_from_metrics(
        config,
        edge_id,
        closure_time_s,
        fraction,
        "partial",
        summary,
        extra,
        teleports,
    )


def _summary_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    baseline = {
        int(row["seed"]): float(row["total_time_loss_s"])
        for row in rows
        if float(row["informed_fraction"]) == 0.0
    }
    summaries: list[dict[str, object]] = []
    fractions = sorted({float(row["informed_fraction"]) for row in rows})
    for fraction in fractions:
        selected = [
            row for row in rows if float(row["informed_fraction"]) == fraction
        ]
        delays = [float(row["total_time_loss_s"]) for row in selected]
        paired_differences = (
            [
                float(row["total_time_loss_s"]) - baseline[int(row["seed"])]
                for row in selected
            ]
            if baseline
            else []
        )
        summaries.append(
            {
                "informed_fraction": fraction,
                "informed_percent": fraction * 100,
                "seed_count": len(selected),
                "total_time_loss_mean_s": mean_or_zero(delays),
                "total_time_loss_median_s": median_or_zero(delays),
                "total_time_loss_stddev_s": statistics.pstdev(delays),
                "total_time_loss_p25_s": quartile(delays, 0.25),
                "total_time_loss_p75_s": quartile(delays, 0.75),
                "total_time_loss_min_s": min(delays),
                "total_time_loss_max_s": max(delays),
                "mean_time_loss_per_vehicle_s": mean_or_zero(
                    [float(row["mean_time_loss_s"]) for row in selected]
                ),
                "mean_completed_trips": mean_or_zero(
                    [float(row["completed_trips"]) for row in selected]
                ),
                "mean_teleports": mean_or_zero(
                    [float(row["teleports"]) for row in selected]
                ),
                "mean_rerouted_vehicles": mean_or_zero(
                    [float(row["rerouted_vehicles"]) for row in selected]
                ),
                "mean_max_alternative_congestion": mean_or_zero(
                    [
                        float(row["maximum_congestion_on_alternative_routes"])
                        for row in selected
                    ]
                ),
                "difference_from_zero_mean_s": (
                    mean_or_zero(paired_differences) if paired_differences else ""
                ),
                "difference_from_zero_median_s": (
                    median_or_zero(paired_differences) if paired_differences else ""
                ),
                "better_than_zero_seed_count": (
                    sum(difference < 0 for difference in paired_differences)
                    if paired_differences
                    else ""
                ),
                "better_than_zero_percentage": (
                    100
                    * sum(difference < 0 for difference in paired_differences)
                    / len(selected)
                    if paired_differences
                    else ""
                ),
            }
        )
    return summaries


def run_information_penetration(
    project_dir: Path,
    vehicles: int = 2_500,
    seeds: tuple[int, ...] = tuple(
        range(DEFAULT_START_SEED, DEFAULT_START_SEED + DEFAULT_SEED_COUNT)
    ),
    fractions: tuple[float, ...] = DEFAULT_FRACTIONS,
    edge_id: str = DEFAULT_CLOSED_EDGE,
    closure_time_s: float = DEFAULT_CLOSURE_TIME_S,
    parallel: int = 1,
    resume: bool = False,
) -> tuple[Path, Path]:
    if not seeds:
        raise ValueError("at least one seed is required")
    if not fractions:
        raise ValueError("at least one informed fraction is required")
    if parallel <= 0:
        raise ValueError("parallel must be positive")
    if any(not 0.0 <= fraction <= 1.0 for fraction in fractions):
        raise ValueError("informed fractions must be between 0 and 1")
    fractions = tuple(sorted(set(fractions)))

    demand_tasks = [(project_dir, vehicles, seed) for seed in seeds]
    run_tasks(_prepare_task, demand_tasks, parallel)

    tasks = [
        (project_dir, vehicles, seed, fraction, edge_id, closure_time_s, resume)
        for fraction in fractions
        for seed in seeds
    ]
    rows = run_tasks(_run_seed_fraction, tasks, parallel)
    rows.sort(key=lambda row: (float(row["informed_fraction"]), int(row["seed"])))

    results_path = project_result_path(project_dir, "information_penetration.csv")
    summary_path = project_result_path(
        project_dir, "information_penetration_summary.csv"
    )
    write_dict_rows(results_path, rows)
    write_dict_rows(summary_path, _summary_rows(rows))
    return results_path, summary_path


def _parse_fractions(value: str) -> tuple[float, ...]:
    try:
        fractions = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as error:
        raise ValueError("fractions must be comma-separated numbers") from error
    if any(not 0.0 <= fraction <= 1.0 for fraction in fractions):
        raise ValueError("fractions must be between 0 and 1")
    return fractions


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure total time loss at different information penetration levels."
    )
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--vehicles", type=int, default=2_500)
    parser.add_argument("--start-seed", type=int, default=DEFAULT_START_SEED)
    parser.add_argument("--count", type=int, default=DEFAULT_SEED_COUNT)
    parser.add_argument(
        "--fractions",
        default=",".join(str(fraction) for fraction in DEFAULT_FRACTIONS),
        help="comma-separated informed fractions from 0 to 1",
    )
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--edge", default=DEFAULT_CLOSED_EDGE)
    parser.add_argument("--closure-time", type=float, default=DEFAULT_CLOSURE_TIME_S)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.count <= 0:
        print("error: count must be positive", file=sys.stderr)
        return 1
    try:
        fractions = _parse_fractions(args.fractions)
        seeds = tuple(range(args.start_seed, args.start_seed + args.count))
        results, summary = run_information_penetration(
            Path(args.project_dir),
            vehicles=args.vehicles,
            seeds=seeds,
            fractions=fractions,
            edge_id=args.edge,
            closure_time_s=args.closure_time,
            parallel=args.parallel,
            resume=args.resume,
        )
    except (ValueError, RuntimeError, MissingSumoError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"\nInformation-penetration sweep complete: {results}")
    print(f"Curve summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
