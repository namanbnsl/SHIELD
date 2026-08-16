from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .commands import MissingSumoError, find_sumo_binary
from .config import DEFAULT_BBOX, SimulationConfig
from .demand import generate_demand
from .metrics import collect_results, write_results, write_summary
from .network import build_network, sha256_file
from .simulation import run_simulation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the minimal Bengaluru SUMO traffic simulation."
    )
    parser.add_argument("--vehicles", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--demand-duration", type=float, default=3_600.0, metavar="SECONDS")
    parser.add_argument("--simulation-end", type=float, default=7_200.0, metavar="SECONDS")
    parser.add_argument("--min-trip-distance", type=float, default=800.0, metavar="METERS")
    parser.add_argument(
        "--bbox",
        default=",".join(map(str, DEFAULT_BBOX)),
        help="WEST,SOUTH,EAST,NORTH (default: %(default)s)",
    )
    parser.add_argument(
        "--refresh-network",
        action="store_true",
        help="redownload OSM instead of reusing the cached snapshot",
    )
    return parser


def _parse_bbox(value: str) -> tuple[float, float, float, float]:
    try:
        parts = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("bbox values must be numbers") from error
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox needs four comma-separated values")
    return parts  # type: ignore[return-value]


def _sumo_version() -> str:
    result = subprocess.run(
        [find_sumo_binary("sumo"), "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()[0].strip()


def run(config: SimulationConfig) -> int:
    config.validate()
    print("Loading Bengaluru road network...", flush=True)
    build_network(config)
    print(f"Generating {config.vehicles:,} trips...", flush=True)
    trips = generate_demand(config)
    print("Running simulation...", flush=True)
    run_simulation(config)

    results, summary = collect_results(
        trips, config.tripinfo_file, simulation_end_s=config.simulation_end_s
    )
    write_results(config.result_file, results)
    metadata = {
        "seed": config.seed,
        "bbox_west_south_east_north": ",".join(map(str, config.bbox)),
        "demand_duration_seconds": config.demand_duration_s,
        "simulation_end_seconds": config.simulation_end_s,
        "sumo_version": _sumo_version(),
        "osm_sha256": sha256_file(config.osm_file),
        "network_sha256": sha256_file(config.network_file),
        "demand_sha256": sha256_file(config.routes_file),
    }
    write_summary(config.summary_file, summary, metadata)

    mean_minutes = (
        "n/a"
        if summary.mean_travel_time_s is None
        else f"{summary.mean_travel_time_s / 60:.1f} min"
    )
    median_minutes = (
        "n/a"
        if summary.median_travel_time_s is None
        else f"{summary.median_travel_time_s / 60:.1f} min"
    )
    print("\nSimulation complete.\n")
    print(f"Vehicles: {summary.vehicles}")
    print(f"Completed trips: {summary.completed}")
    print(f"Unfinished/stranded trips: {summary.unfinished}")
    print(f"Mean travel time: {mean_minutes}")
    print(f"Median travel time: {median_minutes}")
    print(f"\nResults saved to {config.result_file.relative_to(config.project_dir)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        bbox = _parse_bbox(args.bbox)
        config = SimulationConfig(
            project_dir=Path.cwd(),
            vehicles=args.vehicles,
            seed=args.seed,
            demand_duration_s=args.demand_duration,
            simulation_end_s=args.simulation_end,
            min_trip_distance_m=args.min_trip_distance,
            bbox=bbox,
            refresh_network=args.refresh_network,
        )
        return run(config)
    except (ValueError, RuntimeError, MissingSumoError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
