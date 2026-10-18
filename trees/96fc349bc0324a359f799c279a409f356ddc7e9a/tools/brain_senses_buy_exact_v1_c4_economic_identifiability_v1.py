#!/usr/bin/env python3
"""Offline evidence-identifiability inventory for the ten selected c4 buyExact frames.

This tool does not decide whether a buy settled or should be admitted economically. It joins the
already source-bound row taxonomy to transaction-boundary fee/lamport and ordered-wire availability
from the same ten pinned full-block captures, then records missingness and unresolved obligations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from memecoin_pumpswap.compiled_economics import _flatten, _transaction_tree  # noqa: E402
from memecoin_pumpswap.decoder import TOKEN, TOKEN2022  # noqa: E402
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402
from memecoin_pumpswap.schema import SCHEMA  # noqa: E402
from tools import brain_senses_c4_captured_class_replay_v1 as c4_replay  # noqa: E402
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _read_block  # noqa: E402

CONTRACT = "pumpswap_buy_exact_v1_c4_economic_identifiability_v1"
REPLAY_RECEIPT = (
    ROOT / "docs/evidence/pumpswap-buy-exact-v1-c4-pool-v2-development-replay-2026-10-11.json"
)
REPLAY_SHA256 = "04bc668dbf85b348ebdeb264dcff01022028b66e4723ddd2b911ec388681226f"
TAXONOMY_RECEIPT = (
    ROOT / "docs/evidence/pumpswap-buy-exact-v1-c4-evidence-feasibility-2026-10-11.json"
)
TAXONOMY_RECEIPT_SHA256 = "9213239dacf2ff7f7e412efd19f9ef20534ddd483ff57ec5debb9316b49fbeb6"
TAXONOMY_ZSTD = ROOT / "work/pumpswap-buy-exact-v1-c4-evidence-feasibility-taxonomy.json.zst"
TAXONOMY_ZSTD_SHA256 = "eb235db6df81c483769294ef3c999d836ae4d50887e721eaa9ddd930a0ce4c0d"
TAXONOMY_JSON_SHA256 = "81dc5e8433a9b6a695afcedbc4df81fde0bc5406da553831606edb8fa550cd5d"
EVENT_FEASIBILITY_RECEIPT = ROOT / (
    "docs/evidence/pumpswap-c4-event-layout-feasibility-validation-v1-2026-10-11.json"
)
EVENT_FEASIBILITY_SHA256 = "443b38dbd1211ce321283a3cc858483ed5c9d54b4bc19c6f9c0fd6408bd40b9d"
EVENT_LAYOUT_REPORT = (
    ROOT / "work/brain-senses-pumpswap-c4-event-layout-feasibility-v1/report-final.json"
)
EVENT_LAYOUT_REPORT_SHA256 = "bcb6b8f07c9a7e2695c7f1414162e3a74e2da097a85e396689c5bcf49034e16b"
IDL_PATH = ROOT / (
    "work/brain-senses-pumpswap-idl-history-2026-10-11/"
    "8cda1fa30ea658b20909d8aedf002047119388d2.pump_amm.json"
)
IDL_SHA256 = "b7d8c57a4d9c4dd0109a9ab893052352333252d4eedced10ac091d4d66cab89b"
CAMPAIGN_ROOT = ROOT / "work/brain-senses-live-helius-inventory-v3-20261010-4"
EXPECTED_BUCKETS = [f"20261010T{hour:02d}0000Z" for hour in range(13, 23)]
EXPECTED_CANDIDATE_COUNT = 2_333
EXPECTED_CATEGORIES = {
    "successful_structural_pass": 1_550,
    "successful_structural_mismatch": 3,
    "failed_structural_pass": 767,
    "failed_structural_mismatch": 13,
}
NATIVE_MINT = "So11111111111111111111111111111111111111112"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
FEE_EVENT_FIELDS = (
    "lp_fee_basis_points",
    "lp_fee",
    "protocol_fee_basis_points",
    "protocol_fee",
    "coin_creator_fee_basis_points",
    "coin_creator_fee",
    "cashback_fee_basis_points",
    "cashback",
    "buyback_fee_basis_points",
    "buyback_fee",
    "holder_rewards_bps",
    "holder_rewards",
    "creator_fee_unclaimed",
)


class IdentifiabilityError(ValueError):
    """Raised when a pinned input or expected denominator does not verify."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_object(path: Path, expected_sha256: str | None = None) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    if expected_sha256 is not None and _sha(raw) != expected_sha256:
        raise IdentifiabilityError(f"sha256_mismatch:{path.name}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise IdentifiabilityError(f"json_object_required:{path.name}")
    return value, raw


def _taxonomy_rows() -> tuple[dict[str, Any], bytes]:
    if _sha(TAXONOMY_ZSTD.read_bytes()) != TAXONOMY_ZSTD_SHA256:
        raise IdentifiabilityError("taxonomy_compressed_hash_mismatch")
    zstd = shutil.which("zstd")
    if zstd is None:
        raise IdentifiabilityError("zstd_unavailable")
    completed = subprocess.run(
        [zstd, "-q", "-d", "-c", str(TAXONOMY_ZSTD)],
        check=True,
        capture_output=True,
    )
    raw = completed.stdout
    if _sha(raw) != TAXONOMY_JSON_SHA256:
        raise IdentifiabilityError("taxonomy_uncompressed_hash_mismatch")
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list):
        raise IdentifiabilityError("taxonomy_rows_missing")
    return value, raw


def _verified_taxonomy_sources(receipt: dict[str, Any]) -> dict[str, str]:
    """Revalidate the taxonomy's source hashes before reusing its row identities."""
    pins = receipt.get("evidence_artifact", {}).get("toolchain_sha256")
    if not isinstance(pins, dict):
        raise IdentifiabilityError("taxonomy_toolchain_pins_missing")
    paths = {
        "this_tool": ROOT / "tools/brain_senses_buy_exact_v1_c4_evidence_feasibility.py",
        "success_trace_helpers": ROOT / "tools/brain_senses_buy_exact_v1_c4_success_trace.py",
        "census_capture_helpers": ROOT / "tools/brain_senses_buy_exact_v1_census.py",
        "pool_v2_raw_qualifier_helpers": ROOT
        / "tools/brain_senses_buy_exact_v1_pool_v2_prefix_qualifier_v2.py",
        "compiled_economics_tree": ROOT / "src/memecoin_pumpswap/compiled_economics.py",
        "normalize": ROOT / "src/memecoin_pumpswap/normalize.py",
        "decoder": ROOT / "src/memecoin_pumpswap/decoder.py",
        "buy_exact_contract": ROOT / "src/memecoin_pumpswap/buy_exact_v1.py",
    }
    actual = {name: _sha(path.read_bytes()) for name, path in paths.items()}
    if actual != pins:
        raise IdentifiabilityError("taxonomy_source_hash_mismatch")
    return actual


def _partition(row: dict[str, Any]) -> str:
    status = row.get("transaction_state")
    structure = row.get("structural_taxonomy")
    if status == "successful_transaction":
        if structure == "structural_pass_by_frozen_partition_and_exact_success_identity_complement":
            return "successful_structural_pass"
        if structure == "known_successful_structural_mismatch_from_replay_receipt":
            return "successful_structural_mismatch"
    if status == "failed_attempt_only":
        if structure == "failed_structural_pass_pool_v2_readonly":
            return "failed_structural_pass"
        if structure == "failed_structural_mismatch_pool_v2_writable":
            return "failed_structural_mismatch"
    raise IdentifiabilityError("row_partition_unrecognized")


def classify_population(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Recompute overlapping structural/status categories without inventing admission."""
    if len(rows) != EXPECTED_CANDIDATE_COUNT:
        raise IdentifiabilityError("candidate_denominator_mismatch")
    identities = []
    counts: Counter[str] = Counter()
    legacy_censored: Counter[str] = Counter()
    token2022_pairs = 0
    for row in rows:
        identity = row.get("identity")
        if not isinstance(identity, dict):
            raise IdentifiabilityError("row_identity_missing")
        key = (
            identity.get("slot"),
            identity.get("transaction_index"),
            tuple(identity.get("path", [])),
            identity.get("signature"),
        )
        identities.append(key)
        counts[_partition(row)] += 1
        if row.get("economic_admission") is not False:
            raise IdentifiabilityError("economic_admission_claim_present")
        roles = row.get("candidate_roles")
        if not isinstance(roles, dict):
            raise IdentifiabilityError("candidate_roles_missing")
        if TOKEN2022 in (roles.get("base_token_program"), roles.get("quote_token_program")):
            token2022_pairs += 1
        if _partition(row) == "successful_structural_pass":
            state = row.get("legacy_candidate_subtree_vs_boundary_feasibility", {}).get("state")
            if state in {"censored_missing_endpoint", "censored_transaction_boundary_residual"}:
                legacy_censored[state] += 1
    if len(set(identities)) != len(identities):
        raise IdentifiabilityError("candidate_identity_duplicate")
    if dict(counts) != EXPECTED_CATEGORIES:
        raise IdentifiabilityError("candidate_partition_mismatch")
    if legacy_censored != Counter(
        {"censored_missing_endpoint": 2, "censored_transaction_boundary_residual": 2}
    ):
        raise IdentifiabilityError("legacy_censor_partition_mismatch")
    return {
        "candidate_references": len(rows),
        "row_identities_unique": True,
        "category_counts": dict(sorted(counts.items())),
        "successful_references": counts["successful_structural_pass"]
        + counts["successful_structural_mismatch"],
        "failed_attempts": counts["failed_structural_pass"] + counts["failed_structural_mismatch"],
        "structural_mismatches": counts["successful_structural_mismatch"]
        + counts["failed_structural_mismatch"],
        "legacy_success_pass_censorings": dict(sorted(legacy_censored.items())),
        "candidate_token2022_role_pair_count": token2022_pairs,
        "economic_admission": False,
    }


def _event_facts(row: dict[str, Any]) -> dict[str, Any]:
    events = row.get("ordered_candidate_descendant_events", [])
    if not isinstance(events, list):
        raise IdentifiabilityError("event_fact_shape_invalid")
    exact_buys = [
        event
        for event in events
        if event.get("event_name") == "BuyEvent" and event.get("event_exact") is True
    ]
    fee_values = []
    for event in exact_buys:
        fields = event.get("parsed_fields")
        if isinstance(fields, dict):
            fee_values.append({name: fields[name] for name in FEE_EVENT_FIELDS if name in fields})
    comparisons = [event.get("candidate_argument_wire_comparisons", {}) for event in exact_buys]
    return {
        "candidate_descendant_event_count": len(events),
        "exact_buy_event_count": len(exact_buys),
        "all_exact_event_roles_match": bool(exact_buys)
        and all(event.get("event_roles_match_candidate_structure") is True for event in exact_buys),
        "all_instruction_argument_comparisons_match": bool(comparisons)
        and all(all(value is True for value in item.values()) for item in comparisons),
        "event_fee_field_raw_values": fee_values,
        "event_fee_value_unit": (
            "IDL u64 wire values; IDL does not declare decimal scale or realized settlement"
        ),
        "event_claim_scope": (
            "ordered candidate-descendant wire data; not economic binding or realized effects"
        ),
    }


def _boundary_facts(row: dict[str, Any]) -> dict[str, Any]:
    endpoints = row.get("provider_transaction_boundary_endpoints", {})
    endpoint_rows = endpoints.get("provider_pre_post_token_balance_rows", [])
    comparisons = row.get("legacy_candidate_subtree_vs_boundary_feasibility", {}).get(
        "account_delta_comparisons", []
    )
    states = Counter(item.get("state", "unknown") for item in endpoint_rows)
    comparison_states = Counter(item.get("state", "unknown") for item in comparisons)
    return {
        "provider_endpoint_role_rows": len(endpoint_rows),
        "provider_endpoint_row_states": dict(sorted(states.items())),
        "subtree_vs_transaction_boundary_comparisons": len(comparisons),
        "comparison_states": dict(sorted(comparison_states.items())),
        "outside_candidate_subtree_target_touch_count": len(
            row.get("whole_transaction_target_touches_outside_candidate_subtree", [])
        ),
        "endpoint_scope": (
            "whole-transaction token balance boundaries; not position-at-candidate state"
        ),
        "missing_endpoint_interpretation": "unknown, never zero",
    }


def _wire_facts(row: dict[str, Any]) -> dict[str, Any]:
    transfers = row.get("ordered_token_transfer_wire_facts", [])
    by_key: Counter[tuple[str, str, str]] = Counter()
    role_amounts: list[dict[str, Any]] = []
    for item in transfers:
        relation = "descendant" if item.get("candidate_descendant") is True else "outside"
        by_key[
            (item.get("program_id", "unknown"), item.get("wire_variant", "unknown"), relation)
        ] += 1
        links = item.get("target_role_links", [])
        role_amounts.append(
            {
                "order": item.get("order"),
                "path": item.get("path"),
                "program_id": item.get("program_id"),
                "wire_variant": item.get("wire_variant"),
                "relation_to_candidate": relation,
                "amount_atomic": item.get("amount_operand_atomic"),
                "amount_unit": item.get("amount_unit"),
                "wire_mint_candidate_role_match": item.get("wire_mint_candidate_role_match"),
                "target_role_links_by_account_position": [
                    {
                        "position": link.get("position"),
                        "candidate_roles": link.get("candidate_roles", []),
                    }
                    for link in links
                ],
            }
        )
    return {
        "transfer_instruction_counts": [
            {"program_id": program, "wire_variant": variant, "relation": relation, "count": count}
            for (program, variant, relation), count in sorted(by_key.items())
        ],
        "transfer_wire_observations": role_amounts,
        "transfer_units": (
            "captured instruction atomic operands; decimal scaling and net settlement not inferred"
        ),
    }


def _raw_transaction_facts(
    transaction: dict[str, Any],
    identity: dict[str, Any],
    roles: dict[str, str],
    expected_status: str,
) -> dict[str, Any]:
    normalized = normalize_compiled_transaction(transaction)
    signatures = normalized.get("transaction_signatures", [])
    if not signatures or signatures[0] != identity.get("signature"):
        raise IdentifiabilityError("raw_signature_reconciliation_failed")
    if normalized.get("meta_error") is None and expected_status != "successful_transaction":
        raise IdentifiabilityError("raw_status_reconciliation_failed")
    if normalized.get("meta_error") is not None and expected_status != "failed_attempt_only":
        raise IdentifiabilityError("raw_status_reconciliation_failed")
    roots = _transaction_tree(normalized)
    nodes = [node for root in roots for node in _flatten(root)]
    candidate_nodes = [
        node for node in nodes if node["instruction"].get("path") == identity.get("path")
    ]
    if len(candidate_nodes) != 1:
        raise IdentifiabilityError("raw_candidate_path_not_unique")
    candidate_instruction = candidate_nodes[0]["instruction"]
    if candidate_instruction.get("program_id") != SCHEMA["address"]:
        raise IdentifiabilityError("raw_candidate_program_mismatch")
    actual_roles = candidate_instruction.get("accounts", [])
    schema_roles = SCHEMA["instructions"]["buy_exact_quote_in"]["accounts"]
    if len(actual_roles) < len(schema_roles) or dict(
        zip(schema_roles, actual_roles, strict=False)
    ) != {name: roles[name] for name in schema_roles}:
        raise IdentifiabilityError("raw_candidate_role_reconciliation_failed")
    subtree = {id(node) for node in _flatten(candidate_nodes[0])}
    role_keys: dict[str, list[str]] = {}
    for name, key in roles.items():
        role_keys.setdefault(key, []).append(name)
    touched_instructions = []
    for order, node in enumerate(nodes):
        instruction = node["instruction"]
        linked = sorted(
            {name for key in instruction.get("accounts", []) for name in role_keys.get(key, [])}
        )
        if not linked:
            continue
        raw = bytes.fromhex(instruction.get("data_hex", ""))
        if instruction.get("program_id") not in {
            SYSTEM_PROGRAM,
            ASSOCIATED_TOKEN_PROGRAM,
            TOKEN,
            TOKEN2022,
        }:
            kind = "other_program"
        elif instruction.get("program_id") == SYSTEM_PROGRAM:
            kind = "system_program_wire"
        elif instruction.get("program_id") == ASSOCIATED_TOKEN_PROGRAM:
            kind = "associated_token_program_wire"
        else:
            kind = "token_program_wire"
        touched_instructions.append(
            {
                "order": order,
                "path": instruction.get("path"),
                "program_id": instruction.get("program_id"),
                "program_class": kind,
                "target_candidate_roles": linked,
                "candidate_descendant": id(node) in subtree,
                "wire_sha256": hashlib.sha256(raw).hexdigest(),
                "wire_bytes": len(raw),
                "first_byte_tag_hex": raw[:1].hex() if raw else None,
                "first_u32_le": int.from_bytes(raw[:4], "little") if len(raw) >= 4 else None,
            }
        )
    message_meta = transaction.get("meta")
    if not isinstance(message_meta, dict):
        raise IdentifiabilityError("raw_transaction_meta_missing")
    account_rows = normalized.get("accounts")
    pre = message_meta.get("preBalances")
    post = message_meta.get("postBalances")
    lamport_rows = []
    boundary_state = "unavailable"
    if (
        isinstance(account_rows, list)
        and isinstance(pre, list)
        and isinstance(post, list)
        and len(account_rows) == len(pre) == len(post)
        and all(type(value) is int and value >= 0 for value in pre + post)
    ):
        boundary_state = "complete_transaction_boundary"
        by_key = {row["pubkey"]: (pre[row["index"]], post[row["index"]]) for row in account_rows}
        for key, names in sorted(role_keys.items()):
            before_after = by_key.get(key)
            lamport_rows.append(
                {
                    "account_roles": sorted(names),
                    "pre_lamports": before_after[0] if before_after is not None else None,
                    "post_lamports": before_after[1] if before_after is not None else None,
                    "transaction_boundary_delta_lamports": (
                        before_after[1] - before_after[0] if before_after is not None else None
                    ),
                    "state": "observed_transaction_boundary"
                    if before_after is not None
                    else "account_not_in_balance_array",
                }
            )
    first_account = (
        account_rows[0]["pubkey"] if isinstance(account_rows, list) and account_rows else None
    )
    return {
        "transaction_status_matches_taxonomy": True,
        "transaction_meta_fee_lamports": message_meta.get("fee")
        if type(message_meta.get("fee")) is int and message_meta.get("fee") >= 0
        else None,
        "first_message_account_roles": sorted(role_keys.get(first_account, []))
        if first_account
        else [],
        "lamport_balance_boundary_state": boundary_state,
        "lamport_role_boundary_rows": lamport_rows,
        "ordered_target_account_instruction_inventory": touched_instructions,
        "wsol_role_names": sorted(name for name, key in roles.items() if key == NATIVE_MINT),
        "pre_post_mint_or_token_account_state_bytes": False,
        "token2022_extension_state_bytes": False,
        "boundary_scope": "whole transaction before/after only; no per-instruction state snapshot",
    }


def _official_schema(idl: dict[str, Any], event_layout_report: dict[str, Any]) -> dict[str, Any]:
    instructions = [
        row for row in idl.get("instructions", []) if row.get("name") == "buy_exact_quote_in"
    ]
    events = [row for row in idl.get("events", []) if row.get("name") == "BuyEvent"]
    types = [row for row in idl.get("types", []) if row.get("name") == "BuyEvent"]
    if (
        idl.get("address") != SCHEMA["address"]
        or len(instructions) != 1
        or len(events) != 1
        or len(types) != 1
    ):
        raise IdentifiabilityError("official_schema_objects_not_unique")
    instruction, event = instructions[0], events[0]
    args = instruction.get("args", [])
    args_summary = [{"name": row.get("name"), "type": row.get("type")} for row in args]
    accounts = instruction.get("accounts", [])
    event_fields = types[0].get("type", {}).get("fields", [])
    event_receipt_layout = next(
        row
        for row in event_layout_report.get("historical_layouts_enumerated", [])
        if row.get("event_name") == "BuyEvent"
        and row.get("source_commit") == "8cda1fa30ea658b20909d8aedf002047119388d2"
    )
    if (
        len(accounts) != 23
        or [{"name": row.get("name"), "type": row.get("type")} for row in event_fields]
        != event_receipt_layout.get("fields")
        or len(event_fields) != 40
    ):
        raise IdentifiabilityError("official_idl_replay_schema_binding_failed")
    option_bool = next(
        (row for row in idl.get("types", []) if row.get("name") == "OptionBool"), None
    )
    return {
        "source_commit": "8cda1fa30ea658b20909d8aedf002047119388d2",
        "source_sha256": IDL_SHA256,
        "program_id": idl["address"],
        "buy_exact_quote_in": {
            "discriminator": instruction.get("discriminator"),
            "args": args_summary,
            "account_count": len(accounts),
            "accounts": [
                {
                    "name": row.get("name"),
                    "writable": row.get("writable", False),
                    "signer": row.get("signer", False),
                }
                for row in accounts
            ],
            "docs": instruction.get("docs", []),
            "option_bool_type": option_bool,
            "wire_observation": (
                "captured candidate class is 25 bytes: discriminator + two u64 values + "
                "one-byte OptionBool payload; this does not prove deployed implementation"
            ),
        },
        "BuyEvent": {
            "discriminator": event.get("discriminator"),
            "field_count": len(event_fields),
            "field_schema": event_fields,
            "amount_fields_are_u64_without_decimal_unit_metadata": [
                row["name"]
                for row in event_fields
                if row.get("name")
                in {
                    "base_amount_out",
                    "max_quote_amount_in",
                    "quote_amount_in",
                    "user_quote_amount_in",
                    "lp_fee",
                    "protocol_fee",
                    "coin_creator_fee",
                    "cashback",
                    "buyback_fee",
                    "holder_rewards",
                    "creator_fee_unclaimed",
                }
            ],
            "semantic_limit": (
                "schema fields and encoded values are not by themselves realized effects or "
                "a unit/fee-policy contract"
            ),
        },
        "fee_rent_docs": [
            line
            for line in instruction.get("docs", [])
            if any(token in line.lower() for token in ("fee", "rent", "cashback", "account"))
        ],
        "three_extra_accounts": {
            "status": (
                "candidate stratum is defined by the pinned SDK/census prefix, "
                "not by the 23-account IDL list"
            ),
            "roles": ["pool_v2_prefix", "fee_wallet_suffix", "fee_quote_ata_suffix"],
        },
    }


def build() -> tuple[dict[str, Any], bytes]:
    replay, replay_bytes = _json_object(REPLAY_RECEIPT, REPLAY_SHA256)
    taxonomy_receipt, taxonomy_receipt_bytes = _json_object(
        TAXONOMY_RECEIPT, TAXONOMY_RECEIPT_SHA256
    )
    event_receipt, _ = _json_object(EVENT_FEASIBILITY_RECEIPT, EVENT_FEASIBILITY_SHA256)
    if (
        replay.get("scope", {}).get("included_buckets") != EXPECTED_BUCKETS
        or replay.get("scope", {}).get("excluded_tail2_body_opened") is not False
        or replay.get("scope", {}).get("provider_requests") != 0
        or taxonomy_receipt.get("scope", {}).get("frames") != len(EXPECTED_BUCKETS)
        or taxonomy_receipt.get("scope", {}).get("tail2_body_opened") is not False
        or taxonomy_receipt.get("scope", {}).get("provider_requests") != 0
        or taxonomy_receipt.get("evidence_artifact", {}).get("compressed_sha256")
        != TAXONOMY_ZSTD_SHA256
        or taxonomy_receipt.get("evidence_artifact", {}).get("uncompressed_sha256")
        != TAXONOMY_JSON_SHA256
        or event_receipt.get("status") != "complete_offline_selected_event_layout_feasibility"
        or event_receipt.get("activation_authorized") is not False
    ):
        raise IdentifiabilityError("input_scope_or_receipt_pins_invalid")
    taxonomy_sources = _verified_taxonomy_sources(taxonomy_receipt)
    taxonomy, taxonomy_raw = _taxonomy_rows()
    if (
        taxonomy.get("denominators", {}).get("provider_requests") != 0
        or taxonomy.get("denominators", {}).get("tail2_opened") is not False
    ):
        raise IdentifiabilityError("taxonomy_scope_invalid")
    partition = classify_population(taxonomy["rows"])
    if _sha(IDL_PATH.read_bytes()) != IDL_SHA256:
        raise IdentifiabilityError("official_idl_hash_mismatch")
    idl = json.loads(IDL_PATH.read_bytes())
    if _sha(EVENT_LAYOUT_REPORT.read_bytes()) != EVENT_LAYOUT_REPORT_SHA256:
        raise IdentifiabilityError("event_layout_report_hash_mismatch")
    event_report = json.loads(EVENT_LAYOUT_REPORT.read_bytes())
    schema_facts = _official_schema(idl, event_report)

    frozen_manifest, frozen_manifest_sha = c4_replay._verify_frozen_manifest(
        c4_replay.MANIFEST_DEFAULT
    )
    if list(c4_replay.INCLUDED) != EXPECTED_BUCKETS:
        raise IdentifiabilityError("frozen_included_frame_set_mismatch")
    frame_by_slot: dict[int, dict[str, Any]] = {}
    capture_by_slot: dict[int, tuple[Path, dict[str, Any], str]] = {}
    capture_rows: list[dict[str, Any]] = []
    for bucket in EXPECTED_BUCKETS:
        frame_meta = next(
            (row for row in frozen_manifest["frame_metadata"] if row.get("frame_id") == bucket),
            None,
        )
        if frame_meta is None:
            raise IdentifiabilityError("included_frame_metadata_missing")
        frame_root, _plan, summary, _journal, frame_plan_sha = c4_replay._verify_frame(
            frozen_manifest, frame_meta, c4_replay.ROOT / c4_replay.EXPECTED_CAMPAIGN_ROOT
        )
        if summary.get("status") != "frame_complete":
            raise IdentifiabilityError("included_frame_not_complete")
        captures = _capture_rows(frame_root, v3_layout=True)
        if len(captures) != 16:
            raise IdentifiabilityError("included_frame_capture_count_mismatch")
        for path, receipt, request in captures:
            params = request.get("params", [])
            slot = params[0] if params else None
            if type(slot) is not int or slot in capture_by_slot:
                raise IdentifiabilityError("slot_identity_duplicate_or_invalid")
            capture_by_slot[slot] = (path, receipt, bucket)
            frame_by_slot[slot] = frame_meta
            capture_rows.append(
                {
                    "frame_id": bucket,
                    "slot": slot,
                    "response_sha256": receipt.get("response_sha256"),
                    "response_bytes": receipt.get("response_bytes"),
                    "stored_sha256": receipt.get("storage", {}).get("sha256"),
                    "stored_bytes": receipt.get("storage", {}).get("bytes"),
                    "receipt_ns": receipt.get("received_ns"),
                    "frame_plan_sha256": frame_plan_sha,
                }
            )
    if len(capture_by_slot) != 160:
        raise IdentifiabilityError("full_block_denominator_mismatch")

    blocks: dict[int, dict[str, Any]] = {}
    for slot, (path, receipt, _bucket) in capture_by_slot.items():
        blocks[slot] = _read_block("zstd", path, receipt)
    row_ledger: list[dict[str, Any]] = []
    fee_presence: Counter[str] = Counter()
    raw_counts: Counter[str] = Counter()
    for row in taxonomy["rows"]:
        identity = row["identity"]
        slot, tx_index = identity["slot"], identity["transaction_index"]
        if slot not in blocks:
            raise IdentifiabilityError("candidate_slot_not_in_included_frames")
        transactions = blocks[slot].get("transactions")
        if (
            not isinstance(transactions, list)
            or type(tx_index) is not int
            or not 0 <= tx_index < len(transactions)
        ):
            raise IdentifiabilityError("candidate_transaction_index_invalid")
        transaction = transactions[tx_index]
        raw_facts = _raw_transaction_facts(
            transaction,
            identity,
            row["candidate_roles"],
            row["transaction_state"],
        )
        if raw_facts["transaction_meta_fee_lamports"] is None:
            fee_presence["missing_meta_fee"] += 1
        else:
            fee_presence["present_meta_fee"] += 1
        raw_counts[raw_facts["lamport_balance_boundary_state"]] += 1
        event_facts = _event_facts(row)
        for values in event_facts["event_fee_field_raw_values"]:
            for name in values:
                fee_presence[f"event_fee_field:{name}"] += 1
        role_programs = {
            "base_token_program": row["candidate_roles"].get("base_token_program"),
            "quote_token_program": row["candidate_roles"].get("quote_token_program"),
        }
        category = _partition(row)
        row_ledger.append(
            {
                "identity": identity,
                "frame_id": capture_by_slot[slot][2],
                "population_category": category,
                "transaction_status": row["transaction_state"],
                "economic_admission": False,
                "candidate_arguments": row.get("candidate_arguments"),
                "candidate_roles_token_programs": role_programs,
                "candidate_token_program_pair": (
                    "dual_legacy"
                    if role_programs["base_token_program"] == TOKEN
                    and role_programs["quote_token_program"] == TOKEN
                    else "contains_token2022"
                    if TOKEN2022 in role_programs.values()
                    else "other_or_unclassified"
                ),
                "event_facts": event_facts,
                "transfer_facts": _wire_facts(row),
                "transaction_boundary_facts": _boundary_facts(row),
                "legacy_feasibility_censor_or_state": row.get(
                    "legacy_candidate_subtree_vs_boundary_feasibility", {}
                ).get("state"),
                "legacy_account_delta_comparisons": row.get(
                    "legacy_candidate_subtree_vs_boundary_feasibility", {}
                ).get("account_delta_comparisons", []),
                "provider_endpoint_rows": row.get(
                    "provider_transaction_boundary_endpoints", {}
                ).get("provider_pre_post_token_balance_rows", []),
                "whole_transaction_outside_target_touches": row.get(
                    "whole_transaction_target_touches_outside_candidate_subtree", []
                ),
                "raw_transaction_evidence": raw_facts,
                "state_and_position_limits": [
                    "token balance rows are whole-transaction endpoints, not state at candidate "
                    "instruction position",
                    "no historical mint/account bytes or Token-2022 extension/withheld-fee/hook "
                    "state are in getBlock",
                    "transaction fee and lamport deltas are whole-transaction metadata, not "
                    "assigned to this candidate",
                    "ordered program/tag facts do not by themselves prove rent, wrapped-SOL, fee, "
                    "or realized trade semantics",
                ],
            }
        )

    category_counts = Counter(row["population_category"] for row in row_ledger)
    if dict(category_counts) != EXPECTED_CATEGORIES or len(row_ledger) != EXPECTED_CANDIDATE_COUNT:
        raise IdentifiabilityError("rowwise_ledger_partition_mismatch")
    censored = Counter(
        row["legacy_feasibility_censor_or_state"]
        for row in row_ledger
        if row["population_category"] == "successful_structural_pass"
        and row["legacy_feasibility_censor_or_state"]
        in {"censored_missing_endpoint", "censored_transaction_boundary_residual"}
    )
    if censored != Counter(
        {"censored_missing_endpoint": 2, "censored_transaction_boundary_residual": 2}
    ):
        raise IdentifiabilityError("rowwise_legacy_censoring_mismatch")
    rows_json = json.dumps(row_ledger, sort_keys=True, separators=(",", ":")).encode()
    zstd = shutil.which("zstd")
    if zstd is None:
        raise IdentifiabilityError("zstd_unavailable")
    compressed_rows = subprocess.run(
        [zstd, "-q", "-19", "-c"],
        input=rows_json,
        check=True,
        capture_output=True,
    ).stdout
    result = {
        "contract": CONTRACT,
        "classification": (
            "source-bound offline identifiability inventory; no economic admission predicate"
        ),
        "scope": {
            "frames": EXPECTED_BUCKETS,
            "full_blocks": 160,
            "candidate_rows": len(row_ledger),
            "provider_requests": 0,
            "tail2_body_opened": False,
            "other_1223_buyExact_refs_in_census_excluded_from_this_selected_stratum": True,
        },
        "population": partition,
        "official_schema_and_contract_facts": schema_facts,
        "rowwise_ledger": {
            "path": "work/pumpswap-buy-exact-v1-c4-economic-identifiability-v1/rows.json.zst",
            "compressed_bytes": len(compressed_rows),
            "compressed_sha256": _sha(compressed_rows),
            "uncompressed_bytes": len(rows_json),
            "uncompressed_sha256": _sha(rows_json),
            "encoding": (
                "zstd-compressed canonical JSON array; one row per selected candidate identity"
            ),
        },
        "aggregate_evidence_availability": {
            "transaction_fee_metadata": dict(sorted(fee_presence.items())),
            "lamport_boundary_availability": dict(sorted(raw_counts.items())),
            "wsol_candidate_role_count": sum(
                bool(row["raw_transaction_evidence"]["wsol_role_names"]) for row in row_ledger
            ),
            "rowwise_candidate_transfer_and_endpoint_statuses_are_in_artifact": True,
        },
        "full_block_capture_pins": capture_rows,
        "input_receipts": {
            "pool_v2_replay_receipt_sha256": _sha(replay_bytes),
            "taxonomy_receipt_sha256": _sha(taxonomy_receipt_bytes),
            "taxonomy_compressed_sha256": TAXONOMY_ZSTD_SHA256,
            "taxonomy_uncompressed_sha256": TAXONOMY_JSON_SHA256,
            "event_feasibility_receipt_sha256": EVENT_FEASIBILITY_SHA256,
            "event_layout_report_sha256": EVENT_LAYOUT_REPORT_SHA256,
            "frozen_frame_manifest_sha256": frozen_manifest_sha,
            "official_idl_sha256": IDL_SHA256,
        },
        "source_hashes": {
            "economic_identifiability_tool_sha256": _sha(Path(__file__).read_bytes()),
            "taxonomy_toolchain_sha256": taxonomy_sources,
            "identity_set_method": (
                "inherits the exact 2,333 identities from the hash-pinned taxonomy receipt and "
                "revalidates each against its pinned full-block transaction, signature, path, "
                "program, and account roles; it does not rerun the full census scan"
            ),
        },
        "limitations": [
            (
                "The 1,550 successful structural-pass, three successful mismatch, 767 failed "
                "structural-pass, and 13 failed mismatch rows form the 2,333-row partition. "
                "The 16 mismatches overlap the 780 failed attempts where applicable."
            ),
            (
                "The 780 failed transaction references are attempts, not committed trades; "
                "fees can still be charged."
            ),
            (
                "Four dual-legacy success-pass outcomes remain censored: two missing endpoints "
                "and two candidate-subtree versus transaction-boundary residuals. The separate "
                "success structural-mismatch residual is outside that four-row pass stratum."
            ),
            (
                "Event fee values are decoded IDL field claims; the IDL gives u64 field types but "
                "does not establish decimal scale, deployed code, or realized fee settlement."
            ),
            (
                "Token transfer values are wire operands in atomic units; token decimals and "
                "whole-transaction endpoints do not provide candidate-position settlement."
            ),
            (
                "getBlock supplies transaction-wide fee/lamport/token boundaries and ordered "
                "instruction data. These do not attribute fee, rent, native wrapping, or other "
                "account changes to one candidate when other instructions or account touches exist."
            ),
            (
                "Historical bank-time mint/account data and Token-2022 extension, withheld-fee, "
                "transfer-hook, and position-specific state are unavailable. Token-2022 rows stay "
                "quarantined by the existing source-bound diagnostic."
            ),
            (
                "This dossier defines no admission, audit threshold, or decoder promotion. "
                "A reviewed economic contract and independent evidence are required before any "
                "positive predicate can be considered."
            ),
            (
                "Only ten frames were opened; no tail2 body, backfill, RPC, or provider request "
                "was made."
            ),
        ],
    }
    return result, compressed_rows


def _write_create_only(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "work/pumpswap-buy-exact-v1-c4-economic-identifiability-v1",
    )
    args = parser.parse_args()
    result, compressed_rows = build()
    outdir = args.output_dir.resolve()
    if not outdir.is_relative_to((ROOT / "work").resolve()):
        raise IdentifiabilityError("outputs_must_stay_under_ignored_work")
    outdir.mkdir(parents=True, exist_ok=True)
    rows_path = outdir / "rows.json.zst"
    summary_path = outdir / "summary.json"
    if rows_path.exists() or summary_path.exists():
        raise IdentifiabilityError("output_exists_refusing_overwrite")
    result["rowwise_ledger"]["path"] = str(rows_path.relative_to(ROOT))
    summary = json.dumps(result, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    _write_create_only(rows_path, compressed_rows)
    try:
        _write_create_only(summary_path, summary)
    except BaseException:
        rows_path.unlink(missing_ok=True)
        raise
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
