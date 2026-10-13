from __future__ import annotations

import plistlib

import pytest

from tools import brain_senses_gecko_canary_watchdog_v1 as watchdog


def _proof() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    close = 1_800_000_000_000_000_000
    plan = {"sequence": 1, "selection": {"selected_pool_ids": [f"pool-{i}" for i in range(6)]}}
    ids = [f"request-{i}" for i in range(6)]
    state = {
        "intents": [{"request_id": rid, "request": {"plan_sequence": 1}} for rid in ids],
        "dispatches": [
            {"request_id": rid, "dispatch_ns": close + 61_000_000_000 + i * 12_000_000_000}
            for i, rid in enumerate(ids)
        ],
        "results": [{"request_id": rid, "status": "received", "bars": []} for rid in ids],
        "confirmations": [{"request_id": rid} for rid in ids],
        "skips": [],
    }
    verified = {
        "status": "verified_prefix",
        "unresolved_count": 0,
        "provider_io": False,
    }
    return plan, state, {"health": {"stop_latched": False}, "verified": verified, "close": close}


def test_canary_accepts_six_verified_spaced_dispatches() -> None:
    plan, state, context = _proof()
    result = watchdog.evaluate_bucket(
        plan=plan,
        state=state,
        verified=context["verified"],
        bucket_close_ns=context["close"],
        health=context["health"],
    )
    assert result["timing_gate"] == "passed"
    assert result["dispatch_count"] == 6
    assert result["eligible_new_open_count"] == 0
    assert result["economic_admission"] is False


@pytest.mark.parametrize("fault", ["dispatch", "skip", "stop", "spacing", "unresolved"])
def test_canary_fails_closed_for_incomplete_or_unsafe_bucket(fault: str) -> None:
    plan, state, context = _proof()
    verified = dict(context["verified"])
    health = dict(context["health"])
    if fault == "dispatch":
        state["dispatches"] = state["dispatches"][:-1]
    elif fault == "skip":
        state["skips"] = [{"plan_sequence": 1, "slot_index": 5}]
    elif fault == "stop":
        health["stop_latched"] = True
    elif fault == "spacing":
        state["dispatches"][1]["dispatch_ns"] = state["dispatches"][0]["dispatch_ns"]
    else:
        verified["unresolved_count"] = 1
    with pytest.raises(watchdog.WatchdogError):
        watchdog.evaluate_bucket(
            plan=plan,
            state=state,
            verified=verified,
            bucket_close_ns=context["close"],
            health=health,
        )


def test_watchdog_plist_has_finite_one_bucket_deadline_and_no_credentials() -> None:
    close = 1_800_000_000_000_000_000
    data = plistlib.loads(watchdog.render_launchd_plist("a" * 32, close))
    assert data["StartInterval"] == watchdog.WATCHDOG_INTERVAL_SECONDS
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    assert data["EnvironmentVariables"]["SENSES_CANARY_DEADLINE_NS"] == str(
        watchdog.deadline_for(close)
    )
    assert "api_key" not in repr(data).lower()
