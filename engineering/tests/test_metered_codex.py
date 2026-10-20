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
        self.start_calls = 0

    def start(self, model, instructions, *, permit):
        permit.consume("new-thread", model)
        self.start_calls += 1
        return f"thread-{self.start_calls}"

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
        "episode_id": "test-episode",
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


def test_start_replay_is_blocked_but_new_attempt_gets_a_distinct_dispatch(tmp_path):
    ledger = UsageLedger(tmp_path / "start-replay.sqlite")
    rpc = FakeRPC(ledger)
    controller = Controller(ledger, lambda: quota(), state=tmp_path, clock=lambda: 1000)
    first = MeteredCodex(
        rpc,
        controller,
        {
            "role": "worker",
            "team": "Experience",
            "task": "T",
            "attempt_id": 1,
            "episode_id": "attempt:1:worker",
            "authority": "CEO-20261020-02",
        },
    )
    thread1 = first.start("gpt-6-luna", "bounded")
    with pytest.raises(RuntimeError, match="dispatch_already_sent"):
        first.start("gpt-6-luna", "bounded")
    second = MeteredCodex(
        rpc,
        controller,
        {
            "role": "worker",
            "team": "Experience",
            "task": "T",
            "attempt_id": 2,
            "episode_id": "attempt:2:worker",
            "authority": "CEO-20261020-02",
        },
    )
    thread2 = second.start("gpt-6-luna", "bounded")
    assert (thread1, thread2, rpc.start_calls) == ("thread-1", "thread-2", 2)
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


def test_resource_pause_survives_restart_until_two_spaced_reads_and_reconciliation(tmp_path):
    ledger = UsageLedger(tmp_path / "pause.sqlite")
    current = {"time": 1000, "percent": 99}
    controller = Controller(
        ledger,
        lambda: quota(current["percent"]),
        state=tmp_path,
        clock=lambda: current["time"],
    )
    with pytest.raises(RuntimeError, match="quota_unavailable:reserve_threshold"):
        controller.refresh()
    controller.close()

    current["percent"] = 68
    resumed = Controller(
        ledger,
        lambda: quota(current["percent"]),
        state=tmp_path,
        clock=lambda: current["time"],
    )
    with pytest.raises(RuntimeError, match="resource_recovery_required:insufficient_readings"):
        resumed.refresh()
    assert len(ledger.get_controller_state("resource_pause")["recovery_readings"]) == 1
    current["time"] = 1060
    permit = resumed.admit(
        "after-restoration",
        "fresh-thread",
        None,
        "gpt-6-luna",
        {"tokens": 20, "wpu": 1},
        1100,
        {"role": "worker", "team": "Experience"},
    )
    assert permit.dispatch_id == "after-restoration"
    assert ledger.get_controller_state("resource_pause") is None
    assert ledger.get_controller_state("resource_pause_last")["status"] == "recovered"
    resumed.close()
    ledger.close()


def test_quota_95_shortens_watchdog_poll_and_97_limits_new_team(tmp_path):
    ledger, _, controller, client = make(tmp_path, 95)
    controller.refresh()
    assert controller._poll_seconds == 60
    ledger.reserve(
        "active",
        "thread-active",
        "gpt-6-luna",
        {"tokens": 50, "wpu": 1},
        1100,
        context={"role": "worker", "team": "Experience"},
    )
    controller.account_read = lambda: quota(97)
    same_team = controller.admit(
        "same-team",
        "thread-same-team",
        None,
        "gpt-6-luna",
        {"tokens": 50, "wpu": 1},
        1100,
        {"role": "worker", "team": "Experience"},
    )
    assert same_team
    with pytest.raises(ValueError, match="active_team_limit"):
        controller.admit(
            "other-team",
            "thread-other-team",
            None,
            "gpt-6-luna",
            {"tokens": 50, "wpu": 1},
            1100,
            {"role": "worker", "team": "Platform"},
        )
    controller.close()
    ledger.close()


def test_stale_and_future_quota_observations_fail_closed(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.sqlite")
    controller = Controller(
        ledger,
        lambda: {"rate_limits": quota(68), "observed_at": 600},
        state=tmp_path,
        clock=lambda: 1000,
    )
    with pytest.raises(RuntimeError, match="stale_quota_evidence"):
        controller.refresh()
    controller.account_read = lambda: {"rate_limits": quota(68), "observed_at": 1001}
    with pytest.raises(RuntimeError, match="invalid_observation"):
        controller.refresh()
    controller.close()
    ledger.close()


class TerminalRPC(FakeRPC):
    def __init__(self, ledger):
        super().__init__(ledger)
        self.sink = lambda event: None

    def turn(self, thread, prompt, **kwargs):
        self.calls += 1
        kwargs["permit"].consume(thread, kwargs["model"])
        kwargs["started"]("provider-turn")
        start = kwargs["start_usage"]
        end = {
            "inputTokens": start["inputTokens"] + 10,
            "cachedInputTokens": start["cachedInputTokens"],
            "outputTokens": start["outputTokens"],
            "reasoningOutputTokens": start["reasoningOutputTokens"],
            "totalTokens": start["totalTokens"] + 10,
        }
        terminal = kwargs["permit"].terminal_usage_qualified
        self.sink(
            {
                "kind": "turn_completed",
                "thread_id": thread,
                "provider_thread_id": thread,
                "turn_id": "provider-turn",
                "status": "completed",
                "usage_end": end,
                "usage_terminal": terminal,
            }
        )
        return {
            "text": "{}",
            "turn_id": "provider-turn",
            "usage": {"total": end},
            "usage_start": start,
            "usage_end": end,
            "usage_delta": None,
            "usage_terminal": terminal,
        }


def test_qualified_terminal_receipt_covers_only_its_dispatch(tmp_path):
    ledger = UsageLedger(tmp_path / "terminal.sqlite")
    rpc = TerminalRPC(ledger)
    controller = Controller(
        ledger,
        lambda: quota(),
        state=tmp_path,
        clock=lambda: 1000,
        preflight=lambda: {
            "resources": {"policy_id": "CEO-20261020-02"},
            "terminal_usage_qualified": True,
        },
    )
    client = MeteredCodex(
        rpc,
        controller,
        {
            "role": "worker",
            "team": "Experience",
            "task": "T",
            "episode_id": "terminal-episode",
            "authority": "CEO-20261020-02",
        },
    )
    thread = client.start("gpt-6-luna", "bounded")
    result = client.turn(
        thread,
        "bounded",
        dispatch_id="terminal-1",
        model="gpt-6-luna",
        estimate={"tokens": 20, "wpu": 1},
        tool=lambda _: None,
        deadline_seconds=30,
    )
    assert result["usage_terminal"] is True
    assert ledger.dispatch("terminal-1")["status"] == "covered"
    assert ledger.summary()["reservations"] == []
    assert ledger.summary()["incremental"]["total_tokens"] == 10
    controller.close()
    ledger.close()


def test_progress_usage_remains_a_measured_lower_bound_after_turn_failure(tmp_path):
    class ProgressFailRPC(FakeRPC):
        def __init__(self, ledger):
            super().__init__(ledger)
            self.sink = lambda event: None

        def turn(self, thread, prompt, **kwargs):
            kwargs["permit"].consume(thread, kwargs["model"])
            kwargs["started"]("provider-turn")
            snapshot = {
                "inputTokens": 8,
                "cachedInputTokens": 2,
                "outputTokens": 2,
                "reasoningOutputTokens": 1,
                "totalTokens": 10,
            }
            event = {
                "kind": "token_usage",
                "thread_id": thread,
                "turn_id": "provider-turn",
                "usage": {"total": snapshot},
            }
            self.sink(event)
            self.sink(event)
            raise RuntimeError("provider_failed_after_usage")

    ledger = UsageLedger(tmp_path / "progress.sqlite")
    rpc = ProgressFailRPC(ledger)
    controller = Controller(ledger, lambda: quota(), state=tmp_path, clock=lambda: 1000)
    client = MeteredCodex(
        rpc,
        controller,
        {
            "role": "worker",
            "team": "Experience",
            "task": "T",
            "episode_id": "progress-episode",
            "authority": "CEO-20261020-02",
        },
    )
    thread = client.start("gpt-6-luna", "bounded")
    with pytest.raises(RuntimeError, match="provider_failed_after_usage"):
        client.turn(
            thread,
            "bounded",
            dispatch_id="progress-failure",
            model="gpt-6-luna",
            estimate={"tokens": 20, "wpu": 1},
            tool=lambda _: None,
            deadline_seconds=30,
        )
    summary = ledger.summary()
    assert summary["incremental"]["total_tokens"] == 10
    assert summary["by_model"]["unclassified"]["total_tokens"] == 10
    assert any(row.get("dispatch_id") == "progress-failure" for row in summary["reservations"])
    controller.close()
    ledger.close()


def test_monitor_refresh_is_independent_and_cancels_when_quota_read_fails(tmp_path):
    import threading

    ledger = UsageLedger(tmp_path / "monitor.sqlite")
    reads = []

    def account_read():
        reads.append(1)
        if len(reads) > 1:
            raise RuntimeError("quota_read_failed")
        return quota()

    controller = Controller(ledger, account_read, state=tmp_path, clock=lambda: 1000)
    controller._poll_seconds = 0.01
    controller.refresh()
    cancel = threading.Event()
    controller._poll_seconds = 0.01
    stopped = controller.monitor(1100, cancel)
    assert cancel.wait(1)
    assert len(reads) >= 2
    stopped.set()
    controller.close()
    ledger.close()


def test_controller_external_import_is_allowlisted_and_does_not_reserve(tmp_path):
    ledger, _, controller, _ = make(tmp_path)
    batch = {
        "schema": "brain-org-usage-import-v1",
        "streams": [{"identity": "observed", "epoch": "e", "baseline": None, "intervals": []}],
        "unresolved": [{"identity": "observed", "reason": "baseline_unknown", "evidence": {}}],
    }
    digest = ledger.external_batch_hash(batch)
    result = controller.import_external(batch, "codex-rollout-intervals-v1", digest)
    assert result["unresolved"] == 1
    assert ledger.summary()["reservations"] == []
    with pytest.raises(ValueError, match="external_collector_not_allowed"):
        controller.import_external(batch, "unreviewed-source", digest)
    controller.close()
    ledger.close()


def test_stop_does_not_interrupt_an_already_authorized_turn(tmp_path):
    import threading

    ledger, _, controller, _ = make(tmp_path)
    controller._poll_seconds = 0.01
    cancel = threading.Event()
    (tmp_path / "STOP").touch()
    stopped = controller.monitor(1100, cancel)
    assert not cancel.wait(0.05)
    stopped.set()
    controller.close()
    ledger.close()


def test_dispatch_slots_are_atomic_and_uncertainty_retains_reservations(tmp_path):
    ledger, _, controller, client = make(tmp_path)
    for index in range(2):
        controller.admit(
            f"d{index}",
            f"thread-{index}",
            None,
            "gpt-6-luna",
            {"tokens": 20, "wpu": 1},
            1100,
            {"role": "worker", "team": "Experience"},
        )
    with pytest.raises(ValueError, match="active_model_limit"):
        controller.admit(
            "d2",
            "thread-2",
            None,
            "gpt-6-luna",
            {"tokens": 20, "wpu": 1},
            1100,
            {"role": "worker", "team": "Experience"},
        )
    assert len(ledger.summary()["reservations"]) == 2
    controller.close()
    ledger.close()
