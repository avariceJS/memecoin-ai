from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tools import brain_senses_gecko_new_pool_discovery_runtime_v2 as runtime
from tools import brain_senses_gecko_new_pool_discovery_v1 as discovery
from tools import brain_senses_gecko_new_pool_enrollment_v1 as enrollment

NS = 1_000_000_000
START = 1_800_000_000 * NS
SOURCE_SHA = "a" * 64
RELEASE_SHA = "b" * 64


def _b58(raw: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    zeros = len(raw) - len(raw.lstrip(b"\0"))
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, rem = divmod(number, 58)
        encoded = alphabet[rem] + encoded
    return "1" * zeros + (encoded or ("" if zeros else "1"))


POOL_A = _b58(bytes.fromhex("01" * 32))
POOL_B = _b58(bytes.fromhex("02" * 32))
TOKEN_A = _b58(bytes.fromhex("03" * 32))
TOKEN_B = _b58(bytes.fromhex("04" * 32))
TOKEN_C = _b58(bytes.fromhex("05" * 32))


def _pool(pool: str, *, base: str = TOKEN_A, created_at="2026-10-13T12:00:00Z"):
    return {
        "id": f"solana_{pool}",
        "type": "pool",
        "attributes": {
            "address": pool,
            "pool_created_at": created_at,
            "reserve_in_usd": "900000",
            "volume_usd": {"h24": "500000"},
        },
        "relationships": {
            "base_token": {"data": {"id": f"solana_{base}", "type": "token"}},
            "quote_token": {"data": {"id": f"solana_{TOKEN_B}", "type": "token"}},
            "dex": {"data": {"id": "pump-swap", "type": "dex"}},
        },
    }


def _raw(rows=()):
    return json.dumps({"data": list(rows), "included": []}, separators=(",", ":")).encode()


def _fixture(*, scan_index=0, sample=(0, 1)):
    policy = discovery.compile_policy(
        first_scan_ns=START,
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=sample[0],
        sample_denominator=sample[1],
        source_spec_sha256=SOURCE_SHA,
        max_scan_count=864,
    )
    epoch = runtime.epoch_envelope(
        release_id="1" * 32,
        lease_id="2" * 32,
        root_id="3" * 32,
        release_sha256=RELEASE_SHA,
        source_sha256=SOURCE_SHA,
        policy=policy,
        epoch_index=0,
        epoch_start_ns=START,
        previous_epoch_sha256=None,
        lifetime_requests_before=0,
        lifetime_physical_before=0,
        prior_allocated_physical=0,
        prior_pending_physical=0,
    )
    plan = runtime.compile_epoch_scan_plan(
        policy=policy,
        epoch=epoch,
        scan_index=scan_index,
        scheduled_ns=START + scan_index * 300 * NS,
    )
    epoch_history = [{"policy": policy, "epoch": epoch, "seal": None}]
    return policy, epoch, plan, epoch_history


def _scan_item(policy, epoch, plan, rows_by_page, *, scan_index=0, availability_by_page=None):
    availability_by_page = availability_by_page or {}
    pages = []
    raw_by_request_id = {}
    for page_slot in plan["page_slots"]:
        page = page_slot["page"]
        raw = _raw(rows_by_page.get(page, []))
        request_started = page_slot["planned_dispatch_ns"] + NS
        receipt_ns = request_started + 2 * NS
        available_ns = availability_by_page.get(page, receipt_ns + NS)
        pages.append(
            {
                "page": page,
                **runtime.make_page_record(
                    request_id=page_slot["request_id"],
                    status="received",
                    request_started_ns=request_started,
                    receipt_ns=receipt_ns,
                    durable_available_ns=available_ns,
                    raw_body=raw,
                    http_status=200,
                    body_truncated=False,
                ),
            }
        )
        raw_by_request_id[page_slot["request_id"]] = raw
    return {
        "kind": "plan",
        "epoch_index": epoch["epoch_index"],
        "scan_index": scan_index,
        "plan": plan,
        "page_records": pages,
        "raw_by_request_id": raw_by_request_id,
    }


def _replay(timeline, history):
    return enrollment.replay_enrollment_campaign(
        epoch_history=history,
        timeline=timeline,
    )


def test_replay_enrolls_first_durable_identity_with_roles_unassigned_and_no_followup():
    policy, epoch, plan, history = _fixture()
    item = _scan_item(
        policy,
        epoch,
        plan,
        {1: [_pool(POOL_A), _pool(POOL_A)], 2: [_pool(POOL_B)]},
        availability_by_page={1: START + 40 * NS, 2: START + 30 * NS},
    )
    result = _replay([item], history)
    assert result["denominators"]["planned_page_slots"] == 3
    assert result["denominators"]["unique_provider_discovered_pool_ids"] == 2
    assert result["page_outcomes"]["within_page_duplicate_appearances"] == 1
    by_pool = {row["pool_id"]: row for row in result["enrollments"]}
    assert by_pool[f"solana_{POOL_A}"]["role"] == "unassigned_observation"
    assert by_pool[f"solana_{POOL_A}"]["first_durable_available_ns"] == START + 40 * NS
    assert by_pool[f"solana_{POOL_B}"]["first_durable_available_ns"] == START + 30 * NS
    assert all(row["protected_exclusion_state"] == "not_checked" for row in result["enrollments"])
    assert result["claims"]["price_followup_authorized"] is False
    assert result["claims"]["population_kind"] == "provider_discovered_diagnostic_candidates"


def test_page_permutation_keeps_the_same_canonical_projection():
    policy, epoch, plan, history = _fixture()
    item = _scan_item(policy, epoch, plan, {1: [_pool(POOL_A)], 2: [_pool(POOL_B)]})
    permuted = dict(item)
    permuted["page_records"] = list(reversed(item["page_records"]))
    first = _replay([item], history)
    second = _replay([permuted], history)
    assert first == second


@given(st.permutations((0, 1, 2)))
def test_any_page_record_order_preserves_the_same_projection(order):
    policy, epoch, plan, history = _fixture()
    item = _scan_item(policy, epoch, plan, {1: [_pool(POOL_A)], 3: [_pool(POOL_B)]})
    permuted = dict(item)
    permuted["page_records"] = [item["page_records"][index] for index in order]
    assert _replay([permuted], history) == _replay([item], history)


def test_explicit_gap_has_no_pool_selection_and_cannot_replace_a_plan_silently():
    _, _, _, history = _fixture()
    scheduled = START
    gap = enrollment._gap_event(
        epoch_index=0, scan_index=0, scheduled_ns=scheduled, reason="capacity"
    )
    result = _replay([{"kind": "gap", "epoch_index": 0, "scan_index": 0, "gap": gap}], history)
    assert result["denominators"] == {
        "scheduled_scan_slots": 1,
        "planned_scans": 0,
        "gap_scans": 1,
        "planned_page_slots": 0,
        "pool_appearances": 0,
        "unique_provider_discovered_pool_ids": 0,
        "raw_bytes_replayed": 0,
    }
    tampered = dict(gap)
    tampered["selected_pool_ids"] = [f"solana_{POOL_A}"]
    with pytest.raises(enrollment.EnrollmentError, match="enrollment_gap_binding"):
        _replay([{"kind": "gap", "epoch_index": 0, "scan_index": 0, "gap": tampered}], history)


def test_missing_timeline_slot_and_reordered_timeline_fail_closed():
    policy, epoch, plan0, history = _fixture(scan_index=0)
    plan1 = runtime.compile_epoch_scan_plan(
        policy=policy, epoch=epoch, scan_index=1, scheduled_ns=START + 300 * NS
    )
    item0 = _scan_item(policy, epoch, plan0, {})
    item1 = _scan_item(policy, epoch, plan1, {}, scan_index=1)
    with pytest.raises(enrollment.EnrollmentError, match="enrollment_timeline_scan_order"):
        _replay([item1], history)
    gap0 = enrollment._gap_event(epoch_index=0, scan_index=0, scheduled_ns=START, reason="missed")
    with pytest.raises(enrollment.EnrollmentError, match="enrollment_timeline_scan_order"):
        _replay([item1, {"kind": "gap", "epoch_index": 0, "scan_index": 0, "gap": gap0}], history)
    assert _replay([item0, item1], history)["denominators"]["scheduled_scan_slots"] == 2


def test_conflicting_pool_identity_claims_are_quarantined_and_retained():
    policy, epoch, plan0, history = _fixture()
    plan1 = runtime.compile_epoch_scan_plan(
        policy=policy, epoch=epoch, scan_index=1, scheduled_ns=START + 300 * NS
    )
    first = _scan_item(policy, epoch, plan0, {1: [_pool(POOL_A, base=TOKEN_A)]})
    second = _scan_item(
        policy,
        epoch,
        plan1,
        {2: [_pool(POOL_A, base=TOKEN_C)]},
        scan_index=1,
    )
    result = _replay([first, second], history)
    assert len(result["enrollments"]) == 1
    row = result["enrollments"][0]
    assert row["identity_conflict"] is True
    assert row["population_admission"] == "quarantined_conflicting_identity_claims"
    assert row["conflict_observations"][0]["changed_fields"] == ["base_token_id_assertion"]


def test_raw_mutation_and_nonzero_followup_sampling_are_rejected():
    policy, epoch, plan, history = _fixture()
    item = _scan_item(policy, epoch, plan, {1: [_pool(POOL_A)]})
    mutated = dict(item)
    mutated["raw_by_request_id"] = dict(item["raw_by_request_id"])
    request_id, raw = next(iter(mutated["raw_by_request_id"].items()))
    mutated["raw_by_request_id"][request_id] = raw + b" "
    with pytest.raises(runtime.RuntimeErrorV2, match="runtime_raw_artifact_mismatch"):
        _replay([mutated], history)
    policy2, epoch2, plan2, history2 = _fixture(sample=(1, 1))
    item2 = _scan_item(policy2, epoch2, plan2, {1: [_pool(POOL_A)]})
    with pytest.raises(
        enrollment.EnrollmentError, match="enrollment_followup_sampling_must_be_zero"
    ):
        _replay([item2], history2)


def test_receipt_replay_rejects_role_or_denominator_mutation():
    policy, epoch, plan, history = _fixture()
    item = _scan_item(policy, epoch, plan, {1: [_pool(POOL_A)]})
    receipt = _replay([item], history)
    assert (
        enrollment.verify_enrollment_receipt(receipt, epoch_history=history, timeline=[item])
        == receipt
    )
    tampered = json.loads(json.dumps(receipt))
    tampered["enrollments"][0]["role"] = "development"
    with pytest.raises(enrollment.EnrollmentError, match="enrollment_receipt_replay_mismatch"):
        enrollment.verify_enrollment_receipt(tampered, epoch_history=history, timeline=[item])


def test_duplicate_page_slots_do_not_collapse_the_denominator():
    policy, epoch, plan, history = _fixture()
    item = _scan_item(policy, epoch, plan, {})
    item["page_records"].append(dict(item["page_records"][0]))
    with pytest.raises(discovery.DiscoveryError, match="discovery_page_slot_duplicate_or_unknown"):
        _replay([item], history)


def test_epoch_boundary_requires_a_full_predecessor_prefix_and_explicit_successor_slot():
    policy, epoch, _, history = _fixture()
    seal = runtime.make_epoch_seal(
        epoch=epoch,
        scan_count=864,
        unresolved_count=0,
        terminal_replay_sha256="e" * 64,
        coordinator_prefix_sha256="f" * 64,
        lifetime_requests_after=2_592,
        lifetime_physical_after=0,
        allocated_physical_after=0,
        pending_physical_after=0,
        transport_quiescent=True,
        sealed_at_ns=epoch["epoch_end_ns"],
    )
    next_policy = discovery.compile_policy(
        first_scan_ns=epoch["epoch_end_ns"],
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256=SOURCE_SHA,
        max_scan_count=864,
    )
    next_epoch = runtime.successor_epoch(
        previous_epoch=epoch,
        seal=seal,
        policy=policy,
        lease_id="4" * 32,
        root_id="5" * 32,
        predecessor_standby_sha256="6" * 64,
    )
    history = [
        {"policy": policy, "epoch": epoch, "seal": seal},
        {"policy": next_policy, "epoch": next_epoch, "seal": None},
    ]
    timeline = [
        {
            "kind": "gap",
            "epoch_index": 0,
            "scan_index": index,
            "gap": enrollment._gap_event(
                epoch_index=0,
                scan_index=index,
                scheduled_ns=START + index * 300 * NS,
                reason="not_collected",
            ),
        }
        for index in range(864)
    ]
    timeline.append(
        {
            "kind": "gap",
            "epoch_index": 1,
            "scan_index": 0,
            "gap": enrollment._gap_event(
                epoch_index=1,
                scan_index=0,
                scheduled_ns=next_policy["first_scan_ns"],
                reason="not_collected",
            ),
        }
    )
    receipt = _replay(timeline, history)
    assert receipt["denominators"]["scheduled_scan_slots"] == 865
    assert receipt["denominators"]["gap_scans"] == 865
