from __future__ import annotations

from .commands import find_sumo_binary, run_sumo_command
from .config import SimulationConfig


def run_simulation(config: SimulationConfig) -> None:
    command = [
        find_sumo_binary("sumo"),
        "--net-file",
        str(config.network_file),
        "--route-files",
        str(config.routes_file),
        "--seed",
        str(config.seed),
        "--threads",
        "1",
        "--end",
        str(config.simulation_end_s),
        "--tripinfo-output",
        str(config.tripinfo_file),
        "--tripinfo-output.write-unfinished",
        "true",
        "--tripinfo-output.write-undeparted",
        "true",
        "--statistic-output",
        str(config.statistics_file),
        "--no-step-log",
        "true",
        "--duration-log.statistics",
        "true",
    ]
    run_sumo_command(command, config.run_dir / "sumo.log")
