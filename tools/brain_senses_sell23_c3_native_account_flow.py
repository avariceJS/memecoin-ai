#!/usr/bin/env python3
"""Offline native-account lamport-conservation gate for frozen c3 sell23 rows.

This verifies only the temporary legacy-token account's ordered, observed flow.
It does not infer rent, wrapped-token balances from lamports, close-destination
credit, wallet proceeds, or economic admission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from brain_senses_census_pumpswap_sell_ledger import _capture  # noqa: E402

from memecoin_pumpswap.decoder import PROGRAM, TOKEN, encode58  # noqa: E402
from memecoin_pumpswap.economic_v2 import diagnose_pumpswap_sell_v2  # noqa: E402
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402
from memecoin_pumpswap.schema import SCHEMA  # noqa: E402

CONTRACT = "pumpswap_sell23_c3_native_account_flow_v1"
FEASIBILITY = REPO / "docs/evidence/pumpswap-sell23-c3-native-close-feasibility-2026-10-10.json"
BASELINE = REPO / "docs/evidence/pumpswap-sell23-c3-six-frame-ledger-replay-2026-10-10.json"
CAPTURE_ROOT = REPO / "work/brain-senses-live-helius-inventory-v3-20261010-3/epochs"
FRAME_IDS = [f"20261010T{hour:02d}0000Z" for hour in range(5, 11)]
SYSTEM = "11111111111111111111111111111111"
ASSOCIATED_TOKEN = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
WSOL = "So11111111111111111111111111111111111111112"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text())


def _system_effect(ix: dict[str, Any]) -> dict[str, Any] | None:
    if ix["program_id"] != SYSTEM:
        return None
    data = bytes.fromhex(ix["data_hex"])
    accounts = ix["accounts"]
    tag = int.from_bytes(data[:4], "little") if len(data) >= 4 else None
    if tag == 0 and len(data) == 52 and len(accounts) >= 2:
        return {
            "kind": "create_account",
            "path": ix["path"],
            "payer": accounts[0],
            "account": accounts[1],
            "lamports": int.from_bytes(data[4:12], "little"),
            "space_bytes": int.from_bytes(data[12:20], "little"),
            "owner_program": encode58(data[20:52]),
        }
    if tag == 2 and len(data) == 12 and len(accounts) >= 2:
        return {
            "kind": "system_transfer",
            "path": ix["path"],
            "source": accounts[0],
            "destination": accounts[1],
            "lamports": int.from_bytes(data[4:12], "little"),
        }
    return {"kind": "unsupported_touch", "path": ix["path"], "reason": "unknown_system_instruction"}


def _token_effect(ix: dict[str, Any]) -> dict[str, Any] | None:
    if ix["program_id"] != TOKEN:
        return None
    data = bytes.fromhex(ix["data_hex"])
    accounts = ix["accounts"]
    path = ix["path"]
    if not data:
        return {"kind": "unsupported_touch", "path": path, "reason": "empty_token_instruction"}
    tag = data[0]
    if tag == 22 and data == b"\x16" and accounts:
        return {"kind": "initialize_immutable_owner", "path": path, "account": accounts[0]}
    if tag in (1, 16, 18) and len(accounts) >= 2:
        expected = {1: 1, 16: 33, 18: 33}[tag]
        if len(data) == expected:
            owner = accounts[2] if tag == 1 and len(accounts) >= 3 else None
            return {
                "kind": "initialize_account",
                "path": path,
                "account": accounts[0],
                "mint": accounts[1],
                "owner": owner,
                "variant": tag,
            }
    if tag == 17 and data == b"\x11" and accounts:
        return {"kind": "sync_native", "path": path, "account": accounts[0]}
    if tag == 9 and data == b"\x09" and len(accounts) >= 3:
        return {
            "kind": "close_account",
            "path": path,
            "account": accounts[0],
            "destination": accounts[1],
            "authority": accounts[2],
        }
    if tag == 12 and len(data) == 10 and len(accounts) >= 4:
        return {
            "kind": "transfer_checked",
            "path": path,
            "source": accounts[0],
            "mint": accounts[1],
            "destination": accounts[2],
            "amount_atomic": int.from_bytes(data[1:9], "little"),
            "decimals": data[9],
        }
    if tag == 3 and len(data) == 9 and len(accounts) >= 3:
        return {
            "kind": "transfer",
            "path": path,
            "source": accounts[0],
            "destination": accounts[1],
            "amount_atomic": int.from_bytes(data[1:9], "little"),
        }
    return {"kind": "unsupported_touch", "path": path, "reason": f"unknown_legacy_token_tag_{tag}"}


def _touch_effect(ix: dict[str, Any], account: str, target_path: list[int]) -> dict[str, Any]:
    effect = _system_effect(ix) or _token_effect(ix)
    data = bytes.fromhex(ix["data_hex"])
    if effect is not None:
        return effect
    if ix["program_id"] == ASSOCIATED_TOKEN and data == b"\x01" and len(ix["accounts"]) == 6:
        if ix["accounts"][1] == account:
            return {
                "kind": "associated_token_create_idempotent",
                "path": ix["path"],
                "account": account,
            }
    if ix["program_id"] == PROGRAM and ix["path"] == target_path:
        return {"kind": "frozen_sell23_target_instruction", "path": ix["path"], "account": account}
    return {
        "kind": "unsupported_touch",
        "path": ix["path"],
        "reason": "unclassified_program_or_shape",
    }


def _lamports(tx: dict[str, Any], account_index: int, name: str, account_count: int) -> int | None:
    values = (tx.get("meta") or {}).get(name)
    if not isinstance(values, list) or len(values) != account_count:
        return None
    value = values[account_index]
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid_{name}_lamport_value")
    return value


def _ordered(path: list[int]) -> tuple[int, ...]:
    return tuple(path)


def _close_sweep_candidate(
    pre: int | None,
    create: int | None,
    credited: int,
    debited: int,
    post: int | None,
) -> int | None:
    """Conserve account lamports; never mix in token atomic amounts."""
    if pre is None or create is None or post is None:
        return None
    values = (pre, create, credited, debited, post)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("invalid_lamport_flow_quantity")
    result = pre + create + credited - debited - post
    return result if result >= 0 else None


def classify_account(tx: dict[str, Any], account: str, target_path: list[int]) -> dict[str, Any]:
    normalized = normalize_compiled_transaction(tx)
    account_rows = [i for i, row in enumerate(normalized["accounts"]) if row["pubkey"] == account]
    if len(account_rows) != 1:
        return {
            "status": "missing_endpoint",
            "reason": "message_account_index_missing_or_duplicate",
            "account": account,
        }
    account_index = account_rows[0]
    account_count = len(normalized["accounts"])
    pre = _lamports(tx, account_index, "preBalances", account_count)
    post = _lamports(tx, account_index, "postBalances", account_count)
    all_ix = sorted(
        [*normalized["outer_instructions"], *normalized["inner_instructions"]],
        key=lambda ix: _ordered(ix["path"]),
    )
    touched = [ix for ix in all_ix if account in ix["accounts"]]
    if not touched:
        raise ValueError("native_target_has_no_instruction_touches")
    trace = [
        {
            "path": ix["path"],
            "program_id": ix["program_id"],
            "accounts": ix["accounts"],
            "data_hex": ix["data_hex"],
            "effect": _touch_effect(ix, account, target_path),
        }
        for ix in touched
    ]
    effects = [row["effect"] for row in trace]
    unsupported = [e for e in effects if e["kind"] == "unsupported_touch"]
    creates = [e for e in effects if e["kind"] == "create_account" and e.get("account") == account]
    transfers = [
        e
        for e in effects
        if e["kind"] == "system_transfer" and account in (e.get("source"), e.get("destination"))
    ]
    inits = [
        e for e in effects if e["kind"] == "initialize_account" and e.get("account") == account
    ]
    immutable = [
        e
        for e in effects
        if e["kind"] == "initialize_immutable_owner" and e.get("account") == account
    ]
    syncs = [e for e in effects if e["kind"] == "sync_native" and e.get("account") == account]
    closes = [e for e in effects if e["kind"] == "close_account" and e.get("account") == account]
    token_transfers = [
        e
        for e in effects
        if e["kind"] in ("transfer_checked", "transfer")
        and account in (e.get("source"), e.get("destination"))
    ]
    reasons: list[str] = []
    if pre is None or post is None:
        reasons.append("missing_endpoint")
    if unsupported:
        reasons.append("unsupported_touch")
    expected_counts = (
        (len(creates), 1, "create_count_not_one"),
        (len(inits), 1, "initialize_count_not_one"),
        (len(immutable), 1, "immutable_owner_init_count_not_one"),
        (len(syncs), 1, "sync_native_count_not_one"),
        (len(closes), 1, "close_count_not_one"),
        (
            len([e for e in effects if e["kind"] == "associated_token_create_idempotent"]),
            1,
            "associated_create_count_not_one",
        ),
        (
            len([e for e in effects if e["kind"] == "frozen_sell23_target_instruction"]),
            1,
            "sell23_target_touch_count_not_one",
        ),
    )
    reasons.extend(reason for actual, expected, reason in expected_counts if actual != expected)
    if len(transfers) != 1 or transfers and transfers[0].get("destination") != account:
        reasons.append("system_funding_transfer_not_exactly_one_inbound")
    if len(creates) == 1 and (
        creates[0]["owner_program"] != TOKEN or creates[0]["space_bytes"] != 165
    ):
        reasons.append("created_account_owner_or_size_mismatch")
    if len(inits) == 1 and (inits[0].get("mint") != WSOL or inits[0].get("variant") != 18):
        reasons.append("initialize_account_not_expected_native_variant")
    if (
        len(token_transfers) != 1
        or token_transfers
        and (
            token_transfers[0].get("source") != account
            or token_transfers[0].get("mint") != WSOL
            or token_transfers[0].get("decimals") != 9
        )
    ):
        reasons.append("native_token_transfer_not_exact_source_shape")
    if pre is not None and pre != 0:
        reasons.append("opening_lamports_nonzero")
    if post is not None and post != 0:
        reasons.append("closing_lamports_nonzero")
    if len(closes) == 1:
        expected = [
            next(
                (e["path"] for e in effects if e["kind"] == "associated_token_create_idempotent"),
                None,
            ),
            creates[0]["path"] if len(creates) == 1 else None,
            immutable[0]["path"] if len(immutable) == 1 else None,
            inits[0]["path"] if len(inits) == 1 else None,
            transfers[0]["path"] if len(transfers) == 1 else None,
            syncs[0]["path"] if len(syncs) == 1 else None,
            target_path,
            token_transfers[0]["path"] if len(token_transfers) == 1 else None,
            closes[0]["path"],
        ]
        if any(path is None for path in expected) or expected != sorted(expected, key=_ordered):
            reasons.append("ordered_lifecycle_not_exact")
    if not isinstance((tx.get("meta") or {}).get("innerInstructions"), list):
        reasons.append("inner_instruction_trace_missing")
    create_lamports = creates[0]["lamports"] if len(creates) == 1 else None
    credited = sum(e["lamports"] for e in transfers if e.get("destination") == account)
    debited = sum(e["lamports"] for e in transfers if e.get("source") == account)
    close_sweep = (
        _close_sweep_candidate(pre, create_lamports, credited, debited, post)
        if not unsupported
        else None
    )
    status = (
        "source_account_flow_exact"
        if not reasons and close_sweep is not None
        else (
            "missing_endpoint"
            if "missing_endpoint" in reasons
            else "unsupported_touch"
            if unsupported
            else "unqualified"
        )
    )
    destination = closes[0].get("destination") if len(closes) == 1 else None
    destination_touches = [
        {"path": ix["path"], "program_id": ix["program_id"], "accounts": ix["accounts"]}
        for ix in all_ix
        if destination and destination in ix["accounts"]
    ]
    destination_state = (
        "destination_ambiguous" if len(destination_touches) > 1 else "destination_touch_trace_only"
    )
    return {
        "account": account,
        "message_account_index": account_index,
        "status": status,
        "reasons": sorted(set(reasons)),
        "pre_lamports": pre,
        "create_lamports": create_lamports,
        "system_credited_lamports": credited,
        "system_debited_lamports": debited,
        "post_lamports": post,
        "close_sweep_lamports_by_account_conservation": close_sweep,
        "lamport_equation": (
            "pre + create + system_credits - system_debits = post + close_sweep_candidate"
        ),
        "token_transfer_amount_atomic_units": token_transfers[0].get("amount_atomic")
        if len(token_transfers) == 1
        else None,
        "token_transfer_decimals": token_transfers[0].get("decimals")
        if len(token_transfers) == 1
        else None,
        "token_transfer_direction": "outbound_from_temporary_account"
        if len(token_transfers) == 1 and token_transfers[0].get("source") == account
        else "unknown",
        "close_destination": destination,
        "close_destination_state": destination_state,
        "close_destination_touching_instruction_trace": destination_touches,
        "account_touching_instruction_trace": trace,
        "unsupported_touch_paths": [e["path"] for e in unsupported],
    }


def _is_sell23(
    tx: dict[str, Any], disc_by_bytes: dict[bytes, str], sell_disc: bytes
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    normalized = normalize_compiled_transaction(tx)
    calls = []
    for ix in [*normalized["outer_instructions"], *normalized["inner_instructions"]]:
        if ix["program_id"] != PROGRAM:
            continue
        data = bytes.fromhex(ix["data_hex"])
        kind = disc_by_bytes.get(data[:8])
        if kind is not None:
            calls.append((ix, kind, data))
    if len(calls) != 1:
        return None
    ix, kind, data = calls[0]
    if (
        kind != "sell"
        or data[:8] != sell_disc
        or len(data) != 24
        or len(ix["accounts"]) != 23
        or ix["accounts"][11] != TOKEN
        or ix["accounts"][12] != TOKEN
    ):
        return None
    return normalized, ix


def run(
    capture_root: Path, output: Path, feasibility_path: Path, baseline_path: Path
) -> dict[str, Any]:
    baseline = _json(baseline_path)
    feasibility = _json(feasibility_path)
    if (
        baseline.get("eligible_successful_sell23_candidates") != 553
        or feasibility.get("counts", {}).get("censored_rows") != 485
    ):
        raise ValueError("frozen_source_denominator_mismatch")
    frozen_paths = [
        "src/memecoin_pumpswap/compiled_economics.py",
        "src/memecoin_pumpswap/economic_v2.py",
        "src/memecoin_pumpswap/legacy_token_ledger.py",
        "src/memecoin_pumpswap/normalize.py",
        "src/memecoin_pumpswap/decoder.py",
        "src/memecoin_pumpswap/schema.py",
        "tools/brain_senses_select_pumpswap_sell_sample.py",
        "tools/brain_senses_census_pumpswap_sell_ledger.py",
    ]
    for path in frozen_paths:
        if baseline.get("code_sha256", {}).get(path) != sha(REPO / path):
            raise ValueError(f"frozen_qualifier_source_mismatch:{path}")
    for path in FRAME_IDS:
        frozen = feasibility.get("frames", {}).get(path)
        epoch = capture_root / path
        receipt_paths = sorted((epoch / "captures").glob("*/receipt.json"))
        hashes = {
            "plan_sha256": sha(epoch / "epoch-plan.json"),
            "summary_sha256": sha(epoch / "epoch-summary.json"),
            "frame_sha256": sha(epoch / "frame.json"),
            "stop_sha256": sha(epoch / "STOP"),
            "receipt_manifest_sha256": hashlib.sha256(
                "\n".join(f"{p.relative_to(REPO)} {sha(p)}" for p in receipt_paths).encode()
            ).hexdigest(),
        }
        if any(frozen.get(k) != v for k, v in hashes.items()):
            raise ValueError(f"c3_frame_binding_mismatch:{path}")
    disc_by_bytes = {
        bytes(row["discriminator"]): name for name, row in SCHEMA["instructions"].items()
    }
    sell_disc = bytes(SCHEMA["instructions"]["sell"]["discriminator"])
    rows: list[dict[str, Any]] = []
    matched = set()
    counters: Counter[str] = Counter()
    frames: dict[str, Any] = {}
    for frame_id in FRAME_IDS:
        epoch = capture_root / frame_id
        frame_counter: Counter[str] = Counter()
        for receipt_path in sorted((epoch / "captures").glob("*/receipt.json")):
            receipt = _json(receipt_path)
            if receipt.get("method") != "getBlock":
                continue
            request = json.loads(receipt["request_json"])
            params = request.get("params")
            opts = params[1] if isinstance(params, list) and len(params) > 1 else None
            if not isinstance(opts, dict) or opts.get("transactionDetails") != "full":
                continue
            block = json.loads(_capture(receipt_path, receipt)).get("result")
            transactions = block.get("transactions") if isinstance(block, dict) else None
            if not isinstance(transactions, list):
                raise ValueError("verified_full_block_missing_transactions")
            frame_counter["receipt_verified_full_blocks"] += 1
            for tx_index, tx in enumerate(transactions):
                candidate = _is_sell23(tx, disc_by_bytes, sell_disc)
                if candidate is None:
                    continue
                normalized, target = candidate
                signatures = normalized.get("transaction_signatures")
                if (
                    not isinstance(signatures, list)
                    or not signatures
                    or not isinstance(signatures[0], str)
                ):
                    raise ValueError("candidate_signature_missing")
                signature = signatures[0]
                sig_hash = hashlib.sha256(signature.encode()).hexdigest()
                if (tx.get("meta") or {}).get("err") is not None:
                    frame_counter["failed_layout_refs_excluded"] += 1
                    continue
                if sig_hash in matched:
                    raise ValueError("duplicate_candidate_across_c3_frames")
                matched.add(sig_hash)
                frame_counter["successful_exact_layout_refs"] += 1
                diagnosis = diagnose_pumpswap_sell_v2(tx)
                ledger_status = diagnosis.get("account_settlement_state")
                frame_counter[f"ledger:{ledger_status}"] += 1
                if ledger_status != "censored_partial":
                    continue
                native = [
                    account
                    for account, fact in diagnosis.get("account_ledger", {})
                    .get("account_facts", {})
                    .items()
                    if fact.get("mint") == WSOL and fact.get("status") == "censored"
                ]
                if len(native) != 1:
                    raise ValueError("censored_sell23_native_account_count_mismatch")
                account_result = classify_account(tx, native[0], target["path"])
                frame_counter[f"native_flow:{account_result['status']}"] += 1
                for reason in account_result["reasons"]:
                    frame_counter[f"reason:{reason}"] += 1
                rows.append(
                    {
                        "frame_id": frame_id,
                        "slot": params[0],
                        "transaction_index": tx_index,
                        "signature_sha256": sig_hash,
                        "target_instruction_path": target["path"],
                        "capture_receipt_path": str(receipt_path.relative_to(REPO)),
                        "capture_receipt_sha256": sha(receipt_path),
                        "raw_response_sha256": receipt.get("response_sha256"),
                        "capture_receipt_ns": receipt.get("receipt_ns"),
                        "ledger_state": ledger_status,
                        "flow": account_result,
                    }
                )
        frames[frame_id] = {"counts": dict(sorted(frame_counter.items()))}
        counters.update(frame_counter)
    if (
        counters["receipt_verified_full_blocks"] != 96
        or counters["successful_exact_layout_refs"] != 553
        or counters["ledger:censored_partial"] != 485
        or len(rows) != 485
    ):
        raise ValueError("independent_c3_denominator_reconciliation_failed")
    status_counts = Counter(row["flow"]["status"] for row in rows)
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "status": "complete_offline_transaction_local_gate",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "tool_sha256": sha(Path(__file__).resolve()),
        "offline_only": True,
        "provider_requests_made": 0,
        "paid_requests_made": 0,
        "c4_modified": False,
        "input_receipts": {
            "baseline_replay": {
                "path": str(baseline_path.relative_to(REPO)),
                "sha256": sha(baseline_path),
            },
            "feasibility_census": {
                "path": str(feasibility_path.relative_to(REPO)),
                "sha256": sha(feasibility_path),
            },
        },
        "frozen_qualifier_code_sha256": {path: sha(REPO / path) for path in frozen_paths},
        "counts": {
            "receipt_verified_full_blocks": counters["receipt_verified_full_blocks"],
            "successful_exact_layout_refs": counters["successful_exact_layout_refs"],
            "exact_ledger_rows_preserved": counters["ledger:exact"],
            "censored_rows_replayed": counters["ledger:censored_partial"],
            "failed_refs_excluded": counters["failed_layout_refs_excluded"],
            "source_account_flow_exact": status_counts["source_account_flow_exact"],
            "unsupported_touch": status_counts["unsupported_touch"],
            "missing_endpoint": status_counts["missing_endpoint"],
            "other_unqualified": status_counts["unqualified"],
            "destination_ambiguous": sum(
                row["flow"]["close_destination_state"] == "destination_ambiguous" for row in rows
            ),
        },
        "semantics": {
            "unit": (
                "lamports for system/create/close; token quantities stay atomic"
            ),
            "creation_lamports_are_historical_rent": False,
            "close_sweep_is_wallet_net_proceeds": False,
            "close_destination_boundary_delta_is_instruction_scoped": False,
            "sync_native_amount_or_rent_reserve_derived": False,
            "economic_reclassification_or_admission": False,
        },
        "frames": frames,
        "rows": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, default=CAPTURE_ROOT)
    parser.add_argument("--feasibility", type=Path, default=FEASIBILITY)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument(
        "--output", type=Path, default=REPO / "work/brain-senses-sell23-c3-native-account-flow.json"
    )
    args = parser.parse_args()
    result = run(args.capture_root, args.output, args.feasibility, args.baseline)
    print(
        json.dumps(
            {"status": result["status"], "counts": result["counts"], "output": str(args.output)}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
