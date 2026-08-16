from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import TypeVar

from .config import DEFAULT_SEED, SimulationConfig


Task = TypeVar("Task")
Result = TypeVar("Result")


def scenario_dir(
    project_dir: Path,
    experiment_name: str,
    vehicles: int,
    seed: int,
    *,
    group_by_seed: bool = True,
) -> Path:
    path = project_dir / "outputs" / experiment_name / f"vehicles-{vehicles}"
    return path / f"seed-{seed}" if group_by_seed else path


def prepare_scenario(
    project_dir: Path,
    experiment_name: str,
    vehicles: int,
    seed: int = DEFAULT_SEED,
    *,
    group_by_seed: bool = True,
) -> SimulationConfig:
    """Create one experiment workspace that reuses the immutable network data."""
    scenario = scenario_dir(
        project_dir,
        experiment_name,
        vehicles,
        seed,
        group_by_seed=group_by_seed,
    )
    scenario.mkdir(parents=True, exist_ok=True)
    data_link = scenario / "data"
    if not data_link.exists():
        data_link.symlink_to(project_dir / "data", target_is_directory=True)
    return SimulationConfig(project_dir=scenario, vehicles=vehicles, seed=seed)


def run_tasks(
    function: Callable[[Task], Result], tasks: Iterable[Task], parallel: int
) -> list[Result]:
    """Run experiment tasks serially or with the requested worker count."""
    if parallel <= 0:
        raise ValueError("parallel must be positive")
    task_list = list(tasks)
    if parallel == 1:
        return [function(task) for task in task_list]
    with ProcessPoolExecutor(max_workers=parallel) as executor:
        return list(executor.map(function, task_list))
