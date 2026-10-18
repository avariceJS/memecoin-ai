from memecoin_continuous.source import NS, choose, outcome, reserve


def fixture():
    return dict(
        last_ns=0,
        bytes=0,
        attempts=0,
        failures=0,
        rate_events=[],
        cooldown_ns=0,
        pools={
            "p": dict(
                next_ns=0,
                status="active",
                streak=0,
                probes=0,
                probe_day=-1,
                cursor=None,
                history_done=False,
                history_pages=0,
            )
        },
    )


def test_live_priority_reserved_crash_and_history_no_repeat():
    s = fixture()
    u = [dict(pool="p")]
    now = 1000 * NS
    job = choose(s, u, now)
    assert job["lane"] == "prospective"
    s = reserve(s, job, now)
    assert s["attempts"] == 1 and s["bytes"] > 2 * 1024**2
    history = choose(s, u, now)
    assert history["lane"] == "historical"
    s = reserve(s, history, now)
    assert choose(s, u, now) is None
    assert choose(s, u, now + 301 * NS)["lane"] == "prospective"


def test_bounded_provider_cooldowns_auth_and_quota_stop():
    s = fixture()
    job = dict(pool="p", lane="prospective")
    now = 1000 * NS
    for i in range(3):
        s, stop = outcome(
            s,
            job,
            dict(http_status=429, retry_after_seconds=1200),
            [],
            "transport",
            now + i * NS,
            0,
        )
    assert stop == "three_429_in_day" and s["cooldown_ns"] >= now + 1200 * NS
    _, stop = outcome(fixture(), job, dict(http_status=403), [], "transport", now, 0)
    assert stop == "provider_auth"
    s = fixture()
    for i in range(6):
        s, stop = outcome(s, job, {}, [], "transport", now + i * NS, 0)
    assert stop == "six_consecutive_transport_failures"


def test_schema_is_local_and_two_fresh_probes_required():
    now = 1000 * NS
    s = fixture()
    job = dict(pool="p", lane="prospective")
    s, stop = outcome(s, job, {}, [], "invalid_OHLCV", now, 0)
    assert stop is None and s["pools"]["p"]["status"] == "quarantined"
    rows = [dict(open_s=700)]
    s, stop = outcome(s, job, {}, rows, "valid", now, 0)
    assert s["pools"]["p"]["status"] == "quarantined"
    s, stop = outcome(s, job, {}, rows, "valid", now, 0)
    assert s["pools"]["p"]["status"] == "active"
