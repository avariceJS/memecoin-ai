#!/usr/bin/env python3
"""Replay the frozen buyExact tail2 dual-legacy development population offline only."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memecoin_pumpswap.economic_buy_exact_tail2_v1 import (  # noqa: E402
    ATA_INTERFACE_SOURCE_COMMIT,
    ATA_INTERFACE_SOURCE_SHA256,
    CONTRACT,
    diagnose_pumpswap_buy_exact_tail2_dual_legacy_v1,
)
from tools.brain_senses_buy_exact_v1_dev_replay import (  # noqa: E402
    _load_full_block,
    _row_key,
    _selected_rows,
    _sha,
)

EXPECTED_CENSUS = Path(
    "work/brain-senses-live-helius-alchemy-512-pair64-20261009/20261009T150000Z/"
    "paired-block-capture/buy_exact_v1_census_v3.json"
)
EXPECTED_PLAN = Path("work/pumpswap-buy-exact-v1-dev-plan-1500.json")
EXPECTED_CENSUS_SHA256 = "74cf0ed6b3e404874e98a7562d42ec25476214a1bbfac0cbc66ab9d83406ca37"
EXPECTED_PLAN_SHA256 = "c2d86ec3aa46d2f364acb98dadd88a8500380caa01caf0dc888cfaa1bba810a8"
DEVELOPMENT_PURPOSE = (
    "buyExact v1 development replay selection; not audit or population completeness"
)
EXPECTED_ROWS = 62


def replay(census_path: Path, plan_path: Path, output: Path) -> dict[str, Any]:
    census_path = census_path.resolve()
    plan_path = plan_path.resolve()
    output = output.resolve()
    if census_path != (REPO_ROOT / EXPECTED_CENSUS).resolve():
        raise ValueError("development_census_path_not_frozen_source")
    if plan_path != (REPO_ROOT / EXPECTED_PLAN).resolve():
        raise ValueError("development_plan_path_not_frozen_source")
    if output.exists():
        raise ValueError("output_exists_refusing_overwrite")

    census_bytes = census_path.read_bytes()
    plan_bytes = plan_path.read_bytes()
    if _sha(census_bytes) != EXPECTED_CENSUS_SHA256:
        raise ValueError("development_census_hash_mismatch")
    if _sha(plan_bytes) != EXPECTED_PLAN_SHA256:
        raise ValueError("development_plan_hash_mismatch")
    census = json.loads(census_bytes)
    plan = json.loads(plan_bytes)
    if plan.get("purpose") != DEVELOPMENT_PURPOSE:
        raise ValueError("development_only_replay_refuses_other_purpose")
    if plan.get("source_report_sha256") != EXPECTED_CENSUS_SHA256:
        raise ValueError("development_plan_census_binding_mismatch")
    if census.get("source", {}).get("plan_sha256") != plan.get("source_pair64_plan_sha256"):
        raise ValueError("development_pair64_source_binding_mismatch")
    selected = _selected_rows(census)
    supplied = plan.get("selection_rows")
    if (
        len(selected) != EXPECTED_ROWS
        or plan.get("row_count") != EXPECTED_ROWS
        or not isinstance(supplied, list)
        or [_row_key(row) for row in supplied] != [_row_key(row) for row in selected]
        or len({_row_key(row) for row in supplied}) != EXPECTED_ROWS
    ):
        raise ValueError("development_population_denominator_or_selection_mismatch")
    if plan.get("unique_transactions") != len({row["signature"] for row in supplied}):
        raise ValueError("development_unique_transaction_count_mismatch")

    source_root = Path(census["source"]["root"]).resolve()
    source_plan = source_root / "plan.json"
    if _sha(source_plan.read_bytes()) != census["source"].get("plan_sha256"):
        raise ValueError("captured_pair64_plan_hash_mismatch")
    binding_path = Path(census["source"]["provider_audit_receipt"])
    binding_bytes = binding_path.read_bytes()
    if _sha(binding_bytes) != census["source"].get("provider_audit_receipt_sha256"):
        raise ValueError("census_source_binding_receipt_hash_mismatch")
    binding = json.loads(binding_bytes)
    if binding.get("bound_plan_sha256") == census["source"].get("plan_sha256"):
        source_binding_kind = "pair64_plan_bound_provider_audit"
    else:
        expected_capture_root = (source_root / "captures/helius-free").resolve()
        if (
            Path(binding.get("capture_root", "")).resolve() != expected_capture_root
            or binding.get("selected_full_blocks_verified")
            != census["source"].get("expected_full_block_slots")
            or binding.get("offline_replay") is not True
        ):
            raise ValueError("census_source_binding_receipt_does_not_bind_full_blocks")
        source_binding_kind = "captured_class_full_block_replay_receipt"

    zstd = shutil.which("zstd")
    if zstd is None:
        raise ValueError("zstd_cli_unavailable")
    captures_by_slot = {row["slot"]: row for row in census["source"]["captures"]}
    if len(captures_by_slot) != census["source"].get("expected_full_block_slots"):
        raise ValueError("development_full_block_manifest_invalid")
    census_by_key = {
        _row_key(row): row for row in census["rows"] if isinstance(row.get("path"), list)
    }
    block_cache: dict[int, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    facts: list[dict[str, Any]] = []
    for row in supplied:
        key = _row_key(row)
        source_row = census_by_key.get(key)
        if source_row is None:
            raise ValueError("selected_reference_missing_from_census")
        block = _load_full_block(
            source_root, captures_by_slot[row["slot"]], zstd, block_cache, captures_by_slot
        )
        tx_index = row["transaction_index"]
        if not 0 <= tx_index < len(block["transactions"]):
            raise ValueError("selected_transaction_index_out_of_range")
        transaction = block["transactions"][tx_index]
        signatures = transaction.get("transaction", {}).get("signatures")
        if not isinstance(signatures, list) or not signatures or signatures[0] != row["signature"]:
            raise ValueError("selected_signature_index_mismatch")
        fact = diagnose_pumpswap_buy_exact_tail2_dual_legacy_v1(transaction, row["path"])
        counts[f"candidate_token_flow_state:{fact['candidate_token_flow_state']}"] += 1
        counts[
            f"transaction_target_account_ledger_state:{fact['transaction_target_account_ledger_state']}"
        ] += 1
        counts[
            f"event_crosscheck_state:{fact.get('event_crosscheck', {}).get('state', 'unavailable')}"
        ] += 1
        facts.append(
            {
                "slot": row["slot"],
                "transaction_index": tx_index,
                "signature": row["signature"],
                "path": row["path"],
                "availability_ns": source_row["availability_ns"],
                "native_event_time_unix_seconds": source_row["native_event_time_unix_seconds"],
                "fact": fact,
            }
        )
    if len(facts) != EXPECTED_ROWS:
        raise ValueError("development_replay_incomplete_denominator")

    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "status": "development_replay_only",
        "scope": (
            "all 62 frozen successful tail2 dual-legacy references in the opened "
            "2026-10-09 15:00Z Pair64 captured class"
        ),
        "source": {
            "census_report_path": str(census_path),
            "census_report_sha256": EXPECTED_CENSUS_SHA256,
            "development_plan_path": str(plan_path),
            "development_plan_sha256": EXPECTED_PLAN_SHA256,
            "source_capture_plan_sha256": census["source"]["plan_sha256"],
            "source_binding_receipt": str(binding_path),
            "source_binding_receipt_sha256": _sha(binding_bytes),
            "source_binding_kind": source_binding_kind,
            "captured_slots_replayed": sorted(block_cache),
            "full_block_receipts_replayed": len(block_cache),
            "rpc_requests": 0,
        },
        "pins": {
            "associated_token_account_interface": {
                "repository": "https://github.com/solana-program/associated-token-account",
                "commit": ATA_INTERFACE_SOURCE_COMMIT,
                "path": "interface/src/instruction.rs",
                "sha256": ATA_INTERFACE_SOURCE_SHA256,
                "scope": (
                    "instruction tags, ordered account list and required privileges; "
                    "not deployed-binary equivalence"
                ),
            },
            "replay_tool_sha256": _sha(Path(__file__).read_bytes()),
            "economic_contract_sha256": _sha(
                (REPO_ROOT / "src/memecoin_pumpswap/economic_buy_exact_tail2_v1.py").read_bytes()
            ),
            "attempt_inspector_sha256": _sha(
                (REPO_ROOT / "src/memecoin_pumpswap/buy_exact_v1.py").read_bytes()
            ),
            "development_capture_loader_sha256": _sha(
                (REPO_ROOT / "tools/brain_senses_buy_exact_v1_dev_replay.py").read_bytes()
            ),
        },
        "counts": {
            "selected_references": len(facts),
            "unique_transactions": len({row["signature"] for row in facts}),
            "captured_slots_replayed": len(block_cache),
            **dict(sorted(counts.items())),
        },
        "time_semantics": {
            "native_event_time": "captured finalized blockTime",
            "availability": "local receipt time for selected full-block capture",
            "decision_time": None,
        },
        "facts": facts,
        "admission": {"training": "none", "execution": "none", "wallet_net_price": "none"},
        "limitations": [
            (
                "Development-only replay of a captured-class sample; no global discovery "
                "or completeness claim."
            ),
            "No audit source, manifest, plan, or transaction payload is opened by this tool.",
            (
                "Fee equations are hypotheses; event fields are cross-checks and do not "
                "authorize settlement."
            ),
            (
                "Token-2022, +3 suffixes, failed transactions, and unsupported effects remain "
                "quarantined or attempt-only."
            ),
            (
                "Exact token-account endpoints do not establish wallet-net execution price "
                "or admission."
            ),
        ],
    }
    payload = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return {"output": str(output), "output_sha256": _sha(payload), "counts": report["counts"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--census", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(args.census, args.plan, args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        subprocess.CalledProcessError,
    ) as exc:
        print(
            json.dumps({"status": "failed_closed", "error": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
