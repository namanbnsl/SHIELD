from __future__ import annotations

from pathlib import Path

import pytest

from bengaluru_sim import gui


def test_gui_requires_generated_files(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="shield-sim --seed 7"):
        gui.open_gui(tmp_path, seed=7)


def test_gui_opens_matching_seed_demand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    network = tmp_path / "data" / "network" / "bengaluru.net.xml"
    routes = tmp_path / "outputs" / "seed-7" / "demand.rou.xml"
    network.parent.mkdir(parents=True)
    routes.parent.mkdir(parents=True)
    network.write_text("<net/>", encoding="utf-8")
    routes.write_text("<routes/>", encoding="utf-8")

    recorded: list[list[str]] = []

    class Result:
        returncode = 0

    monkeypatch.setattr(gui, "find_sumo_binary", lambda name: "/bin/sumo-gui")
    monkeypatch.setattr(
        gui.subprocess,
        "run",
        lambda command, check: recorded.append(command) or Result(),
    )

    assert gui.open_gui(tmp_path, seed=7, start=True) == 0
    assert recorded == [
        [
            "/bin/sumo-gui",
            "--net-file",
            str(network),
            "--route-files",
            str(routes),
            "--seed",
            "7",
            "--start",
        ]
    ]
