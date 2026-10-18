"""Freeze curriculum membership, then run one bounded, no-network experiment."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from memecoin_brain.data import load_dataset
from memecoin_curriculum.design import experiments
from memecoin_ml.data import read, sha
from memecoin_operational.supervisor import Watch
from memecoin_registry.contracts import digest, seal
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE / "work/brain-b12"
DESIGN = ROOT / "design01"
RUN = ROOT / "run01"


def prepare():
    from memecoin_brain.model import identity

    dataset = BASE / "work/brain-b11/textbook02/dataset-full"
    bundle = load_dataset(dataset)
    bounds = read(dataset.parent / "result.json")["deployed_rolling"]["global_split"]
    exps = list(experiments(bundle, bounds))
    code = identity()
    for name in ("design.py", "training.py"):
        code["memecoin_curriculum/" + name] = sha(BASE / "src/memecoin_curriculum" / name)
    DESIGN.mkdir(exist_ok=False)
    publish(
        DESIGN / "binding.json",
        dict(
            created_ns=time.time_ns(),
            dataset=str(dataset),
            dataset_sha=sha(dataset / "manifest.json"),
            source_root=str(BASE / "src"),
            code=code,
            bounds=bounds,
            plan_sha=sha(BASE / "docs/brain-curriculum-comparison.md"),
            code_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=BASE, text=True
            ).strip(),
            fraction_selection=False,
            steps=1000,
            batch_decisions=64,
            max_fits=36,
            max_fit_seconds=180,
            max_total_seconds=7200,
            F01=False,
            provider_queries=0,
            validation_membership=digest([s["id"] for s in exps[0]["validation"]]),
            test_membership=digest([s["id"] for s in exps[0]["test"]]),
        ),
    )
    publish(DESIGN / "membership.json", {e["name"]: e["support"] for e in exps})
    seal(DESIGN)
    print(
        json.dumps(
            {
                e["name"]: {
                    k: e["support"][k] for k in ["decisions", "pools", "days", "effective_pools"]
                }
                for e in exps
            }
        )
    )


def launch():
    # Deliberately separate from the <=600s source/paper subprocess contract.
    # Existing runtime and its limits are not relaxed for an offline training campaign.
    if RUN.exists():
        raise ValueError("curriculum_attempt_exists")
    watch = Watch(7200)
    rfd, wfd = os.pipe()
    environment = dict(os.environ, MEMECOIN_LEASE_FD=str(rfd), PYTHONDONTWRITEBYTECODE="1")
    publish(
        ROOT / "run01-reservation.json",
        dict(design_sha=sha(DESIGN / "manifest.json"), started_ns=time.time_ns(), max_seconds=7200),
    )
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "worker"]
    category = "completed"
    started = time.monotonic()
    with (ROOT / "run01.log").open("x") as log:
        process = subprocess.Popen(
            command,
            cwd=BASE,
            stdout=log,
            stderr=log,
            env=environment,
            pass_fds=(rfd,),
            start_new_session=True,
        )
        os.close(rfd)
        try:
            while process.poll() is None:
                try:
                    if watch.remaining() <= 0:
                        raise TimeoutError("curriculum_total_deadline")
                except Exception:
                    category = "deadline_or_clock"
                    os.killpg(process.pid, signal.SIGKILL)
                    break
                time.sleep(0.5)
            code = process.wait()
        finally:
            os.close(wfd)
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    if code and category == "completed":
        category = "worker_failed"
    result = dict(exit_code=code, category=category, seconds=time.monotonic() - started)
    publish(ROOT / "run01-process.json", result)
    if code and not (ROOT / "STOP").exists():
        publish(ROOT / "STOP", dict(**result, no_retry=True))
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "run", "worker"])
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "run":
        launch()
    else:
        from memecoin_curriculum.training import run
        from memecoin_operational.worker import lease_watchdog

        lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
        os.nice(10)
        run(DESIGN, RUN)


if __name__ == "__main__":
    main()
