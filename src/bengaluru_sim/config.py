from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_BBOX = (77.62, 12.90, 77.69, 12.94)
DEFAULT_SEED = 42


def project_result_path(project_dir: Path, filename: str) -> Path:
    return project_dir / "results" / filename


@dataclass(frozen=True)
class SimulationConfig:
    project_dir: Path
    vehicles: int = 1_000
    seed: int = DEFAULT_SEED
    demand_duration_s: float = 3_600.0
    simulation_end_s: float = 7_200.0
    min_trip_distance_m: float = 800.0
    bbox: tuple[float, float, float, float] = DEFAULT_BBOX
    refresh_network: bool = False

    @property
    def osm_file(self) -> Path:
        return self.project_dir / "data" / "osm" / "bengaluru.osm.xml"

    @property
    def osm_bbox_file(self) -> Path:
        return self.project_dir / "data" / "osm" / "bengaluru.bbox.txt"

    @property
    def network_file(self) -> Path:
        return self.project_dir / "data" / "network" / "bengaluru.net.xml"

    @property
    def run_dir(self) -> Path:
        return self.output_dir()

    def output_dir(self, run_name: str | None = None) -> Path:
        suffix = "" if run_name is None else f"-{run_name}"
        return self.project_dir / "outputs" / f"seed-{self.seed}{suffix}"

    def result_path(self, filename: str) -> Path:
        return project_result_path(self.project_dir, filename)

    def named_result_paths(self, run_name: str) -> tuple[Path, Path]:
        stem = run_name.replace("-", "_")
        return (
            self.result_path(f"{stem}_run.csv"),
            self.result_path(f"{stem}_run_summary.csv"),
        )

    @property
    def routes_file(self) -> Path:
        return self.run_dir / "demand.rou.xml"

    @property
    def manifest_file(self) -> Path:
        return self.run_dir / "demand.csv"

    @property
    def tripinfo_file(self) -> Path:
        return self.run_dir / "tripinfo.xml"

    @property
    def statistics_file(self) -> Path:
        return self.run_dir / "statistics.xml"

    @property
    def result_file(self) -> Path:
        return self.result_path("run.csv")

    @property
    def summary_file(self) -> Path:
        return self.result_path("run_summary.csv")

    def validate(self) -> None:
        west, south, east, north = self.bbox
        if self.vehicles <= 0:
            raise ValueError("vehicles must be positive")
        if self.demand_duration_s <= 0:
            raise ValueError("demand duration must be positive")
        if self.simulation_end_s <= self.demand_duration_s:
            raise ValueError("simulation end must be after the demand window")
        if self.min_trip_distance_m < 0:
            raise ValueError("minimum trip distance cannot be negative")
        if west >= east or south >= north:
            raise ValueError("bbox must be WEST,SOUTH,EAST,NORTH")
