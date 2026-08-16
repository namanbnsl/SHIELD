from __future__ import annotations

import csv
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .demand import Trip


@dataclass(frozen=True)
class VehicleResult:
    vehicle_id: str
    scheduled_departure_time_s: float
    departure_time_s: float | None
    arrival_time_s: float | None
    travel_time_s: float | None
    status: str
    origin_edge: str
    destination_edge: str


@dataclass(frozen=True)
class Summary:
    vehicles: int
    completed: int
    unfinished: int
    mean_travel_time_s: float | None
    median_travel_time_s: float | None


def _optional_nonnegative(value: str | None) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    return parsed if parsed >= 0 else None


def collect_results(
    trips: list[Trip], tripinfo_file: Path
) -> tuple[list[VehicleResult], Summary]:
    raw: dict[str, ET.Element] = {}
    if tripinfo_file.exists():
        root = ET.parse(tripinfo_file).getroot()
        raw = {item.get("id", ""): item for item in root.findall("tripinfo")}

    results: list[VehicleResult] = []
    for trip in trips:
        item = raw.get(trip.vehicle_id)
        departure = _optional_nonnegative(item.get("depart")) if item is not None else None
        arrival = _optional_nonnegative(item.get("arrival")) if item is not None else None
        vaporized = item is not None and item.get("vaporized", "").lower() == "true"
        if departure is None:
            status = "not_departed" if item is not None else "missing"
        elif arrival is None:
            status = "vaporized" if vaporized else "unfinished"
        else:
            status = "completed"
        travel_time = arrival - departure if status == "completed" else None
        results.append(
            VehicleResult(
                trip.vehicle_id,
                trip.scheduled_departure_s,
                departure,
                arrival,
                travel_time,
                status,
                trip.origin_edge,
                trip.destination_edge,
            )
        )

    travel_times = [
        result.travel_time_s
        for result in results
        if result.travel_time_s is not None
    ]
    completed = len(travel_times)
    summary = Summary(
        vehicles=len(results),
        completed=completed,
        unfinished=len(results) - completed,
        mean_travel_time_s=statistics.fmean(travel_times) if travel_times else None,
        median_travel_time_s=statistics.median(travel_times) if travel_times else None,
    )
    return results, summary


def write_results(path: Path, results: list[VehicleResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "vehicle_id",
                "scheduled_departure_time_s",
                "departure_time_s",
                "arrival_time_s",
                "travel_time_s",
                "status",
                "origin_edge",
                "destination_edge",
            ]
        )
        for result in results:
            writer.writerow(
                [
                    result.vehicle_id,
                    f"{result.scheduled_departure_time_s:.2f}",
                    "" if result.departure_time_s is None else f"{result.departure_time_s:.2f}",
                    "" if result.arrival_time_s is None else f"{result.arrival_time_s:.2f}",
                    "" if result.travel_time_s is None else f"{result.travel_time_s:.2f}",
                    result.status,
                    result.origin_edge,
                    result.destination_edge,
                ]
            )


def write_summary(path: Path, summary: Summary, metadata: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, object, str]] = [
        ("vehicles", summary.vehicles, "count"),
        ("completed_trips", summary.completed, "count"),
        ("unfinished_or_stranded_trips", summary.unfinished, "count"),
        (
            "mean_travel_time",
            "" if summary.mean_travel_time_s is None else f"{summary.mean_travel_time_s:.2f}",
            "seconds",
        ),
        (
            "median_travel_time",
            "" if summary.median_travel_time_s is None else f"{summary.median_travel_time_s:.2f}",
            "seconds",
        ),
    ]
    rows.extend((key, value, "") for key, value in metadata.items())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value", "unit"])
        writer.writerows(rows)

