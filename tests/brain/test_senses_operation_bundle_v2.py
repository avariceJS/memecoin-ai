from __future__ import annotations

import hashlib
import json
import random
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from memecoin_ai.domain import canonical
from memecoin_ai.sources import Capture
from tools import brain_senses_block_epoch_v2 as epoch_v2
from tools import brain_senses_block_epoch_v3 as epoch_v3
from tools import brain_senses_operation_bundle_v2 as bundle_v2
from tools import brain_senses_operation_bundle_verifier_v2 as verifier_v2


@pytest.fixture(autouse=True)
def controlled_host(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    clock = [time.time_ns()]
    monkeypatch.setattr(bundle_v2.time, "time_ns", lambda: clock[0])
    monkeypatch.setattr(
        bundle_v2.shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(200 * 1024**3, 100 * 1024**3, 100 * 1024**3),
    )
    return clock


class FakeRPC:
    def __init__(
        self,
        *,
        anchor: int,
        start: int | None = None,
        end: int | None = None,
        fail_method: str | None = None,
        after_fetch: Any | None = None,
        source_alias: str = "helius-free",
    ):
        self.anchor = anchor
        self.start = start
        self.end = end
        self.fail_method = fail_method
        self.after_fetch = after_fetch
        self.source_alias = source_alias
        self.calls: list[tuple[str, list[Any]]] = []

    def fetch(self, method: str, params: list[Any]) -> Capture:
        self.calls.append((method, params))
        if method == self.fail_method:
            raise TimeoutError("fixture_dispatch_timeout")
        if method == "getSlot":
            value: Any = self.anchor
        elif method == "getBlocks":
            value = list(range(self.start or 0, self.end or 0))
        elif params[1]["transactionDetails"] == "signatures":
            slot = params[0]
            value = {
                "blockhash": f"hash-{slot}",
                "parentSlot": slot - 1,
                "previousBlockhash": f"hash-{slot - 1}",
                "signatures": [f"sig-{slot}"],
            }
        else:
            slot = params[0]
            value = {
                "blockhash": f"hash-{slot}",
                "parentSlot": slot - 1,
                "previousBlockhash": f"hash-{slot - 1}",
                "blockTime": 1_800_000_000 + slot,
                "transactions": [
                    {
                        "transaction": {
                            "signatures": [f"sig-{slot}"],
                            "message": {
                                "accountKeys": ["11111111111111111111111111111111"],
                                "instructions": [],
                            },
                        },
                        "version": "legacy",
                        "meta": {
                            "err": None,
                            "innerInstructions": [],
                            "loadedAddresses": {"writable": [], "readonly": []},
                        },
                    }
                ],
            }
        now = time.time_ns()
        request = canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        response = canonical({"jsonrpc": "2.0", "id": 1, "result": value}).encode()
        capture = Capture(self.source_alias, method, request, now, now, response)
        if self.after_fetch is not None:
            self.after_fetch()
        return capture

    def close(self) -> None:
        pass


class MinimalRPC:
    """A client-shaped fake with no test-only call counter attribute."""

    def __init__(self, client: FakeRPC):
        self.client = client

    def fetch(self, method: str, params: list[Any]) -> Capture:
        return self.client.fetch(method, params)

    def close(self) -> None:
        self.client.close()


def _rebind_bundle_parent(root: Path, bundle_path: Path) -> None:
    bundle_sha = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    path = root / "events-v2.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    previous = bundle_v2.ZERO
    output = []
    for row in rows:
        row.pop("event_sha256", None)
        row["previous_sha256"] = previous
        if row.get("kind") in {"bundle", "seal"}:
            row["bundle_sha256"] = bundle_sha
        row["event_sha256"] = hashlib.sha256(bundle_v2._canonical(row)).hexdigest()
        previous = row["event_sha256"]
        output.append(bundle_v2._canonical(row).decode())
    path.write_text("\n".join(output) + "\n")


def _parent_plan(now: int) -> dict[str, Any]:
    return {
        "schema": bundle_v2.SCHEMA,
        "lease_id": "fake-lease",
        "created_ns": now,
        "expires_ns": now + 4 * bundle_v2.UTC_DAY_NS,
        "source_sha256": {"fixture": "a" * 64},
        "caps": {
            "requests": 100,
            "raw_bytes": 2_000_000_000,
            "stored_bytes": 2_000_000_000,
            "physical_bytes": 50 * 1024**3,
            "width": 1000,
            "daily_requests": 100,
            "daily_raw_bytes": 2_000_000_000,
            "daily_stored_bytes": 2_000_000_000,
        },
        "buckets": [
            {
                "bucket_id": "anchor",
                "kind": "anchor",
                "start_ns": now,
                "end_ns": now + 60_000_000_000,
            },
            {
                "bucket_id": "frame",
                "kind": "frame",
                "start_ns": now + 60_000_000_000,
                "end_ns": now + 120_000_000_000,
            },
        ],
        "predecessor": {"terminal_sha256": "b" * 64, "frontier_slot": 84, "gaps": []},
    }


def _child_plan(
    root: Path,
    *,
    bootstrap: bool,
    next_start: int = 85,
    capture_storage_codec: str = "raw",
) -> dict[str, Any]:
    return epoch_v3.build_epoch_plan(
        epoch_id=root.name,
        bucket=root.name,
        schedule_id="offline-fixture",
        schedule_sha256="c" * 64,
        seed="bundle-fixture-seed",
        bootstrap=bootstrap,
        next_start=None if bootstrap else next_start,
        frame_width_cap=64,
        selection_count=16,
        request_budget=34,
        raw_byte_budget=256_000_000,
        stored_byte_budget=256_000_000,
        minimum_free_disk_bytes=50 * 1024**3,
        capture_storage_codec=capture_storage_codec,
    )


def test_anchor_and_full_34_child_frame_reconcile_independently(
    tmp_path: Path, controlled_host: list[int]
) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    anchor = FakeRPC(anchor=84)
    anchor_result = bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=root / "children/anchor",
        child_plan=_child_plan(root / "children/anchor", bootstrap=True),
        runner=epoch_v3.run_epoch,
        clients={"helius-free": anchor},
    )
    assert anchor_result["status"] == "captured_bundle"
    assert len(anchor.calls) == 1

    controlled_host[0] = now + 60_000_000_001
    frame = FakeRPC(anchor=102, start=85, end=103)
    frame_result = bundle_v2.reserve_and_run(
        root,
        bucket_id="frame",
        operation_id="op-frame",
        child_root=root / "children/frame",
        child_plan=_child_plan(
            root / "children/frame",
            bootstrap=False,
            next_start=85,
            capture_storage_codec="zstd",
        ),
        runner=epoch_v3.run_epoch,
        clients={"helius-free": frame},
    )
    assert frame_result["status"] == "captured_bundle"
    assert len(frame.calls) == 34
    assert len(list((root / "children/frame/captures").glob("*/response.json.zst"))) == 34
    for result_path in sorted((root / "children/frame/request-results").glob("*.json")):
        row = json.loads(result_path.read_bytes())
        assert row["request_started_ns"] <= row["receipt_ns"] <= row["availability_ns"]
    assert verifier_v2.verify(root)["usage"]["requests"] == 35


def test_availability_is_after_durable_capture_with_delayed_capture_return(
    tmp_path: Path, controlled_host: list[int]
) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    client = FakeRPC(
        anchor=84,
        after_fetch=lambda: controlled_host.__setitem__(0, controlled_host[0] + 25),
    )
    bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=root / "children/anchor",
        child_plan=_child_plan(root / "children/anchor", bootstrap=True),
        runner=epoch_v3.run_epoch,
        clients={"helius-free": client},
    )
    result = json.loads((root / "children/anchor/request-results/000000000001.json").read_bytes())
    assert result["availability_ns"] == result["receipt_ns"] + 25
    assert verifier_v2.verify(root)["recovery"] == "sealed"


@pytest.mark.parametrize("failure", ["request", "persistence", "clock"])
def test_unavailable_payload_states_preserve_time_coordinates_and_never_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    controlled_host: list[int],
    failure: str,
) -> None:
    root = tmp_path / failure
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    client = FakeRPC(anchor=84)
    if failure == "request":
        client.fail_method = "getSlot"
    elif failure == "persistence":
        monkeypatch.setattr(
            epoch_v3,
            "_durable_capture",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fixture_fsync_failure")),
        )
    else:
        client.after_fetch = lambda: controlled_host.__setitem__(0, now - 1)

    with pytest.raises(bundle_v2.EnvelopeError):
        bundle_v2.reserve_and_run(
            root,
            bucket_id="anchor",
            operation_id="op-anchor",
            child_root=root / "children/anchor",
            child_plan=_child_plan(root / "children/anchor", bootstrap=True),
            runner=epoch_v3.run_epoch,
            clients={"helius-free": client},
        )

    child = root / "children/anchor"
    result = json.loads((child / "request-results/000000000001.json").read_bytes())
    if failure == "request":
        assert result["status"] == "request_failed"
        assert result["receipt_ns"] is result["availability_ns"] is None
    elif failure == "persistence":
        assert result["status"] == "received_persistence_failed"
        assert type(result["receipt_ns"]) is int
        assert result["availability_ns"] is None
    else:
        assert result["status"] == "captured_availability_unknown"
        assert type(result["receipt_ns"]) is int
        assert result["availability_ns"] is None
    assert type(result["failure_observed_ns"]) is int
    assert verifier_v2.verify(root)["recovery"] == "stop_unknown_started_operation_no_replay"
    assert len(client.calls) == 1


def test_parent_rejects_v2_child_runner_before_any_io(tmp_path: Path) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    client = FakeRPC(anchor=84)
    with pytest.raises(bundle_v2.EnvelopeError, match="child_runner_not_pinned_to_v3"):
        bundle_v2.reserve_and_run(
            root,
            bucket_id="anchor",
            operation_id="op-anchor",
            child_root=root / "children/anchor",
            child_plan=_child_plan(root / "children/anchor", bootstrap=True),
            runner=epoch_v2.run_epoch,
            clients={"helius-free": client},
        )
    assert not client.calls


def test_child_v3_requires_an_explicit_offline_client(tmp_path: Path) -> None:
    plan = _child_plan(tmp_path / "anchor", bootstrap=True)
    with pytest.raises(ValueError, match="explicit_offline_client_required"):
        epoch_v3.run_epoch(tmp_path / "anchor", plan, clients=None)


def test_parent_rejects_non_helius_child_provider_before_any_io(tmp_path: Path) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    client = FakeRPC(anchor=84)
    plan = _child_plan(root / "children/anchor", bootstrap=True)
    plan["provider"] = "other-rpc"
    with pytest.raises(bundle_v2.EnvelopeError, match="child_plan_not_v3"):
        bundle_v2.reserve_and_run(
            root,
            bucket_id="anchor",
            operation_id="op-anchor",
            child_root=root / "children/anchor",
            child_plan=plan,
            runner=epoch_v3.run_epoch,
            clients={"helius-free": client},
        )
    assert not client.calls


@pytest.mark.parametrize("mismatch", ["request_provider", "result_provider", "capture_source"])
def test_provider_identity_mismatch_never_seals_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    root = tmp_path / mismatch
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    source_alias = "other-rpc" if mismatch == "capture_source" else "helius-free"
    client = FakeRPC(anchor=84, source_alias=source_alias)
    plan = _child_plan(root / "children/anchor", bootstrap=True)

    original_make_bundle = bundle_v2._make_bundle

    def mutate_before_bundle(
        parent_root: Path,
        operation_id: str,
        bucket_id: str,
        child_root: Path,
        child_plan: dict[str, Any],
        summary: dict[str, Any],
    ) -> Path:
        if mismatch in {"request_provider", "result_provider"}:
            leaf = "requests" if mismatch == "request_provider" else "request-results"
            path = child_root / leaf / "000000000001.json"
            row = json.loads(path.read_bytes())
            row["provider"] = "other-rpc"
            path.write_bytes((canonical(row) + "\n").encode())
        return original_make_bundle(
            parent_root, operation_id, bucket_id, child_root, child_plan, summary
        )

    monkeypatch.setattr(bundle_v2, "_make_bundle", mutate_before_bundle)
    with pytest.raises((bundle_v2.EnvelopeError, verifier_v2.VerificationError)):
        bundle_v2.reserve_and_run(
            root,
            bucket_id="anchor",
            operation_id="op-anchor",
            child_root=root / "children/anchor",
            child_plan=plan,
            runner=epoch_v3.run_epoch,
            clients={"helius-free": client},
        )
    assert len(client.calls) == 1
    assert not any(row["kind"] == "seal" for row in bundle_v2._events(root))


def test_configured_zstd_capture_verifies_with_plan_pinned_executable(tmp_path: Path) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    result = bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=root / "children/anchor",
        child_plan=_child_plan(
            root / "children/anchor", bootstrap=True, capture_storage_codec="zstd"
        ),
        runner=epoch_v3.run_epoch,
        clients={"helius-free": FakeRPC(anchor=84)},
    )
    assert result["status"] == "captured_bundle"
    assert verifier_v2.verify(root)["recovery"] == "sealed"


def test_zstd_verifier_stops_before_unbounded_decompression() -> None:
    plan = _child_plan(Path("anchor"), bootstrap=True, capture_storage_codec="zstd")
    tool = plan["capture_codec_tool"]
    raw = b"x" * 9
    compressed = subprocess.run(
        [tool["path"], "--quiet", "--stdout"], input=raw, capture_output=True, check=True
    ).stdout
    receipt = {
        "response_bytes": 8,
        "storage": {
            "codec": "zstd",
            "codec_version": tool["version"],
            "path": "response.json.zst",
            "sha256": hashlib.sha256(compressed).hexdigest(),
            "bytes": len(compressed),
        },
    }
    with pytest.raises(
        verifier_v2.VerificationError, match="compressed_capture_exceeds_response_bound"
    ):
        verifier_v2._decode_response(
            receipt,
            compressed,
            expected_codec="zstd",
            codec_tool=tool,
            max_response_bytes=8,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "request",
        "drop_request",
        "capture",
        "receipt",
        "summary",
        "summary_total",
        "bundle_total",
        "manifest_sequence",
        "result_receipt_time",
    ],
)
def test_independent_verifier_rejects_child_record_or_total_mutation(
    tmp_path: Path, mutation: str
) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    client = FakeRPC(anchor=84)
    result = bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=root / "children/anchor",
        child_plan=_child_plan(root / "children/anchor", bootstrap=True),
        runner=epoch_v3.run_epoch,
        clients={"helius-free": client},
    )
    bundle_path = Path(result["bundle"])
    bundle = json.loads(bundle_path.read_text())
    child = root / "children/anchor"
    if mutation in {"request", "drop_request"}:
        path = child / "requests/000000000001.json"
        if mutation == "drop_request":
            path.unlink()
        else:
            value = json.loads(path.read_text())
            value["params"] = []
            path.write_text(canonical(value) + "\n")
    elif mutation == "capture":
        path = root / bundle["children"][0]["response_path"]
        path.write_bytes(path.read_bytes() + b"x")
    elif mutation == "receipt":
        path = root / bundle["children"][0]["receipt_path"]
        path.write_text("{}\n")
    elif mutation == "summary":
        path = child / "epoch-summary.json"
        value = json.loads(path.read_text())
        value["status"] = "partial"
        path.write_text(canonical(value) + "\n")
    elif mutation == "summary_total":
        path = child / "epoch-summary.json"
        value = json.loads(path.read_text())
        value["captured_response_bytes"] += 1
        path.write_text(canonical(value) + "\n")
    elif mutation == "manifest_sequence":
        bundle["children"][0]["sequence"] = 2
        bundle_path.write_text(canonical(bundle) + "\n")
        _rebind_bundle_parent(root, bundle_path)
    elif mutation == "result_receipt_time":
        result_path = child / "request-results/000000000001.json"
        result = json.loads(result_path.read_text())
        result["receipt_ns"] += 1
        result_path.write_text(canonical(result) + "\n")
        bundle["children"][0]["receipt_ns"] += 1
        bundle["children"][0]["result_file_sha256"] = hashlib.sha256(
            result_path.read_bytes()
        ).hexdigest()
        bundle_path.write_text(canonical(bundle) + "\n")
        _rebind_bundle_parent(root, bundle_path)
    else:
        bundle["actual_raw_bytes"] += 1
        bundle_path.write_text(canonical(bundle) + "\n")
    with pytest.raises(verifier_v2.VerificationError):
        verifier_v2.verify(root)


def test_every_child_position_has_independently_bound_request_result_and_capture(
    tmp_path: Path, controlled_host: list[int]
) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    anchor = FakeRPC(anchor=84)
    bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=root / "children/anchor",
        child_plan=_child_plan(root / "children/anchor", bootstrap=True),
        runner=epoch_v3.run_epoch,
        clients={"helius-free": anchor},
    )
    controlled_host[0] = now + 60_000_000_001
    frame_client = FakeRPC(anchor=102, start=85, end=103)
    result = bundle_v2.reserve_and_run(
        root,
        bucket_id="frame",
        operation_id="op-frame",
        child_root=root / "children/frame",
        child_plan=_child_plan(root / "children/frame", bootstrap=False, next_start=85),
        runner=epoch_v3.run_epoch,
        clients={"helius-free": frame_client},
    )
    child = root / "children/frame"
    bundle = json.loads(Path(result["bundle"]).read_text())
    for rel in (bundle["frame_path"], bundle["selection_path"]):
        path = root / rel
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with pytest.raises(verifier_v2.VerificationError):
            verifier_v2.verify(root)
        path.write_bytes(original)
    ordered = list(range(1, 35))
    random.Random(20261011).shuffle(ordered)
    for sequence in ordered:
        item = bundle["children"][sequence - 1]
        choice = sequence % 4
        paths = {
            0: child / "requests" / f"{sequence:012d}.json",
            1: child / "request-results" / f"{sequence:012d}.json",
            2: root / item["receipt_path"],
            3: root / item["response_path"],
        }
        path = paths[choice]
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with pytest.raises(verifier_v2.VerificationError):
            verifier_v2.verify(root)
        path.write_bytes(original)


def test_gap_requires_future_anchor_and_utc_day_caps_roll_over(
    tmp_path: Path, controlled_host: list[int]
) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    day = bundle_v2.UTC_DAY_NS
    plan = _parent_plan(now)
    plan["expires_ns"] = now + 3 * day
    plan["caps"].update(
        requests=10,
        raw_bytes=3_000_000_000,
        stored_bytes=3_000_000_000,
        daily_requests=1,
        daily_raw_bytes=1_000_000_000,
        daily_stored_bytes=1_000_000_000,
    )
    plan["buckets"] = [
        {"bucket_id": "a0", "kind": "anchor", "start_ns": now, "end_ns": now + 60_000_000_000},
        {
            "bucket_id": "missed",
            "kind": "frame",
            "start_ns": now + 60_000_000_000,
            "end_ns": now + 120_000_000_000,
        },
        {
            "bucket_id": "a1",
            "kind": "anchor",
            "start_ns": now + day,
            "end_ns": now + day + 60_000_000_000,
        },
    ]
    bundle_v2.initialize(root, plan)
    first = FakeRPC(anchor=84)
    bundle_v2.reserve_and_run(
        root,
        bucket_id="a0",
        operation_id="op-a0",
        child_root=root / "children/a0",
        child_plan=_child_plan(root / "children/a0", bootstrap=True),
        runner=epoch_v3.run_epoch,
        clients={"helius-free": first},
    )
    bundle_v2.record_gap(
        root, bucket_id="missed", at_ns=now + 120_000_000_000, reason="fixture_missed_bucket"
    )
    controlled_host[0] = now + day + 1
    second = FakeRPC(anchor=200)
    bundle_v2.reserve_and_run(
        root,
        bucket_id="a1",
        operation_id="op-a1",
        child_root=root / "children/a1",
        child_plan=_child_plan(root / "children/a1", bootstrap=True),
        runner=epoch_v3.run_epoch,
        clients={"helius-free": second},
    )
    assert len(first.calls) == len(second.calls) == 1
    assert verifier_v2.verify(root)["usage"]["requests"] == 2


@pytest.mark.parametrize("after_gap", [False, True])
def test_anchor_frontier_regression_stops_without_sealing_or_advancing(
    tmp_path: Path, controlled_host: list[int], after_gap: bool
) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    plan = _parent_plan(now)
    if after_gap:
        plan["expires_ns"] = now + 3 * bundle_v2.UTC_DAY_NS
        plan["buckets"] = [
            {"bucket_id": "a0", "kind": "anchor", "start_ns": now, "end_ns": now + 60_000_000_000},
            {
                "bucket_id": "missed",
                "kind": "frame",
                "start_ns": now + 60_000_000_000,
                "end_ns": now + 120_000_000_000,
            },
            {
                "bucket_id": "a1",
                "kind": "anchor",
                "start_ns": now + bundle_v2.UTC_DAY_NS,
                "end_ns": now + bundle_v2.UTC_DAY_NS + 60_000_000_000,
            },
        ]
    bundle_v2.initialize(root, plan)

    if after_gap:
        first = FakeRPC(anchor=84)
        bundle_v2.reserve_and_run(
            root,
            bucket_id="a0",
            operation_id="op-a0",
            child_root=root / "children/a0",
            child_plan=_child_plan(root / "children/a0", bootstrap=True),
            runner=epoch_v3.run_epoch,
            clients={"helius-free": first},
        )
        bundle_v2.record_gap(
            root,
            bucket_id="missed",
            at_ns=now + 120_000_000_000,
            reason="fixture_gap_before_reset_anchor",
        )
        controlled_host[0] = now + bundle_v2.UTC_DAY_NS + 1
        bucket_id, operation_id = "a1", "op-a1"
        expected_frontier = 85
    else:
        bucket_id, operation_id = "anchor", "op-anchor-regressed"
        expected_frontier = 84

    regressed = FakeRPC(anchor=82)
    with pytest.raises(
        bundle_v2.EnvelopeError, match="independent_child_bundle_verification_failed"
    ):
        bundle_v2.reserve_and_run(
            root,
            bucket_id=bucket_id,
            operation_id=operation_id,
            child_root=root / f"children/{bucket_id}",
            child_plan=_child_plan(root / f"children/{bucket_id}", bootstrap=True),
            runner=epoch_v3.run_epoch,
            clients={"helius-free": regressed},
        )

    rows = bundle_v2._events(root)
    assert len(regressed.calls) == 1
    assert not any(
        row["kind"] == "seal" and row.get("operation_id") == operation_id for row in rows
    )
    assert rows[-1]["kind"] == "stop"
    assert rows[-1]["unknown_interval"] is True
    sealed_anchors = [
        row
        for row in rows
        if row["kind"] == "seal"
        and next(
            event
            for event in rows
            if event["kind"] == "reservation" and event["operation_id"] == row["operation_id"]
        )["bucket_kind"]
        == "anchor"
    ]
    if sealed_anchors:
        last_bundle = next(
            row
            for row in rows
            if row["kind"] == "bundle" and row["operation_id"] == sealed_anchors[-1]["operation_id"]
        )
        assert last_bundle["frontier_after"] == expected_frontier
    assert verifier_v2.verify(root)["recovery"] == "stopped_no_replay"


def test_parent_writer_lock_is_exclusive(tmp_path: Path) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    with bundle_v2._writer(root):
        with pytest.raises(bundle_v2.EnvelopeError, match="parent_writer_already_active"):
            bundle_v2._writer(root)


def test_future_clock_and_low_measured_disk_fail_before_child_io(
    tmp_path: Path, controlled_host: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    def expire(_root: Path, now: int) -> None:
        controlled_host[0] = now + 61_000_000_000

    def drop_disk(_root: Path, _now: int) -> None:
        monkeypatch.setattr(
            bundle_v2.shutil,
            "disk_usage",
            lambda _path: shutil._ntuple_diskusage(60 * 1024**3, 20 * 1024**3, 40 * 1024**3),
        )

    for label, change_host, expected in (
        ("expired", expire, "outside_bucket_window"),
        (
            "low-disk",
            drop_disk,
            "physical_disk_floor_failed",
        ),
    ):
        root = tmp_path / label
        now = time.time_ns()
        controlled_host[0] = now
        bundle_v2.initialize(root, _parent_plan(now))
        change_host(root, now)
        client = FakeRPC(anchor=84)
        with pytest.raises(bundle_v2.EnvelopeError, match=expected):
            bundle_v2.reserve_and_run(
                root,
                bucket_id="anchor",
                operation_id="op-anchor",
                child_root=root / "children/anchor",
                child_plan=_child_plan(root / "children/anchor", bootstrap=True),
                runner=epoch_v3.run_epoch,
                clients={"helius-free": client},
            )
        assert not client.calls
        assert verifier_v2.verify(root)["operation_count"] == 0


def test_frame_frontier_is_next_unobserved_slot(tmp_path: Path, controlled_host: list[int]) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    anchor = FakeRPC(anchor=84)
    bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=root / "children/anchor",
        child_plan=_child_plan(root / "children/anchor", bootstrap=True),
        runner=epoch_v3.run_epoch,
        clients={"helius-free": anchor},
    )
    controlled_host[0] = now + 60_000_000_001
    bad = FakeRPC(anchor=102, start=84, end=103)
    with pytest.raises(
        bundle_v2.EnvelopeError, match="frame_does_not_start_at_next_unobserved_slot"
    ):
        bundle_v2.reserve_and_run(
            root,
            bucket_id="frame",
            operation_id="op-frame-bad",
            child_root=root / "children/frame-bad",
            child_plan=_child_plan(root / "children/frame-bad", bootstrap=False, next_start=84),
            runner=epoch_v3.run_epoch,
            clients={"helius-free": bad},
        )
    assert not bad.calls


def test_child_root_is_unique_across_operations(tmp_path: Path, controlled_host: list[int]) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    plan = _parent_plan(now)
    plan["buckets"][1] = {
        "bucket_id": "anchor-next",
        "kind": "anchor",
        "start_ns": now + 60_000_000_000,
        "end_ns": now + 120_000_000_000,
    }
    bundle_v2.initialize(root, plan)
    child_root = root / "children/reused"
    child_plan = _child_plan(child_root, bootstrap=True)
    first = FakeRPC(anchor=84)
    bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=child_root,
        child_plan=child_plan,
        runner=epoch_v3.run_epoch,
        clients={"helius-free": first},
    )
    controlled_host[0] = now + 60_000_000_001
    second = FakeRPC(anchor=90)
    with pytest.raises(bundle_v2.EnvelopeError, match="child_root_already_bound_to_operation"):
        bundle_v2.reserve_and_run(
            root,
            bucket_id="anchor-next",
            operation_id="op-anchor-next",
            child_root=child_root,
            child_plan=child_plan,
            runner=epoch_v3.run_epoch,
            clients={"helius-free": second},
        )
    assert not second.calls


def test_client_without_test_call_counter_does_not_stop_after_seal(tmp_path: Path) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    client = MinimalRPC(FakeRPC(anchor=84))
    result = bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=root / "children/anchor",
        child_plan=_child_plan(root / "children/anchor", bootstrap=True),
        runner=epoch_v3.run_epoch,
        clients={"helius-free": client},
    )
    assert result["status"] == "captured_bundle"
    assert result["child_request_reservations"] == 1
    rows = bundle_v2._events(root)
    assert rows[-1]["kind"] == "seal"
    assert not any(row["kind"] == "stop" for row in rows)


def test_terminal_stop_dominates_verified_unsealed_bundle_recovery(tmp_path: Path) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    client = FakeRPC(anchor=84)
    plan = _child_plan(root / "children/anchor", bootstrap=True)
    bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=root / "children/anchor",
        child_plan=plan,
        runner=epoch_v3.run_epoch,
        clients={"helius-free": client},
        crash_at="before_parent_seal",
    )
    bundle_v2._stop(root, "op-anchor", "anchor", "fixture_terminal_stop")
    result = verifier_v2.verify(root)
    assert result["recovery"] == "stopped_no_replay"
    assert len(client.calls) == 1


def test_expired_started_operation_is_stopped_without_resume_dispatch(
    tmp_path: Path, controlled_host: list[int]
) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    client = FakeRPC(anchor=84)
    plan = _child_plan(root / "children/anchor", bootstrap=True)
    first = bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=root / "children/anchor",
        child_plan=plan,
        runner=epoch_v3.run_epoch,
        clients={"helius-free": client},
        crash_at="after_start_before_dispatch",
    )
    assert first["child_request_reservations"] == 0
    controlled_host[0] = now + 61_000_000_000
    with pytest.raises(bundle_v2.EnvelopeError, match="outside_bucket_window"):
        bundle_v2.reserve_and_run(
            root,
            bucket_id="anchor",
            operation_id="op-anchor",
            child_root=root / "children/anchor",
            child_plan=plan,
            runner=epoch_v3.run_epoch,
            clients={"helius-free": client},
        )
    assert not client.calls
    assert verifier_v2.verify(root)["recovery"] == "stop_unknown_started_operation_no_replay"


def test_start_disk_recheck_stops_before_first_child_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    samples = [100 * 1024**3, 40 * 1024**3]

    def disk_usage(_path: Path) -> Any:
        free = samples.pop(0)
        return shutil._ntuple_diskusage(200 * 1024**3, 200 * 1024**3 - free, free)

    monkeypatch.setattr(bundle_v2.shutil, "disk_usage", disk_usage)
    client = FakeRPC(anchor=84)
    with pytest.raises(bundle_v2.EnvelopeError, match="start_clock_or_disk_recheck_failed"):
        bundle_v2.reserve_and_run(
            root,
            bucket_id="anchor",
            operation_id="op-anchor",
            child_root=root / "children/anchor",
            child_plan=_child_plan(root / "children/anchor", bootstrap=True),
            runner=epoch_v3.run_epoch,
            clients={"helius-free": client},
        )
    assert not client.calls
    assert verifier_v2.verify(root)["recovery"] == "stopped_no_replay"


def test_each_child_dispatch_rechecks_bucket_window(
    tmp_path: Path, controlled_host: list[int]
) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=root / "children/anchor",
        child_plan=_child_plan(root / "children/anchor", bootstrap=True),
        runner=epoch_v3.run_epoch,
        clients={"helius-free": FakeRPC(anchor=84)},
    )
    frame_start = now + 60_000_000_001
    frame_end = now + 120_000_000_000
    controlled_host[0] = frame_start
    frame = FakeRPC(
        anchor=102,
        start=85,
        end=103,
        after_fetch=lambda: controlled_host.__setitem__(0, frame_end),
    )
    with pytest.raises(bundle_v2.EnvelopeError):
        bundle_v2.reserve_and_run(
            root,
            bucket_id="frame",
            operation_id="op-frame",
            child_root=root / "children/frame",
            child_plan=_child_plan(root / "children/frame", bootstrap=False, next_start=85),
            runner=epoch_v3.run_epoch,
            clients={"helius-free": frame},
        )
    assert len(frame.calls) == 1
    rows = bundle_v2._events(root)
    assert rows[-1]["kind"] == "stop"
    assert rows[-1]["unknown_interval"] is True


def test_dispatched_but_unresolved_child_request_stops_without_replay(tmp_path: Path) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    client = FakeRPC(anchor=84, fail_method="getSlot")
    plan = _child_plan(root / "children/anchor", bootstrap=True)
    with pytest.raises(bundle_v2.EnvelopeError, match="child_request_result_not_captured"):
        bundle_v2.reserve_and_run(
            root,
            bucket_id="anchor",
            operation_id="op-anchor",
            child_root=root / "children/anchor",
            child_plan=plan,
            runner=epoch_v3.run_epoch,
            clients={"helius-free": client},
        )
    assert len(client.calls) == 1
    assert verifier_v2.verify(root)["recovery"] == "stop_unknown_started_operation_no_replay"
    with pytest.raises(bundle_v2.EnvelopeError, match="parent_stopped"):
        bundle_v2.reserve_and_run(
            root,
            bucket_id="anchor",
            operation_id="op-anchor",
            child_root=root / "children/anchor",
            child_plan=plan,
            runner=epoch_v3.run_epoch,
            clients={"helius-free": client},
        )
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "crash_at,expected_calls",
    [
        ("after_reservation", 0),
        ("after_start_before_dispatch", 0),
        ("after_capture_fsync", 1),
        ("before_parent_seal", 1),
    ],
)
def test_crash_prefix_never_replays_started_operation_identity(
    tmp_path: Path, crash_at: str, expected_calls: int
) -> None:
    root = tmp_path / "parent"
    now = time.time_ns()
    bundle_v2.initialize(root, _parent_plan(now))
    client = FakeRPC(anchor=84)
    plan = _child_plan(root / "children/anchor", bootstrap=True)
    bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=root / "children/anchor",
        child_plan=plan,
        runner=epoch_v3.run_epoch,
        clients={"helius-free": client},
        crash_at=crash_at,
    )
    assert len(client.calls) == expected_calls
    if crash_at == "before_parent_seal":
        assert verifier_v2.verify(root)["recovery"] == "verified_bundle_ready_to_seal_no_replay"
    if crash_at == "after_capture_fsync":
        assert verifier_v2.verify(root)["recovery"] == "stop_unknown_started_operation_no_replay"
        with pytest.raises(bundle_v2.EnvelopeError, match="parent_stopped"):
            bundle_v2.reserve_and_run(
                root,
                bucket_id="anchor",
                operation_id="op-anchor",
                child_root=root / "children/anchor",
                child_plan=plan,
                runner=epoch_v3.run_epoch,
                clients={"helius-free": client},
            )
        assert len(client.calls) == expected_calls
        return
    if crash_at == "after_reservation":
        assert (
            verifier_v2.verify(root)["recovery"]
            == "unstarted_reservation_may_be_repaired_or_gapped"
        )
    resumed = bundle_v2.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="op-anchor",
        child_root=root / "children/anchor",
        child_plan=plan,
        runner=epoch_v3.run_epoch,
        clients={"helius-free": client},
    )
    assert resumed["status"] == "captured_bundle"
    assert len(client.calls) == expected_calls + (1 if expected_calls == 0 else 0)
    with pytest.raises(bundle_v2.EnvelopeError, match="operation_identity_already_used_no_replay"):
        bundle_v2.reserve_and_run(
            root,
            bucket_id="anchor",
            operation_id="op-anchor",
            child_root=root / "children/anchor",
            child_plan=plan,
            runner=epoch_v3.run_epoch,
            clients={"helius-free": client},
        )
    assert len(client.calls) == expected_calls + (1 if expected_calls == 0 else 0)
