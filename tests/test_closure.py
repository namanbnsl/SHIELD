from pathlib import Path

from bengaluru_sim.closure import _read_trips


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
