"""One reviewed recovery revision; B09 source, STOP and quotas stay untouched."""

import os
import shutil
import subprocess
import time
from pathlib import Path

from memecoin_continuous.migration import migrate
from memecoin_continuous.runtime import guard_source
from memecoin_ml.data import read, sha
from memecoin_paper.audit import replay, snapshot
from memecoin_paper.runtime import guard, stage
from memecoin_paper.service import models
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
OLD = BASE / "work/brain-b09/paper01"
SOURCE = BASE / "work/brain-b09/source01"
OUT = BASE / "work/brain-b10"
ROOT = OUT / "paper01"
DOMAIN = "gui/" + str(os.getuid())


def launch(action, label):
    target = str(Path.home() / "Library/LaunchAgents" / (label + ".plist"))
    args = (
        ["launchctl", action, DOMAIN, target]
        if action == "bootstrap"
        else ["launchctl", action, DOMAIN + "/" + label]
    )
    result = subprocess.run(args, capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("launchctl_" + action + "_failed_" + str(result.returncode))


def main():
    if ROOT.exists() or not (OLD / "STOP").exists():
        raise ValueError("one_time_failed_parent_required")
    c = guard(OLD)
    guard_source(SOURCE, c["source_runtime_pin"])
    if (SOURCE / "STOP").exists() or time.time_ns() >= c["expires_ns"]:
        raise ValueError("source_stopped_or_expired")
    if time.time_ns() - read(SOURCE / "health.json")["at_ns"] > 180 * 10**9:
        raise ValueError("source_heartbeat_stale")
    OUT.mkdir(exist_ok=True)
    evidence = OUT / "incident-evidence"
    evidence.mkdir()
    snapshot(OLD, evidence / "paper-snapshot")
    for name in (
        "STOP",
        "incident-paper-1791120945079608000.json",
        "paper-worker-1791120937417954000.log",
    ):
        shutil.copyfile(OLD / name, evidence / name)
    start = time.monotonic()
    for _ in range(300):
        guard(OLD)
    publish(
        evidence / "diagnostic.json",
        dict(
            at_ns=time.time_ns(),
            guard_checks=300,
            guard_failures=0,
            seconds=time.monotonic() - start,
            original_cause="unknown_supervisor_guard",
            replay=replay(OLD),
            models=[sha(p / "manifest.json") for p in models(OLD)],
            source_bindings={
                n: sha(SOURCE / n)
                for n in (
                    "config.json",
                    "deployment.json",
                    "activation.json",
                    "seed.json",
                    "universe.json",
                    "provider-seed.json",
                )
            },
            plan_sha=sha(BASE / "docs/brain-operational-recovery.md"),
        ),
    )
    seal(evidence)
    for phase in ("paper", "train"):
        launch("bootout", "ai.memecoin.brain.b09." + phase)
    carried = migrate(
        OLD,
        ROOT,
        SOURCE,
        c["source_runtime_pin"],
        Path(c["champion_model_path"]),
        service_prefix="ai.memecoin.brain.b10.",
    )
    deployed = stage(ROOT, OUT / "paper-runtime01")
    publish(
        ROOT / "revision-review.json",
        dict(
            at_ns=time.time_ns(),
            source_unchanged=True,
            original_stop_preserved=True,
            incident_evidence_sha=sha(evidence / "manifest.json"),
            plan_sha=read(evidence / "diagnostic.json")["plan_sha"],
            code_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=BASE, text=True
            ).strip(),
            config_sha=sha(ROOT / "config.json"),
            runtime_pin=deployed["pin"],
            no_renewal=True,
            unknown_cause_not_claimed_fixed=True,
        ),
    )
    for phase in ("paper", "train"):
        launch("bootstrap", "ai.memecoin.brain.b10." + phase)
    # No source restart and no destructive rollback of a partially published ledger.
    # If the new worker fails, preserve its STOP and keep old read-only UI until review.
    deadline = time.monotonic() + 120
    while not (ROOT / "dashboard.json").exists():
        if (ROOT / "STOP").exists() or time.monotonic() >= deadline:
            raise ValueError("replacement_failed_or_no_dashboard")
        time.sleep(1)
    launch("bootout", "ai.memecoin.brain.b09.ui")
    launch("bootstrap", "ai.memecoin.brain.b10.ui")
    publish(ROOT / "handoff.json", dict(carried=carried, runtime=deployed, at_ns=time.time_ns()))
    print(dict(carried=carried, runtime=deployed))


if __name__ == "__main__":
    main()
