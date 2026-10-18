"""Freeze one finite local calendar experiment and install its read-only launchd job."""

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from calendar_schedule import snapshot_hashes

from memecoin_ai.domain import digest
from memecoin_ai.prospective import hashes, read, write
from memecoin_ai.sources import Capture


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    a = p.parse_args()
    repo = Path.cwd()
    root = a.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    runtime = root / "runtime"
    (runtime / "tools").mkdir(parents=True)
    spec = read(repo / "configs/calendar-execution-v1.json")
    for f in (repo / "tools").glob("*.py"):
        shutil.copy2(f, runtime / "tools" / f.name)
    wheel = sorted((repo / "dist").glob("*.whl"))[-1]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--target",
            str(runtime / "pkg"),
            str(wheel),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    for d in runtime.rglob("__pycache__"):
        shutil.rmtree(d)
    for i, name in enumerate(spec["references"]):
        Capture.load(repo / name).save(root / "references" / f"{i:03}")
    (root / "windows").mkdir()
    (root / "outcomes").mkdir()
    now = time.time_ns()
    plan = dict(
        stage="D43",
        created_ns=now,
        due_ns=[now + s * 10**9 for s in spec["window_offsets_seconds"]],
        spec=spec,
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        wheel_sha256=digest(wheel.read_bytes()),
        python_version=sys.version,
        seed=None,
    )
    write(root / "plan.json", plan)
    write(root / "plan-sha.json", digest((root / "plan.json").read_bytes()))
    write(root / "runtime-manifest.json", snapshot_hashes(runtime))
    write(root / "references-manifest.json", hashes(root / "references"))
    label = "ai.memecoin.d43.calendar01"
    target = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    target.parent.mkdir(parents=True, exist_ok=True)
    plist = dict(
        Label=label,
        ProgramArguments=[
            str(Path(sys.executable).absolute()),
            "-B",
            str(runtime / "tools/calendar_schedule.py"),
            "run-due",
            str(root),
        ],
        WorkingDirectory=str(repo),
        EnvironmentVariables=dict(PYTHONPATH=str(runtime / "pkg"), PYTHONDONTWRITEBYTECODE="1"),
        StartInterval=60,
        RunAtLoad=True,
        ProcessType="Background",
        StandardOutPath=str(root / "service.log"),
        StandardErrorPath=str(root / "service-error.log"),
    )
    with target.open("xb") as f:
        plistlib.dump(plist, f)
    write(
        root / "launchd.json",
        dict(
            label=label,
            plist_path=str(target),
            plist_sha256=digest(target.read_bytes()),
            uid=os.getuid(),
        ),
    )
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)], check=True)
    print("Installed finite calendar job:", label)


if __name__ == "__main__":
    main()
