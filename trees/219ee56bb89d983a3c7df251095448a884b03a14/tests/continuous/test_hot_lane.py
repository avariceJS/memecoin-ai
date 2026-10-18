import copy

import pytest

from memecoin_continuous.hot import NS, authorization, choose
from memecoin_continuous.source import reserve
from memecoin_runner.ledger import publish


def fixture():
    names = list("abcdef")
    s = dict(
        last_ns=0,
        bytes=0,
        attempts=0,
        pools={
            p: dict(
                next_ns=0,
                status="active",
                probes=0,
                probe_day=-1,
                history_pages=0,
                history_done=False,
                cursor=100,
            )
            for p in names + ["cold"]
        },
    )
    c = dict(hot_pools=names, hot_interval_s=120, cold_interval_s=1800, max_background_requests=10)
    return s, c, [dict(pool=p) for p in s["pools"]]


def test_multiple_cycles_live_first_and_background_progress():
    s, c, u = fixture()
    roles = []
    starts = {p: [] for p in c["hot_pools"]}
    for seconds in range(1000, 1600, 12):
        job = choose(s, u, seconds * NS, c)
        if job:
            roles.append(job["role"])
            if job["role"] == "hot":
                starts[job["pool"]].append(seconds)
            s = reserve(s, job, seconds * NS)
    assert "cold" in roles and "history" in roles
    assert all(len(ts) >= 4 for ts in starts.values())
    assert all(b - a <= 132 for ts in starts.values() for a, b in zip(ts, ts[1:], strict=False))
    # An unfinished reserved history job never retries on restart.
    recovered = copy.deepcopy(s)
    for q in recovered["pools"].values():
        if q["history_pages"]:
            assert q["history_done"]


def test_background_cannot_cross_hot_deadline_or_budget():
    s, c, u = fixture()
    for q in s["pools"].values():
        q["next_ns"] = 1040 * NS
    assert choose(s, u, 1006 * NS, c) is None
    s["pools"]["a"]["next_ns"] = 1000 * NS
    assert choose(s, u, 1006 * NS, c)["role"] == "hot"
    for q in s["pools"].values():
        q["next_ns"] = 2000 * NS
    s["background_attempts"] = 10
    assert choose(s, u, 1006 * NS, c) is None


def test_qualification_lease_and_exact_activation(tmp_path):
    c = dict(hot_pools=["p"], qualification_expires_ns=100 * NS, qualification_max_requests=120)
    authorization(tmp_path, c, 99 * NS, 119)
    with pytest.raises(ValueError, match="lease_exhausted"):
        authorization(tmp_path, c, 100 * NS, 0)
    with pytest.raises(ValueError, match="lease_exhausted"):
        authorization(tmp_path, c, 99 * NS, 120)
    publish(tmp_path / "config.json", c)
    publish(tmp_path / "deployment.json", dict(pin="pin"))
    publish(tmp_path / "qualification.json", dict(approved=False))
    publish(
        tmp_path / "activation.json",
        dict(config_sha="wrong", runtime_pin="pin", qualification_sha="wrong"),
    )
    with pytest.raises(ValueError, match="activation_binding"):
        authorization(tmp_path, c, 99 * NS, 1)
