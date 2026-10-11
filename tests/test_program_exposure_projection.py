from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from memecoin_experience.observations import Source
from memecoin_experience.storage import digest, file_hash
from memecoin_pumpswap import program_exposure_projection as projection
from memecoin_pumpswap.program_exposure_projection_verify import (
    _FLAGS,
    _expected_rows,
    _expected_schema,
    _expected_source_binding,
    _manifest_specs,
    _reproducible_content_sha256,
    _utc_iso,
    _validate_census_metadata,
    _validate_receipt_metadata,
    _validate_source_binding,
)

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / projection.LEDGER_RELATIVE_PATH


def _ledger() -> dict:
    assert file_hash(LEDGER_PATH) == projection.LEDGER_SHA256
    return json.loads(LEDGER_PATH.read_text())


def _rehash(ledger: dict) -> None:
    ledger["reproducible_content_sha256"] = projection._content_digest(ledger)


def test_frozen_ledger_reconciles_and_identity_excludes_count() -> None:
    ledger = _ledger()
    census, accounting = projection._validate_ledger(ledger, projection.LEDGER_SHA256, frozen=True)
    assert len(census) == 608
    assert accounting["counts"] == {
        "selected_slot_observations": 608,
        "positive_dataset_observations": 603,
        "captured_local_zero_observations": 5,
        "positive_rows": 603,
        "captured_local_zeros": 5,
        "sum_of_per_slot_counts": 44_285,
        "sum_of_per_dataset_observation_counts": 44_285,
        "distinct_slot_count": 608,
        "distinct_selected_dataset_slots": 608,
    }
    slot, record = next(
        (slot, record) for slot, record in accounting["grouped"].items() if record["count"] > 0
    )
    ledger_hash = hashlib.sha256(b"unit-test-ledger-pin").hexdigest()
    first = projection._event_rows({slot: record}, None, ledger_hash)[0]
    changed_payload = dict(record, count=record["count"] + 1)
    second = projection._event_rows({slot: changed_payload}, None, ledger_hash)[0]
    assert first["id"] == second["id"]
    assert first["values"] != second["values"]


def test_omitted_row_fails_closed() -> None:
    ledger = _ledger()
    ledger = copy.deepcopy(ledger)
    dataset = ledger["datasets"][0]
    dataset["selected_slot_ledger"].pop()
    dataset["selected_slots"] -= 1
    _rehash(ledger)
    with pytest.raises(ValueError):
        projection._validate_ledger(ledger, "0" * 64, frozen=False)


def test_duplicate_slot_fails_closed() -> None:
    ledger = copy.deepcopy(_ledger())
    dataset = ledger["datasets"][0]
    dataset["selected_slot_ledger"].insert(2, copy.deepcopy(dataset["selected_slot_ledger"][1]))
    dataset["selected_slots"] += 1
    _rehash(ledger)
    with pytest.raises(ValueError, match="slot_order_or_duplicate"):
        projection._validate_ledger(ledger, "0" * 64, frozen=False)


def test_conflicting_cross_dataset_blockhash_fails_closed() -> None:
    ledger = copy.deepcopy(_ledger())
    source = ledger["datasets"][0]["selected_slot_ledger"]
    source_dataset = ledger["datasets"][0]
    zero = next(
        row
        for row in source
        if projection._dataset_program_count(source_dataset, projection._slot_entry(row)) == 0
    )
    zero_row = projection._slot_entry(zero)
    target = ledger["datasets"][1]
    row = copy.deepcopy(zero_row)
    row["blockhash"] = "conflicting-blockhash"
    row["program_exposures"] = []
    row["program_count"] = 0
    target_rows = target["selected_slot_ledger"]
    target_rows.insert(
        next(
            (
                index
                for index, current in enumerate(target_rows)
                if projection._slot_entry(current)["slot"] > row["slot"]
            ),
            len(target_rows),
        ),
        [row[key] for key in projection.SLOT_LEDGER_FIELDS],
    )
    target["selected_slots"] += 1
    _rehash(ledger)
    with pytest.raises(ValueError, match="slot_blockhash_conflict"):
        projection._validate_ledger(ledger, "0" * 64, frozen=False)


def test_projection_known_time_must_follow_all_inputs() -> None:
    ledger = _ledger()
    _, accounting = projection._validate_ledger(ledger, projection.LEDGER_SHA256, frozen=True)
    floor = max(accounting["decision_time_ns"], accounting["max_receipt_ns"])
    assert projection._validate_known_time(accounting, floor) == floor
    with pytest.raises(ValueError, match="clock_before_inputs"):
        projection._validate_known_time(accounting, floor - 1)


def test_schema_and_flags_keep_projection_out_of_admission() -> None:
    assert projection.OBSERVATION_SCHEMA["role"] == "research_evaluation"
    assert projection.OBSERVATION_SCHEMA["availability"] == "historical_replay"
    assert all(value is False for value in projection._flags().values())


def test_independent_manifest_verifier_detects_tamper_and_missing_file(tmp_path: Path) -> None:
    campaign = tmp_path / "work" / "fixture" / "schedule-v3.json"
    plan = tmp_path / "work" / "fixture" / "epochs" / "epoch" / "epoch-plan.json"
    summary = plan.parent / "epoch-summary.json"
    campaign.parent.mkdir(parents=True, exist_ok=True)
    plan.parent.mkdir(parents=True, exist_ok=True)
    campaign_value = {"schema_version": 3, "schedule_id": "fixture-schedule"}
    campaign.write_text(json.dumps(campaign_value) + "\n")
    plan_value = {
        "schedule_id": "fixture-schedule",
        "schedule_sha256": digest(campaign_value),
        "schema_version": 2,
        "epoch_id": "fixture-epoch",
    }
    plan.write_text(json.dumps(plan_value) + "\n")
    summary_value = {
        "schema_version": 2,
        "epoch_id": "fixture-epoch",
        "status": "frame_complete",
        "selected_slots": [],
    }
    summary.write_text(json.dumps(summary_value) + "\n")
    ledger = {
        "datasets": [
            {
                "dataset_id": "c3:20261010T050000Z",
                "lineage": {
                    "campaign_manifest_path": "work/fixture/schedule-v3.json",
                    "campaign_manifest_sha256": file_hash(campaign),
                    "campaign_manifest_canonical_sha256": digest(campaign_value),
                    "plan_path": "work/fixture/epochs/epoch/epoch-plan.json",
                    "plan_sha256": file_hash(plan),
                    "summary_sha256": file_hash(summary),
                },
                "bucket_id": "fixture-epoch",
                "selected_slot_ledger": [],
            }
        ]
    }
    pins = _manifest_specs(tmp_path, ledger)
    assert [pin["path"] for pin in pins] == [
        "work/fixture/schedule-v3.json",
        "work/fixture/epochs/epoch/epoch-plan.json",
        "work/fixture/epochs/epoch/epoch-summary.json",
    ]
    campaign.write_text('{"changed":true}\n')
    with pytest.raises(ValueError, match="manifest_hash"):
        _manifest_specs(tmp_path, ledger)
    campaign.write_text(json.dumps(campaign_value) + "\n")
    plan.unlink()
    with pytest.raises(ValueError, match="manifest_missing"):
        _manifest_specs(tmp_path, ledger)
    plan.write_text(json.dumps(plan_value) + "\n")
    summary.write_text(json.dumps({**summary_value, "selected_slots": [123]}) + "\n")
    ledger["datasets"][0]["lineage"]["summary_sha256"] = file_hash(summary)
    with pytest.raises(ValueError, match="c3_manifest_binding"):
        _manifest_specs(tmp_path, ledger)


def test_independent_receipt_and_policy_mutations_fail_closed() -> None:
    ledger = _ledger()
    expected_census, _, independent = _expected_rows(ledger, projection.LEDGER_SHA256)
    manifests = _manifest_specs(ROOT, ledger)
    known_ns = max(independent["decision_time_ns"], independent["max_receipt_ns"])
    census_sha = "a" * 64
    receipt = {
        "schema": "pair64_c3_pump_amm_slot_exposure_projection_receipt_v1",
        "process_name": "pair64-c3-pump-amm-slot-exposure-v1",
        "input_ledger_sha256": projection.LEDGER_SHA256,
        "source_pins": ledger["source_pins"],
        "reproducible_content_sha256": _reproducible_content_sha256(ledger),
        "decision_time_ns": independent["decision_time_ns"],
        "max_input_receipt_ns": independent["max_receipt_ns"],
        "projection_known_ns": known_ns,
        "projection_known_utc": _utc_iso(known_ns),
        "census_sidecar_sha256": census_sha,
        "source_role": "research_evaluation",
        "source_availability": "historical_replay",
        "sum_of_per_slot_counts_is_global_distinct": False,
        "flags": _FLAGS,
        "event_schema": _expected_schema(),
        "accounting": independent["counts"],
        "per_dataset_accounting": independent["dataset_counts"],
        "emitted_event_count": 603,
        "metadata_manifest_pins": manifests,
    }
    _validate_receipt_metadata(
        receipt, ledger, projection.LEDGER_SHA256, manifests, independent, census_sha
    )
    for key, bad_value in (
        ("source_pins", {}),
        ("reproducible_content_sha256", "0" * 64),
        ("decision_time_ns", independent["decision_time_ns"] + 1),
        ("max_input_receipt_ns", independent["max_receipt_ns"] - 1),
        ("per_dataset_accounting", {}),
        ("emitted_event_count", 602),
        ("projection_known_utc", "1970-01-01T00:00:00.000000000Z"),
        ("metadata_manifest_pins", []),
        ("flags", {**_FLAGS, "training_admission": True}),
        (
            "event_schema",
            {**_expected_schema(), "role": "train"},
        ),
    ):
        mutated = copy.deepcopy(receipt)
        mutated[key] = bad_value
        with pytest.raises(ValueError, match="verify_receipt"):
            _validate_receipt_metadata(
                mutated, ledger, projection.LEDGER_SHA256, manifests, independent, census_sha
            )

    census = {
        "schema": "pair64_c3_pump_amm_slot_exposure_census_v1",
        "input_ledger_sha256": projection.LEDGER_SHA256,
        "reproducible_content_sha256": _reproducible_content_sha256(ledger),
        "projection_known_ns": known_ns,
        "projection_known_utc": _utc_iso(known_ns),
        "rows": expected_census,
        "accounting": independent["counts"],
        "per_dataset_accounting": independent["dataset_counts"],
        "input_metadata_manifest_pins": manifests,
        "flags": _FLAGS,
    }
    _validate_census_metadata(
        census, ledger, projection.LEDGER_SHA256, receipt, expected_census, manifests, independent
    )
    for key, bad_value in (
        ("input_metadata_manifest_pins", []),
        ("per_dataset_accounting", {}),
        ("flags", {**_FLAGS, "production_eligible": True}),
    ):
        mutated = copy.deepcopy(census)
        mutated[key] = bad_value
        with pytest.raises(ValueError, match="verify_census"):
            _validate_census_metadata(
                mutated,
                ledger,
                projection.LEDGER_SHA256,
                receipt,
                expected_census,
                manifests,
                independent,
            )


def test_source_binding_requires_exact_ordered_manifest_and_ledger_pins() -> None:
    ledger = _ledger()
    manifests = _manifest_specs(ROOT, ledger)
    output = ROOT / "work/pair64-c3-pump-amm-slot-exposure-v1"
    receipt = json.loads((output / "receipt.json").read_text())
    source = Source(
        output / "market-observations-v2",
        receipt["market_observations_source_pin"],
        role="research_evaluation",
    )
    expected = _expected_source_binding(
        projection.LEDGER_SHA256,
        manifests,
        file_hash(Path(projection.__file__)),
        file_hash(Path(projection.__file__).with_name("program_exposure_projection_verify.py")),
    )
    source_list = copy.deepcopy(source.manifest["binding"]["sources"])
    assert source_list == expected
    outer_pin = source.pin
    assert digest(source.manifest) == outer_pin
    mutated_manifest = copy.deepcopy(source.manifest)
    mutated_manifest["binding"]["sources"].pop(1)
    assert digest(mutated_manifest) != outer_pin
    omitted = mutated_manifest["binding"]["sources"]
    with pytest.raises(ValueError, match="source_binding"):
        _validate_source_binding(omitted, expected)
    assert source.pin == outer_pin
    substituted = copy.deepcopy(expected)
    substituted[0]["pin"] = "0" * 64
    with pytest.raises(ValueError, match="source_binding"):
        _validate_source_binding(substituted, expected)
