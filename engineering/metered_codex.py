"""Durable pre-dispatch guard around Codex inference calls."""

from __future__ import annotations

import hashlib
import inspect
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .usage_ledger import UsageLedger

_USAGE_TOTAL_KEYS = {
    "inputTokens": "input_tokens",
    "cachedInputTokens": "cached_input_tokens",
    "outputTokens": "output_tokens",
    "reasoningOutputTokens": "reasoning_output_tokens",
    "totalTokens": "total_tokens",
}
_CACHE_WRITE_INPUT_TOKENS = "cacheWriteInputTokens"


def _canonical_usage_total(total):
    """Normalize known counters while leaving optional cache-write data in raw evidence."""
    if not isinstance(total, dict):
        return None
    keys = set(total)
    if keys not in (
        set(_USAGE_TOTAL_KEYS),
        set(_USAGE_TOTAL_KEYS) | {_CACHE_WRITE_INPUT_TOKENS},
    ):
        return None
    if any(type(value) is not int or value < 0 for value in total.values()):
        return None
    canonical = {target: total[source] for source, target in _USAGE_TOTAL_KEYS.items()}
    if (
        canonical["total_tokens"] != canonical["input_tokens"] + canonical["output_tokens"]
        or canonical["cached_input_tokens"] > canonical["input_tokens"]
        or canonical["reasoning_output_tokens"] > canonical["output_tokens"]
    ):
        return None
    return canonical


class Permit:
    def __init__(self, controller, dispatch_id, model, identity=None):
        self.controller, self.dispatch_id, self.model = controller, dispatch_id, model
        self.identity = identity
        self._used = False
        self._lock = threading.Lock()
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    @property
    def cancelled(self):
        return self._cancelled.is_set()

    def consume(self, thread_id, model):
        with self._lock:
            if self.cancelled:
                raise RuntimeError("permit_cancelled")
            if (
                self._used
                or model != self.model
                or (self.identity is not None and thread_id != self.identity)
            ):
                raise RuntimeError("permit_replay_or_binding_mismatch")
            self.controller._check_send(self.dispatch_id)
            if not self.controller.ledger.consume_dispatch(self.dispatch_id):
                raise RuntimeError("dispatch_already_consumed")
            self._used = True


class Controller:
    def __init__(
        self,
        ledger,
        *,
        state,
        clock=time.time,
        monotonic=time.monotonic,
        preflight=None,
        external_allowlist=frozenset(),
    ):
        self.ledger, self.state, self.clock = ledger, Path(state), clock
        self.monotonic = monotonic
        self.preflight, self.lock, self.closed = preflight, threading.RLock(), False
        self.external_allowlist = frozenset(external_allowlist)
        self._monitors = set()
        self._monitor_events = {}
        self._monitor_lock = threading.Lock()
        self._monitor_stop = threading.Event()

    def refresh(self, reason="admission"):
        """Check explicit stop/config authority without polling subscription usage."""
        if (self.state / "STOP").exists():
            raise RuntimeError("STOP")
        if self.preflight:
            pf = self.preflight()
            if not isinstance(pf, dict) or not pf.get("resources", {}).get("policy_id"):
                raise RuntimeError("preflight_unknown")
        return True

    def admit(self, dispatch_id, stream, baseline_or_none, model, estimate, deadline_at, context):
        with self.lock:
            if (self.state / "STOP").exists():
                raise RuntimeError("STOP")
            self.refresh("admit")
            row = self.ledger.reserve(
                dispatch_id,
                stream,
                model,
                estimate,
                deadline_at,
                context=context,
                baseline=baseline_or_none,
            )
            if row["status"] != "reserved":
                raise RuntimeError("dispatch_already_sent")
            return Permit(self, dispatch_id, model, stream)

    def _check_send(self, dispatch_id):
        with self.lock:
            if (self.state / "STOP").exists():
                raise RuntimeError("STOP")
            self.refresh("pre_send")
            row = self.ledger.dispatch(dispatch_id)
            if not row or row["status"] != "reserved" or self.monotonic() >= row["deadline_at"]:
                raise RuntimeError("dispatch_not_sendable")

    def monitor(self, deadline, cancel):
        """Enforce only the bounded call deadline; quota resets never cancel or resume work."""
        stopped = threading.Event()

        def deadline_watch():
            while not self.closed:
                if (self.state / "STOP").exists():
                    cancel()
                    return
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    cancel()
                    return
                if stopped.wait(min(0.1, remaining)):
                    return

        timer = threading.Thread(target=deadline_watch, name="metered-deadline", daemon=True)
        timer.start()
        with self._monitor_lock:
            self._monitors.add(timer)
            self._monitor_events[timer] = stopped
        return timer

    def start_monitor(self, deadline, permit):
        return self.monitor(deadline, permit.cancel)

    def import_external(self, batch, collector_id, evidence_bytes, evidence_sha256):
        return self.ledger.import_external(
            batch,
            collector_id,
            evidence_bytes,
            evidence_sha256,
            external_allowlist=self.external_allowlist,
        )

    def reconcile_dispatch(self, dispatch_id, *, terminal_receipt, stream_observations):
        row = self.ledger.dispatch(dispatch_id)
        if not row or row["status"] not in ("completed", "unknown", "failed", "timeout", "settled"):
            raise RuntimeError("dispatch_not_reconcilable")
        pf = self.preflight() if self.preflight else {}
        if not isinstance(pf, dict) or pf.get("terminal_usage_qualified") is not True:
            raise RuntimeError("terminal_usage_not_qualified")
        if not isinstance(terminal_receipt, dict) or not isinstance(stream_observations, list):
            raise ValueError("invalid_terminal_evidence")
        if (
            terminal_receipt.get("thread_id") != row["identity"]
            or terminal_receipt.get("turn_id") != row["provider_turn_id"]
        ):
            raise ValueError("terminal_identity_mismatch")
        encoded_receipt = json.dumps(terminal_receipt, sort_keys=True, separators=(",", ":"))
        receipt_hash = hashlib.sha256(encoded_receipt.encode()).hexdigest()
        stored_receipt = self.ledger.db.execute(
            "SELECT evidence_sha256,evidence FROM terminal_receipts WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        if stored_receipt and (stored_receipt["evidence_sha256"], stored_receipt["evidence"]) != (
            receipt_hash,
            encoded_receipt,
        ):
            conflict_id = hashlib.sha256(
                (dispatch_id + ":" + receipt_hash).encode("utf-8")
            ).hexdigest()
            self.ledger.db.execute(
                "INSERT OR IGNORE INTO terminal_receipt_conflicts VALUES(?,?,?,?,?)",
                (conflict_id, dispatch_id, receipt_hash, encoded_receipt, self.clock()),
            )
            raise ValueError("terminal_receipt_conflict")
        baseline = json.loads(row["baseline"]) if row["baseline"] else None
        if baseline is None or terminal_receipt.get("baseline") != baseline:
            raise ValueError("terminal_baseline_unknown_or_mismatch")
        end = terminal_receipt.get("end")
        from .usage_ledger import _counters

        end = _counters(end)
        self.ledger._validate_interval(baseline, end)
        identity, epoch = row["identity"], terminal_receipt.get("epoch")
        if row.get("epoch") is None or epoch != row["epoch"]:
            raise ValueError("terminal_epoch_dispatch_mismatch")
        reg = self.ledger.db.execute(
            "SELECT 1 FROM registrations WHERE identity=? AND epoch=?", (identity, epoch)
        ).fetchone()
        if not reg:
            raise ValueError("terminal_epoch_unknown")
        if not stream_observations:
            raise ValueError("terminal_interval_uncovered")
        positions = {}
        prepared_observations = []
        for item in stream_observations:
            counters = _counters(item.get("counters"))
            event_id = item.get("event_id")
            sequence = item.get("sequence")
            if not event_id or isinstance(sequence, bool) or not isinstance(sequence, int):
                raise ValueError("invalid_stream_observation")
            self.ledger._validate_interval(baseline, counters)
            self.ledger._validate_interval(counters, end)
            prior_at_position = positions.get(sequence)
            if prior_at_position is not None and prior_at_position != counters:
                raise ValueError("ambiguous_stream_position")
            positions[sequence] = counters
            prepared_observations.append((event_id, sequence, counters))
            existing = self.ledger.db.execute(
                "SELECT identity,epoch,sequence,counters FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing:
                if (
                    existing["identity"],
                    existing["epoch"],
                    existing["sequence"],
                    json.loads(existing["counters"]),
                ) != (identity, epoch, sequence, counters):
                    raise ValueError("stream_event_conflict")
        if not any(point == end for point in positions.values()):
            raise ValueError("terminal_interval_uncovered")
        previous = baseline
        attributed_points = []
        for sequence in sorted(positions):
            counters = positions[sequence]
            self.ledger._validate_interval(previous, counters)
            event_ids = [
                event_id
                for event_id, point_sequence, point in prepared_observations
                if point_sequence == sequence and point == counters
            ]
            attributed_points.extend(
                (event_id, previous, counters, sequence) for event_id in event_ids
            )
            previous = counters
        for event_id, sequence, counters in prepared_observations:
            existing = self.ledger.db.execute(
                "SELECT 1 FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if not existing:
                self.ledger.observe(
                    identity,
                    epoch,
                    event_id,
                    counters,
                    source="reconciliation",
                    sequence=sequence,
                    observed_at=self.clock(),
                )
        for event_id, interval_start, interval_end, sequence in attributed_points:
            self.ledger.attribute_dispatch(
                dispatch_id,
                identity,
                epoch,
                row["model"],
                interval_start,
                interval_end,
                evidence={
                    "event_id": event_id,
                    "receipt_sha256": receipt_hash,
                    "sequence": sequence,
                    "turn_id": row["provider_turn_id"],
                },
                sequence=sequence,
            )
        self.ledger.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.ledger.db.execute(
                "SELECT evidence_sha256,evidence FROM terminal_receipts WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
            receipt_row = (dispatch_id, receipt_hash, encoded_receipt, self.clock())
            if existing:
                if (existing["evidence_sha256"], existing["evidence"]) != (
                    receipt_row[1],
                    receipt_row[2],
                ):
                    raise ValueError("terminal_receipt_conflict")
            else:
                self.ledger.db.execute("INSERT INTO terminal_receipts VALUES(?,?,?,?)", receipt_row)
            self.ledger.db.execute(
                "UPDATE dispatches SET status='settled' WHERE dispatch_id=?", (dispatch_id,)
            )
            self.ledger.db.execute("COMMIT")
        except Exception:
            self.ledger.db.execute("ROLLBACK")
            raise
        return True

    def stop_monitor(self, thread):
        with self._monitor_lock:
            event = self._monitor_events.get(thread)
            peers = [t for t, value in self._monitor_events.items() if value is event]
            if event:
                event.set()
        for item in peers:
            if item is not threading.current_thread():
                item.join()
        with self._monitor_lock:
            for item in peers:
                if not item.is_alive():
                    self._monitor_events.pop(item, None)
                    self._monitors.discard(item)

    def close(self):
        self.closed = True
        self._monitor_stop.set()
        with self._monitor_lock:
            ts = list(self._monitors)
            events = list(self._monitor_events.values())
        for event in events:
            event.set()
        for t in ts:
            t.join()


class MeteredCodex:
    def __init__(self, rpc, controller, context):
        self.rpc, self.controller, self.context = rpc, controller, dict(context)
        self.thread = None
        self._epochs = {}
        self._turn_dispatches = {}
        self._turn_threads = {}
        if hasattr(self.rpc, "raw_ingress_sink"):
            self.rpc.raw_ingress_sink = self.controller.ledger.persist_raw_rpc

    def __getattr__(self, name):
        return getattr(self.rpc, name)

    def start(self, model, instructions):
        did = (
            "start:"
            + hashlib.sha256(
                json.dumps([self.context, model, instructions], sort_keys=True).encode()
            ).hexdigest()
        )
        permit = self.controller.admit(
            did,
            did,
            None,
            model,
            {"tokens": 1, "wpu": 1},
            self.controller.monotonic() + 120,
            self.context,
        )
        # Real Codex.start enforces the permit itself. A small injected RPC fake may
        # expose the historical two-argument shape; consume the same durable permit
        # here before calling that adapter so compatibility never means an unmetered send.
        try:
            parameters = inspect.signature(self.rpc.start).parameters.values()
        except (TypeError, ValueError) as exc:
            raise RuntimeError("rpc_start_permit_capability_unknown") from exc
        accepts_permit = any(
            parameter.name == "permit" or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if accepts_permit:
            # A provider thread ID does not exist before start; bind this one-use
            # capability to the protocol's synthetic new-thread identity.
            permit.identity = "new-thread"
            self.thread = self.rpc.start(model, instructions, permit=permit)
        else:
            # Legacy adapters consume locally against the durable start dispatch.
            permit.consume(did, model)
            self.thread = self.rpc.start(model, instructions)
        from .usage_ledger import ZERO

        self.controller.ledger.register(
            self.thread,
            "initial",
            model,
            ZERO,
            evidence="provider-certified-new-thread:" + did,
            started_after_T0=True,
        )
        self._epochs[self.thread] = "initial"
        self.controller.ledger.record_outcome(did, "completed", {"thread_id": self.thread})
        self.controller.ledger.settle_certified_start(did, self.thread)
        return self.thread

    def resume(self, thread_id, model):
        rows = self.controller.ledger.db.execute(
            "SELECT epoch,model FROM registrations WHERE identity=? ORDER BY epoch", (thread_id,)
        ).fetchall()
        if len(rows) != 1 or rows[0]["model"] != model:
            raise RuntimeError("resume_requires_unique_ledger_epoch")
        result = self.rpc.resume(thread_id, model)
        resumed = (
            result.get("thread", {}).get("id", thread_id) if isinstance(result, dict) else thread_id
        )
        if resumed != thread_id:
            raise RuntimeError("resume_thread_identity_mismatch")
        self.thread = thread_id
        self._epochs[thread_id] = rows[0]["epoch"]
        return self.thread

    def record_outcome(self, dispatch_id, status, receipt=None):
        self.controller.ledger.record_outcome(dispatch_id, status, receipt)

    def turn(
        self,
        thread_id,
        prompt,
        *,
        dispatch_id,
        model,
        estimate,
        tool,
        deadline_seconds,
        heartbeat=None,
        output_schema=None,
        started=None,
    ):
        if type(deadline_seconds) is not int or deadline_seconds < 1:
            raise ValueError("deadline_seconds must be positive integer seconds")
        maximum = 1200 if model == "gpt-6.1-sol" else 600
        if model not in ("gpt-6.1-sol", "gpt-6-luna") or deadline_seconds > maximum:
            raise ValueError("model deadline exceeds bounded allowance")
        monotonic_deadline = self.controller.monotonic() + deadline_seconds
        epoch = self._epochs.get(thread_id)
        if epoch is None:
            candidates = self.controller.ledger.db.execute(
                "SELECT epoch FROM registrations WHERE identity=? ORDER BY epoch", (thread_id,)
            ).fetchall()
            if len(candidates) == 1:
                epoch = candidates[0]["epoch"]
                self._epochs[thread_id] = epoch
        baseline = (
            self.controller.ledger.trusted_terminal_baseline(thread_id, epoch)
            if epoch is not None
            else None
        )
        permit = self.controller.admit(
            dispatch_id, thread_id, baseline, model, estimate, monotonic_deadline, self.context
        )

        monitor = self.controller.start_monitor(monotonic_deadline, permit)
        old_sink = getattr(self.rpc, "sink", None)
        provider_turn = None

        def capture(event):
            if old_sink:
                old_sink(event)
            kind = event.get("kind") if isinstance(event, dict) else None
            turn_id = event.get("turn_id") if isinstance(event, dict) else None
            usage = event.get("usage") if isinstance(event, dict) else None
            total = usage.get("total") if isinstance(usage, dict) else None
            canonical = _canonical_usage_total(total)
            event_hash = hashlib.sha256(
                json.dumps(event, sort_keys=True, default=str).encode()
            ).hexdigest()
            event_dispatch = self._turn_dispatches.get(turn_id, dispatch_id)
            event_thread = self._turn_threads.get(turn_id, thread_id)
            try:
                self.controller.ledger.observe_receipt(
                    event_dispatch + ":receipt:" + event_hash,
                    event_dispatch,
                    canonical,
                    turn_id,
                    kind or "rpc",
                    event.get("event_at"),
                    self.controller.clock(),
                    event,
                )
                known_turn = bool(turn_id) and (
                    turn_id == provider_turn or turn_id in self._turn_dispatches
                )
                if canonical is not None and known_turn and kind == "token_usage":
                    self.controller.ledger.observe(
                        event_thread,
                        self._epochs[event_thread],
                        event_dispatch + ":counter:" + event_hash,
                        canonical,
                        # A cumulative progress sample is useful for the
                        # canonical stream union, but cannot prove which model
                        # owned the interval since the prior sample. Keep its
                        # spend in the union and leave model attribution open
                        # until qualified interval evidence arrives.
                        source="codex-rpc-unattributed",
                        sequence=canonical["total_tokens"],
                        observed_at=self.controller.clock(),
                    )
            except (ValueError, KeyError, TypeError):
                # Raw receipt evidence is kept even when counter semantics fail validation.
                pass

        if old_sink is not None:
            self.rpc.sink = capture

        def bind(turn_id):
            nonlocal provider_turn
            provider_turn = turn_id
            self._turn_dispatches[turn_id] = dispatch_id
            self._turn_threads[turn_id] = thread_id
            epoch = self._epochs.get(thread_id)
            if epoch is None:
                candidates = self.controller.ledger.db.execute(
                    "SELECT epoch FROM registrations WHERE identity=? ORDER BY epoch",
                    (thread_id,),
                ).fetchall()
                if len(candidates) > 1:
                    raise RuntimeError("dispatch_epoch_ambiguous")
                epoch = candidates[0]["epoch"] if candidates else None
                if epoch is not None:
                    self._epochs[thread_id] = epoch
            self.controller.ledger.bind(dispatch_id, thread_id, turn_id, epoch=epoch)
            return started(turn_id) if started else None

        try:
            out = self.rpc.turn(
                thread_id,
                prompt,
                model=model,
                tool=tool,
                deadline_seconds=deadline_seconds,
                heartbeat=heartbeat,
                output_schema=output_schema,
                started=bind,
                permit=permit,
            )
        except Exception as exc:
            self.controller.ledger.record_outcome(
                dispatch_id,
                "timeout" if "deadline" in str(exc).lower() else "unknown",
                {"error": type(exc).__name__, "message": str(exc)[:400]},
            )
            try:
                self.controller.refresh("post_failure")
            except Exception:
                pass
            raise
        finally:
            self.controller.stop_monitor(monitor)
            if old_sink is not None:
                self.rpc.sink = old_sink
        self.controller.ledger.record_outcome(
            dispatch_id,
            "completed",
            {"turn_id": out.get("turn_id"), "usage_end": out.get("usage_end")},
        )
        try:
            self.controller.refresh("post_completion")
        except Exception:
            # A finished turn remains recorded; stale quota blocks the next dispatch.
            pass
        out["dispatch_id"] = dispatch_id
        return out


@contextmanager
def metered_client(
    state, cwd, *, cfg, context, rpc_factory=None, event_sink=None, harness_cwd=None
):
    from .codex_rpc import Codex

    factory = rpc_factory or Codex
    ledger = UsageLedger(Path(state) / "usage-ledger.sqlite")
    rpc = factory(
        cwd,
        codex=cfg.get("codex_executable", "codex"),
        event_sink=event_sink,
        harness_cwd=harness_cwd,
    )

    def common_preflight():
        from .supervisor import config

        current = config(Path(state))
        if current != cfg:
            raise RuntimeError("frozen_controller_config_changed")
        return {"resources": {"policy_id": cfg.get("authority", "founder-manual-quota")}}

    controller = Controller(
        ledger,
        state=state,
        preflight=common_preflight,
    )
    try:
        yield MeteredCodex(rpc, controller, context)
    finally:
        controller.close()
        ledger.close()
        rpc.close()
