"""Durable pre-dispatch guard around Codex inference calls."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .usage_ledger import UsageLedger


class Permit:
    def __init__(self, controller, dispatch_id, model):
        self.controller, self.dispatch_id, self.model = controller, dispatch_id, model
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
            if self._used or model != self.model:
                raise RuntimeError("permit_replay_or_model_mismatch")
            self.controller._check_send(self.dispatch_id)
            if not self.controller.ledger.consume_dispatch(self.dispatch_id):
                raise RuntimeError("dispatch_already_consumed")
            self._used = True


class Controller:
    def __init__(self, ledger, account_read, *, state, clock=time.time, preflight=None):
        self.ledger, self.account_read, self.state, self.clock = (
            ledger,
            account_read,
            Path(state),
            clock,
        )
        self.preflight, self.lock, self.closed = preflight, threading.RLock(), False
        self.account_lock = threading.Lock()
        self._monitors = set()
        self._monitor_lock = threading.Lock()
        self._monitor_stop = threading.Event()

    def refresh(self, reason="admission"):
        with self.account_lock:
            data = self.account_read()
        if not isinstance(data, dict) or data.get("ordinaryUsageAllowed") is not True:
            raise RuntimeError("quota_unknown_or_denied")
        by = data.get("rateLimitsByLimitId", {})
        bucket = by.get("codex") if isinstance(by, dict) else None
        if not isinstance(bucket, dict):
            raise RuntimeError("quota_required_bucket_missing")
        primary = bucket.get("primary")
        if (
            not isinstance(primary, dict)
            or isinstance(primary.get("usedPercent"), bool)
            or not isinstance(primary.get("usedPercent"), (int, float))
            or not math.isfinite(primary["usedPercent"])
        ):
            raise RuntimeError("quota_primary_unknown")
        resets = primary.get("resetsAt")
        if (
            isinstance(resets, bool)
            or not isinstance(resets, (int, float))
            or not math.isfinite(resets)
        ):
            raise RuntimeError("quota_reset_unknown")
        now = self.clock()
        if resets <= now or resets - now > 7 * 24 * 60 * 60:
            raise RuntimeError("quota_evidence_stale")
        used = primary["usedPercent"]
        if used >= 99:
            raise RuntimeError("quota_pause_99")
        if (
            not isinstance(bucket.get("spendControlReached"), bool)
            or "rateLimitReachedType" not in bucket
        ):
            raise RuntimeError("quota_stop_evidence_unknown")
        if bucket["spendControlReached"] is True or bucket.get("rateLimitReachedType"):
            raise RuntimeError("quota_spend_or_rate_stop")
        credits = bucket.get("credits")
        if (
            not isinstance(credits, dict)
            or not isinstance(credits.get("hasCredits"), bool)
            or not isinstance(credits.get("unlimited"), bool)
        ):
            raise RuntimeError("quota_credit_evidence_unknown")
        if credits.get("unlimited") is not True and credits.get("hasCredits") is True:
            raise RuntimeError("paid_credit_path_unqualified")
        if self.preflight:
            pf = self.preflight()
            if not isinstance(pf, dict) or not pf.get("resources", {}).get("policy_id"):
                raise RuntimeError("preflight_unknown")
        return data

    def admit(self, dispatch_id, stream, baseline_or_none, model, estimate, deadline_at, context):
        with self.lock:
            if (self.state / "STOP").exists():
                raise RuntimeError("STOP")
            quota = self.refresh("admit")
            used = quota["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"]
            if used >= 97 and (context.get("team") is None or estimate["tokens"] > 4000):
                raise RuntimeError("quota_97_short_productive_turn_only")
            row = self.ledger.reserve(
                dispatch_id,
                stream,
                model,
                estimate,
                deadline_at,
                context=context,
                baseline=baseline_or_none,
                admission_percent=quota["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"],
            )
            if row["status"] != "reserved":
                raise RuntimeError("dispatch_already_sent")
            return Permit(self, dispatch_id, model)

    def _check_send(self, dispatch_id):
        with self.lock:
            if (self.state / "STOP").exists():
                raise RuntimeError("STOP")
            self.refresh("pre_send")
            row = self.ledger.dispatch(dispatch_id)
            if not row or row["status"] != "reserved" or self.clock() >= row["deadline_at"]:
                raise RuntimeError("dispatch_not_sendable")

    def monitor(self, deadline, cancel):
        def run():
            interval = 300
            while not self.closed and not self._monitor_stop.is_set() and self.clock() < deadline:
                try:
                    q = self.refresh("active")
                    if q["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"] >= 95:
                        interval = 60
                except Exception:
                    cancel()
                    return
                if (self.state / "STOP").exists() or self.clock() >= deadline:
                    cancel()
                    return
                remaining = max(0, deadline - self.clock())
                if self._monitor_stop.wait(min(interval, remaining)):
                    return

        t = threading.Thread(target=run, daemon=True)
        t.start()
        with self._monitor_lock:
            self._monitors.add(t)
        return t

    def start_monitor(self, deadline, permit):
        return self.monitor(deadline, permit.cancel)

    def close(self):
        self.closed = True
        self._monitor_stop.set()
        with self._monitor_lock:
            ts = list(self._monitors)
        for t in ts:
            t.join()


class MeteredCodex:
    def __init__(self, rpc, controller, context):
        self.rpc, self.controller, self.context = rpc, controller, dict(context)
        self.thread = None
        self._epochs = {}
        self._turn_dispatches = {}
        self._turn_threads = {}

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
            self.controller.clock() + 120,
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
            self.thread = self.rpc.start(model, instructions, permit=permit)
        else:
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
        return self.thread

    def resume(self, thread_id, model):
        result = self.rpc.resume(thread_id, model)
        self.thread = (
            result.get("thread", {}).get("id", thread_id) if isinstance(result, dict) else thread_id
        )
        if self.thread not in self._epochs:
            epoch = "resumed-unknown"
            self.controller.ledger.register(
                self.thread,
                epoch,
                model,
                None,
                evidence="resumed-provider-thread",
                started_after_T0=False,
            )
            self._epochs[self.thread] = epoch
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
        deadline = self.controller.clock() + deadline_seconds
        permit = self.controller.admit(
            dispatch_id, thread_id, None, model, estimate, deadline, self.context
        )

        monitor = self.controller.start_monitor(deadline, permit)
        old_sink = getattr(self.rpc, "sink", None)
        provider_turn = None

        def capture(event):
            if old_sink:
                old_sink(event)
            kind = event.get("kind") if isinstance(event, dict) else None
            turn_id = event.get("turn_id") if isinstance(event, dict) else None
            usage = event.get("usage") if isinstance(event, dict) else None
            total = usage.get("total") if isinstance(usage, dict) else None
            canonical = None
            if isinstance(total, dict):
                mapping = {
                    "inputTokens": "input_tokens",
                    "cachedInputTokens": "cached_input_tokens",
                    "outputTokens": "output_tokens",
                    "reasoningOutputTokens": "reasoning_output_tokens",
                    "totalTokens": "total_tokens",
                }
                if set(total) == set(mapping):
                    canonical = {target: total[source] for source, target in mapping.items()}
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
                        source="codex-rpc",
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
            self.controller.ledger.bind(dispatch_id, thread_id, turn_id)
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
            with self.controller._monitor_lock:
                self.controller._monitors.discard(monitor)
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
    account = factory(cwd, codex=cfg.get("codex_executable", "codex"), harness_cwd=harness_cwd)

    def common_preflight():
        from .supervisor import config

        current = config(Path(state))
        if current != cfg:
            raise RuntimeError("frozen_controller_config_changed")
        return {
            "resources": {
                "policy_id": cfg.get("authority", "CEO-20261020-02"),
            },
            "terminal_usage_qualified": False,
        }

    controller = Controller(
        ledger,
        lambda: account.call("account/rateLimits/read"),
        state=state,
        preflight=common_preflight,
    )
    try:
        yield MeteredCodex(rpc, controller, context)
    finally:
        controller.close()
        ledger.close()
        account.close()
        rpc.close()
