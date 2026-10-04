"""Frozen source supervisor; failures do not silently renew any budget or expiry."""

import fcntl
import json
import os
import plistlib
import shutil
import sys
import time
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_operational.contract import GateError
from memecoin_operational.supervisor import Watch, child
from memecoin_paper.runtime import atomic
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish


def stage_source(root, bundle):
    bundle.mkdir()
    src = Path(__file__).parent.parent
    for p in src.glob("memecoin_*/*.py"):
        dest = bundle / "runtime" / p.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    publish(
        bundle / "binding.json",
        dict(
            root=str(root.resolve()),
            python_sha=sha(Path(sys.executable).resolve()),
            files={
                n: sha(root / n)
                for n in ("config.json", "universe.json", "provider-seed.json", "seed.json")
            },
        ),
    )
    seal(bundle)
    pin = sha(bundle / "manifest.json")
    publish(root / "deployment.json", dict(bundle=str(bundle.resolve()), pin=pin))
    for phase, interval in [("source", 5), ("audit", 3600)]:
        label = read(root / "config.json").get("service_prefix", "ai.memecoin.brain.b08.") + phase
        script = (
            "import sys;sys.path.insert(0,sys.argv.pop(1));"
            "from memecoin_continuous.runtime import main;main()"
        )
        plist = dict(
            Label=label,
            StartInterval=interval,
            RunAtLoad=True,
            ProcessType="Background",
            Nice=10,
            ProgramArguments=[
                sys.executable,
                "-I",
                "-B",
                "-c",
                script,
                str((bundle / "runtime").resolve()),
                str(root.resolve()),
                phase,
            ],
            StandardOutPath=str(root / (phase + ".stdout.log")),
            StandardErrorPath=str(root / (phase + ".stderr.log")),
        )
        with (Path.home() / "Library/LaunchAgents" / (label + ".plist")).open("xb") as f:
            plistlib.dump(plist, f)
    return pin


def guard_source(root, pin=None):
    ref = read(root / "deployment.json")
    bundle = Path(ref["bundle"])
    if (pin and pin != ref["pin"]) or sha(bundle / "manifest.json") != ref["pin"]:
        raise ValueError("source_runtime_pin")
    verify(bundle)
    binding = read(bundle / "binding.json")
    if binding["root"] != str(root.resolve()) or binding["python_sha"] != sha(
        Path(sys.executable).resolve()
    ):
        raise ValueError("source_binding")
    if any(sha(root / n) != v for n, v in binding["files"].items()):
        raise ValueError("source_config_binding")
    return read(root / "config.json")


def worker(root, phase):
    from memecoin_operational.worker import lease_watchdog

    lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
    if phase == "source":
        from .source import tick

        tick(root)
    elif phase == "audit":
        from .store import audit_batch

        result = audit_batch(root, time.time_ns())
        publish(root / ("audit-" + str(time.time_ns()) + ".json"), result)
    else:
        raise ValueError("source_phase")


def main():
    root = Path(sys.argv[1])
    phase = sys.argv[2]
    c = guard_source(root)
    if (root / "STOP").exists() or time.time_ns() >= c["expires_ns"]:
        return
    with (root / ("." + phase + "-supervisor.lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        seconds = 240 if phase == "source" else 600
        watch = Watch(seconds)
        runtime = Path(read(root / "deployment.json")["bundle"]) / "runtime"

        def active():
            if (
                (root / "STOP").exists()
                or time.time_ns() >= c["expires_ns"]
                or watch.remaining() <= 0
            ):
                raise GateError("source_stop_or_deadline")

        script = (
            "import sys;sys.path.insert(0,sys.argv.pop(1));from pathlib import Path;"
            "from memecoin_continuous.runtime import worker;"
            "worker(Path(sys.argv[1]),sys.argv[2])"
        )
        result = child(
            [sys.executable, "-I", "-B", "-c", script, str(runtime), str(root), phase],
            root / (phase + "-worker-" + str(time.time_ns()) + ".log"),
            seconds,
            active,
        )
        if result["exit_code"] != 0:
            incident = root / ("incident-" + str(time.time_ns()) + ".json")
            publish(incident, result)
            if not (root / "STOP").exists():
                publish(root / "STOP", dict(reason="source_worker_failure", incident=incident.name))
            atomic(root / "failure-health.json", dict(at_ns=time.time_ns(), **result))
        print(json.dumps(dict(phase=phase, **result)), flush=True)
