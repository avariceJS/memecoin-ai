"""Durable immutable events. OS owner may rewrite history; external head pins detect that."""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from memecoin_registry.contracts import digest

Row = dict[str, Any]


def publish(path: Path, value: Any) -> None:
    """No replacement, even on crash; temporary files are never ledger events."""
    temporary = path.parent / f".pending-{uuid.uuid4().hex}"
    with temporary.open("x") as stream:
        json.dump(value, stream, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink()


class Ledger:
    """Hold this single-writer lock throughout orchestration and protected reads."""

    def __init__(self, root: Path, binding: Row) -> None:
        self.root, self.binding = root, binding
        self.active_session: Row | None = None
        self.session_clock = 0.0
        self.session_fit_base = 0.0
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError("ledger_symlink")
        self.lock = (root / ".lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not (root / "binding.json").exists():
                publish(root / "binding.json", binding)
            if json.loads((root / "binding.json").read_text()) != binding:
                raise ValueError("ledger_binding_changed")
            self.events = self.audit()
        except BaseException:
            self.lock.close()
            raise

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *args: Any) -> None:
        self.lock.close()

    def audit(self, expected_head: str | None = None) -> list[Row]:
        events = []
        previous = digest(self.binding)
        for i, path in enumerate(sorted(self.root.glob("event-*.json"))):
            if path.is_symlink() or path.name != f"event-{i:06d}.json":
                raise ValueError("ledger_sequence_invalid")
            row = json.loads(path.read_text())
            checksum = row.pop("sha256")
            if row["previous"] != previous or row["sequence"] != i or digest(row) != checksum:
                raise ValueError("ledger_chain_invalid")
            row["sha256"] = checksum
            previous = checksum
            events.append(row)
        if expected_head is not None and previous != expected_head:
            raise ValueError("ledger_head_changed")
        return events

    @property
    def head(self) -> str:
        return str(self.events[-1]["sha256"] if self.events else digest(self.binding))

    def append(self, kind: str, **payload: Any) -> Row:
        row = dict(
            sequence=len(self.events),
            previous=self.head,
            kind=kind,
            time_ns=time.time_ns(),
            payload=payload,
        )
        row["sha256"] = digest(row)
        publish(self.root / f"event-{len(self.events):06d}.json", row)
        self.events.append(row)
        return row

    def trial_events(self, trial: str) -> list[Row]:
        return [e for e in self.events if e["payload"].get("trial") == trial]

    def charged(self, trial: str | None = None) -> float:
        """Unclosed attempts consume their entire reserved remainder after crash."""
        starts = {e["sha256"]: e for e in self.events if e["kind"] == "attempt_start"}
        ends = {e["payload"]["start"]: e for e in self.events if e["kind"] == "attempt_end"}
        fits = sum(
            float(ends[k]["payload"]["seconds"] if k in ends else e["payload"]["reserved_seconds"])
            for k, e in starts.items()
            if trial is None or e["payload"]["trial"] == trial
        )

        if trial is not None:
            return fits
        closed = {e["payload"]["start"]: e for e in self.events if e["kind"] == "session_end"}
        overhead = 0.0
        for event in self.events:
            if event["kind"] != "session_start":
                continue
            key = event["sha256"]
            if key in closed:
                overhead += closed[key]["payload"]["seconds"]
            elif self.active_session is event:
                overhead += max(
                    0.0, time.monotonic() - self.session_clock - (fits - self.session_fit_base)
                )
            else:
                overhead += event["payload"]["reserved_seconds"]
        return fits + overhead

    @contextmanager
    def session(self, total_limit: float) -> Iterator[None]:
        remaining = total_limit - self.charged()
        if remaining <= 0 or self.active_session is not None:
            raise ValueError("session_budget_exhausted")
        self.session_fit_base = sum(self.charged(t) for t in self.binding["trial_ids"])
        self.session_clock = time.monotonic()
        start = self.append("session_start", reserved_seconds=remaining)
        self.active_session = start
        finished = False
        try:
            yield
            finished = True
        except BaseException as exc:
            self.append("incident", category=type(exc).__name__)
            raise
        finally:
            fits = sum(self.charged(t) for t in self.binding["trial_ids"]) - self.session_fit_base
            overhead = max(0.0, time.monotonic() - self.session_clock - fits)
            self.append("session_end", start=start["sha256"], seconds=overhead)
            self.active_session = None
            if finished and time.monotonic() - self.session_clock > remaining:
                self.append("incident", category="total_wall_budget_exhausted")
                raise ValueError("total_wall_budget_exhausted")

    def start(self, trial: str, identity: Row, *, fit_limit: float, total_limit: float) -> Row:
        if any(e["kind"] == "incident" for e in self.events):
            raise ValueError("experiment_stopped_after_incident")
        history = self.trial_events(trial)
        if any(e["kind"] == "completed" for e in history):
            raise ValueError("trial_already_completed")
        if history and history[0]["payload"]["identity"] != identity:
            raise ValueError("trial_identity_changed")
        if not history:
            allowed = self.binding["trial_ids"]
            if trial not in allowed:
                raise ValueError("unregistered_trial")
            self.append("reserved", trial=trial, identity=identity)
        remaining = min(fit_limit - self.charged(trial), total_limit - self.charged())
        if remaining <= 0:
            raise ValueError("compute_budget_exhausted")
        return self.append("attempt_start", trial=trial, reserved_seconds=remaining)

    def end(self, start: Row, seconds: float, *, status: str, artifacts: Row) -> None:
        if status not in ("completed", "suspended", "failed", "budget_exhausted"):
            raise ValueError("invalid_attempt_status")
        if any(
            e["kind"] == "attempt_end" and e["payload"]["start"] == start["sha256"]
            for e in self.events
        ):
            raise ValueError("attempt_already_closed")
        if not 0 <= seconds <= start["payload"]["reserved_seconds"] or start not in self.events:
            raise ValueError("invalid_attempt_accounting")
        trial = start["payload"]["trial"]
        self.append(
            "attempt_end", trial=trial, start=start["sha256"], seconds=seconds, status=status
        )
        self.append(status, trial=trial, artifacts=artifacts)
        if status in ("failed", "budget_exhausted"):
            self.append("incident", trial=trial, category=status)

    def expose(self, release: Row, procedure_sha256: str) -> None:
        if any(e["kind"] == "exposure" for e in self.events):
            raise ValueError("holdout_consumed_exact_reproduction_requires_separate_review")
        self.append(
            "exposure",
            release=release,
            procedure_sha256=procedure_sha256,
            consumed_before_read=True,
        )
