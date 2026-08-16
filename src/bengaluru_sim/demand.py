from __future__ import annotations

import csv
import math
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .commands import find_sumo_binary, run_sumo_command
from .config import SimulationConfig


@dataclass(frozen=True)
class Edge:
    edge_id: str
    x: float
    y: float
    weight: float
    road_type: str = ""
    road_name: str = ""


@dataclass(frozen=True)
class Trip:
    vehicle_id: str
    scheduled_departure_s: float
    origin_edge: str
    destination_edge: str
    route_edges: tuple[str, ...]


def read_trips(path: Path) -> list[Trip]:
    """Read routed vehicles back into the demand representation."""
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


def _passenger_lane(lane: ET.Element) -> bool:
    allow = set(lane.get("allow", "").split())
    disallow = set(lane.get("disallow", "").split())
    return (not allow or "passenger" in allow) and "passenger" not in disallow


def load_edges(network_file: Path) -> list[Edge]:
    edges: list[Edge] = []
    for _, element in ET.iterparse(network_file, events=("end",)):
        if element.tag != "edge":
            continue
        edge_id = element.get("id", "")
        if edge_id.startswith(":") or element.get("function"):
            element.clear()
            continue
        lanes = [lane for lane in element.findall("lane") if _passenger_lane(lane)]
        if not lanes:
            element.clear()
            continue
        lane = lanes[0]
        points = lane.get("shape", "").split()
        if not points:
            element.clear()
            continue
        coordinates = [tuple(map(float, point.split(",")[:2])) for point in points]
        x = sum(point[0] for point in coordinates) / len(coordinates)
        y = sum(point[1] for point in coordinates) / len(coordinates)
        length = max(float(lane.get("length", "1")), 1.0)
        edges.append(
            Edge(
                edge_id,
                x,
                y,
                length,
                element.get("type", ""),
                element.get("name", ""),
            )
        )
        element.clear()
    if len(edges) < 2:
        raise RuntimeError("SUMO network contains fewer than two passenger edges")
    return edges


def _road_class(edge: Edge) -> str:
    return edge.road_type.removeprefix("highway.")


def _origin_weight(edge: Edge) -> float:
    """Weight likely residential/local-road edges as trip origins."""
    return {
        "residential": 10.0,
        "living_street": 9.0,
        "unclassified": 4.0,
        "tertiary": 1.5,
        "secondary": 0.6,
        "primary": 0.25,
        "trunk": 0.1,
        "trunk_link": 0.15,
        "primary_link": 0.2,
        "secondary_link": 0.3,
        "tertiary_link": 0.5,
        "service": 2.0,
    }.get(_road_class(edge), 1.0)


def _destination_weight(edge: Edge) -> float:
    """Weight major corridors likely to carry employment/commercial trips."""
    return {
        "trunk": 12.0,
        "primary": 10.0,
        "secondary": 8.0,
        "tertiary": 0.6,
        "trunk_link": 4.0,
        "primary_link": 4.0,
        "secondary_link": 3.0,
        "tertiary_link": 0.8,
        "service": 0.8,
        "residential": 0.03,
        "living_street": 0.02,
        "unclassified": 0.1,
    }.get(_road_class(edge), 1.0)


def _sample_pairs(
    edges: list[Edge], count: int, seed: int, min_distance_m: float
) -> list[tuple[Edge, Edge]]:
    rng = random.Random(seed)
    origin_weights = [_origin_weight(edge) for edge in edges]
    destination_weights = [_destination_weight(edge) for edge in edges]
    # Keep the existing CLI minimum as a floor, but reject very short trips in
    # the weighted model so local-to-local artifacts do not dominate demand.
    minimum_distance = max(min_distance_m, 1_200.0)
    pairs: list[tuple[Edge, Edge]] = []
    max_attempts = count * 100
    for _ in range(max_attempts):
        origin = rng.choices(edges, weights=origin_weights, k=1)[0]
        destination = rng.choices(edges, weights=destination_weights, k=1)[0]
        if origin.edge_id == destination.edge_id:
            continue
        if (
            math.hypot(origin.x - destination.x, origin.y - destination.y)
            < minimum_distance
        ):
            continue
        pairs.append((origin, destination))
        if len(pairs) == count:
            return pairs
    raise RuntimeError(
        f"Could only sample {len(pairs)} of {count} O/D pairs at least "
        f"{min_distance_m:g} m apart"
    )


def _write_candidate_trips(
    path: Path,
    pairs: list[tuple[Edge, Edge]],
    seed: int,
    demand_duration_s: float,
) -> None:
    rng = random.Random(seed ^ 0x5EED)
    departures = sorted(rng.uniform(0, demand_duration_s) for _ in pairs)
    root = ET.Element("routes")
    for index, ((origin, destination), depart) in enumerate(zip(pairs, departures)):
        ET.SubElement(
            root,
            "trip",
            {
                "id": f"candidate_{index:06d}",
                "depart": f"{depart:.2f}",
                "from": origin.edge_id,
                "to": destination.edge_id,
                "departLane": "best",
                "departPos": "random_free",
            },
        )
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _route_candidates(config: SimulationConfig, candidate_count: int) -> Path:
    pairs = _sample_pairs(
        load_edges(config.network_file),
        candidate_count,
        config.seed,
        config.min_trip_distance_m,
    )
    candidate_file = config.run_dir / "candidates.trips.xml"
    routed_file = config.run_dir / "candidates.rou.xml"
    _write_candidate_trips(
        candidate_file, pairs, config.seed, config.demand_duration_s
    )
    command = [
        find_sumo_binary("duarouter"),
        "--net-file",
        str(config.network_file),
        "--route-files",
        str(candidate_file),
        "--output-file",
        str(routed_file),
        "--seed",
        str(config.seed),
        "--routing-algorithm",
        "dijkstra",
        "--ignore-errors",
        "true",
        "--remove-loops",
        "true",
        "--no-step-log",
        "true",
    ]
    run_sumo_command(command, config.run_dir / "duarouter.log")
    return routed_file


def _read_routed_vehicles(path: Path) -> list[tuple[float, tuple[str, ...]]]:
    vehicles: list[tuple[float, tuple[str, ...]]] = []
    root = ET.parse(path).getroot()
    for vehicle in root.findall("vehicle"):
        route = vehicle.find("route")
        edges = tuple(route.get("edges", "").split()) if route is not None else ()
        if len(edges) >= 2:
            vehicles.append((float(vehicle.get("depart", "0")), edges))
    return vehicles


def generate_demand(config: SimulationConfig) -> list[Trip]:
    """Generate exactly the requested number of deterministically routed trips."""
    config.run_dir.mkdir(parents=True, exist_ok=True)
    routed: list[tuple[float, tuple[str, ...]]] = []
    for multiplier in (2, 4, 8):
        routed = _read_routed_vehicles(
            _route_candidates(config, config.vehicles * multiplier)
        )
        if len(routed) >= config.vehicles:
            break
    if len(routed) < config.vehicles:
        raise RuntimeError(
            f"SUMO could route only {len(routed)} valid candidates; "
            f"needed {config.vehicles}"
        )

    selection_rng = random.Random(config.seed ^ 0xC0FFEE)
    selected = selection_rng.sample(routed, config.vehicles)
    selected.sort(key=lambda item: item[0])

    trips = [
        Trip(
            vehicle_id=f"vehicle_{index:04d}",
            scheduled_departure_s=depart,
            origin_edge=edges[0],
            destination_edge=edges[-1],
            route_edges=edges,
        )
        for index, (depart, edges) in enumerate(selected)
    ]
    _write_routes(config.routes_file, trips)
    _write_manifest(config.manifest_file, trips)
    return trips


def _write_routes(path: Path, trips: list[Trip]) -> None:
    root = ET.Element("routes")
    ET.SubElement(
        root,
        "vType",
        {"id": "passenger", "vClass": "passenger", "guiShape": "passenger"},
    )
    for trip in trips:
        vehicle = ET.SubElement(
            root,
            "vehicle",
            {
                "id": trip.vehicle_id,
                "type": "passenger",
                "depart": f"{trip.scheduled_departure_s:.2f}",
                "departLane": "best",
                "departPos": "random_free",
            },
        )
        ET.SubElement(vehicle, "route", {"edges": " ".join(trip.route_edges)})
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _write_manifest(path: Path, trips: list[Trip]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "vehicle_id",
                "scheduled_departure_time_s",
                "origin_edge",
                "destination_edge",
            ]
        )
        writer.writerows(
            (
                trip.vehicle_id,
                f"{trip.scheduled_departure_s:.2f}",
                trip.origin_edge,
                trip.destination_edge,
            )
            for trip in trips
        )
