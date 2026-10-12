from __future__ import annotations

import datetime as dt
import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools.brain_senses_external_writer_v1 import canonical_json  # noqa: E402
from tools.brain_senses_gecko_capture_only_v2_verify import (  # noqa: E402
    VerifyError,
    build_intent,
    expected_request,
    verify_capture_prefix_v2,
)
from tools.brain_senses_gecko_scout_chain_v1 import (  # noqa: E402
    ROOT_PREFIX,
    append_plan,
    build_genesis,
)
from tools.brain_senses_gecko_shared_usage_v1 import (  # noqa: E402
    assess_usage,
    make_verified_root_snapshot,
)

SOURCE_PIN = hashlib.sha256(b"synthetic-source-pin").hexdigest()
POPULATION = [
    {
        "pool_id": f"pool-{index:03d}",
        "base_mint": f"mint-{index:03d}",
        "quote_mint": "wsol",
        "rank_sha256": hashlib.sha256(f"{SOURCE_PIN}\0pool-{index:03d}".encode()).hexdigest(),
    }
    for index in range(scout.POOL_COUNT)
]
POPULATION = scout.pool_order(POPULATION, SOURCE_PIN)
ROOT = f"{ROOT_PREFIX}/{'b' * 32}"
FIRST_CLOSE = 1_800_000_000 * scout.NS
FIRST_CLOSE -= FIRST_CLOSE % scout.BUCKET_NS
INVENTORY_HASH = hashlib.sha256(b"root inventory").hexdigest()
SOURCE_HASH = hashlib.sha256(b"verified prefix").hexdigest()


def _policy() -> dict[str, Any]:
    core = {
        "schema": scout.SCHEMA,
        "schema_version": 1,
        "policy_id": scout.POLICY_ID,
        "source": {"source_pin": SOURCE_PIN},
        "population_count": scout.POOL_COUNT,
        "population_sha256": scout.population_digest(POPULATION),
        "population": POPULATION,
        "provider_io": False,
        "activation_authorized": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }
    return {**core, "policy_sha256": hashlib.sha256(scout.canonical_json(core)).hexdigest()}


def _binding(policy: dict[str, Any]) -> dict[str, Any]:
    core = {
        "schema": "brain-senses-gecko-scout-chain-v1-binding",
        "schema_version": 1,
        "root": ROOT,
        "storage": {
            "root_id": "0123456789abcdef0123456789abcdef",
            "volume_uuid": "12345678-1234-1234-1234-123456789abc",
            "device": 1,
            "inode": 2,
        },
        "policy_sha256": policy["policy_sha256"],
        "population_sha256": policy["population_sha256"],
        "release_sha256": hashlib.sha256(b"release").hexdigest(),
        "provider_io": False,
        "activation_authorized": False,
    }
    return {**core, "binding_sha256": hashlib.sha256(scout.canonical_json(core)).hexdigest()}


def _clock(decision_ns: int, decision_mono_ns: int) -> tuple[dict[str, Any], str]:
    base_ns = decision_ns - 2 * scout.NS
    date = dt.datetime.fromtimestamp(base_ns // scout.NS, tz=dt.UTC).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    date_ns = (base_ns // scout.NS) * scout.NS
    rows = []
    for index, host in enumerate(("www.cloudflare.com", "www.google.com")):
        sent = date_ns - scout.NS + index * scout.NS
        received = sent + scout.NS
        mono_sent = decision_mono_ns - 3 * scout.NS + index * scout.NS
        mono_received = mono_sent + scout.NS
        rows.append(
            {
                "host": host,
                "status": 200,
                "date": date
                if index == 0
                else dt.datetime.fromtimestamp((date_ns // scout.NS) + 1, tz=dt.UTC).strftime(
                    "%a, %d %b %Y %H:%M:%S GMT"
                ),
                "age": None,
                "sent_ns": sent,
                "received_ns": received,
                "monotonic_sent_ns": mono_sent,
                "monotonic_received_ns": mono_received,
            }
        )
    record = {
        "schema": "brain-senses-external-v1-clock",
        "schema_version": 1,
        "samples": rows,
        "checked_ns": rows[-1]["received_ns"],
        "monotonic_ns": rows[-1]["monotonic_received_ns"],
    }
    return record, f"{ROOT}/clocks/{FIRST_CLOSE}-00.json"


def _fixtures() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    policy = _policy()
    binding = _binding(policy)
    genesis = build_genesis(policy, FIRST_CLOSE)
    plan = append_plan(policy, genesis, bucket_close_ns=FIRST_CLOSE)
    return policy, binding, [genesis, plan]


def _admission(request_id: str, checked_ns: int) -> dict[str, Any]:
    roots = [f"services/gecko-market-v1/{name}" for name in ("v3", "v4", "v5")]
    snapshots = [
        make_verified_root_snapshot(
            root=root,
            status="verified_none",
            root_inventory_sha256=INVENTORY_HASH,
            source_prefix_sha256=SOURCE_HASH,
            attempts=[],
        )
        for root in roots
    ]
    return assess_usage(
        snapshots,
        expected_roots=roots,
        checked_ns=checked_ns,
        prospective_request_id=request_id,
        prospective_maximum_response_bytes=MAX_RESPONSE_BYTES,
    )


def _coordinator_events(intent: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    policy_hash = hashlib.sha256(b"coordinator policy").hexdigest()
    events = [
        capacity._event(
            seq=0,
            kind="genesis",
            at_ns=1,
            previous=None,
            fields={"policy_sha256": policy_hash},
        )
    ]
    if intent is not None:
        intent_bytes = canonical_json(intent)
        fields = {
            "reservation_id": f"reservation-{intent['request_id']}",
            "service": "gecko",
            "request_id": intent["request_id"],
            "intent_path": f"{ROOT}/intents/{intent['request_id']}.json",
            "outcome_path": f"{ROOT}/results/{intent['request_id']}.json",
            "intent_sha256": hashlib.sha256(intent_bytes).hexdigest(),
            "reserved_bytes": 1_000_000,
            "intent_bytes_sha256": hashlib.sha256(intent_bytes).hexdigest(),
            "allocated_before_bytes": 0,
        }
        events.append(
            capacity._event(
                seq=1,
                kind="reserved",
                at_ns=2,
                previous=events[-1]["event_sha256"],
                fields=fields,
            )
        )
    return events


def _hashed_record(core: dict[str, Any], hash_field: str) -> dict[str, Any]:
    return {**core, hash_field: hashlib.sha256(canonical_json(core)).hexdigest()}


MAX_RESPONSE_BYTES = scout.MAX_RESPONSE_BYTES


def test_plan_bound_intent_replays_and_unresolved_usage_is_projected_at_maximum() -> None:
    policy, binding, records = _fixtures()
    plan = records[-1]
    request = expected_request(policy, binding["binding_sha256"], plan, 0)
    decision = FIRST_CLOSE + 70 * scout.NS
    mono = 80 * scout.NS
    admission = _admission(request["request_id"], decision)
    clock, clock_path = _clock(decision, mono)
    intent = build_intent(
        root=ROOT,
        policy=policy,
        binding=binding,
        plan_record=plan,
        slot_index=0,
        decision_ns=decision,
        decision_monotonic_ns=mono,
        clock_record=clock,
        admission=admission,
    )
    result = verify_capture_prefix_v2(
        root=ROOT,
        binding=binding,
        policy=policy,
        chain_records=records,
        intents=[intent],
        dispatch_records=[],
        terminals=[],
        raw_responses={},
        clock_files={clock_path: canonical_json(clock)},
        confirmations=[],
        coordinator_events=_coordinator_events(intent),
    )
    assert result["intent_count"] == 1
    assert result["dispatch_count"] == 0
    assert result["usage_attempts"] == [
        {
            "request_id": request["request_id"],
            "decision_ns": decision,
            "maximum_response_bytes": MAX_RESPONSE_BYTES,
            "response_bytes": None,
            "response_sha256": None,
        }
    ]


def test_intent_rejects_assessment_for_different_request() -> None:
    policy, binding, records = _fixtures()
    decision = FIRST_CLOSE + 70 * scout.NS
    clock, _ = _clock(decision, 80 * scout.NS)
    admission = _admission("different-request", decision)
    with pytest.raises(VerifyError, match="v2_admission_not_for_request"):
        build_intent(
            root=ROOT,
            policy=policy,
            binding=binding,
            plan_record=records[-1],
            slot_index=0,
            decision_ns=decision,
            decision_monotonic_ns=80 * scout.NS,
            clock_record=clock,
            admission=admission,
        )


def test_durable_empty_response_is_replayed_and_counts_actual_bytes() -> None:
    policy, binding, records = _fixtures()
    plan = records[-1]
    decision = FIRST_CLOSE + 70 * scout.NS
    mono = 80 * scout.NS
    request = expected_request(policy, binding["binding_sha256"], plan, 0)
    clock, clock_path = _clock(decision, mono)
    intent = build_intent(
        root=ROOT,
        policy=policy,
        binding=binding,
        plan_record=plan,
        slot_index=0,
        decision_ns=decision,
        decision_monotonic_ns=mono,
        clock_record=clock,
        admission=_admission(request["request_id"], decision),
    )
    dispatch = _hashed_record(
        {
            "schema": "brain-senses-gecko-capture-only-v2-dispatch-v1",
            "sequence": 0,
            "request_id": request["request_id"],
            "intent_sha256": intent["intent_sha256"],
            "dispatch_ns": decision + scout.NS,
            "previous_sha256": None,
        },
        "record_sha256",
    )
    body = b'{"data":{"attributes":{"ohlcv_list":[]}}}'
    receipt = dispatch["dispatch_ns"] + scout.NS
    terminal = _hashed_record(
        {
            "schema": "brain-senses-gecko-capture-only-v2-terminal-v1",
            "request_id": request["request_id"],
            "intent_sha256": intent["intent_sha256"],
            "dispatch_record_sha256": dispatch["record_sha256"],
            "dispatch_ns": dispatch["dispatch_ns"],
            "request_started_ns": dispatch["dispatch_ns"],
            "receipt_ns": receipt,
            "availability_ns": receipt,
            "status": "empty_response",
            "http_status": 200,
            "response_bytes": len(body),
            "response_sha256": hashlib.sha256(body).hexdigest(),
            "provider_charge_units": None,
            "provider_charge_status": "unknown_unverified",
        },
        "terminal_sha256",
    )
    result = verify_capture_prefix_v2(
        root=ROOT,
        binding=binding,
        policy=policy,
        chain_records=records,
        intents=[intent],
        dispatch_records=[dispatch],
        terminals=[terminal],
        raw_responses={request["request_id"]: body},
        clock_files={clock_path: canonical_json(clock)},
        confirmations=[],
        coordinator_events=_coordinator_events(intent),
    )
    assert result["terminal_count"] == 1
    assert result["usage_attempts"][0]["response_bytes"] == len(body)


def test_assessment_cannot_be_rebound_to_a_different_decision_time() -> None:
    policy, binding, records = _fixtures()
    request = expected_request(policy, binding["binding_sha256"], records[-1], 0)
    decision = FIRST_CLOSE + 70 * scout.NS
    admission = _admission(request["request_id"], decision - scout.NS)
    clock, _ = _clock(decision, 80 * scout.NS)
    with pytest.raises(VerifyError, match="v2_admission_not_for_request"):
        build_intent(
            root=ROOT,
            policy=policy,
            binding=binding,
            plan_record=records[-1],
            slot_index=0,
            decision_ns=decision,
            decision_monotonic_ns=80 * scout.NS,
            clock_record=clock,
            admission=admission,
        )


def test_v2_verifier_rejects_noncanonical_root_or_mismatched_plan_identity() -> None:
    policy, binding, records = _fixtures()
    empty = {
        "policy": policy,
        "chain_records": records,
        "intents": [],
        "dispatch_records": [],
        "terminals": [],
        "raw_responses": {},
        "clock_files": {},
        "confirmations": [],
        "coordinator_events": _coordinator_events(),
    }
    with pytest.raises(VerifyError, match="v2_root_invalid"):
        verify_capture_prefix_v2(root=f"{ROOT}/../escape", binding=binding, **empty)
    bad_binding = dict(binding, root="services/gecko-market-v1/capture-only-v2/" + "c" * 32)
    core = dict(bad_binding)
    core.pop("binding_sha256")
    bad_binding["binding_sha256"] = hashlib.sha256(scout.canonical_json(core)).hexdigest()
    with pytest.raises(VerifyError, match="v2_binding_invalid"):
        verify_capture_prefix_v2(root=ROOT, binding=bad_binding, **empty)
