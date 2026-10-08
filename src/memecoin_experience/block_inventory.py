"""Offline finalized-block inventory receipts; never market support or price evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from memecoin_ai.sources import Capture

from .storage import (
    PIN,
    atomic_json,
    canonical,
    digest,
    lock,
    safe_path,
    sync_dir,
)

MAX_RANGE_SLOTS = 256
MAX_CAPTURE_BYTES = 64_000_000
MAX_CAPTURE_FILE_BYTES = 12_000_000
MAX_FIXTURE_BYTES = 1_000_000
MAX_CAPTURE_PATH_CHARS = 256
MAX_BOUNDARY_DISTANCE = 256
MAX_CAPTURE_BUNDLE_FILES = 2 * (MAX_RANGE_SLOTS + 3) + 1
LEDGER_PAGE_SIZE = 128
CAPABILITY = "finalized_block_inventory"
STATUSES = {"complete", "skipped", "failed", "stopped", "unknown", "unsupported"}
BLOCK_OPTIONS = {
    "encoding": "jsonParsed",
    "commitment": "finalized",
    "transactionDetails": "full",
    "rewards": False,
    "maxSupportedTransactionVersion": 1,
}


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _module_pin() -> str:
    return _sha256(Path(__file__))


def _load_manifest(root: Path) -> dict:
    path = safe_path(root / "fixture.json")
    if not path.is_file() or path.stat().st_size > MAX_FIXTURE_BYTES:
        raise ValueError("block_inventory_fixture_byte_bound")
    value = json.loads(path.read_text())
    required = {
        "version",
        "start_slot",
        "end_slot",
        "interval_convention",
        "captures",
        "stopped_slots",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("block_inventory_fixture_schema")
    start, end = value["start_slot"], value["end_slot"]
    if (
        value["version"] != "solana-block-range-fixture-v1"
        or type(start) is not int
        or type(end) is not int
        or start < 1
        or end <= start
        or end - start > MAX_RANGE_SLOTS
        or value["interval_convention"] != "half-open-start-inclusive-end-exclusive-v1"
        or not isinstance(value["captures"], list)
        or not value["captures"]
        or any(not isinstance(row, dict) for row in value["captures"])
        or not isinstance(value["stopped_slots"], list)
        or any(type(s) is not int or not start <= s < end for s in value["stopped_slots"])
        or value["stopped_slots"] != sorted(set(value["stopped_slots"]))
    ):
        raise ValueError("block_inventory_fixture_bounds")
    return value


def _capture(root: Path, row: dict, previous_received: int | None):
    if not isinstance(row, dict) or set(row) - {"purpose", "slot", "side", "path"}:
        raise ValueError("block_inventory_capture_ref")
    purpose, relative = row.get("purpose"), row.get("path")
    if (
        purpose not in {"enumeration", "slot", "boundary"}
        or not isinstance(relative, str)
        or len(relative) > MAX_CAPTURE_PATH_CHARS
        or "\\" in relative
        or relative.startswith("/")
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("block_inventory_capture_ref")
    allowed = {
        "enumeration": {"purpose", "path"},
        "slot": {"purpose", "slot", "path"},
        "boundary": {"purpose", "slot", "side", "path"},
    }[purpose]
    if set(row) != allowed:
        raise ValueError("block_inventory_capture_ref")
    path = safe_path(root / relative).resolve()
    if not path.is_relative_to(root) or path.is_symlink() or not path.is_dir():
        raise ValueError("block_inventory_capture_path")
    for name in ("receipt.json", "response.json"):
        file_path = safe_path(path / name)
        if (
            file_path.is_symlink()
            or not file_path.is_file()
            or file_path.stat().st_size > MAX_CAPTURE_FILE_BYTES
        ):
            raise ValueError("block_inventory_capture_file_bound")
    metadata = json.loads((path / "receipt.json").read_text())
    capture = Capture.load(path)
    provenance = metadata.get("http_provenance")
    if capture.source_alias.startswith("synthetic-"):
        if provenance is not None:
            raise ValueError("block_inventory_synthetic_provenance")
    elif capture.source_alias == "alchemy-free-finalized-block-range-v1":
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
            raise ValueError("block_inventory_http_provenance")
        status = provenance["http_status"]
        headers = provenance["response_headers"]
        if (
            provenance["version"] != "solana-http-provenance-v1"
            or provenance["endpoint_origin"] != "https://solana-mainnet.g.alchemy.com"
            or provenance["account_alias"] != "alchemy-free-local"
            or status is not None
            and (type(status) is not int or not 100 <= status <= 599)
            or not isinstance(headers, dict)
            or set(headers)
            - {"content-type", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining"}
            or any(
                not isinstance(key, str) or not isinstance(value, str) or len(value) > 128
                for key, value in headers.items()
            )
            or provenance["retry_count"] != 0
            or provenance["attempt_number"] != 1
            or provenance["transport_error_class"] is not None
            and not isinstance(provenance["transport_error_class"], str)
            or type(provenance["response_truncated"]) is not bool
        ):
            raise ValueError("block_inventory_http_provenance")
    else:
        raise ValueError("block_inventory_source_alias")
    if previous_received is not None and capture.request_started_ns < previous_received:
        raise ValueError("block_inventory_capture_clock_order")
    request = json.loads(capture.request_json)
    if request.get("jsonrpc") != "2.0" or request.get("id") != 1:
        raise ValueError("block_inventory_request_envelope")
    if capture.method != request.get("method"):
        raise ValueError("block_inventory_request_method")
    return path, capture, request, capture.received_ns, provenance


def _response(capture: Capture, provenance=None):
    if provenance is not None and (
        provenance["transport_error_class"] is not None
        or provenance["http_status"] != 200
        or provenance["response_truncated"]
    ):
        return "failed", None
    try:
        value = json.loads(capture.response)
    except (UnicodeDecodeError, json.JSONDecodeError):
        if provenance is not None:
            return "failed", None
        raise ValueError("block_inventory_response_envelope") from None
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0" or value.get("id") != 1:
        if provenance is not None:
            return "failed", None
        raise ValueError("block_inventory_response_envelope")
    if "error" in value:
        error = value["error"]
        if not isinstance(error, dict) or type(error.get("code")) is not int:
            raise ValueError("block_inventory_rpc_error")
        code = error["code"]
        return "unsupported" if code in {-32601, -32602} else "failed", None
    if "result" not in value:
        if provenance is not None:
            return "failed", None
        raise ValueError("block_inventory_response_result")
    return "result", value["result"]


def _capture_content_pin(path: Path) -> str:
    receipt, body = path / "receipt.json", path / "response.json"
    if not receipt.is_file() or not body.is_file():
        raise ValueError("block_inventory_capture_files")
    return digest({"receipt_sha256": _sha256(receipt), "response_sha256": _sha256(body)})


def _validate(root: Path, *, expected_receipt: dict | None = None, page_dir: Path | None = None):
    root = safe_path(root.resolve())
    manifest = _load_manifest(root)
    start, end = manifest["start_slot"], manifest["end_slot"]
    captures = manifest["captures"]
    if not 1 <= len(captures) <= end - start + 3:
        raise ValueError("block_inventory_capture_count")
    if not captures or captures[0].get("purpose") != "enumeration":
        raise ValueError("block_inventory_enumeration_order")
    total_bytes = 0
    total_bytes += (root / "fixture.json").stat().st_size
    if total_bytes > MAX_CAPTURE_BYTES:
        raise ValueError("block_inventory_capture_byte_bound")
    previous_received = None
    seen_paths = set()
    slot_capture_rows = {}
    boundary_rows = {}
    enumeration = None
    capture_pins = []
    capture_kinds = set()
    account_aliases = set()
    provenance_by_path = {}
    for index, row in enumerate(captures):
        path, cap, request, previous_received, provenance = _capture(root, row, previous_received)
        kind = (
            "synthetic_solana_jsonrpc_capture_v1"
            if provenance is None
            else "alchemy_free_http_capture_v1"
        )
        capture_kinds.add(kind)
        if provenance is not None:
            account_aliases.add(provenance["account_alias"])
        provenance_by_path[row["path"]] = provenance
        relative = row["path"]
        if relative in seen_paths:
            raise ValueError("block_inventory_duplicate_capture_path")
        seen_paths.add(relative)
        total_bytes += (path / "response.json").stat().st_size + (
            path / "receipt.json"
        ).stat().st_size
        if total_bytes > MAX_CAPTURE_BYTES:
            raise ValueError("block_inventory_capture_byte_bound")
        capture_pin = _capture_content_pin(path)
        capture_pins.append({"path": relative, "pin": capture_pin})
        if index == 0:
            enumeration = (relative, request, capture_pin)
        elif row["purpose"] == "slot":
            slot = row.get("slot")
            if type(slot) is not int or not start <= slot < end or slot in slot_capture_rows:
                raise ValueError("block_inventory_slot_capture_ref")
            slot_capture_rows[slot] = (relative, request, capture_pin)
        elif row["purpose"] == "boundary":
            side, slot = row.get("side"), row.get("slot")
            bounded = type(slot) is int and (
                side == "left"
                and start - MAX_BOUNDARY_DISTANCE <= slot < start
                or side == "right"
                and end <= slot < end + MAX_BOUNDARY_DISTANCE
            )
            if not bounded or side in boundary_rows:
                raise ValueError("block_inventory_boundary_capture_ref")
            boundary_rows[side] = (slot, relative, request, capture_pin)
        else:
            raise ValueError("block_inventory_capture_order")
    if enumeration is None:
        raise ValueError("block_inventory_enumeration_missing")
    if len(capture_kinds) != 1 or len(account_aliases) > 1:
        raise ValueError("block_inventory_mixed_capture_sources")

    enum_path, enum_request, enum_pin = enumeration
    enum_cap = Capture.load(root / enum_path)
    if (
        enum_cap.method != "getBlocks"
        or enum_request.get("method") != "getBlocks"
        or enum_request.get("params") != [start, end - 1, {"commitment": "finalized"}]
    ):
        raise ValueError("block_inventory_range_request")
    response_state, listed = _response(enum_cap, provenance_by_path[enum_path])
    if response_state == "result" and listed is None:
        enum_status = "unknown"
        listed = []
    elif response_state == "result":
        if (
            not isinstance(listed, list)
            or any(type(slot) is not int or not start <= slot < end for slot in listed)
            or listed != sorted(set(listed))
        ):
            raise ValueError("block_inventory_enumeration_shape")
        enum_status = "complete"
    else:
        enum_status = response_state
        listed = []
    listed_set = set(listed)
    if set(manifest["stopped_slots"]) & set(slot_capture_rows):
        raise ValueError("block_inventory_stopped_attempt_conflict")

    # SQLite stores the slot ledger; capture bodies are reopened individually and output pages
    # are bounded by LEDGER_PAGE_SIZE.
    with tempfile.TemporaryDirectory(prefix="block-inventory-") as scratch:
        db = sqlite3.connect(Path(scratch) / "ledger.sqlite")
        db.execute("pragma cache_size=-2048")
        db.execute("pragma temp_store=FILE")
        db.execute(
            "create table ledger(slot integer primary key,status text,reason text,"
            "enumeration_relation text,lineage_status text,evidence text)"
        )
        for slot in range(start, end):
            if slot in manifest["stopped_slots"]:
                status, reason, relation = "stopped", "acquisition_stopped", "unavailable"
            elif enum_status != "complete":
                status, reason, relation = "unknown", f"enumeration_{enum_status}", "unavailable"
            else:
                status, reason, relation = "unknown", "unresolved_or_unlisted", "unlisted"
            db.execute(
                "insert into ledger values(?,?,?,?,?,?)",
                (slot, status, reason, relation, "unresolved", "[]"),
            )

        valid_blocks = {}
        for slot in sorted(slot_capture_rows):
            relative, request, pin = slot_capture_rows[slot]
            cap = Capture.load(root / relative)
            if (
                cap.method != "getBlock"
                or request.get("method") != "getBlock"
                or request.get("params") != [slot, BLOCK_OPTIONS]
            ):
                raise ValueError("block_inventory_full_block_request")
            state, body = _response(cap, provenance_by_path[relative])
            if state in {"failed", "unsupported"}:
                db.execute(
                    "update ledger set status=?,reason=?,enumeration_relation=?,evidence=? "
                    "where slot=?",
                    (
                        state,
                        "getblock_rpc_error",
                        "listed"
                        if slot in listed_set
                        else "unlisted"
                        if enum_status == "complete"
                        else "unavailable",
                        canonical([pin, enum_pin]).decode(),
                        slot,
                    ),
                )
                continue
            if body is None:
                db.execute(
                    "update ledger set status='unknown',reason='getblock_null_result',"
                    "enumeration_relation=?,evidence=? where slot=?",
                    (
                        "listed"
                        if slot in listed_set
                        else "unlisted"
                        if enum_status == "complete"
                        else "unavailable",
                        canonical([pin, enum_pin]).decode(),
                        slot,
                    ),
                )
                continue
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("blockhash"), str)
                or not body["blockhash"]
                or not isinstance(body.get("previousBlockhash"), str)
                or not body["previousBlockhash"]
                or type(body.get("parentSlot")) is not int
                or not 0 <= body["parentSlot"] < slot
                or not isinstance(body.get("transactions"), list)
            ):
                raise ValueError("block_inventory_full_block_shape")
            valid_blocks[slot] = (
                body["blockhash"],
                body["previousBlockhash"],
                body["parentSlot"],
                pin,
            )
            db.execute(
                "update ledger set status='complete',reason='full_finalized_block',"
                "enumeration_relation=?,lineage_status='unresolved',evidence=? where slot=?",
                (
                    "listed"
                    if slot in listed_set
                    else "not_listed"
                    if enum_status == "complete"
                    else "unavailable",
                    canonical([pin, enum_pin]).decode(),
                    slot,
                ),
            )

        ordered = []
        if "left" in boundary_rows:
            slot, relative, request, pin = boundary_rows["left"]
            ordered.append((slot, relative, request, pin, "left"))
        ordered.extend((slot, *slot_capture_rows[slot], "inside") for slot in sorted(valid_blocks))
        if "right" in boundary_rows:
            slot, relative, request, pin = boundary_rows["right"]
            ordered.append((slot, relative, request, pin, "right"))
        parsed = []
        for slot, relative, request, pin, side in ordered:
            cap = Capture.load(root / relative)
            if cap.method != "getBlock" or request.get("params") != [slot, BLOCK_OPTIONS]:
                raise ValueError("block_inventory_boundary_request")
            state, body = _response(cap, provenance_by_path[relative])
            if state != "result" or not isinstance(body, dict):
                if side == "inside":
                    raise ValueError("block_inventory_internal_block_unavailable")
                continue
            if (
                not isinstance(body.get("blockhash"), str)
                or not body["blockhash"]
                or not isinstance(body.get("previousBlockhash"), str)
                or not body["previousBlockhash"]
                or type(body.get("parentSlot")) is not int
                or not 0 <= body["parentSlot"] < slot
                or not isinstance(body.get("transactions"), list)
            ):
                raise ValueError("block_inventory_boundary_shape")
            parsed.append(
                (slot, body["blockhash"], body["previousBlockhash"], body["parentSlot"], pin, side)
            )
        # A node may authorize a gap only after it has joined the trusted lineage rooted at a
        # captured left witness. Conflicting nodes remain positive block captures but never join.
        qualified = {}
        if parsed and parsed[0][5] == "left":
            qualified[parsed[0][0]] = parsed[0]
        for child in parsed:
            child_slot, child_hash, child_previous, child_parent, child_pin, child_side = child
            if child_side == "left":
                continue
            if (
                child_side == "inside"
                and enum_status == "complete"
                and child_slot not in listed_set
            ):
                db.execute(
                    "update ledger set lineage_status='conflict' where slot=?", (child_slot,)
                )
                continue
            parent = qualified.get(child_parent)
            if parent is None:
                # A hash/parent claim into any captured node is a conflict. An unavailable parent
                # leaves ancestry unresolved, preserving the full body without authorizing skips.
                conflicting = any(
                    row[0] == child_parent or row[1] == child_previous
                    for row in parsed
                    if row[0] < child_slot
                )
                if conflicting and start <= child_slot < end:
                    db.execute(
                        "update ledger set lineage_status='conflict' where slot=?", (child_slot,)
                    )
                continue
            parent_slot, parent_hash, _, _, parent_pin, _ = parent
            intervening = any(
                parent_slot < row[0] < child_slot for row in parsed if row[0] != child_slot
            )
            linked = child_previous == parent_hash and not intervening
            if not linked:
                if start <= child_slot < end:
                    db.execute(
                        "update ledger set lineage_status='conflict' where slot=?", (child_slot,)
                    )
                continue
            qualified[child_slot] = child
            if start <= child_slot < end:
                db.execute("update ledger set lineage_status='linked' where slot=?", (child_slot,))
            if enum_status != "complete":
                continue
            gaps = [gap for gap in range(parent_slot + 1, child_slot) if start <= gap < end]
            if any(gap in listed_set for gap in gaps):
                continue
            for gap in gaps:
                status = db.execute("select status from ledger where slot=?", (gap,)).fetchone()[0]
                attempted = gap in slot_capture_rows
                if status == "unknown" and not attempted:
                    db.execute(
                        "update ledger set status='skipped',reason='parent_link_gap',"
                        "lineage_status='linked',evidence=? where slot=?",
                        (canonical([parent_pin, child_pin, enum_pin]).decode(), gap),
                    )

        counts = {status: 0 for status in sorted(STATUSES)}
        page_refs = []
        page_rows = []
        for row in db.execute(
            "select slot,status,reason,enumeration_relation,lineage_status,evidence "
            "from ledger order by slot"
        ):
            status = row[1]
            if status not in STATUSES:
                raise ValueError("block_inventory_ledger_status")
            counts[status] += 1
            page_rows.append(
                dict(
                    slot=row[0],
                    status=status,
                    reason=row[2],
                    enumeration_relation=row[3],
                    lineage_status=row[4],
                    evidence=json.loads(row[5]),
                )
            )
            if len(page_rows) == LEDGER_PAGE_SIZE:
                page_refs.append(_seal_page(page_rows, page_dir, expected_receipt, len(page_refs)))
                page_rows = []
        if page_rows:
            page_refs.append(_seal_page(page_rows, page_dir, expected_receipt, len(page_refs)))
        db.close()

    capture_set_pin = digest({"manifest_pin": digest(manifest), "captures": capture_pins})
    if expected_receipt is not None:
        expected = expected_receipt
        if (
            expected["capture_manifest_pin"] != digest(manifest)
            or expected["capture_set_pin"] != capture_set_pin
            or expected["source_kind"] != next(iter(capture_kinds))
            or expected["source_account_alias"]
            != (next(iter(account_aliases)) if len(account_aliases) == 1 else None)
            or expected["enumeration_status"] != enum_status
            or expected["ledger_counts"] != counts
            or expected["ledger_pages"] != page_refs
        ):
            raise ValueError("block_inventory_replay_mismatch")
    return dict(
        manifest=manifest,
        source_kind=next(iter(capture_kinds)) if len(capture_kinds) == 1 else "mixed",
        source_account_alias=next(iter(account_aliases)) if len(account_aliases) == 1 else None,
        manifest_pin=digest(manifest),
        capture_set_pin=capture_set_pin,
        capture_pins=capture_pins,
        ledger_counts=counts,
        ledger_pages=page_refs,
        enumeration_pin=enum_pin,
        enumeration_status=enum_status,
    )


def _seal_page(rows, page_dir, expected_receipt, page_index):
    pin = digest(rows)
    reference = dict(pin=pin, count=len(rows))
    if page_dir is not None and expected_receipt is None:
        page_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(page_dir / f"{pin}.json", rows)
    if expected_receipt is not None:
        refs = expected_receipt["ledger_pages"]
        if page_index >= len(refs) or refs[page_index] != reference:
            raise ValueError("block_inventory_ledger_page")
        path = page_dir / f"{pin}.json"
        page = json.loads(path.read_text())
        if digest(page) != pin or page != rows:
            raise ValueError("block_inventory_ledger_page_checksum")
    return reference


def _tree_inventory(root: Path):
    inventory = {}
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        safe_path(path)
        if path.is_symlink():
            raise ValueError("block_inventory_capture_bundle_symlink")
        if not path.is_file() or path.name == "manifest.json":
            continue
        size = path.stat().st_size
        if path.name == "fixture.json":
            if size > MAX_FIXTURE_BYTES:
                raise ValueError("block_inventory_fixture_byte_bound")
        elif size > MAX_CAPTURE_FILE_BYTES:
            raise ValueError("block_inventory_capture_file_bound")
        total_bytes += size
        if len(inventory) >= MAX_CAPTURE_BUNDLE_FILES or total_bytes > MAX_CAPTURE_BYTES:
            raise ValueError("block_inventory_capture_bundle_bound")
        inventory[path.relative_to(root).as_posix()] = _sha256(path)
    return inventory


def _verify_capture_tree(root: Path):
    root = safe_path(root)
    manifest_path = safe_path(root / "manifest.json")
    manifest = json.loads(manifest_path.read_text())
    actual = _tree_inventory(root)
    if manifest != actual:
        raise ValueError("block_inventory_capture_bundle_seal")
    return manifest


def _seal_capture_tree(root: Path):
    atomic_json(root / "manifest.json", _tree_inventory(root))


def _durable_write(path: Path, value: bytes):
    path = safe_path(path)
    with path.open("wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)


def _sync_tree_dirs(root: Path):
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        sync_dir(safe_path(directory))
    sync_dir(safe_path(root))


def _clear_stale_staging(stage_root: Path):
    for path in stage_root.glob("block-inventory-*"):
        if path.is_symlink():
            raise ValueError("block_inventory_staging_symlink")
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def _publish_capture_bundle(store: Path, source: Path, manifest: dict, capture_set_pin: str):
    stage_root = safe_path(store / "staging")
    stage_root.mkdir(parents=True, exist_ok=True)
    target_parent = safe_path(store / "qualification-block-captures")
    target_parent.mkdir(parents=True, exist_ok=True)
    os.chmod(stage_root, 0o700)
    os.chmod(target_parent, 0o700)
    sync_dir(store)
    sync_dir(stage_root.parent)
    target = safe_path(target_parent / capture_set_pin)
    _clear_stale_staging(stage_root)
    stage = Path(tempfile.mkdtemp(prefix="block-inventory-", dir=stage_root))
    try:
        _durable_write(stage / "fixture.json", (source / "fixture.json").read_bytes())
        for capture in manifest["captures"]:
            relative = capture["path"]
            src_dir = safe_path(source / relative)
            dst_dir = safe_path(stage / relative)
            dst_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(dst_dir, 0o700)
            for filename in ("receipt.json", "response.json"):
                src = safe_path(src_dir / filename)
                if not src.is_file() or src.is_symlink():
                    raise ValueError("block_inventory_capture_file")
                dst = safe_path(dst_dir / filename)
                _durable_write(dst, src.read_bytes())
        _sync_tree_dirs(stage)
        _seal_capture_tree(stage)
        _sync_tree_dirs(stage)
        staged_inventory = _verify_capture_tree(stage)
        if target.exists():
            if _verify_capture_tree(target) != staged_inventory:
                raise ValueError("block_inventory_immutable_conflict")
            return target
        os.rename(stage, target)
        sync_dir(target_parent)
        return target
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def publish_finalized_block_inventory(store, capture_root):
    """Publish one bounded offline capture partition with only inventory capability."""
    store = safe_path(store)
    source = safe_path(capture_root).resolve()
    result = _validate(source)
    store.mkdir(parents=True, exist_ok=True)
    with lock(store / ".block-inventory.lock"):
        stage_root = safe_path(store / "staging")
        stage_root.mkdir(parents=True, exist_ok=True)
        _clear_stale_staging(stage_root)
        capture_bundle = _publish_capture_bundle(
            store, source, result["manifest"], result["capture_set_pin"]
        )
        result = _validate(capture_bundle, page_dir=store / "qualification-block-inventory-pages")
        if result["capture_set_pin"] != digest(
            {"manifest_pin": result["manifest_pin"], "captures": result["capture_pins"]}
        ):
            raise ValueError("block_inventory_capture_set_pin")
        counts = result["ledger_counts"]
        interval_size = result["manifest"]["end_slot"] - result["manifest"]["start_slot"]
        if sum(counts.values()) != interval_size:
            raise ValueError("block_inventory_slot_reconciliation")
        receipt = dict(
            version="solana-finalized-block-inventory-v1",
            source_kind=result["source_kind"],
            source_account_alias=result["source_account_alias"],
            interval=dict(
                start_slot=result["manifest"]["start_slot"],
                end_slot=result["manifest"]["end_slot"],
                convention=result["manifest"]["interval_convention"],
            ),
            time_basis="slot_only_no_unix_time_conversion",
            process=dict(
                version="finalized-block-inventory-process-v1",
                enumeration_method="getBlocks",
                block_method="getBlock",
                block_options=BLOCK_OPTIONS,
                block_options_pin=digest(BLOCK_OPTIONS),
                commitment="finalized",
                transaction_details="full",
            ),
            trust_model="provider_asserted_jsonrpc_not_cryptographic_exhaustiveness_proof_v1",
            process_pin=digest(
                dict(
                    version="finalized-block-inventory-process-v1",
                    enumeration_method="getBlocks",
                    block_method="getBlock",
                    block_options=BLOCK_OPTIONS,
                    block_options_pin=digest(BLOCK_OPTIONS),
                    commitment="finalized",
                    transaction_details="full",
                )
            ),
            capture_manifest_pin=result["manifest_pin"],
            capture_set_pin=result["capture_set_pin"],
            capture_pins=result["capture_pins"],
            enumeration_pin=result["enumeration_pin"],
            ledger_counts=counts,
            ledger_pages=result["ledger_pages"],
            accounting_unit="slot_numbers",
            expected_slots=interval_size,
            accounted_slots=sum(counts.values()),
            unresolved_slots=sum(
                counts[s] for s in ("failed", "stopped", "unknown", "unsupported")
            ),
            complete_definition="captured_full_block_only_no_interval_claim",
            resource_bounds=dict(
                max_range_slots=MAX_RANGE_SLOTS,
                max_capture_file_bytes=MAX_CAPTURE_FILE_BYTES,
                max_capture_bundle_bytes=MAX_CAPTURE_BYTES,
                max_fixture_bytes=MAX_FIXTURE_BYTES,
                max_capture_path_chars=MAX_CAPTURE_PATH_CHARS,
                max_boundary_distance=MAX_BOUNDARY_DISTANCE,
                max_capture_bundle_files=MAX_CAPTURE_BUNDLE_FILES,
                ledger_page_size=LEDGER_PAGE_SIZE,
            ),
            verifier_id="solana_block_range_inventory_v1",
            verifier_code_pin=_module_pin(),
            enumeration_status=result["enumeration_status"],
            capabilities=[CAPABILITY],
        )
        pin = digest(receipt)
        receipt_dir = safe_path(store / "qualification-block-inventories")
        receipt_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(receipt_dir / f"{pin}.json", receipt)
    load_finalized_block_inventory(store, pin)
    return pin


def load_finalized_block_inventory(store, pin):
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        raise ValueError("block_inventory_receipt_pin")
    store = safe_path(store)
    receipt = json.loads((store / "qualification-block-inventories" / f"{pin}.json").read_text())
    if digest(receipt) != pin:
        raise ValueError("block_inventory_receipt_checksum")
    required = {
        "version",
        "source_kind",
        "source_account_alias",
        "interval",
        "time_basis",
        "process",
        "trust_model",
        "process_pin",
        "capture_manifest_pin",
        "capture_set_pin",
        "capture_pins",
        "enumeration_pin",
        "ledger_counts",
        "ledger_pages",
        "accounting_unit",
        "expected_slots",
        "accounted_slots",
        "unresolved_slots",
        "complete_definition",
        "resource_bounds",
        "verifier_id",
        "verifier_code_pin",
        "enumeration_status",
        "capabilities",
    }
    if (
        set(receipt) != required
        or receipt["version"] != "solana-finalized-block-inventory-v1"
        or receipt["source_kind"]
        not in {"synthetic_solana_jsonrpc_capture_v1", "alchemy_free_http_capture_v1"}
        or receipt["source_account_alias"]
        != (
            "alchemy-free-local"
            if receipt["source_kind"] == "alchemy_free_http_capture_v1"
            else None
        )
        or receipt["trust_model"]
        != "provider_asserted_jsonrpc_not_cryptographic_exhaustiveness_proof_v1"
        or receipt["time_basis"] != "slot_only_no_unix_time_conversion"
        or receipt["capabilities"] != [CAPABILITY]
        or receipt["verifier_id"] != "solana_block_range_inventory_v1"
        or receipt["verifier_code_pin"] != _module_pin()
        or receipt["enumeration_status"] not in {"complete", "failed", "unsupported", "unknown"}
        or receipt["accounting_unit"] != "slot_numbers"
        or receipt["complete_definition"] != "captured_full_block_only_no_interval_claim"
        or receipt["resource_bounds"]
        != dict(
            max_range_slots=MAX_RANGE_SLOTS,
            max_capture_file_bytes=MAX_CAPTURE_FILE_BYTES,
            max_capture_bundle_bytes=MAX_CAPTURE_BYTES,
            max_fixture_bytes=MAX_FIXTURE_BYTES,
            max_capture_path_chars=MAX_CAPTURE_PATH_CHARS,
            max_boundary_distance=MAX_BOUNDARY_DISTANCE,
            max_capture_bundle_files=MAX_CAPTURE_BUNDLE_FILES,
            ledger_page_size=LEDGER_PAGE_SIZE,
        )
        or receipt["process_pin"] != digest(receipt["process"])
        or receipt["process"]
        != dict(
            version="finalized-block-inventory-process-v1",
            enumeration_method="getBlocks",
            block_method="getBlock",
            block_options=BLOCK_OPTIONS,
            block_options_pin=digest(BLOCK_OPTIONS),
            commitment="finalized",
            transaction_details="full",
        )
    ):
        raise ValueError("block_inventory_receipt_contract")
    capture_root = store / "qualification-block-captures" / receipt["capture_set_pin"]
    _verify_capture_tree(capture_root)
    replay = _validate(
        capture_root,
        expected_receipt=receipt,
        page_dir=store / "qualification-block-inventory-pages",
    )
    if (
        replay["manifest_pin"] != receipt["capture_manifest_pin"]
        or replay["capture_set_pin"] != receipt["capture_set_pin"]
        or replay["source_kind"] != receipt["source_kind"]
        or replay["source_account_alias"] != receipt["source_account_alias"]
        or replay["capture_pins"] != receipt["capture_pins"]
        or replay["enumeration_pin"] != receipt["enumeration_pin"]
        or replay["enumeration_status"] != receipt["enumeration_status"]
        or replay["ledger_counts"] != receipt["ledger_counts"]
        or replay["ledger_pages"] != receipt["ledger_pages"]
        or receipt["interval"]
        != dict(
            start_slot=replay["manifest"]["start_slot"],
            end_slot=replay["manifest"]["end_slot"],
            convention=replay["manifest"]["interval_convention"],
        )
        or sum(receipt["ledger_counts"].values())
        != replay["manifest"]["end_slot"] - replay["manifest"]["start_slot"]
        or receipt["expected_slots"]
        != replay["manifest"]["end_slot"] - replay["manifest"]["start_slot"]
        or receipt["accounted_slots"] != receipt["expected_slots"]
        or receipt["unresolved_slots"]
        != sum(receipt["ledger_counts"][s] for s in ("failed", "stopped", "unknown", "unsupported"))
    ):
        raise ValueError("block_inventory_receipt_replay")
    return receipt


def replicate_finalized_block_inventory(source, target, pin):
    """Copy one receipt, its ledger pages, and one bounded raw capture bundle."""
    source, target = safe_path(source), safe_path(target)
    if source == target:
        raise ValueError("block_inventory_replica_distinct_stores")
    receipt = load_finalized_block_inventory(source, pin)
    raw_source = source / "qualification-block-captures" / receipt["capture_set_pin"]
    target.mkdir(parents=True, exist_ok=True)
    with lock(target / ".block-inventory.lock"):
        stage_root = safe_path(target / "staging")
        stage_root.mkdir(parents=True, exist_ok=True)
        _clear_stale_staging(stage_root)
        raw_target = _publish_capture_bundle(
            target,
            raw_source,
            _load_manifest(raw_source),
            receipt["capture_set_pin"],
        )
        _verify_capture_tree(raw_target)
        page_dir = safe_path(target / "qualification-block-inventory-pages")
        page_dir.mkdir(parents=True, exist_ok=True)
        for reference in receipt["ledger_pages"]:
            src = safe_path(
                source / "qualification-block-inventory-pages" / f"{reference['pin']}.json"
            )
            body = json.loads(src.read_text())
            if digest(body) != reference["pin"]:
                raise ValueError("block_inventory_replica_page_checksum")
            atomic_json(page_dir / f"{reference['pin']}.json", body)
        receipt_dir = safe_path(target / "qualification-block-inventories")
        receipt_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(receipt_dir / f"{pin}.json", receipt)
    load_finalized_block_inventory(target, pin)
    return dict(receipt_pin=pin, capture_set_pin=receipt["capture_set_pin"])
