#!/usr/bin/env python3
"""Offline fee-aware mint snapshot locator over the frozen 140-row c4 stratum."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from memecoin_ai.domain import canonical  # noqa: E402
from memecoin_pumpswap.normalize import _account_table  # noqa: E402
from tools.brain_senses_buy_exact_v1_c4_state_bracketing import (  # noqa: E402
    _message_writable_flags,
)
from tools.brain_senses_buy_exact_v1_census import (  # noqa: E402
    _capture_rows,
    _read_block,
    _sha256,
)

CONTRACT = "pumpswap_buy_exact_v1_c4_state_snapshot_locator_v1"
INPUT_RECEIPT = ROOT / (
    "docs/evidence/pumpswap-buy-exact-v1-c4-state-bracketing-feasibility-2026-10-10.json"
)
INPUT_RECEIPT_SHA256 = "00b52c888a1a8bd1fa7ae8668467f428a88bda006f40fc2fd86543094cc33bc3"
INPUT_TOOL = ROOT / "tools/brain_senses_buy_exact_v1_c4_state_bracketing.py"
INPUT_TOOL_SHA256 = "c1b422ff7e7f2e688cce88a5e95a9c5bca5c2c81286074c0b1da3a15c0952434"
CAPTURE_HELPER = ROOT / "tools/brain_senses_buy_exact_v1_census.py"
CAPTURE_HELPER_SHA256 = "14e6a4eec485b8a8a19267e18621c67131c70ff54c5bd250cf060de023525d46"
NORMALIZER = ROOT / "src/memecoin_pumpswap/normalize.py"
NORMALIZER_SHA256 = "7253588560626c6f45ed65ebfd802200f20e79e2daec88fadb93d34731e84643"
DOMAIN = ROOT / "src/memecoin_ai/domain.py"
DOMAIN_SHA256 = "8123b98462fb822ec6c27d3f7eadbea0a6daf782165e92d369c1a9a77526a1c2"
CAPTURE_ROOT = Path(
    "/Users/root1/Developer/memecoin-research/work/"
    "brain-senses-live-helius-inventory-v3-20261010-4/epochs/20261010T130000Z"
)
T22_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
EXPECTED_ROWS = 140
EXPECTED_SLOTS = 16
EXPECTED_TRANSACTIONS = 17_607
DEFAULT_OUTPUT = ROOT / (
    "docs/evidence/pumpswap-buy-exact-v1-c4-state-snapshot-locator-2026-10-10.json"
)


def _file_sha(path: Path) -> str:
    return _sha256(path.read_bytes())


def _verify_source_bindings() -> dict[str, str]:
    expected = {
        "tools/brain_senses_buy_exact_v1_c4_state_bracketing.py": INPUT_TOOL_SHA256,
        "tools/brain_senses_buy_exact_v1_census.py": CAPTURE_HELPER_SHA256,
        "src/memecoin_pumpswap/normalize.py": NORMALIZER_SHA256,
        "src/memecoin_ai/domain.py": DOMAIN_SHA256,
        INPUT_RECEIPT.relative_to(ROOT).as_posix(): INPUT_RECEIPT_SHA256,
    }
    for relative, sha in expected.items():
        path = ROOT / relative
        if _file_sha(path) != sha:
            raise ValueError(f"pinned_source_hash_mismatch:{relative}")
    return expected


def _status(meta: Any) -> str:
    if not isinstance(meta, dict) or "err" not in meta:
        return "unknown"
    return "success" if meta.get("err") is None else "failed"


def _signature(tx: Any) -> str | None:
    transaction = tx.get("transaction") if isinstance(tx, dict) else None
    signatures = transaction.get("signatures") if isinstance(transaction, dict) else None
    if isinstance(signatures, list) and signatures and isinstance(signatures[0], str):
        return signatures[0]
    return None


def _account_view(tx: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    transaction = tx.get("transaction")
    message = transaction.get("message") if isinstance(transaction, dict) else None
    meta = tx.get("meta")
    if not isinstance(message, dict) or not isinstance(meta, dict):
        raise ValueError("transaction_message_or_meta_unresolved")
    accounts = _account_table(message, meta)
    static_keys = message.get("accountKeys")
    header = message.get("header")
    if not isinstance(static_keys, list) or not isinstance(header, dict):
        raise ValueError("transaction_static_keys_or_header_unresolved")
    flags = _message_writable_flags(accounts, len(static_keys), header)
    return accounts, flags


def _fee_payer(accounts: list[dict[str, Any]]) -> tuple[str | None, str]:
    rows = [row for row in accounts if row.get("index") == 0 and row.get("source") == "static"]
    if len(rows) != 1 or not isinstance(rows[0].get("pubkey"), str):
        return None, "unresolved"
    return rows[0]["pubkey"], "resolved_static_account_zero"


def _writable_occurrences(
    transactions: list[dict[str, Any]], mint_keys: set[str]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    occurrences = {mint: [] for mint in mint_keys}
    statuses = Counter()
    for tx_index, tx in enumerate(transactions):
        meta = tx.get("meta") if isinstance(tx, dict) else None
        status = _status(meta)
        statuses[status] += 1
        signature = _signature(tx)
        try:
            accounts, writable = _account_view(tx)
            payer, payer_state = _fee_payer(accounts)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            # A malformed transaction cannot clear either side of an account-state bracket.
            for mint in mint_keys:
                occurrences[mint].append(
                    {
                        "transaction_index": tx_index,
                        "signature": signature,
                        "transaction_status": status,
                        "fee_payer": None,
                        "fee_payer_resolution": "unresolved",
                        "mint_account_provenance": None,
                        "scan_state": "transaction_account_resolution_unknown",
                        "possible_write": True,
                    }
                )
            continue
        by_key = {row["pubkey"]: row for row in accounts}
        for mint in mint_keys.intersection(writable):
            if not writable[mint]:
                continue
            occurrences[mint].append(
                {
                    "transaction_index": tx_index,
                    "signature": signature,
                    "transaction_status": status,
                    "fee_payer": payer,
                    "fee_payer_resolution": payer_state,
                    "mint_account_provenance": by_key[mint],
                    "scan_state": "message_writable_potential",
                    "possible_write": True,
                }
            )
    return occurrences, dict(statuses)


def _occurrence_side_and_effect(
    occurrence: dict[str, Any], *, target_index: int, mint: str
) -> dict[str, Any]:
    tx_index = occurrence["transaction_index"]
    side = "earlier" if tx_index < target_index else "later"
    status = occurrence["transaction_status"]
    payer = occurrence.get("fee_payer")
    if status == "failed" and payer is not None and payer != mint:
        effect = "failed_nonpayer_nonblocking_for_mint_state"
        blocks = False
    else:
        effect = "blocks_whole_account_snapshot"
        blocks = True
    return {
        **occurrence,
        "side": side,
        "whole_account_blocker": blocks,
        "side_effect_class": effect,
    }


def _target_message_gate(row: dict[str, Any], block: dict[str, Any]) -> dict[str, Any]:
    identity = row["identity"]
    target_index = identity["transaction_index"]
    mint = row["required_state_accounts"]["mint_roles"]["base_mint"]
    transactions = block.get("transactions")
    if not isinstance(transactions, list) or not 0 <= target_index < len(transactions):
        return {
            "eligible": False,
            "reasons": ["target_transaction_index_unresolved"],
            "target_status": "unknown",
            "mint_message_privilege": "unknown",
            "fee_payer": None,
        }
    target = transactions[target_index]
    if not isinstance(target, dict):
        return {
            "eligible": False,
            "reasons": ["target_transaction_shape_unresolved"],
            "target_status": "unknown",
            "mint_message_privilege": "unknown",
            "fee_payer": None,
        }
    reasons: list[str] = []
    if _status(target.get("meta")) != "success":
        reasons.append("target_transaction_not_successful")
    if _signature(target) != identity.get("signature"):
        reasons.append("target_signature_mismatch")
    accounts: list[dict[str, Any]] = []
    try:
        accounts, writable = _account_view(target)
        account_rows = [account for account in accounts if account.get("pubkey") == mint]
        if len(account_rows) != 1:
            mint_privilege = "unresolved"
            reasons.append("target_mint_account_unresolved")
        elif writable.get(mint) is True:
            mint_privilege = "writable"
            reasons.append("target_mint_not_readonly")
        elif writable.get(mint) is False:
            mint_privilege = "readonly"
        else:
            mint_privilege = "unresolved"
            reasons.append("target_mint_privilege_unresolved")
        payer, payer_state = _fee_payer(accounts)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        mint_privilege, payer, payer_state = "unresolved", None, "unresolved"
        reasons.append("target_message_resolution_failed")
    if payer is None:
        reasons.append("target_fee_payer_unresolved")
    elif payer == mint:
        reasons.append("target_fee_payer_is_t22_mint")
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "target_status": _status(target.get("meta")),
        "mint_message_privilege": mint_privilege,
        "fee_payer": payer,
        "fee_payer_resolution": payer_state,
    }


def build_rows(source_receipt: dict[str, Any], blocks: dict[int, dict[str, Any]]) -> dict[str, Any]:
    rows = source_receipt.get("rows")
    if not isinstance(rows, list) or len(rows) != EXPECTED_ROWS:
        raise ValueError("frozen_140_row_denominator_mismatch")
    identities = [
        (
            row.get("identity", {}).get("slot"),
            row.get("identity", {}).get("transaction_index"),
            row.get("identity", {}).get("instruction_path"),
            row.get("identity", {}).get("signature"),
        )
        for row in rows
    ]
    if len(set(map(canonical, identities))) != EXPECTED_ROWS:
        raise ValueError("duplicate_frozen_candidate_identity")
    if set(blocks) != {capture["slot"] for capture in source_receipt["source"]["captures"]}:
        raise ValueError("full_block_slot_set_mismatch")
    transaction_count = sum(len(block.get("transactions", [])) for block in blocks.values())
    if transaction_count != EXPECTED_TRANSACTIONS:
        raise ValueError("full_block_transaction_denominator_mismatch")

    rows_by_slot: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        identity = row.get("identity", {})
        slot = identity.get("slot")
        if type(slot) is not int or slot not in blocks:
            raise ValueError("candidate_slot_not_in_frozen_block_set")
        mint = row.get("required_state_accounts", {}).get("mint_roles", {}).get("base_mint")
        if not isinstance(mint, str) or not mint:
            raise ValueError("candidate_t22_mint_role_missing")
        rows_by_slot.setdefault(slot, []).append(row)

    occurrence_by_slot: dict[int, dict[str, list[dict[str, Any]]]] = {}
    status_by_slot: dict[int, dict[str, int]] = {}
    for slot, slot_rows in rows_by_slot.items():
        mints = {row["required_state_accounts"]["mint_roles"]["base_mint"] for row in slot_rows}
        occurrence_by_slot[slot], status_by_slot[slot] = _writable_occurrences(
            blocks[slot]["transactions"], mints
        )

    counts = Counter()
    output_rows: list[dict[str, Any]] = []
    for row in rows:
        identity = row["identity"]
        slot = identity["slot"]
        target_index = identity["transaction_index"]
        mint = row["required_state_accounts"]["mint_roles"]["base_mint"]
        block = blocks[slot]
        target_gate = _target_message_gate(row, block)
        occurrences = [
            _occurrence_side_and_effect(item, target_index=target_index, mint=mint)
            for item in occurrence_by_slot[slot].get(mint, [])
            if item["transaction_index"] != target_index
        ]
        earlier = [item for item in occurrences if item["side"] == "earlier"]
        later = [item for item in occurrences if item["side"] == "later"]
        earlier_blockers = [item for item in earlier if item["whole_account_blocker"]]
        later_blockers = [item for item in later if item["whole_account_blocker"]]
        pre_ok = target_gate["eligible"] and not earlier_blockers
        post_ok = target_gate["eligible"] and not later_blockers
        counts["rows"] += 1
        counts["target_gate_passed"] += target_gate["eligible"]
        counts["pre_state_snapshot_locator_eligible"] += pre_ok
        counts["post_state_snapshot_locator_eligible"] += post_ok
        counts["two_sided_snapshot_locator_eligible"] += pre_ok and post_ok
        counts["failed_nonpayer_nonblocking_occurrences"] += sum(
            item["side_effect_class"] == "failed_nonpayer_nonblocking_for_mint_state"
            for item in occurrences
        )
        counts["whole_account_blocking_occurrences"] += sum(
            item["whole_account_blocker"] for item in occurrences
        )
        counts["earlier_blocking_rows"] += bool(earlier_blockers)
        counts["later_blocking_rows"] += bool(later_blockers)
        output_rows.append(
            {
                "identity": identity,
                "target_reference_basis": "identity_frozen_in_pinned_feasibility_receipt",
                "slot_blockhash": block["blockhash"],
                "target_t22_mint": mint,
                "target_gate": target_gate,
                "same_slot_mint_writable_potential": occurrences,
                "earlier_blockers": earlier_blockers,
                "later_blockers": later_blockers,
                "snapshot_locator": {
                    "pre_state_slot": slot - 1,
                    "post_state_slot": slot,
                    "whole_account_pre_state_candidate": pre_ok,
                    "whole_account_post_state_candidate": post_ok,
                    "whole_account_two_sided_candidate": pre_ok and post_ok,
                    "whole_account_fields": ["existence", "owner", "lamports", "raw_data_bytes"],
                    "data_bytes_only_diagnostic": "not_evaluated",
                    "economic_or_effect_qualification": False,
                },
                "semantics": (
                    "message_writable_is_potential_only; successful_or_uncertain_status_blocks; "
                    "failed_tx_is_nonblocking_only_when_fee_payer_is_resolved_and_not_the_mint"
                ),
            }
        )
    output_rows.sort(
        key=lambda item: (
            item["identity"]["slot"],
            item["identity"]["transaction_index"],
            item["identity"]["instruction_path"],
            item["identity"]["signature"],
        )
    )
    return {
        "rows": output_rows,
        "counts": dict(counts),
        "selected_block_status_counts": {
            str(slot): status_by_slot[slot] for slot in sorted(status_by_slot)
        },
    }


def build_receipt(*, repository_head: str | None = None) -> dict[str, Any]:
    source_hashes = _verify_source_bindings()
    raw = INPUT_RECEIPT.read_bytes()
    source_receipt = json.loads(raw)
    if (
        source_receipt.get("contract")
        != "pumpswap_buy_exact_v1_c4_bank_state_bracketing_feasibility_v1"
        or source_receipt.get("counts", {}).get("rows") != EXPECTED_ROWS
        or source_receipt.get("source", {}).get("capture_root") != str(CAPTURE_ROOT)
    ):
        raise ValueError("frozen_state_bracketing_receipt_scope_mismatch")
    expected_captures = source_receipt["source"].get("captures")
    if not isinstance(expected_captures, list) or len(expected_captures) != EXPECTED_SLOTS:
        raise ValueError("frozen_capture_denominator_mismatch")
    capture_rows = _capture_rows(CAPTURE_ROOT, v3_layout=True)
    if len(capture_rows) != EXPECTED_SLOTS:
        raise ValueError("captured_full_block_denominator_mismatch")
    expected_by_slot = {item["slot"]: item for item in expected_captures}
    if len(expected_by_slot) != EXPECTED_SLOTS:
        raise ValueError("frozen_capture_slot_duplicate")
    blocks: dict[int, dict[str, Any]] = {}
    capture_evidence: list[dict[str, Any]] = []
    zstd = shutil.which("zstd")
    if zstd is None:
        raise ValueError("zstd_cli_unavailable")
    for path, receipt, request in capture_rows:
        if request.get("method") != "getBlock" or not request.get("params"):
            raise ValueError("non_full_block_capture_in_frozen_set")
        slot = request["params"][0]
        expected = expected_by_slot.get(slot)
        if expected is None or (
            receipt.get("response_sha256") != expected.get("response_sha256")
            or receipt.get("storage", {}).get("sha256") != expected.get("compressed_sha256")
            or receipt.get("received_ns") != expected.get("receipt_ns")
        ):
            raise ValueError("frozen_full_block_receipt_mismatch")
        block = _read_block(zstd, path, receipt)
        transactions = block.get("transactions")
        if not isinstance(transactions, list):
            raise ValueError("full_block_transactions_missing")
        blocks[slot] = block
        capture_evidence.append(
            {
                "slot": slot,
                "blockhash": block.get("blockhash"),
                "transaction_count": len(transactions),
                "response_sha256": expected["response_sha256"],
                "compressed_sha256": expected["compressed_sha256"],
                "receipt_time_ns": expected["receipt_ns"],
            }
        )
    result = build_rows(source_receipt, blocks)
    rows_bytes = canonical(result["rows"]).encode()
    tool_sha = _file_sha(Path(__file__))
    return {
        "contract": CONTRACT,
        "schema_version": 1,
        "status": "offline_snapshot_locator_candidates_only",
        "scope": (
            "the exact 140 successful T22-base identities and 16 receipt-hash-bound full blocks "
            "from the frozen c4 13:00Z frame"
        ),
        "source": {
            "feasibility_receipt_path": INPUT_RECEIPT.relative_to(ROOT).as_posix(),
            "feasibility_receipt_sha256": _file_sha(INPUT_RECEIPT),
            "feasibility_receipt_tool_sha256": INPUT_TOOL_SHA256,
            "source_bindings": source_hashes,
            "capture_root": str(CAPTURE_ROOT),
            "captures": sorted(capture_evidence, key=lambda item: item["slot"]),
            "selected_slot_count": len(blocks),
            "selected_transaction_count": sum(
                len(block["transactions"]) for block in blocks.values()
            ),
            "repository_head": repository_head,
            "tool_sha256": tool_sha,
        },
        "policy": {
            "target_gate": (
                "candidate transaction success; frozen base mint resolved in target message; "
                "mint globally readonly; fee payer resolved and not mint"
            ),
            "other_transaction_rule": (
                "enumerate every same-slot message with mint writable potential in transaction "
                "order, including static and loaded writable accounts"
            ),
            "failed_transaction_rule": (
                "failed tx blocks unless meta.err proves failure and fee payer resolves to an "
                "account other than the mint; transaction fees persist on failure"
            ),
            "snapshot_state": "whole account existence, owner, lamports, and raw data bytes",
            "no_state_acquisition": True,
            "no_economic_or_effect_qualification": True,
        },
        "rows": result["rows"],
        "counts": result["counts"],
        "selected_block_status_counts": result["selected_block_status_counts"],
        "rows_canonical_sha256": _sha256(rows_bytes),
        "limitations": [
            "message-level writable privilege is potential, not proof of account mutation",
            (
                "failed transactions retain fees; only a resolved different fee payer permits "
                "a failed writable tx to be nonblocking for this mint"
            ),
            "snapshot candidates are not fetched and do not establish state or transaction effects",
            "no event, sibling-instruction, lifecycle, rent, settlement, or economic qualification",
            "no archive availability or provider completeness claim",
        ],
    }


def run(output: Path, *, repository_head: str | None = None) -> dict[str, Any]:
    if output.exists():
        raise ValueError("output_exists_refusing_overwrite")
    receipt = build_receipt(repository_head=repository_head)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical(receipt) + "\n")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repository-head", default=None)
    args = parser.parse_args(argv)
    receipt = run(args.output, repository_head=args.repository_head)
    print(
        canonical(
            {
                "status": receipt["status"],
                "output": str(args.output),
                "rows": receipt["counts"],
                "rows_canonical_sha256": receipt["rows_canonical_sha256"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
