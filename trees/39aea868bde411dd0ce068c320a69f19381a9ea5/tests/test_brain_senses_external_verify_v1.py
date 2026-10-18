from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tools import brain_senses_external_schema_v1 as s
from tools import brain_senses_external_verify_v1 as v

N = 1_800_000_000_000_000_000


class MemoryReader:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.events: list[dict[str, Any]] = []

    def read_bytes(self, path: str) -> bytes:
        return self.files[path]

    def list_files(self) -> list[str]:
        return sorted(self.files)

    def put(self, path: str, value: Any) -> str:
        self.files[path] = v.canonical(value)
        return v.sha(self.files[path])

    def event(self, kind: str, bucket: str | None, at: int, payload: dict[str, Any]) -> None:
        row = dict(
            schema=s.EVENT_SCHEMA,
            schema_version=1,
            seq=len(self.events),
            kind=kind,
            bucket=bucket,
            at_ns=at,
            payload=payload,
            previous_sha256=self.events[-1]["event_sha256"] if self.events else "0" * 64,
        )
        row["event_sha256"] = v.sha(v.canonical(row))
        self.events.append(row)
        self.files[s.EVENTS_FILE] = b"".join(v.canonical(event) for event in self.events)


def plan() -> dict[str, Any]:
    from datetime import UTC, datetime

    def window(start: int, duration: int) -> dict[str, Any]:
        return {
            "bucket": datetime.fromtimestamp(start // 10**9, UTC).strftime("%Y%m%dT%H%M%SZ"),
            "start_ns": start,
            "end_ns": start + duration * 10**9,
        }

    return {
        "schema": s.PLAN_SCHEMA,
        "schema_version": 1,
        "service_id": "test-finite",
        "provider": "helius-free",
        "endpoint_origin": "https://mainnet.helius-rpc.com",
        "storage": {
            "root_id": "testroot",
            "volume_uuid": "testvolume",
            "relative_root": "services/test-finite",
        },
        "code_sha256": {"source.py": "a" * 64},
        "predecessor": {
            "campaign_id": "c4",
            "stop_sha256": "b" * 64,
            "terminal_receipt_sha256": "c" * 64,
            "committed_frontier": 80,
            "gap_intervals": [],
            "last_inventoried_interval": None,
        },
        "schedule": {
            "clock_preflight_ns": N - 120 * 10**9,
            "bootstrap": window(N, 60),
            "frame": window(N + 300 * 10**9, 180),
            "expires_ns": N + 480 * 10**9,
        },
        "caps": {
            "max_attempts": 2,
            "max_requests_total": 35,
            "max_requests_per_utc_date": 35,
            "bootstrap_requests": 1,
            "frame_requests": 34,
            "max_frame_width": 20000,
            "selection_count": 16,
            "max_response_bytes": 32000000,
            "raw_bytes_total": 288000000,
            "stored_bytes_total": 288000000,
            "raw_bytes_per_utc_date": 288000000,
            "stored_bytes_per_utc_date": 288000000,
            "disk_floor_bytes": 50 * 2**30,
            "peak_write_margin_bytes": 290000000,
            "bootstrap_raw_bytes": 32000000,
            "bootstrap_stored_bytes": 32000000,
            "frame_raw_bytes": 256000000,
            "frame_stored_bytes": 256000000,
            "storage_codec": "raw",
        },
    }


def fixture(*, with_frame: bool = True) -> tuple[MemoryReader, dict[str, Any]]:
    reader = MemoryReader()
    p = plan()
    ph = reader.put(s.PLAN_FILE, p)
    reader.event("initialized", None, N - 120 * 10**9, {"plan_sha256": ph})
    for phase in ("bootstrap", "frame") if with_frame else ("bootstrap",):
        bucket = p["schedule"][phase]["bucket"]
        base = f"buckets/{bucket}"
        at = p["schedule"][phase]["start_ns"] + 10 * 10**9
        from email.utils import formatdate

        from tools import brain_senses_external_clock_v1 as clock_module

        clock_time = at - 2 * 10**9
        samples = [
            {
                "host": host,
                "status": 200,
                "date": formatdate(clock_time // 10**9, usegmt=True),
                "age": None,
                "sent_ns": clock_time + i * 1000,
                "received_ns": clock_time + i * 1000 + 100,
                "monotonic_sent_ns": clock_time + i * 1000,
                "monotonic_received_ns": clock_time + i * 1000 + 100,
            }
            for i, host in enumerate(clock_module.HOSTS)
        ]
        clock_record = {
            "schema": clock_module.CLOCK_SCHEMA,
            "schema_version": 1,
            "samples": samples,
            "checked_ns": samples[-1]["received_ns"],
            "monotonic_ns": samples[-1]["monotonic_received_ns"],
        }
        clock_path = f"clock/{bucket}-00.json"
        clock_hash = reader.put(clock_path, clock_record)
        reservation = dict(
            schema=s.BUCKET_SCHEMA,
            schema_version=1,
            service_id=p["service_id"],
            bucket=bucket,
            phase=phase,
            reservation_id=bucket,
            plan_sha256=ph,
            request_count=1 if phase == "bootstrap" else 34,
            raw_byte_cap=32000000 if phase == "bootstrap" else 256000000,
            stored_byte_cap=32000000 if phase == "bootstrap" else 256000000,
            created_ns=at,
            not_before_ns=p["schedule"][phase]["start_ns"],
            expires_ns=p["schedule"][phase]["end_ns"],
            predecessor_event_sha256=reader.events[-1]["event_sha256"],
            clock_check_path=clock_path,
            clock_check_sha256=clock_hash,
        )
        digest = reader.put(base + "/reservation.json", reservation)
        reader.event("reservation", bucket, at, {"reservation_sha256": digest})
        started = dict(
            schema=s.BUCKET_SCHEMA,
            schema_version=1,
            reservation_id=bucket,
            plan_sha256=ph,
            started_ns=at + 1,
            pid=1,
        )
        digest = reader.put(base + "/started.json", started)
        reader.event("started", bucket, at + 1, {"started_sha256": digest})
        operations: list[tuple[str, list[Any], Any]] = [
            ("getSlot", [{"commitment": "finalized"}], 100 if phase == "bootstrap" else 102)
        ]
        if phase == "frame":
            operations.append(("getBlocks", [101, 102, {"commitment": "finalized"}], [101, 102]))
            for slot in (101, 102):
                for detail in ("signatures", "full"):
                    options: dict[str, Any] = {
                        "commitment": "finalized",
                        "encoding": "json",
                        "transactionDetails": detail,
                        "rewards": False,
                    }
                    if detail == "full":
                        options["maxSupportedTransactionVersion"] = 1
                    value = {
                        "blockhash": f"block-{slot}",
                        "previousBlockhash": f"block-{slot - 1}",
                        "parentSlot": slot - 1,
                        "blockTime": 1234,
                        "blockHeight": slot - 5,
                        "signatures" if detail == "signatures" else "transactions": [],
                    }
                    operations.append(("getBlock", [slot, options], value))
        for ordinal, (method, params, value) in enumerate(operations):
            request_at = at + 10 + ordinal * 10
            request_id = f"{bucket}-{ordinal:02d}"
            semantic = v.sha(v.canonical({"method": method, "params": params}))
            wire = v.sha(
                json.dumps(
                    dict(jsonrpc="2.0", id=1, method=method, params=params),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            )
            request = dict(
                schema=s.REQUEST_SCHEMA,
                schema_version=1,
                provider=p["provider"],
                reservation_id=bucket,
                request_id=request_id,
                ordinal=ordinal,
                clock_check_path=clock_path,
                clock_check_sha256=clock_hash,
                monotonic_ns=request_at,
                method=method,
                params=params,
                request_sha256=semantic,
                wire_request_sha256=wire,
                request_started_ns=request_at,
                decision_ns=request_at,
            )
            reader.put(f"{base}/requests/{ordinal:02d}.json", request)
            response = v.canonical(dict(jsonrpc="2.0", id=1, result=value))
            response_path = f"{base}/captures/{ordinal:02d}/response.json"
            reader.files[response_path] = response
            receipt = dict(
                schema=s.RECEIPT_SCHEMA,
                schema_version=1,
                provider=p["provider"],
                request_id=request_id,
                request_sha256=semantic,
                wire_request_sha256=wire,
                request_started_ns=request_at,
                received_ns=request_at + 1,
                availability_ns=None,
                decision_ns=request_at,
                response_sha256=v.sha(response),
                response_bytes=len(response),
                storage=dict(
                    codec="raw",
                    path="response.json",
                    sha256=v.sha(response),
                    bytes=len(response),
                    codec_version=None,
                ),
            )
            rh = reader.put(f"{base}/captures/{ordinal:02d}/receipt.json", receipt)
            result = dict(
                schema=s.RESULT_SCHEMA,
                schema_version=1,
                provider=p["provider"],
                request_id=request_id,
                request_sha256=semantic,
                wire_request_sha256=wire,
                receipt_sha256=rh,
                response_sha256=v.sha(response),
                status="captured",
                http_status=None,
                transport_category=None,
                result_status="parsed",
                method=method,
                event_slot=value
                if method == "getSlot"
                else params[0]
                if method == "getBlock"
                else None,
                produced_slots=value if method == "getBlocks" else None,
                signatures=[] if method == "getBlock" else None,
                block_identity={
                    "blockhash": value["blockhash"],
                    "previousBlockhash": value["previousBlockhash"],
                    "parentSlot": value["parentSlot"],
                    "blockTime": value["blockTime"],
                    "blockHeight": value["blockHeight"],
                }
                if method == "getBlock"
                else None,
                response_bytes=len(response),
                stored_response_bytes=len(response),
                event_time_ns=1234 * 10**9 if method == "getBlock" else None,
                availability_ns=request_at + 2,
                decision_ns=request_at,
            )
            reader.put(f"{base}/results/{ordinal:02d}.json", result)
        prefix = v._bucket_prefix(reader, bucket, p)
        bundle = {
            "schema": s.BUNDLE_SCHEMA,
            "schema_version": 1,
            "provider": p["provider"],
            "bucket": bucket,
            "phase": phase,
            "reservation_id": bucket,
            "plan_sha256": ph,
            "status": "complete",
            "provider_charge_units": None,
            "provider_charge_status": "unknown_unverified",
            "terminal_status": phase + "_complete",
        }
        bundle.update(
            {
                key: prefix[key]
                for key in (
                    "requests",
                    "request_count",
                    "raw_bytes",
                    "stored_bytes",
                    "bootstrap_slot",
                    "frame",
                    "selected_blocks",
                )
            }
        )
        bh = reader.put(base + "/bundle.json", bundle)
        reader.event("bundle", bucket, at + 1000, {"bundle_sha256": bh})
        seal = dict(
            schema=s.SEAL_SCHEMA,
            schema_version=1,
            bucket=bucket,
            reservation_id=bucket,
            plan_sha256=ph,
            bundle_sha256=bh,
            sealed_ns=at + 1001,
            status="complete",
        )
        sh = reader.put(base + "/seal.json", seal)
        reader.event("seal", bucket, at + 1001, {"seal_sha256": sh})
    return reader, p


def test_complete_sparse_frame_recomputes_frontier_and_accounting() -> None:
    reader, _ = fixture()
    result = v.verify(reader)
    assert result["frontier"] == 103
    assert result["requests"] == 7
    assert result["raw_bytes"] == result["stored_bytes"] > 0
    assert not result["activation_authorized"]
    assert not result["replay_authorized"]


@pytest.mark.parametrize(
    "mutation",
    [
        "response",
        "selection",
        "signature",
        "availability",
        "wire",
        "provider",
        "orphan",
        "chain",
        "post_stop",
    ],
)
def test_rejects_structural_and_temporal_counterexamples(mutation: str) -> None:
    reader, p = fixture()
    bucket = p["schedule"]["frame"]["bucket"]
    base = f"buckets/{bucket}"
    if mutation == "response":
        reader.files[base + "/captures/00/response.json"] += b" "
    elif mutation == "selection":
        value = v.decode(reader.files[base + "/bundle.json"])
        value["frame"]["selected_slots"] = [102]
        reader.put(base + "/bundle.json", value)
    elif mutation in {"availability", "wire", "provider"}:
        path = base + "/results/00.json"
        value = v.decode(reader.files[path])
        value[
            {
                "availability": "availability_ns",
                "wire": "wire_request_sha256",
                "provider": "provider",
            }[mutation]
        ] = {"availability": 0, "wire": "f" * 64, "provider": "different-provider"}[mutation]
        reader.put(path, value)
    elif mutation == "signature":
        value = v.decode(reader.files[base + "/captures/02/response.json"])
        value["result"]["signatures"] = ["unmatched"]
        reader.put(base + "/captures/02/response.json", value)
    elif mutation == "orphan":
        reader.files[base + "/captures/99/response.json"] = b"{}"
    elif mutation == "chain":
        reader.events[-1]["at_ns"] = 0
        reader.files[s.EVENTS_FILE] = b"".join(v.canonical(e) for e in reader.events)
    else:
        stop = dict(
            schema=s.STOP_SCHEMA,
            schema_version=1,
            reason="test",
            stopped_ns=N,
            last_bucket=bucket,
            bundle_sha256=None,
            post_stop_requests=0,
        )
        reader.put(s.STOP_FILE, stop)
    with pytest.raises(v.VerificationError):
        v.verify(reader)


def test_complete_bundle_without_seal_is_local_recovery_only() -> None:
    reader, p = fixture(with_frame=False)
    bucket = p["schedule"]["bootstrap"]["bucket"]
    del reader.files[f"buckets/{bucket}/seal.json"]
    reader.events.pop()
    reader.files[s.EVENTS_FILE] = b"".join(v.canonical(e) for e in reader.events)
    status = v.verify(reader)
    assert status["buckets"][bucket]["status"] == "complete_bundle_local_seal_only"
    assert status["frontier"] == 80
    assert not status["replay_authorized"]


def test_started_request_without_result_never_becomes_replayable() -> None:
    reader, p = fixture(with_frame=False)
    bucket = p["schedule"]["bootstrap"]["bucket"]
    for suffix in ("bundle.json", "seal.json", "results/00.json"):
        del reader.files[f"buckets/{bucket}/{suffix}"]
    reader.events = reader.events[:3]
    reader.files[s.EVENTS_FILE] = b"".join(v.canonical(e) for e in reader.events)
    status = v.verify(reader)
    assert status["status"] == "interrupted_no_replay"
    assert status["requests"] == 1
    assert status["frontier"] == 80


def test_descriptor_reader_rejects_symlink_hardlink_and_parent_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    file = root / "data"
    file.write_bytes(b"private")
    file.chmod(0o600)
    (root / "alias").symlink_to(file)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with v.DescriptorReader(fd) as reader:
            assert reader.read_bytes("data") == b"private"
            with pytest.raises((OSError, v.VerificationError)):
                reader.read_bytes("alias")
            with pytest.raises(v.VerificationError):
                reader.read_bytes("../root/data")
            os.link(file, root / "hard")
            with pytest.raises(v.VerificationError):
                reader.read_bytes("data")
    finally:
        os.close(fd)


def test_duplicate_json_and_nonfinite_values_rejected() -> None:
    for payload in (b'{"x":1,"x":2}', b'{"x":NaN}'):
        with pytest.raises(v.VerificationError):
            v.decode(payload)


@pytest.mark.parametrize("phase", ["bootstrap", "frame"])
def test_calendar_bucket_identity_is_bound_to_utc_start(phase: str) -> None:
    p = copy.deepcopy(plan())
    p["schedule"][phase]["bucket"] = "20000101T000000Z"
    with pytest.raises(v.VerificationError):
        v.verify_plan(p)


def test_anchor_preserves_unknown_interval_and_predecessor_gap() -> None:
    reader, p = fixture(with_frame=False)
    result = v.verify(reader)
    assert result["gap_intervals"] == [
        {
            "start": 80,
            "end_exclusive": 101,
            "reason": "uncovered_since_predecessor_no_backfill",
            "bootstrap_bucket": p["schedule"]["bootstrap"]["bucket"],
        }
    ]


@pytest.mark.parametrize("side", ["file_only", "event_only", "both"])
def test_one_sided_stop_is_always_terminal_without_rpc_authority(side: str) -> None:
    reader, p = fixture(with_frame=False)
    bucket = p["schedule"]["bootstrap"]["bucket"]
    stop = dict(
        schema=s.STOP_SCHEMA,
        schema_version=1,
        reason="interrupted",
        stopped_ns=N + 100 * 10**9,
        last_bucket=bucket,
        bundle_sha256=None,
        post_stop_requests=0,
    )
    if side in {"file_only", "both"}:
        reader.put(s.STOP_FILE, stop)
    if side in {"event_only", "both"}:
        reader.event("stop", None, stop["stopped_ns"], {"stop": stop})
    result = v.verify(reader)
    assert result["status"] == "stopped"
    assert not result["dispatch_authorized"]


def test_partial_response_is_charged_to_utc_day_even_without_result() -> None:
    reader, p = fixture(with_frame=False)
    bucket = p["schedule"]["bootstrap"]["bucket"]
    for suffix in ("bundle.json", "seal.json", "results/00.json"):
        del reader.files[f"buckets/{bucket}/{suffix}"]
    reader.events = reader.events[:3]
    p["caps"]["raw_bytes_per_utc_date"] = 1
    ph = reader.put(s.PLAN_FILE, p)
    # Rebind plan and reservation so the day-cap assertion, not a stale hash,
    # is the evidence under test.
    reservation = v.decode(reader.files[f"buckets/{bucket}/reservation.json"])
    reservation["plan_sha256"] = ph
    started = v.decode(reader.files[f"buckets/{bucket}/started.json"])
    started["plan_sha256"] = ph
    old_events = reader.events
    reader.events = []
    reader.event("initialized", None, old_events[0]["at_ns"], {"plan_sha256": ph})
    reservation["predecessor_event_sha256"] = reader.events[-1]["event_sha256"]
    rh = reader.put(f"buckets/{bucket}/reservation.json", reservation)
    reader.event("reservation", bucket, old_events[1]["at_ns"], {"reservation_sha256": rh})
    sh = reader.put(f"buckets/{bucket}/started.json", started)
    reader.event("started", bucket, old_events[2]["at_ns"], {"started_sha256": sh})
    with pytest.raises(v.VerificationError, match="utc_day_cap"):
        v.verify(reader)
