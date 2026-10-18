from __future__ import annotations

from tools import brain_senses_external_orchestrator_v5 as runtime
from tools import brain_senses_external_rolling_policy_v5 as policy
from tools import brain_senses_external_schema_v5 as schema


def test_v5_policy_binds_hourly_forward_only_caps_and_shared_accounting_roots() -> None:
    contract = policy.policy_document()
    assert contract["schema"] == "brain-senses-external-rolling-policy-v5"
    assert contract["schema_version"] == 5
    assert contract["epoch_hours"] == 24
    assert contract["max_epoch_requests"] == 817
    assert contract["max_epoch_response_bytes"] == 4_000_000_000
    assert contract["max_epoch_physical_bytes"] == 4_000_000_000
    assert contract["max_rolling_requests"] == 25_000
    assert contract["max_rolling_modeled_credits"] == 250_000
    assert contract["max_rolling_response_bytes"] == 120_000_000_000
    assert contract["max_rolling_physical_bytes"] == 120_000_000_000
    assert contract["max_requests_per_second"] == 4
    assert contract["epoch_expiry"] is None
    assert contract["capacity_coordinator"] == {
        "schema": "brain-senses-capacity-coordinator-policy-v2",
        "version": 2,
        "service": "helius",
        "accounting_roots": [
            "services/external-rolling-v4",
            "services/external-rolling-v5",
        ],
        "shared_lock_scope": "reserve_and_settle_only",
    }
    assert contract["rolling_usage_seed"] == {
        "schema": "brain-senses-external-rolling-v5-cutover-usage",
        "schema_version": 1,
        "path": "services/external-rolling-v5/cutover-usage-v1.json",
        "required_before_dispatch": True,
        "archive_body_reads": False,
        "legacy_attribution": "possible_same_account",
        "provider_charge_status": "unknown_unverified",
    }
    assert schema.LOCK_FILE == ".external-rolling-v4.lock"


def test_v5_plan_has_one_new_anchor_and_no_historical_continuity() -> None:
    created_ns = 1_900_000_000_000_000_000
    anchor_ns = policy.next_fresh_anchor_ns(created_ns)
    bucket = schema.bucket_for(anchor_ns)
    epoch = schema.epoch_id(anchor_ns, "0123456789ab")
    plan = schema.build_plan(
        epoch=epoch,
        created_ns=created_ns,
        anchor_ns=anchor_ns,
        root_id="a" * 32,
        volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        cutover_usage_sha256="c" * 64,
        source_bindings={"tools/v5.py": "b" * 64},
    )
    assert plan["schema_version"] == 5
    assert plan["windows"][0]["kind"] == "anchor"
    assert plan["windows"][0]["bucket"] == bucket
    assert len(plan["windows"]) == 25
    assert plan["prior_interval"] == "unobserved_no_continuity_claim"
    assert plan["caps"]["epoch_expiry"] is None
    assert all(row["start_ns"] >= anchor_ns for row in plan["windows"])


def test_v5_sources_pin_versioned_service_and_shared_coordinator() -> None:
    bindings = runtime.source_bindings()
    assert "tools/brain_senses_external_orchestrator_v5.py" in bindings
    assert "tools/brain_senses_external_verify_v5.py" in bindings
    assert "tools/brain_senses_capacity_coordinator_v2.py" in bindings
    assert "tools/brain_senses_external_orchestrator_v4.py" in bindings
    assert "tools/brain_senses_external_cutover_usage_v5.py" in bindings
