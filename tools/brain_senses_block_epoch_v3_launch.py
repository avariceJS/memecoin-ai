#!/usr/bin/env python3
"""LaunchAgent wrapper: run one finite v3 attempt, then unload after durable STOP."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.brain_senses_block_epoch_v3_schedule import _seal_stop, run_bucket  # noqa: E402

DEFAULT_LABEL = "ai.memecoin.brain.senses.helius-inventory-v3"


def _bootout(
    label: str, *, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> bool:
    target = f"gui/{os.getuid()}/{label}"
    result = runner(
        ["/bin/launchctl", "bootout", target],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode == 0


def run(
    root: Path,
    lineage: Path,
    label: str,
    *,
    runner=subprocess.run,
    scheduler=run_bucket,
) -> int:
    stop = root / "STOP"
    if stop.is_file():
        # A failed prior bootout is retried here; STOP is checked before scheduler or RPC setup.
        return 0 if _bootout(label, runner=runner) else 1

    try:
        scheduler(root, lineage_path=lineage)
    except Exception as exc:
        if not stop.exists():
            _seal_stop(root, "scheduler_process_failed", error_class=type(exc).__name__)
    if stop.exists():
        return 0 if _bootout(label, runner=runner) else 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-base", required=True, type=Path)
    parser.add_argument("--lineage", required=True, type=Path)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    args = parser.parse_args(argv)
    return run(args.root_base, args.lineage, args.label)


if __name__ == "__main__":
    raise SystemExit(main())
