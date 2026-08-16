from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .commands import MissingSumoError, find_sumo_binary
from .config import DEFAULT_SEED, SimulationConfig


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open a generated Bengaluru simulation in SUMO GUI."
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="generated demand seed to open (default: %(default)s)",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="start vehicle movement immediately instead of waiting for Play",
    )
    return parser


def open_gui(project_dir: Path, seed: int, start: bool = False) -> int:
    config = SimulationConfig(project_dir=project_dir, seed=seed)
    missing = [
        path
        for path in (config.network_file, config.routes_file)
        if not path.is_file()
    ]
    if missing:
        paths = ", ".join(str(path.relative_to(project_dir)) for path in missing)
        raise RuntimeError(
            f"Missing generated simulation files: {paths}. "
            f"Run 'uv run shield-sim --seed {seed}' first."
        )

    command = [
        find_sumo_binary("sumo-gui"),
        "--net-file",
        str(config.network_file),
        "--route-files",
        str(config.routes_file),
        "--seed",
        str(seed),
    ]
    if start:
        command.append("--start")
    return subprocess.run(command, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return open_gui(Path.cwd(), args.seed, args.start)
    except (MissingSumoError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
