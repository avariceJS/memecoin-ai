import copy
import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools.brain_senses_gecko_new_pool_discovery_v1 import (
    DiscoveryError,
    canonical,
    compile_policy,
    compile_scan_plan,
    parse_page,
    replay_campaign,
    replay_scan,
    sha256,
)

NS = 1_000_000_000
SPEC_SHA = "a" * 64


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
POOL_C = _b58(bytes.fromhex("03" * 32))
TOKEN_A = _b58(bytes.fromhex("04" * 32))
TOKEN_B = _b58(bytes.fromhex("05" * 32))


def _pool(address: str, *, created_at="2026-10-13T12:00:00Z", liquidity="0"):
    return {
        "id": f"solana_{address}",
        "type": "pool",
        "attributes": {
            "address": address,
            "pool_created_at": created_at,
            "reserve_in_usd": liquidity,
            "volume_usd": {"h24": "0"},
        },
        "relationships": {
            "base_token": {"data": {"id": f"solana_{TOKEN_A}", "type": "token"}},
            "quote_token": {"data": {"id": f"solana_{TOKEN_B}", "type": "token"}},
            "dex": {"data": {"id": "pump-swap", "type": "dex"}},
        },
    }


def _raw(rows):
    return json.dumps({"data": rows, "included": []}, separators=(",", ":")).encode()


def _fixture(*, pages=2, sample=(1, 1)):
    scheduled = 300 * NS * 6_000_000
    policy = compile_policy(
        first_scan_ns=scheduled,
        scan_interval_seconds=300,
        pages=pages,
        page_spacing_seconds=12,
        sample_numerator=sample[0],
        sample_denominator=sample[1],
        source_spec_sha256=SPEC_SHA,
    )
    plan = compile_scan_plan(policy, scan_index=0, scheduled_ns=scheduled)
    return policy, plan, scheduled


def _slot(plan, page, scheduled, raw, *, available_delta, status="received", http_status=200):
    request = plan["page_slots"][page - 1]
    started = scheduled + (page - 1) * 12 * NS + NS
    receipt = scheduled + available_delta * NS - NS
    available = scheduled + available_delta * NS
    return {
        "page": page,
        "request_id": request["request_id"],
        "status": status,
        "http_status": http_status,
        "request_started_ns": started,
        "receipt_ns": receipt,
        "durable_available_ns": available,
        "raw_body": raw,
        "raw_sha256": __import__("hashlib").sha256(raw).hexdigest() if raw is not None else None,
        "raw_bytes": len(raw) if raw is not None else 0,
        "body_truncated": False,
    }


def test_replay_preserves_denominators_and_first_seen_availability_without_activity_filter():
    policy, plan, scheduled = _fixture()
    first = _raw([_pool(POOL_A, liquidity="0"), _pool(POOL_B)])
    second = _raw([_pool(POOL_A), _pool(POOL_C, created_at="2099-01-01T00:00:00Z")])
    pages = [
        _slot(plan, 1, scheduled, first, available_delta=25),
        _slot(plan, 2, scheduled, second, available_delta=20),
    ]
    receipt = replay_scan(
        policy=policy,
        plan=plan,
        page_slots=pages,
        previously_seen_pool_ids=[f"solana_{POOL_B}"],
    )
    assert receipt["page_summary"] == {
        "planned_pages": 2,
        "received_pages": 2,
        "http_error_pages": 0,
        "ambiguous_pages": 0,
        "missed_pages": 0,
        "oversize_response_pages": 0,
        "truncated_http_error_pages": 0,
        "malformed_response_pages": 0,
        "malformed_included_pages": 0,
        "oversize_included_pages": 0,
        "unknown_page_exposure": 0,
        "provider_index_completeness_claim": False,
        "solana_birth_census_claim": False,
        "lifecycle_death_claim": False,
    }
    assert receipt["first_seen_pool_ids"] == [f"solana_{POOL_A}", f"solana_{POOL_C}"]
    assert receipt["followup_sample_pool_ids"] == receipt["first_seen_pool_ids"]
    appearances = receipt["pool_appearances"]
    assert [(row["pool_id"], row["first_seen_in_scan"]) for row in appearances] == [
        (f"solana_{POOL_A}", True),
        (f"solana_{POOL_C}", True),
        (f"solana_{POOL_A}", False),
        (f"solana_{POOL_B}", False),
    ]
    future = next(row for row in appearances if row["pool_id"] == f"solana_{POOL_C}")
    assert future["scheduled_ns"] == scheduled
    assert future["request_started_ns"] == pages[1]["request_started_ns"]
    assert future["receipt_ns"] == pages[1]["receipt_ns"]
    assert future["durable_available_ns"] == pages[1]["durable_available_ns"]
    assert future["decision_ns"] is None
    assert future["provider_pool_created_at_state"] == "future_relative_to_first_receipt"
    assert future["provider_pool_created_at"] == "2099-01-01T00:00:00Z"
    assert receipt["provider_io"] is False


def test_missing_or_duplicate_page_does_not_shrink_fixed_denominator():
    policy, plan, scheduled = _fixture()
    error = _slot(plan, 1, scheduled, None, available_delta=0, status="http_error", http_status=503)
    error.update(
        request_started_ns=scheduled + NS,
        receipt_ns=scheduled + 2 * NS,
        durable_available_ns=scheduled + 3 * NS,
    )
    missed = _slot(plan, 2, scheduled, None, available_delta=0, status="missed", http_status=None)
    missed.update(request_started_ns=None, receipt_ns=None, durable_available_ns=None)
    receipt = replay_scan(
        policy=policy, plan=plan, page_slots=[error, missed], previously_seen_pool_ids=[]
    )
    assert receipt["page_summary"]["planned_pages"] == 2
    assert receipt["page_summary"]["unknown_page_exposure"] == 2
    assert receipt["first_seen_pool_ids"] == []
    with pytest.raises(DiscoveryError, match="duplicate_or_unknown"):
        replay_scan(
            policy=policy, plan=plan, page_slots=[error, error], previously_seen_pool_ids=[]
        )
    dirty_missed = copy.deepcopy(missed)
    dirty_missed["raw_bytes"] = 1
    with pytest.raises(DiscoveryError, match="missed_slot_has_attempt"):
        replay_scan(
            policy=policy,
            plan=plan,
            page_slots=[error, dirty_missed],
            previously_seen_pool_ids=[],
        )


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("request", "page_request_binding"),
        ("spacing", "page_spacing"),
        ("raw_hash", "page_raw_binding"),
        ("missing", "page_denominator_incomplete"),
        ("plan_hash", "plan_binding"),
    ],
)
def test_replay_rejects_binding_and_denominator_mutations(mutation, reason):
    policy, plan, scheduled = _fixture()
    body = _raw([_pool(POOL_A)])
    page1 = _slot(plan, 1, scheduled, body, available_delta=20)
    page2 = _slot(plan, 2, scheduled, body, available_delta=30)
    changed_plan = copy.deepcopy(plan)
    if mutation == "request":
        page2["request_id"] = "f" * 64
    elif mutation == "spacing":
        page2["request_started_ns"] = page1["request_started_ns"] + 11 * NS
    elif mutation == "raw_hash":
        page1["raw_sha256"] = "0" * 64
    elif mutation == "missing":
        with pytest.raises(DiscoveryError, match=reason):
            replay_scan(policy=policy, plan=plan, page_slots=[page1], previously_seen_pool_ids=[])
        return
    elif mutation == "plan_hash":
        changed_plan["page_slots"][0]["page"] = 2
    with pytest.raises(DiscoveryError, match=reason):
        replay_scan(
            policy=policy, plan=changed_plan, page_slots=[page1, page2], previously_seen_pool_ids=[]
        )


def test_malformed_rows_are_quarantined_without_filtering_valid_pool_or_claiming_absence():
    policy, plan, scheduled = _fixture(pages=1)
    raw = _raw([_pool(POOL_A), {"id": "solana_not-base58", "type": "pool", "attributes": {}}])
    slot = _slot(plan, 1, scheduled, raw, available_delta=20)
    result = replay_scan(policy=policy, plan=plan, page_slots=[slot], previously_seen_pool_ids=[])
    page = result["page_results"]["1"]
    assert page["row_count"] == 2
    assert page["valid_pool_count"] == 1
    assert page["malformed_row_count"] == 1
    assert page["quarantined_rows"][0]["reason"] == "discovery_pool_address_invalid"
    assert result["page_summary"]["provider_index_completeness_claim"] is False
    assert result["pool_appearances"][0]["provider_pool_created_at_state"] == (
        "not_future_relative_to_first_receipt"
    )


def test_syntactically_valid_pool_is_enrolled_even_with_malformed_metadata():
    policy, plan, scheduled = _fixture(pages=1)
    row = _pool(POOL_A)
    row["attributes"] = "provider shape drift"
    row["relationships"] = {
        "base_token": {"data": {"id": "not-solana", "type": "token"}},
        "quote_token": {"data": "malformed"},
        "dex": {"data": {"id": "pump-swap", "type": "dex"}},
    }
    result = replay_scan(
        policy=policy,
        plan=plan,
        page_slots=[_slot(plan, 1, scheduled, _raw([row]), available_delta=5)],
        previously_seen_pool_ids=[],
    )
    assert result["first_seen_pool_ids"] == [f"solana_{POOL_A}"]
    appearance = result["pool_appearances"][0]
    assert appearance["attributes_state"] == "malformed"
    assert appearance["provider_address_state"] == "missing"
    assert appearance["base_token_state"] == "other_network"
    assert appearance["quote_token_state"] == "malformed"
    assert appearance["dex_state"] == "parsed"
    assert appearance["provider_pool_created_at_state"] == "attributes_malformed"
    assert len(appearance["row_sha256"]) == 64


def test_bad_included_section_does_not_hide_valid_ids_from_data_rows():
    policy, plan, scheduled = _fixture(pages=1)
    raw = json.dumps(
        {"data": [_pool(POOL_A)], "included": "provider drift"}, separators=(",", ":")
    ).encode()
    result = replay_scan(
        policy=policy,
        plan=plan,
        page_slots=[_slot(plan, 1, scheduled, raw, available_delta=5)],
        previously_seen_pool_ids=[],
    )
    assert result["first_seen_pool_ids"] == [f"solana_{POOL_A}"]
    assert result["page_summary"]["received_pages"] == 1
    assert result["page_summary"]["malformed_included_pages"] == 1


def test_valid_pool_identity_survives_provider_row_type_drift():
    policy, plan, scheduled = _fixture(pages=1)
    row = _pool(POOL_A)
    row["type"] = "provider_type_drift"
    result = replay_scan(
        policy=policy,
        plan=plan,
        page_slots=[_slot(plan, 1, scheduled, _raw([row]), available_delta=5)],
        previously_seen_pool_ids=[],
    )
    assert result["first_seen_pool_ids"] == [f"solana_{POOL_A}"]
    assert result["pool_appearances"][0]["provider_row_type_state"] == "unexpected"


def test_policy_and_schedule_are_frozen_and_bounded():
    policy, _plan, scheduled = _fixture(pages=3)
    assert len(policy["page_range"]) == 3
    with pytest.raises(DiscoveryError, match="policy_input"):
        compile_policy(
            first_scan_ns=300 * NS * 6_000_000,
            scan_interval_seconds=59,
            pages=11,
            page_spacing_seconds=1,
            sample_numerator=1,
            sample_denominator=1,
            max_raw_bytes_per_page=1024,
            source_spec_sha256=SPEC_SHA,
        )
    with pytest.raises(DiscoveryError, match="schedule"):
        compile_scan_plan(policy, scan_index=1, scheduled_ns=scheduled + NS)
    with pytest.raises(DiscoveryError, match="policy_input"):
        compile_policy(
            first_scan_ns=scheduled,
            scan_interval_seconds=300,
            pages=6,
            page_spacing_seconds=12,
            sample_numerator=0,
            sample_denominator=1,
            source_spec_sha256=SPEC_SHA,
        )


def test_campaign_replays_global_spacing_across_scan_boundaries():
    scheduled = 300 * NS * 6_000_000
    policy = compile_policy(
        first_scan_ns=scheduled,
        scan_interval_seconds=60,
        pages=5,
        max_scan_count=2,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256=SPEC_SHA,
    )
    scans = []
    empty = _raw([])
    for scan_index in range(2):
        plan = compile_scan_plan(
            policy,
            scan_index=scan_index,
            scheduled_ns=scheduled + scan_index * 60 * NS,
        )
        slots = []
        for page in range(1, 6):
            slot = _slot(
                plan,
                page,
                plan["scheduled_ns"],
                empty,
                available_delta=100,
            )
            start_offset = (11 if scan_index == 0 else 0) + (page - 1) * 12
            slot["request_started_ns"] = plan["scheduled_ns"] + start_offset * NS
            slot["receipt_ns"] = slot["request_started_ns"] + NS
            slot["durable_available_ns"] = slot["receipt_ns"] + NS
            slots.append(slot)
        scans.append({"plan": plan, "page_slots": slots})
    with pytest.raises(DiscoveryError, match="campaign_global_page_spacing"):
        replay_campaign(policy=policy, scans=scans)


def test_campaign_replays_cross_scan_minimum_page_spacing():
    scheduled = 300 * NS * 6_000_000
    policy = compile_policy(
        first_scan_ns=scheduled,
        scan_interval_seconds=60,
        pages=1,
        max_scan_count=2,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256=SPEC_SHA,
    )
    empty = _raw([])
    scans = []
    for scan_index, offset in enumerate((59, 0)):
        plan = compile_scan_plan(
            policy,
            scan_index=scan_index,
            scheduled_ns=scheduled + scan_index * 60 * NS,
        )
        slot = _slot(plan, 1, plan["scheduled_ns"], empty, available_delta=100)
        slot["request_started_ns"] = plan["scheduled_ns"] + offset * NS
        slot["receipt_ns"] = slot["request_started_ns"] + NS
        slot["durable_available_ns"] = slot["receipt_ns"] + NS
        scans.append({"plan": plan, "page_slots": [slot]})
    with pytest.raises(DiscoveryError, match="campaign_global_page_spacing"):
        replay_campaign(policy=policy, scans=scans)


def test_policy_verifier_rejects_boolean_integer_caps_even_if_rehashed():
    policy = compile_policy(
        first_scan_ns=86_400 * NS * 20_833,
        scan_interval_seconds=86_400,
        pages=1,
        max_scan_count=1,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256=SPEC_SHA,
    )
    policy["caps"]["max_requests_per_lease"] = True
    core = dict(policy)
    core.pop("policy_sha256")
    policy["policy_sha256"] = sha256(canonical(core))
    with pytest.raises(DiscoveryError, match="policy_invalid"):
        compile_scan_plan(policy, scan_index=0, scheduled_ns=policy["first_scan_ns"])


def test_sol_pilot_envelope_has_finite_epochs_and_zero_followup_requests():
    policy = compile_policy(
        first_scan_ns=300 * NS * 6_000_000,
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256=SPEC_SHA,
    )
    assert policy["max_scan_count"] == 864
    assert policy["lease_duration_seconds"] == 72 * 60 * 60
    assert policy["caps"]["max_requests_per_utc_day"] == 864
    assert policy["caps"]["max_requests_per_lease"] == 2_592
    assert policy["caps"]["max_raw_bytes_per_lease"] == 1_358_954_496
    assert policy["caps"]["physical_reserve_bytes_per_page"] == 2_621_440
    assert policy["caps"]["max_physical_reserve_per_lease"] == 6_794_772_480
    assert policy["sample"]["numerator"] == 0
    with pytest.raises(DiscoveryError, match="scan_identity"):
        compile_scan_plan(
            policy, scan_index=864, scheduled_ns=policy["first_scan_ns"] + 864 * 300 * NS
        )


def test_campaign_carries_first_seen_availability_across_scans_and_rejects_omitted_scan():
    policy, plan0, scheduled = _fixture(pages=1)
    raw0 = _raw([_pool(POOL_A)])
    slot0 = _slot(plan0, 1, scheduled, raw0, available_delta=20)
    plan1 = compile_scan_plan(policy, scan_index=1, scheduled_ns=scheduled + 300 * NS)
    raw1 = _raw([_pool(POOL_A), _pool(POOL_B, created_at="bad-date")])
    slot1 = _slot(plan1, 1, scheduled + 300 * NS, raw1, available_delta=25)
    campaign = replay_campaign(
        policy=policy,
        scans=[
            {"plan": plan0, "page_slots": [slot0]},
            {"plan": plan1, "page_slots": [slot1]},
        ],
    )
    assert campaign["summary"]["planned_page_slots"] == 2
    assert campaign["summary"]["unique_first_seen_pool_count"] == 2
    assert [row["pool_id"] for row in campaign["enrollments"]] == [
        f"solana_{POOL_A}",
        f"solana_{POOL_B}",
    ]
    assert campaign["enrollments"][1]["provider_pool_created_at_state"] == "malformed"
    assert campaign["enrollments"][1]["first_seen_request_id"] == slot1["request_id"]
    assert campaign["enrollments"][1]["first_seen_receipt_ns"] == slot1["receipt_ns"]
    assert campaign["page_transitions"][0]["ordered_ids_changed"] is True
    assert campaign["page_transitions"][0]["intersection_count"] == 1
    assert campaign["summary"]["successful_page_order_changes"] == 1
    with pytest.raises(DiscoveryError, match="campaign_scan_sequence"):
        replay_campaign(
            policy=policy,
            scans=[{"plan": plan1, "page_slots": [slot1]}],
        )


def test_unparseable_success_body_is_an_unknown_page_not_an_empty_page():
    policy, plan, scheduled = _fixture(pages=1)
    malformed = b'{"data":['
    slot = _slot(plan, 1, scheduled, malformed, available_delta=10)
    receipt = replay_scan(policy=policy, plan=plan, page_slots=[slot], previously_seen_pool_ids=[])
    assert receipt["page_summary"]["received_pages"] == 1
    assert receipt["page_summary"]["malformed_response_pages"] == 1
    assert receipt["page_summary"]["unknown_page_exposure"] == 1
    assert receipt["page_results"]["1"]["row_count"] is None
    assert receipt["page_results"]["1"]["ordered_pool_ids"] == []


def test_oversize_response_retains_only_bounded_prefix_and_keeps_unknown_exposure():
    policy, plan, scheduled = _fixture(pages=1)
    prefix = b"x" * policy["caps"]["max_raw_bytes_per_page"]
    slot = _slot(plan, 1, scheduled, prefix, available_delta=10, status="oversize_response")
    slot["body_truncated"] = True
    receipt = replay_scan(
        policy=policy,
        plan=plan,
        page_slots=[slot],
        previously_seen_pool_ids=[],
    )
    assert receipt["page_summary"]["oversize_response_pages"] == 1
    assert receipt["page_summary"]["unknown_page_exposure"] == 1
    assert receipt["first_seen_pool_ids"] == []
    page = receipt["page_results"]["1"]
    assert page["body_truncated"] is True
    assert page["raw_bytes"] == len(prefix)


def test_truncated_http_error_retains_bounded_prefix_and_failure_state():
    policy, plan, scheduled = _fixture(pages=1)
    prefix = b"e" * policy["caps"]["max_raw_bytes_per_page"]
    slot = _slot(
        plan, 1, scheduled, prefix, available_delta=10, status="http_error", http_status=503
    )
    slot["body_truncated"] = True
    receipt = replay_scan(policy=policy, plan=plan, page_slots=[slot], previously_seen_pool_ids=[])
    assert receipt["page_summary"]["http_error_pages"] == 1
    assert receipt["page_summary"]["truncated_http_error_pages"] == 1
    assert receipt["page_summary"]["unknown_page_exposure"] == 1


@pytest.mark.parametrize(
    "slots,previous,reason",
    [
        ([None], [], "page_slot_shape"),
        ([{}], [], "page_slot_duplicate_or_unknown"),
        ([], [], "page_denominator_incomplete"),
        ([{"page": []}], [], "page_slot_duplicate_or_unknown"),
    ],
)
def test_malformed_replay_containers_fail_with_typed_error(slots, previous, reason):
    policy, plan, _scheduled = _fixture(pages=1)
    with pytest.raises(DiscoveryError, match=reason):
        replay_scan(
            policy=policy,
            plan=plan,
            page_slots=slots,
            previously_seen_pool_ids=previous,
        )


def test_campaign_requires_at_least_one_explicit_scheduled_scan():
    policy, _plan, _scheduled = _fixture(pages=1)
    with pytest.raises(DiscoveryError, match="campaign_input"):
        replay_campaign(policy=policy, scans=[])


def test_unhashable_prior_seen_value_is_a_typed_error():
    policy, plan, scheduled = _fixture(pages=1)
    missed = _slot(plan, 1, scheduled, None, available_delta=0, status="missed", http_status=None)
    missed.update(request_started_ns=None, receipt_ns=None, durable_available_ns=None)
    with pytest.raises(DiscoveryError, match="prior_seen_identity"):
        replay_scan(
            policy=policy,
            plan=plan,
            page_slots=[missed],
            previously_seen_pool_ids=[["solana"]],
        )


@settings(max_examples=250, deadline=None)
@given(raw=st.binary(max_size=4096))
def test_arbitrary_page_bytes_either_return_a_consistent_projection_or_typed_error(raw):
    try:
        result = parse_page(raw, available_ns=1_800_000_000 * NS)
    except DiscoveryError:
        return
    assert result["raw_sha256"] == __import__("hashlib").sha256(raw).hexdigest()
    assert result["raw_bytes"] == len(raw)
    assert result["row_count"] == result["valid_pool_count"] + result["malformed_row_count"]
    assert len(result["rows"]) == result["valid_pool_count"]
    assert len(result["quarantined_rows"]) == result["malformed_row_count"]


def test_deeply_nested_json_is_a_typed_parse_failure():
    nested = b'{"data":' + b"[" * 10_000 + b"]" * 10_000 + b"}"
    with pytest.raises(DiscoveryError, match="json_invalid"):
        parse_page(nested, available_ns=1_800_000_000 * NS)


@settings(max_examples=100, deadline=None)
@given(
    pool_numbers=st.lists(
        st.integers(min_value=1, max_value=250), min_size=1, max_size=8, unique=True
    )
)
def test_followup_hash_sample_is_invariant_to_page_receipt_order(pool_numbers):
    policy, plan, scheduled = _fixture(sample=(1, 3))
    pool_ids = [_b58(number.to_bytes(32, "big")) for number in pool_numbers]
    first_page_rows = [_pool(pool_ids[0]), *[_pool(pool) for pool in pool_ids[::2][1:]]]
    second_page_rows = [_pool(pool_ids[0]), *[_pool(pool) for pool in pool_ids[1::2]]]
    first = _raw(first_page_rows)
    second = _raw(second_page_rows)
    page1 = _slot(plan, 1, scheduled, first, available_delta=25)
    page2 = _slot(plan, 2, scheduled, second, available_delta=20)
    original = replay_scan(
        policy=policy,
        plan=plan,
        page_slots=[page1, page2],
        previously_seen_pool_ids=[],
    )
    page1_later = {
        **page1,
        "receipt_ns": scheduled + 30 * NS,
        "durable_available_ns": scheduled + 31 * NS,
    }
    page2_earlier = {
        **page2,
        "receipt_ns": scheduled + 18 * NS,
        "durable_available_ns": scheduled + 19 * NS,
    }
    reordered = replay_scan(
        policy=policy,
        plan=plan,
        page_slots=[page1_later, page2_earlier],
        previously_seen_pool_ids=[],
    )
    assert set(original["first_seen_pool_ids"]) == set(reordered["first_seen_pool_ids"])
    assert set(original["followup_sample_pool_ids"]) == set(reordered["followup_sample_pool_ids"])
