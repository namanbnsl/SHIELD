from __future__ import annotations

from pathlib import Path

from .commands import find_sumo_binary, run_sumo_command
from .config import SimulationConfig


def build_sumo_command(
    config: SimulationConfig,
    *,
    end_s: float | None = None,
    output_dir: Path | None = None,
    write_metrics: bool = True,
    suppress_warnings: bool = False,
) -> list[str]:
    """Build the deterministic SUMO command shared by baseline and disruptions."""
    run_dir = config.run_dir if output_dir is None else output_dir
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
        str(config.simulation_end_s if end_s is None else end_s),
        "--no-step-log",
        "true",
    ]
    if write_metrics:
        command.extend(
            [
                "--tripinfo-output",
                str(run_dir / "tripinfo.xml"),
                "--tripinfo-output.write-unfinished",
                "true",
                "--tripinfo-output.write-undeparted",
                "true",
                "--statistic-output",
                str(run_dir / "statistics.xml"),
                "--duration-log.statistics",
                "true",
            ]
        )
    if suppress_warnings:
        command.extend(["--no-warnings", "true"])
    return command


def run_simulation(config: SimulationConfig) -> None:
    command = build_sumo_command(config)
    run_sumo_command(command, config.run_dir / "sumo.log")
