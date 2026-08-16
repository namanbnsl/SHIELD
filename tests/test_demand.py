from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from bengaluru_sim.demand import Edge, _sample_pairs, _write_candidate_trips, load_edges


NETWORK = """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <edge id=":internal" function="internal">
    <lane id=":internal_0" length="5" shape="0,0 1,1"/>
  </edge>
  <edge id="cars">
    <lane id="cars_0" allow="passenger" length="100" shape="0,0 100,0"/>
  </edge>
  <edge id="mixed">
    <lane id="mixed_0" length="200" shape="1000,0 1200,0"/>
  </edge>
  <edge id="bikes">
    <lane id="bikes_0" allow="bicycle" length="50" shape="0,0 0,50"/>
  </edge>
</net>
"""


def test_load_edges_keeps_only_passenger_edges(tmp_path: Path) -> None:
    network = tmp_path / "network.xml"
    network.write_text(NETWORK, encoding="utf-8")
    edges = load_edges(network)
    assert [edge.edge_id for edge in edges] == ["cars", "mixed"]
    assert [edge.weight for edge in edges] == [100.0, 200.0]


def test_pair_sampling_is_repeatable_and_respects_distance() -> None:
    edges = [
        Edge("a", 0, 0, 1),
        Edge("b", 1_000, 0, 1),
        Edge("c", 0, 1_000, 1),
    ]
    first = _sample_pairs(edges, 20, seed=42, min_distance_m=500)
    second = _sample_pairs(edges, 20, seed=42, min_distance_m=500)
    assert first == second
    assert all(origin.edge_id != destination.edge_id for origin, destination in first)


def test_weighted_roles_prefer_local_origins_and_major_destinations() -> None:
    local = Edge("local", 0, 0, 1, "highway.residential")
    major = Edge("major", 2_000, 0, 1, "highway.primary")
    pairs = _sample_pairs([local, major], 200, seed=42, min_distance_m=800)

    assert sum(origin.edge_id == "local" for origin, _ in pairs) > 150
    assert sum(destination.edge_id == "major" for _, destination in pairs) > 150


def test_candidate_departures_are_spread_and_deterministic(tmp_path: Path) -> None:
    pairs = [
        (Edge("a", 0, 0, 1), Edge("b", 1_000, 0, 1)) for _ in range(20)
    ]
    first = tmp_path / "first.xml"
    second = tmp_path / "second.xml"
    _write_candidate_trips(first, pairs, seed=7, demand_duration_s=600)
    _write_candidate_trips(second, pairs, seed=7, demand_duration_s=600)
    assert first.read_bytes() == second.read_bytes()

    departures = [
        float(trip.get("depart", "0"))
        for trip in ET.parse(first).getroot().findall("trip")
    ]
    assert departures == sorted(departures)
    assert departures[-1] - departures[0] > 300
