from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .closure import (
    DEFAULT_CLOSED_EDGE,
    DEFAULT_CLOSURE_TIME_S,
    ClosureMetrics,
    InformationStrategy,
    _read_summary,
    _read_trips,
    run_closure,
)
from .commands import MissingSumoError
from .config import SimulationConfig
from .metrics import Summary
from .metrics import collect_results
from .network import sha256_file


def _teleports(path: Path) -> int:
    root = ET.parse(path).getroot()
    element = root.find("teleports")
    return 0 if element is None else int(element.get("total", "0"))


def _comparison_path(project_dir: Path) -> Path:
    return project_dir / "results" / "information_comparison.csv"


def _summary_row(
    strategy: InformationStrategy,
    config: SimulationConfig,
    edge_id: str,
    closure_time_s: float,
    summary: Summary,
    extra: ClosureMetrics,
    teleports: int,
    network_hash: str,
    demand_hash: str,
) -> dict[str, object]:
    return {
        "strategy": strategy,
        "vehicles": summary.vehicles,
        "seed": config.seed,
        "closure_time_s": closure_time_s,
        "closed_edge_id": edge_id,
        "network_sha256": network_hash,
        "demand_sha256": demand_hash,
        "mean_travel_time_s": summary.mean_travel_time_s,
        "median_travel_time_s": summary.median_travel_time_s,
        "total_time_loss_s": summary.total_time_loss_s,
        "mean_time_loss_s": summary.mean_time_loss_s,
        "completed_trips": summary.completed,
        "teleports": teleports,
        "directly_affected_vehicles": extra.directly_affected,
        "rerouted_vehicles": extra.rerouted,
        "maximum_queue_near_closure": extra.max_queue_vehicles,
        "maximum_congestion_on_alternative_routes": extra.max_alternative_congestion,
        "alternative_edge_count": len(extra.alternative_edge_ids),
    }


def _write_comparison(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def refresh_information_comparison(
    config: SimulationConfig,
    edge_id: str = DEFAULT_CLOSED_EDGE,
    closure_time_s: float = DEFAULT_CLOSURE_TIME_S,
) -> Path:
    """Refresh derived metrics from existing minimal/global raw tripinfo files."""
    trips = _read_trips(config.routes_file)
    network_hash = sha256_file(config.network_file)
    demand_hash = sha256_file(config.routes_file)
    rows: list[dict[str, object]] = []
    for strategy in ("minimal", "global"):
        output_prefix = f"information-{strategy}"
        summary_path = (
            config.project_dir
            / "results"
            / f"{output_prefix.replace('-', '_')}_run_summary.csv"
        )
        values = _read_summary(summary_path)
        tripinfo_path = (
            config.project_dir
            / "outputs"
            / f"seed-{config.seed}-{output_prefix}"
            / "tripinfo.xml"
        )
        _results, summary = collect_results(
            trips, tripinfo_path, simulation_end_s=config.simulation_end_s
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
        rows.append(
            _summary_row(
                strategy,
                config,
                edge_id,
                closure_time_s,
                summary,
                extra,
                _teleports(
                    config.project_dir
                    / "outputs"
                    / f"seed-{config.seed}-{output_prefix}"
                    / "statistics.xml"
                ),
                network_hash,
                demand_hash,
            )
        )
    output = _comparison_path(config.project_dir)
    _write_comparison(output, rows)
    return output


def run_information_comparison(
    config: SimulationConfig,
    edge_id: str = DEFAULT_CLOSED_EDGE,
    closure_time_s: float = DEFAULT_CLOSURE_TIME_S,
) -> Path:
    """Run both information policies against one immutable demand/network pair."""
    config.validate()
    if not config.network_file.exists() or not config.routes_file.exists():
        raise RuntimeError(
            "Validated inputs are missing. Generate the 2,500-vehicle scenario first."
        )

    trips = _read_trips(config.routes_file)
    if len(trips) != config.vehicles:
        raise RuntimeError(
            f"demand contains {len(trips)} vehicles, expected {config.vehicles}"
        )
    network_hash = sha256_file(config.network_file)
    demand_hash = sha256_file(config.routes_file)
    rows: list[dict[str, object]] = []

    for strategy in ("minimal", "global"):
        print(f"Running {strategy}-information disruption...", flush=True)
        summary, extra = run_closure(
            config,
            edge_id=edge_id,
            closure_time_s=closure_time_s,
            strategy=strategy,
            output_prefix=f"information-{strategy}",
            write_comparison=False,
        )
        if sha256_file(config.routes_file) != demand_hash:
            raise RuntimeError("demand file changed while running the comparison")
        statistics_file = (
            config.project_dir
            / "outputs"
            / f"seed-{config.seed}-information-{strategy}"
            / "statistics.xml"
        )
        rows.append(
            _summary_row(
                strategy,
                config,
                edge_id,
                closure_time_s,
                summary,
                extra,
                _teleports(statistics_file),
                network_hash,
                demand_hash,
            )
        )
        mean = "n/a" if summary.mean_travel_time_s is None else f"{summary.mean_travel_time_s:.2f}s"
        print(
            f"{strategy}: completed={summary.completed}/{summary.vehicles}, "
            f"mean={mean}, rerouted={extra.rerouted}, "
            f"alternative-congestion={extra.max_alternative_congestion}",
            flush=True,
        )

    output = _comparison_path(config.project_dir)
    _write_comparison(output, rows)
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare minimal and global information at one road closure."
    )
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--vehicles", type=int, default=2_500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--edge", default=DEFAULT_CLOSED_EDGE)
    parser.add_argument("--closure-time", type=float, default=DEFAULT_CLOSURE_TIME_S)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = SimulationConfig(
        project_dir=args.project_dir,
        vehicles=args.vehicles,
        seed=args.seed,
    )
    try:
        output = run_information_comparison(config, args.edge, args.closure_time)
    except (ValueError, RuntimeError, MissingSumoError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"\nInformation comparison complete: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
