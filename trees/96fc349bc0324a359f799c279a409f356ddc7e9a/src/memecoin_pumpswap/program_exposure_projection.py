"""Retrospective slot aggregate from the frozen Pair64/c3 exposure ledger.

This is an archival, receipt-linked projection. It does not reread block data,
claim global zero/support, or create learning-ready sparse samples.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from memecoin_experience import observations
from memecoin_experience.storage import (
    PIN,
    atomic_json,
    canonical,
    digest,
    file_hash,
    lock,
    safe_path,
    sync_dir,
)

PROCESS_NAME = "pair64-c3-pump-amm-slot-exposure-v1"
LEDGER_RELATIVE_PATH = Path(
    "docs/evidence/senses-program-exposure-ledger-v1-pair64-c3-2026-10-10.json"
)
LEDGER_SHA256 = "65002ae53ed3c7c7b25b0073fd0c816824442e3319a2eea1ad0d01cd6b9dd425"
PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
DATASET_IDS = (
    "pair64",
    "c3:20261010T050000Z",
    "c3:20261010T060000Z",
    "c3:20261010T070000Z",
    "c3:20261010T080000Z",
    "c3:20261010T090000Z",
    "c3:20261010T100000Z",
)
EXPECTED_COUNTS = dict(
    selected_slot_observations=608,
    positive_rows=603,
    captured_local_zeros=5,
    sum_of_per_slot_counts=44_285,
)
SLOT_LEDGER_FIELDS = (
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
DOMAIN = dict(
    version="typed-coordinate-domain-v1",
    name="solana_finalized_slot",
    network="solana-mainnet",
    kind="finalized_slot",
    unit="slot",
)
DOMAIN_PIN = digest(DOMAIN)
STREAM = "pump_amm_program_slot_exposure"
ENTITY = f"solana/program/{PROGRAM_ID}"
OBSERVATION_SCHEMA = dict(
    version="market-observations-v2",
    role="research_evaluation",
    availability="historical_replay",
    streams={
        STREAM: dict(
            process=PROCESS_NAME,
            features=[
                dict(
                    name="captured_unique_referencing_transactions",
                    unit="transactions",
                    modality="structural_program_exposure_count",
                )
            ],
            coordinate_domain=DOMAIN,
            coordinate_domain_pin=DOMAIN_PIN,
            identity_rule=dict(
                version="observation-identity-rule-v1",
                name="sha256_domain_network_process_program_slot_blockhash_v1",
            ),
            position_rule=dict(
                version="observation-position-rule-v1",
                name="slot_aggregate_marker_zero_v1",
            ),
            event_order_rule=dict(
                version="observation-event-order-rule-v1",
                name="coordinate_ascending_slot_then_id_v1",
            ),
        )
    },
)


def _is_pin(value: object) -> bool:
    return isinstance(value, str) and PIN.fullmatch(value) is not None


def _root_child(root: Path, relative: object) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or Path(relative).is_absolute()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("exposure_projection_input_path")
    base = safe_path(root)
    path = safe_path(base / relative)
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("exposure_projection_input_path")
    return path


def _validate_pinned_code(root: Path, ledger: dict) -> None:
    pins = ledger.get("source_pins")
    if not isinstance(pins, dict):
        raise ValueError("exposure_projection_source_pins")
    for path_key, hash_key in (
        ("decoder_path", "decoder_sha256"),
        ("normalizer_path", "normalizer_sha256"),
        ("pump_idl_schema_path", "pump_idl_schema_file_sha256"),
    ):
        relative, expected = pins.get(path_key), pins.get(hash_key)
        if not isinstance(relative, str) or not _is_pin(expected):
            raise ValueError("exposure_projection_source_pin_shape")
        path = _root_child(root, relative)
        if not path.is_file() or file_hash(path) != expected:
            raise ValueError("exposure_projection_source_pin_mismatch")
    tool = _root_child(root, "tools/brain_senses_program_exposure_ledger_v1.py")
    if not _is_pin(pins.get("tool_sha256")) or file_hash(tool) != pins["tool_sha256"]:
        raise ValueError("exposure_projection_tool_pin_mismatch")
    if pins.get("pump_amm_program_id") != PROGRAM_ID:
        raise ValueError("exposure_projection_program_source_mismatch")

    # The file pin proves bytes; the imported schema supplies the parsed identity.
    from memecoin_pumpswap.schema import SCHEMA

    if SCHEMA.get("address") != PROGRAM_ID:
        raise ValueError("exposure_projection_program_table_mismatch")
    provenance = SCHEMA.get("provenance", {})
    if provenance.get("commit") != pins.get("pump_idl_commit") or provenance.get(
        "sha256"
    ) != pins.get("pump_idl_sha256"):
        raise ValueError("exposure_projection_idl_pin_mismatch")


def _manifest_pin(root: Path, relative: str, expected: str) -> dict:
    if not isinstance(relative, str) or not _is_pin(expected):
        raise ValueError("exposure_projection_manifest_pin_shape")
    path = _root_child(root, relative)
    if not path.is_file() or file_hash(path) != expected:
        raise ValueError("exposure_projection_manifest_hash_mismatch")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("exposure_projection_manifest_json") from None
    return {"path": relative, "sha256": expected, "value": value}


def _metadata_manifests(root: Path, dataset: dict) -> list[dict]:
    lineage = dataset.get("lineage")
    if not isinstance(lineage, dict):
        raise ValueError("exposure_projection_dataset_lineage")
    dataset_id = dataset.get("dataset_id")
    if dataset_id == "pair64":
        plan = _manifest_pin(root, lineage.get("plan_path"), lineage.get("plan_sha256"))
        base = Path(lineage["plan_path"]).parent
        summary = _manifest_pin(root, str(base / "summary.json"), lineage.get("summary_sha256"))
        epoch_plan = _manifest_pin(
            root, str(base.parent / "epoch-plan.json"), lineage.get("epoch_plan_sha256")
        )
        epoch_summary = _manifest_pin(
            root, str(base.parent / "epoch-summary.json"), lineage.get("epoch_summary_sha256")
        )
        if (
            summary["value"].get("plan_sha256") != lineage["plan_sha256"]
            or plan["value"].get("schema_version") != 1
            or epoch_plan["value"].get("epoch_id") != dataset.get("bucket_id")
        ):
            raise ValueError("exposure_projection_pair64_manifest_binding")
        expected_slots = [
            _slot_entry(row)["slot"] for row in dataset.get("selected_slot_ledger", [])
        ]
        if (
            plan["value"].get("full_sample_slots") != expected_slots
            or plan["value"].get("signature_sample_slots") != expected_slots
        ):
            raise ValueError("exposure_projection_pair64_selected_slots_mismatch")
        return [plan, summary, epoch_plan, epoch_summary]

    if not isinstance(lineage.get("campaign_manifest_path"), str):
        raise ValueError("exposure_projection_c3_manifest_binding")
    campaign = _manifest_pin(
        root, lineage["campaign_manifest_path"], lineage.get("campaign_manifest_sha256")
    )
    if digest(campaign["value"]) != lineage.get("campaign_manifest_canonical_sha256"):
        raise ValueError("exposure_projection_c3_campaign_canonical_hash")
    plan = _manifest_pin(root, lineage.get("plan_path"), lineage.get("plan_sha256"))
    summary = _manifest_pin(
        root,
        str(Path(lineage["plan_path"]).parent / "epoch-summary.json"),
        lineage.get("summary_sha256"),
    )
    expected_slots = [_slot_entry(row)["slot"] for row in dataset.get("selected_slot_ledger", [])]
    if (
        plan["value"].get("schedule_id") != campaign["value"].get("schedule_id")
        or plan["value"].get("schedule_sha256") != lineage.get("campaign_manifest_canonical_sha256")
        or plan["value"].get("epoch_id") != dataset.get("bucket_id")
        or summary["value"].get("epoch_id") != dataset.get("bucket_id")
        or summary["value"].get("status") != "frame_complete"
        or summary["value"].get("selected_slots") != expected_slots
    ):
        raise ValueError("exposure_projection_c3_selected_slots_mismatch")
    return [campaign, plan, summary]


def verify_input_references(root: Path, ledger: dict) -> list[dict]:
    """Verify only the sealed plan/summary manifests and ledger-pinned code files."""
    _validate_pinned_code(root, ledger)
    all_pins = []
    for dataset in ledger["datasets"]:
        all_pins.extend(
            {
                "dataset_id": dataset["dataset_id"],
                "path": manifest["path"],
                "sha256": manifest["sha256"],
            }
            for manifest in _metadata_manifests(root, dataset)
        )
        for source in dataset.get("lineage", {}).get("epoch_source_pins", []):
            if not isinstance(source, dict):
                raise ValueError("exposure_projection_epoch_source_pin")
            path, expected = source.get("path"), source.get("sha256")
            if not isinstance(path, str) or not _is_pin(expected):
                raise ValueError("exposure_projection_epoch_source_pin")
            commit = source.get("commit")
            if (
                not isinstance(commit, str)
                or len(commit) != 40
                or any(char not in "0123456789abcdef" for char in commit)
            ):
                raise ValueError("exposure_projection_epoch_source_commit")
            _root_child(root, path)
            result = subprocess.run(
                ["git", "show", f"{commit}:{path}"],
                cwd=root,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0 or hashlib.sha256(result.stdout).hexdigest() != expected:
                raise ValueError("exposure_projection_epoch_source_hash_mismatch")
    return all_pins


def _slot_entry(raw: object) -> dict:
    if isinstance(raw, list):
        if len(raw) != len(SLOT_LEDGER_FIELDS):
            raise ValueError("exposure_projection_slot_row_width")
        return dict(zip(SLOT_LEDGER_FIELDS, raw, strict=True))
    if isinstance(raw, dict) and set(raw) == set(SLOT_LEDGER_FIELDS):
        return raw
    raise ValueError("exposure_projection_slot_row_shape")


def _content_digest(ledger: dict) -> str:
    payload = dict(ledger)
    for name in (
        "decision_time_ns",
        "decision_time_provenance",
        "replay_measurements",
        "reproducible_content_sha256",
    ):
        payload.pop(name, None)
    return hashlib.sha256(canonical(payload)).hexdigest()


def _dataset_program_count(dataset: dict, row: dict) -> int:
    table = dataset.get("program_id_table")
    if (
        not isinstance(table, list)
        or table != sorted(table)
        or any(not isinstance(value, str) for value in table)
    ):
        raise ValueError("exposure_projection_program_table")
    exposure_list = row.get("program_exposures")
    if not isinstance(exposure_list, list):
        raise ValueError("exposure_projection_program_exposures")
    previous = -1
    counts = {}
    for item in exposure_list:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or type(item[0]) is not int
            or type(item[1]) is not int
            or not 0 <= item[0] < len(table)
            or item[0] <= previous
            or item[1] <= 0
        ):
            raise ValueError("exposure_projection_program_exposure_entry")
        previous = item[0]
        counts[table[item[0]]] = item[1]
    if row.get("program_count") != len(exposure_list):
        raise ValueError("exposure_projection_program_count_mismatch")
    return counts.get(PROGRAM_ID, 0)


def _row_receipt_pins(row: dict, dataset_pins: list[dict], ledger_sha256: str) -> list[str]:
    values = [
        ledger_sha256,
        *[manifest["sha256"] for manifest in dataset_pins],
        row.get("full_request_json_sha256"),
        row.get("signature_request_json_sha256"),
        row.get("full_response_sha256"),
        row.get("full_stored_sha256"),
        row.get("signature_response_sha256"),
        row.get("signature_stored_sha256"),
    ]
    if any(not _is_pin(value) for value in values):
        raise ValueError("exposure_projection_receipt_pin")
    return sorted(set(values))


def _validate_ledger(ledger: dict, ledger_sha256: str, *, frozen: bool) -> tuple[list[dict], dict]:
    if (
        not isinstance(ledger, dict)
        or ledger.get("schema") != "senses_program_exposure_ledger_v1"
        or ledger.get("status") != "complete_structural_exposure_only"
        or ledger.get("scope", {}).get("selected_blocks_only") is not True
        or ledger.get("source_pins", {}).get("pump_amm_program_id") != PROGRAM_ID
        or ledger.get("reproducible_content_sha256") != _content_digest(ledger)
        or len(ledger.get("datasets", [])) != len(DATASET_IDS)
        or tuple(dataset.get("dataset_id") for dataset in ledger["datasets"]) != DATASET_IDS
    ):
        raise ValueError("exposure_projection_ledger_contract")
    if frozen and ledger_sha256 != LEDGER_SHA256:
        raise ValueError("exposure_projection_ledger_hash_mismatch")

    decision_time_ns = ledger.get("decision_time_ns")
    if type(decision_time_ns) is not int or decision_time_ns <= 0:
        raise ValueError("exposure_projection_decision_time")
    expected_slot_ids = set()
    census, grouped, receipt_times = [], {}, [decision_time_ns]
    positive_total = zero_total = dataset_count_total = 0
    dataset_counts = {}
    for dataset in ledger["datasets"]:
        if (
            dataset.get("status") != "complete"
            or dataset.get("selected_slot_ledger_fields") != list(SLOT_LEDGER_FIELDS)
            or dataset.get("selected_slot_ledger_validation")
            != "full_signature_order_check matched for every row"
            or not isinstance(dataset.get("selected_slot_ledger"), list)
            or dataset.get("selected_slots") != len(dataset["selected_slot_ledger"])
        ):
            raise ValueError("exposure_projection_dataset_contract")
        metadata = dataset.get("lineage", {})
        manifest_pin_rows = [
            {"path": k, "sha256": v}
            for k, v in metadata.items()
            if k.endswith("_sha256") and _is_pin(v)
        ]
        prior_slot = -1
        target_sum = dataset_positive = dataset_zero = 0
        seen_dataset_slots = set()
        for raw_row in dataset["selected_slot_ledger"]:
            row = _slot_entry(raw_row)
            slot = row.get("slot")
            blockhash = row.get("blockhash")
            if type(slot) is not int or not 0 <= slot < 2**63 or slot <= prior_slot:
                raise ValueError("exposure_projection_slot_order_or_duplicate")
            prior_slot = slot
            if not isinstance(blockhash, str) or not blockhash or len(blockhash) > 128:
                raise ValueError("exposure_projection_blockhash")
            if (
                row.get("event_time_unix_seconds") is not None
                and type(row["event_time_unix_seconds"]) is not int
            ):
                raise ValueError("exposure_projection_block_time")
            if row.get("availability_ns") is not None:
                raise ValueError("exposure_projection_historical_availability_forbidden")
            for field in (
                "full_receipt_ns",
                "signature_receipt_ns",
                "provider_signature_count",
                "provider_full_transaction_count",
                "transaction_count",
                "normalized_transactions",
                "failed_transactions",
                "unsupported_normalization",
                "outer_instruction_references",
                "inner_instruction_references",
                "program_count",
            ):
                if type(row.get(field)) is not int or row[field] < 0:
                    raise ValueError("exposure_projection_slot_accounting")
            receipt_times.extend([row["full_receipt_ns"], row["signature_receipt_ns"]])
            count = _dataset_program_count(dataset, row)
            if count:
                dataset_positive += 1
                positive_total += 1
                target_sum += count
                dataset_count_total += count
            else:
                dataset_zero += 1
                zero_total += 1
            identity = (slot, blockhash)
            prior = grouped.get(slot)
            if prior is not None and prior["identity"] != identity:
                raise ValueError("exposure_projection_slot_blockhash_conflict")
            if prior is not None and prior["count"] != count:
                raise ValueError("exposure_projection_slot_count_conflict")
            if prior is not None and prior["block_time"] != row["event_time_unix_seconds"]:
                raise ValueError("exposure_projection_slot_block_time_conflict")
            pins = _row_receipt_pins(row, manifest_pin_rows, ledger_sha256)
            status = "captured_local_positive" if count > 0 else "captured_local_zero"
            census_row = dict(
                dataset_id=dataset["dataset_id"],
                campaign_id=dataset.get("campaign_id"),
                bucket_id=dataset.get("bucket_id"),
                dataset_status=dataset["status"],
                dataset_transaction_status_accounting={
                    key: dataset.get("counts", {}).get(key)
                    for key in (
                        "success_transactions",
                        "failed_transactions",
                        "unknown_status_transactions",
                        "normalized_transactions",
                        "unsupported_normalization",
                    )
                },
                target_program_id=PROGRAM_ID,
                target_program_table_index=(
                    dataset["program_id_table"].index(PROGRAM_ID)
                    if PROGRAM_ID in dataset["program_id_table"]
                    else None
                ),
                captured_unique_referencing_transactions=count,
                status=status,
                projection_disposition=("census_only_zero" if count == 0 else "emitted_positive"),
                source_manifest_pins=sorted(
                    manifest_pin_rows, key=lambda row: (row["path"], row["sha256"])
                ),
                source_receipt_pins=pins,
                **{key: value for key, value in row.items() if key != "program_exposures"},
            )
            census.append(census_row)
            if slot not in grouped:
                grouped[slot] = dict(
                    identity=identity,
                    count=count,
                    block_time=row["event_time_unix_seconds"],
                    evidence=set(),
                    census_indices=[],
                )
            grouped[slot]["evidence"].update(pins)
            grouped[slot]["census_indices"].append(len(census) - 1)
            seen_dataset_slots.add(slot)
            expected_slot_ids.add((dataset["dataset_id"], slot))
        target_summary = next(
            (item for item in dataset.get("programs", []) if item.get("program_id") == PROGRAM_ID),
            None,
        )
        target_program_total = (
            target_summary.get("unique_transactions", 0) if target_summary is not None else 0
        )
        if target_sum != target_program_total:
            raise ValueError("exposure_projection_dataset_total_mismatch")
        dataset_counts[dataset["dataset_id"]] = dict(
            selected_slots=len(seen_dataset_slots),
            positive_rows=dataset_positive,
            captured_local_zeros=dataset_zero,
            sum_of_per_slot_counts=target_sum,
        )
    if max(receipt_times) > decision_time_ns:
        raise ValueError("exposure_projection_ledger_decision_precedes_receipts")
    counts = dict(
        selected_slot_observations=len(census),
        positive_dataset_observations=positive_total,
        captured_local_zero_observations=zero_total,
        positive_rows=sum(record["count"] > 0 for record in grouped.values()),
        captured_local_zeros=sum(record["count"] == 0 for record in grouped.values()),
        sum_of_per_slot_counts=sum(record["count"] for record in grouped.values()),
        sum_of_per_dataset_observation_counts=dataset_count_total,
        distinct_slot_count=len(grouped),
        distinct_selected_dataset_slots=len(expected_slot_ids),
    )
    if len(expected_slot_ids) != len(census):
        raise ValueError("exposure_projection_selected_slot_equality")
    if frozen:
        if (
            any(counts[k] != EXPECTED_COUNTS[k] for k in EXPECTED_COUNTS)
            or counts["distinct_slot_count"] != 608
            or ledger.get("scope", {}).get("datasets")
            != ["Pair64 open sealed Helius capture", "c3 six completed sealed frames"]
        ):
            raise ValueError("exposure_projection_frozen_reconciliation")
    return census, dict(
        counts=counts,
        dataset_counts=dataset_counts,
        grouped=grouped,
        decision_time_ns=decision_time_ns,
        max_receipt_ns=max(receipt_times[1:]),
    )


def _event_rows(grouped: dict, projection_known_ns: int | None, ledger_sha256: str) -> list[dict]:
    if projection_known_ns is not None and (
        type(projection_known_ns) is not int or projection_known_ns <= 0
    ):
        raise ValueError("exposure_projection_known_time")
    events = []
    for slot, record in sorted(grouped.items()):
        count = record["count"]
        if count == 0:
            continue
        slot, blockhash = record["identity"]
        identity = hashlib.sha256(
            canonical(
                {
                    "domain": "market-observations-v2-event-identity-v1",
                    "network": "solana-mainnet",
                    "process": PROCESS_NAME,
                    "program_id": PROGRAM_ID,
                    "slot": slot,
                    "blockhash": blockhash,
                }
            )
        ).hexdigest()
        events.append(
            dict(
                kind="event",
                entity=ENTITY,
                stream=STREAM,
                id=identity,
                coordinate=slot,
                position=[0],
                coordinate_domain_pin=DOMAIN_PIN,
                available_ns=projection_known_ns,
                evidence=sorted(set(record["evidence"]) | {ledger_sha256}),
                values=[count],
            )
        )
    return events


def _validate_known_time(accounting: dict, known_ns: object) -> int:
    earliest = max(accounting["decision_time_ns"], accounting["max_receipt_ns"])
    if type(known_ns) is not int or known_ns < earliest:
        raise ValueError("exposure_projection_clock_before_inputs")
    return known_ns


def _flags() -> dict:
    return dict(
        production_eligible=False,
        economic_qualification=False,
        training_admission=False,
        source_completeness=False,
        global_zero_claim=False,
        global_distinct_transaction_claim=False,
        market_or_pool_identity_claim=False,
    )


def _utc_iso(ns: int) -> str:
    seconds, nanos = divmod(ns, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{nanos:09d}Z"


def _receipt(
    ledger: dict,
    ledger_sha256: str,
    manifest_pins: list[dict],
    known_ns: int,
    source_pin: str,
    sidecar_sha256: str,
    accounting: dict,
) -> dict:
    return dict(
        schema="pair64_c3_pump_amm_slot_exposure_projection_receipt_v1",
        process_name=PROCESS_NAME,
        input_ledger_path=str(LEDGER_RELATIVE_PATH),
        input_ledger_sha256=ledger_sha256,
        reproducible_content_sha256=ledger["reproducible_content_sha256"],
        source_pins=ledger["source_pins"],
        metadata_manifest_pins=manifest_pins,
        decision_time_ns=accounting["decision_time_ns"],
        max_input_receipt_ns=accounting["max_receipt_ns"],
        projection_known_ns=known_ns,
        projection_known_utc=_utc_iso(known_ns),
        source_role="research_evaluation",
        source_availability="historical_replay",
        market_observations_source_pin=source_pin,
        census_sidecar_sha256=sidecar_sha256,
        accounting=accounting["counts"],
        per_dataset_accounting=accounting["dataset_counts"],
        sum_of_per_slot_counts_is_global_distinct=False,
        emitted_event_count=accounting["counts"]["positive_rows"],
        event_schema=OBSERVATION_SCHEMA,
        flags=_flags(),
    )


def _fsync_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("exposure_projection_output_symlink")
        if path.is_file():
            path.chmod(0o600)
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    for path in sorted(
        (p for p in root.rglob("*") if p.is_dir()), key=lambda item: len(item.parts), reverse=True
    ):
        path.chmod(0o700)
        sync_dir(path)
    root.chmod(0o700)
    sync_dir(root)


def load_verified_ledger(root: Path) -> tuple[dict, str, list[dict]]:
    path = safe_path(root / LEDGER_RELATIVE_PATH)
    if not path.is_file():
        raise ValueError("exposure_projection_ledger_missing")
    ledger_sha = file_hash(path)
    if ledger_sha != LEDGER_SHA256:
        raise ValueError("exposure_projection_ledger_hash_mismatch")
    ledger = json.loads(path.read_text())
    _validate_ledger(ledger, ledger_sha, frozen=True)
    manifest_pins = verify_input_references(root, ledger)
    return ledger, ledger_sha, manifest_pins


def create_projection(root: Path, output: Path) -> dict:
    """Publish a create-only, atomic projection using the existing v2 CAS publisher."""
    root = safe_path(root)
    output = safe_path(output)
    work_root = safe_path(root / "work")
    if output == work_root or not output.resolve().is_relative_to(work_root.resolve()):
        raise ValueError("exposure_projection_output_must_be_under_work")
    ledger, ledger_sha, manifest_pins = load_verified_ledger(root)
    census, accounting = _validate_ledger(ledger, ledger_sha, frozen=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with lock(output.parent / ("." + output.name + ".lock")):
        if output.exists():
            from .program_exposure_projection_verify import verify_projection

            return verify_projection(root, output, ledger=ledger, ledger_sha256=ledger_sha)
        for _slot, record in accounting["grouped"].items():
            indices = record["census_indices"]
            for ordinal, index in enumerate(indices):
                census[index]["projection_disposition"] = (
                    "census_only_zero"
                    if record["count"] == 0
                    else "emitted_positive"
                    if ordinal == 0
                    else "deduplicated_identical_slot_blockhash_count"
                )
        stage = Path(tempfile.mkdtemp(prefix="." + output.name + ".stage-", dir=output.parent))
        try:
            observation_store = stage / "market-observations-v2"
            events = _event_rows(accounting["grouped"], None, ledger_sha)
            if len(events) != EXPECTED_COUNTS["positive_rows"]:
                raise ValueError("exposure_projection_event_count")
            sources = [
                dict(name="frozen_program_exposure_ledger", pin=ledger_sha),
                *[
                    dict(
                        name="sealed_dataset_metadata_manifest",
                        dataset_id=entry["dataset_id"],
                        path=entry["path"],
                        pin=entry["sha256"],
                    )
                    for entry in manifest_pins
                ],
                dict(
                    name="projection_implementation",
                    module_sha256=file_hash(Path(__file__)),
                    verifier_sha256=file_hash(
                        Path(__file__).with_name("program_exposure_projection_verify.py")
                    ),
                ),
            ]
            sidecar = dict(
                schema="pair64_c3_pump_amm_slot_exposure_census_v1",
                process_name=PROCESS_NAME,
                input_ledger_sha256=ledger_sha,
                reproducible_content_sha256=ledger["reproducible_content_sha256"],
                input_metadata_manifest_pins=manifest_pins,
                accounting=accounting["counts"],
                per_dataset_accounting=accounting["dataset_counts"],
                sum_of_per_slot_counts_is_global_distinct=False,
                status_semantics=(
                    "captured_local_positive means the frozen selected block ledger records "
                    "at least one distinct transaction referencing the program; "
                    "captured_local_zero "
                    "is census-only and does not assert global absence or complete source support"
                ),
                rows=census,
                flags=_flags(),
            )
            known_ns = _validate_known_time(accounting, time.time_ns())
            for event in events:
                event["available_ns"] = known_ns
            sidecar["projection_known_ns"] = known_ns
            sidecar["projection_known_utc"] = _utc_iso(known_ns)
            source_pin = observations.publish(
                observation_store,
                iter(events),
                schema=OBSERVATION_SCHEMA,
                cutoff=known_ns,
                sources=sources,
            )
            atomic_json(stage / "census.json", sidecar)
            sidecar_sha = file_hash(stage / "census.json")
            receipt = _receipt(
                ledger,
                ledger_sha,
                manifest_pins,
                known_ns,
                source_pin,
                sidecar_sha,
                accounting,
            )
            atomic_json(stage / "receipt.json", receipt)
            from .program_exposure_projection_verify import verify_projection

            verify_projection(root, stage, ledger=ledger, ledger_sha256=ledger_sha)
            _fsync_tree(stage)
            if output.exists():
                raise ValueError("exposure_projection_output_exists")
            os.rename(stage, output)
            sync_dir(output.parent)
            return receipt
        finally:
            if stage.exists():
                shutil.rmtree(stage)
