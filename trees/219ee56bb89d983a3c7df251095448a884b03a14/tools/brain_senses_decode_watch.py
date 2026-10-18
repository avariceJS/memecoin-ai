#!/usr/bin/env python3
"""Read-only decoder exception census over immutable prospective captures.

This tool is commit-bound to the capture root and decoder/schema. It records decoder outcomes
and novel exception classes; it never changes captures, source status, or admission.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from memecoin_ai.domain import canonical, digest
from memecoin_ai.sources import Capture
from memecoin_pumpswap import decoder
from memecoin_pumpswap.schema import SCHEMA

STOP = False


def _write_new(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x") as stream:
        stream.write(canonical(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_immutable(path: Path, row: dict[str, Any]) -> None:
    if path.exists():
        existing = _read(path)
        comparable = {key: value for key, value in row.items() if key != "observed_ns"}
        prior = {key: value for key, value in existing.items() if key != "observed_ns"}
        if prior != comparable:
            raise ValueError("immutable_record_conflict")
        return
    _write_new(path, row)


def _replace(path: Path, row: dict[str, Any]) -> None:
    temp = path.with_name("." + path.name + ".tmp")
    with temp.open("x") as stream:
        stream.write(canonical(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read(path: Path) -> dict[str, Any]:
    row = json.loads(path.read_text())
    if not isinstance(row, dict):
        raise ValueError("invalid_object")
    return row


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _classify(tx: dict[str, Any], result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    blockers = sorted({b for swap in result.get("swaps", []) for b in swap.get("blockers", [])})
    errors = sorted(
        {
            e.get("category", "unknown") if isinstance(e, dict) else str(e)
            for e in result.get("errors", [])
        }
    )
    diagnostics = sorted(
        {
            swap["economic_diagnostic"].get("status", "missing_status")
            for swap in result.get("swaps", [])
            if isinstance(swap.get("economic_diagnostic"), dict)
        }
    )
    unknown = len(result.get("unknown_instructions", []))
    ix_programs: dict[str, int] = {}
    try:
        instructions = tx["transaction"]["message"]["instructions"]
        for ix in instructions:
            program = ix.get("programId", "missing")
            ix_programs[program] = ix_programs.get(program, 0) + 1
    except (KeyError, TypeError, AttributeError):
        ix_programs = {"unavailable": 1}
    shape = dict(
        status=result.get("status", "missing_status"),
        version=tx.get("version", "missing"),
        blockers=blockers,
        errors=errors,
        economic_diagnostic_statuses=diagnostics,
        unknown_instruction_count=unknown,
        outer_program_counts=ix_programs,
    )
    fingerprint = digest(canonical(shape).encode())
    return fingerprint, shape


def _source_binding(root: Path) -> dict[str, Any]:
    state_path = root / "state.json"
    state = _read(state_path)
    binding = state.get("binding")
    if (
        not isinstance(binding, dict)
        or binding.get("coverage_claim") != "provider_returned_address_signatures_only"
    ):
        raise ValueError("source_binding_invalid")
    expected_source_hash = binding.get("collector_code_sha256")
    # The collector is deployed from its source checkout; the recorded hash must be reproducible
    # from the source-root copy registered by this watch's immutable binding.
    return dict(
        state=state,
        binding=binding,
        source_binding_sha256=digest(canonical(binding).encode()),
        expected_source_hash=expected_source_hash,
    )


def _binding(source_root: Path) -> dict[str, Any]:
    source = _source_binding(source_root)
    if (
        source["expected_source_hash"]
        != hashlib.sha256(
            Path(__file__).with_name("brain_senses_program_watch.py").read_bytes()
        ).hexdigest()
    ):
        raise ValueError("collector_source_hash_mismatch")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    ).stdout.strip()
    return dict(
        schema_version=1,
        code_commit=head,
        watcher_code_sha256=_sha(Path(__file__)),
        source_root_identity=str(source_root.resolve()),
        source_binding_sha256=source["source_binding_sha256"],
        source_binding=source["binding"],
        decoder_sha256=_sha(Path(decoder.__file__)),
        schema_sha256=digest(canonical(SCHEMA).encode()),
        semantics="read_only_decode_census_no_admission",
        coverage_claim="unknown_provider_address_index_not_proven_complete",
    )


def _process_sequence(
    source_root: Path, out_root: Path, seq: int, state: dict[str, Any]
) -> dict[str, Any]:
    stem = f"{seq:012d}"
    request_path = source_root / "requests" / f"{stem}.json"
    result_path = source_root / "request-results" / f"{stem}.json"
    request = _read(request_path)
    request_result = _read(result_path)
    base: dict[str, Any] = dict(
        sequence=seq,
        request_method=request.get("method"),
        request_sha256=request.get("request_sha256"),
        request_result_sha256=_sha(result_path),
        source_alias=state["binding"]["source_binding"]["source_alias"],
        decode_time_ns=time.time_ns(),
        admission="none",
    )
    if request.get("method") != "getTransaction":
        return dict(base, outcome="not_transaction_request")
    if request_result.get("status") != "captured":
        return dict(
            base,
            outcome="capture_failed_or_unresolved",
            source_status=request_result.get("status"),
            error_class=request_result.get("error_class"),
        )
    capture_rel = request_result.get("capture_path")
    if (
        not isinstance(capture_rel, str)
        or Path(capture_rel).is_absolute()
        or ".." in Path(capture_rel).parts
    ):
        raise ValueError("capture_path_invalid")
    capture_path = source_root / capture_rel
    capture = Capture.load(capture_path)
    if capture.method != "getTransaction" or digest(capture.request_json.encode()) != request.get(
        "request_sha256"
    ):
        raise ValueError("capture_request_binding_mismatch")
    tx = capture.result()
    if not isinstance(tx, dict):
        return dict(
            base,
            outcome="unsupported_response_shape",
            capture_path=capture_rel,
            raw_response_sha256=digest(capture.response),
            receipt_ns=capture.received_ns,
        )
    result = decoder.decode(tx)
    fingerprint, shape = _classify(tx, result)
    record = dict(
        base,
        outcome="decoded",
        capture_path=capture_rel,
        raw_response_sha256=digest(capture.response),
        receipt_ns=capture.received_ns,
        event_time_seconds=tx.get("blockTime"),
        slot=tx.get("slot"),
        decoder_status=result.get("status"),
        protocol_qualified=result.get("protocol_qualified", False),
        training_eligible=result.get("training_eligible", False),
        exception_class_sha256=fingerprint,
        exception_shape=shape,
        blockers=shape["blockers"],
        errors=shape["errors"],
        economic_diagnostics=[
            swap["economic_diagnostic"]
            for swap in result.get("swaps", [])
            if isinstance(swap.get("economic_diagnostic"), dict)
        ],
        swap_count=len(result.get("swaps", [])),
    )
    # The source transaction manifest is the authoritative signature-key mapping.
    params = json.loads(capture.request_json)
    signature = params.get("params", [None])[0]
    if not isinstance(signature, str):
        raise ValueError("signature_identity_missing")
    signature_key = hashlib.sha256(signature.encode("ascii")).hexdigest()
    record["signature_sha256"] = signature_key
    tx_status_path = source_root / "transactions" / signature_key / "status.json"
    if not tx_status_path.exists():
        return dict(base, outcome="source_manifest_pending", signature_sha256=signature_key)
    tx_status = _read(tx_status_path)
    if tx_status.get("signature_sha256") != signature_key:
        raise ValueError("source_transaction_identity_mismatch")
    record["source_transaction_status_sha256"] = _sha(tx_status_path)
    target = out_root / "decode-results" / signature_key[:2] / f"{signature_key}.json"
    if target.exists():
        if _read(target).get("request_sha256") != record["request_sha256"]:
            raise ValueError("decode_identity_conflict")
    else:
        _write_new(target, record)
    groups = state["classes"]
    group = groups.setdefault(
        fingerprint,
        dict(
            shape=shape,
            count=0,
            first_receipt_ns=capture.received_ns,
            last_receipt_ns=capture.received_ns,
            representatives=[],
        ),
    )
    group["count"] += 1
    group["last_receipt_ns"] = capture.received_ns
    if signature_key not in group["representatives"] and len(group["representatives"]) < 5:
        group["representatives"].append(signature_key)
    if group["count"] == 1:
        _write_immutable(
            out_root / "novelty" / f"{fingerprint}.json",
            dict(
                event="new_decoder_exception_class",
                exception_class_sha256=fingerprint,
                shape=shape,
                first_signature_sha256=signature_key,
                source_capture_path=capture_rel,
                raw_response_sha256=digest(capture.response),
                observed_ns=time.time_ns(),
                action="manual_review_required_no_auto_promotion",
            ),
        )
    return record


def run_once(source_root: Path, out_root: Path) -> dict[str, Any]:
    source = _source_binding(source_root)
    state_path = out_root / "watch-state.json"
    binding = _binding(source_root)
    if state_path.exists():
        state = _read(state_path)
        if state.get("binding") != binding:
            raise ValueError("decode_watch_binding_changed")
    else:
        state = dict(
            binding=binding, last_sequence=0, classes={}, outcomes={}, created_ns=time.time_ns()
        )
        out_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write_new(state_path, state)
    out_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (out_root / "decode-results").mkdir(exist_ok=True)
    (out_root / "novelty").mkdir(exist_ok=True)
    processed = 0
    latest = int(source["state"].get("request_seq", 0))
    for seq in range(state["last_sequence"] + 1, latest + 1):
        request = source_root / "requests" / f"{seq:012d}.json"
        result = source_root / "request-results" / f"{seq:012d}.json"
        if not request.exists() or not result.exists():
            break
        record = _process_sequence(source_root, out_root, seq, state)
        if record["outcome"] == "source_manifest_pending":
            break
        outcome = record["outcome"]
        state["outcomes"][outcome] = state["outcomes"].get(outcome, 0) + 1
        state["last_sequence"] = seq
        processed += 1
    summary = dict(
        binding=binding,
        updated_ns=time.time_ns(),
        last_sequence=state["last_sequence"],
        sequences_processed=state["last_sequence"],
        newly_processed=processed,
        outcomes=state["outcomes"],
        exception_classes=state["classes"],
        observation_coverage="unknown",
        training_or_execution_admission="none",
    )
    _replace(state_path, state)
    _replace(out_root / "summary.json", summary)
    return summary


def _signal(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 10 <= args.poll_seconds <= 3600:
        parser.error("poll-seconds outside safe bounds")
    args.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.root / ".watch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("decoder watch already active", file=sys.stderr)
            return 2
        signal.signal(signal.SIGTERM, _signal)
        signal.signal(signal.SIGINT, _signal)
        while not STOP:
            try:
                print(canonical(run_once(args.source_root, args.root)), flush=True)
            except Exception as exc:
                _write_new(
                    args.root / "errors" / f"error-{time.time_ns()}.json",
                    dict(error_class=type(exc).__name__, time_ns=time.time_ns()),
                )
                print(canonical(dict(status="failed", error_class=type(exc).__name__)), flush=True)
                return 1
            if args.once:
                break
            time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
