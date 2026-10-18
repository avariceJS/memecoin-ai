"""Independent, read-only verifier for the finite external Senses v2 lease.

The verifier never repairs a journal or authorizes replay. A spent request intent
without a complete seal is a terminal unresolved prefix.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from tools import brain_senses_external_clock_v1 as clock_contract
from tools import brain_senses_external_schema_v2 as schema


class VerificationError(ValueError):
    """Evidence does not satisfy the v2 external writer contract."""


class DescriptorReaderV2:
    """V2 nofollow reader with a lease-sized bounded file manifest."""

    MAX_FILES = 2048

    def __init__(self, root_fd: int) -> None:
        from tools.brain_senses_external_verify_v1 import DescriptorReader

        self._reader = DescriptorReader(root_fd)
        self.fd = self._reader.fd
        self.device = self._reader.device
        self.owner = self._reader.owner

    def read_bytes(self, relative: str, *, limit: int = 64_000_000) -> bytes:
        return self._reader.read_bytes(relative, limit=limit)

    def list_files(self) -> list[str]:
        from tools.brain_senses_external_verify_v1 import _parts, _require

        found: list[str] = []

        def visit(fd: int, prefix: str, depth: int) -> None:
            _require(depth <= 8, "tree_too_deep")
            for name in sorted(os.listdir(fd)):
                _parts(name)
                entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                path = prefix + name
                if stat.S_ISDIR(entry.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        self._reader._directory(os.fstat(child))
                        visit(child, path + "/", depth + 1)
                    finally:
                        os.close(child)
                else:
                    _require(
                        stat.S_ISREG(entry.st_mode)
                        and entry.st_uid == self.owner
                        and entry.st_dev == self.device
                        and entry.st_nlink == 1
                        and stat.S_IMODE(entry.st_mode) in {0o400, 0o600},
                        "unsafe_file",
                    )
                    found.append(path)
                    _require(len(found) <= self.MAX_FILES, "tree_too_large")

        visit(self.fd, "", 0)
        return found

    def close(self) -> None:
        self._reader.close()

    def __enter__(self) -> DescriptorReaderV2:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


_SERVICE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_MINUTE_NS = 60_000_000_000
_SECOND_NS = 1_000_000_000


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise VerificationError(reason)


def _digest(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _fields(value: Any, names: str) -> None:
    _require(isinstance(value, dict) and set(value) == set(names.split()), "unexpected_fields")


def decode(payload: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            _require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload, object_pairs_hook=unique, parse_constant=lambda _: _bad_constant()
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise VerificationError("invalid_json") from exc
    return value


def _bad_constant() -> Any:
    raise VerificationError("nonfinite_json")


def _bucket(start_ns: int) -> str:
    return datetime.fromtimestamp(start_ns / _SECOND_NS, UTC).strftime("%Y%m%dT%H%M00Z")


def verify_plan(plan: Any, expected_sources: Mapping[str, str] | None = None) -> None:
    _require(isinstance(plan, dict), "plan_type")
    _fields(
        plan,
        "schema schema_version service_id provider endpoint_origin storage code_sha256 "
        "predecessor schedule caps",
    )
    _require(
        plan["schema"] == schema.PLAN_SCHEMA
        and type(plan["schema_version"]) is int
        and plan["schema_version"] == 2,
        "plan_schema",
    )
    _require(
        isinstance(plan["service_id"], str)
        and _SERVICE_ID.fullmatch(plan["service_id"]) is not None,
        "service_id",
    )
    _require(
        plan["provider"] == "helius-free"
        and plan["endpoint_origin"] in {"https://mainnet.helius-rpc.com", "https://rpc.helius.xyz"},
        "provider_origin",
    )
    storage = plan["storage"]
    _fields(storage, "root_id volume_uuid relative_root")
    _require(
        isinstance(storage["root_id"], str)
        and bool(storage["root_id"])
        and isinstance(storage["volume_uuid"], str)
        and bool(storage["volume_uuid"]),
        "storage_identity",
    )
    _require(storage["relative_root"] == f"services/{plan['service_id']}", "storage_relative_root")
    sources = plan["code_sha256"]
    _require(
        isinstance(sources, dict) and bool(sources) and all(_digest(x) for x in sources.values()),
        "source_pins",
    )
    if expected_sources is not None:
        _require(sources == dict(expected_sources), "source_pin_mismatch")
    predecessor = plan["predecessor"]
    _fields(
        predecessor,
        "certificate certificate_sha256 relative_root candidate_sha256 validation_sha256 "
        "completion_sha256",
    )
    cert = predecessor["certificate"]
    _require(
        isinstance(cert, dict) and cert.get("kind") == "external-v1-sealed",
        "predecessor_certificate",
    )
    for key in ("certificate_sha256", "candidate_sha256", "validation_sha256", "completion_sha256"):
        _require(_digest(predecessor[key]), "predecessor_digest")
    _require(
        sha(canonical(cert)) == predecessor["certificate_sha256"], "predecessor_certificate_digest"
    )
    _require(
        cert.get("storage", {}).get("root_id") == storage["root_id"]
        and cert.get("storage", {}).get("volume_uuid") == storage["volume_uuid"],
        "predecessor_storage",
    )
    _require(cert.get("service_id") != plan["service_id"], "predecessor_service_reuse")
    gaps = cert.get("gap_intervals")
    _require(isinstance(gaps, list), "predecessor_gap_intervals")
    prior_end = -1
    for gap in gaps:
        _require(isinstance(gap, dict), "predecessor_gap_shape")
        start = gap.get("interval_start", gap.get("start"))
        end = gap.get("interval_end_exclusive", gap.get("end_exclusive"))
        _require(
            _integer(start, 0) and _integer(end, 0) and start <= end and start >= prior_end,
            "predecessor_gap_order",
        )
        prior_end = end
    _require(
        predecessor["relative_root"] == cert.get("storage", {}).get("relative_root")
        and predecessor["relative_root"] == f"services/{cert.get('service_id')}",
        "predecessor_relative_root",
    )
    schedule = plan["schedule"]
    _fields(schedule, "clock_preflight_ns bootstrap frames expires_ns")
    _require(_integer(schedule["clock_preflight_ns"], 1), "clock_preflight")
    bootstrap = schedule["bootstrap"]
    _fields(bootstrap, "bucket start_ns end_ns")
    _require(
        _integer(bootstrap["start_ns"], 1)
        and bootstrap["start_ns"] % _MINUTE_NS == 0
        and bootstrap["bucket"] == _bucket(bootstrap["start_ns"]),
        "bootstrap_bucket",
    )
    _require(bootstrap["end_ns"] == bootstrap["start_ns"] + 60 * _SECOND_NS, "bootstrap_window")
    _require(
        bootstrap["start_ns"] >= schedule["clock_preflight_ns"] + 2 * _MINUTE_NS,
        "bootstrap_clock_margin",
    )
    frames = schedule["frames"]
    _require(isinstance(frames, list) and len(frames) == 12, "frame_count")
    used = {bootstrap["bucket"]}
    for index, frame in enumerate(frames):
        _fields(frame, "index bucket start_ns end_ns")
        start = bootstrap["start_ns"] + (5 + 5 * index) * _MINUTE_NS
        _require(
            type(frame["index"]) is int
            and frame["index"] == index
            and frame["start_ns"] == start
            and frame["end_ns"] == start + 180 * _SECOND_NS,
            "frame_window",
        )
        _require(frame["bucket"] == _bucket(start) and frame["bucket"] not in used, "frame_bucket")
        used.add(frame["bucket"])
    _require(schedule["expires_ns"] == bootstrap["start_ns"] + 63 * _MINUTE_NS, "lease_expiry")
    _require(
        bootstrap["start_ns"]
        >= max(
            plan["predecessor"]["certificate"].get("expires_ns", 0),
            plan["predecessor"]["certificate"].get("terminal_sealed_ns", 0),
        )
        + _MINUTE_NS,
        "predecessor_terminal_margin",
    )
    _require(plan["caps"] == schema.CAPS, "caps_mismatch")


def verify_predecessor(
    reader: Any,
    predecessor: dict[str, Any],
    *,
    candidate_bytes: bytes,
    validation_bytes: bytes,
    completion_bytes: bytes,
) -> dict[str, Any]:
    """Re-verify the sealed v1 prefix and its immutable review receipts."""
    from tools import brain_senses_external_verify_v1 as v1

    expected_certificate = predecessor.get("certificate")
    _require(isinstance(expected_certificate, dict), "predecessor_certificate")
    assert isinstance(expected_certificate, dict)
    _require(
        sha(candidate_bytes) == predecessor.get("candidate_sha256"), "candidate_receipt_digest"
    )
    _require(
        sha(validation_bytes) == predecessor.get("validation_sha256"), "validation_receipt_digest"
    )
    _require(
        sha(completion_bytes) == predecessor.get("completion_sha256"), "completion_receipt_digest"
    )
    candidate, validation, completion = map(
        decode, (candidate_bytes, validation_bytes, completion_bytes)
    )
    _require(
        isinstance(candidate, dict)
        and candidate.get("schema") == "brain-senses-external-successor-v2-candidate",
        "candidate_receipt_schema",
    )
    _require(
        candidate.get("predecessor") == expected_certificate
        and candidate.get("provider_io") is False,
        "candidate_certificate_binding",
    )
    _require(
        isinstance(validation, dict) and validation.get("status") == "passed_non_authorizing_only",
        "validation_receipt_status",
    )
    _require(
        validation.get("candidate_sha256") == predecessor.get("candidate_sha256")
        and validation.get("predecessor_sha256") == predecessor.get("certificate_sha256"),
        "validation_receipt_lineage",
    )
    _require(
        isinstance(completion, dict)
        and completion.get("bootstrap_and_frame_sealed") is True
        and completion.get("clean_expired_without_stop") is True
        and completion.get("plan_sha256") == expected_certificate.get("plan_sha256"),
        "completion_receipt_status",
    )
    state = v1.verify(
        reader,
        expected_plan_sha256=expected_certificate.get("plan_sha256"),
        expected_sources=cast(Mapping[str, str], expected_certificate.get("source_sha256")),
    )
    state["plan"] = decode(reader.read_bytes("external-plan-v1.json"))
    _verify_certificate_root(reader, state, expected_certificate)
    _require(
        state.get("status") == "verified_prefix" and state.get("stop") is None,
        "predecessor_not_clean",
    )
    for _phase, bucket in (
        ("bootstrap", expected_certificate["bootstrap"]["bucket"]),
        ("frame", expected_certificate["frame"]["bucket"]),
    ):
        _require(
            state.get("buckets", {}).get(bucket, {}).get("status") == "sealed",
            "predecessor_bucket_unsealed",
        )
    _require(
        state.get("frontier") == expected_certificate["frontier"], "predecessor_frontier_mismatch"
    )
    _require(
        state.get("requests") == expected_certificate["requests"]
        and state.get("raw_bytes") == expected_certificate["raw_bytes"]
        and state.get("stored_bytes") == expected_certificate["stored_bytes"],
        "predecessor_accounting_mismatch",
    )
    _require(state["plan"].get("provider") == "helius-free", "predecessor_provider")
    return state


def predecessor_daily_usage(reader: Any, certificate: Mapping[str, Any]) -> list[dict[str, int]]:
    """Rebuild v1 request-day usage from intent and raw capture bytes."""
    from tools import brain_senses_external_schema_v1 as v1_schema

    usage: dict[int, dict[str, int]] = {}
    files = set(reader.list_files())
    for bucket_ref in (certificate["bootstrap"], certificate["frame"]):
        bucket = bucket_ref["bucket"]
        prefix = f"buckets/{bucket}/requests/"
        request_paths = sorted(
            path for path in files if path.startswith(prefix) and path.endswith(".json")
        )
        for path in request_paths:
            request = decode(reader.read_bytes(path))
            _require(
                request.get("schema") == v1_schema.REQUEST_SCHEMA
                and request.get("provider") == "helius-free",
                "predecessor_request_schema",
            )
            ordinal = request.get("ordinal")
            _require(
                type(ordinal) is int and path.endswith(f"/{ordinal:02d}.json"),
                "predecessor_request_ordinal",
            )
            response_path = f"buckets/{bucket}/captures/{ordinal:02d}/response.json"
            raw = reader.read_bytes(response_path)
            result = decode(reader.read_bytes(f"buckets/{bucket}/results/{ordinal:02d}.json"))
            _require(
                result.get("status") == "captured" and result.get("response_bytes") == len(raw),
                "predecessor_result_usage",
            )
            started = request.get("request_started_ns")
            _require(_integer(started, 1), "predecessor_request_time")
            day = started // 86_400_000_000_000
            record = usage.setdefault(
                day, {"utc_day": day, "requests": 0, "raw_bytes": 0, "stored_bytes": 0}
            )
            record["requests"] += 1
            record["raw_bytes"] += len(raw)
            record["stored_bytes"] += len(raw)
    result = [usage[key] for key in sorted(usage)]
    _require(
        sum(x["requests"] for x in result) == certificate["requests"],
        "predecessor_daily_request_total",
    )
    _require(
        sum(x["raw_bytes"] for x in result) == certificate["raw_bytes"],
        "predecessor_daily_raw_total",
    )
    _require(
        sum(x["stored_bytes"] for x in result) == certificate["stored_bytes"],
        "predecessor_daily_stored_total",
    )
    return result


def _verify_certificate_root(
    reader: Any, state: Mapping[str, Any], certificate: Mapping[str, Any]
) -> None:
    """Bind the certificate to exact v1 plan, event-chain, bundle and seal bytes."""
    from tools import brain_senses_external_schema_v1 as v1_schema
    from tools import brain_senses_external_verify_v1 as v1

    plan_raw = reader.read_bytes(v1_schema.PLAN_FILE)
    plan = decode(plan_raw)
    _require(sha(plan_raw) == certificate.get("plan_sha256"), "certificate_plan_hash")
    _require(plan.get("code_sha256") == certificate.get("source_sha256"), "certificate_sources")
    _require(plan.get("storage") == certificate.get("storage"), "certificate_storage")
    events_raw = reader.read_bytes(v1_schema.EVENTS_FILE)
    events = v1.verify_events(events_raw)
    _require(bool(events), "certificate_events_empty")
    _require(
        sha(events_raw) == certificate.get("events_file_sha256")
        and events[-1]["event_sha256"] == certificate.get("event_tail_sha256"),
        "certificate_event_chain_pin",
    )
    for phase in ("bootstrap", "frame"):
        ref = certificate[phase]
        bucket = ref["bucket"]
        bundle_raw = reader.read_bytes(f"buckets/{bucket}/bundle.json")
        seal_raw = reader.read_bytes(f"buckets/{bucket}/seal.json")
        _require(sha(bundle_raw) == ref["bundle_sha256"], "certificate_bundle_pin")
        _require(sha(seal_raw) == ref["seal_sha256"], "certificate_seal_pin")
        _require(
            state.get("buckets", {}).get(bucket, {}).get("bundle_sha256") == ref["bundle_sha256"],
            "certificate_state_bundle_pin",
        )


def verify_live_predecessor(reader: Any, predecessor: Mapping[str, Any]) -> dict[str, Any]:
    """Reopen and reverify the immutable v1 sibling before every v2 check."""
    from tools import brain_senses_external_verify_v1 as v1

    cert = predecessor["certificate"]
    state = v1.verify(
        reader,
        expected_plan_sha256=cert["plan_sha256"],
        expected_sources=cert["source_sha256"],
    )
    plan = decode(reader.read_bytes("external-plan-v1.json"))
    state["plan"] = plan
    _verify_certificate_root(reader, state, cert)
    _require(
        state["status"] == "verified_prefix" and state["stop"] is None, "predecessor_not_clean"
    )
    _require(plan["storage"]["root_id"] == cert["storage"]["root_id"], "predecessor_root_id")
    _require(
        plan["storage"]["volume_uuid"] == cert["storage"]["volume_uuid"], "predecessor_volume_uuid"
    )
    _require(
        plan["storage"]["relative_root"] == predecessor["relative_root"], "predecessor_root_path"
    )
    _require(
        state["frontier"] == cert["frontier"] and state["gap_intervals"] == cert["gap_intervals"],
        "predecessor_lineage_mismatch",
    )
    _require(
        state["requests"] == 35 and state["requests"] == cert["requests"],
        "predecessor_request_count",
    )
    _require(
        state["raw_bytes"] == cert["raw_bytes"] and state["stored_bytes"] == cert["stored_bytes"],
        "predecessor_byte_totals",
    )
    for phase in ("bootstrap", "frame"):
        bucket = cert[phase]["bucket"]
        _require(
            state["buckets"].get(bucket, {}).get("status") == "sealed", "predecessor_seal_missing"
        )
        _require(
            state["buckets"][bucket]["bundle_sha256"] == cert[phase]["bundle_sha256"],
            "predecessor_bundle_changed",
        )
    state["day_usage"] = predecessor_daily_usage(reader, cert)
    return state


def verify_events(payload: bytes) -> list[dict[str, Any]]:
    _require(not payload or payload.endswith(b"\n"), "incomplete_event_tail")
    events: list[dict[str, Any]] = []
    previous = "0" * 64
    last_at = 0
    allowed = {
        "initialized",
        "reservation",
        "started",
        "request_intent",
        "bundle",
        "seal",
        "missed",
        "stop",
    }
    for line in payload.splitlines():
        event = decode(line)
        _fields(
            event,
            "schema schema_version seq kind bucket at_ns payload previous_sha256 event_sha256",
        )
        _require(
            event["schema"] == schema.EVENT_SCHEMA
            and type(event["schema_version"]) is int
            and event["schema_version"] == 2,
            "event_schema",
        )
        _require(type(event["seq"]) is int and event["seq"] == len(events), "event_sequence")
        _require(
            event["previous_sha256"] == previous and event["kind"] in allowed, "event_chain_or_kind"
        )
        _require(
            _integer(event["at_ns"], 1)
            and event["at_ns"] >= last_at
            and isinstance(event["payload"], dict),
            "event_time_or_payload",
        )
        body = {key: val for key, val in event.items() if key != "event_sha256"}
        _require(
            _digest(event["event_sha256"]) and sha(canonical(body)) == event["event_sha256"],
            "event_digest",
        )
        _require(not events or events[-1]["kind"] != "stop", "event_after_stop")
        if not events:
            _require(event["kind"] == "initialized" and event["bucket"] is None, "initial_event")
        else:
            _require(event["kind"] != "initialized", "duplicate_initialization")
        events.append(event)
        previous, last_at = event["event_sha256"], event["at_ns"]
    return events


def _json_file(reader: Any, path: str, expected_schema: str) -> tuple[dict[str, Any], bytes]:
    raw = reader.read_bytes(path)
    value = decode(raw)
    _require(isinstance(value, dict) and value.get("schema") == expected_schema, "file_schema")
    _require(
        type(value.get("schema_version")) is int and value["schema_version"] == 2, "file_version"
    )
    _require(canonical(value) == raw, "file_noncanonical")
    return value, raw


def _rpc_result(raw: bytes) -> Any:
    value = decode(raw)
    _require(
        isinstance(value, dict) and value.get("jsonrpc") == "2.0" and value.get("id") == 1,
        "rpc_envelope",
    )
    _require("error" not in value and "result" in value, "rpc_result_missing_or_error")
    return value["result"]


def _check_block_params(params: Any, slot: int, detail: str) -> None:
    _require(
        isinstance(params, list)
        and len(params) == 2
        and params[0] == slot
        and isinstance(params[1], dict),
        "selected_block_params",
    )
    options = {
        "commitment": "finalized",
        "encoding": "json",
        "transactionDetails": detail,
        "rewards": False,
    }
    if detail == "full":
        options["maxSupportedTransactionVersion"] = 1
    _require(
        detail in {"signatures", "full"} and params == [slot, options],
        "selected_block_params",
    )


def _verify_bucket(
    reader: Any,
    plan: dict[str, Any],
    bucket: str,
    frontier: int,
    *,
    require_seal: bool = True,
    manifest_files: set[str] | None = None,
) -> dict[str, Any]:
    """Independently reconstruct the exact request sequence and sealed sample."""
    from tools.brain_senses_block_epoch_v2 import _build_selection
    from tools.brain_senses_block_provider_pilot import (
        _signature_list,
        _structural_response_validation,
    )

    phase = "bootstrap" if bucket == plan["schedule"]["bootstrap"]["bucket"] else "frame"
    frame_index = (
        None
        if phase == "bootstrap"
        else next(x["index"] for x in plan["schedule"]["frames"] if x["bucket"] == bucket)
    )
    base = f"buckets/{bucket}"
    event_files = manifest_files if manifest_files is not None else set(reader.list_files())
    all_events = (
        verify_events(reader.read_bytes(schema.EVENTS_FILE))
        if schema.EVENTS_FILE in event_files
        else []
    )
    bucket_events = [event for event in all_events if event["bucket"] == bucket]
    window = (
        plan["schedule"]["bootstrap"]
        if phase == "bootstrap"
        else plan["schedule"]["frames"][frame_index]
    )
    reservation, reservation_raw = _json_file(
        reader, f"{base}/reservation.json", schema.BUCKET_SCHEMA
    )
    _fields(
        reservation,
        "schema schema_version bucket phase frame_index reservation_id plan_sha256 "
        "request_limit raw_byte_cap stored_byte_cap window created_ns "
        "predecessor_event_sha256 clock_check_path clock_check_sha256",
    )
    _require(
        reservation.get("bucket") == bucket
        and reservation.get("phase") == phase
        and reservation.get("frame_index") == frame_index,
        "reservation_identity",
    )
    _require(
        reservation.get("plan_sha256") == sha(reader.read_bytes(schema.PLAN_FILE)),
        "reservation_plan",
    )
    _require(
        reservation.get("window") == {"start_ns": window["start_ns"], "end_ns": window["end_ns"]}
        and _integer(reservation.get("created_ns"), 1)
        and window["start_ns"] + 5 * _SECOND_NS
        <= reservation["created_ns"]
        <= window["end_ns"] - 5 * _SECOND_NS
        and reservation["created_ns"] < plan["schedule"]["expires_ns"],
        "reservation_window_time",
    )
    clock_path = reservation["clock_check_path"]
    _require(
        isinstance(clock_path, str)
        and re.fullmatch(r"clock/" + re.escape(bucket) + r"-[0-9]{2}\.json", clock_path)
        is not None,
        "reservation_clock_path",
    )
    clock_raw = reader.read_bytes(clock_path)
    _require(sha(clock_raw) == reservation["clock_check_sha256"], "reservation_clock_digest")
    clock_record = decode(clock_raw)
    try:
        clock_contract.validate_clock(clock_record)
    except Exception as exc:
        raise VerificationError("reservation_clock_record") from exc
    started, started_raw = _json_file(reader, f"{base}/started.json", schema.BUCKET_SCHEMA)
    _fields(started, "schema schema_version reservation_id plan_sha256 started_ns pid")
    _require(
        started.get("reservation_id") == reservation.get("reservation_id")
        and started.get("plan_sha256") == reservation.get("plan_sha256")
        and _integer(started.get("started_ns"), 1)
        and reservation["created_ns"] <= started["started_ns"] <= window["end_ns"] - 5 * _SECOND_NS
        and started["started_ns"] < plan["schedule"]["expires_ns"],
        "started_reservation_or_window",
    )
    reservation_events = [event for event in bucket_events if event["kind"] == "reservation"]
    started_events = [event for event in bucket_events if event["kind"] == "started"]
    if bucket_events:
        _require(
            len(reservation_events) == len(started_events) == 1
            and reservation_events[0]["at_ns"] == reservation["created_ns"]
            and reservation_events[0]["payload"]
            == {"reservation_sha256": sha(reservation_raw), "frame_index": frame_index}
            and started_events[0]["at_ns"] == started["started_ns"]
            and started_events[0]["payload"] == {"started_sha256": sha(started_raw)},
            "reservation_started_event_time_binding",
        )
    limit = 1 if phase == "bootstrap" else cast(int, schema.CAPS["frame_requests"])
    requests: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    raws: list[bytes] = []
    receipts: list[bytes] = []
    previous_available = 0
    for ordinal in range(limit):
        path = f"{base}/requests/{ordinal:02d}.json"
        if path not in event_files:
            break
        request, request_raw = _json_file(reader, path, schema.REQUEST_SCHEMA)
        _fields(
            request,
            "schema schema_version provider request_id reservation_id ordinal phase frame_index "
            "method params request_sha256 wire_request_sha256 decision_ns request_started_ns "
            "monotonic_ns clock_check_path clock_check_sha256",
        )
        _require(
            request["provider"] == plan["provider"]
            and request["reservation_id"] == reservation["reservation_id"],
            "request_reservation",
        )
        _require(
            request["ordinal"] == ordinal
            and request["request_id"] == f"{bucket}-{ordinal:02d}"
            and request["phase"] == phase
            and request["frame_index"] == frame_index,
            "request_identity",
        )
        _require(
            request["request_started_ns"] >= request["decision_ns"]
            and request["request_started_ns"] <= window["end_ns"] - 5 * _SECOND_NS
            and window["start_ns"] + 5 * _SECOND_NS <= request["decision_ns"]
            and request["decision_ns"] < plan["schedule"]["expires_ns"]
            and _integer(request["monotonic_ns"], 1)
            and request["clock_check_path"] == clock_path
            and request["clock_check_sha256"] == reservation["clock_check_sha256"],
            "request_clock_order",
        )
        try:
            clock_contract.validate_progress(
                clock_record,
                wall_ns=request["decision_ns"],
                monotonic_ns=request["monotonic_ns"],
            )
        except Exception as exc:
            raise VerificationError("request_clock_progress") from exc
        method, params = request["method"], request["params"]
        semantic = sha(canonical({"method": method, "params": params}))
        wire = sha(
            canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).rstrip(b"\n")
        )
        _require(
            semantic == request["request_sha256"] and wire == request["wire_request_sha256"],
            "request_hash",
        )
        intent_events = [
            event
            for event in bucket_events
            if event["kind"] == "request_intent" and event["payload"].get("ordinal") == ordinal
        ]
        if bucket_events:
            _require(
                len(intent_events) == 1
                and intent_events[0]["at_ns"] == request["decision_ns"]
                and intent_events[0]["payload"]["request_sha256"] == sha(request_raw)
                and intent_events[0]["payload"]["request_started_ns"]
                == request["request_started_ns"],
                "request_intent_event_time_binding",
            )
        if method == "getSlot":
            _require(params == [{"commitment": "finalized"}], "slot_request_options")
        result_path = f"{base}/results/{ordinal:02d}.json"
        if result_path not in event_files:
            break
        result, result_raw = _json_file(reader, result_path, schema.RESULT_SCHEMA)
        _fields(
            result,
            "schema schema_version provider request_id request_sha256 wire_request_sha256 "
            "status result_status response_sha256 response_bytes stored_response_bytes "
            "availability_ns decision_ns method event_slot produced_slots signatures "
            "block_identity event_time_ns http_status transport_category",
        )
        _require(
            result.get("provider") == plan["provider"]
            and result.get("request_id") == request["request_id"]
            and result.get("request_sha256") == semantic
            and result.get("wire_request_sha256") == wire
            and result.get("decision_ns") == request["decision_ns"],
            "result_request_binding",
        )
        capture_base = f"{base}/captures/{ordinal:02d}"
        receipt, receipt_raw = _json_file(
            reader, f"{capture_base}/receipt.json", schema.RECEIPT_SCHEMA
        )
        _fields(
            receipt,
            "schema schema_version provider request_id request_sha256 wire_request_sha256 "
            "request_started_ns received_ns availability_ns response_sha256 response_bytes storage",
        )
        raw = reader.read_bytes(f"{capture_base}/response.json")
        _require(
            receipt.get("provider") == plan["provider"]
            and receipt.get("request_id") == request["request_id"],
            "receipt_request_binding",
        )
        _require(
            receipt.get("request_sha256") == semantic
            and receipt.get("wire_request_sha256") == wire,
            "receipt_hash_binding",
        )
        _require(
            _integer(receipt.get("request_started_ns"), 1)
            and _integer(receipt.get("received_ns"), 1)
            and receipt["request_started_ns"] >= request["request_started_ns"]
            and receipt["received_ns"] >= receipt["request_started_ns"],
            "receipt_time_order",
        )
        _require(
            receipt.get("response_sha256") == sha(raw)
            and receipt.get("response_bytes") == len(raw),
            "capture_digest",
        )
        _require(
            receipt.get("storage") == {"path": "response.json", "bytes": len(raw), "codec": "raw"},
            "capture_storage",
        )
        _require(receipt.get("availability_ns") is None, "receipt_availability_semantics")
        capture_started_ns = cast(int, receipt.get("request_started_ns"))
        received_ns = cast(int, receipt.get("received_ns"))
        _require(
            capture_started_ns >= request["request_started_ns"]
            and received_ns >= capture_started_ns,
            "receipt_time_order",
        )
        availability = result.get("availability_ns")
        _require(
            _integer(availability, 1) and availability >= receipt.get("received_ns", 0),
            "result_availability",
        )
        _require(
            result.get("response_sha256") == sha(raw)
            and result.get("response_bytes") == len(raw)
            and result.get("stored_response_bytes") == len(raw),
            "result_capture_binding",
        )
        _require(
            result.get("status") == "captured" and result.get("result_status") == "parsed",
            "nonparsed_result_in_complete_bundle",
        )
        rpc_value = _rpc_result(raw)
        _require(result.get("method") == method, "result_method")
        if method == "getSlot":
            _require(
                result.get("event_slot") == rpc_value
                and result.get("produced_slots") is None
                and result.get("signatures") is None
                and result.get("block_identity") is None
                and result.get("event_time_ns") is None,
                "slot_result_projection",
            )
        elif method == "getBlocks":
            _require(
                result.get("event_slot") is None
                and result.get("produced_slots") == rpc_value
                and result.get("signatures") is None
                and result.get("block_identity") is None
                and result.get("event_time_ns") is None,
                "inventory_result_projection",
            )
        else:
            _require(isinstance(rpc_value, dict), "block_result_type")
            _require(
                isinstance(params, list) and len(params) == 2 and isinstance(params[1], dict),
                "selected_block_params",
            )
            detail = params[1].get("transactionDetails")
            _check_block_params(params, cast(int, params[0]), cast(str, detail))
            if detail == "full":
                _require(
                    not _structural_response_validation(rpc_value, params[0]),
                    "full_block_structure",
                )
            signatures_value = _signature_list(rpc_value, detail)
            identity = {
                key: rpc_value.get(key)
                for key in (
                    "blockhash",
                    "previousBlockhash",
                    "parentSlot",
                    "blockTime",
                    "blockHeight",
                )
            }
            block_time = rpc_value.get("blockTime")
            event_time = block_time * 1_000_000_000 if type(block_time) is int else None
            _require(
                result.get("event_slot") == params[0]
                and result.get("produced_slots") is None
                and result.get("signatures") == signatures_value
                and result.get("block_identity") == identity
                and result.get("event_time_ns") == event_time,
                "block_result_projection",
            )
        if previous_available:
            _require(
                request["decision_ns"] >= previous_available,
                "request_decision_before_previous_availability",
            )
        previous_available = cast(int, availability)
        requests.append(request)
        results.append(result)
        raws.append(raw)
        receipts.append(receipt_raw)
    _require(bool(requests) and len(requests) == len(results), "empty_bucket_requests")
    # The final bundle contains only successful requests and is checked against
    # the exact request/result/capture byte hashes above.
    bundle, bundle_raw = _json_file(reader, f"{base}/bundle.json", schema.BUNDLE_SCHEMA)
    _fields(
        bundle,
        "schema schema_version provider phase frame_index bucket reservation_id plan_sha256 "
        "status request_count requests raw_bytes stored_bytes bootstrap_slot frame "
        "selected_blocks provider_charge_units provider_charge_status terminal_status",
    )
    entries = [
        {
            "ordinal": i,
            "request_sha256": sha(reader.read_bytes(f"{base}/requests/{i:02d}.json")),
            "receipt_sha256": sha(receipts[i]),
            "result_sha256": sha(reader.read_bytes(f"{base}/results/{i:02d}.json")),
            "response_sha256": sha(raws[i]),
        }
        for i in range(len(requests))
    ]
    _require(
        bundle.get("provider") == plan["provider"]
        and bundle.get("phase") == phase
        and bundle.get("frame_index") == frame_index
        and bundle.get("bucket") == bucket,
        "bundle_identity",
    )
    _require(
        bundle.get("reservation_id") == reservation.get("reservation_id")
        and bundle.get("plan_sha256") == sha(reader.read_bytes(schema.PLAN_FILE)),
        "bundle_plan_binding",
    )
    _require(
        bundle.get("status") == "complete" and bundle.get("terminal_status") == phase + "_complete",
        "bundle_status",
    )
    _require(
        bundle.get("provider_charge_units") is None
        and bundle.get("provider_charge_status") == "unknown_unverified",
        "bundle_charge_claim",
    )
    _require(
        bundle.get("requests") == entries and bundle.get("request_count") == len(entries),
        "bundle_request_entries",
    )
    raw_total = sum(len(raw) for raw in raws)
    phase_cap = (
        schema.CAPS["bootstrap_raw_bytes"]
        if phase == "bootstrap"
        else schema.CAPS["frame_raw_bytes"]
    )
    _require(raw_total <= cast(int, phase_cap), "phase_raw_byte_cap")
    _require(
        bundle.get("raw_bytes") == raw_total and bundle.get("stored_bytes") == raw_total,
        "bundle_byte_totals",
    )
    bundle_events = [event for event in bucket_events if event["kind"] == "bundle"]
    if bucket_events:
        if bundle_events:
            _require(
                len(bundle_events) == 1
                and bundle_events[0]["payload"] == {"bundle_sha256": sha(bundle_raw)}
                and bundle_events[0]["at_ns"] >= previous_available,
                "bundle_event_availability_binding",
            )
        else:
            _require(not require_seal, "bundle_event_missing")
    values = [_rpc_result(raw) for raw in raws]
    selected_blocks: list[dict[str, Any]] = []
    if phase == "bootstrap":
        _require(
            len(requests) == 1 and requests[0]["method"] == "getSlot",
            "bootstrap_operation_sequence",
        )
        bootstrap_slot = values[0]
        _require(
            _integer(bootstrap_slot, 1)
            and bootstrap_slot >= plan["predecessor"]["certificate"]["frontier"],
            "invalid_bootstrap_slot",
        )
        expected_frame = None
        next_frontier = bootstrap_slot
    else:
        _require(
            len(requests) >= 2 and requests[0]["method"] == "getSlot", "frame_operation_sequence"
        )
        _require(
            requests[0]["params"] == [{"commitment": "finalized"}],
            "frame_anchor_request_options",
        )
        anchor = values[0]
        _require(_integer(anchor, 1) and anchor >= frontier, "frame_anchor_regression")
        width = anchor - frontier + 1
        _require(width <= schema.MAX_FRAME_WIDTH, "frame_width_exceeded")
        _require(
            requests[1]["method"] == "getBlocks"
            and requests[1]["params"] == [frontier, anchor, {"commitment": "finalized"}],
            "inventory_request_mismatch",
        )
        inventory = values[1]
        _require(
            isinstance(inventory, list) and inventory == sorted(set(inventory)), "inventory_order"
        )
        _require(
            all(_integer(slot, frontier) and slot <= anchor for slot in inventory),
            "inventory_range",
        )
        selection = _build_selection(
            plan={
                "seed": plan["service_id"],
                "epoch_id": bucket,
                "selection_count": schema.SELECTION_COUNT,
                "selection_rule": "uniform_without_replacement_from_finalized_getblocks_members",
            },
            inventory_slots=inventory,
            inventory_sha256=sha(raws[1]),
        )
        expected_methods: list[tuple[str, int | None, str | None]] = [
            ("getSlot", None, None),
            ("getBlocks", None, None),
        ]
        for slot in selection["selected_slots"]:
            expected_methods.extend([("getBlock", slot, "signatures"), ("getBlock", slot, "full")])
        _require(len(requests) == len(expected_methods), "frame_request_count")
        for request, (method, slot, detail) in zip(requests, expected_methods, strict=True):
            _require(request["method"] == method, "frame_method_order")
            if method == "getBlocks":
                _require(
                    request["params"] == [frontier, anchor, {"commitment": "finalized"}],
                    "frame_inventory_params",
                )
            elif method == "getBlock":
                params = request["params"]
                _check_block_params(params, cast(int, slot), cast(str, detail))
        for index, slot in enumerate(selection["selected_slots"]):
            signatures = values[2 + index * 2]
            full = values[3 + index * 2]
            sig_list = _signature_list(signatures, "signatures")
            full_list = _signature_list(full, "full")
            _require(sig_list is not None and sig_list == full_list, "signature_pair_mismatch")
            _require(
                not _structural_response_validation(full, slot),
                "full_block_structure",
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
                "block_identity_mismatch",
            )
            selected_blocks.append(
                {
                    "slot": slot,
                    "signatures": full_list,
                    "block_identity": {
                        key: full.get(key)
                        for key in (
                            "blockhash",
                            "previousBlockhash",
                            "parentSlot",
                            "blockTime",
                            "blockHeight",
                        )
                    },
                }
            )
        expected_frame = {
            "start": frontier,
            "end_exclusive": anchor + 1,
            "anchor_slot": anchor,
            "width": width,
            "inventory_sha256": selection["inventory_sha256"],
            "denominator": selection["inventory_denominator"],
            "selection_count": selection["selection_count"],
            "selected_slots": selection["selected_slots"],
            "inclusion_probability": selection["inclusion_probability"],
            "seed": selection["seed"],
            "selection_rule": selection["selection_rule"],
        }
        _require(
            bundle.get("bootstrap_slot") is None and bundle.get("frame") == expected_frame,
            "frame_summary_mismatch",
        )
        next_frontier = anchor + 1
        bootstrap_slot = None
    if phase == "bootstrap":
        _require(
            bundle.get("bootstrap_slot") == bootstrap_slot
            and bundle.get("frame") is None
            and bundle.get("selected_blocks") == [],
            "bootstrap_summary_mismatch",
        )
    else:
        _require(bundle.get("selected_blocks") == selected_blocks, "selected_blocks_summary")
    seal_path = f"{base}/seal.json"
    if not require_seal and seal_path not in event_files:
        return {
            "phase": phase,
            "frame_index": frame_index,
            "bundle": bundle,
            "bundle_sha256": sha(bundle_raw),
            "latest_available_ns": previous_available,
            "frontier": next_frontier,
        }
    seal, seal_raw = _json_file(reader, seal_path, schema.SEAL_SCHEMA)
    _fields(
        seal,
        "schema schema_version bucket phase frame_index reservation_id plan_sha256 "
        "bundle_sha256 sealed_ns status",
    )
    _require(
        seal.get("bucket") == bucket
        and seal.get("phase") == phase
        and seal.get("frame_index") == frame_index,
        "seal_identity",
    )
    _require(
        seal.get("reservation_id") == reservation["reservation_id"]
        and seal.get("plan_sha256") == sha(reader.read_bytes(schema.PLAN_FILE)),
        "seal_plan_binding",
    )
    _require(
        seal.get("bundle_sha256") == sha(bundle_raw) and seal.get("status") == "complete",
        "seal_bundle_binding",
    )
    _require(
        _integer(seal.get("sealed_ns")) and seal["sealed_ns"] >= previous_available,
        "seal_time_order",
    )
    seal_events = [event for event in bucket_events if event["kind"] == "seal"]
    if bucket_events:
        if seal_events:
            _require(
                len(seal_events) == 1
                and seal_events[0]["at_ns"] == seal["sealed_ns"]
                and seal_events[0]["at_ns"] >= bundle_events[0]["at_ns"]
                and seal_events[0]["payload"] == {"seal_sha256": sha(seal_raw)},
                "seal_event_time_binding",
            )
        else:
            _require(not require_seal, "seal_event_missing")
    return {
        "phase": phase,
        "frame_index": frame_index,
        "bundle": bundle,
        "bundle_sha256": sha(bundle_raw),
        "seal": seal,
        "seal_sha256": sha(seal_raw),
        "latest_available_ns": previous_available,
        "frontier": next_frontier,
        "first_decision_ns": requests[0]["decision_ns"],
        "seal_ns": seal["sealed_ns"],
    }


def _verify_intent_prefix(
    reader: Any,
    plan: dict[str, Any],
    bucket: str,
    frontier: int,
    ordinals: list[int],
    files: set[str],
) -> None:
    """Validate the RPC state machine for sealed and interrupted buckets alike."""
    from tools.brain_senses_block_epoch_v2 import _build_selection
    from tools.brain_senses_block_provider_pilot import (
        _signature_list,
        _structural_response_validation,
    )

    is_bootstrap = bucket == plan["schedule"]["bootstrap"]["bucket"]
    frame_index = (
        None
        if is_bootstrap
        else next(row["index"] for row in plan["schedule"]["frames"] if row["bucket"] == bucket)
    )
    operations: list[tuple[str, list[Any]]] = [("getSlot", [{"commitment": "finalized"}])]
    previous_available = 0
    anchor: int | None = None
    selection: dict[str, Any] | None = None
    prior_signatures: list[str] | None = None
    prior_identity: dict[str, Any] | None = None
    for position, ordinal in enumerate(ordinals):
        base = f"buckets/{bucket}"
        request_path = f"{base}/requests/{ordinal:02d}.json"
        if request_path not in files:
            _require(position == len(ordinals) - 1, "intent_after_missing_request")
            return
        request = decode(reader.read_bytes(request_path))
        _require(
            isinstance(request, dict)
            and request.get("provider") == plan["provider"]
            and request.get("phase") == ("bootstrap" if is_bootstrap else "frame")
            and request.get("frame_index") == frame_index
            and request.get("ordinal") == ordinal
            and request.get("request_id") == f"{bucket}-{ordinal:02d}",
            "prefix_request_identity",
        )
        method, params = request.get("method"), request.get("params")
        _require(
            request.get("request_sha256") == sha(canonical({"method": method, "params": params}))
            and request.get("wire_request_sha256")
            == sha(
                canonical(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": method,
                        "params": params,
                    }
                ).rstrip(b"\n")
            ),
            "prefix_request_hashes",
        )
        decision_ns = request.get("decision_ns")
        _require(_integer(decision_ns, 1), "prefix_decision_time")
        clock_path = request.get("clock_check_path")
        _require(
            clock_path == f"clock/{bucket}-00.json" and clock_path in files,
            "prefix_clock_path",
        )
        clock_raw = reader.read_bytes(clock_path)
        _require(sha(clock_raw) == request.get("clock_check_sha256"), "prefix_clock_digest")
        try:
            clock_contract.validate_progress(
                decode(clock_raw), wall_ns=decision_ns, monotonic_ns=request.get("monotonic_ns")
            )
        except Exception as exc:
            raise VerificationError("prefix_clock_progress") from exc
        _require(bool(operations), "request_after_operation_sequence")
        expected_method, expected_params = operations.pop(0)
        _require(
            method == expected_method and params == expected_params,
            "prefix_operation_mismatch",
        )
        if previous_available:
            _require(decision_ns >= previous_available, "prefix_decision_before_availability")
        result_path = f"{base}/results/{ordinal:02d}.json"
        response_path = f"{base}/captures/{ordinal:02d}/response.json"
        receipt_path = f"{base}/captures/{ordinal:02d}/receipt.json"
        if result_path not in files:
            _require(position == len(ordinals) - 1, "intent_after_unresolved_request")
            return
        result = decode(reader.read_bytes(result_path))
        result_status = result.get("result_status")
        if result_status != "parsed":
            _require(position == len(ordinals) - 1, "intent_after_failed_result")
            return
        _require(
            response_path in files and receipt_path in files,
            "intent_after_nondurable_result",
        )
        raw = reader.read_bytes(response_path)
        receipt = decode(reader.read_bytes(receipt_path))
        _require(
            result.get("availability_ns") is not None
            and receipt.get("availability_ns") is None
            and result.get("availability_ns") >= receipt.get("received_ns", 0),
            "prefix_result_not_durable",
        )
        value = _rpc_result(raw)
        previous_available = result["availability_ns"]
        if method == "getSlot":
            if is_bootstrap:
                _require(
                    _integer(value, 1) and value >= plan["predecessor"]["certificate"]["frontier"],
                    "prefix_bootstrap_frontier",
                )
            else:
                if (
                    not _integer(value, 1)
                    or value < frontier
                    or value - frontier + 1 > schema.MAX_FRAME_WIDTH
                ):
                    _require(position == len(ordinals) - 1, "intent_after_bad_frame_anchor")
                    return
                anchor = value
                operations.append(("getBlocks", [frontier, anchor, {"commitment": "finalized"}]))
        elif method == "getBlocks":
            _require(anchor is not None, "prefix_inventory_without_anchor")
            _require(
                isinstance(value, list)
                and value == sorted(set(value))
                and all(_integer(slot, frontier) and slot <= anchor for slot in value),
                "prefix_inventory_invalid",
            )
            selection = _build_selection(
                plan={
                    "seed": plan["service_id"],
                    "epoch_id": bucket,
                    "selection_count": schema.SELECTION_COUNT,
                    "selection_rule": (
                        "uniform_without_replacement_from_finalized_getblocks_members"
                    ),
                },
                inventory_slots=value,
                inventory_sha256=sha(raw),
            )
            for slot in selection["selected_slots"]:
                operations.extend(
                    [
                        (
                            "getBlock",
                            [
                                slot,
                                {
                                    "commitment": "finalized",
                                    "encoding": "json",
                                    "transactionDetails": "signatures",
                                    "rewards": False,
                                },
                            ],
                        ),
                        (
                            "getBlock",
                            [
                                slot,
                                {
                                    "commitment": "finalized",
                                    "encoding": "json",
                                    "transactionDetails": "full",
                                    "rewards": False,
                                    "maxSupportedTransactionVersion": 1,
                                },
                            ],
                        ),
                    ]
                )
        else:
            detail = params[1]["transactionDetails"]
            slot = cast(int, params[0])
            signatures = _signature_list(value, detail)
            _require(signatures is not None, "prefix_block_signatures")
            if detail == "full":
                _require(
                    not _structural_response_validation(value, slot), "prefix_full_block_shape"
                )
                identity = {
                    key: value.get(key)
                    for key in (
                        "blockhash",
                        "previousBlockhash",
                        "parentSlot",
                        "blockTime",
                        "blockHeight",
                    )
                }
                _require(
                    prior_signatures == signatures and prior_identity == identity,
                    "prefix_signature_pair_mismatch",
                )
                prior_signatures = None
                prior_identity = None
            else:
                prior_signatures = signatures
                prior_identity = {
                    key: value.get(key)
                    for key in (
                        "blockhash",
                        "previousBlockhash",
                        "parentSlot",
                        "blockTime",
                        "blockHeight",
                    )
                }


def _verify_file_closure(
    reader: Any,
    plan: dict[str, Any],
    files: set[str],
    events: list[dict[str, Any]],
    reservations: Mapping[str, Any],
    intents: Mapping[str, list[int]],
    sealed: Mapping[str, bool],
) -> None:
    """Reject unreferenced storage, including extra files below sealed buckets."""
    allowed = {schema.PLAN_FILE, schema.EVENTS_FILE, schema.STOP_FILE, schema.LOCK_FILE}
    scheduled = [
        plan["schedule"]["bootstrap"],
        *plan["schedule"]["frames"],
    ]
    referenced_clocks: set[str] = set()
    from tools.brain_senses_external_clock_v1 import validate_clock

    for window in scheduled:
        bucket = window["bucket"]
        base = f"buckets/{bucket}"
        bucket_events = [event for event in events if event["bucket"] == bucket]
        kinds = [event["kind"] for event in bucket_events]
        if "reservation" in kinds:
            allowed.add(f"{base}/reservation.json")
            reservation_path = f"{base}/reservation.json"
            if reservation_path in files:
                reservation = decode(reader.read_bytes(reservation_path))
                clock_path = reservation.get("clock_check_path")
                _require(
                    isinstance(clock_path, str) and clock_path == f"clock/{bucket}-00.json",
                    "reservation_clock_path",
                )
                referenced_clocks.add(clock_path)
                _require(
                    clock_path not in files
                    or sha(reader.read_bytes(clock_path)) == reservation.get("clock_check_sha256"),
                    "reservation_clock_digest",
                )
        if "started" in kinds:
            allowed.add(f"{base}/started.json")
        for ordinal in intents.get(bucket, []):
            request_path = f"{base}/requests/{ordinal:02d}.json"
            allowed.add(request_path)
            request_published = request_path in files
            if request_published:
                request = decode(reader.read_bytes(request_path))
                clock_path = request.get("clock_check_path")
                _require(
                    isinstance(clock_path, str) and clock_path == f"clock/{bucket}-00.json",
                    "request_clock_path",
                )
                referenced_clocks.add(clock_path)
                _require(
                    clock_path not in files
                    or sha(reader.read_bytes(clock_path)) == request.get("clock_check_sha256"),
                    "request_clock_digest",
                )
            else:
                _require(
                    not any(
                        path in files
                        for path in (
                            f"{base}/captures/{ordinal:02d}/response.json",
                            f"{base}/captures/{ordinal:02d}/receipt.json",
                            f"{base}/results/{ordinal:02d}.json",
                        )
                    ),
                    "capture_without_published_request",
                )
            for path in (
                f"{base}/captures/{ordinal:02d}/response.json",
                f"{base}/captures/{ordinal:02d}/receipt.json",
                f"{base}/results/{ordinal:02d}.json",
            ):
                if path in files:
                    allowed.add(path)
        if (
            any(event["kind"] == "bundle" for event in bucket_events)
            or f"{base}/bundle.json" in files
        ):
            allowed.add(f"{base}/bundle.json")
        if any(event["kind"] == "seal" for event in bucket_events) or f"{base}/seal.json" in files:
            allowed.add(f"{base}/seal.json")

        # The clock is persisted before reservation. A crash in that narrow
        # interval can leave only the deterministic preflight file for an
        # otherwise unspent bucket; it grants no dispatch authority.
        orphan_clock = f"clock/{bucket}-00.json"
        if orphan_clock in files and bucket not in reservations and not intents.get(bucket):
            raw = reader.read_bytes(orphan_clock)
            record = decode(raw)
            _require(canonical(record) == raw, "orphan_clock_noncanonical")
            validate_clock(record)
            referenced_clocks.add(orphan_clock)

    for path in referenced_clocks:
        allowed.add(path)
        _require(path in files, "referenced_clock_missing")
        raw = reader.read_bytes(path)
        record = decode(raw)
        _require(canonical(record) == raw, "clock_noncanonical")
        validate_clock(record)
    for path in files:
        if path.startswith("clock/"):
            _require(path in referenced_clocks, "unreferenced_clock")
    _require(files <= allowed, "unreferenced_file")


def verify(
    reader: Any,
    *,
    expected_plan_sha256: str | None = None,
    expected_sources: Mapping[str, str] | None = None,
    predecessor_reader: Any | None = None,
) -> dict[str, Any]:
    """Verify the immutable plan, append-only event chain, and conservative prefix."""
    plan_bytes = reader.read_bytes(schema.PLAN_FILE)
    plan = decode(plan_bytes)
    _require(canonical(plan) == plan_bytes, "noncanonical_plan")
    verify_plan(plan, expected_sources)
    plan_hash = sha(plan_bytes)
    if expected_plan_sha256 is not None:
        _require(plan_hash == expected_plan_sha256, "plan_pin_mismatch")
    _require(predecessor_reader is not None, "predecessor_lineage_unavailable")
    files = set(reader.list_files())
    events = (
        verify_events(reader.read_bytes(schema.EVENTS_FILE)) if schema.EVENTS_FILE in files else []
    )
    _require(bool(events), "missing_initial_event")
    initial_payload = events[0]["payload"]
    _require(
        events[0]["kind"] == "initialized"
        and events[0]["bucket"] is None
        and set(initial_payload)
        in (
            {"plan_sha256"},
            {"plan_sha256", "predecessor_day_usage", "predecessor_day_usage_sha256"},
        )
        and initial_payload["plan_sha256"] == plan_hash,
        "initial_plan_binding",
    )
    predecessor_state = verify_live_predecessor(predecessor_reader, plan["predecessor"])
    _require(
        predecessor_state.get("plan", {}).get("provider") == plan["provider"]
        and predecessor_state.get("plan", {}).get("endpoint_origin") == plan["endpoint_origin"],
        "predecessor_provider_origin_binding",
    )
    predecessor_day_usage = predecessor_state["day_usage"]
    _require(
        "predecessor_day_usage" in initial_payload
        and initial_payload["predecessor_day_usage"] == predecessor_day_usage
        and initial_payload["predecessor_day_usage_sha256"]
        == sha(canonical(predecessor_day_usage)),
        "predecessor_day_usage_cache_mismatch",
    )
    lineage_verified = True
    reservations: dict[str, dict[str, Any]] = {}
    intents: dict[str, list[int]] = {}
    sealed: dict[str, bool] = {}
    event_order: dict[str, list[str]] = {}
    event_files: dict[tuple[str, str], tuple[str, int, str]] = {}
    missed: set[str] = set()
    previous_intent_ns = 0
    stopped = schema.STOP_FILE in files or any(e["kind"] == "stop" for e in events)
    scheduled = {
        plan["schedule"]["bootstrap"]["bucket"],
        *(row["bucket"] for row in plan["schedule"]["frames"]),
    }
    allowed_roots = {schema.PLAN_FILE, schema.EVENTS_FILE, schema.STOP_FILE, schema.LOCK_FILE}
    for path in files:
        if path in allowed_roots:
            continue
        if path.startswith("clock/"):
            _require(path.count("/") == 1 and path.endswith(".json"), "clock_path")
            continue
        pieces = path.split("/")
        _require(
            len(pieces) >= 3 and pieces[0] == "buckets" and pieces[1] in scheduled,
            "unscheduled_artifact",
        )
    for event in events[1:]:
        kind, bucket, payload = event["kind"], event["bucket"], event["payload"]
        _require(kind != "initialized", "duplicate_initialized_event")
        if kind != "stop":
            _require(bucket in scheduled, "event_unscheduled_bucket")
        if kind in {"reservation", "started", "request_intent"}:
            window = (
                plan["schedule"]["bootstrap"]
                if bucket == plan["schedule"]["bootstrap"]["bucket"]
                else next(row for row in plan["schedule"]["frames"] if row["bucket"] == bucket)
            )
            _require(
                window["start_ns"] + 5 * _SECOND_NS
                <= event["at_ns"]
                <= window["end_ns"] - 5 * _SECOND_NS
                and event["at_ns"] < plan["schedule"]["expires_ns"],
                "event_outside_scheduled_window",
            )
        if kind == "reservation":
            _fields(payload, "reservation_sha256 frame_index")
            _require(bucket not in reservations, "duplicate_reservation")
            _require(_digest(payload["reservation_sha256"]), "reservation_digest")
            if bucket == plan["schedule"]["bootstrap"]["bucket"]:
                _require(payload["frame_index"] is None, "bootstrap_reservation_index")
                _require(not reservations and not missed, "bootstrap_reservation_order")
            else:
                frame_index = next(
                    row["index"] for row in plan["schedule"]["frames"] if row["bucket"] == bucket
                )
                _require(
                    sealed.get(plan["schedule"]["bootstrap"]["bucket"]) is True,
                    "frame_without_bootstrap_seal",
                )
                _require(payload["frame_index"] == frame_index, "frame_reservation_index")
                for prior_row in plan["schedule"]["frames"][:frame_index]:
                    _require(
                        sealed.get(prior_row["bucket"]) is True or prior_row["bucket"] in missed,
                        "frame_schedule_gap_unresolved",
                    )
            reservations[bucket] = payload
            intents[bucket] = []
            event_order[bucket] = [kind]
            event_files[(bucket, kind)] = (payload["reservation_sha256"], event["seq"], kind)
        elif kind == "started":
            _fields(payload, "started_sha256")
            _require(
                bucket in reservations and event_order[bucket] == ["reservation"], "started_order"
            )
            _require(_digest(payload["started_sha256"]), "started_digest")
            event_order[bucket].append(kind)
            event_files[(bucket, kind)] = (payload["started_sha256"], event["seq"], kind)
        elif kind == "request_intent":
            _fields(payload, "ordinal request_sha256 request_started_ns")
            _require(
                bucket in reservations
                and "started" in event_order[bucket]
                and not sealed.get(bucket)
                and "bundle" not in event_order[bucket],
                "intent_without_started_bucket",
            )
            ordinal = payload.get("ordinal")
            _require(type(ordinal) is int and ordinal == len(intents[bucket]), "intent_ordinal")
            _require(_digest(payload["request_sha256"]), "intent_digest")
            _require(_integer(payload["request_started_ns"], 1), "intent_start_time")
            _require(payload["request_started_ns"] == event["at_ns"], "intent_dispatch_time")
            _require(event["at_ns"] - previous_intent_ns >= 250_000_000, "request_rate_exceeded")
            previous_intent_ns = event["at_ns"]
            intents[bucket].append(ordinal)
            event_order[bucket].append(kind)
            event_files[(bucket, f"request:{ordinal}")] = (
                payload["request_sha256"],
                event["seq"],
                kind,
            )
        elif kind == "bundle":
            _fields(payload, "bundle_sha256")
            _require(
                bucket in reservations and _digest(payload["bundle_sha256"]),
                "bundle_without_reservation",
            )
            _require("bundle" not in event_order[bucket], "duplicate_bundle_event")
            event_order[bucket].append(kind)
            event_files[(bucket, kind)] = (payload["bundle_sha256"], event["seq"], kind)
        elif kind == "seal":
            _fields(payload, "seal_sha256")
            _require(
                bucket in reservations and "bundle" in event_order[bucket], "seal_without_bundle"
            )
            _require(_digest(payload["seal_sha256"]), "seal_digest")
            event_order[bucket].append(kind)
            sealed[bucket] = True
            event_files[(bucket, kind)] = (payload["seal_sha256"], event["seq"], kind)
        elif kind == "missed":
            _fields(payload, "index window prior_event_sha256")
            _require(
                bucket not in reservations
                and not any(p.startswith(f"buckets/{bucket}/") for p in files),
                "missed_spent_bucket",
            )
            _require(
                payload["prior_event_sha256"] == event["previous_sha256"],
                "missed_predecessor_digest",
            )
            _require(
                bucket != plan["schedule"]["bootstrap"]["bucket"], "bootstrap_miss_is_terminal"
            )
            row = next(row for row in plan["schedule"]["frames"] if row["bucket"] == bucket)
            _require(
                payload["index"] == row["index"]
                and payload["window"] == {"start_ns": row["start_ns"], "end_ns": row["end_ns"]},
                "missed_window_identity",
            )
            _require(
                event["at_ns"] > row["end_ns"] - 5 * _SECOND_NS,
                "missed_before_dispatch_cutoff",
            )
            _require(
                sealed.get(plan["schedule"]["bootstrap"]["bucket"]) is True,
                "missed_frame_without_bootstrap",
            )
            for prior_row in plan["schedule"]["frames"][: row["index"]]:
                _require(
                    sealed.get(prior_row["bucket"]) is True or prior_row["bucket"] in missed,
                    "missed_frame_order",
                )
            missed.add(bucket)
        elif kind == "stop":
            _require(bucket is None, "stop_event_bucket")
            _fields(payload, "stop")
            _require(isinstance(payload["stop"], dict), "stop_payload")
    for (bucket, key), (digest, event_seq, kind) in event_files.items():
        if key.startswith("request:"):
            ordinal = int(key.split(":", 1)[1])
            path = f"buckets/{bucket}/requests/{ordinal:02d}.json"
        else:
            path = f"buckets/{bucket}/{key}.json"
        if path in files:
            _require(sha(reader.read_bytes(path)) == digest, "event_file_digest_mismatch")
            if key.startswith("request:"):
                request = decode(reader.read_bytes(path))
                intent = events[event_seq]
                _require(
                    request.get("request_started_ns") == intent["payload"]["request_started_ns"]
                    and request.get("decision_ns") == intent["at_ns"],
                    "request_intent_time_binding",
                )
        else:
            _require(
                event_seq == len(events) - 1
                and kind in {"reservation", "started", "request_intent"},
                "missing_nonterminal_event_file",
            )
    _verify_file_closure(reader, plan, files, events, reservations, intents, sealed)
    stop_event = next((e for e in events if e["kind"] == "stop"), None)
    stop_value = stop_event["payload"]["stop"] if stop_event is not None else None
    if schema.STOP_FILE in files:
        stop_raw = reader.read_bytes(schema.STOP_FILE)
        stop_file = decode(stop_raw)
        _fields(stop_file, "schema schema_version reason stopped_ns last_bucket post_stop_requests")
        _require(
            canonical(stop_file) == stop_raw
            and stop_file["schema"] == schema.STOP_SCHEMA
            and stop_file["schema_version"] == 2,
            "stop_file_schema",
        )
        _require(
            stop_file["post_stop_requests"] == 0 and _integer(stop_file["stopped_ns"], 1),
            "stop_file_claim",
        )
        _require(stop_value is None or stop_value == stop_file, "stop_file_event_mismatch")
        stop_value = stop_file
    _require(stop_value is None or stopped, "stop_state")
    if stop_value is not None:
        _require(not events or stop_value["stopped_ns"] >= events[-1]["at_ns"], "stop_time_order")
        if stop_event is not None:
            _require(stop_value["stopped_ns"] == stop_event["at_ns"], "stop_event_time_binding")
        _require(
            all(
                event["kind"] != "request_intent" or event["at_ns"] <= stop_value["stopped_ns"]
                for event in events
            ),
            "post_stop_request_intent",
        )
    # Validate every intent prefix, not only buckets that reached a seal. This
    # is the no-replay boundary for crashes and terminal failures.
    prefix_frontier = plan["predecessor"]["certificate"]["frontier"]
    ordered_for_prefix = [plan["schedule"]["bootstrap"], *plan["schedule"]["frames"]]
    verified_cache: dict[str, dict[str, Any]] = {}
    for window in ordered_for_prefix:
        bucket = window["bucket"]
        bucket_ordinals = intents.get(bucket, [])
        if bucket_ordinals:
            _verify_intent_prefix(reader, plan, bucket, prefix_frontier, bucket_ordinals, files)
        if sealed.get(bucket):
            verified = _verify_bucket(reader, plan, bucket, prefix_frontier, manifest_files=files)
            verified_cache[bucket] = verified
            prefix_frontier = verified["frontier"]
        elif f"buckets/{bucket}/bundle.json" in files:
            # A bundle file is published before its journal event. Validate
            # that crash prefix independently, but never advance the frontier.
            _verify_bucket(
                reader,
                plan,
                bucket,
                prefix_frontier,
                require_seal=False,
                manifest_files=files,
            )
    total_intents = sum(map(len, intents.values()))
    _require(total_intents <= schema.MAX_REQUESTS, "lease_request_cap")
    day_usage: dict[int, dict[str, int]] = {}
    lease_known_raw = 0
    bucket_known_raw: dict[str, int] = {}
    predecessor_days = {row["utc_day"]: row for row in (predecessor_day_usage or [])}
    from tools.brain_senses_block_provider_pilot import _signature_list

    for event in events:
        if event["kind"] != "request_intent":
            continue
        bucket = event["bucket"]
        phase = "bootstrap" if bucket == plan["schedule"]["bootstrap"]["bucket"] else "frame"
        ordinal = event["payload"]["ordinal"]
        request_path = f"buckets/{bucket}/requests/{ordinal:02d}.json"
        started_ns = event["payload"]["request_started_ns"]
        if request_path in files:
            request = decode(reader.read_bytes(request_path))
            _require(request.get("request_started_ns") == started_ns, "intent_time_binding")
        response_bytes = schema.MAX_RESPONSE_BYTES
        receipt_path = f"buckets/{bucket}/captures/{ordinal:02d}/receipt.json"
        result_path = f"buckets/{bucket}/results/{ordinal:02d}.json"
        response_path = f"buckets/{bucket}/captures/{ordinal:02d}/response.json"
        fully_durable = False
        if receipt_path in files:
            receipt = decode(reader.read_bytes(receipt_path))
            _require(response_path in files, "receipt_without_response")
            raw = reader.read_bytes(response_path)
            actual = receipt.get("response_bytes")
            _require(
                _integer(actual) and actual == len(raw) and actual <= schema.MAX_RESPONSE_BYTES,
                "response_byte_cap",
            )
            _require(
                receipt.get("schema") == schema.RECEIPT_SCHEMA
                and receipt.get("provider") == plan["provider"]
                and receipt.get("response_sha256") == sha(raw)
                and receipt.get("availability_ns") is None
                and receipt.get("storage")
                == {"path": "response.json", "bytes": len(raw), "codec": "raw"},
                "receipt_identity_or_digest",
            )
            if result_path in files:
                result = decode(reader.read_bytes(result_path))
                _fields(
                    result,
                    "schema schema_version provider request_id request_sha256 wire_request_sha256 "
                    "status result_status response_sha256 response_bytes stored_response_bytes "
                    "availability_ns decision_ns method event_slot produced_slots signatures "
                    "block_identity event_time_ns http_status transport_category",
                )
                _require(
                    isinstance(request, dict)
                    and result.get("schema") == schema.RESULT_SCHEMA
                    and result.get("status") == "captured"
                    and result.get("result_status")
                    in {"parsed", "provider_error", "invalid_response"}
                    and _integer(result.get("availability_ns"), 1)
                    and result.get("response_bytes") == actual
                    and result.get("stored_response_bytes") == actual
                    and result.get("response_sha256") == sha(raw)
                    and receipt.get("request_id") == request.get("request_id")
                    and receipt.get("request_sha256") == request.get("request_sha256")
                    and receipt.get("wire_request_sha256") == request.get("wire_request_sha256")
                    and result.get("provider") == plan["provider"]
                    and result.get("request_id") == request.get("request_id")
                    and result.get("request_sha256") == request.get("request_sha256")
                    and result.get("wire_request_sha256") == request.get("wire_request_sha256")
                    and result.get("decision_ns") == request.get("decision_ns")
                    and result.get("availability_ns") >= receipt.get("received_ns", 0),
                    "request_durability_identity",
                )
                if result.get("result_status") == "parsed":
                    _require(result.get("method") == request.get("method"), "result_method")
                    value = _rpc_result(raw)
                    if request.get("method") == "getSlot":
                        _require(
                            result.get("event_slot") == value
                            and result.get("produced_slots") is None
                            and result.get("signatures") is None
                            and result.get("block_identity") is None
                            and result.get("event_time_ns") is None,
                            "slot_result_projection",
                        )
                    elif request.get("method") == "getBlocks":
                        _require(
                            result.get("event_slot") is None
                            and result.get("produced_slots") == value
                            and result.get("signatures") is None
                            and result.get("block_identity") is None
                            and result.get("event_time_ns") is None,
                            "inventory_result_projection",
                        )
                    elif isinstance(value, dict):
                        detail = request["params"][1].get("transactionDetails")
                        sigs = _signature_list(value, detail)
                        identity = {
                            key: value.get(key)
                            for key in (
                                "blockhash",
                                "previousBlockhash",
                                "parentSlot",
                                "blockTime",
                                "blockHeight",
                            )
                        }
                        block_time = value.get("blockTime")
                        event_time = block_time * 1_000_000_000 if type(block_time) is int else None
                        _require(
                            result.get("event_slot") == request["params"][0]
                            and result.get("produced_slots") is None
                            and result.get("signatures") == sigs
                            and result.get("block_identity") == identity
                            and result.get("event_time_ns") == event_time,
                            "block_result_projection",
                        )
                    else:
                        raise VerificationError("block_result_type")
                fully_durable = result.get("result_status") == "parsed"
            elif result_path in files:
                raise VerificationError("result_without_receipt")
        elif result_path in files:
            result = decode(reader.read_bytes(result_path))
            request = decode(reader.read_bytes(request_path)) if request_path in files else None
            _fields(
                result,
                "schema schema_version provider request_id request_sha256 wire_request_sha256 "
                "status result_status response_sha256 response_bytes stored_response_bytes "
                "availability_ns decision_ns method event_slot produced_slots signatures "
                "block_identity event_time_ns http_status transport_category",
            )
            _require(
                isinstance(request, dict)
                and result.get("schema") == schema.RESULT_SCHEMA
                and result.get("status") == "ambiguous"
                and result.get("result_status") == "unknown"
                and result.get("response_sha256") is None
                and result.get("response_bytes") is None
                and result.get("stored_response_bytes") is None
                and result.get("availability_ns") is None
                and result.get("provider") == plan["provider"]
                and result.get("request_id") == request.get("request_id")
                and result.get("request_sha256") == request.get("request_sha256")
                and result.get("wire_request_sha256") == request.get("wire_request_sha256")
                and result.get("decision_ns") == request.get("decision_ns")
                and result.get("method") == request.get("method")
                and result.get("event_slot")
                == (request["params"][0] if request.get("method") == "getBlock" else None)
                and result.get("produced_slots") is None
                and result.get("signatures") is None
                and result.get("block_identity") is None
                and result.get("event_time_ns") is None,
                "ambiguous_result_identity",
            )
        if fully_durable:
            response_bytes = actual
        # Every intent must have had a full response-sized admission reserve.
        # Successful, independently durable responses replace that reserve by
        # their actual length; any incomplete/ambiguous intent retains it.
        phase_limit = cast(
            int,
            schema.CAPS["bootstrap_raw_bytes"]
            if phase == "bootstrap"
            else schema.CAPS["frame_raw_bytes"],
        )
        _require(
            bucket_known_raw.get(bucket, 0) + schema.MAX_RESPONSE_BYTES <= phase_limit,
            "phase_dispatch_headroom",
        )
        _require(
            lease_known_raw + schema.MAX_RESPONSE_BYTES <= schema.RAW_LEASE_BYTES,
            "lease_dispatch_headroom",
        )
        day = started_ns // 86_400_000_000_000
        row = day_usage.setdefault(
            day, {"requests": 0, "raw_bytes_reserved": 0, "stored_bytes_reserved": 0}
        )
        predecessor_day = predecessor_days.get(day, {})
        _require(
            int(predecessor_day.get("requests", 0)) + row["requests"] + 1
            <= schema.UTC_DAY_REQUESTS,
            "combined_utc_day_request_headroom",
        )
        _require(
            int(predecessor_day.get("raw_bytes", 0))
            + row["raw_bytes_reserved"]
            + schema.MAX_RESPONSE_BYTES
            <= schema.UTC_DAY_BYTES,
            "combined_utc_day_raw_headroom",
        )
        _require(
            int(predecessor_day.get("stored_bytes", 0))
            + row["stored_bytes_reserved"]
            + schema.MAX_RESPONSE_BYTES
            <= schema.UTC_DAY_BYTES,
            "combined_utc_day_stored_headroom",
        )
        row["requests"] += 1
        charged = response_bytes if fully_durable else schema.MAX_RESPONSE_BYTES
        row["raw_bytes_reserved"] += charged
        row["stored_bytes_reserved"] += charged
        lease_known_raw += charged
        bucket_known_raw[bucket] = bucket_known_raw.get(bucket, 0) + charged
    for row in day_usage.values():
        _require(row["requests"] <= schema.UTC_DAY_REQUESTS, "utc_day_request_cap")
        _require(row["raw_bytes_reserved"] <= schema.UTC_DAY_BYTES, "utc_day_raw_cap")
        _require(row["stored_bytes_reserved"] <= schema.UTC_DAY_BYTES, "utc_day_stored_cap")
    if predecessor_day_usage is not None:
        combined: dict[int, dict[str, int]] = {}
        for row in predecessor_day_usage:
            combined[row["utc_day"]] = {
                "requests": row["requests"],
                "raw_bytes": row["raw_bytes"],
                "stored_bytes": row["stored_bytes"],
            }
        for day, row in day_usage.items():
            combined_row = combined.setdefault(
                day, {"requests": 0, "raw_bytes": 0, "stored_bytes": 0}
            )
            combined_row["requests"] += row["requests"]
            combined_row["raw_bytes"] += row["raw_bytes_reserved"]
            combined_row["stored_bytes"] += row["stored_bytes_reserved"]
        for row in combined.values():
            _require(row["requests"] <= schema.UTC_DAY_REQUESTS, "combined_utc_day_request_cap")
            _require(row["raw_bytes"] <= schema.UTC_DAY_BYTES, "combined_utc_day_raw_cap")
            _require(row["stored_bytes"] <= schema.UTC_DAY_BYTES, "combined_utc_day_stored_cap")
    buckets: dict[str, dict[str, Any]] = {}
    frontier = plan["predecessor"]["certificate"]["frontier"]
    gaps = list(plan["predecessor"]["certificate"]["gap_intervals"])
    ordered = [plan["schedule"]["bootstrap"], *plan["schedule"]["frames"]]
    bootstrap_sealed = False
    last_sealed_ns = 0
    for window in ordered:
        bucket = window["bucket"]
        if sealed.get(bucket):
            _require(
                not (window is not ordered[0] and not bootstrap_sealed),
                "frame_before_bootstrap_seal",
            )
            verified_bucket = verified_cache.get(bucket) or _verify_bucket(
                reader, plan, bucket, frontier, manifest_files=files
            )
            if last_sealed_ns:
                _require(
                    verified_bucket["first_decision_ns"] >= last_sealed_ns,
                    "frame_before_previous_seal",
                )
            else:
                _require(
                    verified_bucket["first_decision_ns"]
                    >= plan["predecessor"]["certificate"]["terminal_sealed_ns"],
                    "bootstrap_before_predecessor_seal",
                )
            buckets[bucket] = verified_bucket
            frontier = verified_bucket["frontier"]
            last_sealed_ns = verified_bucket["seal_ns"]
            if bucket == plan["schedule"]["bootstrap"]["bucket"]:
                bootstrap_sealed = True
                gaps.append(
                    {
                        "start": plan["predecessor"]["certificate"]["frontier"],
                        "end_exclusive": verified_bucket["bundle"]["bootstrap_slot"],
                        "reason": "uncovered_since_predecessor_no_backfill",
                        "bootstrap_bucket": bucket,
                    }
                )
        elif bucket in reservations:
            buckets[bucket] = {
                "status": "interrupted_no_replay",
                "request_count": len(intents[bucket]),
                "sealed": False,
            }
        else:
            buckets[bucket] = {"status": "unspent", "sealed": False}
    status = "stopped" if stopped else ("verified_prefix" if len(events) else "uninitialized")
    unresolved = sorted(bucket for bucket in reservations if not sealed.get(bucket))
    if unresolved and not stopped:
        status = "interrupted_no_replay"
    return {
        "status": status,
        "plan": plan,
        "plan_sha256": plan_hash,
        "events": events,
        "event_tail_sha256": events[-1]["event_sha256"],
        "reservations": reservations,
        "missed": missed,
        "intents": intents,
        "sealed": sealed,
        "requests": total_intents,
        "v2_raw_bytes": sum(row["raw_bytes_reserved"] for row in day_usage.values()),
        "v2_stored_bytes": sum(row["stored_bytes_reserved"] for row in day_usage.values()),
        "v2_day_usage": day_usage,
        "unresolved_buckets": unresolved,
        "stop": stopped,
        "frontier": frontier,
        "gap_intervals": gaps,
        "buckets": buckets,
        "replay_authorized": False,
        "dispatch_authorized": False,
        "predecessor_lineage_verified": lineage_verified,
        "predecessor_day_usage": predecessor_day_usage,
    }
