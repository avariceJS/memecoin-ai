from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO))

import test_brain_senses_gecko_capture_only_v2_verify as old_verify_fixture  # noqa: E402
import test_brain_senses_gecko_scout_chain_v2 as chain_fixture  # noqa: E402

from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools import brain_senses_gecko_scout_chain_v2 as chain_v2  # noqa: E402
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v2 as inventory_v2  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v4 as inventory_v4  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402


@pytest.fixture
def writer(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(fd)
    try:
        yield writer
    finally:
        writer.close()
        os.close(fd)


def _coordinator_genesis() -> tuple[bytes, list[dict[str, Any]]]:
    event = capacity._event(
        seq=0,
        kind="genesis",
        at_ns=1,
        previous=None,
        fields={"policy_sha256": "a" * 64},
    )
    raw = capacity.canonical(event)
    return raw, [event]


def _unresolved_intent(
    writer: DescriptorWriter,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], bytes]:
    inputs = chain_fixture._certificate_inputs(writer)
    policy = chain_fixture._policy()
    certificate = chain_v2.build_cutover_certificate(**inputs)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer,
        root=chain_fixture.ROOT,
        policy=policy,
        certificate=certificate,
        certificate_inputs=inputs,
    )
    journal.initialize()
    close = journal.records()[-1]["next_bucket_close_ns"] + scout.BUCKET_NS
    journal.ensure_plan_for_current_bucket(
        now_ns=close + scout.DISPATCH_EARLIEST_NS,
        bucket_close_ns=close,
    )
    plan = journal.records()[-1]
    request = inventory_v4.capture_verify_v3.expected_request(
        policy, journal.binding["binding_sha256"], plan, 0
    )
    decision_ns = close + scout.DISPATCH_EARLIEST_NS
    decision_monotonic_ns = 100 * scout.NS
    clock, _ = old_verify_fixture._clock(decision_ns, decision_monotonic_ns)
    intent = inventory_v4.capture_verify_v3.build_intent(
        root=chain_fixture.ROOT,
        policy=policy,
        binding=journal.binding,
        plan_record=plan,
        slot_index=0,
        decision_ns=decision_ns,
        decision_monotonic_ns=decision_monotonic_ns,
        clock_record=clock,
        admission=old_verify_fixture._admission(request["request_id"], decision_ns),
    )
    writer.mkdir(f"{chain_fixture.ROOT}/intents")
    writer.create(
        f"{chain_fixture.ROOT}/intents/{intent['request_id']}.json",
        chain_fixture.handoff.canonical_json(intent),
    )
    genesis = capacity._event(
        seq=0,
        kind="genesis",
        at_ns=1,
        previous=None,
        fields={"policy_sha256": "a" * 64},
    )
    intent_bytes = chain_fixture.handoff.canonical_json(intent)
    reservation_fields = {
        "reservation_id": f"reservation-{intent['request_id']}",
        "service": "gecko",
        "request_id": intent["request_id"],
        "intent_path": f"{chain_fixture.ROOT}/intents/{intent['request_id']}.json",
        "outcome_path": f"{chain_fixture.ROOT}/results/{intent['request_id']}.json",
        "intent_sha256": inventory_v4.capture_verify_v3.sha256(intent_bytes),
        "reserved_bytes": scout.MAX_RESPONSE_BYTES,
        "intent_bytes_sha256": inventory_v4.capture_verify_v3.sha256(intent_bytes),
        "allocated_before_bytes": 0,
    }
    reservation = capacity._event(
        seq=1,
        kind="reserved",
        at_ns=decision_ns,
        previous=genesis["event_sha256"],
        fields=reservation_fields,
    )
    events = [genesis, reservation]
    return (
        journal.binding,
        intent,
        events,
        b"".join(capacity.canonical(event) for event in events),
    )


def test_inventory_v4_replays_v2_plan_only_prefix_as_zero_usage(
    writer: DescriptorWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = chain_fixture._certificate_inputs(writer)
    policy = chain_fixture._policy()
    certificate = chain_v2.build_cutover_certificate(**inputs)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer,
        root=chain_fixture.ROOT,
        policy=policy,
        certificate=certificate,
        certificate_inputs=inputs,
    )
    journal.initialize()
    close = journal.records()[-1]["next_bucket_close_ns"] + scout.BUCKET_NS
    journal.ensure_plan_for_current_bucket(
        now_ns=close + scout.DISPATCH_EARLIEST_NS,
        bucket_close_ns=close,
    )
    monkeypatch.setattr(inventory_v4, "_read_coordinator", lambda *_args: _coordinator_genesis())

    replay = inventory_v4._verify_chain_v2_root(
        writer,
        root=chain_fixture.ROOT,
        context={"certificate_inputs": inputs, "observed_now_ns": 1},
    )

    assert replay["status"] == "verified_prefix"
    assert replay["cursor"] == 18
    assert replay["intent_count"] == 0
    assert replay["usage_attempts"] == []
    assert replay["dispatch_blocked"] is False
    assert replay["unaccounted_slots"]
    assert replay["expired_unaccounted_slots"] == []
    assert replay["provider_io"] is False
    assert replay["dispatch_authorized"] is False


def test_inventory_v4_rejects_unrecognized_context_shape(writer: DescriptorWriter) -> None:
    with pytest.raises(inventory_v4.InventoryV4Error, match="successor_v2_chain_context_shape"):
        inventory_v4._verify_chain_v2_root(
            writer,
            root=chain_fixture.ROOT,
            context={"certificate_inputs": {}, "observed_now_ns": 1, "caller_attempts": []},
        )


def test_inventory_v4_keeps_v2_root_in_full_census_and_snapshot(
    writer: DescriptorWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = chain_fixture._certificate_inputs(writer)
    policy = chain_fixture._policy()
    certificate = chain_v2.build_cutover_certificate(**inputs)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer,
        root=chain_fixture.ROOT,
        policy=policy,
        certificate=certificate,
        certificate_inputs=inputs,
    )
    journal.initialize()
    close = journal.records()[-1]["next_bucket_close_ns"] + scout.BUCKET_NS
    journal.ensure_plan_for_current_bucket(
        now_ns=close + scout.DISPATCH_EARLIEST_NS,
        bucket_close_ns=close,
    )
    old_root = chain_fixture.handoff.OLD_ROOT
    writer.mkdir(old_root)
    writer.create(f"{old_root}/.runner.lock", b"old lock\n")
    predecessor_attempts = inputs["predecessor_attempts"]
    predecessor = shared_usage.make_verified_root_snapshot(
        root=old_root,
        status="verified_prefix",
        root_inventory_sha256="1" * 64,
        source_prefix_sha256=inputs["expected_handoff_genesis"]["predecessor_file_prefix_sha256"],
        attempts=predecessor_attempts,
    )
    full_manifest = {"root_paths": [old_root, chain_fixture.ROOT]}
    filtered_manifest = {"root_paths": [old_root]}
    full_hash = inventory_v2.sha256(inventory_v2.canonical_json(full_manifest))
    filtered_hash = inventory_v2.sha256(inventory_v2.canonical_json(filtered_manifest))
    monkeypatch.setattr(
        inventory_v2,
        "_discover_roots",
        lambda _writer: ([old_root, chain_fixture.ROOT], full_manifest, full_hash),
    )

    def base_build(proxy: Any) -> dict[str, Any]:
        assert chain_fixture.ROOT.rsplit("/", 1)[-1] not in proxy.list_names(
            inventory_v4.CAPTURE_V2_PARENT
        )
        return {
            "root_inventory": filtered_manifest,
            "root_inventory_sha256": filtered_hash,
            "snapshots": [predecessor],
            "provider_io": False,
        }

    monkeypatch.setattr(inventory_v2, "build_usage_inventory", base_build)
    monkeypatch.setattr(inventory_v4, "_read_coordinator", lambda *_args: _coordinator_genesis())
    result = inventory_v4.build_usage_inventory(
        writer,
        required_successor_root=chain_fixture.ROOT,
        expected_handoffs={},
        expected_v2_chains={
            chain_fixture.ROOT: {
                "certificate_inputs": inputs,
                "observed_now_ns": 1,
            }
        },
        observed_now_ns=1,
    )

    assert result["root_inventory"] == full_manifest
    assert result["root_inventory_sha256"] == full_hash
    successor = next(row for row in result["snapshots"] if row["root"] == chain_fixture.ROOT)
    assert successor["status"] == "verified_none"
    assert successor["attempts"] == []
    assert result["dispatch_authorized"] is False


def test_inventory_v4_charges_unresolved_intent_at_maximum(
    writer: DescriptorWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding, intent, coordinator_events, _coordinator_raw = _unresolved_intent(writer)
    old_root = chain_fixture.handoff.OLD_ROOT
    writer.mkdir(old_root)
    writer.create(f"{old_root}/.runner.lock", b"old lock\n")
    inputs = chain_fixture._certificate_inputs(writer)
    predecessor_attempts = inputs["predecessor_attempts"]
    predecessor = shared_usage.make_verified_root_snapshot(
        root=old_root,
        status="verified_prefix",
        root_inventory_sha256="1" * 64,
        source_prefix_sha256=inputs["expected_handoff_genesis"]["predecessor_file_prefix_sha256"],
        attempts=predecessor_attempts,
    )
    full_manifest = {"root_paths": [old_root, chain_fixture.ROOT]}
    filtered_manifest = {"root_paths": [old_root]}
    full_hash = inventory_v2.sha256(inventory_v2.canonical_json(full_manifest))
    filtered_hash = inventory_v2.sha256(inventory_v2.canonical_json(filtered_manifest))
    monkeypatch.setattr(
        inventory_v2,
        "_discover_roots",
        lambda _writer: ([old_root, chain_fixture.ROOT], full_manifest, full_hash),
    )

    def base_build(_proxy: Any) -> dict[str, Any]:
        return {
            "root_inventory": filtered_manifest,
            "root_inventory_sha256": filtered_hash,
            "snapshots": [predecessor],
            "provider_io": False,
        }

    monkeypatch.setattr(inventory_v2, "build_usage_inventory", base_build)
    raw = b"".join(capacity.canonical(event) for event in coordinator_events)
    monkeypatch.setattr(
        inventory_v4,
        "_read_coordinator",
        lambda *_args: (raw, coordinator_events),
    )
    result = inventory_v4.build_usage_inventory(
        writer,
        required_successor_root=chain_fixture.ROOT,
        expected_handoffs={},
        expected_v2_chains={
            chain_fixture.ROOT: {
                "certificate_inputs": chain_fixture._certificate_inputs(writer),
                "observed_now_ns": 1,
            }
        },
        observed_now_ns=1,
    )
    snapshot = next(row for row in result["snapshots"] if row["root"] == chain_fixture.ROOT)
    assert snapshot["status"] == "verified_prefix"
    assert len(snapshot["attempts"]) == 1
    attempt = snapshot["attempts"][0]
    assert attempt["request_id"] == intent["request_id"]
    assert attempt["maximum_response_bytes"] == scout.MAX_RESPONSE_BYTES
    assert attempt["response_bytes"] is None
    assert attempt["response_sha256"] is None
