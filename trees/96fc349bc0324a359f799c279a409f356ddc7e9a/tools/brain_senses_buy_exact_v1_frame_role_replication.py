#!/usr/bin/env python3
"""Replay the frozen buyExact role and suffix rules over a later c3 frame."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from memecoin_pumpswap.buy_exact_v1 import (  # noqa: E402
    ACCOUNT_ROLES,
    FEE_RECIPIENTS_DOC_COMMIT,
    FEE_RECIPIENTS_DOC_SHA256,
    IDL_SHA256,
)
from memecoin_pumpswap.compiled_economics import (  # noqa: E402
    FEE_RECIPIENTS_UPGRADE_2026_04_28,
    associated_token_address,
)
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _read_block  # noqa: E402
from tools.brain_senses_buy_exact_v1_role_qualifier import (  # noqa: E402
    TARGET_ROLES,
    _roles_from_raw,
    _sha,
    _validate_frame_binding,
)

SELECTOR = {
    "instruction_data_length": 25,
    "argument_state": "pinned_idl_v1_shape",
}


def _classify_pair(
    census_row: dict[str, Any],
    raw_suffix: list[str],
    raw_roles: dict[str, str],
) -> dict[str, Any]:
    """Check only the frozen trailing wallet/quote-ATA pair; retain any prefix as unknown."""
    result: dict[str, Any] = {
        "pair_state": "unresolved",
        "prefix_state": "none",
        "wallet_in_pinned_eight": None,
        "ata_matches_raw_quote_roles": None,
        "raw_pair_matches_census": None,
        "discordance_reasons": [],
    }
    if len(raw_suffix) not in (2, 3):
        result["discordance_reasons"] = ["remaining_account_count_outside_frozen_2_or_3_strata"]
        return result

    wallet, ata = raw_suffix[-2:]
    wallet_match = wallet in FEE_RECIPIENTS_UPGRADE_2026_04_28
    ata_match = ata == associated_token_address(
        wallet, raw_roles["quote_mint"], raw_roles["quote_token_program"]
    )
    census_match = wallet == census_row.get("observed_fee_recipient") and ata == census_row.get(
        "observed_fee_ata"
    )
    reasons = []
    if not wallet_match:
        reasons.append("trailing_wallet_not_in_pinned_eight")
    if not ata_match:
        reasons.append("trailing_ata_derivation_mismatch")
    if not census_match:
        reasons.append("raw_pair_differs_from_census_pair")
    result.update(
        {
            "pair_state": "source_supported_pair" if not reasons else "discordant",
            "prefix_state": "unresolved_prefix" if len(raw_suffix) == 3 else "none",
            "wallet_in_pinned_eight": wallet_match,
            "ata_matches_raw_quote_roles": ata_match,
            "raw_pair_matches_census": census_match,
            "discordance_reasons": reasons,
        }
    )
    return result


def replicate(report_path: Path, epoch_root: Path, frame_receipt_path: Path) -> dict[str, Any]:
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    frame_receipt, summary, frame_bytes, plan_bytes, summary_bytes = _validate_frame_binding(
        report, epoch_root, frame_receipt_path
    )
    rows = report.get("rows")
    if not isinstance(rows, list) or len(rows) != 479:
        raise ValueError("candidate_instruction_reference_denominator_mismatch")
    signatures = {row.get("signature") for row in rows}
    transactions = {(row.get("slot"), row.get("transaction_index")) for row in rows}
    if len(signatures) != 404 or len(transactions) != 404:
        raise ValueError("candidate_transaction_denominator_mismatch")
    if report.get("counts", {}).get("candidate_transactions") != 404:
        raise ValueError("census_candidate_transaction_count_mismatch")
    if report.get("counts", {}).get("instruction_references") != len(rows):
        raise ValueError("census_instruction_reference_count_mismatch")
    if report.get("counts", {}).get("normalization_errors") != 0:
        raise ValueError("census_normalization_error_count_nonzero")

    candidates = [
        row
        for row in rows
        if all(row.get(key) == value for key, value in SELECTOR.items())
        and row.get("remaining_account_count") in (2, 3)
    ]
    path_keys = [
        (row.get("slot"), row.get("transaction_index"), tuple(row.get("path", [])))
        for row in candidates
    ]
    if len(path_keys) != len(set(path_keys)):
        raise ValueError("duplicate_eligible_instruction_path")

    zstd = shutil.which("zstd")
    if zstd is None:
        raise ValueError("zstd_cli_unavailable")
    capture_entries = _capture_rows(epoch_root, v3_layout=True)
    by_slot: dict[int, tuple[dict[str, Any], Path]] = {}
    capture_bindings = []
    for capture_path, receipt, request in capture_entries:
        params = request.get("params")
        if not isinstance(params, list) or not params or type(params[0]) is not int:
            continue
        slot = params[0]
        by_slot[slot] = (receipt, capture_path)
        capture_bindings.append(
            {
                "slot": slot,
                "capture_sha256": receipt["storage"]["sha256"],
                "capture_bytes": receipt["storage"]["bytes"],
                "response_sha256": receipt["response_sha256"],
                "response_bytes": receipt["response_bytes"],
            }
        )
    if set(by_slot) != set(summary["selected_slots"]):
        raise ValueError("full_block_capture_slots_do_not_match_sealed_summary")
    census_capture_hashes = {
        capture["slot"]: (capture["compressed_sha256"], capture["response_sha256"])
        for capture in report.get("source", {}).get("captures", [])
    }
    actual_capture_hashes = {
        slot: (receipt["storage"]["sha256"], receipt["response_sha256"])
        for slot, (receipt, _) in by_slot.items()
    }
    if census_capture_hashes != actual_capture_hashes:
        raise ValueError("census_report_capture_hash_set_mismatch")

    loaded_blocks: dict[int, dict[str, Any]] = {}
    pools: dict[str, tuple[str, ...]] = {}
    status_counts: Counter[str] = Counter()
    strata: dict[str, Counter[str]] = {"+2": Counter(), "+3": Counter()}
    pair_counts: Counter[tuple[str, str]] = Counter()
    result_rows = []
    for row in candidates:
        slot = row["slot"]
        if slot not in loaded_blocks:
            receipt, capture_path = by_slot[slot]
            loaded_blocks[slot] = _read_block(zstd, capture_path, receipt)
        block = loaded_blocks[slot]
        tx_index = row["transaction_index"]
        txs = block.get("transactions", [])
        if type(tx_index) is not int or not 0 <= tx_index < len(txs):
            raise ValueError("candidate_transaction_index_missing")
        tx = txs[tx_index]
        signatures_in_tx = tx.get("transaction", {}).get("signatures", [])
        if not signatures_in_tx or signatures_in_tx[0] != row["signature"]:
            raise ValueError("candidate_signature_mismatch")
        raw_roles, wire_hex, account_values = _roles_from_raw(tx, row["path"])
        raw_hex = row.get("instruction_data_hex")
        if wire_hex != raw_hex or len(bytes.fromhex(wire_hex)) != 25:
            raise ValueError("candidate_instruction_wire_mismatch")
        expected_status = "success" if tx.get("meta", {}).get("err") is None else "failed"
        if row.get("transaction_status") != expected_status:
            raise ValueError("candidate_transaction_status_mismatch")
        identified = row.get("identified_roles") or {}
        if any(raw_roles[role] != identified.get(role) for role in TARGET_ROLES):
            raise ValueError("identified_idl_role_mismatch")
        if raw_roles["base_mint"] == raw_roles["quote_mint"]:
            raise ValueError("base_quote_mint_alias")
        raw_suffix = account_values[len(ACCOUNT_ROLES) :]
        if len(raw_suffix) != row["remaining_account_count"]:
            raise ValueError("raw_remaining_account_count_mismatch")
        pair = _classify_pair(row, raw_suffix, raw_roles)
        pool_tuple = tuple(raw_roles[role] for role in TARGET_ROLES[1:])
        previous = pools.setdefault(raw_roles["pool"], pool_tuple)
        if previous != pool_tuple:
            raise ValueError("conflicting_role_tuple_for_pool")
        stratum = f"+{len(raw_suffix)}"
        strata[stratum][expected_status] += 1
        pair_counts[(stratum, pair["pair_state"])] += 1
        status_counts[expected_status] += 1
        result_rows.append(
            {
                "slot": slot,
                "transaction_index": tx_index,
                "path": row["path"],
                "signature": row["signature"],
                "transaction_status": expected_status,
                "instruction_data_hex": wire_hex,
                "pool": raw_roles["pool"],
                "base_mint": raw_roles["base_mint"],
                "quote_mint": raw_roles["quote_mint"],
                "base_token_program": raw_roles["base_token_program"],
                "quote_token_program": raw_roles["quote_token_program"],
                "remaining_account_count": len(raw_suffix),
                "ordered_extra_accounts": raw_suffix,
                "census_fee_pair": {
                    "wallet": row.get("observed_fee_recipient"),
                    "ata": row.get("observed_fee_ata"),
                },
                **pair,
            }
        )

    expected_status_counts = Counter({"success": 376, "failed": 42})
    if len(result_rows) != len(candidates) or status_counts != expected_status_counts:
        raise ValueError("eligible_candidate_status_denominator_mismatch")
    expected_strata = {
        "+2": Counter({"success": 150, "failed": 10}),
        "+3": Counter({"success": 226, "failed": 32}),
    }
    if strata != expected_strata:
        raise ValueError("eligible_candidate_stratum_denominator_mismatch")

    return {
        "schema_version": 1,
        "evidence_id": "pumpswap-buy-exact-v1-c3-frame2-role-replication-2026-10-10",
        "scope": (
            "offline replication of pinned IDL roles and frozen trailing wallet/ATA rules over "
            "all 25-byte, pinned-shape c3 frame-2 invocations with two or three remaining accounts"
        ),
        "result": {
            "candidate_transactions": len(transactions),
            "instruction_references": len(rows),
            "eligible_25_byte_invocations": len(result_rows),
            "eligible_successful_transaction_references": status_counts["success"],
            "eligible_failed_transaction_references": status_counts["failed"],
            "eligible_strata": {key: dict(value) for key, value in strata.items()},
            "suffix_pair_outcomes": {
                f"{stratum}:{state}": count
                for (stratum, state), count in sorted(pair_counts.items())
            },
            "distinct_pools": len(pools),
            "base_quote_mints_nonaliased": True,
            "no_conflicting_role_tuple_per_pool": True,
            "duplicate_instruction_paths": 0,
            "normalization_errors": 0,
            "instruction_reference_rows": result_rows,
        },
        "source": {
            "census_report_sha256": _sha(report_bytes),
            "epoch_root": epoch_root.as_posix(),
            "frame_receipt": frame_receipt_path.as_posix(),
            "frame_receipt_sha256": _sha(frame_bytes),
            "epoch_plan_sha256": _sha(plan_bytes),
            "epoch_summary_sha256": _sha(summary_bytes),
            "full_block_captures": sorted(capture_bindings, key=lambda value: value["slot"]),
            "pinned_idl_sha256": IDL_SHA256,
            "fee_recipient_notice_commit": FEE_RECIPIENTS_DOC_COMMIT,
            "fee_recipient_notice_sha256": FEE_RECIPIENTS_DOC_SHA256,
            "replication_tool_sha256": _sha(Path(__file__).read_bytes()),
            "role_qualifier_sha256": _sha(
                (REPO_ROOT / "tools/brain_senses_buy_exact_v1_role_qualifier.py").read_bytes()
            ),
            "source_binding": "raw full-block account indices; loaded writable then readonly",
        },
        "limitations": [
            (
                "The sample is two prospective Helius frames, not global transaction "
                "discovery or population completeness."
            ),
            (
                "Suffix wallet/ATA identity and IDL roles do not establish fee movement, "
                "fill, amounts, price, settlement, or economic orientation."
            ),
            (
                "Three-account invocations retain an unresolved leading account even when "
                "their trailing pair matches."
            ),
            (
                "24-byte and 26-byte payloads, other account counts, and Token-2022 "
                "economics remain unsupported or quarantined."
            ),
            "Failed transaction references are attempts and are never counted as settled fills.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--epoch-root", type=Path, required=True)
    parser.add_argument("--frame-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite output")
    result = replicate(args.report, args.epoch_root, args.frame_receipt)
    payload = json.dumps(result, indent=2, sort_keys=True).encode() + b"\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        stream.write(payload)
    print(
        json.dumps(
            {"output": str(args.output), "sha256": _sha(payload), "result": result["result"]},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
