"""Read-only, provider-agreement reconciliation for bounded finalized block ranges.

This capability says only that two independently captured JSON-RPC providers agreed on
the pinned request schedule and block identities for one bounded range. It is not proof
of cryptographic finality, market completeness, or any market observation support.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from memecoin_ai.sources import Capture

from . import block_inventory
from .storage import PIN, atomic_json, canonical, digest, lock, safe_path, sync_dir

CAPABILITY = "cross_provider_finalized_range_reconciliation_v1"
TRUST_MODEL = "provider_agreement_not_cryptographic_or_market_exhaustiveness"
PAGE_SIZE = 32
MAX_RANGE = 256
MAX_FILE_BYTES = 12_000_000
MAX_BUNDLE_BYTES = 64_000_000
MAX_TOTAL_DEPENDENCY_BYTES = 3 * MAX_BUNDLE_BYTES
MAX_PATH_CHARS = 512
_RECEIPTS = "qualification-block-reconciliations"
_PAGES = "qualification-block-reconciliation-pages"


def _file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _durable_write(path: Path, body: bytes):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)


def _copy_provider_root(source: Path, target: Path, provider: str, expected_pin: str):
    """Seal one bounded provider dependency tree before publishing its reference."""
    source = safe_path(source.resolve())
    target = safe_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        checked = _validate_root(target, provider)
        if checked["source_pin"] != expected_pin:
            raise ValueError("block_reconciliation_dependency_conflict")
        return target
    manifest = _load_json(source / "fixture.json", 1_000_000)
    relative_files = ["fixture.json", "run-receipt.json", "attempt-ledger.json"]
    for capture in manifest.get("captures", []):
        relative_files.extend(
            [f"{capture['path']}/receipt.json", f"{capture['path']}/response.json"]
        )
    if len(relative_files) > 2 * (MAX_RANGE + 3) + 3:
        raise ValueError("block_reconciliation_dependency_count")
    stage = Path(tempfile.mkdtemp(prefix="reconcile-stage-", dir=target.parent))
    os.chmod(stage, 0o700)
    total_bytes = 0
    try:
        for relative in relative_files:
            src = safe_path(source / relative)
            if src.is_symlink() or not src.is_file():
                raise ValueError("block_reconciliation_dependency_file")
            size = src.stat().st_size
            if size > MAX_FILE_BYTES:
                raise ValueError("block_reconciliation_dependency_file_bound")
            total_bytes += size
            if total_bytes > MAX_BUNDLE_BYTES:
                raise ValueError("block_reconciliation_dependency_bundle_bound")
            dst = safe_path(stage / relative)
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(dst.parent, 0o700)
            _durable_write(dst, src.read_bytes())
        for directory in sorted(
            (p for p in stage.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
        ):
            sync_dir(directory)
        sync_dir(stage)
        checked = _validate_root(stage, provider)
        if checked["source_pin"] != expected_pin:
            raise ValueError("block_reconciliation_dependency_copy_pin")
        os.rename(stage, target)
        sync_dir(target.parent)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return target


def _relative_root(path: Path) -> str:
    path = safe_path(path.resolve())
    try:
        relative = path.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        raise ValueError("block_reconciliation_root_must_be_workspace_relative") from None
    if len(relative) > MAX_PATH_CHARS or any(p in {"", ".", ".."} for p in relative.split("/")):
        raise ValueError("block_reconciliation_root_path")
    return relative


def _store_path(store: Path, relative: str) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or len(relative) > MAX_PATH_CHARS
        or "\\" in relative
        or relative.startswith("/")
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("block_reconciliation_root_path")
    base = store.resolve()
    path = safe_path(base / relative)
    if not path.resolve().is_relative_to(base):
        raise ValueError("block_reconciliation_root_path")
    return path


def _load_json(path: Path, limit=MAX_FILE_BYTES):
    path = safe_path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("block_reconciliation_file_bound")
    return json.loads(path.read_text())


def _expected_requests(plan: dict):
    start, end = plan["start_slot"], plan["end_slot"]
    rows = [
        (
            "enumeration",
            None,
            "getBlocks",
            [start, end - 1, {"commitment": "finalized"}],
            "enumeration",
        ),
        (
            "boundary",
            plan["left_witness_slot"],
            "getBlock",
            [plan["left_witness_slot"], block_inventory.BLOCK_OPTIONS],
            "boundary-left",
        ),
    ]
    rows.extend(
        ("slot", slot, "getBlock", [slot, block_inventory.BLOCK_OPTIONS], f"slots/{slot}")
        for slot in range(start, end)
    )
    rows.append(
        (
            "boundary",
            plan["right_witness_slot"],
            "getBlock",
            [plan["right_witness_slot"], block_inventory.BLOCK_OPTIONS],
            "boundary-right",
        )
    )
    return [
        dict(
            purpose=purpose,
            slot=slot,
            method=method,
            path=path,
            request_json=canonical(
                dict(jsonrpc="2.0", id=1, method=method, params=params)
            ).decode(),
        )
        for purpose, slot, method, params, path in rows
    ]


def _capture_rows(root: Path, manifest: dict):
    expected = _expected_requests(_load_json(root / "run-receipt.json")["plan"])
    captures = manifest.get("captures")
    if not isinstance(captures, list) or not 1 <= len(captures) <= len(expected):
        raise ValueError("block_reconciliation_capture_schedule")
    output = []
    total = (root / "fixture.json").stat().st_size
    previous_received = None
    for row, wanted in zip(captures, expected, strict=False):
        purpose, slot, path = wanted["purpose"], wanted["slot"], wanted["path"]
        if purpose == "enumeration":
            expected_ref = {"purpose": purpose, "path": path}
        elif purpose == "slot":
            expected_ref = {"purpose": purpose, "slot": slot, "path": path}
        else:
            expected_ref = {
                "purpose": purpose,
                "slot": slot,
                "side": "left" if path == "boundary-left" else "right",
                "path": path,
            }
        if row != expected_ref:
            raise ValueError("block_reconciliation_capture_schedule")
        directory = safe_path(root / path)
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or not directory.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError("block_reconciliation_capture_path")
        for name in ("receipt.json", "response.json"):
            item = safe_path(directory / name)
            if item.is_symlink() or not item.is_file() or item.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("block_reconciliation_capture_size")
            total += item.stat().st_size
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("block_reconciliation_bundle_size")
        metadata = _load_json(directory / "receipt.json")
        cap = Capture.load(directory)
        provenance = metadata.get("http_provenance")
        if not isinstance(provenance, dict) or set(provenance) != {
            "version",
            "endpoint_origin",
            "account_alias",
            "http_status",
            "response_headers",
            "retry_count",
            "attempt_number",
            "transport_error_class",
            "response_truncated",
        }:
            raise ValueError("block_reconciliation_http_provenance")
        http_status = provenance["http_status"]
        transport_error = provenance["transport_error_class"]
        truncated = provenance["response_truncated"]
        if (
            provenance["version"] != "solana-http-provenance-v1"
            or http_status is not None
            and (type(http_status) is not int or not 100 <= http_status <= 599)
            or provenance["retry_count"] != 0
            or provenance["attempt_number"] != 1
            or transport_error is not None
            and not isinstance(transport_error, str)
            or type(truncated) is not bool
            or not isinstance(provenance["response_headers"], dict)
            or cap.source_alias != _load_json(root / "run-receipt.json")["plan"]["source_alias"]
            or cap.method != wanted["method"]
            or cap.request_json != wanted["request_json"]
            or metadata["http_provenance"]["endpoint_origin"]
            != _load_json(root / "run-receipt.json")["endpoint_origin"]
            or metadata["http_provenance"]["account_alias"]
            != _load_json(root / "run-receipt.json")["account_alias"]
            or previous_received is not None
            and cap.request_started_ns < previous_received
        ):
            raise ValueError("block_reconciliation_capture_provenance")
        previous_received = cap.received_ns
        marker = None
        if transport_error is not None or http_status != 200 or truncated:
            marker = {"_capture_state": "failed", "_capture_reason": "transport_http_or_truncated"}
        else:
            try:
                envelope = json.loads(cap.response)
            except (UnicodeDecodeError, json.JSONDecodeError):
                envelope = None
            if (
                not isinstance(envelope, dict)
                or envelope.get("jsonrpc") != "2.0"
                or envelope.get("id") != 1
            ):
                marker = {"_capture_state": "failed", "_capture_reason": "invalid_json_rpc"}
            elif "error" in envelope:
                error = envelope["error"]
                code = error.get("code") if isinstance(error, dict) else None
                if type(code) is not int:
                    marker = {"_capture_state": "failed", "_capture_reason": "invalid_rpc_error"}
                else:
                    marker = {
                        "_capture_state": "unsupported" if code in {-32601, -32602} else "failed",
                        "_capture_reason": "rpc_unsupported"
                        if code in {-32601, -32602}
                        else "rpc_error",
                    }
            elif "result" not in envelope:
                marker = {"_capture_state": "failed", "_capture_reason": "missing_result"}
            else:
                marker = envelope["result"]
        output.append(
            (
                wanted,
                cap,
                marker,
                _file_sha(directory / "receipt.json"),
                _file_sha(directory / "response.json"),
            )
        )
    return output


def _validate_root(root: Path, provider: str):
    root = safe_path(root.resolve())
    manifest = _load_json(root / "fixture.json", 1_000_000)
    run = _load_json(root / "run-receipt.json")
    ledger = _load_json(root / "attempt-ledger.json")
    plan = run.get("plan")
    if not isinstance(plan, dict) or run.get("version") != "solana-bounded-block-range-capture-v1":
        raise ValueError("block_reconciliation_run_receipt")
    start, end = manifest.get("start_slot"), manifest.get("end_slot")
    total_requests = end - start + 3 if type(start) is int and type(end) is int else 0
    stop_reason = run.get("stop_reason")
    stopped_slots = manifest.get("stopped_slots")
    if (
        manifest.get("version") != "solana-block-range-fixture-v1"
        or manifest.get("interval_convention") != "half-open-start-inclusive-end-exclusive-v1"
        or type(start) is not int
        or type(end) is not int
        or end <= start
        or end - start > MAX_RANGE
        or not isinstance(stopped_slots, list)
        or stopped_slots != sorted(set(stopped_slots))
        or plan.get("start_slot") != start
        or plan.get("end_slot") != end
        or plan.get("left_witness_slot") >= start
        or plan.get("right_witness_slot") < end
        or type(plan.get("request_budget")) is not int
        or not 1 <= plan["request_budget"] <= total_requests
        or plan.get("max_response_bytes", 0) > MAX_FILE_BYTES
        or plan.get("max_bundle_bytes", 0) > MAX_BUNDLE_BYTES
        or stop_reason not in {None, "request_budget_stop", "plan_expired"}
        or not isinstance(run.get("stopped_requests"), list)
        or not isinstance(run.get("stopped_slots"), list)
        or run.get("retries") != 0
        or run.get("planned_requests") != total_requests
        or type(run.get("attempts")) is not int
        or run.get("attempts") != len(ledger.get("attempts", []))
        or run.get("fixture_written") is not bool(manifest.get("captures"))
        or run.get("raw_payloads_in_work") is not True
    ):
        raise ValueError("block_reconciliation_plan_bounds")
    expected_provider = {
        "free": (
            "alchemy",
            "alchemy-free-finalized-block-range-v1",
            "alchemy-free-local",
            "https://solana-mainnet.g.alchemy.com",
        ),
        "public": (
            "solana-public",
            "solana-public-finalized-block-range-v1",
            "public-rpc",
            "https://api.mainnet-beta.solana.com",
        ),
    }[provider]
    if (
        run.get("provider_alias"),
        plan.get("source_alias"),
        run.get("account_alias"),
        run.get("endpoint_origin"),
    ) != expected_provider:
        raise ValueError("block_reconciliation_provider_identity")
    plan_pin = hashlib.sha256(canonical(plan)).hexdigest()
    ledger_path = root / "attempt-ledger.json"
    if (
        run.get("plan_pin") != plan_pin
        or ledger.get("plan") != plan
        or ledger.get("plan_pin") != plan_pin
        or run.get("attempt_ledger_sha256") != _file_sha(ledger_path)
        or ledger.get("interval") != [start, end]
        or ledger.get("request_budget") != plan["request_budget"]
        or run.get("request_budget") != plan["request_budget"]
    ):
        raise ValueError("block_reconciliation_ledger_pin")
    expected = _expected_requests(plan)
    attempts = ledger.get("attempts")
    planned_requests = ledger.get("planned_requests")
    if (
        not isinstance(attempts, list)
        or not isinstance(planned_requests, list)
        or len(planned_requests) != len(expected)
        or len(attempts) != run["attempts"]
        or not 1 <= len(attempts) <= len(expected)
    ):
        raise ValueError("block_reconciliation_ledger_count")
    if len(attempts) < len(expected):
        if stop_reason is None:
            raise ValueError("block_reconciliation_unrecorded_stop")
        stop_status = (
            "stopped_budget" if stop_reason == "request_budget_stop" else "stopped_expired"
        )
    else:
        if stop_reason is not None or run["stopped_requests"] or run["stopped_slots"]:
            raise ValueError("block_reconciliation_stop_contradiction")
        stop_status = None
    expected_stopped_requests = []
    expected_stopped_slots = []
    for want, planned in zip(expected, planned_requests, strict=True):
        if (
            planned.get("purpose") != want["purpose"]
            or planned.get("slot") != want["slot"]
            or planned.get("method") != want["method"]
            or planned.get("path") != want["path"]
            or planned.get("request_json") != want["request_json"]
            or planned.get("request_sha256")
            != hashlib.sha256(want["request_json"].encode()).hexdigest()
        ):
            raise ValueError("block_reconciliation_planned_request")
    for want, planned, attempt in zip(
        expected[: len(attempts)], planned_requests[: len(attempts)], attempts, strict=True
    ):
        if planned.get("status") not in {"response_received", "transport_error"}:
            raise ValueError("block_reconciliation_planned_attempt_status")
        request_hash = hashlib.sha256(want["request_json"].encode()).hexdigest()
        if (
            attempt.get("purpose") != want["purpose"]
            or attempt.get("slot") != want["slot"]
            or attempt.get("method") != want["method"]
            or attempt.get("request_json") != want["request_json"]
            or attempt.get("request_sha256") != request_hash
            or attempt.get("status") != planned.get("status")
            or attempt.get("retry_count") != 0
            or attempt.get("attempt_number") != 1
            or attempt.get("request_started_ns") != planned.get("request_started_ns")
            or attempt.get("received_ns") != planned.get("received_ns")
            or attempt.get("http_status") != planned.get("http_status")
            or attempt.get("transport_error_class") != planned.get("transport_error_class")
            or attempt.get("response_truncated") != planned.get("response_truncated")
            or planned.get("received_ns") is None
            or type(planned.get("request_started_ns")) is not int
            or planned["request_started_ns"] < 0
        ):
            raise ValueError("block_reconciliation_attempt")
    for want, planned in zip(
        expected[len(attempts) :], planned_requests[len(attempts) :], strict=True
    ):
        if stop_status is None or planned.get("status") != stop_status:
            raise ValueError("block_reconciliation_stopped_request")
        expected_stopped_requests.append(
            {key: want[key] for key in ("purpose", "slot", "method", "path")}
            | {"status": stop_status}
        )
        if want["purpose"] == "slot":
            expected_stopped_slots.append(want["slot"])
    if (
        run["stopped_requests"] != expected_stopped_requests
        or run["stopped_slots"] != expected_stopped_slots
    ):
        raise ValueError("block_reconciliation_stopped_request_ledger")
    for want, planned, attempt in zip(
        expected[: len(attempts)], planned_requests[: len(attempts)], attempts, strict=True
    ):
        request_hash = hashlib.sha256(want["request_json"].encode()).hexdigest()
        for row in (planned, attempt):
            if (
                row.get("purpose") != want["purpose"]
                or row.get("slot") != want["slot"]
                or row.get("method") != want["method"]
                or row.get("request_json") != want["request_json"]
                or row.get("request_sha256") != request_hash
                or row.get("status") not in {"response_received", "transport_error"}
                or row.get("retry_count", 0) != 0
                or type(row.get("response_truncated")) is not bool
                or row.get("transport_error_class") is not None
                and not isinstance(row.get("transport_error_class"), str)
                or row.get("http_status") is not None
                and (
                    type(row.get("http_status")) is not int or not 100 <= row["http_status"] <= 599
                )
            ):
                raise ValueError("block_reconciliation_attempt")
        if (
            planned.get("path") != want["path"]
            or attempt.get("attempt_number") != 1
            or attempt.get("request_started_ns") != planned.get("request_started_ns")
            or attempt.get("received_ns") != planned.get("received_ns")
        ):
            raise ValueError("block_reconciliation_attempt_schedule")
    captures = _capture_rows(root, manifest)
    if len(captures) != len(attempts) or manifest["stopped_slots"] != run["stopped_slots"]:
        raise ValueError("block_reconciliation_capture_attempt_count")
    outcomes = run.get("outcomes")
    if not isinstance(outcomes, list) or len(outcomes) != len(captures):
        raise ValueError("block_reconciliation_outcomes")
    for outcome, (want, cap, _, _, response_sha) in zip(outcomes, captures, strict=True):
        provenance = _load_json(root / want["path"] / "receipt.json")["http_provenance"]
        if (
            outcome.get("response_sha256") != response_sha
            or outcome.get("response_bytes") != len(cap.response)
            or outcome.get("http_status") != provenance["http_status"]
            or type(outcome.get("response_truncated")) is not bool
            or outcome.get("response_truncated") != provenance["response_truncated"]
            or outcome.get("retries") != 0
            or outcome.get("transport_error_class") != provenance["transport_error_class"]
            or outcome.get("method") != want["method"]
            or outcome.get("slot") != want["slot"]
        ):
            raise ValueError("block_reconciliation_outcome")
    source_pin = digest(
        {
            "fixture": _file_sha(root / "fixture.json"),
            "run": _file_sha(root / "run-receipt.json"),
            "ledger": _file_sha(ledger_path),
            "captures": [
                {"path": row[0]["path"], "receipt": row[3], "response": row[4]} for row in captures
            ],
        }
    )
    return dict(
        root=root,
        root_path=_relative_root(root),
        manifest=manifest,
        plan=plan,
        captures=captures,
        source_pin=source_pin,
        run=run,
    )


def _block_summary(result):
    summary = {}
    for want, capture, body, _, _ in result["captures"]:
        if want["method"] == "getBlocks":
            continue
        slot = want["slot"]
        if isinstance(body, dict) and body.get("_capture_state") in {
            "failed",
            "unsupported",
        }:
            summary[slot] = dict(
                slot=slot,
                status=body["_capture_state"],
                reason=body["_capture_reason"],
                available_ns=capture.received_ns,
            )
            continue
        if body is None:
            if want["purpose"] != "slot":
                raise ValueError("block_reconciliation_missing_witness")
            summary[slot] = dict(
                slot=slot,
                status="null_result",
                available_ns=capture.received_ns,
            )
            continue
        if not isinstance(body, dict):
            raise ValueError("block_reconciliation_missing_block")
        transactions = body.get("transactions")
        if (
            not isinstance(body.get("blockhash"), str)
            or not body["blockhash"]
            or not isinstance(body.get("previousBlockhash"), str)
            or not body["previousBlockhash"]
            or type(body.get("parentSlot")) is not int
            or not 0 <= body["parentSlot"] < slot
            or not isinstance(transactions, list)
        ):
            summary[slot] = dict(
                slot=slot,
                status="failed",
                reason="invalid_full_block_result",
                available_ns=capture.received_ns,
            )
            continue
        signatures = []
        for tx in transactions:
            if (
                not isinstance(tx, dict)
                or not isinstance(tx.get("transaction"), dict)
                or not isinstance(tx["transaction"].get("signatures"), list)
            ):
                summary[slot] = dict(
                    slot=slot,
                    status="failed",
                    reason="invalid_transaction_record",
                    available_ns=capture.received_ns,
                )
                break
            tx_sigs = tx["transaction"]["signatures"]
            if any(not isinstance(sig, str) or not sig for sig in tx_sigs):
                summary[slot] = dict(
                    slot=slot,
                    status="failed",
                    reason="invalid_transaction_signature",
                    available_ns=capture.received_ns,
                )
                break
            signatures.extend(tx_sigs)
        if summary.get(slot, {}).get("status") == "failed":
            continue
        summary[slot] = dict(
            slot=slot,
            status="complete",
            available_ns=capture.received_ns,
            blockhash=body["blockhash"],
            previous_blockhash=body["previousBlockhash"],
            parent_slot=body["parentSlot"],
            transaction_count=len(transactions),
            signature_count=len(signatures),
            ordered_signatures_sha256=hashlib.sha256(canonical(signatures)).hexdigest(),
        )
    return summary


def _capture_identity_maps(result):
    times = {}
    slot_pins = {}
    for want, capture, _, receipt_sha, response_sha in result["captures"]:
        pin = digest({"receipt_sha256": receipt_sha, "response_sha256": response_sha})
        times[pin] = capture.received_ns
        if want["purpose"] == "slot":
            slot_pins[want["slot"]] = pin
    return times, slot_pins


def _replay(free_root: Path, public_root: Path, inventory_store: Path, inventory_pin: str):
    inventory = block_inventory.load_finalized_block_inventory(inventory_store, inventory_pin)
    free = _validate_root(free_root, "free")
    public = _validate_root(public_root, "public")
    if (
        free["manifest"]["start_slot"] != public["manifest"]["start_slot"]
        or free["manifest"]["end_slot"] != public["manifest"]["end_slot"]
    ):
        raise ValueError("block_reconciliation_interval_mismatch")
    if (
        free["plan"]["left_witness_slot"] != public["plan"]["left_witness_slot"]
        or free["plan"]["right_witness_slot"] != public["plan"]["right_witness_slot"]
    ):
        raise ValueError("block_reconciliation_witness_mismatch")
    for field in (
        "start_slot",
        "end_slot",
        "left_witness_slot",
        "right_witness_slot",
        "request_budget",
        "max_request_bytes",
        "max_response_bytes",
        "max_bundle_bytes",
        "timeout_seconds",
    ):
        if free["plan"][field] != public["plan"][field]:
            raise ValueError("block_reconciliation_plan_mismatch")
    if free["plan"]["endpoint_origin"] == public["plan"]["endpoint_origin"]:
        raise ValueError("block_reconciliation_independent_provider_required")
    replay = block_inventory._validate(free["root"])
    public_replay = block_inventory._validate(public["root"])
    free_status = {row["slot"]: row for row in replay["ledger_rows"]}
    public_status = {row["slot"]: row for row in public_replay["ledger_rows"]}
    free_unresolved_slots = sum(
        replay["ledger_counts"][state] for state in ("failed", "stopped", "unknown", "unsupported")
    )
    if set(free_status) != set(public_status):
        raise ValueError("block_reconciliation_provider_slot_ledger")
    if (
        inventory["source_kind"] != "alchemy_free_http_capture_v1"
        or inventory["source_account_alias"] != "alchemy-free-local"
        or inventory["capture_manifest_pin"] != replay["manifest_pin"]
        or inventory["capture_set_pin"] != replay["capture_set_pin"]
        or inventory["capture_pins"] != replay["capture_pins"]
        or inventory["enumeration_status"] != replay["enumeration_status"]
        or inventory["ledger_counts"] != replay["ledger_counts"]
        or inventory["unresolved_slots"] != free_unresolved_slots
    ):
        raise ValueError("block_reconciliation_inventory_binding")
    start, end = free["manifest"]["start_slot"], free["manifest"]["end_slot"]
    enum_free = free["captures"][0][2]
    enum_public = public["captures"][0][2]
    enumeration_agreement = (
        replay["enumeration_status"] == "complete"
        and public_replay["enumeration_status"] == "complete"
        and isinstance(enum_free, list)
        and isinstance(enum_public, list)
        and enum_free == enum_public
    )
    a, b = _block_summary(free), _block_summary(public)
    free_capture_times, free_slot_pins = _capture_identity_maps(free)
    public_capture_times, public_slot_pins = _capture_identity_maps(public)
    end_slots = range(start, end)
    rows = []
    for slot in end_slots:
        left = free_status[slot]
        right = public_status[slot]
        left_summary = a.get(slot)
        right_summary = b.get(slot)
        left_complete = left_summary is not None and left_summary["status"] == "complete"
        right_complete = right_summary is not None and right_summary["status"] == "complete"
        normalized_left = (
            {k: v for k, v in left_summary.items() if k != "available_ns"}
            if left_complete
            else None
        )
        normalized_right = (
            {k: v for k, v in right_summary.items() if k != "available_ns"}
            if right_complete
            else None
        )
        left_transport_absent = left["status"] in {"failed", "stopped"}
        right_transport_absent = right["status"] in {"failed", "stopped"}
        provider_statuses = {
            "alchemy": dict(
                status=left["status"],
                reason=left["reason"],
                transport_status=left["transport_status"],
                disposition=left["observation_disposition"],
                enumeration_relation=left["enumeration_relation"],
                lineage_status=left["lineage_status"],
                available_ns=(left_summary or {}).get("available_ns"),
            ),
            "solana_public": dict(
                status=right["status"],
                reason=right["reason"],
                transport_status=right["transport_status"],
                disposition=right["observation_disposition"],
                enumeration_relation=right["enumeration_relation"],
                lineage_status=right["lineage_status"],
                available_ns=(right_summary or {}).get("available_ns"),
            ),
        }
        row_available = max(
            (
                value
                for value in (
                    (left_summary or {}).get("available_ns"),
                    (right_summary or {}).get("available_ns"),
                )
                if value is not None
            ),
            default=None,
        )
        block_conflict = left_complete and right_complete and normalized_left != normalized_right
        if left_complete and right_complete:
            if block_conflict:
                status_name = "conflict"
                positive_provider = None
            else:
                status_name = "complete"
                positive_provider = None
        elif left_complete and right_transport_absent:
            status_name = "partial"
            positive_provider = "alchemy"
        elif right_complete and left_transport_absent:
            status_name = "partial"
            positive_provider = "solana_public"
        elif left_complete or right_complete:
            status_name = "conflict"
            positive_provider = None
        else:
            positive_provider = None
            statuses = {left["status"], right["status"]}
            status_name = next(
                (
                    candidate
                    for candidate in ("stopped", "failed", "unsupported", "unknown")
                    if candidate in statuses
                ),
                "unknown",
            )

        absence_proof = (
            enumeration_agreement
            and left["status"] != "complete"
            and right["status"] != "complete"
            and left["enumeration_relation"] == right["enumeration_relation"] == "unlisted"
            and left["observation_disposition"]
            == right["observation_disposition"]
            == "block_absent_confirmed"
            and left["lineage_status"] == right["lineage_status"] == "linked"
            and (
                left_summary is None
                or left_summary["status"] in {"null_result", "failed", "unsupported"}
            )
            and (
                right_summary is None
                or right_summary["status"] in {"null_result", "failed", "unsupported"}
            )
        )
        if absence_proof:
            status_name = "skipped"
            positive_provider = None
        if absence_proof:
            free_proof_pins = set(left["evidence"])
            public_proof_pins = set(right["evidence"])
            if (
                not free_proof_pins
                or not public_proof_pins
                or not free_proof_pins <= set(free_capture_times)
                or not public_proof_pins <= set(public_capture_times)
            ):
                raise ValueError("block_reconciliation_absence_dependencies")
            proof_evidence = sorted(free_proof_pins | public_proof_pins)
            support_proof_available_ns = max(
                [free_capture_times[pin] for pin in free_proof_pins]
                + [public_capture_times[pin] for pin in public_proof_pins]
            )
        else:
            proof_evidence = []
            support_proof_available_ns = None
        if status_name == "complete":
            proof_evidence = sorted({free_slot_pins[slot], public_slot_pins[slot]})
            support_proof_available_ns = max(
                free_capture_times[free_slot_pins[slot]],
                public_capture_times[public_slot_pins[slot]],
            )
        elif status_name == "partial":
            provider_pins = free_slot_pins if positive_provider == "alchemy" else public_slot_pins
            provider_times = (
                free_capture_times if positive_provider == "alchemy" else public_capture_times
            )
            proof_evidence = [provider_pins[slot]]
            support_proof_available_ns = provider_times[provider_pins[slot]]
        rows.append(
            dict(
                slot=slot,
                status=status_name,
                available_ns=row_available,
                disposition=(
                    "block_absent_confirmed"
                    if absence_proof
                    else "block_present"
                    if status_name in {"complete", "partial"}
                    else "block_presence_unresolved"
                ),
                reason=(
                    "bilaterally_witnessed_absence"
                    if absence_proof
                    else "bilateral_full_block_match"
                    if status_name == "complete"
                    else "single_provider_positive_peer_failure_or_stop"
                    if status_name == "partial"
                    else "provider_block_conflict"
                    if status_name == "conflict"
                    else ";".join(sorted({left["reason"], right["reason"]}))
                ),
                enumeration_relation=(
                    "unlisted"
                    if absence_proof
                    else left["enumeration_relation"]
                    if left["enumeration_relation"] == right["enumeration_relation"]
                    else "mismatch"
                ),
                enumeration_status=(
                    "complete"
                    if enumeration_agreement
                    else "mismatch"
                    if replay["enumeration_status"]
                    == public_replay["enumeration_status"]
                    == "complete"
                    else replay["enumeration_status"]
                    if replay["enumeration_status"] == public_replay["enumeration_status"]
                    else "mismatch"
                ),
                transport_status=(
                    "captured_result"
                    if status_name == "complete"
                    else "provider_error"
                    if status_name == "partial"
                    else left["transport_status"]
                    if left["transport_status"] == right["transport_status"]
                    else "provider_status_mismatch"
                ),
                lineage_status=(
                    "linked"
                    if absence_proof
                    else left["lineage_status"]
                    if left["lineage_status"] == right["lineage_status"]
                    else "not_agreed"
                ),
                proof_available_ns=support_proof_available_ns,
                proof_evidence=proof_evidence,
                evidence=sorted(set(left["evidence"]) | set(right["evidence"])),
                providers=provider_statuses,
                positive_provider=positive_provider,
                block_identity_pin=(
                    digest(normalized_left)
                    if status_name == "complete"
                    else digest(normalized_left)
                    if positive_provider == "alchemy"
                    else digest(normalized_right)
                    if positive_provider == "solana_public"
                    else None
                ),
            )
        )
    resolved_slots = sum(row["status"] in {"complete", "skipped"} for row in rows)
    unresolved_slots = len(rows) - resolved_slots
    left_witness = free["plan"]["left_witness_slot"]
    right_witness = free["plan"]["right_witness_slot"]
    return dict(
        free=free,
        public=public,
        inventory=inventory,
        rows=rows,
        witnesses=[
            a.get(left_witness),
            b.get(left_witness),
            a.get(right_witness),
            b.get(right_witness),
        ],
        accounting=dict(
            reconciled=resolved_slots,
            unresolved=unresolved_slots,
        ),
    )


def publish_block_reconciliation(store, free_root, public_root, inventory_store, inventory_pin):
    """Publish an immutable, bounded cross-provider agreement receipt; no provider calls."""
    if not isinstance(inventory_pin, str) or not PIN.fullmatch(inventory_pin):
        raise ValueError("block_reconciliation_inventory_pin")
    replay = _replay(Path(free_root), Path(public_root), Path(inventory_store), inventory_pin)
    store = safe_path(store)
    store.mkdir(parents=True, exist_ok=True)
    if store.resolve() == Path(inventory_store).resolve():
        raise ValueError("block_reconciliation_distinct_inventory_store")
    safe_path(store / "inputs").mkdir(parents=True, exist_ok=True)
    with lock(store / ".block-reconciliation-inputs.lock"):
        free_ref = f"inputs/free-{replay['free']['source_pin']}"
        public_ref = f"inputs/public-{replay['public']['source_pin']}"
        sealed_free = _copy_provider_root(
            replay["free"]["root"], store / free_ref, "free", replay["free"]["source_pin"]
        )
        sealed_public = _copy_provider_root(
            replay["public"]["root"], store / public_ref, "public", replay["public"]["source_pin"]
        )
        inventory_replica = safe_path(store / "inventory")
        block_inventory.replicate_finalized_block_inventory(
            Path(inventory_store), inventory_replica, inventory_pin
        )
        replay = _replay(sealed_free, sealed_public, inventory_replica, inventory_pin)
    receipt = dict(
        version="solana-cross-provider-block-reconciliation-v2",
        capability=CAPABILITY,
        trust_model=TRUST_MODEL,
        qualification_scope="provider_agreement_for_explicit_bounded_finalized_block_range_only",
        scope_definition="full_blocks_bilaterally_witnessed_skips_and_unresolved_slot_status_v2",
        market_support_claims=False,
        completeness_claim="no_market_or_cryptographic_exhaustiveness_claim",
        interval=dict(
            start_slot=replay["free"]["manifest"]["start_slot"],
            end_slot=replay["free"]["manifest"]["end_slot"],
            convention=replay["free"]["manifest"]["interval_convention"],
        ),
        process_pin=digest(
            dict(
                methods=["getBlocks", "getBlock"],
                block_options=block_inventory.BLOCK_OPTIONS,
                interval_convention="half-open-start-inclusive-end-exclusive-v1",
                witness_policy="explicit-left-and-right-witnesses",
                skip_policy="both_provider_getblocks_omit_null_getblock_and_linked_parent_chain_v1",
            )
        ),
        providers=[
            dict(
                alias="alchemy",
                account_alias=replay["free"]["run"]["account_alias"],
                endpoint_scope="solana_mainnet",
                source_pin=replay["free"]["source_pin"],
                root=free_ref,
            ),
            dict(
                alias="solana-public",
                account_alias=replay["public"]["run"]["account_alias"],
                endpoint_scope="solana_mainnet",
                source_pin=replay["public"]["source_pin"],
                root=public_ref,
            ),
        ],
        inventory=dict(
            pin=inventory_pin,
            store="inventory",
            capture_manifest_pin=replay["inventory"]["capture_manifest_pin"],
            capture_set_pin=replay["inventory"]["capture_set_pin"],
        ),
        witnesses=replay["witnesses"],
        accounting=dict(
            unit="in_range_slots",
            expected=replay["free"]["manifest"]["end_slot"]
            - replay["free"]["manifest"]["start_slot"],
            reconciled=replay["accounting"]["reconciled"],
            unresolved=replay["accounting"]["unresolved"],
        ),
        pages=[],
        verifier_id="solana_cross_provider_block_reconciliation_v2",
        verifier_code_pin=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        resource_bounds=dict(
            max_range_slots=MAX_RANGE,
            max_capture_file_bytes=MAX_FILE_BYTES,
            max_capture_bundle_bytes=MAX_BUNDLE_BYTES,
            max_total_dependency_bytes=MAX_TOTAL_DEPENDENCY_BYTES,
            page_size=PAGE_SIZE,
        ),
    )
    pages = [replay["rows"][i : i + PAGE_SIZE] for i in range(0, len(replay["rows"]), PAGE_SIZE)]
    receipt["pages"] = [dict(pin=digest(page), count=len(page)) for page in pages]
    pin = digest(receipt)
    with lock(store / ".block-reconciliation-receipt.lock"):
        page_dir = safe_path(store / _PAGES)
        page_dir.mkdir(parents=True, exist_ok=True)
        for page, ref in zip(pages, receipt["pages"], strict=True):
            atomic_json(page_dir / f"{ref['pin']}.json", page)
        receipt_dir = safe_path(store / _RECEIPTS)
        receipt_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(receipt_dir / f"{pin}.json", receipt)
        sync_dir(receipt_dir)
    load_block_reconciliation(store, pin)
    return pin


def load_block_reconciliation(store, pin):
    """Revalidate receipt pages and replay all pinned raw dependencies read-only."""
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        raise ValueError("block_reconciliation_receipt_pin")
    store = safe_path(store)
    receipt_path = safe_path(store / _RECEIPTS / f"{pin}.json")
    receipt = _load_json(receipt_path)
    if (
        digest(receipt) != pin
        or receipt.get("capability") != CAPABILITY
        or receipt.get("trust_model") != TRUST_MODEL
    ):
        raise ValueError("block_reconciliation_receipt_checksum")
    if receipt.get("verifier_code_pin") != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError("block_reconciliation_verifier_code")
    if receipt.get("resource_bounds") != dict(
        max_range_slots=MAX_RANGE,
        max_capture_file_bytes=MAX_FILE_BYTES,
        max_capture_bundle_bytes=MAX_BUNDLE_BYTES,
        max_total_dependency_bytes=MAX_TOTAL_DEPENDENCY_BYTES,
        page_size=PAGE_SIZE,
    ):
        raise ValueError("block_reconciliation_resource_bounds")
    expected_process_pin = digest(
        dict(
            methods=["getBlocks", "getBlock"],
            block_options=block_inventory.BLOCK_OPTIONS,
            interval_convention="half-open-start-inclusive-end-exclusive-v1",
            witness_policy="explicit-left-and-right-witnesses",
            skip_policy="both_provider_getblocks_omit_null_getblock_and_linked_parent_chain_v1",
        )
    )
    if (
        receipt.get("version") != "solana-cross-provider-block-reconciliation-v2"
        or receipt.get("qualification_scope")
        != "provider_agreement_for_explicit_bounded_finalized_block_range_only"
        or receipt.get("scope_definition")
        != "full_blocks_bilaterally_witnessed_skips_and_unresolved_slot_status_v2"
        or receipt.get("market_support_claims") is not False
        or receipt.get("completeness_claim") != "no_market_or_cryptographic_exhaustiveness_claim"
        or receipt.get("process_pin") != expected_process_pin
        or receipt.get("verifier_id") != "solana_cross_provider_block_reconciliation_v2"
    ):
        raise ValueError("block_reconciliation_receipt_contract")
    page_rows = []
    refs = receipt.get("pages")
    if not isinstance(refs, list) or len(refs) > (MAX_RANGE + PAGE_SIZE - 1) // PAGE_SIZE:
        raise ValueError("block_reconciliation_page_refs")
    for ref in refs:
        if (
            not isinstance(ref, dict)
            or set(ref) != {"pin", "count"}
            or not isinstance(ref["pin"], str)
            or not PIN.fullmatch(ref["pin"])
            or type(ref["count"]) is not int
            or not 1 <= ref["count"] <= PAGE_SIZE
        ):
            raise ValueError("block_reconciliation_page_ref")
        page = _load_json(store / _PAGES / f"{ref['pin']}.json")
        if digest(page) != ref["pin"] or len(page) != ref["count"] or len(page) > PAGE_SIZE:
            raise ValueError("block_reconciliation_page_checksum")
        page_rows.extend(page)
    providers = receipt.get("providers")
    if not isinstance(providers, list) or len(providers) != 2:
        raise ValueError("block_reconciliation_provider_refs")
    roots = [_store_path(store, p["root"]) for p in providers]
    inventory_store = _store_path(store, receipt["inventory"]["store"])
    replay = _replay(roots[0], roots[1], inventory_store, receipt["inventory"]["pin"])
    start = replay["free"]["manifest"]["start_slot"]
    end = replay["free"]["manifest"]["end_slot"]
    if (
        receipt.get("interval")
        != dict(
            start_slot=start, end_slot=end, convention="half-open-start-inclusive-end-exclusive-v1"
        )
        or receipt.get("inventory")
        != dict(
            pin=receipt["inventory"].get("pin"),
            store=receipt["inventory"].get("store"),
            capture_manifest_pin=replay["inventory"]["capture_manifest_pin"],
            capture_set_pin=replay["inventory"]["capture_set_pin"],
        )
        or receipt.get("accounting")
        != dict(
            unit="in_range_slots",
            expected=end - start,
            reconciled=replay["accounting"]["reconciled"],
            unresolved=replay["accounting"]["unresolved"],
        )
    ):
        raise ValueError("block_reconciliation_receipt_scope")
    expected = dict(
        providers=[
            dict(
                alias="alchemy",
                account_alias=replay["free"]["run"]["account_alias"],
                endpoint_scope="solana_mainnet",
                source_pin=replay["free"]["source_pin"],
                root=f"inputs/free-{replay['free']['source_pin']}",
            ),
            dict(
                alias="solana-public",
                account_alias=replay["public"]["run"]["account_alias"],
                endpoint_scope="solana_mainnet",
                source_pin=replay["public"]["source_pin"],
                root=f"inputs/public-{replay['public']['source_pin']}",
            ),
        ],
        rows=replay["rows"],
        witnesses=replay["witnesses"],
    )
    if (
        providers != expected["providers"]
        or page_rows != expected["rows"]
        or receipt["witnesses"] != expected["witnesses"]
    ):
        raise ValueError("block_reconciliation_dependency_replay")
    expected_refs = [
        dict(
            pin=digest(expected["rows"][i : i + PAGE_SIZE]),
            count=len(expected["rows"][i : i + PAGE_SIZE]),
        )
        for i in range(0, len(expected["rows"]), PAGE_SIZE)
    ]
    if receipt["pages"] != expected_refs:
        raise ValueError("block_reconciliation_page_schedule")
    return receipt
