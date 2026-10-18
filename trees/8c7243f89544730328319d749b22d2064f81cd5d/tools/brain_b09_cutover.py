"""Only a passed frozen qualification can authorize IO and exact paper migration."""

import os
import subprocess
import time
from pathlib import Path

from memecoin_continuous.hot import fresh_sample
from memecoin_continuous.migration import migrate
from memecoin_continuous.runtime import guard_source
from memecoin_ml.data import read, sha
from memecoin_paper.runtime import stage
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "work/brain-b09/source01"
OLD = BASE / "work/brain-b07/paper02"
ROOT = BASE / "work/brain-b09/paper01"
DOMAIN = "gui/" + str(os.getuid())


def command(*args):
    result = subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("launchctl_failed:" + str(args[-1]) + ":" + str(result.returncode))


def bootstrap(label):
    command("bootstrap", DOMAIN, str(Path.home() / "Library/LaunchAgents" / (label + ".plist")))


def main():
    q = read(SOURCE / "qualification.json")
    if (
        not q["approved"]
        or time.time_ns() - q["cutoff_ns"] > 300 * 10**9
        or (SOURCE / "STOP").exists()
    ):
        raise ValueError("source_not_currently_qualified")
    if q["config_sha"] != sha(SOURCE / "config.json"):
        raise ValueError("qualification_config_binding")
    guard_source(SOURCE, q["runtime_pin"])
    verify(SOURCE / "qualification-samples")
    if sha(SOURCE / "qualification-samples/manifest.json") != q["samples_sha"]:
        raise ValueError("qualification_samples_binding")
    publish(
        SOURCE / "activation.json",
        dict(
            config_sha=sha(SOURCE / "config.json"),
            runtime_pin=q["runtime_pin"],
            qualification_sha=sha(SOURCE / "qualification.json"),
            approved_ns=time.time_ns(),
            scope="72h_bounded_virtual_feed",
            paid_budget_usd=0,
        ),
    )
    bootstrap("ai.memecoin.brain.b09.source")
    bootstrap("ai.memecoin.brain.b09.audit")
    deadline = time.monotonic() + 120
    while not fresh_sample(SOURCE, time.time_ns())["fresh"]:
        if time.monotonic() > deadline or (SOURCE / "STOP").exists():
            raise ValueError("freshness_lost_before_paper_fence")
        time.sleep(5)
    fenced = []
    try:
        for phase in ("paper", "train", "ui"):
            label = "ai.memecoin.brain.b07." + phase
            command("bootout", DOMAIN + "/" + label)
            fenced.append(label)
        for i in range(7):
            try:
                carried = migrate(
                    OLD, ROOT, SOURCE, q["runtime_pin"], BASE / "work/brain-b01/dev01/models/000000"
                )
                break
            except BlockingIOError:
                if i == 6:
                    raise
                time.sleep(5)
        deployed = stage(ROOT, BASE / "work/brain-b09/paper-runtime01")
        for phase in ("paper", "train", "ui"):
            bootstrap("ai.memecoin.brain.b09." + phase)
    except Exception as exc:
        if ROOT.exists():
            publish(
                ROOT / "cutover-incident.json",
                dict(error_type=type(exc).__name__, at_ns=time.time_ns()),
            )
        # Fence any partially started replacement before restoring old UI/writers.
        for phase in ("paper", "train", "ui"):
            subprocess.run(
                ["launchctl", "bootout", DOMAIN + "/ai.memecoin.brain.b09." + phase],
                capture_output=True,
            )
        for label in fenced:
            bootstrap(label)
        raise
    publish(
        ROOT / "handoff.json",
        dict(
            carried=carried,
            runtime=deployed,
            old_jobs_fenced=True,
            source_qualification_sha=sha(SOURCE / "qualification.json"),
        ),
    )
    print(dict(carried=carried, runtime=deployed), flush=True)


if __name__ == "__main__":
    main()
