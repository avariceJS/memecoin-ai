#!/usr/bin/env python3
"""Offline wire-fact feasibility taxonomy for the frozen c4 buyExact dev class."""

from __future__ import annotations

import argparse
import json
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

from memecoin_pumpswap.buy_exact_v1 import ACCOUNT_ROLES, DISCRIMINATOR  # noqa: E402
from memecoin_pumpswap.compiled_economics import _flatten, _transaction_tree  # noqa: E402
from memecoin_pumpswap.decoder import EVENT_TAG, PROGRAM, TOKEN, event_payload  # noqa: E402
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402
from tools.brain_senses_buy_exact_v1_c4_success_trace import (  # noqa: E402
    _amount_operand,
    _sha256,
    _transaction_endpoint_token_delta,
)
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _read_block  # noqa: E402
from tools.brain_senses_buy_exact_v1_pool_v2_prefix_qualifier_v2 import (  # noqa: E402
    _compiled_instruction,
    _transaction_privilege,
)

REPLAY_RECEIPT = (
    ROOT / "docs/evidence/pumpswap-buy-exact-v1-c4-pool-v2-development-replay-2026-10-11.json"
)
CAMPAIGN_ROOT = ROOT / "work/brain-senses-live-helius-inventory-v3-20261010-4"
CONTRACT = "pumpswap_buy_exact_v1_c4_evidence_feasibility_taxonomy_v1"
EXPECTED_REPLAY_SHA256 = "04bc668dbf85b348ebdeb264dcff01022028b66e4723ddd2b911ec388681226f"


def _read_object(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value, raw


def _identity(row: dict[str, Any]) -> tuple[int, int, tuple[int, ...], str]:
    path = row.get("path")
    if not isinstance(path, list) or any(type(i) is not int or i < 0 for i in path):
        raise ValueError("candidate_path_invalid")
    return row["slot"], row["transaction_index"], tuple(path), row["signature"]


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _token_endpoints(normalized: dict[str, Any], roles: dict[str, str]) -> dict[str, Any]:
    account_keys = [item["pubkey"] for item in normalized["accounts"]]
    target_roles = {
        key: sorted(name for name, value in roles.items() if value == key)
        for key in set(roles.values())
    }
    pre_map: dict[str, dict[str, Any]] = {}
    post_map: dict[str, dict[str, Any]] = {}
    for _side, values, output in (
        ("pre", normalized["pre_token_balances"], pre_map),
        ("post", normalized["post_token_balances"], post_map),
    ):
        if not isinstance(values, list):
            continue
        for value in values:
            index = value.get("accountIndex")
            if type(index) is not int or not 0 <= index < len(account_keys):
                raise ValueError("token_endpoint_account_index_invalid")
            account = account_keys[index]
            if account not in target_roles:
                continue
            ui = value.get("uiTokenAmount")
            if not isinstance(ui, dict):
                raise ValueError("token_endpoint_amount_missing")
            amount, decimals = ui.get("amount"), ui.get("decimals")
            if (
                not isinstance(amount, str)
                or not amount.isascii()
                or not amount.isdecimal()
                or type(decimals) is not int
            ):
                raise ValueError("token_endpoint_amount_shape_invalid")
            output[account] = {
                "mint": value.get("mint"),
                "token_program_id": value.get("programId"),
                "owner": value.get("owner"),
                "amount_atomic": int(amount),
                "decimals_metadata": decimals,
                "unit": "atomic_units_of_mint",
            }
    rows = []
    for account in sorted(target_roles):
        before, after = pre_map.get(account), post_map.get(account)
        delta, state = _transaction_endpoint_token_delta(before, after)
        rows.append(
            {
                "account": account,
                "candidate_roles": target_roles[account],
                "pre": before,
                "post": after,
                "boundary_delta_atomic": delta,
                "unit": "atomic_units_of_mint",
                "state": state,
                "attribution": "whole_transaction_boundary_only_not_candidate_buy",
            }
        )
    return {
        "provider_pre_post_token_balance_rows": rows,
        "missing_rows_are_unknown_not_zero": True,
        "historical_bank_time_account_or_mint_state": "not_present_in_getBlock_capture",
    }


def _trace_candidate(
    tx: dict[str, Any],
    block: dict[str, Any],
    source: dict[str, Any],
    replay_mismatch_ids: set[tuple[int, int, tuple[int, ...], str]],
) -> dict[str, Any]:
    normalized = normalize_compiled_transaction(tx)
    signatures = normalized["transaction_signatures"]
    if not signatures or signatures[0] != source["signature"]:
        raise ValueError("selected_signature_mismatch")
    path = source["path"]
    tree = _transaction_tree(normalized)
    all_nodes = [node for root in tree for node in _flatten(root)]
    candidate_node = next((n for n in all_nodes if n["instruction"]["path"] == path), None)
    if candidate_node is None:
        raise ValueError("candidate_path_missing_from_normalized_transaction")
    candidate = candidate_node["instruction"]
    wire = bytes.fromhex(candidate["data_hex"])
    if len(wire) != 25 or wire[:8] != DISCRIMINATOR or len(candidate["accounts"]) != 26:
        raise ValueError("candidate_wire_or_roles_mismatch")
    roles = dict(zip(ACCOUNT_ROLES, candidate["accounts"][:23], strict=True))
    roles.update(
        pool_v2_prefix=candidate["accounts"][23],
        fee_wallet_suffix=candidate["accounts"][24],
        fee_quote_ata_suffix=candidate["accounts"][25],
    )
    descendant = _flatten(candidate_node)
    subtree_ids = {id(n) for n in descendant}
    descendant_events = []
    for order, node in enumerate(all_nodes):
        if id(node) not in subtree_ids:
            continue
        ix = node["instruction"]
        if ix["program_id"] != PROGRAM:
            continue
        event_wire = bytes.fromhex(ix["data_hex"])
        if not event_wire.startswith(EVENT_TAG):
            continue
        try:
            decoded_event = event_payload(event_wire)
            event_state = "pinned_event_abi_decoded"
        except (ValueError, KeyError, TypeError) as exc:
            decoded_event = None
            event_state = f"unparsed_event_wire:{exc}"
        role_pairs = {
            "pool": "pool",
            "user": "user",
            "user_base_token_account": "user_base_token_account",
            "user_quote_token_account": "user_quote_token_account",
            "protocol_fee_recipient": "protocol_fee_recipient",
            "protocol_fee_recipient_token_account": "protocol_fee_recipient_token_account",
        }
        role_match = None
        if decoded_event is not None and decoded_event.get("name") == "BuyEvent":
            role_match = all(
                decoded_event["fields"].get(event_name) == roles.get(role_name)
                for event_name, role_name in role_pairs.items()
            )
        descendant_events.append(
            {
                "transaction_order": order,
                "path": ix["path"],
                "stack_height": ix["stack_height"],
                "candidate_descendant": True,
                "association_state": "transaction_tree_descendant_not_economic_binding",
                "wire_hex": event_wire.hex(),
                "wire_sha256": _sha256(event_wire),
                "event_state": event_state,
                "event_name": decoded_event.get("name") if decoded_event else None,
                "event_exact": decoded_event.get("exact") if decoded_event else None,
                "event_roles_match_candidate_structure": role_match,
                "unknown_suffix_hex": decoded_event.get("unknown_suffix_hex")
                if decoded_event
                else None,
                "parsed_fields": decoded_event.get("fields") if decoded_event else None,
            }
        )
    event_facts = []
    for event in descendant_events:
        fields = event.get("parsed_fields") or {}
        event_facts.append(
            {
                **event,
                "candidate_argument_wire_comparisons": {
                    "instruction_name_matches": fields.get("ix_name") == "buy_exact_quote_in",
                    "track_volume_matches": fields.get("track_volume")
                    == source.get("arguments", {}).get("track_volume"),
                    "max_quote_amount_in_matches": fields.get("max_quote_amount_in")
                    == source.get("arguments", {}).get("spendable_quote_in"),
                    "min_base_amount_out_matches": fields.get("min_base_amount_out")
                    == source.get("arguments", {}).get("min_base_amount_out"),
                },
                "event_amounts_interpretation": (
                    "amounts_claimed_by_decoded_event_wire_not_realized_effects"
                ),
            }
        )
    transfer_wires = []
    outside_touches = []
    descendant_signed_deltas: dict[str, int] = {}
    descendant_delta_mints: dict[str, str | None] = {}
    descendant_delta_classifiable = True
    target_accounts = set(roles.values())
    for order, node in enumerate(all_nodes):
        ix = node["instruction"]
        accounts = ix["accounts"]
        data = bytes.fromhex(ix["data_hex"])
        operand = _amount_operand(ix["program_id"], data, accounts)
        linked = []
        if operand is not None:
            source_account = accounts[0] if len(accounts) > 0 else None
            destination_index = 2 if operand["wire_variant"] == "transfer_checked" else 1
            destination_account = (
                accounts[destination_index] if len(accounts) > destination_index else None
            )
            authority_index = 3 if operand["wire_variant"] == "transfer_checked" else 2
            authority_account = (
                accounts[authority_index] if len(accounts) > authority_index else None
            )
            linked = [
                {
                    "candidate_roles": sorted(
                        name for name, value in roles.items() if value == account
                    ),
                    "account": account,
                    "position": index,
                }
                for index, account in enumerate(accounts)
                if account in target_accounts
            ]
            transfer_wires.append(
                {
                    "order": order,
                    "path": ix["path"],
                    "candidate_descendant": id(node) in subtree_ids,
                    "program_id": ix["program_id"],
                    "wire_sha256": _sha256(data),
                    "wire_variant": operand["wire_variant"],
                    "amount_operand_atomic": operand["declared_amount_atomic"],
                    "amount_unit": operand["unit"],
                    "mint_pubkey_from_wire_account": operand["mint_pubkey_from_wire_account"],
                    "declared_decimals": operand["declared_decimals"],
                    "source_account": source_account,
                    "destination_account": destination_account,
                    "authority_account": authority_account,
                    "wire_mint_candidate_role_match": (
                        "base_mint"
                        if operand["mint_pubkey_from_wire_account"] == roles["base_mint"]
                        else "quote_mint"
                        if operand["mint_pubkey_from_wire_account"] == roles["quote_mint"]
                        else "unknown_or_not_encoded"
                    ),
                    "wire_program_candidate_role_match": (
                        "base_token_program"
                        if ix["program_id"] == roles["base_token_program"]
                        else "quote_token_program"
                        if ix["program_id"] == roles["quote_token_program"]
                        else "unknown_or_not_candidate_program"
                    ),
                    "interpretation": operand["interpretation"],
                    "target_role_links": linked,
                }
            )
            if id(node) in subtree_ids:
                if (
                    operand["wire_variant"] == "transfer_checked"
                    and ix["program_id"] == TOKEN
                    and type(operand["declared_amount_atomic"]) is int
                    and source_account is not None
                    and destination_account is not None
                    and source_account != destination_account
                    and operand["mint_pubkey_from_wire_account"]
                    in (roles["base_mint"], roles["quote_mint"])
                ):
                    descendant_signed_deltas[source_account] = (
                        descendant_signed_deltas.get(source_account, 0)
                        - operand["declared_amount_atomic"]
                    )
                    descendant_signed_deltas[destination_account] = (
                        descendant_signed_deltas.get(destination_account, 0)
                        + operand["declared_amount_atomic"]
                    )
                    descendant_delta_mints[source_account] = operand[
                        "mint_pubkey_from_wire_account"
                    ]
                    descendant_delta_mints[destination_account] = operand[
                        "mint_pubkey_from_wire_account"
                    ]
                else:
                    descendant_delta_classifiable = False
        touched = sorted(target_accounts.intersection(accounts))
        if touched and id(node) not in subtree_ids and ix["path"] != path:
            outside_touches.append(
                {
                    "order": order,
                    "path": ix["path"],
                    "program_id": ix["program_id"],
                    "target_accounts": touched,
                    "target_roles": sorted(
                        {name for name, value in roles.items() if value in touched}
                    ),
                    "wire_sha256": _sha256(data),
                }
            )
    status = (
        "failed_attempt_only" if normalized["meta_error"] is not None else "successful_transaction"
    )
    identity = _identity(source)
    if identity in replay_mismatch_ids:
        structural_state = "known_successful_structural_mismatch_from_replay_receipt"
    elif status == "successful_transaction":
        structural_state = (
            "structural_pass_by_frozen_partition_and_exact_success_identity_complement"
        )
    else:
        # The frozen replay reports only this failed-reference mismatch check. Recompute it from
        # raw transaction privileges; do not infer economic effects from a reverted transaction.
        compiled = _compiled_instruction(tx, path)
        indexes = compiled.get("accounts")
        if not isinstance(indexes, list) or len(indexes) != 26 or type(indexes[23]) is not int:
            raise ValueError("pool_v2_compiled_index_missing")
        writable, signer = _transaction_privilege(tx, indexes[23])
        structural_state = (
            "failed_structural_mismatch_pool_v2_writable"
            if writable
            else "failed_structural_pass_pool_v2_readonly"
        )
        if signer:
            raise ValueError("pool_v2_suffix_unexpected_signer")
    endpoints = (
        _token_endpoints(normalized, roles)
        if status == "successful_transaction"
        else {
            "state": "failed_attempt_no_committed_token_effects_from_transaction",
            "provider_pre_post_rows_retained_only_as_failed_attempt_metadata": True,
            "historical_bank_time_account_or_mint_state": "not_present_in_getBlock_capture",
        }
    )
    endpoint_by_account = {
        row["account"]: row for row in endpoints.get("provider_pre_post_token_balance_rows", [])
    }
    endpoint_comparisons = []
    for account, candidate_delta in sorted(descendant_signed_deltas.items()):
        endpoint = endpoint_by_account.get(account)
        if endpoint is None or endpoint.get("pre") is None or endpoint.get("post") is None:
            state = "unknown_missing_provider_endpoint"
            observed_delta = None
        else:
            observed_delta = endpoint["post"]["amount_atomic"] - endpoint["pre"]["amount_atomic"]
            state = (
                "matches_candidate_subtree_wire_delta"
                if observed_delta == candidate_delta
                else "transaction_boundary_residual_vs_candidate_subtree_wire_delta"
            )
        endpoint_comparisons.append(
            {
                "account": account,
                "candidate_roles": sorted(
                    name for name, value in roles.items() if value == account
                ),
                "mint_from_candidate_subtree_transfer_wire": descendant_delta_mints.get(account),
                "candidate_subtree_transfer_delta_atomic": candidate_delta,
                "provider_transaction_boundary_delta_atomic": observed_delta,
                "state": state,
            }
        )
    legacy_pool_v2 = roles["base_token_program"] == TOKEN and roles["quote_token_program"] == TOKEN
    residual_states = {item["state"] for item in endpoint_comparisons}
    if not descendant_delta_classifiable:
        reconciliation = "unclassified_descendant_transfer_wire_present"
    elif any(state == "unknown_missing_provider_endpoint" for state in residual_states):
        reconciliation = "censored_missing_endpoint"
    elif any(
        state == "transaction_boundary_residual_vs_candidate_subtree_wire_delta"
        for state in residual_states
    ):
        reconciliation = "censored_transaction_boundary_residual"
    elif endpoint_comparisons:
        reconciliation = (
            "endpoint_deltas_match_candidate_subtree_wire_only_not_full_economic_qualification"
        )
    else:
        reconciliation = "no_candidate_subtree_transfer_delta_available"
    return {
        "identity": {
            "slot": source["slot"],
            "transaction_index": source["transaction_index"],
            "path": path,
            "signature": source["signature"],
        },
        "transaction_state": status,
        "structural_taxonomy": structural_state,
        "block_time_unix_seconds_reported": block.get("blockTime"),
        "capture_availability_time_ns": source.get("availability_ns"),
        "candidate_arguments": source.get("arguments"),
        "candidate_roles": roles,
        "ordered_candidate_descendant_events": event_facts,
        "ordered_token_transfer_wire_facts": transfer_wires,
        "legacy_candidate_subtree_vs_boundary_feasibility": {
            "applicable_token_program_pair": legacy_pool_v2,
            "candidate_transfer_subtree_wire_amounts_all_classifiable_as_legacy_transfer_checked": (
                descendant_delta_classifiable
            ),
            "account_delta_comparisons": endpoint_comparisons,
            "state": reconciliation,
            "no_economic_admission": True,
        },
        "whole_transaction_target_touches_outside_candidate_subtree": outside_touches,
        "provider_transaction_boundary_endpoints": endpoints,
        "economic_admission": False,
    }


def build(capture_root: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("output_exists_refusing_overwrite")
    replay, replay_bytes = _read_object(REPLAY_RECEIPT)
    replay_sha = _sha256(replay_bytes)
    if (
        replay_sha != EXPECTED_REPLAY_SHA256
        or replay.get("evidence_id")
        != "pumpswap-buy-exact-v1-c4-pool-v2-development-replay-2026-10-11"
    ):
        raise ValueError("unexpected_replay_receipt")
    if capture_root.resolve() != CAMPAIGN_ROOT.resolve():
        raise ValueError("unexpected_frozen_capture_root")
    mismatch_ids = {
        (row["slot"], row["transaction_index"], tuple(row["instruction_path"]), row["signature"])
        for row in replay.get("structural_success_mismatch_counterexamples", [])
    }
    if len(mismatch_ids) != 3:
        raise ValueError("successful_mismatch_identity_set_invalid")
    output_rows = []
    source_report_pins = []
    capture_pins = []
    counters: Counter[str] = Counter()
    transfer_counters: Counter[str] = Counter()
    event_counters: Counter[str] = Counter()
    outside_touch_counters: Counter[str] = Counter()
    expected_buckets = replay["scope"]["included_buckets"]
    if expected_buckets != [item["bucket"] for item in replay["frames"]]:
        raise ValueError("frame_bucket_order_mismatch")
    for frame in replay["frames"]:
        report_path = ROOT / frame["source_report_path"]
        report, report_bytes = _read_object(report_path)
        if _sha256(report_bytes) != frame["source_report_sha256"]:
            raise ValueError("source_report_hash_mismatch")
        epoch_root = capture_root / "epochs" / frame["bucket"]
        captures = _capture_rows(epoch_root, v3_layout=True)
        by_slot = {}
        for compressed_path, receipt, request in captures:
            params = request.get("params")
            if not isinstance(params, list) or not params or type(params[0]) is not int:
                raise ValueError("capture_request_slot_invalid")
            slot = params[0]
            if slot in by_slot:
                raise ValueError("duplicate_capture_slot")
            by_slot[slot] = (compressed_path, receipt)
            capture_pins.append(
                {
                    "bucket": frame["bucket"],
                    "slot": slot,
                    "response_sha256": receipt["response_sha256"],
                    "compressed_sha256": receipt["storage"]["sha256"],
                    "receipt_ns": receipt["received_ns"],
                    "response_bytes": receipt["response_bytes"],
                    "compressed_bytes": receipt["storage"]["bytes"],
                }
            )
        expected_caps = {item["slot"]: item for item in frame["full_block_captures"]}
        if set(by_slot) != set(expected_caps) or len(by_slot) != 16:
            raise ValueError("frame_capture_slot_set_mismatch")
        blocks = {}
        for slot, (path, receipt) in by_slot.items():
            expected = expected_caps[slot]
            if (
                receipt.get("response_sha256") != expected.get("response_sha256")
                or receipt.get("storage", {}).get("sha256") != expected.get("compressed_sha256")
                or receipt.get("received_ns") != expected.get("receipt_ns")
            ):
                raise ValueError("capture_receipt_pin_mismatch")
            blocks[slot] = _read_block("zstd", path, receipt)
        refs = report.get("rows")
        if not isinstance(refs, list):
            raise ValueError("census_rows_missing")
        candidates = [
            row
            for row in refs
            if row.get("instruction_data_length") == 25
            and row.get("remaining_account_count") == 3
            and row.get("argument_state") == "pinned_idl_v1_shape"
            and isinstance(row.get("instruction_data_hex"), str)
            and bytes.fromhex(row["instruction_data_hex"])[:8] == DISCRIMINATOR
            and bytes.fromhex(row["instruction_data_hex"])[24] in (0, 1)
        ]
        source_report_pins.append(
            {
                "bucket": frame["bucket"],
                "report_path": str(report_path.relative_to(ROOT)),
                "report_sha256": frame["source_report_sha256"],
                "candidate_references": len(candidates),
                "frame_json_sha256": frame["frame_json_sha256"],
                "epoch_plan_sha256": frame["epoch_plan_sha256"],
                "epoch_summary_sha256": frame["epoch_summary_sha256"],
                "reservation_sha256": frame["reservation_sha256"],
                "reservation_release_sha256": frame["reservation_release_sha256"],
                "attempt_context_sha256": frame["attempt_context_sha256"],
            }
        )
        for source in candidates:
            slot, tx_index = source["slot"], source["transaction_index"]
            if (
                slot not in blocks
                or type(tx_index) is not int
                or not 0 <= tx_index < len(blocks[slot]["transactions"])
            ):
                raise ValueError("candidate_block_or_transaction_missing")
            traced = _trace_candidate(
                blocks[slot]["transactions"][tx_index], blocks[slot], source, mismatch_ids
            )
            if (
                traced["identity"]["signature"] != source["signature"]
                or traced["identity"]["path"] != source["path"]
            ):
                raise ValueError("candidate_identity_reconciliation_failed")
            output_rows.append(traced)
            counters[traced["transaction_state"]] += 1
            counters[traced["structural_taxonomy"]] += 1
            event_counters[str(len(traced["ordered_candidate_descendant_events"]))] += 1
            outside_touch_counters[
                str(len(traced["whole_transaction_target_touches_outside_candidate_subtree"]))
            ] += 1
            for item in traced["ordered_token_transfer_wire_facts"]:
                relation = "descendant" if item["candidate_descendant"] else "outside"
                transfer_counters[f"{item['program_id']}:{item['wire_variant']}:{relation}"] += 1
    output_rows.sort(
        key=lambda row: (
            row["identity"]["slot"],
            row["identity"]["transaction_index"],
            tuple(row["identity"]["path"]),
            row["identity"]["signature"],
        )
    )
    if len(output_rows) != replay["aggregate"]["selected_references"] or len(
        {
            (
                r["identity"]["slot"],
                r["identity"]["transaction_index"],
                tuple(r["identity"]["path"]),
                r["identity"]["signature"],
            )
            for r in output_rows
        }
    ) != len(output_rows):
        raise ValueError("aggregate_candidate_population_reconciliation_failed")
    if (
        sum(row["candidate_references"] for row in source_report_pins)
        != replay["aggregate"]["selected_references"]
    ):
        raise ValueError("frame_candidate_count_partition_mismatch")
    if counters["failed_attempt_only"] != 780 or counters["successful_transaction"] != 1553:
        raise ValueError("transaction_status_partition_mismatch")
    if (
        counters["failed_structural_mismatch_pool_v2_writable"] != 13
        or counters["known_successful_structural_mismatch_from_replay_receipt"] != 3
    ):
        raise ValueError("structural_mismatch_partition_mismatch")
    if (
        replay["scope"].get("excluded_tail2_body_opened") is not False
        or replay["scope"].get("provider_requests") != 0
    ):
        raise ValueError("frozen_or_network_boundary_invalid")
    result = {
        "contract": CONTRACT,
        "schema_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "classification": (
            "offline development evidence-feasibility taxonomy; no economic decoder or admission"
        ),
        "source": {
            "replay_receipt_path": str(REPLAY_RECEIPT.relative_to(ROOT)),
            "replay_receipt_sha256": replay_sha,
            "capture_root": str(capture_root),
            "source_reports": source_report_pins,
            "captures": capture_pins,
            "toolchain_sha256": {
                "this_tool": _file_sha256(Path(__file__)),
                "success_trace_helpers": _file_sha256(
                    ROOT / "tools/brain_senses_buy_exact_v1_c4_success_trace.py"
                ),
                "census_capture_helpers": _file_sha256(
                    ROOT / "tools/brain_senses_buy_exact_v1_census.py"
                ),
                "pool_v2_raw_qualifier_helpers": _file_sha256(
                    ROOT / "tools/brain_senses_buy_exact_v1_pool_v2_prefix_qualifier_v2.py"
                ),
                "compiled_economics_tree": _file_sha256(
                    ROOT / "src/memecoin_pumpswap/compiled_economics.py"
                ),
                "normalize": _file_sha256(ROOT / "src/memecoin_pumpswap/normalize.py"),
                "decoder": _file_sha256(ROOT / "src/memecoin_pumpswap/decoder.py"),
                "buy_exact_contract": _file_sha256(ROOT / "src/memecoin_pumpswap/buy_exact_v1.py"),
            },
        },
        "denominators": {
            "captured_class_candidate_references": len(output_rows),
            "frames": len(source_report_pins),
            "full_blocks": len(capture_pins),
            "provider_requests": 0,
            "tail2_opened": False,
        },
        "units": {
            "transfer_operands": "atomic units from instruction data; decimal scaling not applied",
            "token_endpoint_amounts": "atomic units of provider row mint",
            "slot": "slot index",
            "availability": "provider response receipt ns from capture receipt",
        },
        "counts": {
            "transaction_and_structure": dict(sorted(counters.items())),
            "candidate_descendant_event_count_distribution": dict(sorted(event_counters.items())),
            "outside_target_touch_count_distribution": dict(sorted(outside_touch_counters.items())),
            "transfer_wire_counts": dict(sorted(transfer_counters.items())),
        },
        "limitations": [
            "Candidate-set scope is these ten Helius getBlock captures only, not global discovery "
            "or completeness.",
            "Events and instruction operands are ordered wire facts. Their co-presence and amount "
            "operands do not establish realized transfers, candidate-level fills, or settlement.",
            "Successful transaction-boundary token rows are provider metadata endpoints and remain "
            "unattributed across the transaction; missing rows are unknown, never zero.",
            "Failed transactions are attempt-only; no committed economic effect is inferred.",
            "No bank-time mint/account state, Token-2022 extension/withheld state, or intermediate "
            "account state was present in these getBlock captures.",
            "No decoder promotion, exact economic zero, price, fee, reserve, lifecycle, training, "
            "or execution admission is established.",
        ],
        "rows": output_rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, default=CAMPAIGN_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.capture_root, args.output)
    print(
        json.dumps(
            {
                "contract": CONTRACT,
                "output": str(args.output),
                "references": len(result["rows"]),
                "counts": result["counts"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
