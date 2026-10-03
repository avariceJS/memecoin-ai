"""Real process deadlines and orphan fencing; immutable unsuccessful attempts retained."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from memecoin_collector.scheduler import durable
from memecoin_ml.data import read
from memecoin_registry.contracts import inventory
from memecoin_sampling.contract import NS

from .contract import GateError, check_bundle, no_symlink

Row = dict[str, Any]


class Watch:
    def __init__(
        self,
        seconds: float,
        *,
        wall: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self.wall, self.mono = wall, mono
        self.w0, self.m0, self.seconds = wall(), mono(), seconds
        self.last_wall, self.last_mono = self.w0, self.m0

    def remaining(self) -> float:
        w, m = self.wall(), self.mono()
        if w < self.last_wall or m < self.last_mono or abs((w - self.w0) - (m - self.m0)) > 2:
            raise GateError("clock_jump_or_suspend")
        self.last_wall, self.last_mono = w, m
        return self.seconds - max(w - self.w0, m - self.m0)


def disk_guard(root: Path, *, floor: int, limit: int) -> None:
    import shutil

    no_symlink(root)
    if shutil.disk_usage(root).free < floor + limit:
        raise GateError("disk_headroom")
    size = 0
    for p in root.rglob("*"):
        if p.is_symlink():
            raise GateError("symlink_window")
        if p.is_file():
            size += p.stat().st_size
            if size > limit:
                raise GateError("window_storage_ceiling")


def child(command: list[str], log: Path, seconds: float, guard: Callable[[], None]) -> Row:
    """Lease write FD belongs only to parent. Worker watchdog exits on EOF."""
    if not 0 < seconds <= 600:
        raise GateError("invalid_child_deadline")
    guard()
    rfd, wfd = os.pipe()
    env = dict(os.environ, MEMECOIN_LEASE_FD=str(rfd), PYTHONDONTWRITEBYTECODE="1")
    started = time.monotonic()
    p = None
    category = "completed"
    try:
        with log.open("x") as f:
            p = subprocess.Popen(
                command, stdout=f, stderr=f, env=env, pass_fds=(rfd,), start_new_session=True
            )
            os.close(rfd)
            rfd = -1
            while p.poll() is None:
                try:
                    guard()
                    if time.monotonic() - started >= seconds:
                        raise GateError("child_deadline")
                except Exception as exc:
                    category = str(exc) if isinstance(exc, GateError) else "supervisor_guard"
                    os.killpg(p.pid, signal.SIGKILL)
                    break
                time.sleep(0.05)
            code = p.wait()
            if code and category == "completed":
                category = "worker_failed"
    finally:
        os.close(wfd)
        if rfd != -1:
            os.close(rfd)
        if p is not None and p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
    return dict(exit_code=code, category=category, elapsed_seconds=time.monotonic() - started)


def window(
    bundle: Path,
    pin: str,
    output: Path,
    reservation: Row,
    *,
    live_guard: Callable[[], None] | None = None,
    receipt: Path | None = None,
    receipt_pin: str | None = None,
    scope: str = "bounded_qualification",
) -> Row:
    c = check_bundle(bundle, pin)
    no_symlink(output)
    if read(output / "reservation.json") != reservation or reservation["reserved_rpc"] != 180:
        raise GateError("reservation_changed")
    if c["mode"] == "prospective" and live_guard is None:
        raise GateError("live_guard_required")
    watch = Watch(600)
    logical = reservation["started_ns"]
    outcomes = []

    def guard() -> None:
        if watch.remaining() <= 0:
            raise GateError("window_deadline")
        check_bundle(bundle, pin)
        disk_guard(output, floor=c["free_floor"], limit=c["window_bytes"])
        if (output.parent.parent / "STOP").exists():
            raise GateError("stop_requested")
        if live_guard is not None:
            live_guard()

    for phase in ("init", "segment0", "segment1", "qa"):
        try:
            guard()
            job = dict(
                phase=phase,
                bundle_sha=pin,
                parent_pid=os.getpid(),
                logical_ns=logical,
                host_deadline_ns=int((watch.w0 + 600) * NS),
                receipt=str(receipt) if receipt else None,
                receipt_pin=receipt_pin,
                scope=scope,
            )
            jobpath = output / f"{phase}-job.json"
            durable(jobpath, job)
            # -S prevents user/site packages and editable checkout from overriding frozen code.
            script = (
                "import sys; sys.path.insert(0,sys.argv.pop(1)); "
                "from memecoin_operational.worker import main; main()"
            )
            command = [
                sys.executable,
                "-B",
                "-S",
                "-c",
                script,
                str((bundle / "runtime").resolve()),
                str(bundle.resolve()),
                str(output.resolve()),
                str(jobpath.resolve()),
            ]
            result = child(
                command,
                output / f"{phase}.log",
                min(210 if phase.startswith("segment") else 600, watch.remaining()),
                guard,
            )
            result["phase"] = phase
            outcomes.append(result)
            if result["exit_code"] or result["category"] != "completed":
                break
            guard()  # Child completion cannot bypass the whole-window deadline.
            details = read(output / f"{phase}-result.json")
            logical = details["finished_ns"]
            if details["status"] != "completed":
                outcomes[-1]["category"] = "incomplete_phase"
                break
        except Exception as exc:
            outcomes.append(
                dict(
                    phase=phase,
                    exit_code=1,
                    category=str(exc)
                    if isinstance(exc, GateError)
                    else "integrity_or_worker_failure",
                )
            )
            break
    successful = len(outcomes) == 4 and all(x["category"] == "completed" for x in outcomes)
    finished = logical if c["mode"] == "synthetic_fixture" else time.time_ns()
    successful = (
        successful and reservation["started_ns"] <= finished <= reservation["started_ns"] + 600 * NS
    )
    outcome = dict(
        index=reservation["index"],
        status="completed" if successful else "failed",
        data_kind=c["mode"],
        finished_ns=finished,
        corpus_exit_code=0 if successful else 1,
        execution_or_collection_exit_code=0 if successful else 1,
        qa_exit_code=0 if successful else 1,
    )
    durable(
        output / "supervision.json", dict(phases=outcomes, wall_seconds=time.monotonic() - watch.m0)
    )
    durable(output / "seal.json", dict(outcome=outcome, files=inventory(output)))
    return outcome
