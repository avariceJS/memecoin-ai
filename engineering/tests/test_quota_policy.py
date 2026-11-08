from datetime import UTC, datetime

from engineering.quota_policy import choose_quota_capacity, reset_recovery_decision

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def sample(pct=40, secondary=20, **extra):
    limit = {
        "primary": {"usedPercent": pct, "resetsAt": 1792874446},
        "secondary": None
        if secondary is None
        else {
            "usedPercent": secondary,
            "resetsAt": 1792874446,
        },
        "rateLimitReachedType": None,
        "spendControlReached": False,
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
    }
    return {
        "ordinaryUsageAllowed": True,
        "rateLimitsByLimitId": {"codex": limit},
        **extra,
    }


def choose(data, **kw):
    return choose_quota_capacity(data, observed_at=NOW, now=NOW, **kw)


def test_optional_null_and_unknown_fields():
    assert choose(sample(secondary=None, novel={"x": 1}))["slots"] == 2


def test_malformed_evidence_fails_closed():
    for value in (True, float("nan"), float("inf"), -1, 101):
        assert choose(sample(value))["slots"] == 0
    assert choose({"ordinaryUsageAllowed": True, "rateLimitsByLimitId": {}})["slots"] == 0
    assert (
        choose({"ordinaryUsageAllowed": True, "rateLimitsByLimitId": {"primary": None}})["slots"]
        == 0
    )


def test_each_bucket_counts_and_boundaries():
    assert choose(sample(20, 97))["slots"] == 1
    data = sample(20, None)
    data["rateLimitsByLimitId"]["other"] = {"primary": {"usedPercent": 99}, "secondary": None}
    assert choose(data)["slots"] == 0
    assert choose(sample(20, 99))["slots"] == 0
    assert choose(sample(97))["poll_seconds"] == 60
    assert choose(sample(95))["poll_seconds"] == 60


def test_stale_future_stop_and_active_capacity():
    assert choose(sample(), active_teams=1)["slots"] == 1
    assert choose(sample(97), active_teams=1)["slots"] == 0
    assert choose(sample(), active_teams=2)["slots"] == 0
    assert choose(sample(), stop=True)["slots"] == 0
    assert choose_quota_capacity(sample(), observed_at=NOW.timestamp() - 301, now=NOW)["slots"] == 0
    assert choose_quota_capacity(sample(), observed_at=NOW.timestamp() + 1, now=NOW)["slots"] == 0


def test_provider_spend_and_paid_credit_stops():
    assert choose(sample(spendControlReached=True))["slots"] == 0
    assert choose(sample(providerReached=True))["slots"] == 0
    data = sample()
    data["rateLimitsByLimitId"]["codex"]["rateLimitReachedType"] = "primary"
    assert choose(data)["slots"] == 0
    credit_cases = (
        {"hasCredits": True, "unlimited": False, "balance": "2"},
        {"hasCredits": False, "unlimited": True, "balance": "0"},
    )
    for credits in credit_cases:
        data = sample()
        data["rateLimitsByLimitId"]["codex"]["credits"] = credits
        assert choose(data)["slots"] == 0


def test_reserve_evidence():
    assert choose(sample(99), reserve_points=1)["slots"] == 0
    assert choose(sample(98), reserve_points=2)["slots"] == 0
    assert choose(sample(98), reserve_points=2, reserve_evidence={"ticket": "x"})["slots"] == 0


def test_recovery_needs_two_spaced_restored_readings_and_reconciliation():
    paused = {"observed_at": NOW, "rate_limits": sample(99)}
    a = {"observed_at": NOW, "rate_limits": sample(30)}
    b = {"observed_at": NOW.timestamp() + 60, "rate_limits": sample(25)}
    assert reset_recovery_decision(paused, [a, b], now=NOW.timestamp() + 60, state_reconciled=True)[
        "ready"
    ]
    assert not reset_recovery_decision(paused, [a, b], now=NOW.timestamp() + 60)["ready"]
    assert not reset_recovery_decision(
        paused,
        [a, {**b, "observed_at": NOW.timestamp() + 30}],
        now=NOW.timestamp() + 60,
        state_reconciled=True,
    )["ready"]


def test_recovery_never_infers_reset_from_flat_usage():
    paused = {"observed_at": NOW, "rate_limits": sample(30)}
    a = {"observed_at": NOW, "rate_limits": sample(30)}
    b = {"observed_at": NOW.timestamp() + 60, "rate_limits": sample(30)}
    assert not reset_recovery_decision(
        paused, [a, b], now=NOW.timestamp() + 60, state_reconciled=True
    )["ready"]


def test_recovery_accepts_historical_provider_denial_and_reached_bucket():
    denied = sample(99)
    denied["ordinaryUsageAllowed"] = False
    reached = sample(98)
    reached["rateLimitsByLimitId"]["codex"]["rateLimitReachedType"] = "primary"
    fresh_a = {"observed_at": NOW, "rate_limits": sample(30)}
    fresh_b = {"observed_at": NOW.timestamp() + 60, "rate_limits": sample(25)}
    for historical in (denied, reached):
        paused = {"observed_at": NOW, "rate_limits": historical}
        decision = reset_recovery_decision(
            paused,
            [fresh_a, fresh_b],
            now=NOW.timestamp() + 60,
            state_reconciled=True,
        )
        assert decision == {"ready": True, "reason": "capacity_restored"}
