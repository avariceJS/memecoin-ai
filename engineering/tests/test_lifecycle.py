from __future__ import annotations

import pytest

from engineering.lifecycle import LifecycleError, plan_lifecycle


def inputs(**overrides):
    body = {
        "desired_state": "enabled",
        "stop_reasons": (),
        "runtime_ready": True,
        "accounting_ready": True,
        "quota_ready": True,
        "process_state": "stopped",
        "process_owner": None,
        "lease_owner": None,
        "active_attempt_id": None,
        "recovery_open": False,
        "backoff_until": None,
        "now": 100.0,
        "launch_authorized": False,
        "request_owner": None,
        "request_epoch": None,
    }
    body.update(overrides)
    return body


def test_default_is_inert_without_explicit_launch_authority():
    plan = plan_lifecycle(**inputs())
    assert (plan.action, plan.reason) == ("hold", "launch_not_authorized")


def test_stop_usage_quota_and_restore_gates_fail_closed_in_order():
    cases = (
        ({"stop_reasons": ("founder_stop",)}, "stop_reason_active"),
        ({"accounting_ready": False}, "usage_settlement_unqualified"),
        ({"quota_ready": False}, "quota_not_ready"),
        ({"runtime_ready": False}, "runtime_not_qualified"),
    )
    for changed, reason in cases:
        plan = plan_lifecycle(**inputs(**changed))
        assert (plan.action, plan.reason) == ("hold", reason)


def test_active_process_requires_the_matching_durable_lease_owner():
    observed = plan_lifecycle(
        **inputs(
            process_state="alive",
            process_owner="owner-a",
            lease_owner="owner-a",
            active_attempt_id=7,
            launch_authorized=True,
        )
    )
    assert (observed.action, observed.reason) == ("observe", "active_lease_owner_confirmed")
    rejected = plan_lifecycle(
        **inputs(
            process_state="alive",
            process_owner="owner-a",
            lease_owner="owner-b",
            active_attempt_id=7,
            launch_authorized=True,
        )
    )
    assert (rejected.action, rejected.reason) == ("hold", "process_lease_owner_mismatch")


def test_unknown_or_unowned_process_never_becomes_a_launch_request():
    unknown = plan_lifecycle(**inputs(process_state="unknown", launch_authorized=True))
    orphan = plan_lifecycle(**inputs(process_state="alive", launch_authorized=True))
    assert (unknown.action, unknown.reason) == ("hold", "process_observation_unknown")
    assert (orphan.action, orphan.reason) == ("hold", "live_process_without_lease")


def test_stale_owner_without_active_attempt_blocks_launch():
    stale = plan_lifecycle(
        **inputs(
            lease_owner="old-owner",
            launch_authorized=True,
            request_owner="new-owner",
            request_epoch=4,
        )
    )
    assert (stale.action, stale.reason) == ("hold", "owner_without_active_attempt")


def test_crashed_attempt_requires_recovery_obligation_instead_of_restart():
    recovery_open = plan_lifecycle(
        **inputs(active_attempt_id=9, recovery_open=True, launch_authorized=True)
    )
    recovery_missing = plan_lifecycle(
        **inputs(active_attempt_id=9, recovery_open=False, launch_authorized=True)
    )
    assert (recovery_open.action, recovery_open.reason) == ("hold", "recovery_obligation_open")
    assert (recovery_missing.action, recovery_missing.reason) == ("hold", "recovery_required")


def test_backoff_is_deterministic_and_authorized_start_is_only_a_request():
    pending = plan_lifecycle(**inputs(backoff_until=120.0, launch_authorized=True))
    assert (pending.action, pending.reason, pending.retry_at) == ("wait", "backoff", 120.0)
    first = plan_lifecycle(
        **inputs(launch_authorized=True, request_owner="controller-a", request_epoch=3)
    )
    second = plan_lifecycle(
        **inputs(launch_authorized=True, request_owner="controller-a", request_epoch=3)
    )
    assert (first.action, first.reason) == ("launch_request", "all_gates_clear")
    assert (first.request_owner, first.request_epoch) == ("controller-a", 3)
    assert first.decision_sha256 == second.decision_sha256


def test_launch_request_requires_owner_and_epoch():
    plan = plan_lifecycle(**inputs(launch_authorized=True))
    assert (plan.action, plan.reason) == ("hold", "launch_request_identity_missing")


def test_type_confusion_and_malformed_observations_are_rejected():
    for changed, code in (
        ({"active_attempt_id": True}, "invalid_active_attempt_id"),
        ({"now": True}, "invalid_lifecycle_time"),
        ({"launch_authorized": 1}, "invalid_launch_authorized"),
        ({"stop_reasons": (None,)}, "invalid_stop_reasons"),
        ({"request_owner": " ", "request_epoch": 1}, "invalid_request_owner"),
        ({"request_owner": "controller", "request_epoch": True}, "invalid_request_epoch"),
        ({"now": 10**10000}, "invalid_lifecycle_time"),
    ):
        with pytest.raises(LifecycleError, match=code):
            plan_lifecycle(**inputs(**changed))
