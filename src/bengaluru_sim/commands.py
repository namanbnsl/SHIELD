from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class MissingSumoError(RuntimeError):
    pass


def find_sumo_binary(name: str) -> str:
    executable = f"{name}.exe" if os.name == "nt" else name
    on_path = shutil.which(executable)
    if on_path:
        return on_path

    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        candidate = Path(sumo_home) / "bin" / executable
        if candidate.is_file():
            return str(candidate)

    raise MissingSumoError(
        f"Could not find {name!r}. Run the project command through uv so the "
        "pinned eclipse-sumo package is available, or set SUMO_HOME."
    )


def run_sumo_command(command: list[str], log_file: Path) -> None:
    """Run a SUMO tool quietly, retaining its complete output for diagnostics."""
    result = subprocess.run(command, capture_output=True, text=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    output = result.stdout
    if result.stderr:
        output += ("\n" if output else "") + result.stderr
    log_file.write_text(output, encoding="utf-8")
    if result.returncode != 0:
        tail = "\n".join(output.splitlines()[-20:])
        raise RuntimeError(
            f"{Path(command[0]).name} failed with exit code {result.returncode}. "
            f"See {log_file}.\n{tail}"
        )
