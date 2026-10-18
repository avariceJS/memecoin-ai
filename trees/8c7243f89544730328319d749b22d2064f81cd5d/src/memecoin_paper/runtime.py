"""Isolated frozen deployment and one-writer supervisors for virtual-only services."""

import fcntl
import json
import os
import plistlib
import shutil
import sys
import time
import uuid
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_operational.contract import GateError
from memecoin_operational.supervisor import Watch, child
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish


def atomic(path, value):
    temp = path.with_name(".pending-" + uuid.uuid4().hex)
    with temp.open("w") as f:
        json.dump(value, f, sort_keys=True, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def stage(root, bundle):
    c = read(root / "config.json")
    bundle.mkdir(exist_ok=False)
    src = Path(__file__).parent.parent
    for path in src.glob("memecoin_*/*.py"):
        dest = bundle / "runtime" / path.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    publish(
        bundle / "binding.json",
        dict(
            root=str(root.resolve()),
            config_sha=sha(root / "config.json"),
            python_sha=sha(Path(sys.executable).resolve()),
        ),
    )
    seal(bundle)
    pin = sha(bundle / "manifest.json")
    publish(root / "deployment.json", dict(bundle=str(bundle.resolve()), pin=pin))
    launch = Path.home() / "Library/LaunchAgents"
    for phase, interval in [("paper", 30), ("train", 3600), ("ui", None)]:
        script = (
            "import sys;sys.path.insert(0,sys.argv.pop(1));"
            "from memecoin_paper.runtime import main;main()"
        )
        args = [
            sys.executable,
            "-I",
            "-B",
            "-c",
            script,
            str((bundle / "runtime").resolve()),
            str(root.resolve()),
            phase,
        ]
        value = dict(
            Label=c.get("service_prefix", "ai.memecoin.brain.b07.") + phase,
            ProgramArguments=args,
            RunAtLoad=True,
            WorkingDirectory=str(root.resolve()),
            StandardOutPath=str(root / (phase + ".stdout.log")),
            StandardErrorPath=str(root / (phase + ".stderr.log")),
            ProcessType="Background",
            Nice=10,
        )
        if interval:
            value["StartInterval"] = interval
        else:
            value["KeepAlive"] = True
        path = launch / (c.get("service_prefix", "ai.memecoin.brain.b07.") + phase + ".plist")
        with path.open("xb") as f:
            plistlib.dump(value, f)
    return dict(pin=pin, expires_ns=c["expires_ns"])


def guard(root):
    from .supervision import checked

    ref = checked("paper_deployment", lambda: read(root / "deployment.json"))
    bundle = Path(ref["bundle"])

    def paper_binding():
        if sha(bundle / "manifest.json") != ref["pin"]:
            raise ValueError("runtime_pin")
        verify(bundle)
        b = read(bundle / "binding.json")
        if (
            b["root"] != str(root.resolve())
            or b["config_sha"] != sha(root / "config.json")
            or b["python_sha"] != sha(Path(sys.executable).resolve())
        ):
            raise ValueError("runtime_binding")
        return read(root / "config.json")

    c = checked("paper_inventory_binding", paper_binding)
    if c.get("source_backend") == "continuous":
        from memecoin_continuous.runtime import guard_source

        checked(
            "source_inventory_binding",
            lambda: guard_source(Path(c["source"]), c["source_runtime_pin"]),
        )
    else:
        from memecoin_brain_quality.runtime import verify_runtime

        checked(
            "source_inventory_binding",
            lambda: verify_runtime(Path(c["source"]), c["source_runtime_pin"]),
        )
    return c


def main():
    root = Path(sys.argv[1])
    phase = sys.argv[2]
    if phase == "ui":
        c = guard(root)
        from .web import serve

        serve(root, c["port"])
        return
    if phase not in ("paper", "train"):
        raise ValueError("paper_phase")
    if (root / "STOP").exists() or (phase == "train" and (root / "TRAIN_STOP").exists()):
        return
    from .supervision import (
        GuardFailure,
        checked,
        complete,
        incidents,
        ready,
        reserve,
        safe_error,
        stop,
    )

    try:
        c = guard(root)
        checked("disk", lambda: disk_check(root))
    except Exception as exc:
        detail = exc.detail if isinstance(exc, GuardFailure) else safe_error(exc, "startup")
        result = dict(exit_code=-1, category="startup_guard_failure", detail=detail)
        incident = incidents(root, phase, result)
        stop(root, phase, incident)
        atomic(
            root / (phase + "-health.json"), dict(at_ns=time.time_ns(), status="failed", **result)
        )
        return
    if time.time_ns() >= c["expires_ns"]:
        atomic(
            root / "health.json",
            dict(at_ns=time.time_ns(), status="expired", reason="rollout_expired"),
        )
        return
    with (root / ("." + phase + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            if phase == "paper" and not ready(root, time.time_ns()):
                return
        except Exception as exc:
            result = dict(
                exit_code=-1, category="recovery_audit_failed", detail=safe_error(exc, "replay")
            )
            incident = incidents(root, phase, result)
            stop(root, phase, incident)
            atomic(
                root / (phase + "-health.json"),
                dict(at_ns=time.time_ns(), status="failed", **result),
            )
            return
        runtime = Path(read(root / "deployment.json")["bundle"]) / "runtime"
        script = (
            "import os,sys;sys.path.insert(0,sys.argv.pop(1));"
            "from memecoin_operational.worker import lease_watchdog;"
            'lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]));'
            "from pathlib import Path;from memecoin_paper.service import worker;"
            "worker(Path(sys.argv[1]),sys.argv[2])"
        )
        seconds = 600 if phase == "train" else 120
        watch = Watch(seconds)

        guard_detail = {}

        def active_guard():
            try:
                guard(root)
            except GuardFailure as exc:
                guard_detail.update(exc.detail)
                raise
            if (root / "STOP").exists():
                raise GateError("stop_requested")
            if time.time_ns() >= c["expires_ns"] or watch.remaining() <= 0:
                raise GateError("paper_rollout_or_worker_deadline")

        try:
            result = child(
                [sys.executable, "-I", "-B", "-c", script, str(runtime), str(root), phase],
                root / (phase + "-worker-" + str(time.time_ns()) + ".log"),
                seconds,
                active_guard,
            )
        except Exception as exc:
            result = dict(
                exit_code=-1,
                category="supervisor_" + type(exc).__name__,
                detail=exc.detail
                if isinstance(exc, GuardFailure)
                else safe_error(exc, "child_launch"),
            )
        if guard_detail:
            result["detail"] = guard_detail
        now = time.time_ns()
        if result["exit_code"] != 0:
            incident = incidents(root, phase, result)
            recovering = reserve(root, phase, result, incident, now)
            if not recovering:
                stop(root, phase, incident)
            status = "recovery_cooldown" if recovering else "failed"
        else:
            if phase == "paper":
                complete(root, now)
            status = "completed"
        atomic(root / (phase + "-health.json"), dict(at_ns=now, status=status, **result))
        print(json.dumps(dict(phase=phase, **result)), flush=True)


def disk_check(root):
    if shutil.disk_usage(root).free < 2 * 1024**3:
        raise ValueError("disk_floor")
