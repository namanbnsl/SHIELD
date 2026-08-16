from __future__ import annotations

import argparse
import csv
import random
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from importlib.metadata import distribution
from pathlib import Path
from typing import Any, Literal

from .commands import MissingSumoError, find_sumo_binary
from .config import SimulationConfig
from .demand import Trip
from .metrics import Summary, collect_results, write_results, write_summary
from .network import sha256_file


DEFAULT_CLOSED_EDGE = "377105483#0"
DEFAULT_CLOSURE_TIME_S = 900.0
InformationStrategy = Literal["minimal", "global", "partial"]
_INFORMATION_SELECTION_SALT = 0x51EED


def _select_informed_vehicle_ids(
    vehicle_ids: list[str], seed: int, fraction: float
) -> set[str]:
    """Select a deterministic, nested subset of vehicles to receive information."""
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("informed fraction must be between 0 and 1")
    ordered = sorted(vehicle_ids)
    randomizer = random.Random(seed ^ _INFORMATION_SELECTION_SALT)
    randomizer.shuffle(ordered)
    informed_count = round(fraction * len(ordered))
    return set(ordered[:informed_count])


@dataclass(frozen=True)
class ClosureMetrics:
    directly_affected: int
    rerouted: int
    max_queue_vehicles: int
    queue_edge_ids: tuple[str, ...]
    max_alternative_congestion: int
    alternative_edge_ids: tuple[str, ...]
    alternative_routes_observed: int = 0
    alternative_unique_edge_count: int = 0
    alternative_top_edge_share: float = 0.0
    alternative_edge_hhi: float = 0.0
    alternative_top_route_share: float = 0.0


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
    strategy: InformationStrategy = "minimal",
    informed_fraction: float | None = None,
    output_prefix: str | None = None,
    write_comparison: bool = True,
) -> tuple[Summary, ClosureMetrics]:
    config.validate()
    if strategy not in ("minimal", "global", "partial"):
        raise ValueError("strategy must be 'minimal', 'partial', or 'global'")
    if strategy == "partial" and informed_fraction is None:
        raise ValueError("partial strategy requires informed_fraction")
    if strategy != "partial" and informed_fraction is not None:
        raise ValueError("informed_fraction is only valid for the partial strategy")
    if informed_fraction is not None and not 0.0 <= informed_fraction <= 1.0:
        raise ValueError("informed fraction must be between 0 and 1")
    if not config.network_file.exists() or not config.routes_file.exists():
        raise RuntimeError("Baseline inputs are missing. Run 'uv run shield-sim' first.")
    if write_comparison and not config.summary_file.exists():
        raise RuntimeError("Baseline summary is missing. Run 'uv run shield-sim' first.")
    if not 0 < closure_time_s < config.simulation_end_s:
        raise ValueError("closure time must fall within the simulation")

    trips = _read_trips(config.routes_file)
    original_routes = {trip.vehicle_id: trip.route_edges for trip in trips}
    informed_vehicle_ids = (
        _select_informed_vehicle_ids(
            list(original_routes), config.seed, informed_fraction
        )
        if strategy == "partial" and informed_fraction is not None
        else set()
    )
    traci, sumolib = _load_sumo_modules()
    network = sumolib.net.readNet(str(config.network_file))
    try:
        closed_edge = network.getEdge(edge_id)
    except KeyError as error:
        raise ValueError(f"unknown closure edge: {edge_id}") from error
    queue_edges = tuple(
        dict.fromkeys([edge_id, *(edge.getID() for edge in closed_edge.getIncoming())])
    )

    run_name = "closure" if output_prefix is None else output_prefix
    closure_dir = config.project_dir / "outputs" / f"seed-{config.seed}-{run_name}"
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
    alternative_route_counts: Counter[tuple[str, ...]] = Counter()
    max_queue = 0
    max_alternative_congestion = 0
    closed = False
    connection = None
    try:
        label = "shield-closure" if output_prefix is None else f"shield-{run_name}"
        traci.start(command, label=label, stdout=subprocess.PIPE)
        connection = traci.getConnection(label)
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
                    route = tuple(connection.vehicle.getRoute(vehicle_id))
                    route_index = connection.vehicle.getRouteIndex(vehicle_id)
                    original_index = original.index(edge_id) if edge_id in original else None
                    affected = (
                        original_index is not None
                        and route_index <= original_index
                    )
                    informed = vehicle_id in informed_vehicle_ids
                    receives_global_information = strategy == "global" or (
                        strategy == "partial" and informed
                    )
                    if not receives_global_information and (
                        not affected or route != original
                    ):
                        continue
                    directly_affected.update({vehicle_id} if affected else ())
                    if vehicle_id in reroute_attempted:
                        continue
                    reroute_attempted.add(vehicle_id)
                    before = route
                    try:
                        connection.vehicle.rerouteTraveltime(
                            vehicle_id, currentTravelTimes=True
                        )
                    except traci.TraCIException:
                        continue
                    after = tuple(connection.vehicle.getRoute(vehicle_id))
                    reroute_succeeded = after != before and (
                        receives_global_information
                        or edge_id not in after[max(route_index, 0):]
                    )
                    if reroute_succeeded:
                        rerouted.add(vehicle_id)

                    remaining_before = set(before[max(route_index, 0):])
                    alternative_route = tuple(
                        candidate
                        for candidate in after[max(route_index, 0):]
                        if not candidate.startswith(":")
                    )
                    if reroute_succeeded:
                        alternative_route_counts[alternative_route] += 1
                        alternative_edge_counts.update(
                            candidate
                            for candidate in alternative_route
                            if candidate not in remaining_before
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
    results, summary = collect_results(
        trips, tripinfo_file, simulation_end_s=config.simulation_end_s
    )
    statistics_root = ET.parse(statistics_file).getroot()
    teleports_node = statistics_root.find("teleports")
    closure_teleports = (
        0 if teleports_node is None else int(teleports_node.get("total", "0"))
    )
    result_stem = "closure" if output_prefix is None else output_prefix.replace("-", "_")
    result_file = config.project_dir / "results" / f"{result_stem}_run.csv"
    summary_file = config.project_dir / "results" / f"{result_stem}_run_summary.csv"
    write_results(result_file, results)
    extra = ClosureMetrics(
        len(directly_affected),
        len(rerouted),
        max_queue,
        queue_edges,
        max_alternative_congestion,
        tuple(edge for edge, _ in alternative_edge_counts.most_common(32)),
    )
    alternative_route_total = sum(alternative_route_counts.values())
    alternative_edge_total = sum(alternative_edge_counts.values())
    alternative_top_edge_share = (
        0.0
        if alternative_edge_total == 0
        else alternative_edge_counts.most_common(1)[0][1] / alternative_edge_total
    )
    alternative_edge_hhi = (
        0.0
        if alternative_edge_total == 0
        else sum(
            (count / alternative_edge_total) ** 2
            for count in alternative_edge_counts.values()
        )
    )
    alternative_top_route_share = (
        0.0
        if alternative_route_total == 0
        else alternative_route_counts.most_common(1)[0][1] / alternative_route_total
    )
    extra = ClosureMetrics(
        extra.directly_affected,
        extra.rerouted,
        extra.max_queue_vehicles,
        extra.queue_edge_ids,
        extra.max_alternative_congestion,
        extra.alternative_edge_ids,
        alternative_route_total,
        len(alternative_edge_counts),
        alternative_top_edge_share,
        alternative_edge_hhi,
        alternative_top_route_share,
    )
    planned_route_users = sum(edge_id in route for route in original_routes.values())
    metadata = {
        "seed": config.seed,
        "demand_duration_seconds": config.demand_duration_s,
        "simulation_end_seconds": config.simulation_end_s,
        "closure_time_seconds": closure_time_s,
        "closed_edge_id": edge_id,
        "closed_edge_name": closed_edge.getName(),
        "information_strategy": strategy,
        "informed_fraction": (
            "" if informed_fraction is None else f"{informed_fraction:.6f}"
        ),
        "informed_vehicles": len(informed_vehicle_ids),
        "queue_edge_ids": " ".join(queue_edges),
        "vehicles_directly_affected": extra.directly_affected,
        "rerouted_vehicles": extra.rerouted,
        "planned_route_users": planned_route_users,
        "teleports": closure_teleports,
        "maximum_queue_near_closure": extra.max_queue_vehicles,
        "maximum_congestion_on_alternative_routes": extra.max_alternative_congestion,
        "alternative_edge_ids": " ".join(extra.alternative_edge_ids),
        "alternative_routes_observed": extra.alternative_routes_observed,
        "alternative_unique_edge_count": extra.alternative_unique_edge_count,
        "alternative_top_edge_share": f"{extra.alternative_top_edge_share:.6f}",
        "alternative_edge_hhi": f"{extra.alternative_edge_hhi:.6f}",
        "alternative_top_route_share": f"{extra.alternative_top_route_share:.6f}",
        "network_sha256": sha256_file(config.network_file),
        "demand_sha256": sha256_file(config.routes_file),
    }
    write_summary(summary_file, summary, metadata)
    if write_comparison:
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
