#!/usr/bin/env python3
"""Trace frozen successful buyExact candidates in c4 full blocks without economic admission."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from memecoin_pumpswap.buy_exact_v1 import ACCOUNT_ROLES, DISCRIMINATOR, IDL_SHA256  # noqa: E402
from memecoin_pumpswap.decoder import (  # noqa: E402
    EVENT_TAG,
    PROGRAM,
    TOKEN,
    TOKEN2022,
    event_payload,
)
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _read_block, _sha256  # noqa: E402
from tools.brain_senses_buy_exact_v1_pool_v2_prefix_qualifier_v2 import (  # noqa: E402
    PINNED_LOCAL_IDL_SHA256,
)

CONTRACT = "pumpswap_buy_exact_v1_c4_first_frame_success_trace_v1"
EXPECTED_CENSUS = ROOT / "docs/evidence/pumpswap-buy-exact-v1-c4-first-frame-census-2026-10-10.json"
EXPECTED_QUALIFICATION = ROOT / (
    "docs/evidence/pumpswap-buy-exact-v1-c4-first-frame-pool-v2-prefix-"
    "raw-reconciled-2026-10-10.json"
)
EXPECTED_FRAME = ROOT / "docs/evidence/helius-inventory-v3-c4-first-frame-2026-10-10.json"
TOKEN2022_SOURCE_RECEIPT = ROOT / (
    "docs/evidence/pumpswap-buy-exact-v1-token2022-transfer-checked-2026-10-10.json"
)
TOKEN2022_SOURCE_RECEIPT_SHA256 = "55f52250d3833ca09fe4214e3d5b7132317a3ab28aa7705bfc6c9e09fe1a0e5e"
TOKEN2022_SOURCE_COMMIT = "b4724e6e7fecc8fc36295f861a769401bf92b270"
EXPECTED_ROWS = 141
TOKEN_AMOUNT_UNITS = {
    "transfer": "atomic_units_of_source_mint_unidentified_by_wire",
    "transfer_checked": "atomic_units_of_wire_mint",
}


def _read(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value, raw


def _key(row: dict[str, Any]) -> tuple[int, int, tuple[int, ...], str]:
    path = row.get("path")
    if not isinstance(path, list) or any(type(i) is not int or i < 0 for i in path):
        raise ValueError("candidate_path_invalid")
    return row["slot"], row["transaction_index"], tuple(path), row["signature"]


def _event_records(
    nodes: list[dict[str, Any]], candidate_path: list[int], roles: dict[str, str]
) -> list[dict[str, Any]]:
    out = []
    for order, node in enumerate(nodes):
        ix = node["instruction"]
        if ix["program_id"] != PROGRAM:
            continue
        wire = bytes.fromhex(ix["data_hex"])
        if not wire.startswith(EVENT_TAG):
            continue
        decoded = None
        decode_state = "unknown_event_wire"
        try:
            decoded = event_payload(wire)
            decode_state = "pinned_event_abi_decoded"
        except ValueError as exc:
            decode_state = f"unparsed_event_wire:{exc}"
        role_pairs = {
            "pool": "pool",
            "user": "user",
            "user_base_token_account": "user_base_token_account",
            "user_quote_token_account": "user_quote_token_account",
            "protocol_fee_recipient": "protocol_fee_recipient",
            "protocol_fee_recipient_token_account": "protocol_fee_recipient_token_account",
        }
        role_match = None
        if decoded is not None and decoded.get("name") == "BuyEvent":
            role_match = all(
                decoded["fields"].get(event_name) == roles.get(role_name)
                for event_name, role_name in role_pairs.items()
            )
        out.append(
            {
                "transaction_order": order,
                "path": ix["path"],
                "stack_height": ix["stack_height"],
                "candidate_descendant": ix["path"][: len(candidate_path)] == candidate_path,
                "association_state": "co_present_only_no_economic_binding",
                "wire_hex": wire.hex(),
                "wire_sha256": _sha256(wire),
                "event_state": decode_state,
                "event_name": decoded.get("name") if decoded else None,
                "event_exact": decoded.get("exact") if decoded else None,
                "event_roles_match_candidate_structure": role_match,
                "unknown_suffix_hex": decoded.get("unknown_suffix_hex") if decoded else None,
            }
        )
    return out


def _amount_operand(program: str, wire: bytes, accounts: list[str]) -> dict[str, Any] | None:
    if program not in (TOKEN, TOKEN2022) or not wire:
        return None
    if wire[0] == 3 and len(wire) == 9:
        kind, amount = "transfer", int.from_bytes(wire[1:9], "little")
        mint = None
        decimals = None
    elif wire[0] == 12 and len(wire) == 10:
        kind, amount = "transfer_checked", int.from_bytes(wire[1:9], "little")
        mint = accounts[1] if len(accounts) > 1 else None
        decimals = wire[9]
    else:
        return None
    return {
        "wire_variant": kind,
        "declared_amount_atomic": amount,
        "unit": TOKEN_AMOUNT_UNITS[kind],
        "mint_pubkey_from_wire_account": mint,
        "declared_decimals": decimals,
        "decimal_scale_is_not_applied": True,
        "interpretation": "wire_operand_only_not_realized_transfer_or_settlement",
    }


def _transaction_endpoint_token_delta(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[int | None, str]:
    """Compare spendable endpoint amounts only when mint identity and scale match."""
    if before is None or after is None:
        return None, "unknown_missing_endpoint"
    identity = ("mint", "token_program_id", "decimals_metadata")
    if any(before.get(field) != after.get(field) for field in identity):
        return None, "censored_endpoint_identity_changed"
    if (
        not isinstance(before.get("mint"), str)
        or before.get("token_program_id") not in (TOKEN, TOKEN2022)
        or type(before.get("decimals_metadata")) is not int
        or not 0 <= before["decimals_metadata"] <= 255
    ):
        return None, "unknown_endpoint_identity_invalid"
    before_amount, after_amount = before.get("amount_atomic"), after.get("amount_atomic")
    if type(before_amount) is not int or type(after_amount) is not int:
        return None, "unknown_invalid_endpoint_amount"
    if not (0 <= before_amount < 1 << 64 and 0 <= after_amount < 1 << 64):
        return None, "unknown_endpoint_amount_outside_u64"
    return after_amount - before_amount, "exact_transaction_boundary_delta_unattributed"


def _trace_row(
    tx: dict[str, Any], block: dict[str, Any], capture_receipt: dict[str, Any], row: dict[str, Any]
) -> dict[str, Any]:
    normalized = normalize_compiled_transaction(tx)
    if normalized["meta_error"] is not None:
        raise ValueError("selected_success_has_transaction_error")
    tx_signatures = normalized["transaction_signatures"]
    if (
        not isinstance(tx_signatures, list)
        or not tx_signatures
        or tx_signatures[0] != row["signature"]
    ):
        raise ValueError("selected_signature_mismatch")
    from memecoin_pumpswap.compiled_economics import _flatten, _transaction_tree

    tree = _transaction_tree(normalized)
    target_node = next(
        (
            node
            for root in tree
            for node in _flatten(root)
            if node["instruction"]["path"] == row["path"]
        ),
        None,
    )
    if target_node is None:
        raise ValueError("selected_candidate_path_missing")
    candidate = target_node["instruction"]
    candidate_wire = bytes.fromhex(candidate["data_hex"])
    if len(candidate_wire) != 25 or candidate_wire[:8] != DISCRIMINATOR:
        raise ValueError("selected_candidate_wire_mismatch")
    if len(candidate["accounts"]) != 26 or len(ACCOUNT_ROLES) != 23:
        raise ValueError("selected_candidate_role_count_mismatch")
    roles = dict(zip(ACCOUNT_ROLES, candidate["accounts"][:23], strict=True))
    roles.update(
        pool_v2_prefix=candidate["accounts"][23],
        fee_wallet_suffix=candidate["accounts"][24],
        fee_quote_ata_suffix=candidate["accounts"][25],
    )
    target_accounts = set(roles.values())
    subtree_ids = {id(n) for n in _flatten(target_node)}
    all_nodes = [node for root in tree for node in _flatten(root)]
    instruction_trace = []
    for order, node in enumerate(all_nodes):
        ix = node["instruction"]
        touched = sorted(target_accounts.intersection(ix["accounts"]))
        if not touched:
            continue
        wire = bytes.fromhex(ix["data_hex"])
        instruction_trace.append(
            {
                "transaction_order": order,
                "path": ix["path"],
                "stack_height": ix["stack_height"],
                "program_id": ix["program_id"],
                "relation": "candidate_subtree"
                if id(node) in subtree_ids
                else "outside_candidate_subtree_sibling_or_unrelated",
                "touched_target_accounts": touched,
                "account_positions": [
                    i for i, key in enumerate(ix["accounts"]) if key in target_accounts
                ],
                "ordered_accounts": (
                    ix["accounts"]
                    if ix["program_id"] in (TOKEN, TOKEN2022, "11111111111111111111111111111111")
                    else None
                ),
                "wire_hex": wire.hex(),
                "wire_sha256": _sha256(wire),
                "amount_operand": _amount_operand(ix["program_id"], wire, ix["accounts"]),
            }
        )
    if not any(item["path"] == row["path"] for item in instruction_trace):
        raise ValueError("candidate_absent_from_touch_trace")

    # Preserve provider endpoint units; represent a missing row as unknown, never zero.
    account_keys = [item["pubkey"] for item in normalized["accounts"]]
    meta = tx.get("meta", {})
    pre_lamports, post_lamports = meta.get("preBalances"), meta.get("postBalances")
    pre_tokens, post_tokens = normalized["pre_token_balances"], normalized["post_token_balances"]
    token_sides: dict[str, dict[str, Any]] = {}
    for side, values in (("pre", pre_tokens), ("post", post_tokens)):
        mapped: dict[str, Any] = {}
        if isinstance(values, list):
            for balance in values:
                idx = balance.get("accountIndex")
                if type(idx) is not int or not 0 <= idx < len(account_keys):
                    raise ValueError("token_endpoint_account_index_invalid")
                account = account_keys[idx]
                if account in target_accounts:
                    amount = balance.get("uiTokenAmount", {}).get("amount")
                    decimals = balance.get("uiTokenAmount", {}).get("decimals")
                    if (
                        not isinstance(amount, str)
                        or not amount.isascii()
                        or not amount.isdecimal()
                        or type(decimals) is not int
                    ):
                        raise ValueError("token_endpoint_amount_shape_invalid")
                    mapped[account] = {
                        "mint": balance.get("mint"),
                        "token_program_id": balance.get("programId"),
                        "amount_atomic": int(amount),
                        "unit": "atomic_units_of_mint",
                        "decimals_metadata": decimals,
                    }
        token_sides[side] = mapped
    endpoint_rows = []
    for account in sorted(target_accounts):
        try:
            index = account_keys.index(account)
        except ValueError as exc:
            raise ValueError("target_account_not_in_resolved_message") from exc
        token_pre, token_post = token_sides["pre"].get(account), token_sides["post"].get(account)
        pre_lamport = (
            pre_lamports[index]
            if isinstance(pre_lamports, list) and len(pre_lamports) == len(account_keys)
            else None
        )
        post_lamport = (
            post_lamports[index]
            if isinstance(post_lamports, list) and len(post_lamports) == len(account_keys)
            else None
        )
        token_delta, token_delta_state = _transaction_endpoint_token_delta(token_pre, token_post)
        lamport_delta = (
            post_lamport - pre_lamport
            if type(pre_lamport) is int
            and type(post_lamport) is int
            and 0 <= pre_lamport < 1 << 64
            and 0 <= post_lamport < 1 << 64
            else None
        )
        endpoint_rows.append(
            {
                "account": account,
                "roles": sorted(name for name, key in roles.items() if key == account),
                "token_endpoint_state": "both_rows_present"
                if token_pre and token_post
                else "partial_or_missing_unknown",
                "pre_token": token_pre,
                "post_token": token_post,
                "transaction_boundary_token_delta_atomic": token_delta,
                "token_delta_unit": "atomic_units_of_mint",
                "token_delta_state": token_delta_state,
                "token_delta_attribution": "whole_transaction_endpoint_only_not_candidate_buy",
                "lamport_endpoint_state": "both_values_present"
                if type(pre_lamport) is int
                and type(post_lamport) is int
                and 0 <= pre_lamport < 1 << 64
                and 0 <= post_lamport < 1 << 64
                else "partial_or_missing_unknown",
                "pre_lamports": pre_lamport,
                "post_lamports": post_lamport,
                "transaction_boundary_lamport_delta": lamport_delta,
                "lamport_delta_attribution": "transaction_boundary_only_unattributed",
                "lamport_unit": "lamports",
                "token_delta_computed": token_delta is not None,
                "lamport_delta_computed": lamport_delta is not None,
            }
        )

    events = _event_records(all_nodes, row["path"], roles)
    event_descendants = [item for item in events if item["candidate_descendant"]]
    exact_role_matching_events = [
        item
        for item in event_descendants
        if item.get("event_name") == "BuyEvent"
        and item.get("event_exact") is True
        and item.get("event_roles_match_candidate_structure") is True
    ]
    token_2022_base = row["identified_roles"]["base_token_program"] == TOKEN2022
    dual_legacy = (
        row["identified_roles"]["base_token_program"] == TOKEN
        and row["identified_roles"]["quote_token_program"] == TOKEN
    )
    gate_reasons = []
    if not dual_legacy:
        gate_reasons.append("not_dual_legacy")
    if len(event_descendants) != 1 or len(exact_role_matching_events) != 1:
        gate_reasons.append("no_unique_exact_role_matching_descendant_buy_event")
    if any(
        item["relation"] != "candidate_subtree"
        for item in instruction_trace
        if item["touched_target_accounts"] and item["path"] != row["path"]
    ):
        gate_reasons.append("outside_subtree_target_account_touches_unresolved")
    if any(
        item["program_id"] not in (TOKEN, PROGRAM)
        for item in instruction_trace
        if item["touched_target_accounts"]
    ):
        gate_reasons.append("unsupported_target_account_touch_program")
    if any(
        item["token_endpoint_state"] != "both_rows_present"
        for item in endpoint_rows
        if any(r.endswith("token_account") or r.endswith("ata_suffix") for r in item["roles"])
    ):
        gate_reasons.append("token_endpoints_incomplete")
    if any(item["lamport_endpoint_state"] != "both_values_present" for item in endpoint_rows):
        gate_reasons.append("lamport_lifecycle_endpoints_incomplete")
    return {
        "identity": {
            "slot": row["slot"],
            "transaction_index": row["transaction_index"],
            "signature": row["signature"],
            "instruction_path": row["path"],
        },
        "transaction_status": "success",
        "block_time_unix_seconds_as_reported": block.get("blockTime"),
        "provider_response_receipt_time_ns": capture_receipt.get("received_ns"),
        "receipt_time_unit": "nanoseconds_since_unix_epoch",
        "capture_receipt_path": row["capture_receipt"],
        "capture_response_sha256": capture_receipt["response_sha256"],
        "instruction": {
            "wire_hex": candidate_wire.hex(),
            "wire_sha256": _sha256(candidate_wire),
            "arguments": {
                "spendable_quote_in_atomic": row["arguments"]["spendable_quote_in"],
                "spendable_quote_unit": "atomic_units_of_quote_mint",
                "min_base_amount_out_atomic": row["arguments"]["min_base_amount_out"],
                "min_base_unit": "atomic_units_of_base_mint",
                "track_volume": row["arguments"]["track_volume"],
            },
            "roles": roles,
            "base_token_program": row["identified_roles"]["base_token_program"],
            "quote_token_program": row["identified_roles"]["quote_token_program"],
        },
        "events": events,
        "event_descendant_count": len(event_descendants),
        "ordered_target_account_instruction_trace": instruction_trace,
        "target_account_endpoint_availability": endpoint_rows,
        "token2022_economic_equations": (
            "prohibited_without_bank_time_extension_withheld_state_and_ordered_cpi_reconciliation"
        )
        if token_2022_base
        else "not_run",
        "historical_extension_state": (
            "unavailable_at_transaction_bank_time"
            if token_2022_base
            else "not_applicable_to_dual_legacy_token_program_pair"
        ),
        "dual_legacy_diagnostic_gate": {
            "eligible_for_conditional_ledger_check": dual_legacy and not gate_reasons,
            "reasons_not_satisfied": sorted(set(gate_reasons)),
            "equations_run": False,
            "class_admission": False,
        },
        "economic_state": "unknown_censored_diagnostic_only",
    }


def trace(
    capture_root: Path,
    census_path: Path,
    qualification_path: Path,
    frame_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    capture_root, census_path, qualification_path, frame_path, output_path = (
        p.resolve()
        for p in (capture_root, census_path, qualification_path, frame_path, output_path)
    )
    if output_path.exists():
        raise ValueError("output_exists_refusing_overwrite")
    if (
        census_path != EXPECTED_CENSUS.resolve()
        or qualification_path != EXPECTED_QUALIFICATION.resolve()
        or frame_path != EXPECTED_FRAME.resolve()
    ):
        raise ValueError("unexpected_frozen_input_paths")
    census, census_bytes = _read(census_path)
    qualification, qualification_bytes = _read(qualification_path)
    frame, frame_bytes = _read(frame_path)
    token_source, token_source_bytes = _read(TOKEN2022_SOURCE_RECEIPT)
    token_source_pin = token_source.get("source", {}).get("token2022_official_source", {})
    if (
        _sha256(token_source_bytes) != TOKEN2022_SOURCE_RECEIPT_SHA256
        or token_source_pin.get("valid") is not True
        or token_source_pin.get("commit") != TOKEN2022_SOURCE_COMMIT
    ):
        raise ValueError("official_token_program_source_pin_mismatch")
    if census.get("contract") != "pumpswap_v1_buy_exact_quote_in_attempt_and_suffix_candidate_v1":
        raise ValueError("census_contract_mismatch")
    if (
        qualification.get("contract")
        != "pumpswap_v1_buy_exact_pool_v2_three_tail_prefix_v2_raw_report_reconciled"
    ):
        raise ValueError("qualification_contract_mismatch")
    if census.get("source", {}).get("root") != str(capture_root):
        raise ValueError("capture_root_does_not_match_census")
    if (
        qualification.get("population_binding", {}).get("candidate_set_equal") is not True
        or qualification.get("population_binding", {}).get("raw_candidate_references") != 171
    ):
        raise ValueError("frozen_qualification_population_binding_invalid")
    if (
        frame.get("campaign_id") != "helius-inventory-v3-20261010-4"
        or frame.get("frame", {}).get("status") != "frame_complete"
    ):
        raise ValueError("c4_frame_receipt_invalid")
    if (
        Path(census.get("source", {}).get("provider_audit_receipt", "")).resolve() != frame_path
        or census.get("source", {}).get("provider_audit_receipt_sha256") != _sha256(frame_bytes)
        or qualification.get("source", {}).get("report_sha256") != _sha256(census_bytes)
        or qualification.get("source", {}).get("frame_receipt_sha256") != _sha256(frame_bytes)
    ):
        raise ValueError("census_qualification_frame_lineage_mismatch")
    if (
        IDL_SHA256 != PINNED_LOCAL_IDL_SHA256
        or census.get("pins", {}).get("pump_amm_idl_sha256") != PINNED_LOCAL_IDL_SHA256
    ):
        raise ValueError("pinned_idl_hash_mismatch")
    census_rows = census.get("rows")
    qualified_rows = qualification.get("result", {}).get("rows")
    if not isinstance(census_rows, list) or not isinstance(qualified_rows, list):
        raise ValueError("frozen_rows_missing")
    success = [
        r
        for r in qualified_rows
        if r.get("transaction_status") == "success"
        and r.get("state") == "sdk_source_bound_pool_v2_pda_candidate"
    ]
    if len(success) != EXPECTED_ROWS or len({_key(r) for r in success}) != EXPECTED_ROWS:
        raise ValueError("frozen_success_denominator_mismatch")
    census_by_key = {_key(r): r for r in census_rows if isinstance(r.get("path"), list)}
    if len(census_by_key) != len([r for r in census_rows if isinstance(r.get("path"), list)]):
        raise ValueError("census_duplicate_identity")
    captures = _capture_rows(capture_root, v3_layout=True)
    captures_by_slot: dict[int, tuple[Path, dict[str, Any], Path]] = {}
    for path, receipt, request in captures:
        params = request.get("params")
        if not isinstance(params, list) or not params or type(params[0]) is not int:
            continue
        slot = params[0]
        if slot in captures_by_slot:
            raise ValueError("duplicate_capture_slot")
        captures_by_slot[slot] = (path, receipt, path.parent / "receipt.json")
    bound = {item["slot"]: item for item in census.get("source", {}).get("captures", [])}
    if set(captures_by_slot) != set(bound) or len(bound) != 16:
        raise ValueError("capture_manifest_slot_set_mismatch")
    if set(frame.get("frame", {}).get("selected_slots", [])) != set(bound):
        raise ValueError("frame_selected_slots_differ_from_frozen_capture_set")
    blocks = {}
    for slot, (path, receipt, _receipt_path) in captures_by_slot.items():
        expected = bound[slot]
        if (
            receipt.get("response_sha256") != expected.get("response_sha256")
            or receipt.get("storage", {}).get("sha256") != expected.get("compressed_sha256")
            or receipt.get("received_ns") != expected.get("receipt_ns")
        ):
            raise ValueError("capture_receipt_differs_from_frozen_census")
        blocks[slot] = _read_block(shutil.which("zstd") or "zstd", path, receipt)
    candidate_rows = sorted(success, key=_key)
    output_rows = []
    token_program_pairs = Counter()
    event_counts = Counter()
    touch_counts = Counter()
    for qualified in candidate_rows:
        key = _key(qualified)
        source = census_by_key.get(key)
        if source is None:
            raise ValueError("qualified_ref_absent_from_census")
        if source.get("transaction_status") != "success":
            raise ValueError("census_status_mismatch")
        if _sha256(bytes.fromhex(source["instruction_data_hex"])) != qualified.get(
            "instruction_data_sha256"
        ):
            raise ValueError("frozen_qualification_wire_hash_mismatch")
        capture_path, receipt, receipt_path = captures_by_slot[source["slot"]]
        block = blocks[source["slot"]]
        tx_index = source["transaction_index"]
        if not 0 <= tx_index < len(block["transactions"]):
            raise ValueError("transaction_index_out_of_bounds")
        row = dict(source)
        row["capture_receipt"] = str(receipt_path.relative_to(capture_root))
        traced = _trace_row(block["transactions"][tx_index], block, receipt, row)
        if (
            traced["identity"]["signature"] != source["signature"]
            or traced["identity"]["instruction_path"] != source["path"]
        ):
            raise ValueError("trace_identity_mismatch")
        token_program_pairs[
            (
                traced["instruction"]["base_token_program"],
                traced["instruction"]["quote_token_program"],
            )
        ] += 1
        event_counts[str(traced["event_descendant_count"])] += 1
        touch_counts[str(len(traced["ordered_target_account_instruction_trace"]))] += 1
        output_rows.append(traced)
    if len(output_rows) != EXPECTED_ROWS:
        raise ValueError("trace_output_denominator_mismatch")
    result = {
        "contract": CONTRACT,
        "schema_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": (
            "one frozen c4 first-frame pool-v2 prefix population; "
            "141 successful transaction references only"
        ),
        "source": {
            "capture_root": str(capture_root),
            "census_path": str(census_path),
            "census_sha256": _sha256(census_bytes),
            "qualification_path": str(qualification_path),
            "qualification_sha256": _sha256(qualification_bytes),
            "frame_receipt_path": str(frame_path),
            "frame_receipt_sha256": _sha256(frame_bytes),
            "token_program_source_receipt": str(TOKEN2022_SOURCE_RECEIPT),
            "token_program_source_receipt_sha256": _sha256(token_source_bytes),
            "token_program_source_commit": TOKEN2022_SOURCE_COMMIT,
            "c4_code_commit": frame["frame"]["code_commit"],
            "c4_frame_receipt_sha256": _sha256(frame_bytes),
            "idl_sha256": PINNED_LOCAL_IDL_SHA256,
            "tool_sha256": _sha256(Path(__file__).read_bytes()),
            "captures": [
                {
                    "slot": slot,
                    "response_sha256": bound[slot]["response_sha256"],
                    "compressed_sha256": bound[slot]["compressed_sha256"],
                    "receipt_ns": bound[slot]["receipt_ns"],
                }
                for slot in sorted(bound)
            ],
        },
        "units": {
            "slot": "slot_index",
            "transaction_index": "transaction_index_within_full_block",
            "block_time_unix_seconds_as_reported": "seconds_since_unix_epoch_as_reported_by_block",
            "provider_response_receipt_time_ns": "nanoseconds_since_unix_epoch_at_response_receipt",
            "token_amount_operands": "atomic_units; no decimal scaling applied",
            "lamport_endpoints": "lamports",
        },
        "denominators": {
            "qualifier_success_references": EXPECTED_ROWS,
            "failed_attempts_preserved_in_source_census": 30,
            "rows_traced": len(output_rows),
        },
        "counts": {
            "token_program_pairs": [
                {"base": list(pair)[0], "quote": list(pair)[1], "references": count}
                for pair, count in sorted(token_program_pairs.items())
            ],
            "event_descendant_counts": dict(event_counts),
            "target_touch_instruction_count_distribution": dict(touch_counts),
            "dual_legacy_conditional_gate_true": sum(
                bool(r["dual_legacy_diagnostic_gate"]["eligible_for_conditional_ledger_check"])
                for r in output_rows
            ),
        },
        "limitations": [
            "Raw trace covers 16 captured c4 blocks, not global discovery or completeness.",
            "Successful structural references are not economic fills or settlements.",
            "Event co-presence and decoded bytes are not bound to this candidate economically.",
            "Token-2022 operands and endpoints do not establish fees, hooks, withheld amounts, "
            "reserves, or realized effects; no Token-2022 equations were run.",
            "Endpoint token amounts are boundary facts where returned. Missing rows are unknown, "
            "never zero. Lamports are recorded without deltas or rent/close attribution.",
            "No class admission, wallet-net price, fee schedule, historical pool initialization, "
            "or training/execution eligibility is established.",
        ],
        "rows": output_rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--census", type=Path, default=EXPECTED_CENSUS)
    parser.add_argument("--qualification", type=Path, default=EXPECTED_QUALIFICATION)
    parser.add_argument("--frame", type=Path, default=EXPECTED_FRAME)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = trace(args.capture_root, args.census, args.qualification, args.frame, args.output)
    print(
        json.dumps(
            {
                "contract": result["contract"],
                "output": str(args.output),
                "rows": len(result["rows"]),
                "counts": result["counts"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
