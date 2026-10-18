"""Killable child boundary for bounded qualification jobs (no service)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_child(command: list[str], log: Path, *, timeout: float) -> int:
    if not 0 < timeout <= 600:
        raise ValueError("child_deadline_out_of_bounds")
    with log.open("x") as stream:
        try:
            return subprocess.run(
                command, stdout=stream, stderr=stream, timeout=timeout, check=False
            ).returncode
        except subprocess.TimeoutExpired:
            # subprocess.run kills and waits for its child; workers must not spawn
            # grandchildren. Partial files are deliberately retained.
            return 124
