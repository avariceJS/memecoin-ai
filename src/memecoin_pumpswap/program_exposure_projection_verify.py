"""Independent verifier for the archival Pair64/c3 slot exposure projection."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from memecoin_experience import observations
from memecoin_experience.storage import canonical, digest, file_hash, safe_path

from . import program_exposure_projection as projection

_PROCESS = "pair64-c3-pump-amm-slot-exposure-v1"
_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
_ENTITY = "solana/program/pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
_STREAM = "pump_amm_program_slot_exposure"
_DATASET_IDS = (
    "pair64",
    "c3:20261010T050000Z",
    "c3:20261010T060000Z",
    "c3:20261010T070000Z",
    "c3:20261010T080000Z",
    "c3:20261010T090000Z",
    "c3:20261010T100000Z",
)
_SLOT_LEDGER_FIELDS = (
    "slot",
    "blockhash",
    "event_time_unix_seconds",
    "full_request_json_sha256",
    "signature_request_json_sha256",
    "full_receipt_ns",
    "signature_receipt_ns",
    "availability_ns",
    "full_response_sha256",
    "full_stored_sha256",
    "signature_response_sha256",
    "signature_stored_sha256",
    "provider_signature_count",
    "provider_full_transaction_count",
    "transaction_count",
    "normalized_transactions",
    "failed_transactions",
    "unsupported_normalization",
    "outer_instruction_references",
    "inner_instruction_references",
    "program_count",
    "program_exposures",
)
_FLAGS = {
    "production_eligible": False,
    "economic_qualification": False,
    "training_admission": False,
    "source_completeness": False,
    "global_zero_claim": False,
    "global_distinct_transaction_claim": False,
    "market_or_pool_identity_claim": False,
}


def _expected_schema() -> dict:
    domain = {
        "version": "typed-coordinate-domain-v1",
        "name": "solana_finalized_slot",
        "network": "solana-mainnet",
        "kind": "finalized_slot",
        "unit": "slot",
    }
    return {
        "version": "market-observations-v2",
        "role": "research_evaluation",
        "availability": "historical_replay",
        "streams": {
            "pump_amm_program_slot_exposure": {
                "process": _PROCESS,
                "features": [
                    {
                        "name": "captured_unique_referencing_transactions",
                        "unit": "transactions",
                        "modality": "structural_program_exposure_count",
                    }
                ],
                "coordinate_domain": domain,
                "coordinate_domain_pin": digest(domain),
                "identity_rule": {
                    "version": "observation-identity-rule-v1",
                    "name": "sha256_domain_network_process_program_slot_blockhash_v1",
                },
                "position_rule": {
                    "version": "observation-position-rule-v1",
                    "name": "slot_aggregate_marker_zero_v1",
                },
                "event_order_rule": {
                    "version": "observation-event-order-rule-v1",
                    "name": "coordinate_ascending_slot_then_id_v1",
                },
            }
        },
    }


def _field_row(value: object) -> dict:
    if isinstance(value, list):
        if len(value) != len(_SLOT_LEDGER_FIELDS):
            raise ValueError("exposure_projection_verify_row_width")
        return dict(zip(_SLOT_LEDGER_FIELDS, value, strict=True))
    if isinstance(value, dict) and set(value) == set(_SLOT_LEDGER_FIELDS):
        return value
    raise ValueError("exposure_projection_verify_row_shape")


def _reproducible_content_sha256(ledger: dict) -> str:
    payload = dict(ledger)
    for key in (
        "decision_time_ns",
        "decision_time_provenance",
        "replay_measurements",
        "reproducible_content_sha256",
    ):
        payload.pop(key, None)
    return hashlib.sha256(canonical(payload)).hexdigest()


def _utc_iso(ns: int) -> str:
    seconds, nanos = divmod(ns, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{nanos:09d}Z"


def _manifest_specs(root: Path, ledger: dict) -> list[dict]:
    """Resolve expected manifest path/hash pairs from the frozen ledger alone."""
    specs = []
    values_by_path = {}
    for dataset in ledger.get("datasets", []):
        lineage = dataset.get("lineage", {})
        dataset_id = dataset.get("dataset_id")
        plan_path = lineage.get("plan_path")
        if not isinstance(plan_path, str):
            raise ValueError("exposure_projection_verify_manifest_binding")
        if dataset_id == "pair64":
            base = Path(plan_path).parent
            candidates = [
                (plan_path, lineage.get("plan_sha256")),
                (str(base / "summary.json"), lineage.get("summary_sha256")),
                (str(base.parent / "epoch-plan.json"), lineage.get("epoch_plan_sha256")),
                (str(base.parent / "epoch-summary.json"), lineage.get("epoch_summary_sha256")),
            ]
        else:
            campaign_path = lineage.get("campaign_manifest_path")
            candidates = [
                (campaign_path, lineage.get("campaign_manifest_sha256")),
                (plan_path, lineage.get("plan_sha256")),
                (
                    str(Path(plan_path).parent / "epoch-summary.json"),
                    lineage.get("summary_sha256"),
                ),
            ]
        for relative, expected in candidates:
            if (
                not isinstance(relative, str)
                or not relative
                or Path(relative).is_absolute()
                or "\\" in relative
                or any(part in {"", ".", ".."} for part in relative.split("/"))
                or not isinstance(expected, str)
                or len(expected) != 64
            ):
                raise ValueError("exposure_projection_verify_manifest_binding")
            path = safe_path(root / relative)
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError("exposure_projection_verify_manifest_missing")
            if file_hash(path) != expected:
                raise ValueError("exposure_projection_verify_manifest_hash")
            try:
                values_by_path[relative] = json.loads(path.read_bytes())
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError("exposure_projection_verify_manifest_json") from None
            specs.append({"dataset_id": dataset_id, "path": relative, "sha256": expected})
        selected_slots = [
            _field_row(row)["slot"] for row in dataset.get("selected_slot_ledger", [])
        ]
        if dataset_id == "pair64":
            plan = values_by_path[plan_path]
            summary_path = str(Path(plan_path).parent / "summary.json")
            epoch_plan_path = str(Path(plan_path).parent.parent / "epoch-plan.json")
            epoch_summary_path = str(Path(plan_path).parent.parent / "epoch-summary.json")
            summary = values_by_path[summary_path]
            epoch_plan = values_by_path[epoch_plan_path]
            epoch_summary = values_by_path[epoch_summary_path]
            if (
                plan.get("schema_version") != 1
                or plan.get("full_sample_slots") != selected_slots
                or plan.get("signature_sample_slots") != selected_slots
                or summary.get("plan_sha256") != lineage.get("plan_sha256")
                or epoch_plan.get("epoch_id") != dataset.get("bucket_id")
                or epoch_summary.get("status") != "provider_window_attempted"
            ):
                raise ValueError("exposure_projection_verify_pair64_manifest_binding")
        else:
            campaign_path = lineage["campaign_manifest_path"]
            plan = values_by_path[plan_path]
            campaign = values_by_path[campaign_path]
            summary_path = str(Path(plan_path).parent / "epoch-summary.json")
            summary = values_by_path[summary_path]
            campaign_digest = digest(campaign)
            if (
                campaign_digest != lineage.get("campaign_manifest_canonical_sha256")
                or campaign.get("schema_version") != 3
                or plan.get("schedule_id") != campaign.get("schedule_id")
                or plan.get("schedule_sha256") != campaign_digest
                or plan.get("schema_version") != 2
                or plan.get("epoch_id") != dataset.get("bucket_id")
                or summary.get("schema_version") != 2
                or summary.get("epoch_id") != dataset.get("bucket_id")
                or summary.get("status") != "frame_complete"
                or summary.get("selected_slots") != selected_slots
            ):
                raise ValueError("exposure_projection_verify_c3_manifest_binding")
    return specs


def _verify_source_pins(root: Path, ledger: dict) -> list[dict]:
    """Recheck current implementation pins and exact historical source revisions."""
    pins = ledger.get("source_pins", {})
    for path_key, hash_key in (
        ("decoder_path", "decoder_sha256"),
        ("normalizer_path", "normalizer_sha256"),
        ("pump_idl_schema_path", "pump_idl_schema_file_sha256"),
        ("tool_path", "tool_sha256"),
    ):
        relative, expected = pins.get(path_key), pins.get(hash_key)
        if path_key == "tool_path" and relative is None:
            relative = "tools/brain_senses_program_exposure_ledger_v1.py"
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or not isinstance(expected, str)
            or len(expected) != 64
        ):
            raise ValueError("exposure_projection_verify_source_pin_shape")
        path = safe_path(root / relative)
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("exposure_projection_verify_source_missing")
        if file_hash(path) != expected:
            raise ValueError("exposure_projection_verify_source_hash")
    if pins.get("pump_amm_program_id") != _PROGRAM_ID:
        raise ValueError("exposure_projection_verify_program_pin")
    from memecoin_pumpswap.schema import SCHEMA

    provenance = SCHEMA.get("provenance", {})
    if (
        SCHEMA.get("address") != _PROGRAM_ID
        or provenance.get("commit") != pins.get("pump_idl_commit")
        or provenance.get("sha256") != pins.get("pump_idl_sha256")
    ):
        raise ValueError("exposure_projection_verify_pump_idl_binding")
    for dataset in ledger.get("datasets", []):
        for source in dataset.get("lineage", {}).get("epoch_source_pins", []):
            path, commit, expected = source.get("path"), source.get("commit"), source.get("sha256")
            if (
                not isinstance(path, str)
                or not isinstance(commit, str)
                or len(commit) != 40
                or not isinstance(expected, str)
                or len(expected) != 64
                or any(char not in "0123456789abcdef" for char in commit)
            ):
                raise ValueError("exposure_projection_verify_epoch_source_pin")
            result = subprocess.run(
                ["git", "show", f"{commit}:{path}"], cwd=root, capture_output=True, check=False
            )
            if result.returncode != 0 or hashlib.sha256(result.stdout).hexdigest() != expected:
                raise ValueError("exposure_projection_verify_epoch_source_hash")
    return _manifest_specs(root, ledger)


def _expected_rows(ledger: dict, ledger_sha: str) -> tuple[list[dict], list[dict], dict]:
    """Rebuild census and positives directly from the compact ledgers, not writer output."""
    expected_census = []
    selected_by_dataset = set()
    by_slot = {}
    decision_time = ledger.get("decision_time_ns")
    if type(decision_time) is not int or decision_time <= 0:
        raise ValueError("exposure_projection_verify_decision_time")
    max_receipt = 0
    positive = zeros = dataset_count_total = 0
    dataset_counts = {}
    for dataset in ledger.get("datasets", []):
        dataset_id = dataset.get("dataset_id")
        if dataset_id not in _DATASET_IDS:
            raise ValueError("exposure_projection_verify_unexpected_dataset")
        fields = dataset.get("selected_slot_ledger_fields")
        if fields != list(_SLOT_LEDGER_FIELDS):
            raise ValueError("exposure_projection_verify_field_binding")
        table = dataset.get("program_id_table")
        if not isinstance(table, list) or table != sorted(table):
            raise ValueError("exposure_projection_verify_program_table")
        manifest_pins = [
            {"path": key, "sha256": value}
            for key, value in dataset.get("lineage", {}).items()
            if key.endswith("_sha256") and isinstance(value, str) and len(value) == 64
        ]
        manifest_pins.sort(key=lambda pin: (pin["path"], pin["sha256"]))
        last_slot = -1
        dataset_positive = dataset_zero = 0
        dataset_slots = 0
        dataset_sum = 0
        for packed in dataset.get("selected_slot_ledger", []):
            row = _field_row(packed)
            slot = row["slot"]
            if type(slot) is not int or slot <= last_slot:
                raise ValueError("exposure_projection_verify_slot_order")
            last_slot = slot
            if row["availability_ns"] is not None:
                raise ValueError("exposure_projection_verify_historical_time")
            if (
                type(row.get("full_receipt_ns")) is not int
                or type(row.get("signature_receipt_ns")) is not int
                or row["full_receipt_ns"] <= 0
                or row["signature_receipt_ns"] <= 0
            ):
                raise ValueError("exposure_projection_verify_receipt_time")
            max_receipt = max(max_receipt, row["full_receipt_ns"], row["signature_receipt_ns"])
            count = 0
            for program_index, value in row["program_exposures"]:
                if (
                    type(program_index) is not int
                    or type(value) is not int
                    or not 0 <= program_index < len(table)
                    or value <= 0
                ):
                    raise ValueError("exposure_projection_verify_program_exposure")
                if table[program_index] == _PROGRAM_ID:
                    count = value
            if count:
                positive += 1
                dataset_positive += 1
                dataset_count_total += count
                dataset_sum += count
            else:
                zeros += 1
                dataset_zero += 1
            identity = (row["blockhash"], count)
            prior = by_slot.get(slot)
            if prior is not None and prior["identity"] != identity:
                raise ValueError("exposure_projection_verify_conflicting_slot")
            if prior is None:
                prior = by_slot[slot] = {
                    "identity": identity,
                    "block_time": row["event_time_unix_seconds"],
                    "evidence": set(),
                    "rows": [],
                }
            elif prior["block_time"] != row["event_time_unix_seconds"]:
                raise ValueError("exposure_projection_verify_conflicting_time")
            evidence = {
                ledger_sha,
                *[pin["sha256"] for pin in manifest_pins],
                row["full_request_json_sha256"],
                row["signature_request_json_sha256"],
                row["full_response_sha256"],
                row["full_stored_sha256"],
                row["signature_response_sha256"],
                row["signature_stored_sha256"],
            }
            prior["evidence"].update(evidence)
            prior["rows"].append((dataset, row, count, sorted(evidence)))
            selected_by_dataset.add((dataset_id, slot))
            dataset_slots += 1
            expected_census.append(
                {
                    "dataset_id": dataset_id,
                    "campaign_id": dataset.get("campaign_id"),
                    "bucket_id": dataset.get("bucket_id"),
                    "target_program_id": _PROGRAM_ID,
                    "dataset_status": dataset.get("status"),
                    "dataset_transaction_status_accounting": {
                        key: dataset.get("counts", {}).get(key)
                        for key in (
                            "success_transactions",
                            "failed_transactions",
                            "unknown_status_transactions",
                            "normalized_transactions",
                            "unsupported_normalization",
                        )
                    },
                    "target_program_table_index": (
                        table.index(_PROGRAM_ID) if _PROGRAM_ID in table else None
                    ),
                    "captured_unique_referencing_transactions": count,
                    "status": "captured_local_positive" if count else "captured_local_zero",
                    "source_receipt_pins": sorted(evidence),
                    "source_manifest_pins": manifest_pins,
                    **{key: value for key, value in row.items() if key != "program_exposures"},
                }
            )
        if dataset.get("selected_slots") != dataset_slots:
            raise ValueError("exposure_projection_verify_selected_slot_count")
        target_summary = next(
            (item for item in dataset.get("programs", []) if item.get("program_id") == _PROGRAM_ID),
            None,
        )
        target_total = target_summary.get("unique_transactions", 0) if target_summary else 0
        if dataset_sum != target_total:
            raise ValueError("exposure_projection_verify_dataset_count")
        dataset_counts[dataset_id] = {
            "selected_slots": dataset_slots,
            "positive_rows": dataset_positive,
            "captured_local_zeros": dataset_zero,
            "sum_of_per_slot_counts": dataset_sum,
        }

    # The writer adds dispositions after cross-dataset reconciliation. Rebuild them
    # independently by assigning one canonical positive event per unique slot.
    for row in expected_census:
        slot = row["slot"]
        count = row["captured_unique_referencing_transactions"]
        matches = by_slot[slot]["rows"]
        # Dataset order is stable in the frozen ledger; the first matching dataset emits.
        index = next(
            i
            for i, (dataset, source_row, _count, _pins) in enumerate(matches)
            if dataset["dataset_id"] == row["dataset_id"] and source_row["slot"] == slot
        )
        row["projection_disposition"] = (
            "census_only_zero"
            if count == 0
            else "emitted_positive"
            if index == 0
            else "deduplicated_identical_slot_blockhash_count"
        )
    events = []
    for slot, value in sorted(by_slot.items()):
        blockhash, count = value["identity"]
        if count == 0:
            continue
        events.append(
            {
                "kind": "event",
                "entity": _ENTITY,
                "stream": _STREAM,
                "id": hashlib.sha256(
                    canonical(
                        {
                            "domain": "market-observations-v2-event-identity-v1",
                            "network": "solana-mainnet",
                            "process": _PROCESS,
                            "program_id": _PROGRAM_ID,
                            "slot": slot,
                            "blockhash": blockhash,
                        }
                    )
                ).hexdigest(),
                "coordinate": slot,
                "position": [0],
                "coordinate_domain_pin": digest(
                    {
                        "version": "typed-coordinate-domain-v1",
                        "name": "solana_finalized_slot",
                        "network": "solana-mainnet",
                        "kind": "finalized_slot",
                        "unit": "slot",
                    }
                ),
                "available_ns": None,
                "evidence": sorted(value["evidence"]),
                "values": [count],
            }
        )
    counts = dict(
        selected_slot_observations=len(expected_census),
        positive_rows=positive,
        captured_local_zeros=zeros,
        positive_dataset_observations=sum(
            row["captured_unique_referencing_transactions"] > 0 for row in expected_census
        ),
        captured_local_zero_observations=sum(
            row["captured_unique_referencing_transactions"] == 0 for row in expected_census
        ),
        sum_of_per_slot_counts=sum(value["identity"][1] for value in by_slot.values()),
        sum_of_per_dataset_observation_counts=dataset_count_total,
        distinct_slot_count=len(by_slot),
        distinct_selected_dataset_slots=len(selected_by_dataset),
    )
    return (
        expected_census,
        events,
        dict(
            counts=counts,
            dataset_counts=dataset_counts,
            decision_time_ns=decision_time,
            max_receipt_ns=max_receipt,
        ),
    )


def _validate_receipt_metadata(
    receipt: dict,
    ledger: dict,
    ledger_sha: str,
    expected_manifest_pins: list[dict],
    independent: dict,
    census_sha256: str,
) -> None:
    known_ns = receipt.get("projection_known_ns")
    if (
        receipt.get("schema") != "pair64_c3_pump_amm_slot_exposure_projection_receipt_v1"
        or receipt.get("process_name") != _PROCESS
        or receipt.get("input_ledger_sha256") != ledger_sha
        or receipt.get("source_pins") != ledger.get("source_pins")
        or receipt.get("reproducible_content_sha256") != _reproducible_content_sha256(ledger)
        or receipt.get("decision_time_ns") != independent["decision_time_ns"]
        or receipt.get("max_input_receipt_ns") != independent["max_receipt_ns"]
        or type(known_ns) is not int
        or known_ns < max(independent["decision_time_ns"], independent["max_receipt_ns"])
        or receipt.get("projection_known_utc") != _utc_iso(known_ns)
        or receipt.get("census_sidecar_sha256") != census_sha256
        or receipt.get("source_role") != "research_evaluation"
        or receipt.get("source_availability") != "historical_replay"
        or receipt.get("sum_of_per_slot_counts_is_global_distinct") is not False
        or receipt.get("flags") != _FLAGS
        or receipt.get("event_schema") != _expected_schema()
        or receipt.get("accounting") != independent["counts"]
        or receipt.get("per_dataset_accounting") != independent["dataset_counts"]
        or receipt.get("emitted_event_count") != 603
        or receipt.get("metadata_manifest_pins") != expected_manifest_pins
    ):
        raise ValueError("exposure_projection_verify_receipt")


def _validate_census_metadata(
    census: dict,
    ledger: dict,
    ledger_sha: str,
    receipt: dict,
    expected_census: list[dict],
    expected_manifest_pins: list[dict],
    independent: dict,
) -> None:
    if (
        census.get("schema") != "pair64_c3_pump_amm_slot_exposure_census_v1"
        or census.get("input_ledger_sha256") != ledger_sha
        or census.get("reproducible_content_sha256") != _reproducible_content_sha256(ledger)
        or census.get("projection_known_ns") != receipt["projection_known_ns"]
        or census.get("projection_known_utc") != receipt["projection_known_utc"]
        or census.get("rows") != expected_census
        or census.get("accounting") != independent["counts"]
        or census.get("per_dataset_accounting") != independent["dataset_counts"]
        or census.get("input_metadata_manifest_pins") != expected_manifest_pins
        or census.get("flags") != _FLAGS
    ):
        raise ValueError("exposure_projection_verify_census")


def _expected_source_binding(
    ledger_sha: str,
    manifest_pins: list[dict],
    module_sha256: str,
    verifier_sha256: str,
) -> list[dict]:
    return [
        {"name": "frozen_program_exposure_ledger", "pin": ledger_sha},
        *[
            {
                "dataset_id": entry["dataset_id"],
                "name": "sealed_dataset_metadata_manifest",
                "pin": entry["sha256"],
            }
            for entry in manifest_pins
        ],
        {
            "module_sha256": module_sha256,
            "name": "projection_implementation",
            "verifier_sha256": verifier_sha256,
        },
    ]


def _validate_source_binding(actual: list[dict], expected: list[dict]) -> None:
    if actual != expected:
        raise ValueError("exposure_projection_verify_source_binding")


def verify_projection(
    root: Path,
    output: Path,
    *,
    ledger: dict | None = None,
    ledger_sha256: str | None = None,
) -> dict:
    root, output = safe_path(root), safe_path(output)
    work_root = safe_path(root / "work")
    if output == work_root or not output.resolve().is_relative_to(work_root.resolve()):
        raise ValueError("exposure_projection_output_must_be_under_work")
    ledger_path = safe_path(root / projection.LEDGER_RELATIVE_PATH)
    if ledger is None:
        ledger_sha256 = file_hash(ledger_path)
        ledger = json.loads(ledger_path.read_text())
    if ledger_sha256 != projection.LEDGER_SHA256 or file_hash(ledger_path) != ledger_sha256:
        raise ValueError("exposure_projection_verify_ledger_pin")
    expected_manifest_pins = _verify_source_pins(root, ledger)
    if ledger.get("reproducible_content_sha256") != _reproducible_content_sha256(ledger):
        raise ValueError("exposure_projection_verify_ledger_content_digest")
    expected_census, expected_events, independent = _expected_rows(ledger, ledger_sha256)
    if (
        independent["counts"]["selected_slot_observations"] != 608
        or independent["counts"]["positive_rows"] != 603
        or independent["counts"]["captured_local_zeros"] != 5
        or independent["counts"]["sum_of_per_slot_counts"] != 44_285
        or independent["counts"]["distinct_slot_count"] != 608
    ):
        raise ValueError("exposure_projection_verify_reconciliation")

    expected_files = {"census.json", "receipt.json", "market-observations-v2"}
    if {path.name for path in output.iterdir()} != expected_files:
        raise ValueError("exposure_projection_verify_output_inventory")
    receipt_path, census_path = output / "receipt.json", output / "census.json"
    receipt = json.loads(receipt_path.read_text())
    census = json.loads(census_path.read_text())
    _validate_receipt_metadata(
        receipt,
        ledger,
        ledger_sha256,
        expected_manifest_pins,
        independent,
        file_hash(census_path),
    )
    _validate_census_metadata(
        census,
        ledger,
        ledger_sha256,
        receipt,
        expected_census,
        expected_manifest_pins,
        independent,
    )

    source = observations.Source(
        output / "market-observations-v2",
        receipt.get("market_observations_source_pin"),
        role="research_evaluation",
    )
    schema = _expected_schema()
    if (
        source.schema != schema
        or source.manifest["binding"]["cutoff_ns"] != receipt["projection_known_ns"]
        or source.manifest.get("production_eligible") is not False
    ):
        raise ValueError("exposure_projection_verify_schema")
    _validate_source_binding(
        source.manifest["binding"]["sources"],
        _expected_source_binding(
            ledger_sha256,
            expected_manifest_pins,
            file_hash(Path(projection.__file__)),
            file_hash(Path(__file__)),
        ),
    )
    # Slot coordinates are not UTC. Prove indexing rejects before opening/creating a DB.
    with tempfile.TemporaryDirectory(prefix="slot-projection-index-check-") as temp_dir:
        index_path = Path(temp_dir) / "must-not-exist.sqlite"
        try:
            source.index(index_path)
        except ValueError as exc:
            if str(exc) != "observation_non_utc_coordinate_index":
                raise
        else:
            raise ValueError("exposure_projection_verify_slot_index_unexpectedly_supported")
        if index_path.exists():
            raise ValueError("exposure_projection_verify_slot_index_created")
    # This is event-only archival structure: no enrollment/support facts or sparse rows.
    actual_events = list(source.rows())
    expected_events = [
        dict(event, available_ns=receipt["projection_known_ns"]) for event in expected_events
    ]
    if actual_events != expected_events:
        raise ValueError("exposure_projection_verify_event_equality")
    if any(row.get("kind") != "event" for row in actual_events):
        raise ValueError("exposure_projection_verify_non_event_fact")
    if len(actual_events) != 603 or len({row["coordinate"] for row in actual_events}) != 603:
        raise ValueError("exposure_projection_verify_event_count")
    if any(
        row["entity"] != _ENTITY
        or row["stream"] != _STREAM
        or row["position"] != [0]
        or row["values"][0] <= 0
        for row in actual_events
    ):
        raise ValueError("exposure_projection_verify_event_semantics")
    return receipt
