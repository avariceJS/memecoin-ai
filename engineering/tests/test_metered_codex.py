import json
import threading
import time

import pytest

from engineering.metered_codex import Controller, MeteredCodex, _canonical_usage_total
from engineering.usage_ledger import UsageLedger


class FakeRPC:
    def __init__(self, ledger):
        self.ledger = ledger
        self.calls = 0

    def start(self, model, instructions, *, permit):
        permit.consume("new-thread", model)
        return "thread"

    def turn(self, thread, prompt, **kwargs):
        self.calls += 1
        assert self.ledger.summary()["reservations"]
        kwargs["permit"].consume(thread, kwargs["model"])
        kwargs["started"]("provider-turn")
        return {
            "text": "{}",
            "turn_id": "provider-turn",
            "usage": {"total": {"totalTokens": 10}},
            "usage_start": None,
            "usage_end": None,
            "usage_delta": None,
            "usage_terminal": False,
        }


def make(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.sqlite")
    rpc = FakeRPC(ledger)
    controller = Controller(
        ledger,
        state=tmp_path,
        clock=lambda: 1000,
        preflight=lambda: {"resources": {"policy_id": "CEO-20261020-02"}},
    )
    context = {
        "role": "worker",
        "team": "Experience",
        "task": "T",
        "authority": "CEO-20261020-02",
    }
    return ledger, rpc, controller, MeteredCodex(rpc, controller, context)


def test_canonical_usage_accepts_optional_cache_write_counter():
    five_fields = {
        "inputTokens": 12,
        "cachedInputTokens": 4,
        "outputTokens": 3,
        "reasoningOutputTokens": 1,
        "totalTokens": 15,
    }
    expected = {
        "input_tokens": 12,
        "cached_input_tokens": 4,
        "output_tokens": 3,
        "reasoning_output_tokens": 1,
        "total_tokens": 15,
    }

    assert _canonical_usage_total(five_fields) == expected
    assert _canonical_usage_total({**five_fields, "cacheWriteInputTokens": 0}) == expected
    assert _canonical_usage_total({**five_fields, "cacheWriteInputTokens": 8}) == expected


@pytest.mark.parametrize("value", [True, -1, 1.5, "8"])
def test_canonical_usage_rejects_invalid_optional_cache_write_counter(value):
    five_fields = {
        "inputTokens": 12,
        "cachedInputTokens": 4,
        "outputTokens": 3,
        "reasoningOutputTokens": 1,
        "totalTokens": 15,
    }
    assert _canonical_usage_total({**five_fields, "cacheWriteInputTokens": value}) is None


def test_canonical_usage_preserves_raw_optional_counter_in_receipt(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.sqlite")

    class UsageRPC(FakeRPC):
        def __init__(self, ledger):
            super().__init__(ledger)
            self.sink = lambda _event: None

        def turn(self, thread, prompt, **kwargs):
            kwargs["permit"].consume(thread, kwargs["model"])
            kwargs["started"]("cache-write-turn")
            usage = {
                "inputTokens": 12,
                "cachedInputTokens": 4,
                "outputTokens": 3,
                "reasoningOutputTokens": 1,
                "totalTokens": 15,
                "cacheWriteInputTokens": 8,
            }
            self.sink(
                {
                    "kind": "token_usage",
                    "thread_id": thread,
                    "turn_id": "cache-write-turn",
                    "usage": {"total": usage},
                }
            )
            return {
                "text": "{}",
                "turn_id": "cache-write-turn",
                "usage": {"total": usage},
                "usage_start": None,
                "usage_end": None,
                "usage_delta": None,
                "usage_terminal": False,
            }

    rpc = UsageRPC(ledger)
    controller = Controller(
        ledger,
        state=tmp_path,
        clock=lambda: 1000,
        preflight=lambda: {"resources": {"policy_id": "CEO-20261020-02"}},
    )
    client = MeteredCodex(
        rpc, controller, {"role": "worker", "team": "Experience", "task": "cache-write"}
    )
    thread = client.start("gpt-6-luna", "start")
    client.turn(
        thread,
        "bounded",
        dispatch_id="cache-write-dispatch",
        model="gpt-6-luna",
        estimate={"tokens": 100, "wpu": 1},
        tool=lambda _: None,
        deadline_seconds=10,
    )
    row = ledger.db.execute(
        "SELECT receipt,evidence FROM receipts WHERE provider_turn_id=?", ("cache-write-turn",)
    ).fetchone()
    assert json.loads(row["receipt"]) == {
        "input_tokens": 12,
        "cached_input_tokens": 4,
        "output_tokens": 3,
        "reasoning_output_tokens": 1,
        "total_tokens": 15,
    }
    assert json.loads(row["evidence"])["usage"]["total"]["cacheWriteInputTokens"] == 8
    controller.close()
    ledger.close()


def test_pre_send_reservation_and_replay_and_stop(tmp_path):
    ledger, rpc, controller, client = make(tmp_path)
    thread = client.start("gpt-6-luna", "bounded")
    kwargs = {
        "dispatch_id": "d1",
        "model": "gpt-6-luna",
        "estimate": {"tokens": 100, "wpu": 1},
        "tool": lambda _: None,
        "deadline_seconds": 30,
    }
    result = client.turn(thread, "bounded", **kwargs)
    assert result["dispatch_id"] == "d1"
    assert ledger.summary()["reservations"]
    calls = rpc.calls
    with pytest.raises(RuntimeError):
        client.turn(thread, "again", **kwargs)
    assert rpc.calls == calls
    (tmp_path / "STOP").touch()
    with pytest.raises(RuntimeError):
        client.turn(thread, "blocked", **{**kwargs, "dispatch_id": "d2"})
    assert rpc.calls == calls
    controller.close()
    ledger.close()


def test_unknown_quota_usage_does_not_gate_dispatch_and_identity_still_prevents_replay(tmp_path):
    ledger, rpc, controller, client = make(tmp_path)
    thread = client.start("gpt-6-luna", "start")
    client.turn(
        thread,
        "prompt",
        dispatch_id="d",
        model="gpt-6-luna",
        estimate={"tokens": 1, "wpu": 1},
        tool=lambda _: None,
        deadline_seconds=3,
    )
    assert rpc.calls == 1
    with pytest.raises((RuntimeError, ValueError)):
        client.turn(
            thread,
            "retry",
            dispatch_id="d",
            model="gpt-6-luna",
            estimate={"tokens": 1, "wpu": 1},
            tool=lambda _: None,
            deadline_seconds=3,
        )
    assert rpc.calls == 1
    controller.close()
    ledger.close()


def test_lost_response_and_unknown_end_keep_dispatch_obligation(tmp_path):
    ledger, rpc, controller, client = make(tmp_path)
    thread = client.start("gpt-6-luna", "bounded")
    rpc.turn = lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("lost response"))
    with pytest.raises(TimeoutError):
        client.turn(
            thread,
            "prompt",
            dispatch_id="lost",
            model="gpt-6-luna",
            estimate={"tokens": 100, "wpu": 1},
            tool=lambda _: None,
            deadline_seconds=20,
        )
    rows = ledger.summary()["reservations"]
    assert any(row.get("dispatch_id") == "lost" and row["status"] == "unknown" for row in rows)
    with pytest.raises((RuntimeError, ValueError)):
        client.turn(
            thread,
            "retry",
            dispatch_id="lost",
            model="gpt-6-luna",
            estimate={"tokens": 100, "wpu": 1},
            tool=lambda _: None,
            deadline_seconds=20,
        )
    assert rpc.calls == 0
    controller.close()
    ledger.close()


def test_deadline_monitor_cancels_without_quota_polling(tmp_path):
    ledger, _rpc, controller, _client = make(tmp_path)
    cancelled = threading.Event()
    watcher = controller.monitor(time.monotonic() + 0.05, cancelled.set)
    assert cancelled.wait(2)
    controller.stop_monitor(watcher)
    assert not watcher.is_alive()
    assert watcher not in controller._monitors
    controller.close()
    ledger.close()


def test_stop_file_cancels_active_dispatch_without_account_polling(tmp_path):
    ledger, _rpc, controller, _client = make(tmp_path)
    cancelled = threading.Event()
    watcher = controller.monitor(time.monotonic() + 10, cancelled.set)
    (tmp_path / "STOP").write_text("STOP\n", encoding="utf-8")
    assert cancelled.wait(2)
    controller.stop_monitor(watcher)
    assert not watcher.is_alive()
    controller.close()
    ledger.close()


def test_unqualified_progress_across_model_switch_stays_unassigned(tmp_path):
    ledger = UsageLedger(tmp_path / "switch.sqlite")

    class SwitchingRPC(FakeRPC):
        def __init__(self, ledger):
            super().__init__(ledger)
            self.sink = lambda _event: None
            self.endpoints = iter((100, 200, 300))

        def turn(self, thread, prompt, **kwargs):
            self.calls += 1
            kwargs["permit"].consume(thread, kwargs["model"])
            turn_id = "switch-turn-" + str(self.calls)
            kwargs["started"](turn_id)
            total = next(self.endpoints)
            self.sink(
                {
                    "kind": "token_usage",
                    "turn_id": turn_id,
                    "usage": {
                        "total": {
                            "inputTokens": total,
                            "cachedInputTokens": 0,
                            "outputTokens": 0,
                            "reasoningOutputTokens": 0,
                            "totalTokens": total,
                        }
                    },
                }
            )
            return {
                "text": "{}",
                "turn_id": turn_id,
                "usage": {"total": {"totalTokens": total}},
                "usage_start": None,
                "usage_end": None,
                "usage_delta": None,
                "usage_terminal": False,
            }

    rpc = SwitchingRPC(ledger)
    controller = Controller(
        ledger,
        state=tmp_path,
        clock=lambda: 1000,
        preflight=lambda: {"resources": {"policy_id": "CEO-20261020-02"}},
    )
    context = {"role": "worker", "team": "Experience", "task": "switch"}
    client = MeteredCodex(rpc, controller, context)
    thread = client.start("gpt-6-luna", "start")
    for index, model in enumerate(("gpt-6-luna", "gpt-6.1-sol", "gpt-6-luna")):
        client.turn(
            thread,
            "bounded",
            dispatch_id=f"switch-{index}",
            model=model,
            estimate={"tokens": 100, "wpu": 1},
            tool=lambda _: None,
            deadline_seconds=10,
        )
    summary = ledger.summary()
    assert summary["incremental"]["total_tokens"] == 300
    assert summary["by_model"] == {}
    assert (
        sum(
            row["counters"]["total_tokens"]
            for row in summary["unresolved"]
            if row["reason"] == "model_attribution_unverified"
        )
        == 300
    )
    controller.close()
    ledger.close()


def test_qualified_terminal_reconciliation_attributes_only_certified_interval(tmp_path):
    ledger = UsageLedger(tmp_path / "qualified.sqlite")

    class TerminalRPC(FakeRPC):
        def __init__(self, ledger):
            super().__init__(ledger)
            self.sink = lambda _event: None

        def turn(self, thread, prompt, **kwargs):
            kwargs["permit"].consume(thread, kwargs["model"])
            kwargs["started"]("qualified-turn")
            for total in (40, 70, 100):
                self.sink(
                    {
                        "kind": "token_usage",
                        "turn_id": "qualified-turn",
                        "usage": {
                            "total": {
                                "inputTokens": total,
                                "cachedInputTokens": 0,
                                "outputTokens": 0,
                                "reasoningOutputTokens": 0,
                                "totalTokens": total,
                            }
                        },
                    }
                )
            return {
                "text": "{}",
                "turn_id": "qualified-turn",
                "usage": {"total": {"totalTokens": 100}},
                "usage_start": None,
                "usage_end": None,
                "usage_delta": None,
                "usage_terminal": False,
            }

    rpc = TerminalRPC(ledger)
    controller = Controller(
        ledger,
        state=tmp_path,
        clock=lambda: 1000,
        preflight=lambda: {"resources": {"policy_id": "CEO-20261020-02"}},
    )
    client = MeteredCodex(
        rpc, controller, {"role": "worker", "team": "Experience", "task": "terminal"}
    )
    thread = client.start("gpt-6-luna", "start")
    client.turn(
        thread,
        "bounded",
        dispatch_id="qualified-dispatch",
        model="gpt-6-luna",
        estimate={"tokens": 100, "wpu": 1},
        tool=lambda _: None,
        deadline_seconds=10,
    )
    before = ledger.summary()
    assert before["by_model"] == {}
    assert any(row["reason"] == "model_attribution_unverified" for row in before["unresolved"])
    dispatch = ledger.dispatch("qualified-dispatch")
    assert json.loads(dispatch["baseline"]) == {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
    }
    progress = ledger.db.execute(
        "SELECT event_id,sequence,counters FROM events "
        "WHERE source='codex-rpc-unattributed' ORDER BY sequence"
    ).fetchall()
    controller.preflight = lambda: {
        "resources": {"policy_id": "CEO-20261020-02"},
        "terminal_usage_qualified": True,
    }
    terminal_receipt = {
        "thread_id": thread,
        "turn_id": "qualified-turn",
        "epoch": "initial",
        "baseline": {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
            "total_tokens": 0,
        },
        "end": {
            "input_tokens": 100,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
            "total_tokens": 100,
        },
    }
    stream_observations = [
        {
            "event_id": item["event_id"],
            "sequence": item["sequence"],
            "counters": json.loads(item["counters"]),
        }
        for item in progress
    ]
    controller.reconcile_dispatch(
        "qualified-dispatch",
        terminal_receipt=terminal_receipt,
        stream_observations=stream_observations,
    )
    # Exact terminal evidence is idempotent after the dispatch is settled.
    controller.reconcile_dispatch(
        "qualified-dispatch",
        terminal_receipt=terminal_receipt,
        stream_observations=stream_observations,
    )
    after = ledger.summary()
    assert after["by_model"]["gpt-6-luna"]["input_tokens"] == 100
    assert after["unresolved"] == []
    assert after["reservations"] == []
    assert ledger.trusted_terminal_baseline(thread, "initial")["total_tokens"] == 100
    before_conflicting_replay = after["incremental"]
    conflicting_receipt = {
        **terminal_receipt,
        "end": {**terminal_receipt["end"], "input_tokens": 200, "total_tokens": 200},
    }
    try:
        controller.reconcile_dispatch(
            "qualified-dispatch",
            terminal_receipt=conflicting_receipt,
            stream_observations=stream_observations,
        )
    except ValueError as exc:
        assert str(exc) == "terminal_receipt_conflict"
    else:
        raise AssertionError("conflicting terminal replay was accepted")
    assert ledger.summary()["incremental"] == before_conflicting_replay
    assert ledger.db.execute("SELECT COUNT(*) FROM terminal_receipt_conflicts").fetchone()[0] == 1
    controller.close()
    ledger.close()


def test_completed_turn_without_usage_keeps_next_baseline_unknown(tmp_path):
    ledger = UsageLedger(tmp_path / "no-usage.sqlite")

    class NoUsageRPC(FakeRPC):
        def __init__(self, ledger):
            super().__init__(ledger)
            self.sink = lambda _event: None

    rpc = NoUsageRPC(ledger)
    controller = Controller(
        ledger,
        state=tmp_path,
        clock=lambda: 1000,
        preflight=lambda: {"resources": {"policy_id": "CEO-20261020-02"}},
    )
    client = MeteredCodex(
        rpc, controller, {"role": "worker", "team": "Experience", "task": "no-usage"}
    )
    thread = client.start("gpt-6-luna", "start")
    kwargs = {
        "model": "gpt-6-luna",
        "estimate": {"tokens": 100, "wpu": 1},
        "tool": lambda _: None,
        "deadline_seconds": 10,
    }
    client.turn(thread, "first", dispatch_id="no-usage-first", **kwargs)
    assert json.loads(ledger.dispatch("no-usage-first")["baseline"])["total_tokens"] == 0
    client.turn(thread, "second", dispatch_id="no-usage-second", **kwargs)
    assert ledger.dispatch("no-usage-second")["baseline"] is None
    controller.close()
    ledger.close()


def test_lost_provider_binding_prevents_zero_baseline_reuse(tmp_path):
    ledger = UsageLedger(tmp_path / "lost-bind.sqlite")

    class LostBindingRPC(FakeRPC):
        def __init__(self, ledger):
            super().__init__(ledger)
            self.sink = lambda _event: None
            self.lose_first = True

        def turn(self, thread, prompt, **kwargs):
            kwargs["permit"].consume(thread, kwargs["model"])
            if self.lose_first:
                self.lose_first = False
                raise TimeoutError("lost before started notification")
            kwargs["started"]("bound-second-turn")
            return {
                "text": "{}",
                "turn_id": "bound-second-turn",
                "usage": {"total": {"totalTokens": 0}},
                "usage_start": None,
                "usage_end": None,
                "usage_delta": None,
                "usage_terminal": False,
            }

    rpc = LostBindingRPC(ledger)
    controller = Controller(
        ledger,
        state=tmp_path,
        clock=lambda: 1000,
        preflight=lambda: {"resources": {"policy_id": "CEO-20261020-02"}},
    )
    client = MeteredCodex(
        rpc, controller, {"role": "worker", "team": "Experience", "task": "lost-bind"}
    )
    thread = client.start("gpt-6-luna", "start")
    with pytest.raises(TimeoutError):
        client.turn(
            thread,
            "first",
            dispatch_id="lost-before-bind",
            model="gpt-6-luna",
            estimate={"tokens": 100, "wpu": 1},
            tool=lambda _: None,
            deadline_seconds=10,
        )
    first = ledger.dispatch("lost-before-bind")
    assert first["status"] == "unknown"
    assert first["epoch"] is None
    client.turn(
        thread,
        "second",
        dispatch_id="after-lost-bind",
        model="gpt-6-luna",
        estimate={"tokens": 100, "wpu": 1},
        tool=lambda _: None,
        deadline_seconds=10,
    )
    assert ledger.dispatch("after-lost-bind")["baseline"] is None
    controller.close()
    ledger.close()
