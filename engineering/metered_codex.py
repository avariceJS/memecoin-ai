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

from .quota_policy import choose_quota_capacity, reset_recovery_decision
from .usage_ledger import EXTERNAL_COLLECTORS, UsageLedger


class Permit:
    def __init__(self, controller, dispatch_id, model):
        self.controller, self.dispatch_id, self.model = controller, dispatch_id, model
        self._used = False
        self._lock = threading.Lock()
        self.terminal_usage_qualified = False

    def consume(self, thread_id, model):
        with self._lock:
            if self._used or model != self.model:
                raise RuntimeError("permit_replay_or_model_mismatch")
            self.controller._check_send(self.dispatch_id)
            if not self.controller.ledger.consume_dispatch(self.dispatch_id):
                raise RuntimeError("dispatch_already_consumed")
            self.terminal_usage_qualified = (
                self.controller._last_preflight.get("terminal_usage_qualified") is True
            )
            self._used = True


class Controller:
    def __init__(
        self,
        ledger,
        account_read,
        *,
        state,
        clock=time.time,
        preflight=None,
        state_reconciler=None,
    ):
        self.ledger, self.account_read, self.state, self.clock = (
            ledger,
            account_read,
            Path(state),
            clock,
        )
        self.state_reconciler = state_reconciler
        self.preflight, self.lock, self.closed = preflight, threading.RLock(), False
        self._account_lock = threading.Lock()
        self._monitors = set()
        self._monitor_lock = threading.Lock()
        self._poll_seconds = 300
        self._last_preflight = {}
        self.last_post_refresh_error = None

    def _refresh_after_dispatch(self, reason):
        try:
            self.refresh(reason)
        except Exception as exc:
            self.last_post_refresh_error = type(exc).__name__ + ":" + str(exc)

    def import_external(self, batch, collector_id, evidence_hash):
        if type(collector_id) is not str or collector_id not in EXTERNAL_COLLECTORS:
            raise ValueError("external_collector_not_allowed")
        return self.ledger.import_external(batch, collector_id, evidence_hash)

    @staticmethod
    def _quota_evidence(quota):
        if not isinstance(quota, dict):
            return None
        keys = (
            "ordinaryUsageAllowed",
            "rateLimitsByLimitId",
            "spendControlReached",
            "providerReached",
        )
        return {key: quota[key] for key in keys if key in quota}

    def _pause_resources(self, quota, observed_at, reason):
        safe = self._quota_evidence(quota)
        pause = {
            "schema": "brain-resource-pause-v1",
            "status": "paused",
            "paused_at": self.clock(),
            "reason": str(reason)[:200],
            "recovery_basis": (
                "unknown_access"
                if str(reason).startswith("quota_read_failed:")
                else "compare_to_pause"
            ),
            "paused_reading": {"rate_limits": safe, "observed_at": observed_at},
            "recovery_readings": [],
        }
        state_ledger = UsageLedger(self.ledger.path)
        try:
            state_ledger.set_controller_state("resource_pause", pause)
        finally:
            state_ledger.close()

    def _require_resource_recovery(self, quota, observed_at):
        state_ledger = UsageLedger(self.ledger.path)
        try:
            return self._require_resource_recovery_with_ledger(state_ledger, quota, observed_at)
        finally:
            state_ledger.close()

    def _require_resource_recovery_with_ledger(self, state_ledger, quota, observed_at):
        pause = state_ledger.get_controller_state("resource_pause")
        if pause is None:
            return
        expected_pause = json.loads(json.dumps(pause, sort_keys=True))
        safe = self._quota_evidence(quota)
        sample = {"rate_limits": safe, "observed_at": observed_at}
        readings = list(pause.get("recovery_readings", []))
        if observed_at >= pause.get("paused_at", float("inf")):
            previous_at = (
                readings[-1].get("observed_at", float("inf")) if readings else float("inf")
            )
            if not readings:
                readings = [sample]
            elif observed_at > previous_at:
                readings = (readings + [sample])[-2:]
        pause["recovery_readings"] = readings
        reason = "insufficient_readings"
        ready = False
        if len(readings) == 2:
            paused_reading = pause.get("paused_reading")
            if pause.get("recovery_basis") == "unknown_access":
                paused_reading = None
            decision = reset_recovery_decision(
                paused_reading,
                readings,
                now=self.clock(),
                state_reconciled=(
                    state_ledger.state_reconciled(self.clock())
                    and self.state_reconciler is not None
                    and self.state_reconciler(self.clock()) is True
                ),
            )
            ready = decision["ready"] is True
            reason = decision["reason"]
        if not ready:
            if not state_ledger.compare_controller_state("resource_pause", expected_pause, pause):
                raise RuntimeError("resource_pause_changed_retry")
            raise RuntimeError("resource_recovery_required:" + reason)
        recovered = {**pause, "status": "recovered", "recovery_decision": decision}
        if not state_ledger.finish_resource_recovery(expected_pause, recovered):
            raise RuntimeError("resource_pause_changed_retry")

    def refresh(self, reason="admission"):
        with self._account_lock:
            try:
                data = self.account_read()
            except Exception as exc:
                self._pause_resources(None, self.clock(), "quota_read_failed:" + type(exc).__name__)
                raise
        if isinstance(data, dict) and "rate_limits" in data:
            quota = data["rate_limits"]
            observed_at = data.get("observed_at")
        else:
            quota = data
            observed_at = self.clock()
        decision = choose_quota_capacity(
            quota,
            observed_at=observed_at,
            now=self.clock(),
            active_teams=0,
            stop=False,
            reserve_points=1,
        )
        self._poll_seconds = decision["poll_seconds"]
        if decision["slots"] == 0:
            self._pause_resources(quota, observed_at, decision["reason"])
            raise RuntimeError("quota_unavailable:" + decision["reason"])
        self._require_resource_recovery(quota, observed_at)
        self._last_quota = quota
        self._last_quota_observed_at = observed_at
        self._last_preflight = {}
        if self.preflight:
            pf = self.preflight()
            if not isinstance(pf, dict) or not pf.get("resources", {}).get("policy_id"):
                raise RuntimeError("preflight_unknown")
            self._last_preflight = pf
        return quota

    def admit(self, dispatch_id, stream, baseline_or_none, model, estimate, deadline_at, context):
        with self.lock:
            if (self.state / "STOP").exists():
                raise RuntimeError("STOP")
            quota = self.refresh("admit")
            if model == "gpt-6-astra" and context.get("role") != "manual-chief":
                raise RuntimeError("automatic_astra_forbidden")
            by = quota["rateLimitsByLimitId"]
            use = max(
                limit[key]["usedPercent"]
                for limit in by.values()
                for key in ("primary", "secondary")
                if limit[key] is not None
            )
            row = self.ledger.reserve(
                dispatch_id,
                stream,
                model,
                estimate,
                deadline_at,
                context=context,
                baseline=baseline_or_none,
                max_active_teams=1 if use >= 97 else 2,
                max_model_dispatches=2,
                now=self.clock(),
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
        stop = threading.Event()

        def run():
            while not self.closed and not stop.is_set():
                remaining = deadline - self.clock()
                if remaining <= 0:
                    cancel.set()
                    return
                if stop.wait(min(self._poll_seconds, remaining)):
                    return
                try:
                    self.refresh("active")
                except Exception:
                    cancel.set()
                    return
                if self.clock() >= deadline:
                    cancel.set()
                    return

        t = threading.Thread(target=run, daemon=True)
        t.start()
        with self._monitor_lock:
            self._monitors.add((t, stop))
        return stop

    def close(self):
        self.closed = True
        with self._monitor_lock:
            monitors = list(self._monitors)
        for t, stop in monitors:
            stop.set()
            t.join(timeout=45)


class MeteredCodex:
    def __init__(self, rpc, controller, context):
        self.rpc, self.controller, self.context = rpc, controller, dict(context)
        self.thread = None

    def __getattr__(self, name):
        return getattr(self.rpc, name)

    def start(self, model, instructions):
        episode_id = self.context.get("episode_id")
        if type(episode_id) is not str or not episode_id.strip():
            raise RuntimeError("dispatch_episode_required")
        did = (
            "start:"
            + hashlib.sha256(
                json.dumps([episode_id, self.context, model, instructions], sort_keys=True).encode()
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
        try:
            parameters = inspect.signature(self.rpc.start).parameters
            accepts_permit = "permit" in parameters or any(
                item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()
            )
            if accepts_permit:
                self.thread = self.rpc.start(model, instructions, permit=permit)
            else:
                permit.consume("new-thread", model)
                self.thread = self.rpc.start(model, instructions)
            self.controller.ledger.settle_new_stream(
                did, self.thread, evidence="provider-certified-new-unused-thread:" + did
            )
            self.controller._refresh_after_dispatch("after_start")
            return self.thread
        except Exception as exc:
            if self.controller.ledger.dispatch(did):
                self.controller.ledger.record_outcome(did, "unknown", {"error": type(exc).__name__})
            raise

    def resume(self, thread_id, model):
        self.thread = self.rpc.resume(thread_id, model)
        return self.thread

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
        if (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds)
            or deadline_seconds <= 0
        ):
            raise ValueError("invalid_turn_deadline")
        max_seconds = {
            "gpt-6.1-sol": 1200,
            "gpt-6-luna": 600,
            "gpt-6-astra": 120,
        }.get(model)
        if (
            max_seconds is None
            or deadline_seconds > max_seconds
            or (model == "gpt-6-astra" and self.context.get("role") != "manual-chief")
        ):
            raise ValueError("model_or_turn_limit_forbidden")
        deadline = self.controller.clock() + deadline_seconds
        snapshot = self.controller.ledger.current_snapshot(thread_id)
        baseline = snapshot["counters"] if snapshot is not None else None
        permit = self.controller.admit(
            dispatch_id, thread_id, baseline, model, estimate, deadline, self.context
        )

        def bind(turn_id):
            bound_turn[0] = turn_id
            self.controller.ledger.bind(dispatch_id, thread_id, turn_id)
            return started(turn_id) if started else None

        cancel = threading.Event()
        monitor_stop = self.controller.monitor(deadline, cancel)
        original_sink = getattr(self.rpc, "sink", None)
        bound_turn = [None]

        def record_event(event):
            if original_sink:
                original_sink(event)
            if event.get("kind") not in ("token_usage", "turn_completed"):
                return
            turn_id = event.get("turn_id")
            target_dispatch = (
                dispatch_id
                if turn_id and turn_id == bound_turn[0]
                else "unmatched:" + str(thread_id) + ":" + str(turn_id or "unknown")
            )
            event_id = hashlib.sha256(
                json.dumps(
                    [target_dispatch, event], sort_keys=True, separators=(",", ":"), default=str
                ).encode()
            ).hexdigest()
            observed_at = self.controller.clock()
            counters = None
            snapshot = None
            if event.get("kind") == "token_usage":
                try:
                    counters = _canonical_counters(event["usage"]["total"])
                    snapshot = self.controller.ledger.current_snapshot(thread_id)
                except (KeyError, TypeError, ValueError):
                    counters = None
                    snapshot = None
                if counters is not None and snapshot is not None:
                    event_key = hashlib.sha256(
                        json.dumps(
                            [thread_id, counters, event.get("turn_id"), observed_at],
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest()
                    self.controller.ledger.observe(
                        thread_id,
                        snapshot["epoch"],
                        "provider-usage:" + event_key,
                        counters,
                        source="provider_usage_lower_bound",
                        sequence=counters["total_tokens"],
                        observed_at=observed_at,
                    )
            self.controller.ledger.observe_receipt(
                event_id,
                target_dispatch,
                counters,
                turn_id,
                event["kind"],
                None,
                observed_at,
                event,
            )

        if original_sink is not None:
            self.rpc.sink = record_event
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
                cancel_event=cancel,
                start_usage=_rpc_counters(baseline),
            )
        except Exception as exc:
            self.controller.ledger.record_outcome(
                dispatch_id,
                "timeout" if "deadline" in str(exc) else "unknown",
                {"error": type(exc).__name__},
            )
            provider_terminal = self.controller.ledger.reconcile_dispatch_from_receipt(dispatch_id)
            if (
                provider_terminal
                and provider_terminal.get("status") == "completed"
                and provider_terminal.get("usage_terminal") is True
            ):
                try:
                    self.controller.ledger.cover_dispatch(
                        dispatch_id,
                        thread_id=thread_id,
                        turn_id=provider_terminal["turn_id"],
                        model=model,
                        counters=_canonical_counters(provider_terminal["usage_end"]),
                        terminal_qualified=True,
                        observed_at=self.controller.clock(),
                    )
                except (KeyError, TypeError, ValueError):
                    pass
            self.controller._refresh_after_dispatch("after_failure")
            raise
        else:
            self.controller.ledger.record_outcome(
                dispatch_id,
                "completed",
                {"turn_id": out.get("turn_id"), "usage_end": out.get("usage_end")},
            )
            if out.get("usage_terminal") is True:
                try:
                    end = _canonical_counters(out["usage_end"])
                    self.controller.ledger.cover_dispatch(
                        dispatch_id,
                        thread_id=thread_id,
                        turn_id=out["turn_id"],
                        model=model,
                        counters=end,
                        terminal_qualified=permit.terminal_usage_qualified,
                        observed_at=self.controller.clock(),
                    )
                except (KeyError, TypeError, ValueError):
                    pass  # Keep the durable reservation when attribution is incomplete.
            self.controller._refresh_after_dispatch("after_completion")
            out["dispatch_id"] = dispatch_id
            return out
        finally:
            monitor_stop.set()
            if original_sink is not None:
                self.rpc.sink = original_sink


def _canonical_counters(value):
    if type(value) is not dict:
        raise ValueError("usage counters must be an object")
    names = {
        "inputTokens": "input_tokens",
        "cachedInputTokens": "cached_input_tokens",
        "outputTokens": "output_tokens",
        "reasoningOutputTokens": "reasoning_output_tokens",
        "totalTokens": "total_tokens",
    }
    if set(value) != set(names):
        raise ValueError("usage counters have unknown shape")
    return {target: value[source] for source, target in names.items()}


def _rpc_counters(value):
    if value is None:
        return None
    names = {
        "input_tokens": "inputTokens",
        "cached_input_tokens": "cachedInputTokens",
        "output_tokens": "outputTokens",
        "reasoning_output_tokens": "reasoningOutputTokens",
        "total_tokens": "totalTokens",
    }
    if type(value) is not dict or set(value) != set(names):
        raise ValueError("ledger counters have unknown shape")
    return {target: value[source] for source, target in names.items()}


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

    from .supervisor import config as frozen_config
    from .supervisor import organization_state_reconciled

    def preflight():
        current = frozen_config(state)
        if current != cfg:
            raise RuntimeError("controller_config_changed")
        return current

    controller = Controller(
        ledger,
        lambda: account.call("account/rateLimits/read"),
        state=state,
        preflight=preflight,
        state_reconciler=lambda now: organization_state_reconciled(state, ledger, now),
    )
    try:
        yield MeteredCodex(rpc, controller, context)
    finally:
        controller.close()
        ledger.close()
        account.close()
        rpc.close()
