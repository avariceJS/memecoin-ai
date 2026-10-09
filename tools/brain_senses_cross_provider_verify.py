#!/usr/bin/env python3
"""Resumable same-signature Helius vs public RPC response comparison.

The pinned public signature set is the sampling frame. Agreement is conditional on that frame;
provider misses, nulls, and failed attempts are unknown and never retried automatically.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import random
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from memecoin_ai.domain import canonical, digest
from memecoin_ai.sources import Capture, ReadOnlyRPC
from memecoin_pumpswap.decoder import decode, decode58
from memecoin_pumpswap.schema import SCHEMA
from tools.brain_senses_program_watch import _create, _provider_endpoint, _read, _replace

STOP = False
MAX_CREDITS = 1000
TARGET_LABELS = (
    "tx_failed",
    "version_legacy",
    "version_0",
    "version_1",
    "buy",
    "buy_exact_quote_in",
    "sell",
    "token2022",
    "remaining_accounts",
    "known_event_mismatch",
    "no_supported_swap",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _durable_capture(capture: Capture, path: Path) -> None:
    capture.save(path)
    for name in ("response.json", "receipt.json"):
        with (path / name).open("rb") as stream:
            os.fsync(stream.fileno())
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _transaction_statuses(
    public_root: Path, slot_start: int, slot_end: int
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for status_path in sorted((public_root / "transactions").glob("*/status.json")):
        status = _read(status_path)
        slot = status.get("slot")
        if type(slot) is not int or not slot_start <= slot < slot_end:
            continue
        signature_hash = status.get("signature_sha256")
        if (
            not isinstance(signature_hash, str)
            or len(signature_hash) != 64
            or status_path.parent.name != signature_hash
        ):
            raise ValueError("public_transaction_identity_invalid")
        capture_rel = status.get("capture_path")
        if not isinstance(capture_rel, str):
            continue
        capture_path = public_root / capture_rel
        capture = Capture.load(capture_path)
        tx = capture.result()
        if not isinstance(tx, dict) or tx.get("slot") != slot:
            raise ValueError("public_capture_slot_mismatch")
        request = json.loads(capture.request_json)
        signature = request.get("params", [None])[0]
        if (
            not isinstance(signature, str)
            or hashlib.sha256(signature.encode("ascii")).hexdigest() != signature_hash
        ):
            raise ValueError("public_capture_signature_mismatch")
        result.append(
            dict(
                signature=signature,
                signature_sha256=signature_hash,
                slot=slot,
                source_status=status.get("status"),
                capture_path=capture_rel,
                receipt_ns=capture.received_ns,
                block_time_seconds=tx.get("blockTime"),
                raw_response_sha256=digest(capture.response),
                status_path=str(status_path.relative_to(public_root)),
                status_file_sha256=_sha(status_path),
                labels=_labels(tx, status),
            )
        )
    return result


def _labels(tx: dict[str, Any], status: dict[str, Any]) -> list[str]:
    labels: set[str] = set()
    if status.get("status") == "captured_failed_transaction":
        labels.add("tx_failed")
    version = tx.get("version")
    if version == "legacy":
        labels.add("version_legacy")
    elif type(version) is int:
        labels.add(f"version_{version}")
    try:
        instructions = tx["transaction"]["message"]["instructions"]
        for ix in instructions:
            data = ix.get("data")
            if not isinstance(data, str):
                continue
            raw = decode58(data)
            for kind, spec in SCHEMA["instructions"].items():
                if raw[:8] == bytes(spec["discriminator"]):
                    labels.add(kind)
        decoded = decode(tx)
        for swap in decoded.get("swaps", []):
            blockers = swap.get("blockers", [])
            if "token2022_extensions_unqualified" in blockers:
                labels.add("token2022")
            if "unreviewed_remaining_accounts" in blockers:
                labels.add("remaining_accounts")
            if (
                "event_transfer_mismatch" in blockers
                or swap.get("status") == "reconciled_diagnostic_only"
            ):
                labels.add("known_event_mismatch")
        if decoded.get("status") == "no_supported_swap":
            labels.add("no_supported_swap")
    except (KeyError, TypeError, ValueError):
        pass
    return sorted(labels)


def _select(
    candidates: list[dict[str, Any]], seed: int, random_count: int, target_count: int
) -> list[dict[str, Any]]:
    if random_count < 0 or target_count < 0 or random_count + target_count > MAX_CREDITS:
        raise ValueError("selection_budget_invalid")
    ordered = sorted(candidates, key=lambda row: row["signature_sha256"])
    rng = random.Random(seed)
    sample = rng.sample(ordered, min(random_count, len(ordered)))
    selected: dict[str, dict[str, Any]] = {
        row["signature_sha256"]: dict(row, selection_roles=["seeded_random"]) for row in sample
    }
    used: set[str] = set(selected)
    added_targets = 0
    for label in TARGET_LABELS:
        if added_targets >= target_count:
            break
        rows = [
            row for row in ordered if label in row["labels"] and row["signature_sha256"] not in used
        ]
        if not rows:
            continue
        choice = min(rows, key=lambda row: row["signature_sha256"])
        selected[choice["signature_sha256"]] = dict(choice, selection_roles=["targeted:" + label])
        used.add(choice["signature_sha256"])
        added_targets += 1
    return [selected[key] for key in sorted(selected)]


def _build_plan(
    public_root: Path,
    slot_start: int,
    slot_end: int,
    seed: int,
    random_count: int,
    target_count: int,
) -> dict[str, Any]:
    public_state = _read(public_root / "state.json")
    public_binding = public_state.get("binding")
    if (
        not isinstance(public_binding, dict)
        or public_binding.get("source_alias") != "solana-public"
        or public_binding.get("commitment") != "finalized"
        or public_binding.get("coverage_claim") != "provider_returned_address_signatures_only"
        or public_binding.get("address") != SCHEMA["address"]
    ):
        raise ValueError("public_source_not_qualified_for_sampling")
    if (
        type(slot_start) is not int
        or type(slot_end) is not int
        or slot_start < 0
        or slot_end <= slot_start
    ):
        raise ValueError("slot_interval_invalid")
    candidates = _transaction_statuses(public_root, slot_start, slot_end)
    manifest = dict(
        public_source_binding=public_binding,
        process_address=public_binding["address"],
        commitment="finalized",
        slot_interval=[slot_start, slot_end],
        frame_semantics="captured_public_address_index_transactions_only_coverage_unknown",
        candidate_count=len(candidates),
        candidates=candidates,
    )
    selected = _select(candidates, seed, random_count, target_count)
    repo_root = Path(__file__).resolve().parents[1]
    code_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True
    ).stdout.strip()
    role_counts: dict[str, int] = {}
    for row in selected:
        for role in row["selection_roles"]:
            role_counts[role] = role_counts.get(role, 0) + 1
    return dict(
        schema_version=1,
        code_commit=code_commit,
        code_sha256=_sha(Path(__file__)),
        python_version=platform.python_version(),
        public_root_identity=str(public_root.resolve()),
        public_manifest_sha256=digest(canonical(manifest).encode()),
        public_manifest=manifest,
        helius_alias="helius-free",
        helius_request=dict(
            method="getTransaction",
            encoding="jsonParsed",
            commitment="finalized",
            maxSupportedTransactionVersion=1,
            retry_policy="one reserved attempt per selected signature; no automatic retry",
        ),
        comparison="canonical_json_tree_exact_with_missing_null_distinct",
        credit_accounting=(
            "one standard getTransaction request = one published credit; hard request cap"
        ),
        budget=dict(
            hard_credit_cap=MAX_CREDITS,
            selected_count=len(selected),
            random_seed=seed,
            random_requested=random_count,
            targeted_requested=target_count,
            selected_roles=role_counts,
        ),
        selection_roles={row["signature_sha256"]: row["selection_roles"] for row in selected},
        selected=selected,
        coverage="conditional_on_pinned_public_signature_frame; discovery_completeness_unknown",
    )


def _json_differences(left: Any, right: Any, path: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        diffs: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}/{key.replace('~', '~0').replace('/', '~1')}"
            if key not in left:
                diffs.append(child + ":missing_in_public")
            elif key not in right:
                diffs.append(child + ":missing_in_helius")
            else:
                diffs.extend(_json_differences(left[key], right[key], child))
        return diffs
    if isinstance(left, list) and isinstance(right, list):
        diffs = []
        if len(left) != len(right):
            diffs.append(path + ":length")
        for i, (a, b) in enumerate(zip(left, right, strict=False)):
            diffs.extend(_json_differences(a, b, f"{path}/{i}"))
        return diffs
    if type(left) is not type(right) or left != right:
        return [path + ":value"]
    return []


def _verify_public_capture(public_root: Path, selected: dict[str, Any]) -> dict[str, Any]:
    status_path = public_root / selected["status_path"]
    if _sha(status_path) != selected["status_file_sha256"]:
        raise ValueError("pinned_public_status_changed")
    capture_path = public_root / selected["capture_path"]
    capture = Capture.load(capture_path)
    if digest(capture.response) != selected["raw_response_sha256"]:
        raise ValueError("pinned_public_capture_changed")
    tx = capture.result()
    if not isinstance(tx, dict) or tx.get("slot") != selected["slot"]:
        raise ValueError("pinned_public_transaction_identity_changed")
    request = json.loads(capture.request_json)
    signature = request.get("params", [None])[0]
    if signature != selected["signature"]:
        raise ValueError("pinned_public_signature_changed")
    return tx


def _attempt(
    root: Path,
    public_root: Path,
    rpc: ReadOnlyRPC,
    seq: int,
    selected: dict[str, Any],
) -> dict[str, Any]:
    signature = selected["signature"]
    params = [
        signature,
        {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 1},
    ]
    request_obj = dict(jsonrpc="2.0", id=1, method="getTransaction", params=params)
    request_body = canonical(request_obj).encode()
    try:
        public_tx = _verify_public_capture(public_root, selected)
    except Exception as exc:
        raise ValueError("pinned_public_capture_invalid:" + type(exc).__name__) from exc
    _create(
        root / "requests" / f"{seq:012d}.json",
        dict(
            sequence=seq,
            signature_sha256=selected["signature_sha256"],
            request_sha256=hashlib.sha256(request_body).hexdigest(),
            params_sha256=hashlib.sha256(canonical(params).encode()).hexdigest(),
            reserved_ns=time.time_ns(),
            credit_reservation=1,
            outcome="reserved_before_io",
            policy="never_retry_or_replace_selected_signature",
        ),
    )
    capture: Capture | None = None
    error_class: str | None = None
    try:
        capture = rpc.fetch("getTransaction", params)
    except Exception as exc:
        error_class = type(exc).__name__
    row: dict[str, Any] = dict(
        sequence=seq,
        signature_sha256=selected["signature_sha256"],
        status="request_failed" if capture is None else "captured",
        error_class=error_class,
        capture_path=None,
        raw_response_sha256=None,
        response_bytes=0,
        request_started_ns=capture.request_started_ns if capture is not None else None,
        receipt_ns=capture.received_ns if capture is not None else time.time_ns(),
    )
    if capture is not None:
        capture_rel = f"captures/{seq:012d}"
        _durable_capture(capture, root / capture_rel)
        row["capture_path"] = capture_rel
        row["raw_response_sha256"] = digest(capture.response)
        row["response_bytes"] = len(capture.response)
        try:
            helius_tx = capture.result()
            row["helius_status"] = "provider_null" if helius_tx is None else "captured"
            row["helius_slot"] = helius_tx.get("slot") if isinstance(helius_tx, dict) else None
            differences = _json_differences(public_tx, helius_tx)
            row["comparison_status"] = (
                "provider_null"
                if helius_tx is None
                else "field_equivalent"
                if not differences
                else "field_differences"
            )
            row["difference_paths"] = differences[:500]
            row["difference_path_count"] = len(differences)
            row["public_slot"] = public_tx.get("slot")
            row["public_event_time_seconds"] = public_tx.get("blockTime")
            row["helius_event_time_seconds"] = (
                helius_tx.get("blockTime") if isinstance(helius_tx, dict) else None
            )
            if row["public_slot"] != row["helius_slot"] and helius_tx is not None:
                row["comparison_status"] = "identity_mismatch"
        except Exception as exc:
            row["comparison_status"] = "response_parse_or_compare_error"
            row["comparison_error_class"] = type(exc).__name__
    _create(root / "request-results" / f"{seq:012d}.json", row)
    return row


def _recover(root: Path, plan: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    reservations = sorted((root / "requests").glob("*.json"))
    results = sorted((root / "request-results").glob("*.json"))
    reservation_names = {path.name for path in reservations}
    result_names = {path.name for path in results}
    if result_names - reservation_names:
        raise ValueError("result_without_request_reservation")
    if reservation_names - result_names:
        seq = min(int(name[:-5]) for name in reservation_names - result_names)
        _create(
            root / "STOP",
            dict(
                reason="ambiguous_reserved_request_requires_manual_reconciliation",
                sequence=seq,
                time_ns=time.time_ns(),
            ),
        )
        raise ValueError("ambiguous_reserved_request_requires_manual_reconciliation")
    if len(results) > len(plan["selected"]):
        raise ValueError("attempt_count_exceeds_pinned_selection")
    for index, result_path in enumerate(results, start=1):
        if result_path.name != f"{index:012d}.json":
            raise ValueError("non_contiguous_request_history")
        result = _read(result_path)
        reservation = _read(root / "requests" / result_path.name)
        selected = plan["selected"][index - 1]
        if (
            reservation.get("sequence") != index
            or reservation.get("signature_sha256") != selected["signature_sha256"]
            or result.get("sequence") != index
            or result.get("signature_sha256") != selected["signature_sha256"]
        ):
            raise ValueError("request_history_identity_mismatch")
    return dict(
        plan_sha256=_sha(plan_path),
        next_sequence=len(results) + 1,
        completed=len(results),
        updated_ns=time.time_ns(),
    )


def run_once(
    public_root: Path,
    root: Path,
    *,
    slot_start: int,
    slot_end: int,
    seed: int = 20261009,
    random_count: int = 800,
    target_count: int = 200,
    pace_seconds: float = 0.25,
    rpc_client: ReadOnlyRPC | Any | None = None,
) -> dict[str, Any]:
    if not 0.25 <= pace_seconds <= 10:
        raise ValueError("pace_seconds_outside_bound")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    for name in ("requests", "request-results", "captures", "observations", "events"):
        (root / name).mkdir(exist_ok=True, mode=0o700)
        os.chmod(root / name, 0o700)
    plan_path = root / "plan.json"
    if plan_path.exists():
        plan = _read(plan_path)
        if plan.get("public_root_identity") != str(public_root.resolve()):
            raise ValueError("paired_verification_public_root_changed")
        if plan.get("code_sha256") != _sha(Path(__file__)):
            raise ValueError("paired_verification_code_changed")
        if (
            plan.get("helius_alias") != "helius-free"
            or plan.get("budget", {}).get("hard_credit_cap") != MAX_CREDITS
        ):
            raise ValueError("paired_verification_plan_invalid")
        if (
            plan.get("public_manifest", {}).get("slot_interval") != [slot_start, slot_end]
            or plan.get("budget", {}).get("random_seed") != seed
            or plan.get("budget", {}).get("random_requested") != random_count
            or plan.get("budget", {}).get("targeted_requested") != target_count
        ):
            raise ValueError("paired_verification_arguments_changed")
    else:
        plan = _build_plan(public_root, slot_start, slot_end, seed, random_count, target_count)
        _create(plan_path, plan)
    state_path = root / "state.json"
    if state_path.exists():
        old_state = _read(state_path)
        if old_state.get("plan_sha256") != _sha(plan_path):
            raise ValueError("paired_verification_state_plan_mismatch")
    if (root / "STOP").exists():
        raise ValueError("verification_stopped")
    state = _recover(root, plan, plan_path)
    _replace(state_path, state)

    own_client = rpc_client is None
    if own_client:
        rpc_endpoint, alias = _provider_endpoint("helius-free")
        client: Any = ReadOnlyRPC(
            rpc_endpoint, alias, timeout=20, max_bytes=8_000_000, keep_alive=True
        )
    else:
        client = rpc_client
    outcomes: dict[str, int] = {}
    last_request_ns = 0
    try:
        selected = plan["selected"]
        while state["completed"] < len(selected):
            if state["next_sequence"] > MAX_CREDITS:
                _create(root / "STOP", dict(reason="hard_credit_cap", time_ns=time.time_ns()))
                break
            item = selected[state["completed"]]
            wait = pace_seconds - (time.time_ns() - last_request_ns) / 1_000_000_000
            if wait > 0:
                time.sleep(wait)
            result = _attempt(root, public_root, client, state["next_sequence"], item)
            last_request_ns = time.time_ns()
            result_status = result.get("comparison_status") or result["status"]
            outcomes[result_status] = outcomes.get(result_status, 0) + 1
            state = _recover(root, plan, plan_path)
            _replace(state_path, state)
            if STOP:
                break
    finally:
        if own_client:
            client.close()
    history: dict[str, int] = {}
    results = [_read(path) for path in sorted((root / "request-results").glob("*.json"))]
    for row in results:
        status = row.get("comparison_status") or row["status"]
        history[status] = history.get(status, 0) + 1
    summary = dict(
        plan_sha256=_sha(plan_path),
        updated_ns=time.time_ns(),
        selected_count=len(plan["selected"]),
        completed=state["completed"],
        attempted_request_count=len(list((root / "requests").glob("*.json"))),
        reserved_credit_count=len(list((root / "requests").glob("*.json"))),
        hard_credit_cap=MAX_CREDITS,
        request_credit_assumption="one credit per standard getTransaction attempt; see pinned plan",
        outcomes=history,
        newly_completed=outcomes,
        frame=plan["public_manifest"]["frame_semantics"],
        coverage=(
            "conditional_on_pinned_public_signature_frame; provider discovery completeness unknown"
        ),
    )
    _replace(root / "summary.json", summary)
    return summary


def _signal(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--slot-start", type=int, required=True)
    parser.add_argument("--slot-end", type=int, required=True, help="exclusive upper bound")
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--random-count", type=int, default=800)
    parser.add_argument("--target-count", type=int, default=200)
    parser.add_argument("--pace-seconds", type=float, default=0.25)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.root, 0o700)
    with (args.root / ".verify.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("verification already active", file=sys.stderr)
            return 2
        signal.signal(signal.SIGTERM, _signal)
        signal.signal(signal.SIGINT, _signal)
        try:
            print(
                canonical(
                    run_once(
                        args.public_root,
                        args.root,
                        slot_start=args.slot_start,
                        slot_end=args.slot_end,
                        seed=args.seed,
                        random_count=args.random_count,
                        target_count=args.target_count,
                        pace_seconds=args.pace_seconds,
                    )
                ),
                flush=True,
            )
        except Exception as exc:
            if not (args.root / "STOP").exists():
                _create(
                    args.root / "STOP",
                    dict(
                        reason="runtime_exception",
                        error_class=type(exc).__name__,
                        time_ns=time.time_ns(),
                    ),
                )
            print(canonical(dict(status="failed", error_class=type(exc).__name__)), flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
