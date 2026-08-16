from pathlib import Path

from bengaluru_sim.closure import _read_trips, _select_informed_vehicle_ids


def test_read_trips_preserves_routed_demand(tmp_path: Path) -> None:
    routes = tmp_path / "demand.rou.xml"
    routes.write_text(
        '<routes><vehicle id="v0" depart="12.5"><route edges="a b c"/></vehicle></routes>',
        encoding="utf-8",
    )

    trips = _read_trips(routes)

    assert len(trips) == 1
    assert trips[0].vehicle_id == "v0"
    assert trips[0].scheduled_departure_s == 12.5
    assert trips[0].route_edges == ("a", "b", "c")


def test_informed_vehicle_selection_is_deterministic_and_nested() -> None:
    vehicle_ids = [f"vehicle_{index:04d}" for index in range(20)]

    quarter = _select_informed_vehicle_ids(vehicle_ids, seed=42, fraction=0.25)
    half = _select_informed_vehicle_ids(vehicle_ids, seed=42, fraction=0.50)
    all_vehicles = _select_informed_vehicle_ids(vehicle_ids, seed=42, fraction=1.0)

    assert _select_informed_vehicle_ids(vehicle_ids, 42, 0.25) == quarter
    assert not _select_informed_vehicle_ids(vehicle_ids, 42, 0.0)
    assert len(quarter) == 5
    assert len(half) == 10
    assert quarter <= half <= all_vehicles
    assert all_vehicles == set(vehicle_ids)
