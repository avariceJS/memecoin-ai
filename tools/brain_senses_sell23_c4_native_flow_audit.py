#!/usr/bin/env python3
"""One-shot offline audit for the predeclared first four completed c4 frames.

This tool must be frozen before opening c4 transaction cases. It makes no provider
requests and applies the unchanged c3 native-account classifier to every eligible
sell23 native-close row in the selected frames.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from brain_senses_block_epoch_v2 import _validate_journal  # noqa: E402
from brain_senses_census_pumpswap_sell_ledger import _capture  # noqa: E402
from brain_senses_select_pumpswap_sell_sample import eligible_sell23  # noqa: E402
from brain_senses_sell23_c3_native_account_flow import (  # noqa: E402
    CONTRACT as FROZEN_FLOW_CONTRACT,
)
from brain_senses_sell23_c3_native_account_flow import classify_account  # noqa: E402
from brain_senses_v2_selector_yield_census import _selected_block_rows  # noqa: E402

from memecoin_pumpswap.decoder import PROGRAM, TOKEN  # noqa: E402
from memecoin_pumpswap.economic_v2 import diagnose_pumpswap_sell_v2  # noqa: E402
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402
from memecoin_pumpswap.schema import SCHEMA  # noqa: E402

CONTRACT = "pumpswap_sell23_c4_native_account_flow_audit_v1"
FROZEN_GATE_COMMIT = "5c5eed390f0702ce54a81091753bc860c6db2cbe"
FROZEN_DOC_COMMIT = "cc55b2a1c77049a18a2eea143d7f35721b0ccd84"
DEFAULT_ROOT = REPO / "work/brain-senses-live-helius-inventory-v3-20261010-4"
FROZEN_RECEIPT = REPO / "docs/evidence/pumpswap-sell23-c3-native-account-flow-2026-10-10.json"
FLOW_TOOL = "tools/brain_senses_sell23_c3_native_account_flow.py"
FLOW_BINDINGS = (
    "src/memecoin_pumpswap/compiled_economics.py",
    "src/memecoin_pumpswap/economic_v2.py",
    "src/memecoin_pumpswap/legacy_token_ledger.py",
    "src/memecoin_pumpswap/normalize.py",
    "src/memecoin_pumpswap/decoder.py",
    "src/memecoin_pumpswap/schema.py",
    "tools/brain_senses_select_pumpswap_sell_sample.py",
    "tools/brain_senses_census_pumpswap_sell_ledger.py",
    FLOW_TOOL,
)
WSOL = "So11111111111111111111111111111111111111112"
MIN_FRAME_REQUESTS = 34
SELECTED_BLOCKS = 16
MAX_CAPTURE_RESPONSE_BYTES = 32_000_000


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _validate_receipt_paths(receipt_paths: list[Path]) -> None:
    """Treat capture receipts as data: reject traversal and oversized payloads."""
    for receipt_path in receipt_paths:
        receipt = read_json(receipt_path)
        if receipt.get("method") != "getBlock":
            continue
        storage = receipt.get("storage")
        relative = storage.get("path") if isinstance(storage, dict) else None
        if not isinstance(relative, str):
            raise ValueError("capture_storage_path_missing")
        parent = receipt_path.parent.resolve()
        payload = (receipt_path.parent / relative).resolve()
        try:
            payload.relative_to(parent)
        except ValueError as exc:
            raise ValueError("capture_storage_path_escapes_receipt") from exc
        response_bytes = receipt.get("response_bytes")
        if (
            not payload.is_file()
            or type(response_bytes) is not int
            or not 0 <= response_bytes <= MAX_CAPTURE_RESPONSE_BYTES
        ):
            raise ValueError("capture_payload_size_or_path_invalid")


def completed_frame_ids(
    root: Path, *, freeze_ns: int, schedule: dict[str, Any]
) -> tuple[list[str], list[dict[str, Any]]]:
    epochs = root / "epochs"
    candidates = sorted(
        path
        for path in epochs.iterdir()
        if path.is_dir() and len(path.name) == 16 and path.name.endswith("Z")
    )
    completed: list[str] = []
    skipped: list[dict[str, Any]] = []
    for epoch in candidates:
        summary_path = epoch / "epoch-summary.json"
        summary = read_json(summary_path) if summary_path.is_file() else {}
        status = summary.get("status", "missing_summary")
        if status == "frame_complete":
            timing = _bucket_timing(epoch, schedule, freeze_ns)
            if not timing["eligible"]:
                skipped.append({"bucket": epoch.name, **timing})
            else:
                completed.append(epoch.name)
        else:
            skipped.append({"bucket": epoch.name, "status": status})
    return completed[:4], skipped


def _bucket_timing(
    epoch: Path, schedule: dict[str, Any], freeze_ns: int | None = None
) -> dict[str, Any]:
    """Bind scheduled trigger, plan creation, first durable reservation and seal."""
    plan_path = epoch / "epoch-plan.json"
    request_path = epoch / "requests" / "000000000001.json"
    result_path = epoch / "request-results" / "000000000001.json"
    summary_path = epoch / "epoch-summary.json"
    try:
        plan = read_json(plan_path)
        request = read_json(request_path)
        result = read_json(result_path)
        summary = read_json(summary_path)
        interval = schedule.get("service_policy", {}).get("start_calendar_interval", {})
        minute = interval.get("minute")
        if (
            plan.get("epoch_id") != epoch.name
            or plan.get("bucket") != epoch.name
            or request.get("sequence") != 1
            or request.get("method") != "getSlot"
            or request.get("phase") != "anchor"
            or type(request.get("reserved_ns")) is not int
            or result.get("sequence") != 1
            or result.get("status") != "captured"
            or type(result.get("request_started_ns")) is not int
            or summary.get("status") != "frame_complete"
            or type(summary.get("sealed_ns")) is not int
            or type(plan.get("created_ns")) is not int
            or type(minute) is not int
            or schedule.get("service_policy", {}).get("schedule_timezone")
            != "host-local calendar trigger; epoch buckets are UTC"
        ):
            raise ValueError("missing_or_ambiguous_timing_binding")
        bucket_time = datetime.strptime(epoch.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        local_bucket = bucket_time.astimezone().replace(minute=0, second=0, microsecond=0)
        local_trigger = local_bucket + timedelta(minutes=minute)
        scheduled_utc = local_trigger.astimezone(UTC)
        if scheduled_utc != bucket_time + timedelta(minutes=minute):
            raise ValueError("host_timezone_bucket_alignment_ambiguous")
        scheduled_trigger_ns = int(scheduled_utc.timestamp()) * 1_000_000_000
        timestamps = {
            "scheduled_trigger_ns": scheduled_trigger_ns,
            "scheduled_trigger_utc": scheduled_utc.isoformat(),
            "scheduled_trigger_local": local_trigger.isoformat(),
            "host_timezone_name": local_trigger.tzname(),
            "host_timezone_offset_seconds": int(
                local_trigger.utcoffset().total_seconds()
            ),
            "epoch_plan_created_ns": plan["created_ns"],
            "first_request_reserved_ns": request["reserved_ns"],
            "first_request_started_ns": result["request_started_ns"],
            "epoch_sealed_ns": summary["sealed_ns"],
            "epoch_plan_sha256": file_sha(plan_path),
            "first_request_reservation_sha256": file_sha(request_path),
            "first_request_result_sha256": file_sha(result_path),
            "epoch_summary_sha256": file_sha(summary_path),
        }
        if freeze_ns is None:
            raise ValueError("freeze_time_required")
        time_fields = (
            "scheduled_trigger_ns",
            "epoch_plan_created_ns",
            "first_request_reserved_ns",
            "first_request_started_ns",
            "epoch_sealed_ns",
        )
        ordered_times = [timestamps[field] for field in time_fields]
        if ordered_times != sorted(ordered_times):
            raise ValueError("frame_request_time_order_invalid")
        if any(timestamps[field] <= freeze_ns for field in time_fields):
            return {
                "status": "completed_before_or_during_freeze",
                "eligible": False,
                "timing": timestamps,
            }
        return {"status": "eligible_completed_frame", "eligible": True, "timing": timestamps}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return {
            "status": "timing_evidence_missing_or_ambiguous",
            "eligible": False,
            "reason": str(exc) or type(exc).__name__,
        }


def select_pass_examples(
    rows: list[dict[str, Any]], *, limit_per_pool: int = 2
) -> list[dict[str, Any]]:
    """Select stable examples by signature hash; never changes audit denominators."""
    if limit_per_pool < 0:
        raise ValueError("negative_example_limit")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("flow_status") == "source_account_flow_exact":
            grouped[row["pool"]].append(row)
    selected = []
    for pool in sorted(grouped):
        ranked = sorted(
            grouped[pool],
            key=lambda row: (
                hashlib.sha256(row["signature"].encode("ascii")).hexdigest(),
                row["signature"],
            ),
        )
        selected.extend(ranked[:limit_per_pool])
    return selected


def _sell23(tx: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    normalized = normalize_compiled_transaction(tx)
    instructions = SCHEMA["instructions"]
    discriminators = {bytes(row["discriminator"]): name for name, row in instructions.items()}
    sell_discriminator = bytes(instructions["sell"]["discriminator"])
    calls = []
    for ix in [*normalized["outer_instructions"], *normalized["inner_instructions"]]:
        if ix["program_id"] != PROGRAM:
            continue
        data = bytes.fromhex(ix["data_hex"])
        name = discriminators.get(data[:8])
        if name is not None:
            calls.append((ix, name, data))
    if len(calls) != 1:
        return None
    ix, name, data = calls[0]
    if (
        name != "sell"
        or data[:8] != sell_discriminator
        or len(data) != 24
        or len(ix["accounts"]) != 23
        or ix["accounts"][11] != TOKEN
        or ix["accounts"][12] != TOKEN
    ):
        return None
    return normalized, ix


def _frozen_bindings() -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in FLOW_BINDINGS:
        current = file_sha(REPO / relative)
        frozen = subprocess.run(
            ["git", "show", f"{FROZEN_GATE_COMMIT}:{relative}"],
            cwd=REPO,
            check=True,
            capture_output=True,
        ).stdout
        frozen_hash = sha(frozen)
        if current != frozen_hash:
            raise ValueError(f"frozen_flow_source_changed:{relative}")
        result[relative] = frozen_hash
    return result


def _frozen_receipt_binding(source_hashes: dict[str, str]) -> dict[str, str]:
    receipt = read_json(FROZEN_RECEIPT)
    recorded = receipt.get("frozen_qualifier_code_sha256")
    expected = {path: digest for path, digest in source_hashes.items() if path != FLOW_TOOL}
    if (
        receipt.get("git_commit") != FROZEN_GATE_COMMIT
        or receipt.get("contract") != FROZEN_FLOW_CONTRACT
        or recorded != expected
        or receipt.get("tool_sha256") != source_hashes.get(FLOW_TOOL)
    ):
        raise ValueError("frozen_c3_receipt_binding_mismatch")
    precommit_doc = subprocess.run(
        ["git", "show", f"{FROZEN_DOC_COMMIT}:docs/brain-source-architecture.md"],
        cwd=REPO,
        check=True,
        capture_output=True,
    ).stdout
    doc_text = precommit_doc.decode("utf-8")
    if (
        "first four completed scheduled c4 hourly frames after the freeze" not in doc_text
        or "ascending SHA-256 of the ASCII signature" not in doc_text
    ):
        raise ValueError("precommitted_audit_selection_text_missing")
    return {
        "receipt_sha256": file_sha(FROZEN_RECEIPT),
        "selection_document_sha256": sha(precommit_doc),
    }


def _frame_rows(
    root: Path, frame_id: str, schedule_sha: str, timing: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    epoch = root / "epochs" / frame_id
    plan = read_json(epoch / "epoch-plan.json")
    summary = read_json(epoch / "epoch-summary.json")
    if (
        plan.get("epoch_id") != frame_id
        or summary.get("epoch_id") != frame_id
        or summary.get("status") != "frame_complete"
        or plan.get("schedule_sha256") != schedule_sha
        or not (epoch / "STOP").is_file()
    ):
        raise ValueError(f"frame_binding_or_terminal_mismatch:{frame_id}")
    journal = _validate_journal(epoch, plan)
    structural_replay = _selected_block_rows(epoch, plan, summary)
    request_outcomes = Counter(
        f"{row.get('phase', 'unknown')}:{row.get('status', 'unknown')}"
        for row in journal
    )
    request_files = list((epoch / "requests").glob("*.json"))
    result_files = list((epoch / "request-results").glob("*.json"))
    checks = summary.get("structural_checks")
    if (
        len(journal) != MIN_FRAME_REQUESTS
        or len(request_files) != MIN_FRAME_REQUESTS
        or len(result_files) != MIN_FRAME_REQUESTS
        or summary.get("request_reservations") != MIN_FRAME_REQUESTS
        or summary.get("structural_checks_total") not in (None, SELECTED_BLOCKS)
        or not isinstance(checks, list)
        or len(checks) != SELECTED_BLOCKS
        or sum(row.get("status") == "qualified_structural_reference_count" for row in checks)
        != SELECTED_BLOCKS
    ):
        raise ValueError(f"frame_accounting_not_complete:{frame_id}")
    receipts = sorted((epoch / "captures").glob("*/receipt.json"))
    _validate_receipt_paths(receipts)
    receipt_manifest_sha = sha(
        "\n".join(
            f"{path.relative_to(REPO)} {file_sha(path)}" for path in receipts
        ).encode("utf-8")
    )
    rows = eligible_sell23(epoch / "captures")
    by_signature = {row["signature"]: row for row in rows}
    if len(by_signature) != len(rows):
        raise ValueError(f"duplicate_eligible_signature:{frame_id}")
    matched: set[str] = set()
    outcomes: Counter[str] = Counter()
    result_rows: list[dict[str, Any]] = []
    full_blocks = 0
    for receipt_path in receipts:
        receipt = read_json(receipt_path)
        if receipt.get("method") != "getBlock":
            continue
        request = json.loads(receipt["request_json"])
        params = request.get("params")
        options = params[1] if isinstance(params, list) and len(params) > 1 else None
        if not isinstance(options, dict) or options.get("transactionDetails") != "full":
            continue
        block = json.loads(_capture(receipt_path, receipt)).get("result")
        if not isinstance(block, dict) or not isinstance(block.get("transactions"), list):
            raise ValueError(f"verified_full_block_invalid:{frame_id}")
        full_blocks += 1
        for transaction_index, tx in enumerate(block["transactions"]):
            candidate = _sell23(tx)
            if candidate is None:
                continue
            normalized, target = candidate
            signatures = normalized.get("transaction_signatures")
            signature = signatures[0] if isinstance(signatures, list) and signatures else None
            if not isinstance(signature, str):
                raise ValueError(f"sell23_signature_missing:{frame_id}")
            signature_hash = sha(signature.encode("ascii"))
            roles = dict(
                zip(
                    SCHEMA["instructions"]["sell"]["accounts"],
                    target["accounts"][:21],
                    strict=True,
                )
            )
            pool = roles["pool"]
            is_failed = (tx.get("meta") or {}).get("err") is not None
            if is_failed:
                outcomes["failed_exact_sell23_structural_refs"] += 1
                result_rows.append(
                    {
                        "bucket": frame_id,
                        "slot": params[0],
                        "block_time_unix_seconds": block.get("blockTime"),
                        "transaction_index": transaction_index,
                        "signature": signature,
                        "signature_sha256": signature_hash,
                        "pool": pool,
                        "capture_receipt_ns": receipt.get("receipt_ns"),
                        "raw_response_sha256": receipt.get("response_sha256"),
                        "gate_eligibility": "failed_transaction",
                        "audit_state": "failed_exact_sell23_structural_reference",
                    }
                )
                continue
            selected = by_signature.get(signature)
            if selected is None:
                raise ValueError(f"successful_sell23_missing_from_eligible_scan:{frame_id}")
            if signature in matched:
                raise ValueError(f"duplicate_successful_sell23:{frame_id}")
            matched.add(signature)
            diagnosis = diagnose_pumpswap_sell_v2(tx)
            ledger_state = diagnosis.get("account_settlement_state", "unknown")
            outcomes[f"ledger:{ledger_state}"] += 1
            if ledger_state != "censored_partial":
                audit_state = (
                    "outside_native_close_gate_exact_settlement"
                    if ledger_state == "exact"
                    else "unresolved_ledger_semantics"
                )
                outcomes[f"audit_state:{audit_state}"] += 1
                result_rows.append(
                    {
                        "bucket": frame_id,
                        "slot": selected["slot"],
                        "block_time_unix_seconds": block.get("blockTime"),
                        "transaction_index": transaction_index,
                        "signature": signature,
                        "signature_sha256": signature_hash,
                        "pool": pool,
                        "capture_receipt_ns": receipt.get("receipt_ns"),
                        "raw_response_sha256": receipt.get("response_sha256"),
                        "ledger_state": ledger_state,
                        "gate_eligibility": "outside_native_close_gate",
                        "audit_state": audit_state,
                    }
                )
                continue
            native_accounts = [
                account
                for account, fact in (diagnosis.get("account_ledger") or {})
                .get("account_facts", {})
                .items()
                if fact.get("mint") == WSOL and fact.get("status") == "censored"
            ]
            if len(native_accounts) != 1:
                outcomes["native_close_candidate_unresolved"] += 1
                result_rows.append(
                    {
                        "bucket": frame_id,
                        "slot": selected["slot"],
                        "block_time_unix_seconds": block.get("blockTime"),
                        "transaction_index": transaction_index,
                        "signature": signature,
                        "signature_sha256": signature_hash,
                        "pool": pool,
                        "capture_receipt_ns": receipt.get("receipt_ns"),
                        "raw_response_sha256": receipt.get("response_sha256"),
                        "ledger_state": ledger_state,
                        "gate_eligibility": "eligible_native_close_unresolved",
                        "audit_state": "native_close_candidate_unresolved",
                        "reason": "censored_native_account_count_not_one",
                    }
                )
                continue
            flow = classify_account(tx, native_accounts[0], target["path"])
            outcomes[f"native_flow:{flow['status']}"] += 1
            outcomes[f"audit_state:{flow['status']}"] += 1
            result_rows.append(
                {
                    "bucket": frame_id,
                    "slot": selected["slot"],
                    "block_time_unix_seconds": block.get("blockTime"),
                    "transaction_index": transaction_index,
                    "signature": signature,
                    "signature_sha256": signature_hash,
                    "pool": pool,
                    "capture_receipt": str(receipt_path.relative_to(REPO)),
                    "capture_receipt_sha256": file_sha(receipt_path),
                    "raw_response_sha256": receipt.get("response_sha256"),
                    "capture_receipt_ns": receipt.get("receipt_ns"),
                    "ledger_state": ledger_state,
                    "target_instruction_path": target["path"],
                    "gate_eligibility": "eligible_native_close",
                    "audit_state": flow["status"],
                    "flow_status": flow["status"],
                    "flow_reasons": flow["reasons"],
                    "flow": flow,
                }
            )
    if set(by_signature) != matched:
        raise ValueError(f"raw_candidate_set_mismatch:{frame_id}")
    if full_blocks != SELECTED_BLOCKS:
        raise ValueError(f"full_block_count_mismatch:{frame_id}")
    return (
        {
            "bucket": frame_id,
            "timing": timing,
            "status": summary["status"],
            "epoch_plan_sha256": file_sha(epoch / "epoch-plan.json"),
            "epoch_summary_sha256": file_sha(epoch / "epoch-summary.json"),
            "frame_sha256": file_sha(epoch / "frame.json"),
            "stop_sha256": file_sha(epoch / "STOP"),
            "capture_receipt_manifest_sha256": receipt_manifest_sha,
            "request_count": len(request_files),
            "raw_bytes": summary.get("captured_response_bytes"),
            "stored_bytes": summary.get("stored_response_bytes"),
            "request_outcomes": dict(sorted(request_outcomes.items())),
            "full_blocks": full_blocks,
            "structural_replay_checks": structural_replay,
            "eligible_scan_candidates": len(by_signature),
            "successful_exact_sell23": len(matched),
            "missing_successful_candidates_from_raw_replay": len(set(by_signature) - matched),
            "outcomes": dict(sorted(outcomes.items())),
        },
        result_rows,
    )


def run(root: Path, output: Path) -> dict[str, Any]:
    schedule_path = root / "schedule-v3.json"
    schedule = read_json(schedule_path)
    if schedule.get("provider") != "helius-free" or not schedule.get("schedule_id", "").startswith(
        "helius-inventory-v3-"
    ):
        raise ValueError("c4_schedule_identity_invalid")
    schedule_sha = file_sha(schedule_path)
    bindings = _frozen_bindings()
    frozen_sources = _frozen_receipt_binding(bindings)
    freeze_seconds = int(
        subprocess.run(
            ["git", "show", "-s", "--format=%ct", FROZEN_GATE_COMMIT],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    freeze_ns = freeze_seconds * 1_000_000_000
    selected_ids, skipped = completed_frame_ids(
        root, freeze_ns=freeze_ns, schedule=schedule
    )
    frontier_path = root / "frontier-v3.json"
    frontier = read_json(frontier_path) if frontier_path.is_file() else {}
    schedule_gaps = [
        gap
        for epoch in sorted((root / "epochs").glob("20*T*Z"))
        if (epoch / "epoch-plan.json").is_file()
        for gap in read_json(epoch / "epoch-plan.json").get("missed_schedule_buckets", [])
    ]
    if len(selected_ids) < 4:
        return {
            "schema_version": 1,
            "contract": CONTRACT,
            "status": "inconclusive_fewer_than_four_completed_frames",
            "completed_after_freeze": selected_ids,
            "skipped_or_incomplete_buckets": skipped,
            "provider_requests_made": 0,
            "paid_requests_made": 0,
            "transaction_cases_opened": False,
            "schedule_sha256": schedule_sha,
            "freeze_commit": FROZEN_GATE_COMMIT,
            "freeze_commit_time_ns": freeze_ns,
            "frozen_flow_source_sha256": bindings,
            "frozen_c3_receipt_and_selection_document_sha256": frozen_sources,
            "frontier_sha256": file_sha(frontier_path) if frontier_path.is_file() else None,
            "frontier_gaps": frontier.get("gap_intervals", []),
            "missed_schedule_buckets": schedule_gaps,
        }
    frame_summaries: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for frame_id in selected_ids:
        timing = _bucket_timing(root / "epochs" / frame_id, schedule, freeze_ns)
        if not timing["eligible"]:
            raise ValueError(f"selected_frame_timing_changed:{frame_id}")
        summary, rows = _frame_rows(root, frame_id, schedule_sha, timing["timing"])
        frame_summaries.append(summary)
        all_rows.extend(rows)
    seen = [row["signature"] for row in all_rows]
    if len(seen) != len(set(seen)):
        raise ValueError("duplicate_audit_signature_across_frames")
    gate_rows = [
        row
        for row in all_rows
        if row.get("gate_eligibility", "").startswith("eligible_native_close")
    ]
    flow_counts = Counter(row.get("flow_status", row["audit_state"]) for row in gate_rows)
    pool_counts: dict[str, Counter[str]] = defaultdict(Counter)
    frame_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in all_rows:
        pool_counts[row["pool"]][row["audit_state"]] += 1
        frame_counts[row["bucket"]][row["audit_state"]] += 1
        if row.get("ledger_state") is not None:
            pool_counts[row["pool"]][f"ledger:{row['ledger_state']}"] += 1
            frame_counts[row["bucket"]][f"ledger:{row['ledger_state']}"] += 1
    examples = select_pass_examples(all_rows)
    mismatches = [
        row
        for row in gate_rows
        if row.get("flow_status") != "source_account_flow_exact"
    ]
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "status": "complete_offline_first_four_completed_c4_frames",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "tool_sha256": file_sha(Path(__file__).resolve()),
        "frame_integrity_validator_sha256": file_sha(
            REPO / "tools/brain_senses_v2_selector_yield_census.py"
        ),
        "source_decision": {
            "frozen_flow_gate_commit": FROZEN_GATE_COMMIT,
            "frozen_document_commit": FROZEN_DOC_COMMIT,
            "flow_contract": FROZEN_FLOW_CONTRACT,
            "selection_rule": "first four completed scheduled c4 hourly frames after freeze",
            "example_rule": (
                "up to two passing rows per pool, ascending SHA-256(ASCII signature), "
                "then signature"
            ),
            "do_not_tune_on_audit": True,
            "every_mismatch_included": True,
        },
        "offline_only": True,
        "provider_requests_made": 0,
        "paid_requests_made": 0,
        "campaign_modified": False,
        "c4_schedule_sha256": schedule_sha,
        "freeze_commit": FROZEN_GATE_COMMIT,
        "freeze_commit_time_ns": freeze_ns,
        "frozen_flow_source_sha256": bindings,
        "frozen_c3_receipt_and_selection_document_sha256": frozen_sources,
        "selected_frame_ids": selected_ids,
        "skipped_or_incomplete_buckets": skipped,
        "frontier_sha256": file_sha(frontier_path) if frontier_path.is_file() else None,
        "frontier_gaps": frontier.get("gap_intervals", []),
        "missed_schedule_buckets": schedule_gaps,
        "frame_summaries": frame_summaries,
        "counts": {
            "exact_layout_sell23_refs_all_states": len(all_rows),
            "eligible_successful_sell23_refs": sum(
                row.get("gate_eligibility") != "failed_transaction" for row in all_rows
            ),
            "failed_sell23_structural_refs": sum(
                row.get("gate_eligibility") == "failed_transaction" for row in all_rows
            ),
            "exact_settlement_outside_native_close_gate": sum(
                row.get("audit_state") == "outside_native_close_gate_exact_settlement"
                for row in all_rows
            ),
            "unresolved_ledger_semantics": sum(
                row.get("audit_state") == "unresolved_ledger_semantics" for row in all_rows
            ),
            "eligible_native_close_rows": len(gate_rows),
            "source_account_flow_exact": flow_counts["source_account_flow_exact"],
            "unsupported_touch": flow_counts["unsupported_touch"],
            "missing_endpoint": flow_counts["missing_endpoint"],
            "unqualified": flow_counts["unqualified"],
            "unresolved_native_close_candidate": flow_counts["native_close_candidate_unresolved"],
            "destination_ambiguous": sum(
                row.get("flow", {}).get("close_destination_state") == "destination_ambiguous"
                for row in gate_rows
            ),
            "audit_mismatches": len(mismatches),
        },
        "by_frame": {
            key: dict(sorted(value.items())) for key, value in sorted(frame_counts.items())
        },
        "by_pool": {
            key: dict(sorted(value.items())) for key, value in sorted(pool_counts.items())
        },
        "pass_examples": examples,
        "mismatches": mismatches,
        "rows": all_rows,
        "semantics": {
            "lamport_conservation_only": True,
            "historical_rent_inferred": False,
            "sync_native_amount_inferred": False,
            "close_destination_attribution": False,
            "wallet_net_economics": False,
            "economic_admission": False,
        },
    }
    if output.exists():
        raise ValueError("output_exists_refusing_overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO / "work/brain-senses-sell23-c4-native-flow-audit.json",
    )
    args = parser.parse_args()
    result = run(args.root, args.output)
    print(
        json.dumps(
            {
                "status": result["status"],
                "selected_frame_ids": result.get("selected_frame_ids", []),
                "completed_after_freeze": result.get("completed_after_freeze", []),
                "counts": result.get("counts"),
                "transaction_cases_opened": result.get("transaction_cases_opened", True),
            },
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "complete_offline_first_four_completed_c4_frames" else 2


if __name__ == "__main__":
    raise SystemExit(main())
