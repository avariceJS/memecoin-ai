"""A separate frozen future exam. Never changes B09/B10 jobs, config or training allowance."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from memecoin_continuous.runtime import guard_source
from memecoin_ml.data import read, sha
from memecoin_paper.health import operational
from memecoin_paper.runtime import guard, stage
from memecoin_paper.service import exam_models
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "work/brain-b12"
ROOT = OUT / "paper01"
SOURCE = BASE / "work/brain-b09/source01"
CONTROL = BASE / "work/brain-b10/paper01"


def main():
    if ROOT.exists():
        raise ValueError("exam_exists_no_retry")
    run = OUT / "run01"
    verify(run)
    result = read(run / "result.json")
    if result["fits"] != 36 or (OUT / "STOP").exists():
        raise ValueError("exam_incomplete_experiment")
    source = guard_source(SOURCE)
    control = guard(CONTROL)
    health = operational(CONTROL)
    if not health["paper"]["healthy"] or not health["source"]["healthy"]:
        raise ValueError("exam_control_not_healthy")
    if shutil.disk_usage(OUT).free < 4 * 1024**3:
        raise ValueError("exam_disk_headroom")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 8791))
    refs = [
        dict(
            path=str(run / (arm + "-100")),
            sha=sha(run / (arm + "-100") / "manifest.json"),
            label=arm,
        )
        for arm in ("recent", "full", "balanced")
    ]
    now = time.time_ns()
    config = {
        k: control[k]
        for k in [
            "champion_model_path",
            "champion_model_sha",
            "source",
            "source_backend",
            "source_runtime_pin",
            "policy",
        ]
    }
    config.update(
        activation_ns=now,
        exam_start_ns=now,
        expires_ns=min(now + 86400 * 10**9, source["expires_ns"]),
        training_max_runs=0,
        frozen_exam_models=refs,
        real_execution_enabled=False,
        service_prefix="ai.memecoin.brain.b12.",
        port=8791,
        experiment_manifest_sha=sha(run / "manifest.json"),
        plan_sha=sha(BASE / "docs/brain-curriculum-comparison.md"),
    )
    exam_models(config)
    ROOT.mkdir()
    (ROOT / "training").mkdir()
    publish(ROOT / "config.json", config)
    publish(
        ROOT / "activation.json",
        dict(
            at_ns=now,
            mode="frozen_exploratory_exam",
            real_execution=False,
            source_queries=0,
            experiment_sha=config["experiment_manifest_sha"],
            control_health=health,
        ),
    )
    runtime = stage(ROOT, OUT / "paper-runtime01")
    script = (
        "import sys;sys.path.insert(0,sys.argv.pop(1));"
        "from memecoin_paper.runtime import main;main()"
    )
    command = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        script,
        str(OUT / "paper-runtime01/runtime"),
        str(ROOT),
        "paper",
    ]
    qual = subprocess.run(command, cwd=BASE, capture_output=True, timeout=150)
    if qual.returncode or (ROOT / "STOP").exists() or not (ROOT / "dashboard.json").exists():
        publish(
            OUT / "activation-STOP",
            dict(category="isolated_paper_qualification", returncode=qual.returncode),
        )
        raise ValueError("exam_qualification_failed")
    control_after = operational(CONTROL)
    if not control_after["paper"]["healthy"] or not control_after["source"]["healthy"]:
        publish(OUT / "activation-STOP", dict(category="control_health_after_qualification"))
        raise ValueError("exam_control_health_failed")
    publish(
        ROOT / "qualification.json",
        dict(
            healthy=operational(ROOT),
            control=control_after,
            runtime=runtime,
            fits=36,
            provider_queries=0,
        ),
    )
    domain = "gui/" + str(os.getuid())
    for phase in ("paper", "ui"):
        label = "ai.memecoin.brain.b12." + phase
        plist = Path.home() / "Library/LaunchAgents" / (label + ".plist")
        launched = subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist)], capture_output=True, timeout=30
        )
        if launched.returncode:
            publish(
                OUT / "activation-STOP",
                dict(category="launchctl", phase=phase, returncode=launched.returncode),
            )
            raise ValueError("exam_launch_failed")
    # The generic stage helper creates a train plist, but it is NEVER loaded; max_runs=0 as defense.
    report = dict(
        activated_ns=time.time_ns(),
        expires_ns=config["expires_ns"],
        root=str(ROOT),
        runtime=runtime,
        models=refs,
        selected=result["selection"],
        control_unchanged=True,
        training_job_loaded=False,
        port=8791,
    )
    publish(OUT / "activation-result.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
