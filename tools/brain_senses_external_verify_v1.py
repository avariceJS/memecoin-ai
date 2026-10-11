"""Independent, read-only verifier for descriptor-bound external Senses v1.

This module never authorizes RPC or repairs a journal. A valid interrupted prefix
is evidence of interruption, not permission to replay its requests.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from typing import Any, cast

from tools import brain_senses_external_clock_v1 as clock_contract
from tools import brain_senses_external_schema_v1 as schema


class VerificationError(ValueError):
    """The supplied evidence does not satisfy the external writer contract."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise VerificationError(reason)


def _integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _digest(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in out, "duplicate_json_key")
        out[key] = value
    return out


def decode(payload: bytes) -> Any:
    try:
        return json.loads(
            payload, object_pairs_hook=_unique, parse_constant=lambda _: _invalid_constant()
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise VerificationError("invalid_json") from exc


def _invalid_constant() -> Any:
    raise VerificationError("nonfinite_json")


def _parts(relative: str) -> list[str]:
    _require(isinstance(relative, str) and bool(relative), "invalid_relative_path")
    parts = relative.split("/")
    _require(
        all(p and p not in {".", ".."} and "\x00" not in p and "\\" not in p for p in parts),
        "invalid_relative_path",
    )
    return parts


class DescriptorReader:
    """Nofollow reads beneath an already verified storage descriptor.

    The caller owns volume/root identity validation and its writer exclusion lock.
    This reader additionally checks the ownership/device/type/link identity of
    every traversed object. It never resolves an absolute capture pathname.
    """

    def __init__(self, root_fd: int) -> None:
        self.fd = os.dup(root_fd)
        entry = os.fstat(self.fd)
        self.device = entry.st_dev
        self.owner = os.getuid()
        try:
            self._directory(entry)
        except BaseException:
            os.close(self.fd)
            raise

    def _directory(self, entry: os.stat_result) -> None:
        _require(
            stat.S_ISDIR(entry.st_mode)
            and entry.st_uid == self.owner
            and entry.st_dev == self.device
            and stat.S_IMODE(entry.st_mode) == 0o700,
            "unsafe_directory",
        )

    def _parent(self, relative: str) -> tuple[int, str]:
        parts = _parts(relative)
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
                self._directory(os.fstat(fd))
            return fd, parts[-1]
        except BaseException:
            os.close(fd)
            raise

    def read_bytes(self, relative: str, *, limit: int = 64_000_000) -> bytes:
        parent, name = self._parent(relative)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        finally:
            os.close(parent)
        try:
            before = os.fstat(fd)
            _require(
                stat.S_ISREG(before.st_mode)
                and before.st_uid == self.owner
                and before.st_dev == self.device
                and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) in {0o400, 0o600},
                "unsafe_file",
            )
            _require(before.st_size <= limit, "file_too_large")
            chunks = []
            total = 0
            while chunk := os.read(fd, min(1024 * 1024, limit + 1 - total)):
                chunks.append(chunk)
                total += len(chunk)
                _require(total <= limit, "file_too_large")
            after = os.fstat(fd)
            _require(
                (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                    before.st_mode,
                    before.st_nlink,
                )
                == (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                    after.st_mode,
                    after.st_nlink,
                ),
                "file_changed_during_read",
            )
            return b"".join(chunks)
        finally:
            os.close(fd)

    def list_files(self) -> list[str]:
        result: list[str] = []

        def walk(fd: int, prefix: str, depth: int) -> None:
            _require(depth <= 8, "tree_too_deep")
            for name in sorted(os.listdir(fd)):
                _parts(name)
                entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                path = prefix + name
                if stat.S_ISDIR(entry.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        self._directory(os.fstat(child))
                        walk(child, path + "/", depth + 1)
                    finally:
                        os.close(child)
                else:
                    _require(stat.S_ISREG(entry.st_mode), "nonregular_tree_entry")
                    result.append(path)
                    _require(len(result) <= 1000, "tree_too_large")

        walk(self.fd, "", 0)
        return result

    def close(self) -> None:
        os.close(self.fd)

    def __enter__(self) -> DescriptorReader:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def verify_events(payload: bytes) -> list[dict[str, Any]]:
    _require(not payload or payload.endswith(b"\n"), "incomplete_event_tail")
    events: list[dict[str, Any]] = []
    previous: str | None = "0" * 64
    at_ns = 0
    for line in payload.splitlines():
        event = decode(line)
        _require(isinstance(event, dict), "event_not_object")
        _fields(
            event,
            "schema schema_version seq kind bucket at_ns payload previous_sha256 event_sha256",
        )
        _require(
            event.get("schema") == schema.EVENT_SCHEMA
            and type(event.get("schema_version")) is int
            and event.get("schema_version") == 1,
            "event_schema",
        )
        _require(
            event.get("seq") == len(events) and type(event.get("seq")) is int, "event_sequence"
        )
        _require(event.get("previous_sha256") == previous, "event_chain")
        _require(
            event.get("kind")
            in {"initialized", "gap", "reservation", "started", "bundle", "seal", "stop"},
            "event_kind",
        )
        _require(_integer(event.get("at_ns")) and event["at_ns"] >= at_ns, "event_clock_regression")
        _require(isinstance(event.get("payload"), dict), "event_payload")
        digest = event.get("event_sha256")
        _require(
            _digest(digest)
            and sha(canonical({k: v for k, v in event.items() if k != "event_sha256"})) == digest,
            "event_digest",
        )
        _require(not events or events[-1]["kind"] != "stop", "event_after_stop")
        if not events:
            _require(
                event["kind"] == "initialized" and event.get("bucket") is None, "initial_event"
            )
        else:
            _require(event["kind"] != "initialized", "duplicate_initialization")
        events.append(event)
        previous = digest
        at_ns = event["at_ns"]
    return events


def verify_plan(plan: Any, expected_sources: Mapping[str, str] | None = None) -> None:
    _require(
        isinstance(plan, dict)
        and plan.get("schema") == schema.PLAN_SCHEMA
        and type(plan.get("schema_version")) is int
        and plan.get("schema_version") == 1,
        "plan_schema",
    )
    _require(isinstance(plan.get("service_id"), str) and bool(plan["service_id"]), "service_id")
    _require(
        plan.get("provider") == "helius-free"
        and plan.get("endpoint_origin")
        in {"https://mainnet.helius-rpc.com", "https://rpc.helius.xyz"},
        "provider_origin",
    )
    _fields(
        plan,
        (
            "schema schema_version service_id provider endpoint_origin "
            "storage code_sha256 predecessor schedule caps"
        ),
    )
    storage = plan.get("storage")
    _require(isinstance(storage, dict), "storage_binding")
    _require(
        isinstance(storage.get("root_id"), str)
        and bool(storage["root_id"])
        and isinstance(storage.get("volume_uuid"), str)
        and bool(storage["volume_uuid"]),
        "storage_identity",
    )
    relative = _parts(storage.get("relative_root"))
    _require(
        len(relative) == 2 and relative[0] == "services" and relative[1] == plan["service_id"],
        "service_relative_root",
    )
    _fields(storage, "root_id volume_uuid relative_root")
    sources = plan.get("code_sha256")
    _require(
        isinstance(sources, dict)
        and bool(sources)
        and all(_digest(value) for value in sources.values()),
        "source_pins",
    )
    if expected_sources is not None:
        _require(sources == dict(expected_sources), "source_pin_mismatch")
    predecessor = plan.get("predecessor")
    _require(isinstance(predecessor, dict), "predecessor")
    _require(
        _digest(predecessor.get("stop_sha256"))
        and _digest(predecessor.get("terminal_receipt_sha256"))
        and _integer(predecessor.get("committed_frontier"), 1),
        "predecessor_pins",
    )
    _require(isinstance(predecessor.get("gap_intervals"), list), "predecessor_gaps")
    _fields(
        predecessor,
        (
            "campaign_id stop_sha256 terminal_receipt_sha256 "
            "committed_frontier gap_intervals last_inventoried_interval"
        ),
    )
    schedule = plan.get("schedule")
    _require(isinstance(schedule, dict), "schedule")
    _fields(schedule, "bootstrap frame expires_ns clock_preflight_ns")
    _require(_integer(schedule.get("clock_preflight_ns"), 1), "preflight_time")
    for phase in ("bootstrap", "frame"):
        window = schedule.get(phase)
        _require(
            isinstance(window, dict) and set(window) == {"bucket", "start_ns", "end_ns"},
            "window_fields",
        )
        _require(
            isinstance(window.get("bucket"), str) and "/" not in window["bucket"], "bucket_identity"
        )
        _parts(window["bucket"])
        from datetime import UTC, datetime

        try:
            bucket_ns = (
                int(
                    datetime.strptime(window["bucket"], "%Y%m%dT%H%M%SZ")
                    .replace(tzinfo=UTC)
                    .timestamp()
                )
                * 1_000_000_000
            )
        except (ValueError, OverflowError) as exc:
            raise VerificationError("bucket_format") from exc
        _require(window.get("start_ns") == bucket_ns, "bucket_start_identity")
        _require(
            _integer(window.get("start_ns"), 1)
            and _integer(window.get("end_ns"), 1)
            and window["end_ns"] > window["start_ns"] + 10_000_000_000,
            "window_times",
        )
    for phase, duration in (("bootstrap", 60), ("frame", 180)):
        _require(
            schedule[phase]["end_ns"] - schedule[phase]["start_ns"] == duration * 1_000_000_000,
            "fixed_window_duration",
        )
    _require(schedule["expires_ns"] == schedule["frame"]["end_ns"], "fixed_lease_expiry")
    _require(schedule["bootstrap"]["bucket"] != schedule["frame"]["bucket"], "duplicate_bucket")
    _require(
        schedule["bootstrap"]["start_ns"] >= schedule["clock_preflight_ns"] + 60_000_000_000,
        "bootstrap_preflight_margin",
    )
    delta = schedule["frame"]["start_ns"] - schedule["bootstrap"]["start_ns"]
    _require(
        300_000_000_000 <= delta <= 600_000_000_000
        and schedule["bootstrap"]["end_ns"] <= schedule["frame"]["start_ns"],
        "schedule_order",
    )
    _require(
        _integer(schedule.get("expires_ns"))
        and schedule["expires_ns"] >= schedule["frame"]["end_ns"],
        "schedule_expiry",
    )
    caps = plan.get("caps")
    _require(isinstance(caps, dict), "caps")
    _fields(
        caps,
        (
            "max_attempts max_requests_total max_requests_per_utc_date "
            "bootstrap_requests frame_requests max_frame_width selection_count "
            "max_response_bytes raw_bytes_total stored_bytes_total "
            "raw_bytes_per_utc_date stored_bytes_per_utc_date disk_floor_bytes "
            "peak_write_margin_bytes storage_codec bootstrap_raw_bytes bootstrap_stored_bytes "
            "frame_raw_bytes frame_stored_bytes"
        ),
    )
    for name, expected in {
        "max_attempts": 2,
        "max_requests_total": 35,
        "bootstrap_requests": 1,
        "frame_requests": 34,
        "selection_count": 16,
    }.items():
        _require(type(caps.get(name)) is int and caps[name] == expected, "cap_" + name)
    _require(
        _integer(caps.get("max_frame_width"), 1) and caps["max_frame_width"] <= 20000, "frame_width"
    )
    _require(
        _integer(caps.get("max_response_bytes"), 1) and caps["max_response_bytes"] <= 32000000,
        "response_cap",
    )
    _require(
        _integer(caps.get("disk_floor_bytes"), 50 * 2**30)
        and _integer(caps.get("peak_write_margin_bytes")),
        "disk_floor",
    )
    for name in (
        "max_requests_per_utc_date",
        "raw_bytes_total",
        "stored_bytes_total",
        "raw_bytes_per_utc_date",
        "stored_bytes_per_utc_date",
    ):
        _require(_integer(caps.get(name), 1), "positive_cap_" + name)
    for phase, maximum in (("bootstrap", caps["max_response_bytes"]), ("frame", 256_000_000)):
        for kind in ("raw", "stored"):
            value = caps.get(f"{phase}_{kind}_bytes")
            _require(_integer(value, 1) and value <= maximum, "phase_byte_cap")
    for kind in ("raw", "stored"):
        _require(
            caps[f"{kind}_bytes_total"]
            == caps[f"bootstrap_{kind}_bytes"] + caps[f"frame_{kind}_bytes"],
            "aggregate_phase_cap",
        )
    _require(
        caps["peak_write_margin_bytes"]
        >= caps["frame_stored_bytes"] + caps["max_response_bytes"] + 2_000_000,
        "physical_peak_envelope",
    )
    _require(caps.get("storage_codec") == "raw", "unqualified_storage_codec")


def _fields(value: dict[str, Any], fields: str) -> None:
    _require(set(value) == set(fields.split()), "unexpected_fields")


def _object(reader: Any, path: str, expected_schema: str) -> tuple[dict[str, Any], bytes]:
    payload = reader.read_bytes(path)
    value = decode(payload)
    _require(
        isinstance(value, dict)
        and value.get("schema") == expected_schema
        and type(value.get("schema_version")) is int
        and value.get("schema_version") == 1,
        "file_schema:" + path,
    )
    _require(canonical(value) == payload, "noncanonical_file:" + path)
    fields = {
        schema.REQUEST_SCHEMA: (
            "provider reservation_id request_id ordinal method params "
            "request_sha256 wire_request_sha256 request_started_ns "
            "decision_ns clock_check_path clock_check_sha256 monotonic_ns"
        ),
        schema.RECEIPT_SCHEMA: (
            "provider request_id request_sha256 wire_request_sha256 "
            "request_started_ns received_ns availability_ns decision_ns "
            "response_sha256 response_bytes storage"
        ),
        schema.RESULT_SCHEMA: (
            "provider request_id request_sha256 wire_request_sha256 "
            "receipt_sha256 response_sha256 status result_status method "
            "event_slot produced_slots signatures block_identity "
            "response_bytes stored_response_bytes event_time_ns "
            "availability_ns decision_ns http_status transport_category"
        ),
        schema.BUNDLE_SCHEMA: (
            "provider bucket phase reservation_id plan_sha256 status "
            "request_count requests raw_bytes stored_bytes bootstrap_slot "
            "frame selected_blocks provider_charge_units "
            "provider_charge_status terminal_status"
        ),
        schema.SEAL_SCHEMA: "bucket reservation_id plan_sha256 bundle_sha256 sealed_ns status",
        schema.STOP_SCHEMA: "reason stopped_ns last_bucket bundle_sha256 post_stop_requests",
    }
    if expected_schema in fields:
        _fields(value, "schema schema_version " + fields[expected_schema])
    return value, payload


def _phase(plan: dict[str, Any], bucket: str) -> str:
    for phase in ("bootstrap", "frame"):
        if plan["schedule"][phase]["bucket"] == bucket:
            return phase
    raise VerificationError("unscheduled_bucket")


def _rpc_value(raw: bytes, *, allow_null: bool = False) -> Any:
    value = decode(raw)
    _require(
        isinstance(value, dict)
        and value.get("jsonrpc") == "2.0"
        and type(value.get("id")) is int
        and value["id"] == 1
        and "result" in value
        and "error" not in value,
        "rpc_envelope",
    )
    _require(allow_null or value["result"] is not None, "rpc_null_result")
    return value["result"]


def _semantically_valid(
    value: Any, method: str, params: Any, phase: str, plan: dict[str, Any]
) -> bool:
    if method == "getSlot":
        return _integer(value, 1) and (
            phase != "bootstrap" or value + 1 >= plan["predecessor"]["committed_frontier"]
        )
    if method == "getBlocks":
        return (
            isinstance(value, list)
            and all(_integer(slot, params[0]) and slot <= params[1] for slot in value)
            and value == sorted(set(value))
        )
    from tools.brain_senses_block_provider_pilot import (
        _signature_list,
        _structural_response_validation,
    )

    if not isinstance(value, dict):
        return False
    detail = params[1]["transactionDetails"]
    if detail == "full" and _structural_response_validation(value, params[0]):
        return False
    try:
        return _signature_list(value, detail) is not None
    except (TypeError, AttributeError, IndexError):
        return False


def _selection(
    plan: dict[str, Any], bucket: str, inventory: list[int], digest: str
) -> dict[str, Any]:
    import random
    from fractions import Fraction

    count = min(plan["caps"]["selection_count"], len(inventory))
    seed = f"{plan['service_id']}:{bucket}:{digest}"
    selected = sorted(random.Random(seed).sample(inventory, count))
    fraction = Fraction(count, len(inventory)) if inventory else None
    return {
        "inventory_sha256": digest,
        "denominator": len(inventory),
        "selection_count": count,
        "selected_slots": selected,
        "inclusion_probability": f"{fraction.numerator}/{fraction.denominator}"
        if fraction
        else None,
        "seed": plan["service_id"],
        "selection_rule": "uniform_without_replacement_from_finalized_getblocks_members",
    }


def _request_operations(
    plan: dict[str, Any],
    bucket: str,
    values: list[Any],
    raws: list[bytes],
    bootstrap_slot: int | None,
) -> tuple[list[tuple[str, list[Any]]], dict[str, Any] | None]:
    operations: list[tuple[str, list[Any]]] = [("getSlot", [{"commitment": "finalized"}])]
    if _phase(plan, bucket) == "bootstrap" or not values:
        return operations, None
    anchor = values[0]
    _require(_integer(anchor, 1), "invalid_anchor")
    _require(bootstrap_slot is not None and anchor >= bootstrap_slot + 1, "anchor_regression")
    assert bootstrap_slot is not None
    start = bootstrap_slot + 1
    end = anchor + 1
    _require(end - start <= plan["caps"]["max_frame_width"], "oversized_frame")
    operations.append(("getBlocks", [start, anchor, {"commitment": "finalized"}]))
    frame = {"start": start, "end_exclusive": end, "anchor_slot": anchor, "width": end - start}
    if len(values) < 2:
        return operations, frame
    inventory = values[1]
    _require(
        isinstance(inventory, list)
        and all(_integer(slot, start) and slot < end for slot in inventory)
        and inventory == sorted(set(inventory)),
        "invalid_inventory",
    )
    selection = _selection(plan, bucket, inventory, sha(raws[1]))
    frame.update(selection)
    for slot in selection["selected_slots"]:
        for detail in ("signatures", "full"):
            options: dict[str, Any] = {
                "commitment": "finalized",
                "encoding": "json",
                "transactionDetails": detail,
                "rewards": False,
            }
            if detail == "full":
                options["maxSupportedTransactionVersion"] = 1
            operations.append(("getBlock", [slot, options]))
    return operations, frame


def _clock_binding(reader: Any, record: dict[str, Any], bucket: str, at_ns: int) -> dict[str, Any]:
    path = record.get("clock_check_path")
    _require(
        isinstance(path, str) and path.startswith(f"clock/{bucket}-") and path.endswith(".json"),
        "clock_path",
    )
    parts = _parts(cast(str, path))
    _require(len(parts) == 2, "clock_path_depth")
    payload = reader.read_bytes(path)
    _require(record.get("clock_check_sha256") == sha(payload), "clock_digest")
    value = decode(payload)
    try:
        clock_contract.validate_clock(value, now_ns=at_ns)
    except (clock_contract.ClockError, TypeError, AttributeError) as exc:
        raise VerificationError("clock_record") from exc
    return cast(dict[str, Any], value)


def _bucket_prefix(reader: Any, bucket: str, plan: dict[str, Any]) -> dict[str, Any]:
    phase = _phase(plan, bucket)
    base = f"buckets/{bucket}"
    all_files = reader.list_files()
    files = {path for path in all_files if path.startswith(base + "/")}
    reservation, reservation_bytes = _object(
        reader, base + "/reservation.json", schema.BUCKET_SCHEMA
    )
    plan_hash = sha(reader.read_bytes(schema.PLAN_FILE))
    _require(
        reservation.get("service_id") == plan["service_id"]
        and reservation.get("bucket") == bucket
        and reservation.get("phase") == phase
        and reservation.get("plan_sha256") == plan_hash,
        "reservation_binding",
    )
    _require(
        isinstance(reservation.get("reservation_id"), str) and bool(reservation["reservation_id"]),
        "reservation_id",
    )
    _require(
        reservation.get("raw_byte_cap") == plan["caps"][phase + "_raw_bytes"]
        and reservation.get("stored_byte_cap") == plan["caps"][phase + "_stored_bytes"],
        "reservation_byte_caps",
    )
    expected_count = plan["caps"][phase + "_requests"]
    _require(reservation.get("request_count") == expected_count, "reservation_request_count")
    _fields(
        reservation,
        (
            "schema schema_version service_id bucket phase reservation_id "
            "plan_sha256 request_count raw_byte_cap stored_byte_cap "
            "created_ns not_before_ns expires_ns predecessor_event_sha256 "
            "clock_check_path clock_check_sha256"
        ),
    )
    _clock_binding(reader, reservation, bucket, cast(int, reservation.get("created_ns")))
    window = plan["schedule"][phase]
    _require(
        reservation.get("not_before_ns") == window["start_ns"]
        and reservation.get("expires_ns") == window["end_ns"],
        "reservation_window",
    )
    _require(
        _integer(reservation.get("created_ns"))
        and window["start_ns"] + 5_000_000_000
        <= reservation["created_ns"]
        <= window["end_ns"] - 5_000_000_000,
        "reservation_time",
    )
    allowed = {
        base + "/reservation.json",
        base + "/started.json",
        base + "/bundle.json",
        base + "/seal.json",
    }
    started = None
    if base + "/started.json" in files:
        started, _ = _object(reader, base + "/started.json", schema.BUCKET_SCHEMA)
        _require(
            started.get("reservation_id") == reservation["reservation_id"]
            and started.get("plan_sha256") == plan_hash,
            "started_binding",
        )
        _require(
            _integer(started.get("started_ns"))
            and reservation["created_ns"]
            <= started["started_ns"]
            <= window["end_ns"] - 5_000_000_000,
            "started_time",
        )
    request_paths = sorted(path for path in files if path.startswith(base + "/requests/"))
    _require(
        len(request_paths) <= expected_count and (not request_paths or started is not None),
        "requests_without_start_or_over_cap",
    )
    values: list[Any] = []
    raws: list[bytes] = []
    rows: list[dict[str, Any]] = []
    request_records: list[dict[str, Any]] = []
    raw_total = stored_total = 0
    latest_available = reservation["created_ns"]
    unresolved = False
    bootstrap_slot = None
    if phase == "frame":
        boot_bucket = plan["schedule"]["bootstrap"]["bucket"]
        boot = verify_bundle(reader, boot_bucket, plan)
        _require(f"buckets/{boot_bucket}/seal.json" in all_files, "unsealed_bootstrap")
        bootstrap_slot = boot["bootstrap_slot"]
    for ordinal, path in enumerate(request_paths):
        _require(path == f"{base}/requests/{ordinal:02d}.json", "request_ordinal_gap")
        _require(not unresolved, "request_after_unresolved")
        request, request_bytes = _object(reader, path, schema.REQUEST_SCHEMA)
        request_id = f"{bucket}-{ordinal:02d}"
        _require(
            request.get("request_id") == request_id
            and request.get("ordinal") == ordinal
            and type(request.get("ordinal")) is int
            and request.get("reservation_id") == reservation["reservation_id"],
            "request_identity",
        )
        _require(request.get("provider") == plan["provider"], "request_provider")
        method = request.get("method")
        params: Any = request.get("params")
        operations, _ = _request_operations(plan, bucket, values, raws, bootstrap_slot)
        _require(
            ordinal < len(operations) and (method, params) == operations[ordinal],
            "request_operation",
        )
        _require(
            request.get("request_sha256") == sha(canonical({"method": method, "params": params})),
            "semantic_request_hash",
        )
        wire = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        _require(request.get("wire_request_sha256") == sha(wire), "wire_request_hash")
        request_time: Any = request.get("request_started_ns")
        _require(
            _integer(request_time)
            and started is not None
            and started["started_ns"] <= request_time <= window["end_ns"] - 5_000_000_000
            and request_time >= latest_available,
            "request_time",
        )
        _require(
            _integer(request.get("decision_ns"))
            and reservation["created_ns"] <= request["decision_ns"] <= request_time,
            "request_decision_time",
        )
        clock_record = _clock_binding(reader, request, bucket, request_time)
        try:
            clock_contract.validate_progress(
                clock_record,
                wall_ns=request_time,
                monotonic_ns=cast(int, request.get("monotonic_ns")),
            )
        except (clock_contract.ClockError, TypeError) as exc:
            raise VerificationError("request_clock_progress") from exc
        request_records.append(request)
        capture_base = f"{base}/captures/{ordinal:02d}"
        response_path = capture_base + "/response.json"
        receipt_path = capture_base + "/receipt.json"
        result_path = f"{base}/results/{ordinal:02d}.json"
        allowed.update({path, response_path, receipt_path, result_path})
        if result_path not in files:
            unresolved = True
            # A partial capture is preserved; its mere presence never permits replay.
            _require(
                receipt_path not in files or response_path in files,
                "partial_receipt_without_response",
            )
            if response_path in files:
                partial_raw = reader.read_bytes(response_path)
                _require(
                    len(partial_raw) <= plan["caps"]["max_response_bytes"], "partial_response_cap"
                )
                raw_total += len(partial_raw)
                stored_total += len(partial_raw)
                if receipt_path in files:
                    partial_receipt, _ = _object(reader, receipt_path, schema.RECEIPT_SCHEMA)
                    _require(
                        partial_receipt.get("request_id") == request_id
                        and partial_receipt.get("provider") == plan["provider"]
                        and partial_receipt.get("request_sha256") == request["request_sha256"]
                        and partial_receipt.get("wire_request_sha256")
                        == request["wire_request_sha256"]
                        and partial_receipt.get("response_sha256") == sha(partial_raw)
                        and partial_receipt.get("response_bytes") == len(partial_raw),
                        "partial_receipt_binding",
                    )
                    _require(
                        partial_receipt.get("storage")
                        == {
                            "codec": "raw",
                            "path": "response.json",
                            "sha256": sha(partial_raw),
                            "bytes": len(partial_raw),
                            "codec_version": None,
                        },
                        "partial_storage_binding",
                    )
                    _require(
                        partial_receipt.get("availability_ns") is None
                        and partial_receipt.get("decision_ns") == request["decision_ns"]
                        and _integer(partial_receipt.get("request_started_ns"))
                        and _integer(partial_receipt.get("received_ns"))
                        and request_time
                        <= partial_receipt["request_started_ns"]
                        <= partial_receipt["received_ns"],
                        "partial_receipt_times",
                    )
            continue
        result, result_bytes = _object(reader, result_path, schema.RESULT_SCHEMA)
        _require(
            result.get("request_id") == request_id
            and result.get("request_sha256") == request["request_sha256"]
            and result.get("provider") == plan["provider"]
            and result.get("method") == method,
            "result_binding",
        )
        _require(result.get("decision_ns") == request["decision_ns"], "result_decision_time")
        _require(
            result.get("wire_request_sha256") == request["wire_request_sha256"], "result_wire_hash"
        )
        http_status = result.get("http_status")
        category = result.get("transport_category")
        _require(
            http_status is None or (type(http_status) is int and 100 <= http_status <= 599),
            "http_failure_status",
        )
        _require(
            category in {None, "timeout", "dns", "tls", "connection", "other"},
            "transport_failure_category",
        )
        if result.get("status") != "captured":
            _require(
                result.get("status") == "ambiguous" and result.get("result_status") == "unknown",
                "unsupported_result_status",
            )
            _require(
                response_path not in files and receipt_path not in files,
                "ambiguous_capture_artifact",
            )
            _require(
                all(
                    result.get(key) is None
                    for key in (
                        "receipt_sha256",
                        "response_sha256",
                        "response_bytes",
                        "stored_response_bytes",
                        "availability_ns",
                    )
                ),
                "ambiguous_capture_claim",
            )
            unresolved = True
            continue
        _require(
            result.get("result_status") in {"parsed", "provider_error", "invalid_response"},
            "unsupported_parse_status",
        )
        _require(http_status is None and category is None, "captured_transport_failure_claim")
        receipt, receipt_bytes = _object(reader, receipt_path, schema.RECEIPT_SCHEMA)
        raw = reader.read_bytes(response_path)
        _require(
            receipt.get("request_id") == request_id
            and receipt.get("request_sha256") == request["request_sha256"]
            and receipt.get("provider") == plan["provider"],
            "receipt_binding",
        )
        _require(
            receipt.get("response_sha256") == sha(raw)
            and receipt.get("response_bytes") == len(raw)
            and 0 < len(raw) <= plan["caps"]["max_response_bytes"],
            "response_binding",
        )
        _require(
            receipt.get("wire_request_sha256") == request["wire_request_sha256"],
            "receipt_wire_hash",
        )
        storage: Any = receipt.get("storage")
        _require(
            isinstance(storage, dict)
            and storage.get("codec") == "raw"
            and storage.get("path") == "response.json"
            and storage.get("sha256") == sha(raw)
            and storage.get("bytes") == len(raw)
            and storage.get("codec_version") is None,
            "stored_response_binding",
        )
        _fields(storage, "codec path sha256 bytes codec_version")
        _require(
            receipt.get("availability_ns") is None
            and receipt.get("decision_ns") == request["decision_ns"],
            "receipt_time_contract",
        )
        received: Any = receipt.get("received_ns")
        available: Any = result.get("availability_ns")
        _require(
            _integer(receipt.get("request_started_ns"))
            and _integer(received)
            and _integer(available)
            and request_time <= receipt["request_started_ns"] <= received <= available,
            "capture_time_order",
        )
        _require(
            result.get("receipt_sha256") == sha(receipt_bytes)
            and result.get("response_sha256") == sha(raw)
            and result.get("response_bytes") == len(raw)
            and result.get("stored_response_bytes") == len(raw),
            "result_capture_binding",
        )
        raw_total += len(raw)
        stored_total += len(raw)
        latest_available = available
        if result.get("result_status") != "parsed":
            if result["result_status"] == "provider_error":
                try:
                    _rpc_value(raw, allow_null=True)
                except VerificationError:
                    pass
                else:
                    raise VerificationError("false_provider_error_classification")
            else:
                value = _rpc_value(raw, allow_null=True)
                _require(
                    not _semantically_valid(value, cast(str, method), params, phase, plan),
                    "false_invalid_response_classification",
                )
            unresolved = True
            continue
        value = _rpc_value(raw)
        if method == "getSlot":
            _require(_integer(value, 1) and result.get("event_slot") == value, "anchor_projection")
            if phase == "bootstrap":
                _require(
                    value + 1 >= plan["predecessor"]["committed_frontier"], "bootstrap_regression"
                )
        elif method == "getBlocks":
            _require(result.get("produced_slots") == value, "inventory_projection")
        else:
            from tools.brain_senses_block_provider_pilot import (
                _signature_list,
                _structural_response_validation,
            )

            _require(
                result.get("event_slot") == params[0] and isinstance(value, dict),
                "block_projection",
            )
            _require(
                result.get("block_identity")
                == {
                    "blockhash": value.get("blockhash"),
                    "previousBlockhash": value.get("previousBlockhash"),
                    "parentSlot": value.get("parentSlot"),
                    "blockTime": value.get("blockTime"),
                    "blockHeight": value.get("blockHeight"),
                },
                "block_identity_projection",
            )
            signatures = _signature_list(value, params[1]["transactionDetails"])
            _require(
                signatures is not None and result.get("signatures") == signatures,
                "signature_projection",
            )
            if params[1]["transactionDetails"] == "full":
                _require(
                    not _structural_response_validation(value, params[0]),
                    "invalid_full_block_structure",
                )
            event_time = value.get("blockTime")
            _require(
                result.get("event_time_ns")
                == (event_time * 1_000_000_000 if type(event_time) is int else None),
                "event_time_projection",
            )
        values.append(value)
        raws.append(raw)
        rows.append(
            {
                "request_id": request_id,
                "request_sha256": request["request_sha256"],
                "receipt_sha256": sha(receipt_bytes),
                "result_sha256": sha(result_bytes),
                "response_sha256": sha(raw),
            }
        )
    _require(files <= allowed, "orphan_bucket_artifact")
    _require(
        raw_total <= reservation["raw_byte_cap"] and stored_total <= reservation["stored_byte_cap"],
        "bucket_byte_cap",
    )
    try:
        operations, frame = _request_operations(plan, bucket, values, raws, bootstrap_slot)
    except VerificationError as exc:
        reason = str(exc)
        allowed_reasons = {
            "oversized_frame": "external_writer_oversized_frame",
            "anchor_regression": "external_writer_anchor_regression",
        }
        _require(
            phase == "frame"
            and len(request_paths) == len(values) == 1
            and reason in allowed_reasons
            and base + "/bundle.json" not in files
            and base + "/seal.json" not in files,
            "invalid_terminal_anchor_prefix",
        )
        stop = (
            decode(reader.read_bytes(schema.STOP_FILE)) if schema.STOP_FILE in all_files else None
        )
        if stop is None:
            events = verify_events(reader.read_bytes(schema.EVENTS_FILE))
            stop = (
                events[-1]["payload"].get("stop")
                if events and events[-1]["kind"] == "stop"
                else None
            )
        _require(
            isinstance(stop, dict) and stop.get("reason") == allowed_reasons[reason],
            "terminal_anchor_stop_reason",
        )
        operations, frame, unresolved = [], None, True
    selected_blocks = []
    if phase == "frame" and frame is not None and len(values) >= 2:
        for index, slot in enumerate(frame["selected_slots"]):
            at = 2 + 2 * index
            if len(values) <= at + 1:
                break
            signatures, full = values[at], values[at + 1]
            from tools.brain_senses_block_provider_pilot import _signature_list

            sigs = _signature_list(full, "full")
            _require(
                sigs == _signature_list(signatures, "signatures"), "signature_full_disagreement"
            )
            _require(
                all(
                    signatures.get(key) == full.get(key)
                    for key in (
                        "blockhash",
                        "previousBlockhash",
                        "parentSlot",
                        "blockTime",
                        "blockHeight",
                    )
                ),
                "block_identity_disagreement",
            )
            selected_blocks.append(
                {
                    "slot": slot,
                    "signatures": sigs,
                    "blockhash": full["blockhash"],
                    "previous_blockhash": full["previousBlockhash"],
                    "parent_slot": full["parentSlot"],
                }
            )
    return {
        "reservation": reservation,
        "reservation_sha256": sha(reservation_bytes),
        "request_records": request_records,
        "requests": rows,
        "request_count": len(request_paths),
        "raw_bytes": raw_total,
        "stored_bytes": stored_total,
        "latest_available_ns": latest_available,
        "unresolved": unresolved,
        "complete": not unresolved and len(values) == len(operations),
        "bootstrap_slot": values[0] if phase == "bootstrap" and values else None,
        "frame": frame,
        "selected_blocks": selected_blocks,
        "phase": phase,
    }


def verify_bundle(reader: Any, bucket: str, plan: dict[str, Any]) -> dict[str, Any]:
    prefix = _bucket_prefix(reader, bucket, plan)
    bundle, bundle_bytes = _object(reader, f"buckets/{bucket}/bundle.json", schema.BUNDLE_SCHEMA)
    _require(
        bundle.get("bucket") == bucket
        and bundle.get("phase") == prefix["phase"]
        and bundle.get("reservation_id") == prefix["reservation"]["reservation_id"]
        and bundle.get("plan_sha256") == sha(reader.read_bytes(schema.PLAN_FILE)),
        "bundle_identity",
    )
    _require(
        bundle.get("provider") == plan["provider"]
        and bundle.get("provider_charge_units") is None
        and bundle.get("provider_charge_status") == "unknown_unverified",
        "bundle_provider_or_charge_claim",
    )
    for key in (
        "requests",
        "request_count",
        "raw_bytes",
        "stored_bytes",
        "bootstrap_slot",
        "frame",
        "selected_blocks",
    ):
        _require(bundle.get(key) == prefix[key], "bundle_recomputed_" + key)
    _require(
        bundle.get("status") == "complete"
        and prefix["complete"]
        and bundle.get("terminal_status") == prefix["phase"] + "_complete",
        "incomplete_bundle",
    )
    seal_path = f"buckets/{bucket}/seal.json"
    if seal_path in reader.list_files():
        seal, _ = _object(reader, seal_path, schema.SEAL_SCHEMA)
        _require(
            seal.get("bucket") == bucket
            and seal.get("reservation_id") == bundle["reservation_id"]
            and seal.get("plan_sha256") == bundle["plan_sha256"]
            and seal.get("bundle_sha256") == sha(bundle_bytes)
            and seal.get("status") == "complete",
            "seal_binding",
        )
        _require(
            _integer(seal.get("sealed_ns")) and seal["sealed_ns"] >= prefix["latest_available_ns"],
            "seal_before_availability",
        )
    return {
        **bundle,
        "bundle_sha256": sha(bundle_bytes),
        "latest_available_ns": prefix["latest_available_ns"],
    }


def verify(
    reader: Any,
    expected_plan_sha256: str | None = None,
    expected_sources: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Verify durable evidence and return conservative, nonauthorizing recovery state."""
    plan, plan_bytes = _object(reader, schema.PLAN_FILE, schema.PLAN_SCHEMA)
    verify_plan(plan, expected_sources)
    plan_hash = sha(plan_bytes)
    if expected_plan_sha256 is not None:
        _require(plan_hash == expected_plan_sha256, "plan_pin_mismatch")
    files = set(reader.list_files())
    events = (
        verify_events(reader.read_bytes(schema.EVENTS_FILE)) if schema.EVENTS_FILE in files else []
    )
    _require(
        bool(events) or files <= {schema.PLAN_FILE, schema.EVENTS_FILE, schema.LOCK_FILE},
        "missing_initial_event",
    )
    if events:
        _require(events[0]["payload"] == {"plan_sha256": plan_hash}, "initial_plan_binding")
        _require(
            events[0]["at_ns"] + 60_000_000_000 <= plan["schedule"]["bootstrap"]["start_ns"],
            "bootstrap_not_future_at_initialization",
        )
    scheduled = {plan["schedule"][phase]["bucket"]: phase for phase in ("bootstrap", "frame")}
    allowed_top = {schema.PLAN_FILE, schema.EVENTS_FILE, schema.LOCK_FILE, schema.STOP_FILE}
    for path in files:
        if path in allowed_top:
            continue
        if path.startswith("clock/"):
            parts = _parts(path)
            _require(
                len(parts) == 2 and any(parts[1].startswith(bucket + "-") for bucket in scheduled),
                "clock_bucket",
            )
            try:
                clock_contract.validate_clock(decode(reader.read_bytes(path)))
            except (clock_contract.ClockError, TypeError, AttributeError) as exc:
                raise VerificationError("clock_record") from exc
            continue
        pieces = _parts(path)
        _require(
            len(pieces) >= 3 and pieces[0] == "buckets" and pieces[1] in scheduled,
            "unscheduled_artifact",
        )
    per_bucket: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in scheduled}
    stop_event = None
    for event in events[1:]:
        if event["kind"] == "stop":
            _require(
                event.get("bucket") is None and set(event["payload"]) == {"stop"},
                "stop_event_payload",
            )
            stop_event = event
            continue
        bucket: Any = event.get("bucket")
        _require(bucket in scheduled, "event_unscheduled_bucket")
        per_bucket[bucket].append(event)
        kind = event["kind"]
        if kind == "gap":
            _require(
                _integer(event["at_ns"])
                and event["at_ns"] >= plan["schedule"][scheduled[bucket]]["end_ns"],
                "gap_not_elapsed",
            )
            _require(len(per_bucket[bucket]) == 1, "gap_after_attempt")
            continue
        order = [e["kind"] for e in per_bucket[bucket]]
        _require(
            order == ["reservation", "started", "bundle", "seal"][: len(order)],
            "bucket_event_order",
        )
        field = kind + "_sha256"
        _require(
            set(event["payload"]) == {field} and _digest(event["payload"][field]), "event_file_hash"
        )
        path = f"buckets/{bucket}/{kind}.json"
        # Event-first publication may leave a missing last file. It is spent,
        # never a fresh opportunity to dispatch.
        if path in files:
            _require(sha(reader.read_bytes(path)) == event["payload"][field], "event_file_mismatch")
        else:
            _require(
                event is events[-1] or (events[-1]["kind"] == "stop" and event is events[-2]),
                "missing_nonterminal_event_file",
            )
    total_requests = total_raw = total_stored = 0
    unresolved = False
    frontier = plan["predecessor"]["committed_frontier"]
    gaps = list(plan["predecessor"]["gap_intervals"])
    last_time = events[-1]["at_ns"] if events else 0
    day_usage: dict[int, dict[str, int]] = {}
    buckets: dict[str, Any] = {}
    for phase in ("bootstrap", "frame"):
        bucket = plan["schedule"][phase]["bucket"]
        base = f"buckets/{bucket}"
        bucket_events = per_bucket[bucket]
        present = {path for path in files if path.startswith(base + "/")}
        if not present:
            if bucket_events:
                unresolved |= bucket_events[-1]["kind"] != "gap"
                buckets[bucket] = {
                    "status": "gap"
                    if bucket_events[-1]["kind"] == "gap"
                    else "interrupted_no_replay"
                }
            else:
                if any(path.startswith(f"clock/{bucket}-") for path in files):
                    unresolved = True
                    buckets[bucket] = {"status": "interrupted_no_replay"}
                else:
                    buckets[bucket] = {"status": "unstarted"}
            continue
        _require(
            bool(bucket_events) and bucket_events[0]["kind"] == "reservation",
            "bucket_without_reservation_event",
        )
        prefix = _bucket_prefix(reader, bucket, plan)
        total_requests += prefix["request_count"]
        total_raw += prefix["raw_bytes"]
        total_stored += prefix["stored_bytes"]
        last_time = max(
            last_time,
            prefix["latest_available_ns"],
            *(r["request_started_ns"] for r in prefix["request_records"]),
        )
        for record in prefix["request_records"]:
            day = record["request_started_ns"] // 86_400_000_000_000
            usage = day_usage.setdefault(day, {"requests": 0, "raw_bytes": 0, "stored_bytes": 0})
            usage["requests"] += 1
            response_path = f"{base}/captures/{record['ordinal']:02d}/response.json"
            if response_path in files:
                size = len(reader.read_bytes(response_path))
                usage["raw_bytes"] += size
                usage["stored_bytes"] += size
        if base + "/bundle.json" in files:
            bundle = verify_bundle(reader, bucket, plan)
            if base + "/seal.json" in files:
                _require(any(e["kind"] == "seal" for e in bucket_events), "seal_without_event")
                seal = decode(reader.read_bytes(base + "/seal.json"))
                last_time = max(last_time, seal["sealed_ns"])
                frontier = (
                    bundle["bootstrap_slot"] + 1
                    if phase == "bootstrap"
                    else bundle["frame"]["end_exclusive"]
                )
                if phase == "bootstrap":
                    gaps.append(
                        {
                            "start": plan["predecessor"]["committed_frontier"],
                            "end_exclusive": frontier,
                            "reason": "uncovered_since_predecessor_no_backfill",
                            "bootstrap_bucket": bucket,
                        }
                    )
                buckets[bucket] = {"status": "sealed", "bundle_sha256": bundle["bundle_sha256"]}
            else:
                unresolved = True
                buckets[bucket] = {
                    "status": "complete_bundle_local_seal_only",
                    "bundle_sha256": bundle["bundle_sha256"],
                }
        else:
            unresolved = True
            buckets[bucket] = {
                "status": "interrupted_no_replay",
                "request_count": prefix["request_count"],
            }
    caps = plan["caps"]
    _require(
        total_requests <= caps["max_requests_total"]
        and total_raw <= caps["raw_bytes_total"]
        and total_stored <= caps["stored_bytes_total"],
        "lease_cap",
    )
    for usage in day_usage.values():
        _require(
            usage["requests"] <= caps["max_requests_per_utc_date"]
            and usage["raw_bytes"] <= caps["raw_bytes_per_utc_date"]
            and usage["stored_bytes"] <= caps["stored_bytes_per_utc_date"],
            "utc_day_cap",
        )
    stop = stop_event["payload"]["stop"] if stop_event else None
    if schema.STOP_FILE in files:
        stop_file, _ = _object(reader, schema.STOP_FILE, schema.STOP_SCHEMA)
        _require(stop is None or stop == stop_file, "stop_event_file_mismatch")
        stop = stop_file
    if stop is not None:
        _require(
            isinstance(stop, dict)
            and stop.get("schema") == schema.STOP_SCHEMA
            and stop.get("schema_version") == 1
            and _integer(stop.get("stopped_ns"))
            and stop["stopped_ns"] >= last_time
            and stop.get("post_stop_requests") == 0,
            "invalid_stop",
        )
        # All request starts have already been included in last_time. A claim of
        # zero requests cannot override durable intents with later timestamps.
        _require(
            stop_event is None or stop_event["at_ns"] == stop["stopped_ns"], "stop_time_binding"
        )
    return {
        "schema_version": 1,
        "plan_sha256": plan_hash,
        "status": "stopped"
        if stop is not None
        else "interrupted_no_replay"
        if unresolved
        else "verified_prefix",
        "activation_authorized": False,
        "dispatch_authorized": False,
        "replay_authorized": False,
        "frontier": frontier,
        "gap_intervals": gaps,
        "requests": total_requests,
        "raw_bytes": total_raw,
        "stored_bytes": total_stored,
        "buckets": buckets,
        "stop": stop,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }
