from __future__ import annotations

import argparse
import csv
import statistics
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from .closure import (
    DEFAULT_CLOSED_EDGE,
    DEFAULT_CLOSURE_TIME_S,
    _load_sumo_modules,
    _read_summary,
    _read_trips,
    run_closure,
)
from .commands import MissingSumoError, find_sumo_binary
from .config import SimulationConfig


def _scenario_dir(project_dir: Path, vehicles: int, seed: int) -> Path:
    return project_dir / "outputs" / "information-sweep" / f"vehicles-{vehicles}" / f"seed-{seed}"


def _queue_edges(network: Any, edge_id: str) -> tuple[str, ...]:
    edge = network.getEdge(edge_id)
    return tuple(dict.fromkeys([edge_id, *(item.getID() for item in edge.getIncoming())]))


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _baseline_probe(
    config: SimulationConfig,
    edge_id: str,
    alternative_edge_ids: tuple[str, ...],
    closure_time_s: float,
) -> dict[str, float]:
    """Measure the unclosed network through t=900 for one seed."""
    traci, sumolib = _load_sumo_modules()
    network = sumolib.net.readNet(str(config.network_file))
    queue_edge_ids = _queue_edges(network, edge_id)
    valid_alternative_ids = tuple(
        candidate
        for candidate in alternative_edge_ids
        if not candidate.startswith(":")
        and all(candidate != queue_edge for queue_edge in queue_edge_ids)
    )
    command = [
        find_sumo_binary("sumo"),
        "--net-file", str(config.network_file),
        "--route-files", str(config.routes_file),
        "--seed", str(config.seed),
        "--threads", "1",
        "--end", str(closure_time_s + 1),
        "--no-step-log", "true",
        "--no-warnings", "true",
    ]
    queue_samples: list[float] = []
    alternative_samples: list[float] = []
    queue_at_closure = 0.0
    alternative_at_closure = 0.0
    connection = None
    try:
        label = f"shield-baseline-probe-{config.seed}"
        traci.start(command, label=label, stdout=subprocess.PIPE)
        connection = traci.getConnection(label)
        while (
            connection.simulation.getMinExpectedNumber() > 0
            and connection.simulation.getTime() < closure_time_s
        ):
            connection.simulationStep()
            now = connection.simulation.getTime()
            queue_value = float(
                sum(
                    connection.edge.getLastStepVehicleNumber(edge)
                    for edge in queue_edge_ids
                )
            )
            alternative_value = float(
                sum(
                    connection.edge.getLastStepVehicleNumber(edge)
                    for edge in valid_alternative_ids
                )
            )
            queue_samples.append(queue_value)
            alternative_samples.append(alternative_value)
            if now >= closure_time_s:
                queue_at_closure = queue_value
                alternative_at_closure = alternative_value
                break
    finally:
        if connection is not None:
            connection.close()

    return {
        "baseline_queue_mean_before_closure": _mean(queue_samples),
        "baseline_queue_max_before_closure": max(queue_samples, default=0.0),
        "baseline_queue_at_closure": queue_at_closure,
        "baseline_alternative_mean_before_closure": _mean(alternative_samples),
        "baseline_alternative_max_before_closure": max(alternative_samples, default=0.0),
        "baseline_alternative_at_closure": alternative_at_closure,
        "baseline_alternative_edge_count": float(len(valid_alternative_ids)),
    }


def _run_seed(task: tuple[Path, int, int, str, float, dict[str, str]]) -> dict[str, object]:
    project_dir, vehicles, seed, edge_id, closure_time_s, outcome = task
    config = SimulationConfig(
        project_dir=_scenario_dir(project_dir, vehicles, seed),
        vehicles=vehicles,
        seed=seed,
    )
    output_prefix = f"diagnostic-global-{seed}"
    summary_path = (
        config.project_dir
        / "results"
        / f"{output_prefix.replace('-', '_')}_run_summary.csv"
    )
    if not summary_path.exists():
        print(f"[{seed}] Running global diagnostic...", flush=True)
        run_closure(
            config,
            edge_id=edge_id,
            closure_time_s=closure_time_s,
            strategy="global",
            output_prefix=output_prefix,
            write_comparison=False,
        )
    diagnostic = _read_summary(summary_path)
    alternative_edge_ids = tuple(diagnostic.get("alternative_edge_ids", "").split())
    baseline = _baseline_probe(config, edge_id, alternative_edge_ids, closure_time_s)
    trips = _read_trips(config.routes_file)
    row: dict[str, object] = {
        "seed": seed,
        "group": "global_worse" if outcome["global_worse_by_primary_metric"] == "True" else "global_better",
        "mean_travel_time_difference_s": float(outcome["mean_travel_time_difference_s"]),
        "total_time_loss_difference_s": float(outcome["total_time_loss_difference_s"]),
        "vehicles": vehicles,
        "planned_route_users": sum(edge_id in trip.route_edges for trip in trips),
        "global_completed_trips": int(outcome["global_completed_trips"]),
        "minimal_completed_trips": int(outcome["minimal_completed_trips"]),
        "global_teleports": int(outcome["global_teleports"]),
        "global_max_alternative_congestion": int(
            outcome["global_max_alternative_congestion"]
        ),
        "global_alternative_routes_observed": int(
            diagnostic.get("alternative_routes_observed", "0")
        ),
        "global_alternative_unique_edge_count": int(
            diagnostic.get("alternative_unique_edge_count", "0")
        ),
        "global_alternative_top_edge_share": float(
            diagnostic.get("alternative_top_edge_share", "0")
        ),
        "global_alternative_edge_hhi": float(
            diagnostic.get("alternative_edge_hhi", "0")
        ),
        "global_alternative_top_route_share": float(
            diagnostic.get("alternative_top_route_share", "0")
        ),
    }
    row.update(baseline)
    return row


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _group_summary(rows: list[dict[str, object]], group: str) -> dict[str, object]:
    selected = [row for row in rows if row["group"] == group]

    def values(key: str) -> list[float]:
        return [float(row[key]) for row in selected]

    def average(key: str) -> float:
        return statistics.fmean(values(key)) if selected else 0.0

    def median(key: str) -> float:
        return statistics.median(values(key)) if selected else 0.0

    return {
        "group": group,
        "seed_count": len(selected),
        "mean_travel_time_difference_mean_s": average("mean_travel_time_difference_s"),
        "mean_travel_time_difference_median_s": median("mean_travel_time_difference_s"),
        "total_time_loss_difference_mean_s": average("total_time_loss_difference_s"),
        "total_time_loss_difference_median_s": median("total_time_loss_difference_s"),
        "planned_route_users_mean": average("planned_route_users"),
        "baseline_queue_mean_before_closure_mean": average(
            "baseline_queue_mean_before_closure"
        ),
        "baseline_queue_max_before_closure_mean": average(
            "baseline_queue_max_before_closure"
        ),
        "baseline_queue_at_closure_mean": average("baseline_queue_at_closure"),
        "baseline_alternative_mean_before_closure_mean": average(
            "baseline_alternative_mean_before_closure"
        ),
        "baseline_alternative_max_before_closure_mean": average(
            "baseline_alternative_max_before_closure"
        ),
        "baseline_alternative_at_closure_mean": average(
            "baseline_alternative_at_closure"
        ),
        "global_alternative_routes_observed_mean": average(
            "global_alternative_routes_observed"
        ),
        "global_alternative_unique_edge_count_mean": average(
            "global_alternative_unique_edge_count"
        ),
        "global_alternative_top_edge_share_mean": average(
            "global_alternative_top_edge_share"
        ),
        "global_alternative_edge_hhi_mean": average("global_alternative_edge_hhi"),
        "global_alternative_top_route_share_mean": average(
            "global_alternative_top_route_share"
        ),
        "global_max_alternative_congestion_mean": average(
            "global_max_alternative_congestion"
        ),
    }


def run_condition_analysis(
    project_dir: Path,
    vehicles: int = 2_500,
    edge_id: str = DEFAULT_CLOSED_EDGE,
    closure_time_s: float = DEFAULT_CLOSURE_TIME_S,
    parallel: int = 1,
) -> tuple[Path, Path]:
    sweep_path = project_dir / "results" / "information_seed_sweep.csv"
    if not sweep_path.exists():
        raise RuntimeError("Run shield-information-sweep before condition analysis")
    with sweep_path.open(newline="", encoding="utf-8") as handle:
        outcomes = list(csv.DictReader(handle))
    if not outcomes:
        raise RuntimeError("information seed sweep is empty")
    tasks = [
        (project_dir, vehicles, int(row["seed"]), edge_id, closure_time_s, row)
        for row in outcomes
    ]
    if parallel == 1:
        rows = [_run_seed(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=parallel) as executor:
            rows = list(executor.map(_run_seed, tasks))
    rows.sort(key=lambda row: int(row["seed"]))
    results_path = project_dir / "results" / "information_condition_analysis.csv"
    summary_path = project_dir / "results" / "information_condition_summary.csv"
    _write_rows(results_path, rows)
    _write_rows(
        summary_path,
        [_group_summary(rows, "global_better"), _group_summary(rows, "global_worse")],
    )
    return results_path, summary_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare baseline conditions for global-better and global-worse seeds."
    )
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--vehicles", type=int, default=2_500)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--edge", default=DEFAULT_CLOSED_EDGE)
    parser.add_argument("--closure-time", type=float, default=DEFAULT_CLOSURE_TIME_S)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.parallel <= 0:
        print("error: parallel must be positive", file=sys.stderr)
        return 1
    try:
        results, summary = run_condition_analysis(
            Path(args.project_dir),
            vehicles=args.vehicles,
            edge_id=args.edge,
            closure_time_s=args.closure_time,
            parallel=args.parallel,
        )
    except (ValueError, RuntimeError, MissingSumoError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"\nCondition analysis complete: {results}")
    print(f"Group summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
