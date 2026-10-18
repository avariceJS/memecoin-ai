import pytest

from memecoin_experience.storage import digest
from memecoin_pumpswap import slot_targets

RECONCILIATION_PIN = "a" * 64
SOURCE_PIN = "b" * 64
EVIDENCE_PIN = "c" * 64


def _row(slot, status="complete", **overrides):
    row = {
        "slot": slot,
        "status": status,
        "available_ns": slot * 100 + 50,
        "disposition": "block_present" if status == "complete" else "block_presence_unresolved",
        "reason": "",
        "enumeration_relation": "listed",
        "enumeration_status": "complete",
        "transport_status": "captured_result",
        "lineage_status": "not_required",
        "proof_available_ns": None,
        "proof_evidence": [],
        "evidence": [EVIDENCE_PIN],
    }
    row.update(overrides)
    return row


def _accounting(slot, count=0, **overrides):
    row = {
        "slot": slot,
        "available_ns": slot * 100 + 50,
        "positive_reference_facts": count,
        "unsupported_traces": 0,
        "malformed_instruction_data": 0,
        "unresolved_role_maps": 0,
        "protected_entity_rejections": 0,
    }
    row.update(overrides)
    return row


def _build(rows, accounting, counts, available=None):
    first, last = rows[0]["slot"], rows[-1]["slot"] + 1
    return slot_targets.build_logical_samples(
        interval_start_slot=first,
        interval_end_slot=last,
        support_rows=rows,
        slot_accounting={str(slot): value for slot, value in accounting.items()},
        fact_counts=counts,
        fact_available_ns=available or {},
        reconciliation_pin=RECONCILIATION_PIN,
        observation_source_pin=SOURCE_PIN,
    )


def test_h1_real_shape_exact_count_then_pending_target():
    samples = _build(
        [_row(454538109), _row(454538110)],
        {454538109: _accounting(454538109), 454538110: _accounting(454538110, 87)},
        {454538110: 87},
        {454538110: 454538110 * 100 + 40},
    )
    assert samples[0]["origin_coordinate"] == 454538109
    assert samples[0]["target_window_start_coordinate"] == 454538110
    assert samples[0]["target_window_end_coordinate_exclusive"] == 454538111
    assert samples[0]["target_status"] == "exact"
    assert samples[0]["target_value"] == 87
    assert samples[0]["decision_available_ns"] == 454538109 * 100 + 50
    assert samples[0]["target_available_ns"] == 454538110 * 100 + 50
    assert samples[1]["origin_coordinate"] == 454538110
    assert samples[1]["target_status"] == "pending"
    assert samples[1]["target_value"] is None
    assert samples[1]["lower_bound"] is None
    assert all(not sample["fit_eligible"] for sample in samples)
    assert all(sample["role"] == "exploratory" for sample in samples)


def test_incomplete_trace_keeps_positive_lower_bound_not_exact():
    samples = _build(
        [_row(1), _row(2)],
        {1: _accounting(1), 2: _accounting(2, 4, unsupported_traces=1)},
        {2: 4},
        {2: 240},
    )
    sample = samples[0]
    assert sample["target_status"] == "lower_bound"
    assert sample["target_value"] is None
    assert sample["lower_bound"] == 4
    assert sample["lower_bound_available_ns"] == 240
    assert sample["target_reasons"] == [
        {"slot": 2, "status": "unsupported", "reason": "unsupported_traces"}
    ]
    loss = slot_targets.experience_loss_adapter(sample)
    assert loss["exact_mask"] is False
    assert loss["lower_bound_mask"] is True


def test_single_provider_failure_status_with_no_positive_is_not_a_zero_bound():
    partial = _row(
        2,
        "partial",
        reason="single_provider_positive_peer_failure_or_stop",
        transport_status="provider_error",
    )
    sample = _build(
        [_row(1), partial],
        {1: _accounting(1), 2: _accounting(2)},
        {},
    )[0]
    assert sample["target_status"] == "masked"
    assert sample["target_value"] is None
    assert sample["lower_bound"] is None
    loss = slot_targets.experience_loss_adapter(sample)
    assert loss["exact_mask"] is False
    assert loss["lower_bound_mask"] is False


def test_bound_availability_uses_latest_positive_capture_across_window():
    samples = slot_targets.build_logical_samples(
        interval_start_slot=1,
        interval_end_slot=4,
        support_rows=[_row(1), _row(2), _row(3)],
        slot_accounting={
            "1": _accounting(1),
            "2": _accounting(2, 4, unsupported_traces=1),
            "3": _accounting(3, 5, unsupported_traces=1),
        },
        fact_counts={2: 4, 3: 5},
        fact_available_ns={2: 240, 3: 340},
        reconciliation_pin=RECONCILIATION_PIN,
        observation_source_pin=SOURCE_PIN,
        horizons=(2,),
    )
    assert samples[0]["target_status"] == "lower_bound"
    assert samples[0]["lower_bound"] == 9
    assert samples[0]["lower_bound_available_ns"] == 340


def test_complete_target_without_support_availability_is_masked():
    rows = [
        _row(1, available_ns=None),
        _row(2, available_ns=None),
    ]
    samples = _build(
        rows,
        {1: _accounting(1, available_ns=None), 2: _accounting(2, available_ns=None)},
        {},
    )
    assert samples[0]["target_status"] == "masked"
    assert samples[0]["target_value"] is None
    assert samples[0]["target_maturity_available_ns"] is None


def test_only_bilaterally_witnessed_skip_can_prove_exact_zero():
    witnessed = _row(
        2,
        "skipped",
        available_ns=250,
        disposition="block_absent_confirmed",
        enumeration_relation="unlisted",
        enumeration_status="complete",
        transport_status="captured_null_result",
        lineage_status="linked",
        proof_available_ns=300,
        reason="bilateral_null_and_parent_chain",
    )
    sample = _build(
        [_row(1), witnessed],
        {1: _accounting(1), 2: _accounting(2)},
        {},
    )[0]
    assert sample["target_status"] == "exact"
    assert sample["target_value"] == 0
    assert sample["target_available_ns"] == 300
    assert sample["target_maturity_available_ns"] == 300
    loss = slot_targets.experience_loss_adapter(sample)
    assert loss["exact_mask"] is True
    assert loss["exact_target"] == 0
    unqualified = _row(
        2,
        "unknown",
        disposition="block_presence_unresolved",
        enumeration_relation="unlisted",
        enumeration_status="complete",
        transport_status="captured_null_result",
        lineage_status="unlinked",
        proof_available_ns=300,
        reason="not_listed_without_complete_enumeration",
    )
    sample = _build(
        [_row(1), unqualified],
        {1: _accounting(1), 2: _accounting(2)},
        {},
    )[0]
    assert sample["target_status"] == "masked"
    assert sample["target_value"] is None


def test_logical_targets_publish_as_shared_v3_coordinate_samples(tmp_path):
    from memecoin_experience import coordinate_samples
    from memecoin_experience.schema import checked_samples
    from memecoin_experience.storage import Snapshot, build

    logical = _build(
        [_row(454538109), _row(454538110)],
        {
            454538109: _accounting(454538109, 23),
            454538110: _accounting(454538110, 87),
        },
        {454538109: 23, 454538110: 87},
        {454538109: 454538109 * 100 + 40, 454538110: 454538110 * 100 + 40},
    )
    schema, rows = slot_targets.coordinate_samples_from_targets(
        logical,
        source_pin=SOURCE_PIN,
        reconciliation_pin=RECONCILIATION_PIN,
        facts_by_slot={
            454538109: {"count": 23, "available_ns": 454538109 * 100 + 40},
            454538110: {"count": 87, "available_ns": 454538110 * 100 + 40},
        },
        support_by_slot={
            454538109: _row(454538109),
            454538110: _row(454538110),
        },
        cutoff_ns=10**12,
    )
    assert list(checked_samples(rows, schema, 10**12)) == rows
    assert rows[0]["decision_ns"] is None
    assert rows[0]["origin"]["coordinate"] == 454538109
    assert rows[0]["values"] == [[23.0]]
    assert rows[0]["targets"] == [87]
    assert rows[1]["target_status"] == ["pending"]
    assert rows[1]["targets"] == [None]

    result = build(
        tmp_path / "experience",
        rows,
        sources=[
            dict(kind="synthetic_reference_audit", pin="d" * 64),
            dict(kind="synthetic_observation_source", pin=SOURCE_PIN),
        ],
        cutoff=10**12,
        schema=schema,
        role="research_evaluation",
        chunk=1,
    )
    snapshot = Snapshot(tmp_path / "experience", result["snapshot"])
    assert snapshot.manifest["binding"]["role"] == "research_evaluation"
    plan = coordinate_samples.make_split_plan(
        schema["coordinate_domain_pin"],
        {
            "train": {
                "intervals": [[454538109, 454538111]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 10**12,
            },
            "validation": {
                "intervals": [],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 10**12,
            },
            "test": {
                "intervals": [],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 10**12,
            },
        },
    )
    eligibility = [coordinate_samples.eligibility(row, schema, plan, "train") for row in rows]
    assert sum(any(item["exact_mask"]) for item in eligibility) == 1
    assert all(row["fit_eligible"] is False for row in rows)


@pytest.mark.parametrize("transport", ["failed", "stopped"])
def test_independent_absence_proof_can_exact_zero_despite_failed_or_stopped_transport(transport):
    absent = _row(
        2,
        transport,
        available_ns=250,
        disposition="block_absent_confirmed",
        enumeration_relation="unlisted",
        enumeration_status="complete",
        transport_status=transport,
        lineage_status="linked",
        proof_available_ns=400,
        reason=f"getblock_{transport}",
    )
    samples = _build(
        [_row(1), absent],
        {1: _accounting(1), 2: _accounting(2)},
        {},
    )
    sample = samples[0]
    assert sample["target_status"] == "exact"
    assert sample["target_value"] == 0
    assert sample["target_available_ns"] == 400
    assert sample["target_reasons"] == [
        {
            "slot": 2,
            "status": transport,
            "reason": f"getblock_{transport}",
            "transport_status": transport,
            "enumeration_relation": "unlisted",
            "enumeration_status": "complete",
            "lineage_status": "linked",
        }
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"enumeration_status": "failed"},
        {"enumeration_relation": "listed"},
        {"lineage_status": "conflict"},
        {"disposition": "block_present"},
        {"proof_available_ns": None},
    ],
)
def test_incomplete_or_conflicting_absence_proof_stays_masked(overrides):
    row_overrides = dict(overrides)
    status = row_overrides.pop("status", "failed")
    fields = {
        "available_ns": 250,
        "disposition": "block_absent_confirmed",
        "enumeration_relation": "unlisted",
        "enumeration_status": "complete",
        "transport_status": "failed",
        "lineage_status": "linked",
        "proof_available_ns": 400,
        "reason": "slot_attempt_failed",
    }
    fields.update(row_overrides)
    absent = _row(2, status, **fields)
    samples = _build([_row(1), absent], {1: _accounting(1), 2: _accounting(2)}, {})
    assert samples[0]["target_status"] == "masked"
    assert samples[0]["target_value"] is None


def test_mixed_absent_and_complete_slots_produce_exact_window_count():
    absent = _row(
        2,
        "failed",
        available_ns=250,
        disposition="block_absent_confirmed",
        enumeration_relation="unlisted",
        enumeration_status="complete",
        transport_status="failed",
        lineage_status="linked",
        proof_available_ns=400,
        reason="slot_attempt_failed",
    )
    samples = slot_targets.build_logical_samples(
        interval_start_slot=1,
        interval_end_slot=4,
        support_rows=[_row(1), absent, _row(3)],
        slot_accounting={
            "1": _accounting(1),
            "2": _accounting(2),
            "3": _accounting(3, 4),
        },
        fact_counts={3: 4},
        fact_available_ns={3: 340},
        reconciliation_pin=RECONCILIATION_PIN,
        observation_source_pin=SOURCE_PIN,
        horizons=(2,),
    )
    sample = samples[0]
    assert sample["target_status"] == "exact"
    assert sample["target_value"] == 4
    assert sample["target_available_ns"] == 400
    assert sample["target_reasons"][0]["status"] == "failed"


@pytest.mark.parametrize("status", ["failed", "stopped", "unknown", "unsupported"])
def test_unresolved_status_and_reason_are_preserved_without_zero(status):
    row = _row(
        2,
        status,
        available_ns=None,
        reason=f"provider_{status}",
        transport_status=status,
    )
    accounting = _accounting(2, available_ns=None)
    samples = _build([_row(1), row], {1: _accounting(1), 2: accounting}, {})
    assert samples[0]["target_status"] == "masked"
    assert samples[0]["target_reasons"][0]["status"] == status
    assert samples[0]["target_reasons"][0]["reason"] == f"provider_{status}"


def test_zero_lower_bound_does_not_supply_loss_and_accounting_must_reconcile():
    with pytest.raises(ValueError, match="bound_adapter"):
        slot_targets.experience_loss_adapter(
            {"target_status": "lower_bound", "target_value": None, "lower_bound": 0}
        )
    with pytest.raises(ValueError, match="fact_reconciliation"):
        _build([_row(1), _row(2)], {1: _accounting(1), 2: _accounting(2, 3)}, {})


def test_contract_pin_binds_native_domain_and_retrospective_semantics():
    assert slot_targets.CONTRACT_PIN == digest(slot_targets.CONTRACT)
    assert slot_targets.CONTRACT["coordinate_domain"]["unit"] == "slot"
    assert slot_targets.CONTRACT["forecast_semantics"].startswith("retrospective_")
    assert slot_targets.CONTRACT["role"] == "exploratory_no_source_role"
