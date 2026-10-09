#!/usr/bin/env python3
"""Durable, read-only prospective capture of a Solana program's finalized transactions.

This records provider-returned address-index observations and full transaction bodies. It does
not decode or assert that the provider returned every transaction, and it grants no market-process
completeness or economic-feature eligibility.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import signal
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from memecoin_ai.domain import canonical, digest
from memecoin_ai.sources import Capture, ReadOnlyRPC
from memecoin_pumpswap.schema import SCHEMA

NS = 1_000_000_000
STOP = False


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _create(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(canonical(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _sync_dir(path.parent)


def _replace(path: Path, row: dict[str, Any]) -> None:
    temp = path.with_name("." + path.name + ".tmp")
    with temp.open("x") as stream:
        stream.write(canonical(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    _sync_dir(path.parent)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("invalid_object")
    return value


def _signature_key(signature: str) -> str:
    return hashlib.sha256(signature.encode("ascii")).hexdigest()


def _provider_endpoint(provider: str) -> tuple[str, str]:
    if provider == "solana-public":
        return "https://api.mainnet-beta.solana.com", provider
    env_name = {
        "alchemy-free": "MEMECOIN_ALCHEMY_FREE_HTTP_URL",
        "helius-free": "MEMECOIN_HELIUS_RPC_URL",
    }.get(provider)
    if env_name is None:
        raise ValueError("unsupported_provider")
    value = os.environ.get(env_name)
    if not value:
        for line in Path(".env.local").read_text().splitlines():
            key, separator, candidate = line.partition("=")
            if separator and key.strip() == env_name:
                value = candidate.strip().strip("\"'")
                break
    if not value:
        raise ValueError("provider_endpoint_unavailable")
    parsed = urlsplit(value)
    valid_host = (
        parsed.hostname == "solana-mainnet.g.alchemy.com"
        if provider == "alchemy-free"
        else bool(
            parsed.hostname
            and (parsed.hostname.endswith(".helius-rpc.com") or parsed.hostname == "rpc.helius.xyz")
        )
    )
    if parsed.scheme != "https" or not valid_host or parsed.username or parsed.password:
        raise ValueError("provider_endpoint_invalid")
    return value, provider


def _record_source_epoch(root: Path, provider: str, parent_root: Path | None) -> dict[str, Any]:
    """Pin a fresh address-index observation without inheriting an old cursor."""
    path = root / "source-epoch.json"
    if path.exists():
        return _read(path)
    if any((root / "requests").glob("*.json")):
        raise ValueError("source_epoch_missing_for_existing_requests")
    parent_stop = parent_root / "STOP" if parent_root is not None else None
    parent_results = parent_root / "request-results" if parent_root is not None else None
    previous_receipts = []
    if parent_results is not None and parent_results.exists():
        for result_path in parent_results.glob("*.json"):
            result = _read(result_path)
            receipt_ns = result.get("receipt_ns") or result.get("received_ns")
            if isinstance(receipt_ns, int):
                previous_receipts.append(receipt_ns)
    manifest = {
        "schema_version": 1,
        "epoch_id": root.name,
        "provider": provider,
        "created_ns": time.time_ns(),
        "parent_epoch_root": str(parent_root) if parent_root else None,
        "parent_stop": _read(parent_stop) if parent_stop and parent_stop.exists() else None,
        "parent_last_receipt_ns": max(previous_receipts, default=None),
        "cursor_inheritance": "none",
        "first_query_policy": (
            "fresh current-head observation with no before/until cursor; "
            "overlap is recorded and old gaps remain unknown"
        ),
        "completeness": "unknown",
    }
    _create(path, manifest)
    return manifest


def _reserve(root: Path, seq: int, method: str, params: list[Any], scheduled_ns: int) -> Path:
    request = dict(jsonrpc="2.0", id=1, method=method, params=params)
    body = canonical(request).encode()
    path = root / "requests" / f"{seq:012d}.json"
    _create(
        path,
        dict(
            schema_version=1,
            sequence=seq,
            scheduled_ns=scheduled_ns,
            request_started_ns=time.time_ns(),
            method=method,
            request_sha256=hashlib.sha256(body).hexdigest(),
            params_sha256=hashlib.sha256(canonical(params).encode()).hexdigest(),
            policy="one durable reservation per attempt; no automatic retry",
            outcome="reserved_before_io",
        ),
    )
    return path


def _finish(
    path: Path, status: str, *, capture: Capture | None = None, error: str | None = None
) -> None:
    stem = path.stem
    root = path.parent.parent
    result: dict[str, Any] = dict(
        sequence=int(stem), status=status, received_ns=time.time_ns(), error_class=error
    )
    if capture is not None:
        capture_root = root / "captures" / stem
        capture.save(capture_root)
        result.update(
            capture_path=str(capture_root.relative_to(root)),
            response_sha256=digest(capture.response),
            response_bytes=len(capture.response),
            request_started_ns=capture.request_started_ns,
            received_ns=capture.received_ns,
        )
    _create(root / "request-results" / f"{stem}.json", result)


def _request(
    root: Path,
    client: ReadOnlyRPC,
    seq: int,
    method: str,
    params: list[Any],
    scheduled_ns: int,
    pace_seconds: float,
    last_request_ns: int,
    daily_count: list[int],
) -> tuple[Capture | None, int]:
    wait = pace_seconds - (time.time_ns() - last_request_ns) / NS
    if wait > 0:
        time.sleep(wait)
    daily_count[0] += 1
    daily_path = root / "daily-accounting" / (time.strftime("%Y-%m-%d", time.gmtime()) + ".json")
    _replace(daily_path, dict(utc_date=daily_path.stem, reserved_requests=daily_count[0]))
    reservation = _reserve(root, seq, method, params, scheduled_ns)
    try:
        capture = client.fetch(method, params)
        # Seal response bytes before parsing any RPC result.
        _finish(reservation, "captured", capture=capture)
        return capture, capture.request_started_ns
    except Exception as exc:
        _finish(reservation, "failed", error=type(exc).__name__)
        return None, time.time_ns()


def _load_state(
    root: Path,
    program: str,
    source_alias: str,
    *,
    max_pages: int,
    page_limit: int,
    pace_seconds: float,
    daily_request_cap: int,
    poll_seconds: float,
) -> dict[str, Any]:
    state_path = root / "state.json"
    binding = dict(
        schema_version=1,
        source_alias=source_alias,
        address=program,
        collector_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        python_version=platform.python_version(),
        polling=dict(
            max_pages=max_pages,
            page_limit=page_limit,
            pace_seconds=pace_seconds,
            daily_request_cap=daily_request_cap,
            poll_seconds=poll_seconds,
        ),
        commitment="finalized",
        transaction_encoding="jsonParsed",
        transaction_version_max=1,
        coverage_claim="provider_returned_address_signatures_only",
        retry_policy="no automatic retry; ambiguous attempts remain explicit",
    )
    if state_path.exists():
        state = _read(state_path)
        if state.get("binding") != binding:
            raise ValueError("collector_binding_changed")
        return state
    state = dict(
        binding=binding,
        cursor=None,
        request_seq=0,
        last_attempt_ns=None,
        pending_before=None,
        pending_until=None,
        pending_newest=None,
        created_ns=time.time_ns(),
    )
    _create(state_path, state)
    return state


def _request_count_today(root: Path) -> int:
    today = time.strftime("%Y-%m-%d", time.gmtime())
    meter = root / "daily-accounting" / f"{today}.json"
    if meter.exists():
        row = _read(meter)
        if row.get("utc_date") != today or type(row.get("reserved_requests")) is not int:
            raise ValueError("daily_request_accounting_corrupt")
        return row["reserved_requests"]
    count = 0
    for path in (root / "requests").glob("*.json"):
        if time.strftime("%Y-%m-%d", time.gmtime(_read(path)["request_started_ns"] / NS)) == today:
            count += 1
    _create(meter, dict(utc_date=today, reserved_requests=count))
    return count


def _reconcile_daily_count(root: Path) -> None:
    (root / "requests").mkdir(parents=True, exist_ok=True)
    (root / "daily-accounting").mkdir(exist_ok=True)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    count = sum(
        time.strftime("%Y-%m-%d", time.gmtime(_read(path)["request_started_ns"] / NS)) == today
        for path in (root / "requests").glob("*.json")
    )
    _replace(
        root / "daily-accounting" / f"{today}.json",
        dict(utc_date=today, reserved_requests=count, reconciled_ns=time.time_ns()),
    )


def _process_signature(
    root: Path,
    client: ReadOnlyRPC,
    state: dict[str, Any],
    signature: str,
    last_request_ns: int,
    daily_count: list[int],
    pace_seconds: float,
    daily_request_cap: int,
) -> tuple[int, str]:
    key = _signature_key(signature)
    result_path = root / "transactions" / key / "status.json"
    if result_path.exists():
        result = _read(result_path)
        if result.get("signature_sha256") != key:
            raise ValueError("transaction_identity_changed")
        return last_request_ns, result["status"]
    if daily_count[0] >= daily_request_cap:
        return last_request_ns, "stopped_budget"
    params: list[Any] = [
        signature,
        {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 1},
    ]
    seq = state["request_seq"] + 1
    capture, last_request_ns = _request(
        root,
        client,
        seq,
        "getTransaction",
        params,
        time.time_ns(),
        pace_seconds,
        last_request_ns,
        daily_count,
    )
    state["request_seq"] = seq
    _replace(root / "state.json", state)
    status = "unresolved_request_failure"
    if capture is not None:
        try:
            value = capture.result()
            status = "provider_null"
            slot = None
            if isinstance(value, dict):
                slot = value.get("slot")
                status = (
                    "captured_success"
                    if (value.get("meta") or {}).get("err") is None
                    else "captured_failed_transaction"
                )
            elif value is not None:
                status = "unresolved_result_shape"
            _create(
                result_path,
                dict(
                    signature_sha256=key,
                    status=status,
                    slot=slot,
                    capture_path=str((root / "captures" / f"{seq:012d}").relative_to(root)),
                    receipt_ns=capture.received_ns,
                    event_time_seconds=value.get("blockTime") if isinstance(value, dict) else None,
                ),
            )
        except Exception as exc:
            status = "unresolved_response_parse"
            _create(
                result_path,
                dict(
                    signature_sha256=key,
                    status=status,
                    error_class=type(exc).__name__,
                    capture_path=str((root / "captures" / f"{seq:012d}").relative_to(root)),
                ),
            )
    else:
        # The charged reservation/result pair is the evidence; do not repeat the attempt.
        _create(result_path, dict(signature_sha256=key, status=status, request_sequence=seq))
    return last_request_ns, status


def poll_once(
    root: Path,
    client: ReadOnlyRPC,
    *,
    max_pages: int = 4,
    page_limit: int = 1000,
    pace_seconds: float = 1.0,
    daily_request_cap: int = 20_000,
    poll_seconds: float = 15,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    (root / "requests").mkdir(exist_ok=True)
    (root / "request-results").mkdir(exist_ok=True)
    (root / "captures").mkdir(exist_ok=True)
    (root / "transactions").mkdir(exist_ok=True)
    (root / "events").mkdir(exist_ok=True)
    (root / "polls").mkdir(exist_ok=True)
    (root / "daily-accounting").mkdir(exist_ok=True)
    # An attempt reserved before a crash is ambiguous. Never replay it automatically.
    for reservation in sorted((root / "requests").glob("*.json")):
        if not (root / "request-results" / reservation.name).exists():
            raise ValueError("unresolved_request_reservation_requires_reconciliation")
    program = SCHEMA["address"]
    state = _load_state(
        root,
        program,
        client.source_alias,
        max_pages=max_pages,
        page_limit=page_limit,
        pace_seconds=pace_seconds,
        daily_request_cap=daily_request_cap,
        poll_seconds=poll_seconds,
    )
    seen_sequences = [int(p.stem) for p in (root / "requests").glob("*.json")]
    state["request_seq"] = max([state["request_seq"], *seen_sequences])
    scheduled_ns = time.time_ns()
    start_seq = state["request_seq"]
    last_request_ns = int(state.get("last_attempt_ns") or 0)
    daily_count = [_request_count_today(root)]
    page_before: str | None = state.get("pending_before")
    previous_cursor = state.get("pending_until") or state.get("cursor")
    candidate_cursor: str | None = state.get("pending_newest")
    candidate_available_ns: int | None = state.get("pending_newest_available_ns")
    signatures_seen: set[str] = set()
    fetched = 0
    failed = 0
    page_count = 0
    gap_reason = None
    caught_up = False
    for _page_index in range(max_pages):
        params_obj: dict[str, Any] = {"limit": page_limit, "commitment": "finalized"}
        if page_before is not None:
            params_obj["before"] = page_before
        if previous_cursor is not None:
            params_obj["until"] = previous_cursor
        if daily_count[0] >= daily_request_cap:
            gap_reason = "daily_request_cap"
            break
        seq = state["request_seq"] + 1
        capture, last_request_ns = _request(
            root,
            client,
            seq,
            "getSignaturesForAddress",
            [program, params_obj],
            scheduled_ns,
            pace_seconds,
            last_request_ns,
            daily_count,
        )
        state["request_seq"] = seq
        state["last_attempt_ns"] = last_request_ns
        _replace(root / "state.json", state)
        if capture is None:
            gap_reason = "signature_query_failed_no_retry"
            break
        page_count += 1
        try:
            rows = capture.result()
            if not isinstance(rows, list) or len(rows) > page_limit:
                raise ValueError("signature_result_shape")
            signatures = [r.get("signature") if isinstance(r, dict) else None for r in rows]
            slots = [r.get("slot") if isinstance(r, dict) else None for r in rows]
            if any(not isinstance(s, str) or not s for s in signatures) or len(
                set(signatures)
            ) != len(signatures):
                raise ValueError("signature_identity_invalid")
            if any(type(s) is not int for s in slots) or any(
                a < b for a, b in zip(slots, slots[1:], strict=False)
            ):
                raise ValueError("signature_slot_order_invalid")
            if any(r.get("confirmationStatus") != "finalized" for r in rows):
                raise ValueError("signature_not_finalized")
            page_before = signatures[-1] if signatures else page_before
            if candidate_cursor is None and signatures:
                candidate_cursor = signatures[0]
                candidate_available_ns = capture.received_ns
            for row in reversed(rows):
                signature = row["signature"]
                if signature in signatures_seen:
                    continue
                signatures_seen.add(signature)
                last_request_ns, status = _process_signature(
                    root,
                    client,
                    state,
                    signature,
                    last_request_ns,
                    daily_count,
                    pace_seconds,
                    daily_request_cap,
                )
                fetched += status.startswith("captured_")
                failed += not status.startswith("captured_")
                state["last_attempt_ns"] = last_request_ns
                if STOP or status == "stopped_budget":
                    gap_reason = "stop_or_budget_during_page"
                    break
            if gap_reason:
                break
            if not rows or len(rows) < page_limit:
                caught_up = True
                break
            # On an initial full page, older history is intentionally outside the new collector.
            # Mark it unknown and pin the first observed watermark.
            if previous_cursor is None:
                gap_reason = "initial_head_page_saturated_older_scope_unknown"
                caught_up = True
                break
        except Exception as exc:
            gap_reason = "signature_response_" + type(exc).__name__
            break
    if gap_reason in (
        "daily_request_cap",
        "signature_query_failed_no_retry",
        "stop_or_budget_during_page",
    ):
        _create(root / "STOP", dict(reason=gap_reason, time_ns=time.time_ns()))
    if gap_reason and gap_reason not in (
        "daily_request_cap",
        "initial_head_page_saturated_older_scope_unknown",
        "stop_or_budget_during_page",
        "signature_query_failed_no_retry",
    ):
        _create(root / "STOP", dict(reason=gap_reason, time_ns=time.time_ns()))
    if (
        caught_up
        and candidate_cursor is not None
        and gap_reason not in ("signature_query_failed_no_retry", "stop_or_budget_during_page")
    ):
        state["cursor"] = candidate_cursor
        state["last_cursor_available_ns"] = candidate_available_ns
        state["pending_before"] = None
        state["pending_until"] = None
        state["pending_newest"] = None
    elif page_before is not None and candidate_cursor is not None:
        state["pending_before"] = page_before
        state["pending_until"] = previous_cursor
        state["pending_newest"] = candidate_cursor
        state["pending_newest_available_ns"] = candidate_available_ns
    state["last_attempt_ns"] = last_request_ns
    state["request_seq"] = max(state["request_seq"], start_seq)
    _replace(root / "state.json", state)
    elapsed_seconds = max(0.0, (time.time_ns() - scheduled_ns) / NS)
    request_count = state["request_seq"] - start_seq
    response_bytes = sum(
        (root / "captures" / f"{seq:012d}" / "response.json").stat().st_size
        for seq in range(start_seq + 1, state["request_seq"] + 1)
        if (root / "captures" / f"{seq:012d}" / "response.json").is_file()
    )
    summary = dict(
        poll_started_ns=scheduled_ns,
        poll_finished_ns=time.time_ns(),
        elapsed_seconds=elapsed_seconds,
        signature_pages=page_count,
        rpc_requests=request_count,
        response_bytes=response_bytes,
        signatures_observed=len(signatures_seen),
        transactions_captured=fetched,
        transactions_unresolved=failed,
        signatures_per_second=(len(signatures_seen) / elapsed_seconds if elapsed_seconds else None),
        response_bytes_per_second=(response_bytes / elapsed_seconds if elapsed_seconds else None),
        cursor_sha256=hashlib.sha256(state["cursor"].encode()).hexdigest()
        if state.get("cursor")
        else None,
        provider_alias=client.source_alias,
        gap_reason=gap_reason,
        completeness="unknown",
    )
    _create(root / "polls" / f"{scheduled_ns}.json", summary)
    return summary


def _stop(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--provider",
        choices=("alchemy-free", "helius-free", "solana-public"),
        default="alchemy-free",
    )
    parser.add_argument("--poll-seconds", type=float, default=15)
    parser.add_argument("--pace-seconds", type=float, default=1.0)
    parser.add_argument("--daily-request-cap", type=int, default=20_000)
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--page-limit", type=int, default=1000)
    parser.add_argument("--parent-epoch-root", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 5 <= args.poll_seconds <= 3600 or not 0.25 <= args.pace_seconds <= 10:
        parser.error("poll/pace interval outside safe bounds")
    if not 100 <= args.daily_request_cap <= 100_000 or not 1 <= args.max_pages <= 10:
        parser.error("request/page bound outside safe range")
    if not 1 <= args.page_limit <= 1000:
        parser.error("page limit outside safe range")
    args.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.root, 0o700)
    with (args.root / ".collector.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("collector already active", file=sys.stderr)
            return 2
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        _record_source_epoch(args.root, args.provider, args.parent_epoch_root)
        _reconcile_daily_count(args.root)
        rpc_endpoint, source_alias = _provider_endpoint(args.provider)
        client = ReadOnlyRPC(
            rpc_endpoint,
            source_alias,
            timeout=20,
            max_bytes=8_000_000,
            keep_alive=True,
        )
        try:
            while not STOP:
                if (args.root / "STOP").exists():
                    break
                try:
                    result = poll_once(
                        args.root,
                        client,
                        max_pages=args.max_pages,
                        page_limit=args.page_limit,
                        pace_seconds=args.pace_seconds,
                        daily_request_cap=args.daily_request_cap,
                        poll_seconds=args.poll_seconds,
                    )
                    print(canonical(result), flush=True)
                except Exception as exc:
                    # Error text can include URL details; store only the class.
                    _create(
                        args.root / "events" / f"runtime-{time.time_ns()}.json",
                        dict(
                            event="runtime_exception",
                            error_class=type(exc).__name__,
                            time_ns=time.time_ns(),
                        ),
                    )
                    if not (args.root / "STOP").exists():
                        _create(
                            args.root / "STOP",
                            dict(reason="runtime_exception", time_ns=time.time_ns()),
                        )
                    print(
                        canonical(dict(status="failed", error_class=type(exc).__name__)), flush=True
                    )
                    return 1
                if args.once:
                    break
                time.sleep(args.poll_seconds)
        finally:
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
