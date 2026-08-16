from __future__ import annotations

import argparse
import csv
import statistics
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .closure import DEFAULT_CLOSED_EDGE, DEFAULT_CLOSURE_TIME_S, _read_trips
from .commands import MissingSumoError
from .config import SimulationConfig
from .demand import generate_demand
from .information import refresh_information_comparison, run_information_comparison


DEFAULT_START_SEED = 42
DEFAULT_SEED_COUNT = 50


def _scenario_dir(project_dir: Path, vehicles: int, seed: int) -> Path:
    return project_dir / "outputs" / "information-sweep" / f"vehicles-{vehicles}" / f"seed-{seed}"


def _prepare_scenario(project_dir: Path, vehicles: int, seed: int) -> SimulationConfig:
    scenario = _scenario_dir(project_dir, vehicles, seed)
    scenario.mkdir(parents=True, exist_ok=True)
    data_link = scenario / "data"
    if not data_link.exists():
        data_link.symlink_to(project_dir / "data", target_is_directory=True)
    return SimulationConfig(project_dir=scenario, vehicles=vehicles, seed=seed)


def _read_comparison(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if {row.get("strategy") for row in rows} != {"minimal", "global"}:
        raise RuntimeError(f"comparison must contain minimal and global rows: {path}")
    return rows


def _difference_row(comparison_rows: list[dict[str, str]]) -> dict[str, object]:
    by_strategy = {row["strategy"]: row for row in comparison_rows}
    minimal = by_strategy["minimal"]
    global_information = by_strategy["global"]
    mean_difference = float(global_information["mean_travel_time_s"]) - float(
        minimal["mean_travel_time_s"]
    )
    total_time_loss_difference = float(global_information["total_time_loss_s"]) - float(
        minimal["total_time_loss_s"]
    )
    return {
        "seed": int(minimal["seed"]),
        "vehicles": int(minimal["vehicles"]),
        "closure_time_s": float(minimal["closure_time_s"]),
        "closed_edge_id": minimal["closed_edge_id"],
        "network_sha256": minimal["network_sha256"],
        "demand_sha256": minimal["demand_sha256"],
        "minimal_mean_travel_time_s": float(minimal["mean_travel_time_s"]),
        "global_mean_travel_time_s": float(global_information["mean_travel_time_s"]),
        "mean_travel_time_difference_s": mean_difference,
        "minimal_total_time_loss_s": float(minimal["total_time_loss_s"]),
        "global_total_time_loss_s": float(global_information["total_time_loss_s"]),
        "total_time_loss_difference_s": total_time_loss_difference,
        "minimal_mean_time_loss_s": float(minimal["mean_time_loss_s"]),
        "global_mean_time_loss_s": float(global_information["mean_time_loss_s"]),
        "mean_time_loss_difference_s": float(global_information["mean_time_loss_s"])
        - float(minimal["mean_time_loss_s"]),
        "minimal_median_travel_time_s": float(minimal["median_travel_time_s"]),
        "global_median_travel_time_s": float(global_information["median_travel_time_s"]),
        "minimal_completed_trips": int(minimal["completed_trips"]),
        "global_completed_trips": int(global_information["completed_trips"]),
        "minimal_teleports": int(minimal["teleports"]),
        "global_teleports": int(global_information["teleports"]),
        "teleports_difference": int(global_information["teleports"])
        - int(minimal["teleports"]),
        "minimal_max_alternative_congestion": int(
            minimal["maximum_congestion_on_alternative_routes"]
        ),
        "global_max_alternative_congestion": int(
            global_information["maximum_congestion_on_alternative_routes"]
        ),
        "max_alternative_congestion_difference": int(
            global_information["maximum_congestion_on_alternative_routes"]
        )
        - int(minimal["maximum_congestion_on_alternative_routes"]),
        "minimal_rerouted_vehicles": int(minimal["rerouted_vehicles"]),
        "global_rerouted_vehicles": int(global_information["rerouted_vehicles"]),
        "global_worse_by_mean_travel_time": mean_difference > 0,
        "global_worse_by_total_time_loss": total_time_loss_difference > 0,
        "global_worse_by_primary_metric": total_time_loss_difference > 0,
    }


def _prepare_demand(task: tuple[Path, int, int, bool]) -> None:
    project_dir, vehicles, seed, resume = task
    config = _prepare_scenario(project_dir, vehicles, seed)
    if resume and config.routes_file.exists():
        trips = _read_trips(config.routes_file)
        if len(trips) != vehicles:
            raise RuntimeError(
                f"seed {seed}: existing demand has {len(trips)} trips, expected {vehicles}"
            )
        return
    print(f"[{seed}] Generating weighted demand...", flush=True)
    trips = generate_demand(config)
    if len(trips) != vehicles:
        raise RuntimeError(f"seed {seed}: generated {len(trips)} trips, expected {vehicles}")


def _run_seed(task: tuple[Path, int, int, str, float, bool]) -> dict[str, object]:
    project_dir, vehicles, seed, edge_id, closure_time_s, resume = task
    config = _prepare_scenario(project_dir, vehicles, seed)
    comparison_path = config.project_dir / "results" / "information_comparison.csv"
    if resume and comparison_path.exists():
        existing_rows = _read_comparison(comparison_path)
        if "total_time_loss_s" in existing_rows[0]:
            print(f"[{seed}] Reusing completed comparison.", flush=True)
        else:
            print(f"[{seed}] Refreshing uncensored metrics from raw tripinfo...", flush=True)
            refresh_information_comparison(config, edge_id, closure_time_s)
    else:
        print(f"[{seed}] Running both information strategies...", flush=True)
        run_information_comparison(config, edge_id, closure_time_s)
    return _difference_row(_read_comparison(comparison_path))


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _quantile(values: list[float], fraction: float) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=4, method="inclusive")[int(fraction * 4) - 1]


def _summary_row(
    rows: list[dict[str, object]],
    project_dir: Path,
    vehicles: int,
    seeds: tuple[int, ...],
    edge_id: str,
    closure_time_s: float,
) -> dict[str, object]:
    mean_differences = [float(row["mean_travel_time_difference_s"]) for row in rows]
    total_time_loss_differences = [
        float(row["total_time_loss_difference_s"]) for row in rows
    ]
    teleports_differences = [float(row["teleports_difference"]) for row in rows]
    congestion_differences = [
        float(row["max_alternative_congestion_difference"]) for row in rows
    ]
    minimal_means = [float(row["minimal_mean_travel_time_s"]) for row in rows]
    global_means = [float(row["global_mean_travel_time_s"]) for row in rows]
    minimal_total_time_loss = [
        float(row["minimal_total_time_loss_s"]) for row in rows
    ]
    global_total_time_loss = [float(row["global_total_time_loss_s"]) for row in rows]
    minimal_teleports = [float(row["minimal_teleports"]) for row in rows]
    global_teleports = [float(row["global_teleports"]) for row in rows]
    minimal_congestion = [
        float(row["minimal_max_alternative_congestion"]) for row in rows
    ]
    global_congestion = [
        float(row["global_max_alternative_congestion"]) for row in rows
    ]
    completion_differences = [
        int(row["global_completed_trips"]) - int(row["minimal_completed_trips"])
        for row in rows
    ]
    global_worse = sum(bool(row["global_worse_by_primary_metric"]) for row in rows)
    global_more_teleports = sum(value > 0 for value in teleports_differences)
    global_higher_congestion = sum(value > 0 for value in congestion_differences)
    return {
        "vehicles": vehicles,
        "requested_seed_count": len(seeds),
        "completed_seed_count": len(rows),
        "seed_first": min(seeds),
        "seed_last": max(seeds),
        "closure_time_s": closure_time_s,
        "closed_edge_id": edge_id,
        "network_sha256": rows[0]["network_sha256"],
        "mean_travel_time_difference_mean_s": _mean(mean_differences),
        "mean_travel_time_difference_median_s": _median(mean_differences),
        "mean_travel_time_difference_stddev_s": statistics.pstdev(mean_differences),
        "mean_travel_time_difference_p25_s": _quantile(mean_differences, 0.25),
        "mean_travel_time_difference_p75_s": _quantile(mean_differences, 0.75),
        "mean_travel_time_difference_min_s": min(mean_differences),
        "mean_travel_time_difference_max_s": max(mean_differences),
        "total_time_loss_difference_mean_s": _mean(total_time_loss_differences),
        "total_time_loss_difference_median_s": _median(total_time_loss_differences),
        "total_time_loss_difference_stddev_s": statistics.pstdev(
            total_time_loss_differences
        ),
        "total_time_loss_difference_p25_s": _quantile(
            total_time_loss_differences, 0.25
        ),
        "total_time_loss_difference_p75_s": _quantile(
            total_time_loss_differences, 0.75
        ),
        "total_time_loss_difference_min_s": min(total_time_loss_differences),
        "total_time_loss_difference_max_s": max(total_time_loss_differences),
        "minimal_mean_travel_time_across_seeds_s": _mean(minimal_means),
        "global_mean_travel_time_across_seeds_s": _mean(global_means),
        "minimal_total_time_loss_across_seeds_s": _mean(minimal_total_time_loss),
        "global_total_time_loss_across_seeds_s": _mean(global_total_time_loss),
        "minimal_teleports_mean": _mean(minimal_teleports),
        "global_teleports_mean": _mean(global_teleports),
        "teleports_difference_mean": _mean(teleports_differences),
        "teleports_difference_median": _median(teleports_differences),
        "global_more_teleports_seed_count": global_more_teleports,
        "global_more_teleports_percentage": 100 * global_more_teleports / len(rows),
        "minimal_max_alternative_congestion_mean": _mean(minimal_congestion),
        "global_max_alternative_congestion_mean": _mean(global_congestion),
        "max_alternative_congestion_difference_mean": _mean(congestion_differences),
        "max_alternative_congestion_difference_median": _median(congestion_differences),
        "global_higher_congestion_seed_count": global_higher_congestion,
        "global_higher_congestion_percentage": 100
        * global_higher_congestion
        / len(rows),
        "completed_trips_difference_mean": _mean(
            [float(value) for value in completion_differences]
        ),
        "completed_trips_difference_median": _median(
            [float(value) for value in completion_differences]
        ),
        "global_worse_seed_count": global_worse,
        "global_worse_percentage": 100 * global_worse / len(rows),
        "completed_mean_travel_time_worse_seed_count": sum(
            bool(row["global_worse_by_mean_travel_time"]) for row in rows
        ),
        "completed_mean_travel_time_worse_percentage": 100
        * sum(bool(row["global_worse_by_mean_travel_time"]) for row in rows)
        / len(rows),
        "per_seed_results": str(
            project_dir / "results" / "information_seed_sweep.csv"
        ),
    }


def run_information_sweep(
    project_dir: Path,
    vehicles: int = 2_500,
    seeds: tuple[int, ...] = tuple(range(DEFAULT_START_SEED, DEFAULT_START_SEED + DEFAULT_SEED_COUNT)),
    edge_id: str = DEFAULT_CLOSED_EDGE,
    closure_time_s: float = DEFAULT_CLOSURE_TIME_S,
    parallel: int = 1,
    resume: bool = False,
) -> tuple[Path, Path]:
    if not seeds:
        raise ValueError("at least one seed is required")
    if parallel <= 0:
        raise ValueError("parallel must be positive")

    demand_tasks = [(project_dir, vehicles, seed, resume) for seed in seeds]
    if parallel == 1:
        for task in demand_tasks:
            _prepare_demand(task)
    else:
        with ProcessPoolExecutor(max_workers=parallel) as executor:
            list(executor.map(_prepare_demand, demand_tasks))

    tasks = [
        (project_dir, vehicles, seed, edge_id, closure_time_s, resume)
        for seed in seeds
    ]
    if parallel == 1:
        rows = [_run_seed(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=parallel) as executor:
            rows = list(executor.map(_run_seed, tasks))
    rows.sort(key=lambda row: int(row["seed"]))

    results_path = project_dir / "results" / "information_seed_sweep.csv"
    summary_path = project_dir / "results" / "information_seed_sweep_summary.csv"
    _write_rows(results_path, rows)
    _write_rows(
        summary_path,
        [_summary_row(rows, project_dir, vehicles, seeds, edge_id, closure_time_s)],
    )
    return results_path, summary_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the minimal/global information comparison across seeds."
    )
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--vehicles", type=int, default=2_500)
    parser.add_argument("--start-seed", type=int, default=DEFAULT_START_SEED)
    parser.add_argument("--count", type=int, default=DEFAULT_SEED_COUNT)
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
    seeds = tuple(range(args.start_seed, args.start_seed + args.count))
    try:
        results, summary = run_information_sweep(
            Path(args.project_dir),
            vehicles=args.vehicles,
            seeds=seeds,
            edge_id=args.edge,
            closure_time_s=args.closure_time,
            parallel=args.parallel,
            resume=args.resume,
        )
    except (ValueError, RuntimeError, MissingSumoError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"\nSeed sweep complete: {results}")
    print(f"Distribution summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
