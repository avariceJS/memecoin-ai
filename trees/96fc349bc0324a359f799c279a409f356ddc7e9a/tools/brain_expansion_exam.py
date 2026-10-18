"""A separate prospective comparison, reading the live source without sending GETs."""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from memecoin_continuous.runtime import guard_source
from memecoin_ml.data import read, sha
from memecoin_paper.runtime import stage
from memecoin_paper.service import exam_models
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]


def activate(experiment, root, bundle, port):
    verify(experiment)
    if read(experiment / "result.json")["fits"] != 8:
        raise ValueError("expansion_fit_membership")
    operator = BASE / "work/brain-b13/operator04"
    oc = read(operator / "config.json")
    source = Path(oc["source_root"])
    sc = guard_source(source)
    health = read(source / "health.json")
    now = time.time_ns()
    if (
        (source / "STOP").exists()
        or now - health["at_ns"] > 180 * 10**9
        or now < health["cooldown_ns"]
    ):
        raise ValueError("expansion_source_unhealthy")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))
    frozen = operator / "operator/jobs/job_ef9cb49cef6d7157/artifact/model"
    models = dict(
        frozen_b13=frozen, control=experiment / "control-model", broad=experiment / "broad-model"
    )
    refs = [
        dict(label=k, path=str(p.resolve()), sha=sha(p / "manifest.json"))
        for k, p in models.items()
    ]
    config = dict(
        activation_ns=now,
        exam_start_ns=now,
        expires_ns=min(now + 86400 * 10**9, sc["expires_ns"]),
        champion_model_path=oc["champion_model_path"],
        champion_model_sha=oc["champion_model_sha"],
        source=str(source),
        source_backend="continuous",
        source_runtime_pin=read(source / "deployment.json")["pin"],
        frozen_exam_models=refs,
        training_max_runs=0,
        real_execution_enabled=False,
        policy="B07-price-proxy-1",
        service_prefix="ai.memecoin.brain.expansion.",
        port=port,
        experiment_sha=sha(experiment / "manifest.json"),
        plan_sha=sha(BASE / "docs/brain-training-expansion.md"),
    )
    if config["expires_ns"] - now < 3600 * 10**9:
        raise ValueError("expansion_source_near_expiry")
    exam_models(config)
    root.mkdir(exist_ok=False)
    (root / "training").mkdir()
    publish(root / "config.json", config)
    publish(
        root / "activation.json",
        dict(
            at_ns=now,
            mode="frozen_future_data_comparison",
            backdated_predictions=False,
            promotion=False,
        ),
    )
    runtime = stage(root, bundle)
    script = (
        "import sys;sys.path.insert(0,sys.argv.pop(1));"
        "from memecoin_paper.runtime import main;main()"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(bundle / "runtime"), str(root), "paper"],
        capture_output=True,
        timeout=150,
    )
    if result.returncode or (root / "STOP").exists() or not (root / "dashboard.json").exists():
        publish(
            root / "activation-STOP",
            dict(category="isolated_paper_qualification", returncode=result.returncode),
        )
        raise ValueError("expansion_paper_qualification")
    domain = f"gui/{os.getuid()}"
    for phase in ("paper", "ui"):
        plist = Path.home() / "Library/LaunchAgents" / (config["service_prefix"] + phase + ".plist")
        result = subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist)], capture_output=True, timeout=30
        )
        if result.returncode:
            publish(
                root / "activation-STOP",
                dict(category="launchagent", phase=phase, returncode=result.returncode),
            )
            raise ValueError("expansion_launchagent")
    report = dict(
        root=str(root),
        models=refs,
        runtime=runtime,
        activation_ns=now,
        expires_ns=config["expires_ns"],
        port=port,
        provider_requests=0,
        training_job_loaded=False,
        existing_books_unchanged=True,
    )
    publish(root / "qualification.json", report)
    print(json.dumps(report))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("experiment", type=Path)
    p.add_argument("root", type=Path)
    p.add_argument("bundle", type=Path)
    p.add_argument("--port", type=int, default=8797)
    a = p.parse_args()
    activate(a.experiment.resolve(), a.root.resolve(), a.bundle.resolve(), a.port)


if __name__ == "__main__":
    main()
