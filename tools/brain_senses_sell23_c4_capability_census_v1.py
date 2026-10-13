#!/usr/bin/env python3
"""Read-only c4 raw-meta capability census for the receipt-bound sell23 sample.

The frozen raw replay owns frame selection and sell23 structural qualification.
This follow-on records only whether source fields exist in the selected raw
transaction responses. Transaction metadata is explicitly ex-post; it is not
evidence that a field was available at a historical decision time.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

import brain_senses_sell23_raw_capture_replay_v1 as frozen  # noqa: E402

from memecoin_pumpswap.decoder import decode58  # noqa: E402
from memecoin_pumpswap.economic_v2 import diagnose_pumpswap_sell_v2  # noqa: E402
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402

WSOL = "So11111111111111111111111111111111111111112"
BUY_EXACT25_DISC = bytes.fromhex("c62e1552b4d9e870")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _token_balance(meta: dict[str, Any], field: str, account_index: int) -> dict[str, Any] | None:
    rows = meta.get(field)
    if not isinstance(rows, list):
        return None
    matches = [
        row for row in rows if isinstance(row, dict) and row.get("accountIndex") == account_index
    ]
    return matches[0] if len(matches) == 1 else None


def _balance_capability(meta: dict[str, Any], field: str, account_index: int | None) -> str:
    rows = meta.get(field)
    if not isinstance(rows, list) or account_index is None:
        return "missing"
    return (
        "present" if len(rows) > account_index and type(rows[account_index]) is int else "missing"
    )


def _endpoint_status(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if type(value) is int:
        return "zero" if value == 0 else "positive"
    if isinstance(value, str):
        return value
    return "not_applicable"


def _destination_attribution(pre: str, post: str, other_touches: int) -> str:
    if pre != "present" or post != "present":
        return "endpoint_missing"
    return "present_but_unattributed" if other_touches else "no_other_touch_observed"


def _raw_instructions(tx: dict[str, Any]) -> list[tuple[list[int], dict[str, Any]]]:
    message = (tx.get("transaction") or {}).get("message") or {}
    result = [([index], ix) for index, ix in enumerate(message.get("instructions", []))]
    meta = tx.get("meta") or {}
    for group in (
        meta.get("innerInstructions", []) if isinstance(meta.get("innerInstructions"), list) else []
    ):
        outer = group.get("index")
        if type(outer) is not int:
            continue
        result.extend(
            ([outer, index], ix) for index, ix in enumerate(group.get("instructions", []))
        )
    return result


def _instruction_identity(
    ix: dict[str, Any], normalized: dict[str, Any]
) -> tuple[str | None, bytes]:
    program_id = ix.get("programId")
    if program_id is None:
        index = ix.get("programIdIndex")
        accounts = normalized.get("accounts", [])
        if type(index) is int and 0 <= index < len(accounts):
            program_id = accounts[index].get("pubkey")
    data = ix.get("data")
    raw = b""
    if isinstance(data, str):
        try:
            raw = decode58(data)
        except (ValueError, TypeError):
            raw = b""
    return program_id if isinstance(program_id, str) else None, raw


def _capability_row(
    bucket: str,
    slot: int,
    block: dict[str, Any],
    tx_index: int,
    tx: dict[str, Any],
    target_path: list[int],
    capture_receipt: dict[str, Any],
    request_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    signature = ((tx.get("transaction") or {}).get("signatures") or [None])[0]
    if not isinstance(signature, str):
        raise frozen.ReplayError("sell23_signature_invalid")
    signature_sha = _sha(signature.encode("ascii"))
    message = (tx.get("transaction") or {}).get("message") or {}
    meta = tx.get("meta") or {}
    normalized = normalize_compiled_transaction(tx)
    accounts = normalized.get("accounts", [])
    account_index = {row.get("pubkey"): index for index, row in enumerate(accounts)}
    message_keys = message.get("accountKeys", [])
    signer_count = (message.get("header") or {}).get("numRequiredSignatures")
    signers = set()
    if type(signer_count) is int and 0 <= signer_count <= len(message_keys):
        signers = {
            row.get("pubkey") if isinstance(row, dict) else row
            for row in message_keys[:signer_count]
            if isinstance(row, str) or isinstance(row, dict) and isinstance(row.get("pubkey"), str)
        }
    if (
        message_keys
        and isinstance(message_keys[0], dict)
        and isinstance(message_keys[0].get("pubkey"), str)
    ):
        fee_payer = message_keys[0]["pubkey"]
    elif message_keys and isinstance(message_keys[0], str):
        fee_payer = message_keys[0]
    else:
        fee_payer = None

    failed = meta.get("err") is not None
    outcome = "failed_structural_attempt" if failed else "successful_outside_native_close"
    source_account: str | None = None
    close_destination: str | None = None
    source_flow = None
    diagnosis = None
    if not failed:
        diagnosis = diagnose_pumpswap_sell_v2(tx)
        if diagnosis.get("account_settlement_state") == "censored_partial":
            native = [
                account
                for account, fact in diagnosis.get("account_ledger", {})
                .get("account_facts", {})
                .items()
                if fact.get("mint") == WSOL and fact.get("status") == "censored"
            ]
            if len(native) != 1:
                raise frozen.ReplayError("native_account_identity_not_unique")
            source_account = native[0]
            flow = frozen.classify_account(tx, source_account, target_path)
            source_flow = flow["status"]
            close_destination = flow.get("close_destination")
            outcome = (
                "native_close_unsupported_touch"
                if source_flow == "unsupported_touch"
                else "native_close"
            )

    source_index = account_index.get(source_account) if source_account else None
    destination_index = account_index.get(close_destination) if close_destination else None
    source_pre_token = (
        _token_balance(meta, "preTokenBalances", source_index) if source_index is not None else None
    )
    source_post_token = (
        _token_balance(meta, "postTokenBalances", source_index)
        if source_index is not None
        else None
    )
    destination_pre_token = (
        _token_balance(meta, "preTokenBalances", destination_index)
        if destination_index is not None
        else None
    )
    destination_post_token = (
        _token_balance(meta, "postTokenBalances", destination_index)
        if destination_index is not None
        else None
    )

    inner = meta.get("innerInstructions")
    rent_key_present = any(
        (row.get("pubkey") if isinstance(row, dict) else row)
        == "SysvarRent111111111111111111111111111111111"
        for row in message_keys
    )
    lifecycle_counts: Counter[str] = Counter()
    unsupported_fingerprint = None
    flow_trace = (
        frozen.classify_account(tx, source_account, target_path).get(
            "account_touching_instruction_trace", []
        )
        if source_account
        else []
    )
    for trace in flow_trace:
        path = trace.get("path", [])
        effect = trace.get("effect", {})
        kind = effect.get("kind")
        if kind:
            lifecycle_counts[kind] += 1
        if kind == "unsupported_touch":
            fp = {
                "path": path,
                "program_id": trace.get("program_id"),
                "accounts_sha256": _sha(_canonical(trace.get("accounts", []))),
                "data_sha256": _sha(bytes.fromhex(trace.get("data_hex", ""))),
                "data_bytes": len(bytes.fromhex(trace.get("data_hex", ""))),
            }
            unsupported_fingerprint = {**fp, "fingerprint_sha256": _sha(_canonical(fp))}

    close_paths = {
        tuple(trace.get("path", []))
        for trace in flow_trace
        if trace.get("effect", {}).get("kind") == "close_account"
    }
    other_destination_touches = 0
    if close_destination is not None:
        for path, ix in _raw_instructions(tx):
            instruction_accounts = []
            for index in ix.get("accounts", []) if isinstance(ix.get("accounts"), list) else []:
                if type(index) is int and 0 <= index < len(accounts):
                    instruction_accounts.append(accounts[index].get("pubkey"))
            if close_destination in instruction_accounts:
                # Keep the target CloseAccount path from the recognized lifecycle trace.
                if tuple(path) in close_paths:
                    continue
                else:
                    other_destination_touches += 1

    dest_pre = _balance_capability(meta, "preBalances", destination_index)
    dest_post = _balance_capability(meta, "postBalances", destination_index)
    source_owner_observed = (
        any(
            r.get("accountIndex") == source_index and r.get("owner")
            for field in ("preTokenBalances", "postTokenBalances")
            for r in (meta.get(field) or [])
            if isinstance(r, dict)
        )
        if source_index is not None
        else None
    )
    destination_owner_observed = (
        any(
            r.get("accountIndex") == destination_index and r.get("owner")
            for field in ("preTokenBalances", "postTokenBalances")
            for r in (meta.get(field) or [])
            if isinstance(r, dict)
        )
        if destination_index is not None
        else None
    )
    destination_owner_signer_match: bool | str | None = None
    if destination_index is not None:
        destination_owner_rows = [
            r.get("owner")
            for field in ("preTokenBalances", "postTokenBalances")
            for r in (meta.get(field) or [])
            if isinstance(r, dict) and r.get("accountIndex") == destination_index and r.get("owner")
        ]
        destination_owner_signer_match = (
            any(owner in signers for owner in destination_owner_rows)
            if destination_owner_rows
            else "owner_missing"
        )

    row = {
        "bucket": bucket,
        "slot": slot,
        "blockhash": block.get("blockhash"),
        "transaction_index": tx_index,
        "signature_sha256": signature_sha,
        "target_instruction_path": target_path,
        "outcome": outcome,
        "source_flow_diagnostic": source_flow,
        "event_time_unix_seconds": block.get("blockTime"),
        "capture_receipt_ns": capture_receipt.get("received_ns"),
        "request_started_ns": request_result.get("request_started_ns"),
        "availability_ns": request_result.get("availability_ns"),
        "capture_response_sha256": capture_receipt.get("response_sha256"),
        "decision_time_ns": None,
        "meta_time_class": "transaction_completion_meta_ex_post",
        "meta_capabilities": {
            "pre_lamports_array": isinstance(meta.get("preBalances"), list),
            "post_lamports_array": isinstance(meta.get("postBalances"), list),
            "pre_token_balances_array": isinstance(meta.get("preTokenBalances"), list),
            "post_token_balances_array": isinstance(meta.get("postTokenBalances"), list),
            "inner_instructions_array": isinstance(inner, list),
            "inner_instructions_missing_or_null": inner is None,
            "account_owner_program_endpoints": False,
            "rent_amount_endpoint": False,
            "rent_sysvar_key_present": rent_key_present,
        },
        "account_endpoints": {
            "source_lamports_pre": _balance_capability(meta, "preBalances", source_index),
            "source_lamports_post": _balance_capability(meta, "postBalances", source_index),
            "source_token_pre": ("present" if source_pre_token is not None else "missing")
            if source_index is not None
            else None,
            "source_token_post": ("present" if source_post_token is not None else "missing")
            if source_index is not None
            else None,
            "close_destination_lamports_pre": _balance_capability(
                meta, "preBalances", destination_index
            ),
            "close_destination_lamports_post": dest_post,
            "close_destination_token_pre": (
                "present" if destination_pre_token is not None else "missing"
            )
            if destination_index is not None
            else None,
            "close_destination_token_post": (
                "present" if destination_post_token is not None else "missing"
            )
            if destination_index is not None
            else None,
            "source_meta_owner": ("present" if source_owner_observed else "missing")
            if source_index is not None
            else None,
            "destination_meta_owner": ("present" if destination_owner_observed else "missing")
            if destination_index is not None
            else None,
            "source_is_message_signer": source_account in signers if source_account else None,
            "destination_is_message_signer": close_destination in signers
            if close_destination
            else None,
            "destination_is_fee_payer": close_destination == fee_payer
            if close_destination
            else None,
            "close_destination_other_instruction_touches": other_destination_touches
            if close_destination
            else None,
            "close_destination_lamport_attribution": _destination_attribution(
                dest_pre, dest_post, other_destination_touches
            )
            if close_destination
            else None,
            "historical_rent_reserve": "missing" if source_account else None,
            "destination_owner_matches_signer": destination_owner_signer_match,
        },
        "lifecycle_touch_counts": dict(sorted(lifecycle_counts.items())),
        "unsupported_touch_fingerprint": unsupported_fingerprint,
    }
    return row, diagnosis


def _capture_frames(
    replay_report: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    buy_examples: list[dict[str, Any]] = []
    buy_observed_transaction_count = 0
    for frame in replay_report["frames"]:
        bucket = frame["summary"]["bucket"]
        epoch = frozen.ROOT / "epochs" / bucket
        slots = frame["summary"]["selected_slots"]
        for block_index, slot in enumerate(slots):
            sequence = 3 + 2 * block_index
            receipt_path = epoch / "captures" / f"{sequence:012d}" / "receipt.json"
            capture_receipt = frozen._json(receipt_path)
            _, response = frozen._stored_capture(receipt_path, capture_receipt)
            result = frozen._json(epoch / "request-results" / f"{sequence:012d}.json")
            block = response.get("result")
            if not isinstance(block, dict) or block.get("parentSlot") is None:
                raise frozen.ReplayError("selected_full_block_response_invalid")
            if block.get("parentSlot") != slot - 1:
                raise frozen.ReplayError("selected_full_block_slot_mismatch")
            for tx_index, tx in enumerate(block.get("transactions", [])):
                selected = frozen._sell23(tx)
                normalized = normalize_compiled_transaction(tx)
                if selected is not None:
                    _, target = selected
                    row, _ = _capability_row(
                        bucket, slot, block, tx_index, tx, target["path"], capture_receipt, result
                    )
                    rows.append(row)
                signature = ((tx.get("transaction") or {}).get("signatures") or [None])[0]
                if not isinstance(signature, str):
                    continue
                observed = []
                for path, ix in _raw_instructions(tx):
                    program_id, raw = _instruction_identity(ix, normalized)
                    if len(raw) == 25 and raw[:8] == BUY_EXACT25_DISC:
                        observed.append(
                            {
                                "path": path,
                                "program_id": program_id,
                                "data_bytes": len(raw),
                                "discriminator_hex": raw[:8].hex(),
                            }
                        )
                if observed:
                    buy_observed_transaction_count += 1
                    observed_program_counts: Counter[str] = Counter()
                    for _, flow_ix in _raw_instructions(tx):
                        flow_program_id, _ = _instruction_identity(flow_ix, normalized)
                        observed_program_counts[flow_program_id or "unknown"] += 1
                    buy_examples.append(
                        {
                            "bucket": bucket,
                            "slot": slot,
                            "transaction_index": tx_index,
                            "signature_sha256": _sha(signature.encode("ascii")),
                            "raw_instruction_observations": observed,
                            "outer_instruction_count": len(
                                ((tx.get("transaction") or {}).get("message") or {}).get(
                                    "instructions", []
                                )
                            ),
                            "inner_instruction_count": sum(
                                len(group.get("instructions", []))
                                for group in ((tx.get("meta") or {}).get("innerInstructions") or [])
                                if isinstance(group, dict)
                            ),
                            "observed_program_id_instruction_counts": dict(
                                sorted(observed_program_counts.items())
                            ),
                            "transaction_meta_status": "present_ex_post"
                            if isinstance(tx.get("meta"), dict)
                            else "missing",
                        }
                    )
    rows.sort(
        key=lambda row: (
            row["bucket"],
            row["slot"],
            row["transaction_index"],
            row["signature_sha256"],
        )
    )
    buy_examples.sort(key=lambda row: row["signature_sha256"])
    return rows, buy_examples[:3], buy_observed_transaction_count


def _validate_outcome_partition(rows: list[dict[str, Any]]) -> Counter[str]:
    outcomes = Counter(row["outcome"] for row in rows)
    if len(rows) != 388 or sum(outcomes.values()) != 388:
        raise frozen.ReplayError("structural_row_denominator_mismatch")
    expected = Counter(
        {
            "native_close": 341,
            "native_close_unsupported_touch": 1,
            "successful_outside_native_close": 45,
            "failed_structural_attempt": 1,
        }
    )
    if outcomes != expected:
        raise frozen.ReplayError("outcome_denominator_mismatch")
    native = outcomes["native_close"] + outcomes["native_close_unsupported_touch"]
    if native != 342:
        raise frozen.ReplayError("native_close_denominator_mismatch")
    if outcomes["failed_structural_attempt"] + outcomes[
        "successful_outside_native_close"
    ] + native != len(rows):
        raise frozen.ReplayError("structural_partition_not_closed")
    return outcomes


def build_receipt() -> dict[str, Any]:
    replay_report = frozen.replay()
    rows, buy_examples, buy_observed_transaction_count = _capture_frames(replay_report)
    outcome_counts = _validate_outcome_partition(rows)
    field_counts: Counter[str] = Counter()
    capability_counts: Counter[str] = Counter()
    lifecycle_reference_counts: Counter[str] = Counter()
    fingerprint_rows = []
    for row in rows:
        for key, value in row["account_endpoints"].items():
            field_counts[f"{key}:{_endpoint_status(value)}"] += 1
        for key, value in row["meta_capabilities"].items():
            capability_counts[f"{key}:{str(value).lower()}"] += 1
        for kind, count in row["lifecycle_touch_counts"].items():
            if count:
                lifecycle_reference_counts[kind] += 1
        if row["unsupported_touch_fingerprint"]:
            fingerprint_rows.append(row["unsupported_touch_fingerprint"])
    den = replay_report["denominators"]
    return {
        "schema": "brain-senses-sell23-c4-raw-capability-census-v1",
        "status": "complete_read_only_raw_capability_census",
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "input_receipt": "pumpswap-sell23-c4-first-four-raw-capture-replay-2026-10-13.json",
        "input_receipt_sha256": frozen._file_sha(
            REPO / "docs/evidence/pumpswap-sell23-c4-first-four-raw-capture-replay-2026-10-13.json"
        ),
        "predicate_sha256": frozen.PREDICATE_SHA256,
        "source_root_relative": frozen.ROOT_REL,
        "root_identity": replay_report["root_identity"],
        "source_sha256": {
            "capability_tool": frozen._file_sha(Path(__file__)),
            "raw_replay_tool": frozen._file_sha(
                REPO / "tools/brain_senses_sell23_raw_capture_replay_v1.py"
            ),
            **replay_report["source_sha256"],
        },
        "frames": [
            {
                "bucket": frame["summary"]["bucket"],
                "frame_sha256": frame["summary"]["frame_sha256"],
                "selected_blocks": frame["summary"]["selected_blocks"],
            }
            for frame in replay_report["frames"]
        ],
        "denominators": {
            "structural_references": len(rows),
            "successful_structural_references": den["successful_structural_references"],
            "failed_structural_references": den["failed_structural_references"],
            "native_close_references": sum(
                1 for row in rows if row["outcome"].startswith("native_close")
            ),
            "native_close_exact_source_flow": outcome_counts["native_close"],
            "native_close_unsupported_touch": outcome_counts["native_close_unsupported_touch"],
            "successful_outside_native_close": outcome_counts["successful_outside_native_close"],
            "provider_requests": 0,
            "economic_admissions": 0,
            "training_admissions": 0,
        },
        "population_outcomes": dict(sorted(outcome_counts.items())),
        "meta_capability_counts_over_388": dict(sorted(capability_counts.items())),
        "account_endpoint_counts_over_388": dict(sorted(field_counts.items())),
        "native_close_capabilities_over_342": {
            "source_lamports_pre_present": field_counts["source_lamports_pre:present"],
            "source_lamports_post_present": field_counts["source_lamports_post:present"],
            "source_token_pre_missing": field_counts["source_token_pre:missing"],
            "source_token_post_missing": field_counts["source_token_post:missing"],
            "close_destination_lamports_pre_present": field_counts[
                "close_destination_lamports_pre:present"
            ],
            "close_destination_lamports_post_present": field_counts[
                "close_destination_lamports_post:present"
            ],
            "close_destination_present_but_unattributed": field_counts[
                "close_destination_lamport_attribution:present_but_unattributed"
            ],
            "historical_rent_reserve_missing": field_counts["historical_rent_reserve:missing"],
            "source_account_owner_missing": field_counts["source_meta_owner:missing"],
            "destination_account_owner_missing": field_counts["destination_meta_owner:missing"],
            "destination_is_message_signer": field_counts["destination_is_message_signer:true"],
            "destination_is_fee_payer": field_counts["destination_is_fee_payer:true"],
            "source_is_message_signer": field_counts["source_is_message_signer:true"],
            "destination_token_owner_field_missing": field_counts[
                "destination_owner_matches_signer:owner_missing"
            ],
            "lifecycle_touch_reference_counts": dict(sorted(lifecycle_reference_counts.items())),
        },
        "unsupported_touch_fingerprints": fingerprint_rows,
        "buy_exact25_observed_examples": {
            "selection": (
                "Up to three by ascending signature SHA-256; raw discriminator and length only."
            ),
            "discriminator_hex": BUY_EXACT25_DISC.hex(),
            "instruction_data_bytes": 25,
            "argument_decoding": False,
            "examples": buy_examples,
            "observed_transaction_count_in_selected_blocks": buy_observed_transaction_count,
            "count_note": "Count covers 64 selected blocks; examples are capped at three.",
        },
        "time_semantics": {
            "event_time": "getBlock blockTime, when present",
            "receipt_time": "raw capture receipt received_ns",
            "availability_time": "local request-result availability_ns",
            "decision_time": None,
            "transaction_meta": "later transaction-completion metadata; ex-post only",
        },
        "claims": {
            "raw_capability_census_only": True,
            "source_flow_diagnostic_only": True,
            "account_ownership_established": False,
            "rent_attributed": False,
            "close_destination_settled": False,
            "transaction_message_signer_and_fee_payer_link_observed": True,
            "wallet_net_established": False,
            "native_proceeds_or_wallet_net": False,
            "economic_admission": False,
            "training_admission": False,
        },
        "limitations": [
            "Only c4 14:00Z-17:00Z frames; all four frame hashes match the replay receipt.",
            (
                "Raw metadata endpoints are transaction-boundary facts. They do not prove "
                "instruction-position state or decision-time availability."
            ),
            (
                "A destination lamport endpoint can be present but unattributed when sibling "
                "or parent instructions also touch it."
            ),
            (
                "Rent sysvar visibility or account-creation lamports do not establish historical "
                "Rent or wrapped-token amount."
            ),
            (
                "Token-balance owner fields are token-account owner claims, not account-program "
                "owner endpoints."
            ),
        ],
        "references": rows,
    }


def main() -> None:
    print(json.dumps(build_receipt(), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
