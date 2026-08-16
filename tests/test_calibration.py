from pathlib import Path

from bengaluru_sim.calibration import _teleports


def test_teleports_reads_total(tmp_path: Path) -> None:
    statistics = tmp_path / "statistics.xml"
    statistics.write_text(
        '<statistics><teleports total="3" jam="2" yield="1"/></statistics>',
        encoding="utf-8",
    )

    assert _teleports(statistics) == 3
