#!/usr/bin/env python3
"""Offline strict settlement-feasibility replay for the two open c3 buyExact dev frames."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memecoin_pumpswap.buy_exact_v1 import ACCOUNT_ROLES, IDL_SHA256  # noqa: I001
from memecoin_pumpswap.compiled_economics import _flatten, _transaction_tree
from memecoin_pumpswap.decoder import PROGRAM, TOKEN
from memecoin_pumpswap.economic_buy_exact_pool_v2 import (
    CONTRACT as ECONOMIC_CONTRACT,
)
from memecoin_pumpswap.economic_buy_exact_pool_v2 import (
    inspect_pool_v2_buy_exact,
)
from memecoin_pumpswap.normalize import normalize_compiled_transaction
from tools.brain_senses_buy_exact_v1_census import (
    _capture_rows,
    _read_block,
)
from tools.brain_senses_buy_exact_v1_pool_v2_prefix_qualifier_v2 import (
    CONTRACT as PREFIX_CONTRACT,
)
from tools.brain_senses_buy_exact_v1_pool_v2_prefix_qualifier_v2 import (
    PINNED_LOCAL_IDL_SHA256,
)

CONTRACT = "pumpswap_buy_exact_c3_two_frame_dual_legacy_settlement_feasibility_v1"
EXPECTED_CENSUSES = {
    "20261010T050000Z": ROOT / "work/review/helius-v3-c3-first-frame-buyexact-census-v4.json",
    "20261010T060000Z": ROOT
    / "docs/evidence/pumpswap-buy-exact-v1-c3-frame2-census-2026-10-10.json",
}
EXPECTED_QUALIFICATIONS = {
    "20261010T050000Z": ROOT
    / "docs/evidence/pumpswap-buy-exact-v1-c3-pool-v2-prefix-raw-reconciled-2026-10-10.json",
    "20261010T060000Z": ROOT
    / (
        "work/brain-senses-buy-exact-v1-c3-frame2-pool-v2-prefix-"
        "raw-reconciled-replay-2026-10-10.json"
    ),
}
EXPECTED_FRAME_RECEIPTS = {
    "20261010T050000Z": ROOT / "docs/evidence/helius-inventory-v3-c3-first-frame-2026-10-10.json",
    "20261010T060000Z": ROOT / "docs/evidence/helius-inventory-v3-c3-frame2-2026-10-10.json",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value, raw


def _subtree_paths(normalized: dict[str, Any], path: list[int]) -> set[tuple[int, ...]]:
    roots = _transaction_tree(normalized)
    matches = [
        node for root in roots for node in _flatten(root) if node["instruction"]["path"] == path
    ]
    if len(matches) != 1:
        raise ValueError("candidate_invocation_node_not_unique")
    return {tuple(node["instruction"]["path"]) for node in _flatten(matches[0])}


def _candidate_node(normalized: dict[str, Any], path: list[int]) -> dict[str, Any]:
    nodes = [*normalized["outer_instructions"], *normalized["inner_instructions"]]
    matches = [row for row in nodes if row["path"] == path]
    if len(matches) != 1:
        raise ValueError("candidate_instruction_path_not_unique")
    instruction = matches[0]
    names = ACCOUNT_ROLES
    if instruction["program_id"] != PROGRAM or len(instruction["accounts"]) != len(names) + 3:
        raise ValueError("candidate_instruction_layout_mismatch")
    roles = dict(zip(names, instruction["accounts"][: len(names)], strict=True))
    tails = instruction["accounts"][-3:]
    return {"instruction": instruction, "roles": roles, "tails": tails}


def _outside_touches(
    normalized: dict[str, Any], subtree_paths: set[tuple[int, ...]], tracked_accounts: set[str]
) -> list[dict[str, Any]]:
    touches = []
    for instruction in [*normalized["outer_instructions"], *normalized["inner_instructions"]]:
        if tuple(instruction["path"]) in subtree_paths:
            continue
        overlap = sorted(set(instruction["accounts"]) & tracked_accounts)
        if overlap:
            touches.append(
                {
                    "path": instruction["path"],
                    "program_id": instruction["program_id"],
                    "accounts": overlap,
                }
            )
    return touches


def _load_frames() -> list[dict[str, Any]]:
    zstd = shutil.which("zstd")
    if not zstd:
        raise ValueError("zstd_cli_unavailable")
    frames = []
    for bucket in sorted(EXPECTED_CENSUSES):
        census_path = EXPECTED_CENSUSES[bucket].resolve()
        qualification_path = EXPECTED_QUALIFICATIONS[bucket].resolve()
        frame_path = EXPECTED_FRAME_RECEIPTS[bucket].resolve()
        census, census_bytes = _json(census_path)
        qualification, qualification_bytes = _json(qualification_path)
        frame, frame_bytes = _json(frame_path)
        if qualification.get("contract") != PREFIX_CONTRACT:
            raise ValueError("pool_v2_qualification_contract_mismatch")
        if qualification.get("scope", {}).get("frame_bucket") != bucket:
            raise ValueError("pool_v2_qualification_bucket_mismatch")
        if qualification.get("source", {}).get("report_sha256") != _sha(census_bytes):
            raise ValueError("qualification_does_not_bind_exact_census_bytes")
        if qualification.get("source", {}).get("frame_receipt_sha256") != _sha(frame_bytes):
            raise ValueError("qualification_does_not_bind_frame_receipt")
        if census.get("source", {}).get("root") != qualification.get("scope", {}).get(
            "capture_root"
        ):
            # Older v2 reports bind the local raw root through each capture receipt instead.
            root = Path(census.get("source", {}).get("root", "")).resolve()
        else:
            root = Path(census["source"]["root"]).resolve()
        if not root.is_dir():
            raise ValueError("captured_epoch_root_missing")
        if census.get("pins", {}).get("pump_amm_idl_sha256", IDL_SHA256) != PINNED_LOCAL_IDL_SHA256:
            raise ValueError("census_pinned_idl_mismatch")
        if IDL_SHA256 != PINNED_LOCAL_IDL_SHA256:
            raise ValueError("local_pinned_idl_mismatch")
        captures = _capture_rows(root, v3_layout=True)
        captures_by_slot: dict[int, tuple[Path, dict[str, Any]]] = {}
        for capture_path, receipt, request in captures:
            params = request.get("params")
            if isinstance(params, list) and params and type(params[0]) is int:
                slot = params[0]
                if slot in captures_by_slot:
                    raise ValueError("duplicate_full_block_capture_slot")
                captures_by_slot[slot] = (capture_path, receipt)
        bound = {row["slot"]: row for row in qualification["source"]["full_block_captures"]}
        frame_slots = frame.get("frame", {}).get("selected_slots")
        if frame_slots is not None and set(bound) != set(frame_slots):
            raise ValueError("frame_selected_slots_do_not_match_qualification")
        if len(bound) != qualification.get("scope", {}).get("selected_full_blocks"):
            raise ValueError("qualification_full_block_denominator_mismatch")
        if set(bound) - set(captures_by_slot):
            raise ValueError("qualified_full_block_capture_missing")
        blocks: dict[int, dict[str, Any]] = {}
        for slot, expected in bound.items():
            capture_path, receipt = captures_by_slot[slot]
            if (
                receipt.get("response_sha256") != expected["response_sha256"]
                or receipt.get("storage", {}).get("sha256") != expected["sha256"]
                or (
                    expected.get("receipt_ns") is not None
                    and receipt.get("received_ns") != expected.get("receipt_ns")
                )
            ):
                raise ValueError("full_block_receipt_differs_from_qualification")
            blocks[slot] = _read_block(zstd, capture_path, receipt)
        rows = qualification.get("result", {}).get("rows")
        if not isinstance(rows, list):
            raise ValueError("qualified_candidate_rows_missing")
        if len(rows) != qualification.get("scope", {}).get("references"):
            raise ValueError("qualified_candidate_denominator_mismatch")
        frames.append(
            {
                "bucket": bucket,
                "census_path": census_path,
                "census_sha256": _sha(census_bytes),
                "qualification_path": qualification_path,
                "qualification_sha256": _sha(qualification_bytes),
                "frame_path": frame_path,
                "frame_sha256": _sha(frame_bytes),
                "epoch_root": root,
                "blocks": blocks,
                "rows": rows,
                "population_binding": qualification.get("population_binding"),
            }
        )
    return frames


def replay(output: Path) -> dict[str, Any]:
    output = output.resolve()
    if output.exists():
        raise ValueError("output_exists_refusing_overwrite")
    frames = _load_frames()
    result_rows = []
    counts: Counter[str] = Counter()
    for frame in frames:
        for qualified in frame["rows"]:
            counts[f"{frame['bucket']}:structural:{qualified['state']}"] += 1
            if qualified.get("transaction_status") != "success":
                counts[f"{frame['bucket']}:failed_attempt_only"] += 1
                continue
            slot, tx_index, path = (
                qualified["slot"],
                qualified["transaction_index"],
                qualified["path"],
            )
            block = frame["blocks"].get(slot)
            if block is None or not 0 <= tx_index < len(block["transactions"]):
                raise ValueError("qualified_transaction_index_out_of_range")
            tx = block["transactions"][tx_index]
            signatures = tx.get("transaction", {}).get("signatures")
            if (
                not isinstance(signatures, list)
                or not signatures
                or signatures[0] != qualified["signature"]
            ):
                raise ValueError("qualified_signature_index_mismatch")
            normalized = normalize_compiled_transaction(tx)
            target = _candidate_node(normalized, path)
            wire = bytes.fromhex(target["instruction"]["data_hex"])
            if _sha(wire) != qualified.get("instruction_data_sha256"):
                raise ValueError("candidate_instruction_wire_hash_mismatch")
            roles = target["roles"]
            dual_legacy = (
                roles["base_token_program"] == TOKEN and roles["quote_token_program"] == TOKEN
            )
            counts[f"{frame['bucket']}:successful_references"] += 1
            counts[
                f"{frame['bucket']}:token_pair:{roles['base_token_program']}:{roles['quote_token_program']}"
            ] += 1
            if not dual_legacy:
                counts[f"{frame['bucket']}:successful_non_dual_legacy_quarantined"] += 1
                continue
            counts[f"{frame['bucket']}:successful_dual_legacy"] += 1
            if qualified.get("state") != "sdk_source_bound_pool_v2_pda_candidate":
                counts[f"{frame['bucket']}:structural_gate_closed_success"] += 1
                result_rows.append(
                    {
                        "bucket": frame["bucket"],
                        "slot": slot,
                        "transaction_index": tx_index,
                        "signature": qualified["signature"],
                        "path": path,
                        "native_event_time_unix_seconds": qualified.get(
                            "native_event_time_unix_seconds"
                        ),
                        "availability_ns": qualified.get(
                            "availability_ns", qualified.get("raw_receipt_ns")
                        ),
                        "state": "quarantined",
                        "reason": "frozen_pool_v2_structural_qualification_failed",
                        "structural_failed_checks": qualified.get("failed_checks", []),
                    }
                )
                continue

            fact = inspect_pool_v2_buy_exact(tx, path)
            counts[f"{frame['bucket']}:instruction_event:{fact['state']}"] += 1
            tracked = set(roles.values()) | set(target["tails"])
            touches = _outside_touches(normalized, _subtree_paths(normalized, path), tracked)
            counts[f"{frame['bucket']}:outside_target_touch_rows:{len(touches)}"] += 1
            if fact.get("state") != "exact_candidate_subtree_token_settlement":
                result_rows.append(
                    {
                        "bucket": frame["bucket"],
                        "slot": slot,
                        "transaction_index": tx_index,
                        "signature": qualified["signature"],
                        "path": path,
                        "state": fact["state"],
                        "reason": fact.get("reason", "trade_fact_not_exact"),
                        "details": {
                            key: value
                            for key, value in fact.items()
                            if key not in {"contract", "state", "reason"}
                        },
                        "outside_subtree_target_touches": touches,
                    }
                )
                continue

            if touches:
                state, reason = "censored", "outside_subtree_target_account_touches_unresolved"
            else:
                state, reason = fact["state"], None
            counts[f"{frame['bucket']}:strict_state:{state}"] += 1
            result_rows.append(
                {
                    "bucket": frame["bucket"],
                    "slot": slot,
                    "transaction_index": tx_index,
                    "signature": qualified["signature"],
                    "path": path,
                    "native_event_time_unix_seconds": qualified.get(
                        "native_event_time_unix_seconds"
                    ),
                    "availability_ns": qualified.get(
                        "availability_ns", qualified.get("raw_receipt_ns")
                    ),
                    "state": state,
                    "reason": reason,
                    "trade_fact_state": fact["state"],
                    "outside_subtree_target_touches": touches,
                    "fact": fact,
                    "admission": "none",
                }
            )
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "scope": (
            "all successful references in the two already-open c3 pool-v2 dev frames; "
            "not an independent audit or completeness claim"
        ),
        "source": {
            "frames": [
                {
                    "bucket": frame["bucket"],
                    "census_path": str(frame["census_path"]),
                    "census_sha256": frame["census_sha256"],
                    "qualification_path": str(frame["qualification_path"]),
                    "qualification_sha256": frame["qualification_sha256"],
                    "frame_receipt_path": str(frame["frame_path"]),
                    "frame_receipt_sha256": frame["frame_sha256"],
                    "epoch_plan_sha256": frame["population_binding"].get("plan_sha256")
                    if isinstance(frame["population_binding"], dict)
                    else None,
                    "selected_slots": sorted(frame["blocks"]),
                    "full_blocks_replayed": len(frame["blocks"]),
                    "raw_candidate_references": frame["population_binding"].get(
                        "raw_candidate_references"
                    )
                    if isinstance(frame["population_binding"], dict)
                    else len(frame["rows"]),
                    "candidate_set_equal": frame["population_binding"].get("candidate_set_equal")
                    if isinstance(frame["population_binding"], dict)
                    else None,
                }
                for frame in frames
            ],
            "idl_sha256": PINNED_LOCAL_IDL_SHA256,
            "economic_diagnostic_contract": ECONOMIC_CONTRACT,
            "economic_diagnostic_sha256": _sha(
                (ROOT / "src/memecoin_pumpswap/economic_buy_exact_pool_v2.py").read_bytes()
            ),
            "runner_sha256": _sha(Path(__file__).read_bytes()),
        },
        "units": {
            "spendable_quote_in": "atomic_units_of_quote_mint",
            "minimum_base_amount_out": "atomic_units_of_base_mint",
            "token_amounts": "atomic_units_of_named_mint; no decimal scaling",
            "slot": "slot_index",
            "transaction_index": "index_within_full_block",
        },
        "counts": dict(sorted(counts.items())),
        "rows": result_rows,
        "admission": {"training": "none", "execution": "none", "wallet_net_price": "none"},
        "limitations": [
            (
                "Both c3 frames are development material already examined by earlier structural "
                "qualification; they are not sealed independent audit data."
            ),
            "Completeness is scoped to candidates in these 32 captured full blocks only.",
            (
                "Only successful dual-legacy structural candidates are evaluated; failed refs "
                "remain attempt-only and Token-2022 pairs remain quarantined."
            ),
            (
                "Exact outcomes are candidate-subtree token-account facts only; no wallet-net "
                "proceeds, pool reserve accounting, fee qualification, or execution quality."
            ),
            (
                "Provider-returned ordered traces and token endpoints are accepted as captured; "
                "omitted provider effects cannot be ruled out."
            ),
            ("Other states remain censored, quarantined, or failed; unknown is never zero."),
        ],
    }
    payload = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = replay(args.output)
    print(
        json.dumps(
            {"output": str(args.output.resolve()), "counts": report["counts"]}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
