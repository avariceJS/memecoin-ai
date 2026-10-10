#!/usr/bin/env python3
"""Offline bank-state bracketing feasibility for frozen c4 Token-2022 references."""

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

from memecoin_pumpswap.decoder import TOKEN2022  # noqa: E402
from memecoin_pumpswap.normalize import (  # noqa: E402
    _account_table,
    normalize_compiled_transaction,
)
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _read_block, _sha256  # noqa: E402

CONTRACT = "pumpswap_buy_exact_v1_c4_bank_state_bracketing_feasibility_v1"
TRACE = ROOT / "docs/evidence/pumpswap-buy-exact-v1-c4-success-trace-2026-10-10.json.zst"
TRACE_SHA256 = "f636d745548c266267ca2638cd13eadff33de9595e1e5d57c484e68e39f1a6a9"
CENSUS = ROOT / "docs/evidence/pumpswap-buy-exact-v1-c4-first-frame-census-2026-10-10.json"
QUALIFICATION = ROOT / (
    "docs/evidence/pumpswap-buy-exact-v1-c4-first-frame-pool-v2-prefix-"
    "raw-reconciled-2026-10-10.json"
)
FRAME = ROOT / "docs/evidence/helius-inventory-v3-c4-first-frame-2026-10-10.json"
TOKEN2022_SOURCE = ROOT / (
    "docs/evidence/pumpswap-buy-exact-v1-token2022-transfer-checked-2026-10-10.json"
)
TOKEN2022_SOURCE_SHA256 = "55f52250d3833ca09fe4214e3d5b7132317a3ab28aa7705bfc6c9e09fe1a0e5e"
TOKEN_ACCOUNT_ROLES = (
    "pool_base_token_account",
    "pool_quote_token_account",
    "user_base_token_account",
    "user_quote_token_account",
    "protocol_fee_recipient_token_account",
    "coin_creator_vault_ata",
    "fee_quote_ata_suffix",
)
MINT_ROLES = ("base_mint", "quote_mint")
EXPECTED_SELECTED_BLOCK_TRANSACTIONS = 17_607


def _json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value, raw


def _required_accounts(row: dict[str, Any]) -> dict[str, Any]:
    roles = row["instruction"]["roles"]
    mints = {name: roles[name] for name in MINT_ROLES}
    token_accounts = {name: roles[name] for name in TOKEN_ACCOUNT_ROLES}
    return {
        "mint_roles": mints,
        "token_account_roles": token_accounts,
        "unique_mint_keys": sorted(set(mints.values())),
        "unique_token_account_keys": sorted(set(token_accounts.values())),
        "unique_full_state_keys": sorted(set(mints.values()) | set(token_accounts.values())),
    }


def _message_writable_flags(
    accounts: list[dict[str, Any]], static_count: int, header: dict[str, Any]
) -> dict[str, bool]:
    required = header.get("numRequiredSignatures")
    readonly_signed = header.get("numReadonlySignedAccounts")
    readonly_unsigned = header.get("numReadonlyUnsignedAccounts")
    if any(
        type(value) is not int or value < 0
        for value in (required, readonly_signed, readonly_unsigned)
    ):
        raise ValueError("same_slot_header_privileges_invalid")
    if (
        required > static_count
        or readonly_signed > required
        or readonly_unsigned > static_count - required
        or len(accounts) < static_count
    ):
        raise ValueError("same_slot_header_privileges_out_of_range")
    keys = [account.get("pubkey") for account in accounts]
    if any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("same_slot_duplicate_or_invalid_message_account_key")
    privileges: dict[str, bool] = {}
    for account in accounts:
        index = account.get("index")
        if type(index) is not int or not 0 <= index < len(accounts):
            raise ValueError("same_slot_account_index_invalid")
        source = account.get("source")
        if source == "loaded_writable":
            writable = True
        elif source == "loaded_readonly":
            writable = False
        elif source == "static" and index < static_count:
            if index < required:
                writable = index < required - readonly_signed
            else:
                writable = index < static_count - readonly_unsigned
        else:
            raise ValueError("same_slot_account_privilege_provenance_unknown")
        privileges[account["pubkey"]] = writable
    return privileges


def _index_slot_writable_potential(
    transactions: list[dict[str, Any]], wanted_accounts: set[str]
) -> dict[str, list[dict[str, Any]]]:
    by_account: dict[str, list[dict[str, Any]]] = {account: [] for account in wanted_accounts}
    for tx_index, tx in enumerate(transactions):
        raw_tx = tx.get("transaction")
        message = raw_tx.get("message") if isinstance(raw_tx, dict) else None
        meta = tx.get("meta")
        if not isinstance(message, dict) or not isinstance(meta, dict):
            raise ValueError("same_slot_message_or_meta_missing")
        if "err" not in meta:
            raise ValueError("same_slot_transaction_status_missing")
        if meta.get("err") is not None:
            # Failed transactions cannot commit writes, but are retained as an explicit
            # non-writing attempt in the completeness denominator elsewhere.
            continue
        accounts = _account_table(message, meta)
        account_keys = [account["pubkey"] for account in accounts]
        if len(account_keys) != len(set(account_keys)):
            raise ValueError("same_slot_duplicate_message_account_key")
        static_keys = message.get("accountKeys")
        header = message.get("header")
        if not isinstance(static_keys, list) or not isinstance(header, dict):
            raise ValueError("same_slot_static_keys_or_header_missing")
        static_count = len(static_keys)
        privileges = _message_writable_flags(accounts, static_count, header)
        signatures = raw_tx.get("signatures")
        if not isinstance(signatures, list) or not signatures or not isinstance(signatures[0], str):
            raise ValueError("same_slot_signature_unavailable")
        paths_by_account: dict[str, list[list[int]]] = {account: [] for account in wanted_accounts}
        outer = message.get("instructions")
        groups = meta.get("innerInstructions")
        if not isinstance(outer, list) or not isinstance(groups, list):
            raise ValueError("same_slot_instruction_inventory_missing")
        for outer_index, instruction in enumerate(outer):
            if not isinstance(instruction, dict) or not isinstance(
                instruction.get("accounts"), list
            ):
                raise ValueError("same_slot_outer_instruction_invalid")
            for index in instruction["accounts"]:
                if type(index) is not int or not 0 <= index < len(accounts):
                    raise ValueError("same_slot_outer_account_index_invalid")
                account = accounts[index]["pubkey"]
                if account in paths_by_account:
                    paths_by_account[account].append([outer_index])
        for group in groups:
            if not isinstance(group, dict) or type(group.get("index")) is not int:
                raise ValueError("same_slot_inner_group_invalid")
            outer_index = group["index"]
            instructions = group.get("instructions")
            if not 0 <= outer_index < len(outer) or not isinstance(instructions, list):
                raise ValueError("same_slot_inner_instructions_invalid")
            for inner_index, instruction in enumerate(instructions):
                if not isinstance(instruction, dict) or not isinstance(
                    instruction.get("accounts"), list
                ):
                    raise ValueError("same_slot_inner_instruction_invalid")
                for index in instruction["accounts"]:
                    if type(index) is not int or not 0 <= index < len(accounts):
                        raise ValueError("same_slot_inner_account_index_invalid")
                    account = accounts[index]["pubkey"]
                    if account in paths_by_account:
                        paths_by_account[account].append([outer_index, inner_index])
        for account in wanted_accounts.intersection(privileges):
            if not privileges[account]:
                continue
            account_rows = [row for row in accounts if row["pubkey"] == account]
            if len(account_rows) != 1:
                raise ValueError("same_slot_account_identity_ambiguous")
            paths = paths_by_account[account]
            by_account[account].append(
                {
                    "transaction_index": tx_index,
                    "signature": signatures[0],
                    "writable_potential": True,
                    "instruction_paths_referencing_account": paths,
                    "account_provenance": account_rows[0],
                    "privilege_semantics": (
                        "transaction_message_writable_potential_not_proof_of_program_write"
                    ),
                }
            )
    return by_account


def _endpoint_status(row: dict[str, Any], accounts: list[str]) -> dict[str, Any]:
    by_key = {item["account"]: item for item in row["target_account_endpoint_availability"]}
    missing = []
    complete = []
    for account in accounts:
        endpoint = by_key.get(account)
        if endpoint and endpoint.get("token_endpoint_state") == "both_rows_present":
            complete.append(account)
        else:
            missing.append(account)
    return {
        "required_token_account_count": len(accounts),
        "both_transaction_endpoint_rows_present_count": len(complete),
        "missing_or_partial_endpoint_count": len(missing),
        "complete_accounts": complete,
        "unknown_accounts": missing,
        "semantics": (
            "transaction_boundary_provider_rows_only_not_bank_state_or_candidate_attribution"
        ),
    }


def _candidate_gates(
    row: dict[str, Any], required: dict[str, Any], occurrences: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    events = row.get("events", [])
    matching_events = [
        event
        for event in events
        if event.get("candidate_descendant") is True
        and event.get("event_name") == "BuyEvent"
        and event.get("event_exact") is True
        and event.get("event_roles_match_candidate_structure") is True
    ]
    endpoints = _endpoint_status(row, required["unique_token_account_keys"])
    sibling_paths = [
        {
            "path": item["path"],
            "program_id": item["program_id"],
            "accounts": item["touched_target_accounts"],
        }
        for item in row.get("ordered_target_account_instruction_trace", [])
        if item.get("relation") != "candidate_subtree"
    ]
    mint_keys = set(required["unique_mint_keys"])
    mint_sibling_paths = [
        item for item in sibling_paths if mint_keys.intersection(item["accounts"])
    ]
    mint_clear = all(not occurrences[key] for key in required["unique_mint_keys"])
    full_clear = all(not occurrences[key] for key in required["unique_full_state_keys"])
    event_clear = len(matching_events) == 1 and row.get("event_descendant_count") == 1
    endpoints_clear = endpoints["missing_or_partial_endpoint_count"] == 0
    sibling_clear = not sibling_paths
    return {
        "event_gate": "one_exact_role_matching_descendant_buy_event"
        if event_clear
        else "event_missing_nonunique_or_unqualified",
        "event_descendant_count": row.get("event_descendant_count"),
        "exact_role_matching_descendant_count": len(matching_events),
        "endpoint_gate": "all_required_token_account_endpoint_rows_present"
        if endpoints_clear
        else "required_token_account_endpoint_missing_or_partial",
        "endpoint_completeness": endpoints,
        "sibling_gate": "no_outside_candidate_subtree_target_account_touch"
        if sibling_clear
        else "same_transaction_sibling_or_unrelated_target_touch_unresolved",
        "unresolved_sibling_paths": sibling_paths,
        "mint_sibling_paths": mint_sibling_paths,
        "mint_only_no_other_transaction_writable_potential": mint_clear,
        "full_state_no_other_transaction_writable_potential": full_clear,
        "mint_only_state_snapshot_bracket_feasible": (
            mint_clear and event_clear and not mint_sibling_paths
        ),
        "economic_path_qualified": False,
        "full_state_slot_bracket_feasible": full_clear
        and event_clear
        and endpoints_clear
        and sibling_clear,
        "archive_state_obtained": False,
    }


def _extension_requirements(row: dict[str, Any]) -> dict[str, Any]:
    token2022 = []
    for item in row.get("ordered_target_account_instruction_trace", []):
        if item.get("program_id") != TOKEN2022:
            continue
        try:
            wire = bytes.fromhex(item["wire_hex"])
        except (KeyError, ValueError) as exc:
            raise ValueError("token2022_wire_invalid") from exc
        token2022.append(
            {
                "path": item["path"],
                "transaction_order": item["transaction_order"],
                "wire_tag": wire[0] if wire else None,
                "wire_length_bytes": len(wire),
                "instruction_class": "wire_tag_only_no_effect_decoding",
                "account_order": item.get("ordered_accounts"),
            }
        )
    return {
        "target_touching_token2022_instruction_count": len(token2022),
        "target_touching_token2022_instructions": token2022,
        "bank_time_requirements": [
            "finalized raw mint and token-account data bytes at the bank state of interest",
            "owner, lamports and account lifecycle/existence at each state boundary",
            "Token-2022 TLV extension types and values from those exact bytes",
            "TransferFeeConfig active epoch and older/newer fee parameters when present",
            "TransferFeeAmount withheld token-account amount when present",
            "TransferHook program/config and ordered hook account dependencies when present",
            "confidential-transfer state/dependencies when present; otherwise remain unsupported",
            "native/wrapped-native and account-close/rent dependencies when present",
        ],
        "wire_classification": (
            "tag_and_length_only; no transfer, fee, hook, withheld, or effect inference"
        ),
    }


def build_manifest(trace: dict[str, Any], blocks: dict[int, dict[str, Any]]) -> dict[str, Any]:
    rows = [
        row
        for row in trace.get("rows", [])
        if row.get("instruction", {}).get("base_token_program") == TOKEN2022
    ]
    if len(rows) != 140:
        raise ValueError("token2022_frozen_denominator_mismatch")
    rows_by_slot = blocks
    slot_writable_indexes: dict[int, dict[str, list[dict[str, Any]]]] = {}
    accounts_by_slot: dict[int, set[str]] = {}
    block_transaction_status: dict[int, dict[str, int]] = {}
    for candidate in rows:
        slot = candidate["identity"]["slot"]
        accounts_by_slot.setdefault(slot, set()).update(
            _required_accounts(candidate)["unique_full_state_keys"]
        )
    for slot, block in rows_by_slot.items():
        if slot in accounts_by_slot:
            statuses = [tx.get("meta", {}).get("err", "unknown") for tx in block["transactions"]]
            block_transaction_status[slot] = {
                "transactions": len(statuses),
                "successful": sum(status is None for status in statuses),
                "failed": sum(status is not None and status != "unknown" for status in statuses),
                "status_unknown": sum(status == "unknown" for status in statuses),
            }
            slot_writable_indexes[slot] = _index_slot_writable_potential(
                block["transactions"], accounts_by_slot[slot]
            )
    if sum(item["transactions"] for item in block_transaction_status.values()) != (
        EXPECTED_SELECTED_BLOCK_TRANSACTIONS
    ) or any(item["status_unknown"] for item in block_transaction_status.values()):
        raise ValueError("selected_block_transaction_denominator_mismatch")
    output_rows = []
    counts = Counter()
    for row in rows:
        identity = row["identity"]
        slot, target_index = identity["slot"], identity["transaction_index"]
        block = rows_by_slot.get(slot)
        if block is None:
            raise ValueError("candidate_slot_capture_missing")
        transactions = block["transactions"]
        if not 0 <= target_index < len(transactions):
            raise ValueError("candidate_transaction_index_out_of_bounds")
        target_tx = transactions[target_index]
        normalized = normalize_compiled_transaction(target_tx)
        signatures = normalized["transaction_signatures"]
        if not signatures or signatures[0] != identity["signature"]:
            raise ValueError("candidate_signature_binding_mismatch")
        required = _required_accounts(row)
        all_occurrences = {}
        for key in required["unique_full_state_keys"]:
            all_occurrences[key] = [
                {
                    **item,
                    "order_relative_to_target": "earlier"
                    if item["transaction_index"] < target_index
                    else "later",
                }
                for item in slot_writable_indexes[slot].get(key, [])
                if item["transaction_index"] != target_index
            ]
        gates = _candidate_gates(row, required, all_occurrences)
        event_state = (
            "one_exact_role_matching_descendant_buy_event"
            if gates["exact_role_matching_descendant_count"] == 1
            and gates["event_descendant_count"] == 1
            else "absent_nonunique_or_unqualified"
        )
        endpoint_state = gates["endpoint_completeness"]
        counts["rows"] += 1
        counts["mint_only_state_snapshot_bracket_feasible"] += gates[
            "mint_only_state_snapshot_bracket_feasible"
        ]
        counts["mint_accounts_without_other_transaction_writable_potential"] += gates[
            "mint_only_no_other_transaction_writable_potential"
        ]
        counts["full_state_slot_bracket_feasible"] += gates["full_state_slot_bracket_feasible"]
        counts[event_state] += 1
        counts["rows_with_missing_token_endpoints"] += (
            endpoint_state["missing_or_partial_endpoint_count"] > 0
        )
        counts["rows_with_unresolved_sibling_paths"] += bool(gates["unresolved_sibling_paths"])
        counts["required_mint_accounts"] += len(required["unique_mint_keys"])
        counts["required_token_accounts"] += len(required["unique_token_account_keys"])
        counts["same_slot_writable_potential_occurrences"] += sum(
            len(items) for items in all_occurrences.values()
        )
        counts["accounts_with_earlier_writable_potential"] += sum(
            any(item["transaction_index"] < target_index for item in items)
            for items in all_occurrences.values()
        )
        counts["accounts_with_later_writable_potential"] += sum(
            any(item["transaction_index"] > target_index for item in items)
            for items in all_occurrences.values()
        )
        source_capture = next(item for item in trace["source"]["captures"] if item["slot"] == slot)
        account_occurrences = []
        for account in required["unique_full_state_keys"]:
            account_occurrences.append(
                {
                    "account": account,
                    "roles": sorted(
                        role
                        for group in (required["mint_roles"], required["token_account_roles"])
                        for role, value in group.items()
                        if value == account
                    ),
                    "account_type": "mint"
                    if account in required["unique_mint_keys"]
                    else "token_account",
                    "target_transaction_account_provenance": next(
                        (a for a in normalized["accounts"] if a["pubkey"] == account), None
                    ),
                    "same_slot_other_transaction_writable_potential": all_occurrences[account],
                }
            )
        output_rows.append(
            {
                "identity": identity,
                "capture_response_sha256": source_capture["response_sha256"],
                "capture_compressed_sha256": source_capture["compressed_sha256"],
                "capture_receipt_time_ns": source_capture["receipt_ns"],
                "required_state_accounts": required,
                "account_temporal_occurrences": account_occurrences,
                "event_state": event_state,
                "endpoint_state": endpoint_state,
                "extension_dependent_instruction_classes": _extension_requirements(row),
                "gates": gates,
                "archive_bracket": {
                    "pre_state_slot": slot - 1,
                    "post_state_slot": slot,
                    "required_semantics": "finalized_account_state_after_slot_bank_execution",
                    "eligibility": "eligible_for_single_row_control"
                    if gates["full_state_slot_bracket_feasible"]
                    else "not_eligible",
                    "failure_reasons": [
                        reason
                        for reason, failed in (
                            (
                                "other_same_slot_writable_potential",
                                not gates["full_state_no_other_transaction_writable_potential"],
                            ),
                            (
                                "event_missing_nonunique_or_unqualified",
                                gates["event_gate"]
                                != "one_exact_role_matching_descendant_buy_event",
                            ),
                            (
                                "endpoint_missing_or_partial",
                                gates["endpoint_gate"]
                                != "all_required_token_account_endpoint_rows_present",
                            ),
                            (
                                "same_transaction_sibling_touch_unresolved",
                                gates["sibling_gate"]
                                != "no_outside_candidate_subtree_target_account_touch",
                            ),
                        )
                        if failed
                    ],
                    "mint_only_failure_reasons": [
                        reason
                        for reason, failed in (
                            (
                                "other_same_slot_writable_potential_on_mint",
                                not gates["mint_only_no_other_transaction_writable_potential"],
                            ),
                            (
                                "same_transaction_sibling_mint_touch",
                                bool(gates["mint_sibling_paths"]),
                            ),
                            (
                                "event_missing_nonunique_or_unqualified",
                                gates["event_gate"]
                                != "one_exact_role_matching_descendant_buy_event",
                            ),
                        )
                        if failed
                    ],
                },
            }
        )
    output_rows.sort(
        key=lambda row: (
            row["identity"]["slot"],
            row["identity"]["transaction_index"],
            row["identity"]["instruction_path"],
            row["identity"]["signature"],
        )
    )
    return {
        "rows": output_rows,
        "counts": dict(counts),
        "selected_block_transaction_status": block_transaction_status,
    }


def run(capture_root: Path, output: Path) -> dict[str, Any]:
    capture_root, output = capture_root.resolve(), output.resolve()
    if output.exists():
        raise ValueError("output_exists_refusing_overwrite")
    trace_compressed = TRACE.read_bytes()
    if _sha256(trace_compressed) != TRACE_SHA256:
        raise ValueError("frozen_trace_hash_mismatch")
    zstd = shutil.which("zstd")
    if zstd is None:
        raise ValueError("zstd_cli_unavailable")
    trace_raw = (
        __import__("subprocess")
        .run([zstd, "-dc", str(TRACE)], capture_output=True, check=True)
        .stdout
    )
    trace = json.loads(trace_raw)
    census, census_raw = _json(CENSUS)
    qualification, qualification_raw = _json(QUALIFICATION)
    frame, frame_raw = _json(FRAME)
    token_source, token_source_raw = _json(TOKEN2022_SOURCE)
    if capture_root != Path(trace["source"]["capture_root"]).resolve():
        raise ValueError("capture_root_does_not_match_trace")
    if (
        _sha256(token_source_raw) != TOKEN2022_SOURCE_SHA256
        or trace["source"]["token_program_source_receipt_sha256"] != TOKEN2022_SOURCE_SHA256
        or token_source.get("source", {}).get("token2022_official_source", {}).get("valid")
        is not True
        or trace["source"]["census_sha256"] != _sha256(census_raw)
        or trace["source"]["qualification_sha256"] != _sha256(qualification_raw)
        or trace["source"]["frame_receipt_sha256"] != _sha256(frame_raw)
        or frame.get("frame", {}).get("status") != "frame_complete"
    ):
        raise ValueError("frozen_source_lineage_mismatch")
    captures = _capture_rows(capture_root, v3_layout=True)
    trace_captures = {item["slot"]: item for item in trace["source"]["captures"]}
    if len(captures) != 16 or set(trace_captures) != {
        request["params"][0] for _path, _receipt, request in captures
    }:
        raise ValueError("full_block_capture_denominator_mismatch")
    blocks = {}
    capture_hashes = []
    for path, receipt, request in captures:
        slot = request["params"][0]
        expected = trace_captures[slot]
        if (
            receipt.get("response_sha256") != expected["response_sha256"]
            or receipt.get("storage", {}).get("sha256") != expected["compressed_sha256"]
            or receipt.get("received_ns") != expected["receipt_ns"]
        ):
            raise ValueError("capture_receipt_lineage_mismatch")
        blocks[slot] = _read_block(zstd, path, receipt)
        capture_hashes.append({"slot": slot, **expected})
    result = build_manifest(trace, blocks)
    selected = next(
        (row for row in result["rows"] if row["gates"]["full_state_slot_bracket_feasible"]),
        None,
    )
    acquisition_control = None
    if selected is not None:
        acquisition_control = {
            "identity": selected["identity"],
            "account_keys": selected["required_state_accounts"]["unique_full_state_keys"],
            "pre_state_slot": selected["archive_bracket"]["pre_state_slot"],
            "post_state_slot": selected["archive_bracket"]["post_state_slot"],
            "status": "offline_control_only_not_executed",
            "provider_method_or_archive_path": "unqualified",
        }
    receipt = {
        "contract": CONTRACT,
        "schema_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": (
            "exhaustive offline temporal-state feasibility over the 140 frozen c4 "
            "Token-2022-base successful references"
        ),
        "source": {
            "trace_path": str(TRACE),
            "trace_compressed_sha256": _sha256(trace_compressed),
            "trace_uncompressed_sha256": _sha256(trace_raw),
            "census_sha256": _sha256(census_raw),
            "qualification_sha256": _sha256(qualification_raw),
            "frame_receipt_sha256": _sha256(frame_raw),
            "token2022_source_receipt_sha256": _sha256(token_source_raw),
            "token2022_source_commit": token_source["source"]["token2022_official_source"][
                "commit"
            ],
            "capture_root": str(capture_root),
            "captures": capture_hashes,
            "normalizer": "solana_compiled_instruction_resolution_v1",
            "privilege_source": (
                "Solana message header plus loaded writable/readonly address provenance"
            ),
        },
        "semantics": {
            "same_slot_writable_potential": (
                "message-level writable privilege in another successful transaction; "
                "possible write only, not proof the account was written"
            ),
            "temporal_order": "transaction index within receipt-hash-verified full block",
            "slot_archive_bracket": (
                "state at finalized slot S-1 and after finalized slot S brackets target "
                "transaction only when no other same-slot transaction can write any required "
                "state account and trace gates pass"
            ),
            "failed_transactions": (
                "retained in full-block denominator; no committed account writes assumed "
                "from failed transaction"
            ),
            "missing_or_censored": "unknown; never zero",
        },
        "future_state_evidence_contract": {
            "required_per_account": [
                "address",
                "context slot/bank state",
                "owner",
                "lamports",
                "executable",
                "raw data bytes",
                "raw data sha256",
                "existence/closure state",
                "provider receipt time",
                "request identity and accounting",
            ],
            "required_for_token2022": [
                "mint and account TLV extensions from exact raw bytes",
                "active fee epoch/config",
                "withheld token-account amount",
                "hook program/config and ordered dependency accounts",
                "confidential state and dependencies",
                "native/wrapped-native and lifecycle effects when applicable",
            ],
            "temporal_limit": (
                "later getAccountInfo, simulation, or present-day state does not establish "
                "historical bank state"
            ),
            "acquisition_policy": (
                "no RPC, no paid probe, no retry, and no acquisition-control request emitted "
                "unless one row passes all offline gates"
            ),
        },
        "denominators": {
            "successful_frozen_references": len(result["rows"]),
            "failed_attempts_preserved_in_census_only": 30,
            "receipt_hash_verified_full_blocks": len(capture_hashes),
        },
        "selected_block_transaction_status": {
            str(slot): result["selected_block_transaction_status"][slot]
            for slot in sorted(result["selected_block_transaction_status"])
        },
        "counts": result["counts"],
        "single_row_acquisition_control": acquisition_control,
        "limitations": [
            "16 captured full blocks are a frozen selected-slot sample, not global market "
            "completeness.",
            "Writable privilege is only a conservative potential-write indicator; program "
            "semantics and actual writes are not inferred.",
            "No historical account state was fetched or reconstructed; this is feasibility only.",
            "No economic equation, fee, settlement, Token-2022 effect, or decoder/class "
            "admission is qualified.",
            "A later independent economic audit still requires a fresh disjoint frozen "
            "sample after the state path and predicate are qualified.",
        ],
        "rows": result["rows"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = run(args.capture_root, args.output)
    print(
        json.dumps(
            {
                "contract": receipt["contract"],
                "rows": len(receipt["rows"]),
                "counts": receipt["counts"],
                "single_row_control": receipt["single_row_acquisition_control"] is not None,
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
