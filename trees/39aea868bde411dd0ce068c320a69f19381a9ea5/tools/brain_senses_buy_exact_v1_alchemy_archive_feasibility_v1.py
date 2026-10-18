#!/usr/bin/env python3
"""Offline Alchemy Account Archive feasibility census for the accepted buyExact c4 sample.

Reads only accepted row/taxonomy ledgers and byte-pinned public documentation. It never reads
captured c4 transaction bodies and never contacts Alchemy or another provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "pumpswap_buy_exact_v1_alchemy_archive_feasibility_v1"
IDENT_RECEIPT = ROOT / (
    "docs/evidence/pumpswap-buy-exact-v1-c4-economic-identifiability-validation-v1-2026-10-11.json"
)
IDENT_RECEIPT_SHA256 = "29e9d00c326f10973dde8f953b16bac136e7774d3fe94e67190a43c6832228c2"
IDENT_ROWS = ROOT / (
    "work/pumpswap-buy-exact-v1-c4-economic-identifiability-v1-final/rows.json.zst"
)
IDENT_ROWS_ZSTD_SHA256 = "21e384721f35d67ba88e3b673b03dda06776168180f4157ba76b255072230812"
IDENT_ROWS_SHA256 = "25eca394b85ad11df4325b23d3f6a8c03d5eea5a04e81418fabf754602d47d4f"
TAXONOMY_RECEIPT = (
    ROOT / "docs/evidence/pumpswap-buy-exact-v1-c4-evidence-feasibility-2026-10-11.json"
)
TAXONOMY_RECEIPT_SHA256 = "9213239dacf2ff7f7e412efd19f9ef20534ddd483ff57ec5debb9316b49fbeb6"
TAXONOMY = ROOT / "work/pumpswap-buy-exact-v1-c4-evidence-feasibility-taxonomy.json.zst"
TAXONOMY_ZSTD_SHA256 = "eb235db6df81c483769294ef3c999d836ae4d50887e721eaa9ddd930a0ce4c0d"
TAXONOMY_SHA256 = "81dc5e8433a9b6a695afcedbc4df81fde0bc5406da553831606edb8fa550cd5d"
USAGE_OBSERVATION = ROOT / (
    "work/alchemy-account-archive-source-review-2026-10-11/read-only-usage-mcp-observation.json"
)
USAGE_OBSERVATION_SHA256 = "9b211ef63ae924fdb35cdd5e0b2dc501d906bb3d9a88f6b107444e104527effd"
ROW_COUNT = 2_333
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
NATIVE_MINT = "So11111111111111111111111111111111111111112"
NON_STATE_ROLES = {
    "associated_token_program",
    "base_token_program",
    "fee_program",
    "program",
    "quote_token_program",
    "system_program",
}
SOURCE_ROOT = ROOT / "work/alchemy-account-archive-source-review-2026-10-11"
SOURCES = (
    (
        "https://www.alchemy.com/docs/solana/account-archive",
        "account-archive.html",
        "28630d26157df2b9a07fc66d5b92e598709f2690f9ad727f1d19f3eca22c0566",
    ),
    (
        "https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-account-info",
        "get-account-info.html",
        "38e0bd98a0fef54b310d0c948699c6d6f3004e7665dbf364634553aa415f302a",
    ),
    (
        "https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/simulate-transactions-at-slot",
        "simulate-transactions-at-slot.html",
        "e7481f01f5214b78316eab035c09f4dd782214a622a50ab5c9f7fa947a270aed",
    ),
    (
        "https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-token-accounts-by-owner-at-slot",
        "get-token-accounts-by-owner-at-slot.html",
        "149f4b385d93a10d10a8b6a9965ae275128ad0762f0905f1ecde141847e6d766",
    ),
    (
        "https://www.alchemy.com/docs/reference/compute-unit-costs",
        "compute-unit-costs.html",
        "85ae44ea427dd276f489d4455241ca9215168ecd1d54a7c1b42c25b00a43d88d",
    ),
    (
        "https://www.alchemy.com/docs/reference/compute-units",
        "compute-units.html",
        "d867a5c03e4ed330ae042001735eef0d094b96950ac14ed4ae21e52f8332fc7e",
    ),
    (
        "https://www.alchemy.com/docs/reference/pricing-plans",
        "pricing-plans.html",
        "c24e6c5172e77bea7b9c6612125a37081c442cb78c88571ac73330d353f8c848",
    ),
)
DEFAULT_RECEIPT = ROOT / (
    "docs/evidence/pumpswap-buy-exact-v1-alchemy-account-archive-feasibility-v1-2026-10-11.json"
)
DEFAULT_ROWS = ROOT / (
    "work/pumpswap-buy-exact-v1-alchemy-account-archive-feasibility-v1-2026-10-11/rows.json.zst"
)
TEST_PATH = ROOT / "tests/brain/test_senses_buy_exact_v1_alchemy_archive_feasibility.py"


class FeasibilityError(ValueError):
    """A pinned source, row identity, or scoped accounting invariant failed."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def _read_zstd(path: Path, expected_sha: str, expected_uncompressed_sha: str) -> tuple[Any, bytes]:
    compressed = path.read_bytes()
    if _sha(compressed) != expected_sha:
        raise FeasibilityError(f"compressed_artifact_hash_mismatch:{path.name}")
    result = subprocess.run(["zstd", "-q", "-d", "-c", str(path)], check=True, capture_output=True)
    if _sha(result.stdout) != expected_uncompressed_sha:
        raise FeasibilityError(f"uncompressed_artifact_hash_mismatch:{path.name}")
    return json.loads(result.stdout), result.stdout


def _load_inputs() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if _file_sha(IDENT_RECEIPT) != IDENT_RECEIPT_SHA256:
        raise FeasibilityError("identifiability_receipt_hash_mismatch")
    if _file_sha(TAXONOMY_RECEIPT) != TAXONOMY_RECEIPT_SHA256:
        raise FeasibilityError("taxonomy_receipt_hash_mismatch")
    if _file_sha(USAGE_OBSERVATION) != USAGE_OBSERVATION_SHA256:
        raise FeasibilityError("usage_observation_hash_mismatch")
    usage = json.loads(USAGE_OBSERVATION.read_text())
    if (
        usage["usage_summary"]["totals"]["monthToDate"]["amount"] != "18680"
        or usage["usage_summary"]["totals"]["monthToDate"]["usd"] != "0.01"
        or usage["usage_summary"]["usage_limit_field_present"] is not False
    ):
        raise FeasibilityError("usage_observation_scope_mismatch")
    ident_receipt = json.loads(IDENT_RECEIPT.read_text())
    taxonomy_receipt = json.loads(TAXONOMY_RECEIPT.read_text())
    if (
        ident_receipt["input_receipts"]["taxonomy_compressed_sha256"] != TAXONOMY_ZSTD_SHA256
        or ident_receipt["input_receipts"]["taxonomy_uncompressed_sha256"] != TAXONOMY_SHA256
    ):
        raise FeasibilityError("taxonomy_identity_binding_mismatch")
    if taxonomy_receipt["evidence_artifact"]["compressed_sha256"] != TAXONOMY_ZSTD_SHA256:
        raise FeasibilityError("taxonomy_receipt_artifact_binding_mismatch")
    ident_rows, _ = _read_zstd(IDENT_ROWS, IDENT_ROWS_ZSTD_SHA256, IDENT_ROWS_SHA256)
    taxonomy, _ = _read_zstd(TAXONOMY, TAXONOMY_ZSTD_SHA256, TAXONOMY_SHA256)
    taxonomy_rows = taxonomy.get("rows")
    if not isinstance(ident_rows, list) or not isinstance(taxonomy_rows, list):
        raise FeasibilityError("input_rows_not_lists")
    if len(ident_rows) != ROW_COUNT or len(taxonomy_rows) != ROW_COUNT:
        raise FeasibilityError("input_row_count_mismatch")
    return ident_rows, taxonomy_rows, ident_receipt


def _identity(row: dict[str, Any]) -> tuple[int, int, tuple[int, ...], str]:
    value = row.get("identity")
    if not isinstance(value, dict):
        raise FeasibilityError("identity_missing")
    slot, tx_index, path, signature = (
        value.get("slot"),
        value.get("transaction_index"),
        value.get("path"),
        value.get("signature"),
    )
    if (
        type(slot) is not int
        or type(tx_index) is not int
        or tx_index < 0
        or not isinstance(path, list)
        or any(type(index) is not int for index in path)
        or not isinstance(signature, str)
        or not signature
    ):
        raise FeasibilityError("identity_shape_invalid")
    return slot, tx_index, tuple(path), signature


def _whole_prefix_single_request_capacity(tx_index: int) -> tuple[int, str]:
    if type(tx_index) is not int or tx_index < 0:
        raise FeasibilityError("transaction_index_invalid")
    count = tx_index + 1
    state = "within_limit_capacity_only" if count <= 5 else "exceeds_limit"
    return count, state


def _row_facts(
    ident_row: dict[str, Any], taxonomy_row: dict[str, Any]
) -> tuple[dict[str, Any], set[tuple[int, str]]]:
    ident = _identity(ident_row)
    if _identity(taxonomy_row) != ident:
        raise FeasibilityError("taxonomy_identity_mismatch")
    if (
        ident_row.get("economic_admission") is not False
        or taxonomy_row.get("economic_admission") is not False
    ):
        raise FeasibilityError("economic_admission_not_false")
    status = ident_row.get("transaction_status")
    category = ident_row.get("population_category")
    allowed = {
        "failed_attempt_only": {"failed_structural_pass", "failed_structural_mismatch"},
        "successful_transaction": {
            "successful_structural_pass",
            "successful_structural_mismatch",
        },
    }
    if category not in allowed.get(status, set()):
        raise FeasibilityError("population_status_category_inconsistent")
    roles = taxonomy_row.get("candidate_roles")
    if not isinstance(roles, dict) or any(not isinstance(value, str) for value in roles.values()):
        raise FeasibilityError("candidate_roles_invalid")
    slot, tx_index, path, signature = ident
    role_pairs = {(slot, address) for address in set(roles.values())}
    state_pairs = {
        (slot, address) for role, address in roles.items() if role not in NON_STATE_ROLES
    }
    if not state_pairs.issubset(role_pairs):
        raise FeasibilityError("state_pair_subset_invalid")
    token2022 = TOKEN_2022 in (
        roles.get("base_token_program"),
        roles.get("quote_token_program"),
    )
    wsol = roles.get("quote_mint") == NATIVE_MINT
    preceding_count = tx_index
    simulated_prefix_count, prefix_capacity_screen = _whole_prefix_single_request_capacity(tx_index)
    fact = {
        "identity": {
            "slot": slot,
            "transaction_index": tx_index,
            "path": list(path),
            "signature": signature,
        },
        "population_category": category,
        "transaction_status": status,
        "same_slot_preceding_transaction_count": preceding_count,
        "candidate_plus_prefix_transaction_count": simulated_prefix_count,
        "whole_prefix_single_request_capacity_screen": prefix_capacity_screen,
        "candidate_role_address_count_lower_bound": len(set(roles.values())),
        "transaction_referenced_account_count": "unknown_from_accepted_ledgers",
        "128_account_capacity_screen": "unknown_from_accepted_ledgers",
        "state_override_count": "not_planned_for_exact_state_query",
        "override_capacity_screen": "unknown_if_hypothetical_override_path_used",
        "rendered_result_bytes": "unknown_without_provider_response",
        "25_mib_result_capacity_screen": "unknown_without_provider_response",
        "candidate_role_state_pairs_at_slot_boundary": len(state_pairs),
        "token_2022_role_present": token2022,
        "wsol_quote_role_present": wsol,
        "slot_boundary_state": "documented_finalized_as_of_slot_only",
        "candidate_transaction_boundary": "pinned_full_block_identity_and_transaction_facts",
        "candidate_instruction_position_state": "not_exposed_by_documented_archive_api",
        "economic_admission": False,
    }
    return fact, role_pairs


def _validate_sources() -> tuple[dict[str, str], dict[str, str]]:
    hashes: dict[str, str] = {}
    mtimes: list[float] = []
    for url, name, expected in SOURCES:
        path = SOURCE_ROOT / name
        actual = _file_sha(path)
        if actual != expected:
            raise FeasibilityError(f"official_source_hash_mismatch:{name}")
        hashes[url] = actual
        mtimes.append(path.stat().st_mtime)
    archive = (SOURCE_ROOT / "account-archive.html").read_text(errors="replace")
    simulation = (SOURCE_ROOT / "simulate-transactions-at-slot.html").read_text(errors="replace")
    get_info = (SOURCE_ROOT / "get-account-info.html").read_text(errors="replace")
    compute = (SOURCE_ROOT / "compute-unit-costs.html").read_text(errors="replace")
    required = (
        (archive, "latest write with"),
        (archive, "strictly before"),
        (archive, "paid plans only"),
        (simulation, "maxItems: 5"),
        (simulation, "128 unique transaction-referenced accounts per entry"),
        (simulation, "25 MiB combined byte budget"),
        (simulation, "single Agave/SVM release"),
        (get_info, 'class="text-primary font-bold">10</span>'),
        (simulation, 'class="text-primary font-bold">1500</span>'),
        (compute, "getAccountInfo"),
    )
    if any(token not in body for body, token in required):
        raise FeasibilityError("pinned_source_semantics_marker_missing")
    return hashes, {
        "first_snapshot_mtime_utc": datetime.fromtimestamp(min(mtimes), UTC).isoformat(),
        "last_snapshot_mtime_utc": datetime.fromtimestamp(max(mtimes), UTC).isoformat(),
    }


def build() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ident_rows, taxonomy_rows, ident_receipt = _load_inputs()
    source_hashes, source_window = _validate_sources()
    facts: list[dict[str, Any]] = []
    all_role_pairs: set[tuple[int, str]] = set()
    for ident_row, taxonomy_row in zip(ident_rows, taxonomy_rows, strict=True):
        fact, pairs = _row_facts(ident_row, taxonomy_row)
        facts.append(fact)
        all_role_pairs.update(pairs)
    if (
        len(
            {
                (
                    row["identity"]["slot"],
                    row["identity"]["transaction_index"],
                    tuple(row["identity"]["path"]),
                    row["identity"]["signature"],
                )
                for row in facts
            }
        )
        != ROW_COUNT
    ):
        raise FeasibilityError("duplicate_candidate_identity")
    states = Counter(row["transaction_status"] + ":" + row["population_category"] for row in facts)
    tx_capacity = Counter(row["whole_prefix_single_request_capacity_screen"] for row in facts)
    tx_index_counts = Counter(
        "0"
        if row["same_slot_preceding_transaction_count"] == 0
        else "1-4"
        if row["same_slot_preceding_transaction_count"] <= 4
        else "5+"
        for row in facts
    )
    slot_count = len({row["identity"]["slot"] for row in facts})
    role_address_counts = [row["candidate_role_address_count_lower_bound"] for row in facts]
    token2022 = sum(row["token_2022_role_present"] for row in facts)
    wsol = sum(row["wsol_quote_role_present"] for row in facts)
    capacity_cross_tab = Counter(
        (
            row["transaction_status"],
            row["population_category"],
            row["whole_prefix_single_request_capacity_screen"],
        )
        for row in facts
    )
    state_account_slot_pairs = {
        (facts[index]["identity"]["slot"], address)
        for index, row in enumerate(taxonomy_rows)
        for role, address in row["candidate_roles"].items()
        if role not in NON_STATE_ROLES
    }
    state_account_pubkeys = {address for _, address in state_account_slot_pairs}
    taxonomy_summary = json.loads(TAXONOMY_RECEIPT.read_text())
    summary = {
        "contract": CONTRACT,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": (
            "offline Alchemy Account Archive capability screen over accepted buyExact c4 ledgers "
            "and pinned public docs; no c4 body opened and no provider chain request"
        ),
        "input_bindings": {
            "identifiability_receipt_sha256": IDENT_RECEIPT_SHA256,
            "identifiability_rows_path": IDENT_ROWS.relative_to(ROOT).as_posix(),
            "identifiability_rows_compressed_sha256": IDENT_ROWS_ZSTD_SHA256,
            "identifiability_rows_uncompressed_sha256": IDENT_ROWS_SHA256,
            "taxonomy_receipt_path": TAXONOMY_RECEIPT.relative_to(ROOT).as_posix(),
            "taxonomy_receipt_sha256": TAXONOMY_RECEIPT_SHA256,
            "taxonomy_rows_path": TAXONOMY.relative_to(ROOT).as_posix(),
            "taxonomy_rows_compressed_sha256": TAXONOMY_ZSTD_SHA256,
            "taxonomy_rows_uncompressed_sha256": TAXONOMY_SHA256,
            "row_count": ROW_COUNT,
            "unique_candidate_identity_count": ROW_COUNT,
        },
        "implementation": {
            "tool_path": Path(__file__).resolve().relative_to(ROOT).as_posix(),
            "tool_sha256": _file_sha(Path(__file__).resolve()),
            "test_path": TEST_PATH.relative_to(ROOT).as_posix(),
            "test_sha256": _file_sha(TEST_PATH),
        },
        "candidate_population": {
            "slots": slot_count,
            "status_and_category_counts": dict(sorted(states.items())),
            "token_2022_program_role_rows": token2022,
            "wsol_quote_role_rows": wsol,
            "same_slot_predecessor_count_buckets": dict(sorted(tx_index_counts.items())),
            "candidate_plus_prefix_simulation_count_buckets": dict(sorted(tx_capacity.items())),
            "simulation_capacity_cross_tab": {
                f"{status}:{category}:{screen}": count
                for (status, category, screen), count in sorted(capacity_cross_tab.items())
            },
            "candidate_role_address_count_lower_bound": {
                "minimum": min(role_address_counts),
                "maximum": max(role_address_counts),
            },
        },
        "archive_account_state_scope": {
            "unique_slot_pubkey_pairs_for_all_candidate_roles": len(all_role_pairs),
            "unique_slot_pubkey_pairs_after_excluding_program_roles": len(state_account_slot_pairs),
            "distinct_candidate_state_pubkeys_after_excluding_program_roles": len(
                state_account_pubkeys
            ),
            "one_getAccountInfo_call_per_distinct_state_account_slot_scenario": len(
                state_account_slot_pairs
            ),
            "nominal_cu_scenario_at_published_10_cu_method_reference": (
                10 * len(state_account_slot_pairs)
            ),
            "nominal_cu_scenario_assumption": (
                "one standard getAccountInfo call per unique candidate state-account×slot pair; "
                "not a measured request count, bill, or account entitlement"
            ),
            "minimum_data_needed": (
                "pre-slot state for touched accounts, mints, fee/hook programs and related state; "
                "candidate roles are a lower-bound scope"
            ),
            "account_query_count": (
                "not equated to unique account-slot pairs; batching and archive API behavior "
                "require separate validation"
            ),
        },
        "api_capabilities_and_limits": {
            "historical_getAccountInfo": {
                "documented_state": (
                    "paid-plan finalized historical account bytes; slot S is inclusive latest "
                    "write at or before S"
                ),
                "history_cursors": (
                    "lastUpdateBeforeSlot S is strictly before S. firstUpdateAfterSlot S is "
                    "ambiguous: the table says strictly after S, prose says at or after S; "
                    "this feasibility screen does not rely on that cursor boundary"
                ),
                "documented_exclusions": "vote accounts and per-slot sysvars",
                "method_cu_reference": 10,
                "account_archive_entitlement_verified": False,
            },
            "simulateTransactionsAtSlot": {
                "documented_behavior": (
                    "hypothetical execution against archived account state with "
                    "ordered shared overlay"
                ),
                "max_transactions_per_request": 5,
                "max_transaction_referenced_accounts_per_entry": 128,
                "max_state_override_entries_per_simulation": 100,
                "max_combined_rendered_result_bytes": 25 * 1024 * 1024,
                "runtime": (
                    "one pinned Agave/SVM release; usable historical window is recent and grows "
                    "with backfill"
                ),
                "method_cu_reference": 1500,
                "same_slot_transaction_index_or_instruction_index_selector": False,
                "original_candidate_position_replay_established": False,
            },
            "getTokenAccountsByOwnerAtSlot": {
                "method_cu_reference": 40,
                "scope": (
                    "historical paginated wallet token-account discovery; not "
                    "instruction-position state"
                ),
            },
            "pricing_and_usage": {
                "method_cu_is_dollar_cost": False,
                "paid_plan_and_archive_eligibility_verified": False,
                "account_usage_limit_or_overage_cap_verified": False,
                "no_per_call_dollar_cost_claim": True,
            },
        },
        "position_state_feasibility": {
            "slot_boundary_bytes": (
                "potentially available only after verifying paid Archive entitlement, slot "
                "coverage, account type, and read completeness"
            ),
            "pre_slot_bytes": (
                "potentially addressable with lastUpdateBeforeSlot(S); not same-slot "
                "transaction-position state"
            ),
            "transaction_boundary": (
                "captured full-block identities and transaction-wide metadata exist for the "
                "included development frames; no validator replay was run"
            ),
            "candidate_instruction_position": (
                "not supplied by documented APIs; historical simulation starts from archived "
                "slot state and lacks a target transaction/instruction selector"
            ),
            "same_slot_predecessors": (
                "counted from transaction index; prior transaction effects and complete bank "
                "state are not reconstructed by this screen"
            ),
            "token2022": (
                "historical mint/account extension, epoch fee config, withheld amounts, "
                "transfer-hook state and invocation effects remain position-dependent"
            ),
            "wsol": "wrapped SOL token/lamport/rent/sync/close state remains position-dependent",
            "all_economic_admission_false": all(
                row["economic_admission"] is False for row in facts
            ),
            "audit_or_decoder_admission": False,
        },
        "official_source_snapshots": {
            "local_root": SOURCE_ROOT.relative_to(ROOT).as_posix(),
            "file_mtime_window_utc": source_window,
            "sha256_by_url": source_hashes,
        },
        "read_only_alchemy_usage_observation": {
            "path": USAGE_OBSERVATION.relative_to(ROOT).as_posix(),
            "sha256": USAGE_OBSERVATION_SHA256,
            "source_type": "Alchemy MCP account-usage admin methods; not chain RPC",
            "month_to_date_cu": 18_680,
            "month_to_date_usd_reported": "0.01",
            "usage_limit_field_present": False,
            "interpretation": (
                "aggregate account usage does not verify Account Archive entitlement, spend "
                "caps, or per-method Archive charges"
            ),
        },
        "captured_class_context": {
            "full_block_count": ident_receipt["scope"]["full_blocks"],
            "full_block_response_bytes": taxonomy_summary["capture_volume"][
                "full_block_response_bytes"
            ],
            "full_block_compressed_capture_bytes": taxonomy_summary["capture_volume"][
                "full_block_compressed_capture_bytes"
            ],
            "note": (
                "inherited from taxonomy receipt; not a claim of global discovery or "
                "population completeness"
            ),
        },
        "interpretation_limits": [
            (
                "a slot-finalized account snapshot is not the account state before a candidate "
                "transaction within that same slot"
            ),
            "lastUpdateBeforeSlot is slot-scoped and supplies no transaction or instruction index",
            (
                "the one-request whole-prefix-plus-candidate capacity screen does not prove "
                "causal necessity of every predecessor or historical replay; simulation is "
                "hypothetical and slot-state initialized"
            ),
            (
                "full transaction referenced-account counts, override plans, rendered response "
                "sizes, archive coverage, and batch economics remain unknown from accepted ledgers"
            ),
            (
                "no Alchemy chain RPC, account archive read, backfill, c4 response body, or "
                "tail2 access occurred"
            ),
            (
                "all rows retain economic_admission=false; no settlement, economic zero, audit, "
                "decoder, or training claim is produced"
            ),
        ],
    }
    return summary, facts


def _write_zstd(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    raw = (
        json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["zstd", "-q", "-f", "-19", "-c"], input=raw, check=True, capture_output=True
    )
    path.write_bytes(result.stdout)
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "row_count": len(rows),
        "contains_pubkeys": False,
        "contains_raw_capture_payloads": False,
        "compressed_bytes": len(result.stdout),
        "compressed_sha256": _sha(result.stdout),
        "uncompressed_bytes": len(raw),
        "uncompressed_sha256": _sha(raw),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    args = parser.parse_args()
    receipt, rows = build()
    receipt["derived_rows"] = _write_zstd(args.rows, rows)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"completed": True, "receipt": str(args.receipt), "row_count": len(rows)}))


if __name__ == "__main__":
    main()
