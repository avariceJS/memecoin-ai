#!/usr/bin/env python3
"""Durable same-slot Helius/public finalized block inventory pilot.

The pilot compares provider-returned slot sets, signatures-only blocks, and full blocks. A block
is eligible for process completeness only when a full response was captured and structurally
parsed; provider errors, null blocks, and unsupported transaction shapes remain unknown.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from memecoin_ai.domain import canonical, digest
from memecoin_ai.sources import Capture, ReadOnlyRPC
from memecoin_pumpswap.schema import SCHEMA

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.brain_senses_program_watch import (  # noqa: E402
    _create,
    _provider_endpoint,
    _read,
    _replace,
)

STOP = False
PROVIDERS = ("helius-free", "solana-public")
DETAILS = ("signatures", "full")
MAX_REQUESTS_PER_PROVIDER = 1000
MAX_RESPONSE_BYTES = 32_000_000
PUMP_PROGRAM = SCHEMA["address"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_ranges(values: list[str]) -> list[list[int]]:
    ranges: list[list[int]] = []
    for value in values:
        start_text, separator, end_text = value.partition(":")
        if not separator:
            raise ValueError("slot_range_must_be_start_colon_exclusive_end")
        start, end = int(start_text), int(end_text)
        if start < 1 or end <= start or end - start > 256:
            raise ValueError("slot_range_outside_pilot_bound")
        ranges.append([start, end])
    if not ranges or len(ranges) > 8:
        raise ValueError("pilot_requires_1_to_8_slot_ranges")
    if len({tuple(pair) for pair in ranges}) != len(ranges):
        raise ValueError("duplicate_slot_range")
    return ranges


def _build_operations(slot_ranges: list[list[int]]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for provider in PROVIDERS:
        for start, end in slot_ranges:
            operations.append(
                dict(
                    provider=provider,
                    method="getBlocks",
                    params=[start, end - 1, {"commitment": "finalized"}],
                    slot_range=[start, end],
                    detail="enumeration",
                )
            )
        for start, end in slot_ranges:
            for slot in range(start, end):
                for detail in DETAILS:
                    options: dict[str, Any] = {
                        "commitment": "finalized",
                        "encoding": "json",
                        "transactionDetails": detail,
                        "rewards": False,
                    }
                    if detail == "full":
                        options["maxSupportedTransactionVersion"] = 1
                    operations.append(
                        dict(
                            provider=provider,
                            method="getBlock",
                            params=[slot, options],
                            slot=slot,
                            detail=detail,
                        )
                    )
    return operations


def _plan(slot_ranges: list[list[int]], request_cap: int, helius_pace: float) -> dict[str, Any]:
    if not 1 <= request_cap <= MAX_REQUESTS_PER_PROVIDER:
        raise ValueError("request_cap_outside_hard_bound")
    if not 0.25 <= helius_pace <= 10:
        raise ValueError("helius_pace_outside_bound")
    operations = _build_operations(slot_ranges)
    per_provider = {
        provider: sum(op["provider"] == provider for op in operations) for provider in PROVIDERS
    }
    if any(value > request_cap for value in per_provider.values()):
        raise ValueError("planned_requests_exceed_provider_cap")
    repo_root = Path(__file__).resolve().parents[1]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return dict(
        schema_version=1,
        code_commit=commit,
        code_sha256=_sha(Path(__file__)),
        python_version=platform.python_version(),
        providers=list(PROVIDERS),
        slot_ranges=slot_ranges,
        request_caps={provider: request_cap for provider in PROVIDERS},
        expected_requests=per_provider,
        helius_pace_seconds=helius_pace,
        public_shared_rate_ledger_env="MEMECOIN_SOLANA_PUBLIC_RATE_LEDGER",
        max_response_bytes=MAX_RESPONSE_BYTES,
        commitment="finalized",
        enumerate_method="getBlocks inclusive slot ranges",
        block_methods={
            "signatures": "getBlock transactionDetails=signatures",
            "full": "getBlock transactionDetails=full",
        },
        retry_policy="one durable reservation per method/provider/slot; no automatic retry",
        credit_assumption={
            "helius": (
                "published standard getBlock/getBlocks cost is one credit per request; "
                "no live meter exposed"
            ),
            "public": "unmetered public RPC request count; shared 1 request/second start gate",
        },
        operations=operations,
        scope="provider-returned finalized slots only; no global ledger completeness claim",
    )


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


def _result_for(
    root: Path, sequence: int, operation: dict[str, Any], client: Any
) -> dict[str, Any]:
    request = dict(jsonrpc="2.0", id=1, method=operation["method"], params=operation["params"])
    body = canonical(request).encode()
    reservation_path = root / "requests" / f"{sequence:012d}.json"
    _create(
        reservation_path,
        dict(
            sequence=sequence,
            provider=operation["provider"],
            method=operation["method"],
            slot=operation.get("slot"),
            slot_range=operation.get("slot_range"),
            detail=operation["detail"],
            request_sha256=hashlib.sha256(body).hexdigest(),
            params_sha256=hashlib.sha256(canonical(operation["params"]).encode()).hexdigest(),
            reserved_ns=time.time_ns(),
            request_reservation=1,
            credit_reservation=1 if operation["provider"] == "helius-free" else 0,
            policy="no automatic retry; ambiguous request stays unresolved",
        ),
    )
    try:
        capture = client.fetch(operation["method"], operation["params"])
        rel = f"captures/{operation['provider']}/{sequence:012d}"
        _durable_capture(capture, root / rel)
        row: dict[str, Any] = dict(
            sequence=sequence,
            provider=operation["provider"],
            method=operation["method"],
            slot=operation.get("slot"),
            slot_range=operation.get("slot_range"),
            detail=operation["detail"],
            status="captured",
            capture_path=rel,
            response_sha256=digest(capture.response),
            response_bytes=len(capture.response),
            request_started_ns=capture.request_started_ns,
            receipt_ns=capture.received_ns,
        )
        try:
            value = capture.result()
            row["result_status"] = "provider_null" if value is None else "parsed"
            if operation["method"] == "getBlocks":
                row["returned_slot_count"] = len(value) if isinstance(value, list) else None
                row["returned_slots_sha256"] = (
                    digest(canonical(value).encode()) if isinstance(value, list) else None
                )
            elif isinstance(value, dict):
                txs = value.get("transactions")
                row["blockhash"] = value.get("blockhash")
                row["parent_slot"] = value.get("parentSlot")
                row["block_time_seconds"] = value.get("blockTime")
                row["transaction_count"] = len(txs) if isinstance(txs, list) else None
                if operation["detail"] == "signatures":
                    signature_rows = value.get("signatures")
                    row["signature_count"] = (
                        len(signature_rows) if isinstance(signature_rows, list) else None
                    )
            else:
                row["result_status"] = "result_shape_unknown"
        except Exception as exc:
            row["result_status"] = "response_error"
            row["result_error_class"] = type(exc).__name__
    except Exception as exc:
        row = dict(
            sequence=sequence,
            provider=operation["provider"],
            method=operation["method"],
            slot=operation.get("slot"),
            slot_range=operation.get("slot_range"),
            detail=operation["detail"],
            status="request_failed",
            error_class=type(exc).__name__,
            receipt_ns=time.time_ns(),
        )
    _create(root / "request-results" / f"{sequence:012d}.json", row)
    return row


def _recover(root: Path, plan: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    reservations = sorted((root / "requests").glob("*.json"))
    results = sorted((root / "request-results").glob("*.json"))
    reservation_names = {path.name for path in reservations}
    result_names = {path.name for path in results}
    if result_names - reservation_names:
        raise ValueError("block_result_without_reservation")
    if reservation_names - result_names:
        seq = min(int(name[:-5]) for name in reservation_names - result_names)
        _create(
            root / "STOP",
            dict(
                reason="ambiguous_block_request_requires_manual_reconciliation",
                sequence=seq,
                time_ns=time.time_ns(),
            ),
        )
        raise ValueError("ambiguous_block_request_requires_manual_reconciliation")
    if len(results) > len(plan["operations"]):
        raise ValueError("block_attempt_count_exceeds_plan")
    for index, path in enumerate(results, start=1):
        if path.name != f"{index:012d}.json":
            raise ValueError("block_request_history_non_contiguous")
        result = _read(path)
        reservation = _read(root / "requests" / path.name)
        operation = plan["operations"][index - 1]
        if (
            result.get("sequence") != index
            or reservation.get("sequence") != index
            or result.get("provider") != operation["provider"]
            or reservation.get("provider") != operation["provider"]
            or result.get("method") != operation["method"]
            or reservation.get("method") != operation["method"]
            or result.get("slot") != operation.get("slot")
            or result.get("slot_range") != operation.get("slot_range")
            or result.get("detail") != operation["detail"]
        ):
            raise ValueError("block_request_history_identity_mismatch")
    return dict(
        plan_sha256=_sha(plan_path),
        completed=len(results),
        next_sequence=len(results) + 1,
        updated_ns=time.time_ns(),
    )


def _block_result(root: Path, row: dict[str, Any]) -> Any:
    if row.get("status") != "captured" or row.get("result_status") != "parsed":
        return None
    cap = Capture.load(root / row["capture_path"])
    return cap.result()


def _transaction_keys(tx: dict[str, Any]) -> list[str | None]:
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = message.get("accountKeys") or []
    resolved: list[str | None] = []
    for value in keys:
        resolved.append(
            value.get("pubkey")
            if isinstance(value, dict)
            else value
            if isinstance(value, str)
            else None
        )
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    for value in loaded.get("writable") or []:
        resolved.append(value if isinstance(value, str) else None)
    for value in loaded.get("readonly") or []:
        resolved.append(value if isinstance(value, str) else None)
    return resolved


def _pump_invocations(block: dict[str, Any]) -> dict[str, Any]:
    total = success = failed = unresolved = transactions = 0
    for entry in block.get("transactions") or []:
        tx = entry.get("transaction") if isinstance(entry, dict) else None
        message = (tx or {}).get("message") or {}
        keys = _transaction_keys(entry)
        meta = entry.get("meta") or {}
        refs = 0
        instructions = message.get("instructions") or []
        all_instructions = list(instructions)
        for inner in meta.get("innerInstructions") or []:
            all_instructions.extend(inner.get("instructions") or [])
        for ix in all_instructions:
            if not isinstance(ix, dict):
                unresolved += 1
                continue
            program = ix.get("programId")
            if program is None:
                index = ix.get("programIdIndex")
                if type(index) is int and 0 <= index < len(keys):
                    program = keys[index]
                else:
                    unresolved += 1
            if program == PUMP_PROGRAM:
                refs += 1
        total += refs
        if refs:
            transactions += 1
            if meta.get("err") is None:
                success += refs
            else:
                failed += refs
    return dict(
        structural_reference_count=total,
        transactions_with_reference=transactions,
        references_in_successful_transactions=success,
        references_in_failed_transactions=failed,
        unresolved_instruction_shapes=unresolved,
        economic_trade_count=None,
        exact_market_activity_claim=False,
    )


def _signature_list(value: Any, detail: str) -> list[str] | None:
    if not isinstance(value, dict):
        return None
    if detail == "signatures":
        signatures = value.get("signatures")
        if not isinstance(signatures, list) or any(not isinstance(sig, str) for sig in signatures):
            return None
        return signatures
    if not isinstance(value.get("transactions"), list):
        return None
    signatures: list[str] = []
    for item in value["transactions"]:
        tx = item.get("transaction") if isinstance(item, dict) else None
        values = (tx or {}).get("signatures") or []
        signature = values[0] if values and isinstance(values[0], str) else None
        if signature is None:
            return None
        signatures.append(signature)
    return signatures


def _compare(root: Path, plan: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    by_key = {
        (row["provider"], row["method"], row.get("slot"), row["detail"]): row for row in results
    }
    windows: list[dict[str, Any]] = []
    for start, end in plan["slot_ranges"]:
        enumeration: dict[str, set[int] | None] = {}
        for provider in PROVIDERS:
            row = next(
                (
                    r
                    for r in results
                    if r.get("provider") == provider
                    and r.get("method") == "getBlocks"
                    and r.get("slot_range") == [start, end]
                ),
                None,
            )
            value = _block_result(root, row) if row else None
            if not isinstance(value, list) or any(type(slot) is not int for slot in value):
                enumeration[provider] = None
            else:
                enumeration[provider] = set(value)
        slot_rows: list[dict[str, Any]] = []
        for slot in range(start, end):
            per_provider: dict[str, dict[str, Any]] = {}
            for provider in PROVIDERS:
                full_row = by_key.get((provider, "getBlock", slot, "full"))
                sig_row = by_key.get((provider, "getBlock", slot, "signatures"))
                full = _block_result(root, full_row) if full_row else None
                sigs = _block_result(root, sig_row) if sig_row else None
                full_signatures = _signature_list(full, "full") if isinstance(full, dict) else None
                signature_only = (
                    _signature_list(sigs, "signatures") if isinstance(sigs, dict) else None
                )
                per_provider[provider] = dict(
                    full_status=full_row.get("result_status") if full_row else "not_run",
                    signatures_status=sig_row.get("result_status") if sig_row else "not_run",
                    enumeration_contains_slot=(slot in enumeration[provider])
                    if enumeration[provider] is not None
                    else None,
                    full_transaction_count=len(full.get("transactions", []))
                    if isinstance(full, dict) and isinstance(full.get("transactions"), list)
                    else None,
                    signatures_count=len(signature_only) if signature_only is not None else None,
                    full_signature_count=len(full_signatures)
                    if full_signatures is not None
                    else None,
                    signatures_only_list_sha256=(
                        digest(canonical(signature_only).encode())
                        if signature_only is not None
                        else None
                    ),
                    full_signature_list_sha256=(
                        digest(canonical(full_signatures).encode())
                        if full_signatures is not None
                        else None
                    ),
                    full_matches_signatures_only=(
                        full_signatures == signature_only
                        if full_signatures is not None and signature_only is not None
                        else None
                    ),
                    blockhash=full.get("blockhash") if isinstance(full, dict) else None,
                    parent_slot=full.get("parentSlot") if isinstance(full, dict) else None,
                    previous_blockhash=full.get("previousBlockhash")
                    if isinstance(full, dict)
                    else None,
                    program_references=_pump_invocations(full) if isinstance(full, dict) else None,
                    full_response_bytes=full_row.get("response_bytes") if full_row else None,
                    signatures_response_bytes=sig_row.get("response_bytes") if sig_row else None,
                )
            a, b = (per_provider[provider] for provider in PROVIDERS)
            both_full = (
                a["full_signature_count"] is not None and b["full_signature_count"] is not None
            )
            signature_equivalent = (
                both_full and a["full_signature_count"] == b["full_signature_count"]
            )
            signature_order_equal = (
                both_full
                and a["full_signature_list_sha256"] is not None
                and a["full_signature_list_sha256"] == b["full_signature_list_sha256"]
            )
            # Full transaction list identities are compared from raw responses below.
            block_identity_equivalent = (
                both_full
                and a["blockhash"] == b["blockhash"]
                and a["parent_slot"] == b["parent_slot"]
                and a["previous_blockhash"] == b["previous_blockhash"]
            )
            slot_rows.append(
                dict(
                    slot=slot,
                    providers=per_provider,
                    cross_provider_signature_count_equal=signature_equivalent,
                    cross_provider_signature_order_equal=signature_order_equal,
                    cross_provider_block_identity_equal=block_identity_equivalent,
                    comparison_status=(
                        "paired_full_blocks" if both_full else "unknown_unpaired_or_incomplete"
                    ),
                )
            )
        left = enumeration[PROVIDERS[0]]
        right = enumeration[PROVIDERS[1]]
        windows.append(
            dict(
                slot_range=[start, end],
                provider_enumerations={
                    provider: (
                        None if enumeration[provider] is None else sorted(enumeration[provider])
                    )
                    for provider in PROVIDERS
                },
                enumeration_equal=left == right if left is not None and right is not None else None,
                slots_compared=slot_rows,
            )
        )
    return dict(windows=windows)


def run_once(
    root: Path,
    *,
    slot_ranges: list[list[int]],
    request_cap: int = 1000,
    helius_pace: float = 0.25,
    clients: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = _plan(slot_ranges, request_cap, helius_pace)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    for name in ("requests", "request-results", "captures", "observations", "events"):
        (root / name).mkdir(exist_ok=True, mode=0o700)
        os.chmod(root / name, 0o700)
    plan_path = root / "plan.json"
    if plan_path.exists():
        existing = _read(plan_path)
        if existing != plan:
            raise ValueError("block_pilot_plan_changed")
        plan = existing
    else:
        _create(plan_path, plan)
    if (root / "STOP").exists():
        raise ValueError("block_pilot_stopped")
    state = _recover(root, plan, plan_path)
    state_path = root / "state.json"
    if state_path.exists() and _read(state_path).get("plan_sha256") != _sha(plan_path):
        raise ValueError("block_pilot_state_plan_mismatch")
    _replace(state_path, state)
    own_clients = clients is None
    if own_clients:
        clients = {}
        for provider in PROVIDERS:
            endpoint, alias = _provider_endpoint(provider)
            clients[provider] = ReadOnlyRPC(
                endpoint, alias, timeout=45, max_bytes=MAX_RESPONSE_BYTES, keep_alive=True
            )
    last_attempt: dict[str, int] = {provider: 0 for provider in PROVIDERS}
    outcomes: dict[str, int] = {}
    try:
        operations = plan["operations"]
        while state["completed"] < len(operations):
            seq = state["next_sequence"]
            operation = operations[state["completed"]]
            if seq > request_cap * len(PROVIDERS):
                _create(root / "STOP", dict(reason="aggregate_request_cap", time_ns=time.time_ns()))
                break
            if STOP:
                break
            pace = helius_pace if operation["provider"] == "helius-free" else 0.25
            wait = pace - (time.time_ns() - last_attempt[operation["provider"]]) / 1_000_000_000
            if wait > 0:
                time.sleep(wait)
            row = _result_for(root, seq, operation, clients[operation["provider"]])
            last_attempt[operation["provider"]] = time.time_ns()
            status = row.get("result_status") or row["status"]
            outcomes[status] = outcomes.get(status, 0) + 1
            state = _recover(root, plan, plan_path)
            _replace(state_path, state)
        results = [_read(path) for path in sorted((root / "request-results").glob("*.json"))]
        comparison = _compare(root, plan, results)
        summary = dict(
            plan_sha256=_sha(plan_path),
            updated_ns=time.time_ns(),
            operation_count=len(operations),
            completed=state["completed"],
            attempts=len(list((root / "requests").glob("*.json"))),
            helius_credit_reservations=sum(
                _read(path).get("credit_reservation", 0)
                for path in (root / "requests").glob("*.json")
            ),
            outcomes={
                key: sum(
                    1 for row in results if (row.get("result_status") or row.get("status")) == key
                )
                for key in sorted(
                    {row.get("result_status") or row.get("status") for row in results}
                )
            },
            newly_completed=outcomes,
            comparison=comparison,
            claims=(
                "bounded provider-returned block inventories; no global ledger or "
                "economic-completeness claim"
            ),
        )
        _replace(root / "summary.json", summary)
        return summary
    finally:
        if own_clients and clients:
            for client in clients.values():
                client.close()


def _signal(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--slot-range", action="append", required=True, metavar="START:END")
    parser.add_argument("--request-cap-per-provider", type=int, default=1000)
    parser.add_argument("--helius-pace-seconds", type=float, default=0.25)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.root, 0o700)
    with (args.root / ".pilot.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("block pilot already active", file=sys.stderr)
            return 2
        signal.signal(signal.SIGTERM, _signal)
        signal.signal(signal.SIGINT, _signal)
        try:
            summary = run_once(
                args.root,
                slot_ranges=_parse_ranges(args.slot_range),
                request_cap=args.request_cap_per_provider,
                helius_pace=args.helius_pace_seconds,
            )
            print(
                canonical(
                    {
                        key: summary[key]
                        for key in (
                            "plan_sha256",
                            "operation_count",
                            "completed",
                            "attempts",
                            "helius_credit_reservations",
                            "outcomes",
                            "claims",
                        )
                    }
                ),
                flush=True,
            )
            return 0
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


if __name__ == "__main__":
    raise SystemExit(main())
