#!/usr/bin/env python3
"""Offline transaction-endpoint amount deltas for the frozen buyExact Token-2022 dev census.

Amounts remain unsigned token atomic units at each endpoint; signed deltas are computed only
when both endpoint rows identify the same mint, token program, and decimal scale. Missing rows
are unknown, never zero. This does not decode instruction-level settlement or fees.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from memecoin_ai.domain import canonical  # noqa: E402
from memecoin_pumpswap.decoder import PROGRAM, TOKEN, TOKEN2022  # noqa: E402
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _read_block  # noqa: E402
from tools.brain_senses_buy_exact_v1_token2022_evidence_census import (  # noqa: E402
    _analyze_candidate,
    _candidate,
)

CONTRACT = "pumpswap_buy_exact_v1_token2022_endpoint_delta_census_v1"
DEVELOPMENT_RECEIPT = REPO_ROOT / (
    "docs/evidence/pumpswap-buy-exact-v1-token2022-dev-census-2026-10-10.json.zst"
)
CAPTURE_ROOT = REPO_ROOT / (
    "work/brain-senses-live-helius-alchemy-512-pair64-20261009/"
    "20261009T150000Z/paired-block-capture"
)
CONTROL_SIGNATURE = (
    "2psFKsvp5MggmTVfw1dZDGofXdMgpTJDEMeGwivkMKNPRowMCdRMn6LwpUF73hEDbi6Mj2KWvuoQGixsePVoKKQQ"
)
EXPECTED_ROWS = 1234
MAX_TOKEN_AMOUNT_ATOMIC = (1 << 64) - 1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("expected_json_object")
    return value


def _read_zstd_json(path: Path, zstd: str) -> tuple[dict[str, Any], bytes, bytes]:
    compressed = path.read_bytes()
    result = subprocess.run([zstd, "-dc", str(path)], capture_output=True, check=True)
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("development_receipt_not_object")
    return value, compressed, result.stdout


def _account_deltas(row: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return exact signed endpoint differences only for dimensionally matching rows."""
    targets = row.get("target_token_accounts")
    if not isinstance(targets, list) or any(not isinstance(item, str) for item in targets):
        raise ValueError("target_token_accounts_invalid")
    if len(set(targets)) != len(targets):
        raise ValueError("duplicate_target_token_account")
    balance = row.get("transaction_boundary_spendable_balances")
    if not isinstance(balance, dict):
        balance = {}
    before = balance.get("pre_token_balances")
    after = balance.get("post_token_balances")
    deltas: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for account in sorted(targets):
        pre = before.get(account) if isinstance(before, dict) else None
        post = after.get(account) if isinstance(after, dict) else None
        if not isinstance(pre, dict) or not isinstance(post, dict):
            reasons = []
            if not isinstance(pre, dict):
                reasons.append("pre_endpoint_missing")
            if not isinstance(post, dict):
                reasons.append("post_endpoint_missing")
            unknown.append({"account": account, "state": "unknown", "reasons": reasons})
            continue
        identity_fields = ("mint", "program_id", "decimals")
        mismatches = [field for field in identity_fields if pre.get(field) != post.get(field)]
        pre_amount = pre.get("amount_atomic")
        post_amount = post.get("amount_atomic")
        if (
            mismatches
            or type(pre_amount) is not int
            or type(post_amount) is not int
            or pre_amount < 0
            or post_amount < 0
            or pre_amount > MAX_TOKEN_AMOUNT_ATOMIC
            or post_amount > MAX_TOKEN_AMOUNT_ATOMIC
            or not isinstance(pre.get("mint"), str)
            or pre.get("program_id") not in (TOKEN, TOKEN2022)
            or type(pre.get("decimals")) is not int
            or not 0 <= pre["decimals"] <= 255
        ):
            reasons = list(mismatches)
            if type(pre_amount) is not int or type(post_amount) is not int:
                reasons.append("endpoint_atomic_amount_invalid")
            if (type(pre_amount) is int and pre_amount < 0) or (
                type(post_amount) is int and post_amount < 0
            ):
                reasons.append("endpoint_atomic_amount_negative")
            if (type(pre_amount) is int and pre_amount > MAX_TOKEN_AMOUNT_ATOMIC) or (
                type(post_amount) is int and post_amount > MAX_TOKEN_AMOUNT_ATOMIC
            ):
                reasons.append("endpoint_atomic_amount_outside_u64")
            if pre.get("program_id") not in (TOKEN, TOKEN2022):
                reasons.append("token_program_unsupported")
            if type(pre.get("decimals")) is not int or not 0 <= pre["decimals"] <= 255:
                reasons.append("decimal_scale_invalid")
            unknown.append(
                {"account": account, "state": "unknown", "reasons": sorted(set(reasons))}
            )
            continue
        deltas.append(
            {
                "account": account,
                "mint": pre["mint"],
                "token_program_id": pre["program_id"],
                "decimals": pre["decimals"],
                "pre_amount_atomic": pre_amount,
                "post_amount_atomic": post_amount,
                "transaction_endpoint_delta_atomic": post_amount - pre_amount,
                "unit": "atomic_units_of_mint",
                "provider_scope": "reported_transaction_endpoint_token_amount",
            }
        )
    return deltas, unknown


def _subtree_attribution(
    row: dict[str, Any], deltas: list[dict[str, Any]], unknown: list[dict[str, Any]]
) -> str:
    if row.get("outside_subtree_target_account_touches"):
        return "censored_by_outside_subtree_target_account_touch"
    roles = row.get("target_accounts")
    if not isinstance(roles, dict):
        return "unknown_target_account_roles"
    token_roles = {
        "user_base_token_account",
        "pool_base_token_account",
        "user_quote_token_account",
        "pool_quote_token_account",
        "protocol_fee_recipient_token_account",
        "coin_creator_vault_ata",
        "observed_fee_ata",
    }
    mapped_targets = {roles[name] for name in token_roles if isinstance(roles.get(name), str)}
    if mapped_targets != set(row.get("target_token_accounts", [])) or len(mapped_targets) != len(
        row.get("target_token_accounts", [])
    ):
        return "unknown_or_aliased_target_account_roles"
    if unknown or len(deltas) != len(mapped_targets):
        return "censored_by_incomplete_transaction_endpoints"
    touches = row.get("subtree_instruction_trace")
    if not isinstance(touches, list) or not touches:
        return "unknown_target_account_touch_trace"
    target_touches = [touch for touch in touches if touch.get("touched_target_accounts")]
    if not target_touches:
        return "unknown_target_account_touch_trace"
    for touch in target_touches:
        program = touch.get("program_id")
        path = touch.get("path")
        tag = touch.get("wire_first_byte")
        length = touch.get("wire_length")
        if not (
            (program == PROGRAM and path == row.get("instruction_path"))
            or (program == TOKEN and (tag, length) in {(3, 9), (12, 10)})
            or (program == TOKEN2022 and (tag, length) == (12, 10))
        ):
            return "censored_by_unqualified_target_account_instruction"
    return "isolated_subtree_account_delta_candidate_only"


def _verify_isolated_raw_control(
    row: dict[str, Any], census_data: dict[str, Any], capture_root: Path, zstd: str
) -> dict[str, Any]:
    if row.get("signature") != CONTROL_SIGNATURE or row.get(
        "outside_subtree_target_account_touches"
    ):
        raise ValueError("preidentified_isolated_control_changed")
    structure = _read_json(capture_root / "buy_exact_v1_census_v3.json")
    capture_plan = capture_root / "plan.json"
    source = census_data.get("source", {})
    if (
        _sha256(capture_plan.read_bytes()) != source.get("capture_plan_sha256")
        or _sha256((capture_root / "buy_exact_v1_census_v3.json").read_bytes())
        != source.get("structural_census_sha256")
        or structure.get("source", {}).get("plan_sha256") != source.get("capture_plan_sha256")
    ):
        raise ValueError("isolated_control_source_binding_mismatch")
    candidates = [
        item
        for item in structure.get("rows", [])
        if item.get("signature") == row["signature"]
        and item.get("path") == row.get("instruction_path")
        and _candidate(item)
    ]
    if len(candidates) != 1:
        raise ValueError("isolated_control_structural_candidate_not_unique")
    capture_rows = [
        item
        for item in _capture_rows(capture_root)
        if item[2].get("params", [None])[0] == row.get("slot")
    ]
    if len(capture_rows) != 1:
        raise ValueError("isolated_control_block_capture_not_unique")
    capture_path, capture_receipt, request = capture_rows[0]
    block = _read_block(zstd, capture_path, capture_receipt)
    index = row.get("transaction_index")
    if type(index) is not int or not 0 <= index < len(block["transactions"]):
        raise ValueError("isolated_control_transaction_index_invalid")
    tx = block["transactions"][index]
    signatures = tx.get("transaction", {}).get("signatures", [])
    if not signatures or signatures[0] != row["signature"]:
        raise ValueError("isolated_control_signature_mismatch")
    replayed = _analyze_candidate(tx, candidates[0], capture_receipt, block.get("blockTime"))
    if canonical(replayed) != canonical(row):
        raise ValueError("isolated_control_raw_replay_differs_from_development_receipt")
    deltas, unknown = _account_deltas(replayed)
    if unknown or len(deltas) != len(row.get("target_token_accounts", [])):
        raise ValueError("isolated_control_endpoint_rows_incomplete")
    return {
        "status": "raw_replay_and_endpoint_rows_qualified_for_feasibility",
        "signature": row["signature"],
        "slot": row["slot"],
        "transaction_index": index,
        "instruction_path": row["instruction_path"],
        "capture_response_sha256": capture_receipt["response_sha256"],
        "capture_compressed_sha256": capture_receipt["storage"]["sha256"],
        "capture_response_bytes": capture_receipt["response_bytes"],
        "capture_compressed_bytes": capture_receipt["storage"]["bytes"],
        "availability_ns": capture_receipt["received_ns"],
        "native_event_time_unix_seconds": block.get("blockTime"),
        "target_account_count": len(deltas),
        "outside_subtree_target_account_touches": 0,
        "target_trace": "legacy_transfer_and_token2022_transfer_checked_only",
        "lifecycle_instruction_evidence": "none_in_captured_target_account_trace",
        "endpoint_balance_semantics": "provider_reported_transaction_boundary_token_amounts",
        "subtree_attribution": _subtree_attribution(replayed, deltas, unknown),
        "economic_interpretation": "not_assessed",
    }


def _analyze_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(rows) != EXPECTED_ROWS:
        raise ValueError("frozen_development_population_size_changed")
    output: list[dict[str, Any]] = []
    counter: Counter[str] = Counter()
    for row in rows:
        if len(row.get("target_token_accounts", [])) != 7:
            raise ValueError("frozen_target_account_population_changed")
        deltas, unknown = _account_deltas(row)
        attribution = _subtree_attribution(row, deltas, unknown)
        state = (
            "complete_for_all_target_accounts"
            if not unknown
            else "partial_target_account_endpoints"
            if deltas
            else "no_qualifying_target_account_endpoint_delta"
        )
        counter[state] += 1
        counter[attribution] += 1
        counter["target_account_delta_records"] += len(deltas)
        counter["target_account_unknown_records"] += len(unknown)
        for item in unknown:
            for reason in item["reasons"]:
                counter[f"unknown_reason:{reason}"] += 1
        counter["explicit_zero_endpoint_delta_records"] += sum(
            record["transaction_endpoint_delta_atomic"] == 0 for record in deltas
        )
        output.append(
            {
                "signature": row["signature"],
                "slot": row["slot"],
                "transaction_index": row["transaction_index"],
                "instruction_path": row["instruction_path"],
                "availability_ns": row["availability_ns"],
                "native_event_time_unix_seconds": row["native_event_time_unix_seconds"],
                "outside_subtree_target_account_touch_count": len(
                    row.get("outside_subtree_target_account_touches", [])
                ),
                "transaction_endpoint_state": state,
                "transaction_endpoint_deltas": deltas,
                "unknown_target_accounts": unknown,
                "subtree_attribution": attribution,
                "settlement_state": "not_assessed",
                "fee_and_withheld_amount_state": (
                    "historical_configuration_and_extension_state_unavailable"
                ),
                "wallet_net_price_state": "not_assessed",
                "learning_or_execution_admission": "not_qualified",
            }
        )
    summary = {key: value for key, value in sorted(counter.items())}
    summary["references"] = len(rows)
    summary["outside_subtree_touch_refs"] = sum(
        bool(row.get("outside_subtree_target_account_touches")) for row in rows
    )
    summary["no_outside_subtree_touch_refs"] = sum(
        not row.get("outside_subtree_target_account_touches") for row in rows
    )
    return output, summary


def analyze(
    *, development_receipt: Path, capture_root: Path, output_path: Path, zstd: str
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("output_exists_refusing_overwrite")
    development, compressed, uncompressed = _read_zstd_json(development_receipt, zstd)
    if (
        development.get("contract") != "pumpswap_v1_buy_exact_token2022_evidence_census_v1"
        or development.get("scope", {}).get("structural_source_candidate_denominator")
        != EXPECTED_ROWS
        or Path(development.get("source", {}).get("capture_root", "")).resolve()
        != capture_root.resolve()
    ):
        raise ValueError("development_source_contract_or_population_mismatch")
    structural_path = capture_root / "buy_exact_v1_census_v3.json"
    provider_receipt = (
        REPO_ROOT / "docs/evidence/helius-alchemy-pair64-offline-audit-2026-10-09.json"
    )
    provider_receipt_bytes = provider_receipt.read_bytes()
    source = development["source"]
    if _sha256(provider_receipt_bytes) != source.get("provider_audit_receipt_sha256"):
        raise ValueError("provider_binding_receipt_hash_mismatch")
    structural_hash = _sha256(structural_path.read_bytes())
    if structural_hash != source.get("structural_census_sha256"):
        raise ValueError("structural_census_hash_mismatch")
    rows = development.get("rows")
    if not isinstance(rows, list):
        raise ValueError("development_rows_missing")
    isolated = [row for row in rows if not row.get("outside_subtree_target_account_touches")]
    if len(isolated) != 1 or isolated[0].get("signature") != CONTROL_SIGNATURE:
        raise ValueError("frozen_isolated_control_population_mismatch")
    control = _verify_isolated_raw_control(isolated[0], development, capture_root, zstd)
    analyzed_rows, summary = _analyze_rows(rows)
    source_data = {
        "development_receipt_path": str(development_receipt),
        "development_receipt_compressed_sha256": _sha256(compressed),
        "development_receipt_uncompressed_sha256": _sha256(uncompressed),
        "development_contract": development["contract"],
        "development_source_code_commit": development.get("code_commit"),
        "development_tool_sha256": development.get("tool_sha256"),
        "provider": source.get("provider"),
        "capture_root": str(capture_root.resolve()),
        "capture_plan_sha256": source["capture_plan_sha256"],
        "structural_census_path": str(structural_path),
        "structural_census_sha256": structural_hash,
        "provider_audit_receipt": str(provider_receipt),
        "provider_audit_receipt_sha256": _sha256(provider_receipt_bytes),
        "token_balance_schema_reference": "https://solana.com/docs/rpc/json-structures",
        "development_population_receipts": EXPECTED_ROWS,
        "network_requests": 0,
        "buy_exact_audit_sample_accessed": False,
        "raw_replay_scope": "one preidentified no-sibling development reference",
    }
    payload = {
        "contract": CONTRACT,
        "schema_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "claim": (
            "provider-reported whole-transaction token-account endpoint amount deltas; "
            "buy-subtree attribution is at most a single isolated control candidate"
        ),
        "unit_contract": {
            "pre_post_amount_atomic": ("unsigned u64 token atomic units for the same mint"),
            "transaction_endpoint_delta_atomic": (
                "signed post minus pre integer token atomic units; no decimal scaling or "
                "cross-mint arithmetic"
            ),
            "decimals": "metadata that must match across endpoints; no conversion applied",
            "event_time": "native blockTime seconds; preserved separately from availability_ns",
            "availability_time": (
                "original capture receipt nanoseconds; distinct from native event time"
            ),
        },
        "summary": summary,
        "isolated_control": control,
        "limitations": [
            "transaction endpoint deltas do not identify per-CPI intermediate balances",
            "1,233 sibling-touching references remain censored for buy-subtree attribution",
            "missing endpoint rows are unknown and are never converted to zero",
            (
                "an explicit zero endpoint delta means equal transaction-boundary amounts, "
                "not no activity"
            ),
            "reported token amounts do not quantify Token-2022 withheld transfer fees",
            (
                "historical mint/account extension bytes and same-slot write ordering remain "
                "unavailable"
            ),
            (
                "no exact swap settlement, fee, wallet-net price, market completeness, learning "
                "or execution admission is qualified"
            ),
        ],
        "source": source_data,
        "rows": analyzed_rows,
        "tool_sha256": _sha256(Path(__file__).read_bytes()),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    with output_path.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development-receipt", type=Path, default=DEVELOPMENT_RECEIPT)
    parser.add_argument("--capture-root", type=Path, default=CAPTURE_ROOT)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "work/pumpswap-buy-exact-v1-token2022-endpoint-delta-census.json",
    )
    parser.add_argument("--zstd", type=Path, default=Path("/opt/homebrew/bin/zstd"))
    args = parser.parse_args(argv)
    result = analyze(
        development_receipt=args.development_receipt,
        capture_root=args.capture_root,
        output_path=args.output,
        zstd=str(args.zstd),
    )
    print(
        json.dumps(
            {
                "contract": result["contract"],
                "summary": result["summary"],
                "isolated_control": result["isolated_control"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
