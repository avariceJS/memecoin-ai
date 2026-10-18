#!/usr/bin/env python3
"""Offline source-supported suffix-role census for the non-59 c3 buyExact references."""

from __future__ import annotations

import argparse
import json
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
from tools.brain_senses_buy_exact_v1_role_qualifier import (  # noqa: E402
    SELECTOR as ROLE_QUALIFIER_SELECTOR,
)
from tools.brain_senses_buy_exact_v1_role_qualifier import (  # noqa: E402
    TARGET_ROLES,
    _capture_rows,
    _read_block,
    _roles_from_raw,
    _sha,
    _validate_frame_binding,
)

EXPECTED_TOTAL_REFERENCES = 365
EXPECTED_ALREADY_QUALIFIED = 59
PAIR_STATES = ("source_supported_pair", "unresolved_extension", "discordant")
PREFIX_STATES = ("none", "unresolved_prefix")


def classify_suffix(
    census_row: dict[str, Any],
    raw_suffix: list[str],
    raw_roles: dict[str, str],
) -> dict[str, Any]:
    """Classify only the pinned recipient/ATA tail; keep all preceding accounts unknown."""
    result: dict[str, Any] = {
        "pair_state": "unresolved_extension",
        "prefix_state": "none",
        "wallet_in_pinned_eight": None,
        "ata_matches_raw_quote_roles": None,
        "raw_pair_matches_census": None,
        "discordance_reasons": [],
    }
    if len(raw_suffix) < 2:
        result["discordance_reasons"] = ["fewer_than_two_remaining_accounts"]
        return result

    wallet, ata = raw_suffix[-2:]
    wallet_match = wallet in FEE_RECIPIENTS_UPGRADE_2026_04_28
    derived_ata = associated_token_address(
        wallet, raw_roles["quote_mint"], raw_roles["quote_token_program"]
    )
    ata_match = ata == derived_ata
    census_match = wallet == census_row.get("observed_fee_recipient") and ata == census_row.get(
        "observed_fee_ata"
    )
    result.update(
        {
            "wallet_in_pinned_eight": wallet_match,
            "ata_matches_raw_quote_roles": ata_match,
            "raw_pair_matches_census": census_match,
        }
    )
    if not wallet_match:
        result["discordance_reasons"].append("trailing_wallet_not_in_pinned_set")
    if not ata_match:
        result["discordance_reasons"].append("trailing_ata_derivation_mismatch")
    if not census_match:
        result["discordance_reasons"].append("raw_suffix_differs_from_census")

    if wallet_match and ata_match and census_match:
        result["pair_state"] = "source_supported_pair"
        if len(raw_suffix) > 2:
            result["prefix_state"] = "unresolved_prefix"
    elif (
        census_row.get("suffix_state", "").startswith("documented_fee_pair")
        or census_row.get("observed_fee_recipient")
        or census_row.get("observed_fee_ata")
    ):
        result["pair_state"] = "discordant"
    return result


def census(
    report_path: Path,
    epoch_root: Path,
    frame_receipt_path: Path,
) -> dict[str, Any]:
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    _, summary, frame_bytes, plan_bytes, summary_bytes = _validate_frame_binding(
        report, epoch_root, frame_receipt_path
    )
    references = report.get("rows", [])
    if len(references) != EXPECTED_TOTAL_REFERENCES:
        raise ValueError(f"expected_365_references_got_{len(references)}")
    already_qualified = [
        row
        for row in references
        if all(row.get(key) == value for key, value in ROLE_QUALIFIER_SELECTOR.items())
    ]
    if len(already_qualified) != EXPECTED_ALREADY_QUALIFIED:
        raise ValueError(f"expected_59_prior_refs_got_{len(already_qualified)}")
    prior_keys = {
        (row["slot"], row["transaction_index"], tuple(row["path"])) for row in already_qualified
    }
    candidates = [
        row
        for row in references
        if (row["slot"], row["transaction_index"], tuple(row["path"])) not in prior_keys
    ]
    if len(candidates) != 306:
        raise ValueError(f"expected_306_other_refs_got_{len(candidates)}")
    prior_receipt_path = (
        REPO_ROOT
        / "docs/evidence/pumpswap-buy-exact-v1-c3-role-tuple-qualification-2026-10-10.json"
    )
    prior_receipt_bytes = prior_receipt_path.read_bytes()
    prior_result = json.loads(prior_receipt_bytes).get("result", {})
    if (
        prior_result.get("references_verified") != EXPECTED_ALREADY_QUALIFIED
        or prior_result.get("suffix_source_checks_passed") != EXPECTED_ALREADY_QUALIFIED
        or prior_result.get("suffix_source_checks_failed_or_unresolved") != 0
    ):
        raise ValueError("prior_59_suffix_role_gate_not_qualified")

    zstd = _find_zstd()
    capture_entries = _capture_rows(epoch_root, v3_layout=True)
    by_slot = {}
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

    loaded_blocks: dict[int, dict[str, Any]] = {}
    pool_roles: dict[str, tuple[str, ...]] = {}
    status_counts = Counter()
    census_suffix_states = Counter()
    census_suffix_check_outcomes: dict[str, Counter[str]] = {}
    pair_counts = Counter()
    prefix_counts = Counter()
    classification_by_status = {
        "success": {"pair_state": Counter(), "prefix_state": Counter()},
        "failed": {"pair_state": Counter(), "prefix_state": Counter()},
    }
    output_rows = []
    for row in candidates:
        slot = row["slot"]
        if slot not in by_slot:
            raise ValueError("candidate_slot_missing_full_block_capture")
        if slot not in loaded_blocks:
            receipt, capture_path = by_slot[slot]
            loaded_blocks[slot] = _read_block(zstd, capture_path, receipt)
        block = loaded_blocks[slot]
        tx_index = row["transaction_index"]
        transactions = block.get("transactions", [])
        if type(tx_index) is not int or not 0 <= tx_index < len(transactions):
            raise ValueError("candidate_transaction_index_missing")
        tx = transactions[tx_index]
        signatures = tx.get("transaction", {}).get("signatures", [])
        if not signatures or signatures[0] != row["signature"]:
            raise ValueError("candidate_signature_mismatch")
        raw_roles, wire_hex, account_values = _roles_from_raw(tx, row["path"])
        if wire_hex != row["instruction_data_hex"]:
            raise ValueError("candidate_instruction_wire_mismatch")
        expected_status = "success" if tx.get("meta", {}).get("err") is None else "failed"
        if row["transaction_status"] != expected_status:
            raise ValueError("candidate_transaction_status_mismatch")
        identified = row.get("identified_roles") or {}
        if any(raw_roles[role] != identified.get(role) for role in TARGET_ROLES):
            raise ValueError("identified_idl_role_mismatch")
        if raw_roles["base_mint"] == raw_roles["quote_mint"]:
            raise ValueError("base_quote_mint_alias")

        raw_suffix = account_values[len(ACCOUNT_ROLES) :]
        if len(raw_suffix) != row.get("remaining_account_count"):
            raise ValueError("raw_remaining_account_count_mismatch")
        classification = classify_suffix(row, raw_suffix, raw_roles)
        status_counts[expected_status] += 1
        census_suffix_states[row["suffix_state"]] += 1
        for check, value in row.get("suffix_checks", {}).items():
            outcome = "true" if value is True else "false" if value is False else "unknown"
            census_suffix_check_outcomes.setdefault(check, Counter())[outcome] += 1
        pair_counts[classification["pair_state"]] += 1
        prefix_counts[classification["prefix_state"]] += 1
        classification_by_status[expected_status]["pair_state"][classification["pair_state"]] += 1
        classification_by_status[expected_status]["prefix_state"][
            classification["prefix_state"]
        ] += 1

        pool = raw_roles["pool"]
        tuple_value = tuple(raw_roles[role] for role in TARGET_ROLES[1:])
        previous = pool_roles.setdefault(pool, tuple_value)
        if previous != tuple_value:
            raise ValueError("conflicting_role_tuple_for_pool")
        output_rows.append(
            {
                "slot": slot,
                "transaction_index": tx_index,
                "path": row["path"],
                "signature": row["signature"],
                "transaction_status": expected_status,
                "native_event_time_unix_seconds": row["native_event_time_unix_seconds"],
                "availability_ns": row["availability_ns"],
                "instruction_data_length": row["instruction_data_length"],
                "argument_state": row["argument_state"],
                "remaining_account_count": len(raw_suffix),
                "census_suffix_state": row["suffix_state"],
                "census_suffix_checks": row.get("suffix_checks", {}),
                "pool": pool,
                "base_mint": raw_roles["base_mint"],
                "quote_mint": raw_roles["quote_mint"],
                "base_token_program": raw_roles["base_token_program"],
                "quote_token_program": raw_roles["quote_token_program"],
                "ordered_extra_accounts": raw_suffix,
                **classification,
            }
        )

    if sum(status_counts.values()) != len(candidates):
        raise ValueError("candidate_status_denominator_mismatch")
    return {
        "schema_version": 1,
        "evidence_id": "pumpswap-buy-exact-v1-c3-extra-role-census-2026-10-10",
        "scope": (
            "offline structural suffix-role replay over the 306 c3 buyExact refs outside "
            "the separately qualified 59 two-account candidates"
        ),
        "result": {
            "references_verified": len(output_rows),
            "successful_transaction_references": status_counts["success"],
            "failed_transaction_references": status_counts["failed"],
            "distinct_pools": len(pool_roles),
            "census_suffix_state_counts": dict(census_suffix_states),
            "census_suffix_check_outcomes": {
                check: {outcome: counts[outcome] for outcome in ("true", "false", "unknown")}
                for check, counts in census_suffix_check_outcomes.items()
            },
            "pair_state_counts": {state: pair_counts[state] for state in PAIR_STATES},
            "prefix_state_counts": {state: prefix_counts[state] for state in PREFIX_STATES},
            "status_stratified_pair_and_prefix_counts": {
                status: {
                    "pair_state": {state: classes["pair_state"][state] for state in PAIR_STATES},
                    "prefix_state": {
                        state: classes["prefix_state"][state] for state in PREFIX_STATES
                    },
                }
                for status, classes in classification_by_status.items()
            },
            "references": output_rows,
        },
        "source": {
            "census_report_sha256": _sha(report_bytes),
            "frame_receipt": frame_receipt_path.as_posix(),
            "frame_receipt_sha256": _sha(frame_bytes),
            "epoch_plan_sha256": _sha(plan_bytes),
            "epoch_summary_sha256": _sha(summary_bytes),
            "full_block_captures": sorted(capture_bindings, key=lambda item: item["slot"]),
            "pinned_idl_sha256": IDL_SHA256,
            "fee_recipient_notice_commit": FEE_RECIPIENTS_DOC_COMMIT,
            "fee_recipient_notice_sha256": FEE_RECIPIENTS_DOC_SHA256,
            "buy_exact_decoder_sha256": _sha(
                (REPO_ROOT / "src/memecoin_pumpswap/buy_exact_v1.py").read_bytes()
            ),
            "compiled_economics_sha256": _sha(
                (REPO_ROOT / "src/memecoin_pumpswap/compiled_economics.py").read_bytes()
            ),
            "prior_59_role_receipt_sha256": _sha(prior_receipt_bytes),
            "role_qualifier_helper_sha256": _sha(
                Path(__file__).with_name("brain_senses_buy_exact_v1_role_qualifier.py").read_bytes()
            ),
            "qualifier_sha256": _sha(Path(__file__).read_bytes()),
            "prior_59_role_receipt": prior_receipt_path.relative_to(REPO_ROOT).as_posix(),
        },
        "limitations": [
            "Only the trailing wallet/ATA pair is source-tested; leading remaining accounts "
            "retain unresolved roles.",
            "No pool_v2, cashback, hook, or other extension role is assigned without its own "
            "pinned order and derivation contract.",
            "Successful transaction references do not establish fills, fees moved, wallet "
            "settlement, pricing, or economic completeness.",
            "All buyExact economics and Token-2022 effects remain quarantined; this is a "
            "one-frame structural census.",
        ],
        "time_semantics": {
            "native_event_time": "blockTime reported in the captured finalized block",
            "availability": (
                "local full-block capture receipt time; not historical provider availability"
            ),
            "decision_time": "not applicable to this offline structural census",
        },
    }


def _find_zstd() -> str:
    import shutil

    value = shutil.which("zstd")
    if value is None:
        raise ValueError("zstd_cli_unavailable")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--epoch-root", type=Path, required=True)
    parser.add_argument("--frame-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite output")
    result = census(args.report, args.epoch_root, args.frame_receipt)
    data = json.dumps(result, indent=2, sort_keys=True).encode() + b"\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        stream.write(data)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": _sha(data),
                "result": {
                    key: value for key, value in result["result"].items() if key != "references"
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
