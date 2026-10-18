#!/usr/bin/env python3
"""Build a no-RPC proposed Alchemy Account Archive mint-read manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from memecoin_ai.domain import canonical  # noqa: E402

LOCATOR_RECEIPT = ROOT / (
    "docs/evidence/pumpswap-buy-exact-v1-c4-state-snapshot-locator-2026-10-10.json"
)
LOCATOR_RECEIPT_SHA256 = "75433e0302e974d00f651899a143fa10cb0843e7a7a7bdc18f5ba86d06804043"
LOCATOR_TOOL = ROOT / "tools/brain_senses_buy_exact_v1_c4_state_snapshot_locator.py"
FEASIBILITY_RECEIPT = ROOT / (
    "docs/evidence/pumpswap-buy-exact-v1-c4-state-bracketing-feasibility-2026-10-10.json"
)
FEASIBILITY_RECEIPT_SHA256 = "00b52c888a1a8bd1fa7ae8668467f428a88bda006f40fc2fd86543094cc33bc3"
EXPECTED_ROWS = 140
EXPECTED_SLOTS = 16
EXPECTED_TRANSACTIONS = 17_607
EXPECTED_PAIRS_PER_BOUNDARY = 75
T22_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
CAPTURE_ROOT = (
    "/Users/root1/Developer/memecoin-research/work/"
    "brain-senses-live-helius-inventory-v3-20261010-4/epochs/20261010T130000Z"
)
ALCHEMY_ACCOUNT_ARCHIVE_URL = "https://www.alchemy.com/docs/solana/account-archive"
ALCHEMY_GET_ACCOUNT_INFO_URL = (
    "https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-account-info"
)
SOLANA_GET_ACCOUNT_INFO_URL = "https://solana.com/docs/rpc/http/getaccountinfo"
DEFAULT_OUTPUT = ROOT / (
    "docs/evidence/alchemy-account-archive-c4-mint-snapshot-manifest-2026-10-10.json"
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity_key(identity: dict[str, Any]) -> str:
    return canonical(
        {
            "slot": identity.get("slot"),
            "transaction_index": identity.get("transaction_index"),
            "instruction_path": identity.get("instruction_path"),
            "signature": identity.get("signature"),
        }
    )


def _validate_lineage(
    locator: dict[str, Any], feasibility: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if locator.get("contract") != "pumpswap_buy_exact_v1_c4_state_snapshot_locator_v1":
        raise ValueError("locator_contract_mismatch")
    if locator.get("source", {}).get("feasibility_receipt_sha256") != FEASIBILITY_RECEIPT_SHA256:
        raise ValueError("locator_feasibility_hash_mismatch")
    if locator.get("source", {}).get("tool_sha256") != _sha256(LOCATOR_TOOL.read_bytes()):
        raise ValueError("locator_tool_hash_mismatch")
    if locator.get("rows_canonical_sha256") != _sha256(canonical(locator.get("rows")).encode()):
        raise ValueError("locator_rows_hash_mismatch")
    if feasibility.get("contract") != (
        "pumpswap_buy_exact_v1_c4_bank_state_bracketing_feasibility_v1"
    ):
        raise ValueError("feasibility_contract_mismatch")
    if feasibility.get("counts", {}).get("rows") != EXPECTED_ROWS:
        raise ValueError("feasibility_row_denominator_mismatch")
    if locator.get("source", {}).get("selected_slot_count") != EXPECTED_SLOTS:
        raise ValueError("locator_slot_denominator_mismatch")
    if locator.get("source", {}).get("selected_transaction_count") != EXPECTED_TRANSACTIONS:
        raise ValueError("locator_transaction_denominator_mismatch")
    if locator.get("source", {}).get("capture_root") != CAPTURE_ROOT:
        raise ValueError("locator_capture_root_mismatch")
    if locator.get("counts", {}).get("rows") != EXPECTED_ROWS:
        raise ValueError("locator_count_denominator_mismatch")

    locator_rows = locator.get("rows")
    feasibility_rows = feasibility.get("rows")
    if not isinstance(locator_rows, list) or len(locator_rows) != EXPECTED_ROWS:
        raise ValueError("locator_row_denominator_mismatch")
    if not isinstance(feasibility_rows, list) or len(feasibility_rows) != EXPECTED_ROWS:
        raise ValueError("feasibility_row_denominator_mismatch")

    locator_by_identity = {_identity_key(row.get("identity", {})): row for row in locator_rows}
    feasibility_by_identity = {
        _identity_key(row.get("identity", {})): row for row in feasibility_rows
    }
    if (
        len(locator_by_identity) != EXPECTED_ROWS
        or len(feasibility_by_identity) != EXPECTED_ROWS
        or locator_by_identity.keys() != feasibility_by_identity.keys()
    ):
        raise ValueError("locator_feasibility_identity_set_mismatch")

    locator_captures = locator.get("source", {}).get("captures")
    feasibility_captures = feasibility.get("source", {}).get("captures")
    if (
        not isinstance(locator_captures, list)
        or len(locator_captures) != EXPECTED_SLOTS
        or not isinstance(feasibility_captures, list)
        or len(feasibility_captures) != EXPECTED_SLOTS
    ):
        raise ValueError("capture_denominator_mismatch")
    locator_capture_by_slot = {item.get("slot"): item for item in locator_captures}
    feasibility_capture_by_slot = {item.get("slot"): item for item in feasibility_captures}
    if (
        len(locator_capture_by_slot) != EXPECTED_SLOTS
        or locator_capture_by_slot.keys() != feasibility_capture_by_slot.keys()
    ):
        raise ValueError("capture_slot_set_mismatch")
    for slot, capture in locator_capture_by_slot.items():
        frozen = feasibility_capture_by_slot[slot]
        if (
            capture.get("response_sha256") != frozen.get("response_sha256")
            or capture.get("compressed_sha256") != frozen.get("compressed_sha256")
            or capture.get("receipt_time_ns") != frozen.get("receipt_ns")
        ):
            raise ValueError("capture_hash_or_receipt_join_mismatch")

    for identity_hash, locator_row in locator_by_identity.items():
        source_row = feasibility_by_identity[identity_hash]
        identity = locator_row["identity"]
        slot = identity.get("slot")
        if type(slot) is not int or slot not in locator_capture_by_slot:
            raise ValueError("identity_slot_not_in_capture_set")
        if locator_row.get("slot_blockhash") != locator_capture_by_slot[slot].get("blockhash"):
            raise ValueError("locator_row_blockhash_join_mismatch")
        mint = locator_row.get("target_t22_mint")
        expected_mint = (
            source_row.get("required_state_accounts", {}).get("mint_roles", {}).get("base_mint")
        )
        if not isinstance(mint, str) or not mint or mint != expected_mint:
            raise ValueError("locator_base_mint_role_join_mismatch")
        if locator_row.get("target_gate", {}).get("eligible") is not True:
            raise ValueError("locator_target_gate_not_qualified")
        if (
            locator_row.get("snapshot_locator", {}).get("whole_account_two_sided_candidate")
            is not True
        ):
            raise ValueError("locator_row_not_two_sided_candidate")
    return locator_rows, locator_captures


def _build_pairs(rows: list[dict[str, Any]], locator_receipt_sha256: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        identity = row["identity"]
        event_slot = identity["slot"]
        mint = row["target_t22_mint"]
        grouped[(mint, event_slot - 1, "pre_state_S_minus_1")].append(row)
        grouped[(mint, event_slot, "post_state_S")].append(row)

    pairs: list[dict[str, Any]] = []
    for (mint, request_slot, boundary), source_rows in sorted(grouped.items()):
        if request_slot < 0:
            raise ValueError("negative_requested_slot")
        pair_identity = {
            "mint": mint,
            "requested_slot": request_slot,
            "boundary": boundary,
            "locator_receipt_sha256": locator_receipt_sha256,
        }
        pair_id = _sha256(canonical(pair_identity).encode())
        candidate_identities = sorted(
            (row["identity"] for row in source_rows),
            key=lambda item: (
                item["slot"],
                item["transaction_index"],
                item["instruction_path"],
                item["signature"],
            ),
        )
        request_id = _sha256(
            canonical(
                {
                    "pair_id": pair_id,
                    "method": "getAccountInfo",
                    "encoding": "base64",
                    "commitment": "finalized",
                }
            ).encode()
        )
        pairs.append(
            {
                "pair_id": pair_id,
                "mint": mint,
                "mint_owner_expected": T22_PROGRAM_ID,
                "requested_finalized_slot": request_slot,
                "boundary": boundary,
                "candidate_identities": candidate_identities,
                "planned_request": {
                    "request_identity_sha256": request_id,
                    "method": "getAccountInfo",
                    "params": [
                        mint,
                        {
                            "encoding": "base64",
                            "commitment": "finalized",
                            "slot": request_slot,
                        },
                    ],
                    "dataSlice": None,
                    "minContextSlot": None,
                },
                "planned_result": {
                    "status": "not_requested_access_gate_pending",
                    "reservation_id": None,
                    "request_journal_entry_id": None,
                    "raw_response_sha256": None,
                    "raw_response_bytes": None,
                    "raw_capture_path": None,
                    "request_reserved_ns": None,
                    "request_started_ns": None,
                    "receipt_ns": None,
                    "availability_ns": None,
                    "decision_ns": None,
                    "context_slot": None,
                    "account_owner": None,
                    "lamports": None,
                    "space": None,
                    "decoded_data_length": None,
                    "decoded_data_sha256": None,
                    "tlv_decode_status": "not_attempted",
                    "tlv_extension_records": None,
                    "tlv_unparsed_suffix_sha256": None,
                    "provider_error_code": None,
                    "provider_error_message_redacted": None,
                    "cross_source_comparison": "not_assessed_no_second_source",
                    "other_source_raw_response_sha256": None,
                    "provider_cu_before": None,
                    "provider_cu_after": None,
                    "provider_cu_delta": None,
                    "provider_usage_snapshot_before_id": None,
                    "provider_usage_snapshot_after_id": None,
                    "cu_journal_entry_id": None,
                    "http_status": None,
                    "jsonrpc_error_code": None,
                },
            }
        )
    return pairs


def build_manifest(
    locator: dict[str, Any],
    feasibility: dict[str, Any],
    *,
    locator_receipt_sha256: str,
    repository_head: str | None,
) -> dict[str, Any]:
    locator_rows, locator_captures = _validate_lineage(locator, feasibility)
    pairs = _build_pairs(locator_rows, locator_receipt_sha256)
    pre_count = sum(pair["boundary"] == "pre_state_S_minus_1" for pair in pairs)
    post_count = sum(pair["boundary"] == "post_state_S" for pair in pairs)
    if pre_count != EXPECTED_PAIRS_PER_BOUNDARY or post_count != EXPECTED_PAIRS_PER_BOUNDARY:
        raise ValueError("unique_pair_denominator_mismatch")
    total_transactions = sum(item.get("transaction_count", 0) for item in locator_captures)
    if total_transactions != EXPECTED_TRANSACTIONS:
        raise ValueError("capture_transaction_denominator_mismatch")
    return {
        "contract": "alchemy_account_archive_c4_mint_snapshot_manifest_v1",
        "schema_version": 1,
        "documentation_checked_utc_date": "2026-10-10",
        "status": "no_rpc_proposed_manifest_access_gate_pending",
        "scope": (
            "offline proposed account-state reads for the exact 140 frozen c4 13:00Z "
            "Token-2022-base identities"
        ),
        "source": {
            "locator_receipt_path": LOCATOR_RECEIPT.relative_to(ROOT).as_posix(),
            "locator_receipt_sha256": locator_receipt_sha256,
            "locator_rows_canonical_sha256": locator["rows_canonical_sha256"],
            "locator_tool_sha256": locator["source"]["tool_sha256"],
            "locator_code_commit": locator["source"]["repository_head"],
            "feasibility_receipt_path": FEASIBILITY_RECEIPT.relative_to(ROOT).as_posix(),
            "feasibility_receipt_sha256": FEASIBILITY_RECEIPT_SHA256,
            "selected_full_block_count": len(locator_captures),
            "selected_transaction_count": total_transactions,
            "captures": sorted(
                (
                    {
                        "slot": capture["slot"],
                        "blockhash": capture["blockhash"],
                        "response_sha256": capture["response_sha256"],
                        "compressed_sha256": capture["compressed_sha256"],
                        "receipt_time_ns": capture["receipt_time_ns"],
                    }
                    for capture in locator_captures
                ),
                key=lambda item: item["slot"],
            ),
            "repository_head": repository_head,
            "manifest_tool_path": Path(__file__).relative_to(ROOT).as_posix(),
            "manifest_tool_sha256": _sha256(Path(__file__).read_bytes()),
        },
        "source_documentation": [
            {
                "url": ALCHEMY_ACCOUNT_ARCHIVE_URL,
                "claims_used": [
                    "historical slot reads are paid-plan only; free tier cannot use them",
                    "slot S means latest write at or before finalized S",
                    (
                        "only finalized slots enter the archive; historical reads are "
                        "commitment-independent"
                    ),
                    "archive point-in-time response context slot is shown equal to requested slot",
                    "value null means account did not exist as of the resolved slot",
                ],
            },
            {
                "url": ALCHEMY_GET_ACCOUNT_INFO_URL,
                "claims_used": [
                    "historical reads require PAYG or enterprise per current docs",
                    "published standard getAccountInfo cost is 10 CUs",
                    "-32020 is documented as out-of-coverage",
                ],
            },
            {
                "url": SOLANA_GET_ACCOUNT_INFO_URL,
                "claims_used": [
                    "standard response has context and value object-or-null",
                    (
                        "base64 getAccountInfo response includes data, owner, lamports and "
                        "space fields"
                    ),
                ],
            },
        ],
        "access_gate": {
            "entitlement_verified": False,
            "research_payg_endpoint_verified": False,
            "account_limit_and_remaining_budget_verified": False,
            "archive_method_meter_verified": False,
            "archive_slot_coverage_verified": False,
            "existing_three_call_control_status": "frozen_no_rpc_access_gate_pending",
            "rpc_requests_made_by_this_manifest": 0,
        },
        "query_semantics": {
            "method": "getAccountInfo",
            "params_template": [
                "mint",
                {
                    "encoding": "base64",
                    "commitment": "finalized",
                    "slot": "requested_finalized_slot",
                },
            ],
            "full_account_data_required": True,
            "dataSlice_forbidden": True,
            "minContextSlot_forbidden_with_historical_slot": True,
            "required_response": [
                "result.context.slot == requested_finalized_slot",
                "result.value is null or has data=[base64_text, base64], owner, lamports, space",
                (
                    "for non-null values, strict base64 decode succeeds and decoded bytes "
                    "length equals space"
                ),
                (
                    "preserve raw bytes/hash and expose Token-2022 TLV parse status without "
                    "dropping unknown extensions"
                ),
                (
                    "record ordered TLV extension type, offset, length and payload hash, or an "
                    "explicit unknown/parse-failure status"
                ),
            ],
            "slot_boundary_meaning": {
                "pre_state_S_minus_1": (
                    "finalized account snapshot as of S-1, inclusive latest write at or before S-1"
                ),
                "post_state_S": (
                    "finalized account snapshot as of S, inclusive latest write at or before S"
                ),
            },
            "not_inferred": [
                "account write position within slot S",
                "state observed by the target transaction mid-slot",
                "Token-2022 transfer or extension effects",
                "buy settlement or economic outcome",
            ],
        },
        "outcome_states": {
            "not_requested_access_gate_pending": (
                "this manifest state; no request, response, time, or usage value exists"
            ),
            "account_present_valid": (
                "success response, exact context slot, complete base64 data, "
                "owner/lamports/space validated, TLV parser status recorded"
            ),
            "account_nonexistent_value_null": (
                "success response with exact context slot and value null; explicit "
                "nonexistence at requested point"
            ),
            "out_of_coverage": "provider JSON-RPC error -32020; unknown state, not nonexistence",
            "transport_failure": (
                "transport failed or timed out; outcome/charge may be unknown, no identity retry"
            ),
            "provider_error": (
                "other JSON-RPC or HTTP provider error, retained verbatim only after "
                "redaction policy; no retry"
            ),
            "partial_or_malformed": (
                "missing required fields, invalid base64, byte-length/space mismatch, "
                "context-slot mismatch, or incomplete raw capture"
            ),
            "owner_mismatch": (
                "non-null account is not owned by Token-2022; retain exact result but do not "
                "treat as mint bytes"
            ),
            "cross_source_conflict": (
                "independent same-slot source returns a different existence/owner/lamports/"
                "space/raw-data tuple"
            ),
            "cross_source_match": (
                "independent source matches normalized whole-account tuple at same context slot"
            ),
            "not_compared": (
                "no independent source response exists; raw envelope-hash differences alone "
                "are not state conflicts"
            ),
        },
        "capture_and_journal_requirements": {
            "reserve_before_io": True,
            "write_exact_raw_response_before_parse": True,
            "request_identity_is_pair_bound": True,
            "ambiguous_or_started_request_replay": "forbidden",
            "journal_fields": [
                "request_identity_sha256",
                "reservation_id",
                "request_reserved_ns",
                "request_started_ns",
                "receipt_ns",
                "availability_ns",
                "decision_ns",
                "raw_response_sha256",
                "raw_response_bytes",
                "raw_capture_path",
                "provider_cu_before",
                "provider_cu_after",
                "provider_cu_delta",
                "cu_journal_entry_id",
                "outcome_state",
            ],
            "availability_time_rule": (
                "never substitute event slot/time or request/receipt time for an "
                "independently observed availability time"
            ),
        },
        "proposed_caps_not_authorization": {
            "max_unique_account_slot_pairs": len(pairs),
            "max_request_attempts": len(pairs),
            "automatic_retries": 0,
            "max_raw_response_bytes_per_request": 65_536,
            "max_total_raw_response_bytes": 65_536 * len(pairs),
            "published_standard_cu_per_getAccountInfo": 10,
            "proposed_max_standard_cu": 10 * len(pairs),
            "historical_archive_surcharge_or_meter": (
                "unknown; cap is provisional and not account-meter evidence"
            ),
            "authorization": (
                "none; any acquisition requires separate access/budget review and approval"
            ),
            "proposed_byte_cap_basis": (
                "65,536-byte per-response ceiling from the prior frozen control plan, "
                "scaled to 150 unique pairs; it is not a measured response-size bound"
            ),
        },
        "pairs": pairs,
        "counts": {
            "frozen_identities": len(locator_rows),
            "unique_pre_state_pairs": pre_count,
            "unique_post_state_pairs": post_count,
            "unique_total_pairs_and_proposed_max_requests": len(pairs),
            "actual_requests": 0,
            "actual_raw_response_bytes": 0,
            "actual_cu": None,
        },
        "limitations": [
            "this is a proposed offline manifest; it authorizes no request",
            (
                "the paid entitlement, research PAYG endpoint, account cap, archive coverage "
                "and account-specific billing remain unverified"
            ),
            (
                "S-1/S account equality would not reconstruct state at the target transaction "
                "position or prove Token-2022 effects"
            ),
            (
                "139/140 identities have missing or partial token endpoints and all 140 retain "
                "outside-subtree touches, so buy settlement remains prohibited"
            ),
            (
                "no archive response, cross-source comparison, account bytes, availability time, "
                "request reservation, or CU value was fabricated"
            ),
        ],
    }


def load_manifest(repository_head: str | None) -> dict[str, Any]:
    locator_raw = LOCATOR_RECEIPT.read_bytes()
    feasibility_raw = FEASIBILITY_RECEIPT.read_bytes()
    if _sha256(locator_raw) != LOCATOR_RECEIPT_SHA256:
        raise ValueError("locator_receipt_hash_mismatch")
    if _sha256(feasibility_raw) != FEASIBILITY_RECEIPT_SHA256:
        raise ValueError("feasibility_receipt_hash_mismatch")
    return build_manifest(
        json.loads(locator_raw),
        json.loads(feasibility_raw),
        locator_receipt_sha256=_sha256(locator_raw),
        repository_head=repository_head,
    )


def run(output: Path, repository_head: str | None) -> dict[str, Any]:
    if output.exists():
        raise ValueError("output_exists_refusing_overwrite")
    manifest = load_manifest(repository_head)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical(manifest) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repository-head", default=None)
    args = parser.parse_args(argv)
    manifest = run(args.output, args.repository_head)
    print(
        canonical(
            {
                "status": manifest["status"],
                "output": str(args.output),
                "counts": manifest["counts"],
                "proposed_caps_not_authorization": manifest["proposed_caps_not_authorization"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
