"""Independent resource-policy and adapter regression cases."""


def test_independent_quota_acceptance():
    import copy
    import json

    from engineering.quota_policy import choose_quota_capacity, reset_recovery_decision

    def limits(value=68, reset=10000):
        return {
            "ordinaryUsageAllowed": True,
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": {"usedPercent": value, "resetsAt": reset},
                    "secondary": None,
                    "rateLimitReachedType": None,
                    "spendControlReached": False,
                    "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                }
            },
        }

    def decide(value=68, **kw):
        return choose_quota_capacity(limits(value), observed_at=1000, now=1000, **kw)

    assert decide()["slots"] == 2
    for value in (67, 68, 69, 96):
        assert decide(value)["slots"] == 2
    assert decide(97)["slots"] == 1 and decide(98)["slots"] == 1
    assert decide(99)["slots"] == 0 and decide(100)["slots"] == 0
    assert decide(95)["poll_seconds"] == 60 and decide(94)["poll_seconds"] == 300
    assert decide(stop=True)["slots"] == 0
    assert decide(active_teams=1)["slots"] == 1
    assert decide(active_teams=2)["slots"] == 0
    assert decide(97, active_teams=1)["slots"] == 0
    assert choose_quota_capacity(limits(95), observed_at=939, now=1000)["slots"] == 0
    assert choose_quota_capacity(limits(), observed_at=699, now=1000)["slots"] == 0
    for value in (float("nan"), float("inf"), True, -1, 101):
        assert choose_quota_capacity(limits(value), observed_at=1000, now=1000)["slots"] == 0
    assert choose_quota_capacity(limits(), observed_at=1001, now=1000)["slots"] == 0
    x = limits()
    x["rateLimitsByLimitId"]["other"] = {
        **x["rateLimitsByLimitId"]["codex"],
        "primary": {"usedPercent": 99},
    }
    assert choose_quota_capacity(x, observed_at=1000, now=1000)["slots"] == 0
    for field, value in [("spendControlReached", True), ("rateLimitReachedType", "primary")]:
        x = limits()
        x["rateLimitsByLimitId"]["codex"][field] = value
        assert choose_quota_capacity(x, observed_at=1000, now=1000)["slots"] == 0
    x = limits()
    del x["rateLimitsByLimitId"]["codex"]["primary"]
    assert choose_quota_capacity(x, observed_at=1000, now=1000)["slots"] == 0
    x = limits()
    x["rateLimitsByLimitId"]["codex"]["credits"]["hasCredits"] = True
    assert choose_quota_capacity(x, observed_at=1000, now=1000)["slots"] == 0
    assert decide(98, reserve_points=2)["slots"] == 0
    assert (
        decide(98, reserve_points=2, reserve_evidence="measured in-flight exposure")["slots"] == 0
    )
    paused = {"observed_at": 800, "rate_limits": limits(99, 900)}
    readings = [
        {"observed_at": 940, "rate_limits": limits(1, 2000)},
        {"observed_at": 1000, "rate_limits": limits(1, 2000)},
    ]
    assert (
        reset_recovery_decision(paused, readings, now=1000, state_reconciled=True)["ready"] is True
    )
    assert reset_recovery_decision(paused, readings, now=1000)["ready"] is False
    assert (
        reset_recovery_decision(
            paused, readings, now=1000, state_reconciled=True, other_stop_reasons=["pilot STOP"]
        )["ready"]
        is False
    )
    short = copy.deepcopy(readings)
    short[0]["observed_at"] = 941
    assert reset_recovery_decision(paused, short, now=1000, state_reconciled=True)["ready"] is False
    unchanged = [
        {"observed_at": 940, "rate_limits": limits(99, 900)},
        {"observed_at": 1000, "rate_limits": limits(99, 900)},
    ]
    assert (
        reset_recovery_decision(paused, unchanged, now=1000, state_reconciled=True)["ready"]
        is False
    )
    print(json.dumps({"frozen_quota_acceptance": "passed"}))
    # Independent negative cases found after the initial primitive proposal.
    for field in ("secondary", "credits", "rateLimitReachedType", "spendControlReached"):
        x = limits()
        del x["rateLimitsByLimitId"]["codex"][field]
        assert choose_quota_capacity(x, observed_at=1000, now=1000)["slots"] == 0, field
    # Restoration within the productive reduced window is authorized.
    reduced = [
        {"observed_at": 940, "rate_limits": limits(97, 2000)},
        {"observed_at": 1000, "rate_limits": limits(98, 2000)},
    ]
    assert (
        reset_recovery_decision(paused, reduced, now=1000, state_reconciled=True)["ready"] is True
    )
    stale_final = [
        {"observed_at": 930, "rate_limits": limits(1, 2000)},
        {"observed_at": 990, "rate_limits": limits(1, 2000)},
    ]
    assert (
        reset_recovery_decision(paused, stale_final, now=1291, state_reconciled=True)["ready"]
        is False
    )


def test_independent_adapter_acceptance():
    import json
    import queue
    from unittest.mock import Mock

    from engineering.codex_rpc import Codex, counter_delta

    def count(n):
        return {
            "inputTokens": n,
            "cachedInputTokens": n // 2,
            "outputTokens": 10,
            "reasoningOutputTokens": 2,
            "totalTokens": n + 10,
        }

    assert counter_delta(count(200000), count(200100))["totalTokens"] == 100
    assert counter_delta(None, count(20)) is None
    assert counter_delta(count(20), count(10)) is None
    bad = count(20)
    bad["cachedInputTokens"] = True
    assert counter_delta(count(0), bad) is None
    c = Codex.__new__(Codex)
    c.call = Mock(return_value={"turn": {"id": "t"}})
    c.sink = Mock()
    c.messages = queue.Queue()
    c.notifications = [
        {
            "method": "thread/tokenUsage/updated",
            "params": {"threadId": "s", "tokenUsage": {"total": count(220000)}},
        },
        {"method": "turn/completed", "params": {"turn": {"id": "t", "status": "completed"}}},
    ]
    x = c.turn("s", "bounded", model="gpt-6-luna", tool=Mock(), start_usage=count(200000))
    assert x["usage_delta"]["totalTokens"] == 20000
    assert x["usage_start"] == count(200000)
    print(json.dumps({"frozen_adapter_acceptance": "passed"}))


def test_independent_ledger_acceptance():
    import json
    import tempfile
    from pathlib import Path

    from engineering.usage_ledger import UsageLedger

    def count(n):
        return dict(
            input_tokens=n,
            cached_input_tokens=n // 2,
            output_tokens=n // 10,
            reasoning_output_tokens=0,
            total_tokens=n + n // 10,
        )

    def rejects(fn):
        try:
            fn()
        except (ValueError, TypeError, KeyError):
            return
        raise AssertionError("invalid accounting was accepted")

    with tempfile.TemporaryDirectory() as t:
        path = Path(t) / "usage.sqlite"
        x = UsageLedger(path)
        x.register("sol", "e1", "gpt-6.1-sol", count(100), evidence="T0 counter witness")
        kw = dict(source="rollout", sequence=2, observed_at=10, event_at=9)
        x.observe("sol", "e1", "s2", count(200), **kw)
        x.observe("sol", "e1", "s2", count(200), **kw)
        x.observe(
            "sol",
            "e1",
            "s2-overlap",
            count(200),
            source="notification",
            sequence=2,
            observed_at=11,
            event_at=9,
        )
        x.observe(
            "sol",
            "e1",
            "s1-late",
            count(150),
            source="rollout",
            sequence=1,
            observed_at=12,
            event_at=5,
        )
        assert x.summary()["incremental"]["total_tokens"] == 110
        x.correct(
            "s2", "s2-correction", count(210), reason="late provider correction", source="provider"
        )
        # Disagreeing overlapping sources remain unresolved until both receipts are corrected.
        assert x.summary()["unresolved"]
        x.correct(
            "s2-overlap",
            "s2-overlap-correction",
            count(210),
            reason="same canonical position corrected",
            source="provider",
        )
        assert x.summary()["incremental"]["total_tokens"] == 121
        rejects(lambda: x.observe("sol", "e1", "s2", count(220), **kw))
        rejects(
            lambda: x.observe(
                "sol",
                "e1",
                "reset-without-epoch",
                count(10),
                source="rollout",
                sequence=3,
                observed_at=13,
            )
        )
        x.register(
            "luna",
            "e1",
            "gpt-6-luna",
            None,
            evidence="new root after T0 verified",
            started_after_T0=True,
        )
        x.observe("luna", "e1", "l1", count(50), source="rollout", sequence=1, observed_at=12)
        x.register("unknown", "e1", "gpt-6-astra", None, evidence="history baseline unavailable")
        x.observe("unknown", "e1", "u1", count(1000), source="rollout", sequence=1, observed_at=12)
        assert x.summary()["incremental"]["total_tokens"] == 176
        assert x.summary()["unresolved"]
        x.start_turn(
            "retry-1",
            "sol",
            "e1",
            count(210),
            reservation_tokens=1000,
            reservation_wpu=1,
            deadline_at=100,
            evidence="dispatch reservation",
        )
        assert x.summary()["reservations"]
        x.finish_turn("retry-1", count(250), receipt="failed receipt", failed=True)
        assert x.summary()["turns"]["retry-1"]["total_tokens"] == 44
        assert not x.summary()["reservations"]
        # Attribution is not an additional union charge.
        assert x.summary()["incremental"]["total_tokens"] == 176
        x.start_turn(
            "retry-2",
            "sol",
            "e1",
            count(250),
            reservation_tokens=1000,
            reservation_wpu=1,
            deadline_at=100,
            evidence="retry reservation",
        )
        assert x.summary()["reservations"]
        x.close()
        x = UsageLedger(path)
        assert x.summary()["incremental"]["total_tokens"] == 176 and x.summary()["reservations"]
        bad = count(100)
        bad["cached_input_tokens"] = True
        rejects(
            lambda: x.observe(
                "sol", "e1", "bad-counter", bad, source="rollout", sequence=4, observed_at=20
            )
        )
        x.register(
            "sol", "e2", "gpt-6.1-sol", count(0), evidence="counter reset independently verified"
        )
        x.observe("sol", "e2", "new-epoch", count(10), source="rollout", sequence=1, observed_at=20)
        assert x.summary()["incremental"]["total_tokens"] == 187
        x.close()
    print(json.dumps({"frozen_ledger_acceptance": "passed"}))
