import json
import threading
import time

import pytest

from engineering.metered_codex import Controller, MeteredCodex
from engineering.usage_ledger import UsageLedger


def quota(percent=68):
    return {
        "ordinaryUsageAllowed": True,
        "rateLimitsByLimitId": {
            "codex": {
                "primary": {"usedPercent": percent, "resetsAt": 9999},
                "secondary": None,
                "rateLimitReachedType": None,
                "spendControlReached": False,
                "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
            }
        },
    }


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


def make(tmp_path, percent=68):
    ledger = UsageLedger(tmp_path / "usage.sqlite")
    rpc = FakeRPC(ledger)
    controller = Controller(
        ledger,
        lambda: quota(percent),
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


def test_quota_99_blocks_before_reservation(tmp_path):
    ledger, _, controller, client = make(tmp_path, 99)
    with pytest.raises(RuntimeError):
        client.turn(
            "thread",
            "prompt",
            dispatch_id="d",
            model="gpt-6-luna",
            estimate={"tokens": 1, "wpu": 1},
            tool=lambda _: None,
            deadline_seconds=3,
        )
    assert not ledger.summary()["reservations"]
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


def test_unknown_required_or_paid_quota_evidence_stops_before_send(tmp_path):
    for name, change in (
        ("missing reset", lambda q: q["rateLimitsByLimitId"]["codex"]["primary"].pop("resetsAt")),
        (
            "paid credits",
            lambda q: q["rateLimitsByLimitId"]["codex"]["credits"].update(hasCredits=True),
        ),
        ("unknown spend", lambda q: q["rateLimitsByLimitId"]["codex"].pop("spendControlReached")),
    ):
        q = quota()
        change(q)
        ledger = UsageLedger(tmp_path / (name.replace(" ", "-") + ".sqlite"))
        rpc = FakeRPC(ledger)
        controller = Controller(ledger, lambda q=q: q, state=tmp_path, clock=lambda: 1000)
        client = MeteredCodex(rpc, controller, {"role": "worker", "team": "X", "task": "T"})
        with pytest.raises(RuntimeError):
            client.turn(
                "thread",
                "prompt",
                dispatch_id=name,
                model="gpt-6-luna",
                estimate={"tokens": 100, "wpu": 1},
                tool=lambda _: None,
                deadline_seconds=10,
            )
        assert rpc.calls == 0
        assert not ledger.summary()["reservations"]
        controller.close()
        ledger.close()


def test_stop_monitor_waits_for_inflight_quota_read_and_joins_watcher(tmp_path):
    ledger, _rpc, controller, _client = make(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def blocked_read():
        entered.set()
        release.wait()
        return quota()

    controller.account_read = blocked_read
    watcher = controller.monitor(time.monotonic() + 30, lambda: None)
    assert entered.wait(2)
    stopper = threading.Thread(target=controller.stop_monitor, args=(watcher,))
    stopper.start()
    time.sleep(0.05)
    assert stopper.is_alive()
    release.set()
    stopper.join(3)
    assert not stopper.is_alive()
    assert not watcher.is_alive()
    assert watcher not in controller._monitors
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
        lambda: quota(),
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
        lambda: quota(),
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
        lambda: quota(),
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
