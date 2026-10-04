"""Safe operational diagnostics and a bounded, replay-checked paper restart allowance."""

import errno
import re
import time

from memecoin_ml.data import read
from memecoin_operational.contract import GateError
from memecoin_runner.ledger import Ledger, publish

SAFE_REASONS = frozenset(
    {
        "runtime_pin",
        "runtime_binding",
        "source_runtime_pin",
        "source_binding",
        "source_config_binding",
        "artifact_inventory_mismatch",
        "unsafe_manifest_path",
        "symlink_forbidden",
        "stop_requested",
        "clock_jump_or_suspend",
        "paper_rollout_or_worker_deadline",
        "disk_floor",
    }
)


class GuardFailure(GateError):
    def __init__(self, detail):
        super().__init__("paper_guard_failure")
        self.detail = detail


def safe_error(exc, stage):
    # Never serialize arbitrary exception messages, paths or response bodies.
    return dict(
        stage=stage,
        error_type=type(exc).__name__,
        errno=exc.errno if isinstance(exc, OSError) and exc.errno in errno.errorcode else None,
        reason=str(exc) if str(exc) in SAFE_REASONS else "unclassified",
    )


def checked(stage, operation):
    try:
        return operation()
    except GuardFailure:
        raise
    except Exception as exc:
        raise GuardFailure(safe_error(exc, stage)) from None


def incidents(root, phase, result):
    path = root / ("incident-" + phase + "-" + str(time.time_ns()) + ".json")
    publish(path, result)
    return path


def stop(root, phase, incident):
    path = root / ("STOP" if phase == "paper" else "TRAIN_STOP")
    if not path.exists():
        publish(path, dict(reason=phase + "_worker_failure", incident=incident.name))


def reservations(root):
    return sorted(
        p
        for p in (root / "recovery").glob("attempt-*.json")
        if re.fullmatch(r"attempt-\d{6}\.json", p.name)
    )


def reserve(root, phase, result, incident, now):
    """Only an external signal, never a guard-induced kill, is automatically recoverable."""
    attempts = reservations(root)
    if (
        phase != "paper"
        or result["category"] != "worker_failed"
        or result["exit_code"] not in (-9, -15)
        or len(attempts) >= 2
    ):
        return False
    (root / "recovery").mkdir(exist_ok=True)
    publish(
        root / "recovery" / f"attempt-{len(attempts):06d}.json",
        dict(incident=incident.name, reserved_ns=now, not_before_ns=now + 60 * 10**9),
    )
    return True


def pending(root):
    attempts = reservations(root)
    if not attempts:
        return None
    p = attempts[-1]
    return p if not p.with_name(p.stem + "-complete.json").exists() else None


def ready(root, now):
    p = pending(root)
    if p is None:
        return True
    if now < read(p)["not_before_ns"]:
        return False
    from .audit import replay
    from .projection import restore

    result = replay(root)
    with Ledger(root / "ledger", read(root / "ledger/binding.json")) as ledger:
        restore(root, ledger.events)  # A corrupt derived checkpoint also fails closed.
    # Each revalidation is preserved; a parent crash cannot reset the allowance.
    publish(
        root / "recovery" / f"validated-{time.time_ns()}.json",
        dict(attempt=p.name, at_ns=now, replay=result),
    )
    return True


def complete(root, now):
    p = pending(root)
    if p is not None:
        publish(p.with_name(p.stem + "-complete.json"), dict(completed_ns=now))
