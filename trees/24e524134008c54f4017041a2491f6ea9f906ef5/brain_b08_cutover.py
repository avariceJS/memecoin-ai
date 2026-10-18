"""Reviewed qualification -> short writer fence -> exact ledger migration -> new jobs."""

import subprocess
from pathlib import Path

from memecoin_continuous.migration import migrate
from memecoin_continuous.runtime import guard_source
from memecoin_ml.data import read
from memecoin_paper.runtime import stage
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
OLD = BASE / "work/brain-b07/paper02"
ROOT = BASE / "work/brain-b08/paper01"
SOURCE = BASE / "work/brain-b08/source01"


def command(*args):
    r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise ValueError("launchctl_failed:" + str(args[-1]) + ":" + str(r.returncode))


def main():
    q = read(SOURCE / "qualification-v2.json")
    if not q["approved"] or (SOURCE / "STOP").exists():
        raise ValueError("source_not_qualified")
    guard_source(SOURCE, q["runtime_pin"])
    # The old frozen source is already STOP after its 429. No STOP is removed.
    for phase in ("paper", "train", "ui"):
        command("launchctl", "bootout", "gui/501/ai.memecoin.brain.b07." + phase)
    try:
        carried = migrate(
            OLD, ROOT, SOURCE, q["runtime_pin"], BASE / "work/brain-b01/dev01/models/000000"
        )
        deployed = stage(ROOT, BASE / "work/brain-b08/paper-runtime01")
    except Exception:
        for phase in ("paper", "train", "ui"):
            command(
                "launchctl",
                "bootstrap",
                "gui/501",
                str(
                    Path.home()
                    / "Library/LaunchAgents"
                    / ("ai.memecoin.brain.b07." + phase + ".plist")
                ),
            )
        raise
    for phase in ("source", "audit", "paper", "train", "ui"):
        command(
            "launchctl",
            "bootstrap",
            "gui/501",
            str(
                Path.home() / "Library/LaunchAgents" / ("ai.memecoin.brain.b08." + phase + ".plist")
            ),
        )
    publish(
        ROOT / "handoff.json",
        dict(carried=carried, runtime=deployed, old_jobs_fenced=True, old_STOPs_preserved=True),
    )
    print(dict(carried=carried, runtime=deployed), flush=True)


if __name__ == "__main__":
    main()
