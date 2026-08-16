from __future__ import annotations

import csv
from pathlib import Path

from bengaluru_sim.demand import Trip
from bengaluru_sim.metrics import collect_results, write_results, write_summary


def _trip(index: int) -> Trip:
    return Trip(f"vehicle_{index:04d}", index * 10.0, "from", "to", ("from", "to"))


def test_collect_results_accounts_for_every_requested_trip(tmp_path: Path) -> None:
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text(
        """<?xml version="1.0"?>
<tripinfos>
  <tripinfo id="vehicle_0000" depart="2" arrival="62" duration="60" timeLoss="10"/>
  <tripinfo id="vehicle_0001" depart="12" arrival="-1" duration="88" timeLoss="20"/>
  <tripinfo id="vehicle_0002" depart="-1" arrival="-1" duration="-1"/>
  <tripinfo id="vehicle_0003" depart="32" arrival="-1" vaporized="true" timeLoss="30"/>
</tripinfos>
""",
        encoding="utf-8",
    )
    results, summary = collect_results([_trip(i) for i in range(5)], tripinfo)
    assert [result.status for result in results] == [
        "completed",
        "unfinished",
        "not_departed",
        "vaporized",
        "missing",
    ]
    assert summary.vehicles == 5
    assert summary.completed == 1
    assert summary.unfinished == 4
    assert summary.mean_travel_time_s == 60
    assert summary.median_travel_time_s == 60
    assert summary.total_time_loss_s == 60
    assert summary.mean_time_loss_s == 20


def test_csv_writers_produce_structured_outputs(tmp_path: Path) -> None:
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text(
        '<tripinfos><tripinfo id="vehicle_0000" depart="1" arrival="3"/></tripinfos>',
        encoding="utf-8",
    )
    results, summary = collect_results([_trip(0)], tripinfo)
    result_file = tmp_path / "results" / "run.csv"
    summary_file = tmp_path / "results" / "run_summary.csv"
    write_results(result_file, results)
    write_summary(summary_file, summary, {"seed": 42})

    with result_file.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["travel_time_s"] == "2.00"
    assert rows[0]["status"] == "completed"

    with summary_file.open(newline="", encoding="utf-8") as handle:
        metrics = {row["metric"]: row["value"] for row in csv.DictReader(handle)}
    assert metrics["completed_trips"] == "1"
    assert metrics["seed"] == "42"
