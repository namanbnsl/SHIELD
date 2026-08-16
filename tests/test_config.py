from pathlib import Path

import pytest

from bengaluru_sim.config import SimulationConfig


def test_default_config_is_valid(tmp_path: Path) -> None:
    config = SimulationConfig(project_dir=tmp_path)
    config.validate()
    assert config.vehicles == 1_000
    assert config.result_file == tmp_path / "results" / "run.csv"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"vehicles": 0}, "vehicles"),
        ({"demand_duration_s": 0}, "demand duration"),
        (
            {"demand_duration_s": 100, "simulation_end_s": 100},
            "simulation end",
        ),
        ({"min_trip_distance_m": -1}, "minimum trip distance"),
        ({"bbox": (1, 2, 1, 3)}, "bbox"),
    ],
)
def test_invalid_config_is_rejected(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    config = SimulationConfig(project_dir=tmp_path, **overrides)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=message):
        config.validate()

