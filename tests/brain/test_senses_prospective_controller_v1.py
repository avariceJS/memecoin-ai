from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tools import brain_senses_prospective_controller_v1 as controller

NOW = 100
DISK_FLOOR = 50 * 1024**3


def _plan() -> dict:
    return {
        "schema": controller.SCHEMA,
        "lease_id": "lease-test-001",
        "created_ns": 90,
        "expires_ns": 5_000,
        "source_sha256": {"runner": "a" * 64, "credential_loader": "b" * 64},
        "predecessor": {
            "terminal_receipt_sha256": "c" * 64,
            "stop_sha256": "d" * 64,
            "frontier_slot": 123,
            "gaps": [{"from_slot": 124, "to_slot_exclusive": None, "status": "unknown_ended"}],
        },
        "caps": {
            "requests": 4,
            "raw_bytes": 100,
            "stored_bytes": 80,
            "physical_bytes": DISK_FLOOR,
            "width": 20_000,
            "daily_requests": 4,
            "daily_raw_bytes": 100,
            "daily_stored_bytes": 80,
        },
        "buckets": [
            {"bucket_id": "anchor-1", "kind": "anchor", "start_ns": 1_000, "end_ns": 2_000},
            {"bucket_id": "frame-1", "kind": "frame", "start_ns": 3_000, "end_ns": 4_000},
        ],
    }


def _reserve(root: Path, **kwargs: object) -> dict:
    kwargs.setdefault("method", "getSlot")
    kwargs.setdefault("request_sha256", "e" * 64)
    return controller.reserve(root, **kwargs)  # type: ignore[arg-type]


def _initialized(tmp_path: Path) -> Path:
    root = tmp_path / "lease"
    controller.initialize(root, _plan(), now_ns=NOW)
    return root


def _capture_and_seal(root: Path, tmp_path: Path, *, bucket: str, request_id: str, t: int) -> None:
    _reserve(
        root,
        request_id=request_id,
        bucket_id=bucket,
        requests=1,
        raw_bytes=10,
        stored_bytes=10,
        width=1,
        at_ns=t,
        physical_free_bytes=DISK_FLOOR,
    )
    controller.mark_started(
        root, request_id=request_id, at_ns=t + 1, physical_free_bytes=DISK_FLOOR
    )
    artifact = tmp_path / f"{request_id}.raw"
    artifact.write_bytes(b"raw-proof")
    os.chmod(artifact, 0o400)
    controller.record_capture(
        root,
        request_id=request_id,
        artifact_path=artifact,
        event_time={"slot": 500},
        receipt_ns=t + 2,
        availability_ns=t + 3,
    )
    controller.record_result(
        root,
        request_id=request_id,
        status_value="captured",
        actual_requests=1,
        actual_raw_bytes=9,
        actual_stored_bytes=9,
        receipt_ns=t + 2,
        availability_ns=t + 3,
    )
    controller.seal(root, request_id=request_id, at_ns=t + 4)


def test_plan_hash_chain_and_anchor_then_frame(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    _capture_and_seal(root, tmp_path, bucket="anchor-1", request_id="request-1", t=1_000)
    _capture_and_seal(root, tmp_path, bucket="frame-1", request_id="request-2", t=3_000)
    state = controller.recover(root, at_ns=3_100)
    assert state["usage"] == {"requests": 2, "raw_bytes": 18, "stored_bytes": 18}
    assert state["outstanding_request_ids"] == []
    lines = (root / "events-v1.jsonl").read_text().splitlines()
    assert len(lines) == 11
    assert all(json.loads(line)["event_sha256"] for line in lines)


@pytest.mark.parametrize(
    ("field", "value"),
    [("requests", 5), ("raw_bytes", 101), ("stored_bytes", 81), ("width", 20_001)],
)
def test_each_reservation_dimension_fails_closed(tmp_path: Path, field: str, value: int) -> None:
    root = _initialized(tmp_path)
    args = {"requests": 1, "raw_bytes": 10, "stored_bytes": 10, "width": 1}
    args[field] = value
    with pytest.raises(controller.ControllerError):
        _reserve(
            root,
            request_id="too-large",
            bucket_id="anchor-1",
            **args,
            at_ns=1_000,
            physical_free_bytes=DISK_FLOOR,
        )


@pytest.mark.parametrize("offset", [-1, 0, 1, 2, 3, 4, 5])
def test_physical_floor_boundary_is_inclusive_only_at_floor(tmp_path: Path, offset: int) -> None:
    root = _initialized(tmp_path)
    if offset < 0:
        with pytest.raises(controller.ControllerError, match="physical_disk_floor_failed"):
            _reserve(
                root,
                request_id="r",
                bucket_id="anchor-1",
                requests=1,
                raw_bytes=1,
                stored_bytes=1,
                width=0,
                at_ns=1_000,
                physical_free_bytes=DISK_FLOOR + offset,
            )
    else:
        _reserve(
            root,
            request_id="r",
            bucket_id="anchor-1",
            requests=1,
            raw_bytes=1,
            stored_bytes=1,
            width=0,
            at_ns=1_000,
            physical_free_bytes=DISK_FLOOR + offset,
        )


def test_missed_bucket_must_be_gap_and_cannot_be_caught_up(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    with pytest.raises(controller.ControllerError, match="missed_bucket_requires_explicit_gap"):
        _reserve(
            root,
            request_id="late",
            bucket_id="frame-1",
            requests=1,
            raw_bytes=1,
            stored_bytes=1,
            width=1,
            at_ns=3_100,
            physical_free_bytes=DISK_FLOOR,
        )
    controller.record_gap(root, bucket_id="anchor-1", at_ns=2_001, reason="missed_tick")
    with pytest.raises(controller.ControllerError, match="gap_requires_future_anchor_only"):
        _reserve(
            root,
            request_id="late",
            bucket_id="frame-1",
            requests=1,
            raw_bytes=1,
            stored_bytes=1,
            width=1,
            at_ns=3_100,
            physical_free_bytes=DISK_FLOOR,
        )


def test_gap_requires_new_anchor_before_any_frame(tmp_path: Path) -> None:
    plan = _plan()
    plan["expires_ns"] = 9_000
    plan["buckets"].extend(
        [
            {"bucket_id": "anchor-2", "kind": "anchor", "start_ns": 4_100, "end_ns": 4_300},
            {"bucket_id": "frame-2", "kind": "frame", "start_ns": 4_500, "end_ns": 4_800},
        ]
    )
    root = tmp_path / "lease"
    controller.initialize(root, plan, now_ns=NOW)
    _capture_and_seal(root, tmp_path, bucket="anchor-1", request_id="anchor", t=1_000)
    controller.record_gap(root, bucket_id="frame-1", at_ns=4_001, reason="missed_tick")
    _capture_and_seal(root, tmp_path, bucket="anchor-2", request_id="anchor-reset", t=4_100)
    _reserve(
        root,
        request_id="frame-after-reset",
        bucket_id="frame-2",
        requests=1,
        raw_bytes=1,
        stored_bytes=1,
        width=1,
        at_ns=4_500,
        physical_free_bytes=DISK_FLOOR,
    )


def test_failed_attempt_consumes_full_reservation_and_stops(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    _reserve(
        root,
        request_id="failed",
        bucket_id="anchor-1",
        requests=2,
        raw_bytes=10,
        stored_bytes=8,
        width=1,
        at_ns=1_000,
        physical_free_bytes=DISK_FLOOR,
    )
    controller.mark_started(root, request_id="failed", at_ns=1_001, physical_free_bytes=DISK_FLOOR)
    controller.record_result(
        root,
        request_id="failed",
        status_value="failed",
        actual_requests=1,
        actual_raw_bytes=0,
        actual_stored_bytes=0,
        receipt_ns=1_010,
        availability_ns=None,
    )
    state = controller.status(root, now_ns=1_020)
    assert state["stopped"] is True
    assert state["usage"] == {"requests": 2, "raw_bytes": 10, "stored_bytes": 8}


def test_premature_normal_close_is_byte_preserving_and_does_not_stop(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    _capture_and_seal(root, tmp_path, bucket="anchor-1", request_id="anchor", t=1_000)
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    with pytest.raises(controller.ControllerError, match="schedule_not_exhausted"):
        controller.close_lease(root, frontier_slot=501, at_ns=1_100)
    assert before == {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    assert not (root / "STOP").exists()
    assert not (root / "TERMINAL.json").exists()


def test_early_owner_cancel_is_abnormal_and_censors_unstarted_buckets(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    _capture_and_seal(root, tmp_path, bucket="anchor-1", request_id="anchor", t=1_000)
    state = controller.cancel_lease(root, at_ns=1_100)
    assert state["stopped"] is True
    assert state["cancelled_unstarted"] == ["frame-1"]
    assert not (root / "TERMINAL.json").exists()
    assert not any(event["kind"] == "gap" for event in controller._events(root))
    with pytest.raises(
        controller.ControllerError, match="abnormal_stop_cannot_auto_close_or_renew"
    ):
        controller.close_lease(root, frontier_slot=501, at_ns=1_101)
    successor_plan = _plan()
    successor_plan["lease_id"] = "must-not-be-created"
    successor_plan["review"] = {"review_id": "review", "decision_sha256": "f" * 64}
    successor = tmp_path / "cancelled-successor"
    with pytest.raises(
        controller.ControllerError, match="abnormal_stop_cannot_auto_close_or_renew"
    ):
        controller.initialize_successor(
            root, successor, successor_plan, frontier_slot=501, at_ns=1_101
        )
    assert not successor.exists()


def test_owner_cancel_does_not_reclassify_outstanding_request(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    _reserve(
        root,
        request_id="outstanding",
        bucket_id="anchor-1",
        requests=1,
        raw_bytes=10,
        stored_bytes=10,
        width=1,
        at_ns=1_000,
        physical_free_bytes=DISK_FLOOR,
    )
    controller.mark_started(
        root, request_id="outstanding", at_ns=1_001, physical_free_bytes=DISK_FLOOR
    )
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    with pytest.raises(
        controller.ControllerError, match="owner_cancel_with_outstanding_request_forbidden"
    ):
        controller.cancel_lease(root, at_ns=1_100)
    assert before == {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    assert not (root / "STOP").exists()


def test_backdated_owner_cancel_is_byte_preserving(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    _capture_and_seal(root, tmp_path, bucket="anchor-1", request_id="anchor", t=1_000)
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    with pytest.raises(controller.ControllerError, match="stop_time_precedes_durable_event"):
        controller.cancel_lease(root, at_ns=1_003)
    assert before == {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    assert not (root / "STOP").exists()
    assert not (root / "TERMINAL.json").exists()


@pytest.mark.parametrize("prefix", ["reserved", "started"])
def test_crash_prefix_cannot_become_normal_terminal(tmp_path: Path, prefix: str) -> None:
    root = _initialized(tmp_path)
    _reserve(
        root,
        request_id="interrupted",
        bucket_id="anchor-1",
        requests=1,
        raw_bytes=10,
        stored_bytes=10,
        width=1,
        at_ns=1_000,
        physical_free_bytes=DISK_FLOOR,
    )
    if prefix == "started":
        controller.mark_started(
            root, request_id="interrupted", at_ns=1_001, physical_free_bytes=DISK_FLOOR
        )
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    with pytest.raises(controller.ControllerError, match="cannot_close_with_outstanding_request"):
        controller.close_lease(root, frontier_slot=123, at_ns=1_100)
    assert before == {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    assert not (root / "STOP").exists()
    assert not (root / "TERMINAL.json").exists()


@pytest.mark.parametrize("crash_after", ["result", "seal"])
def test_failed_result_crash_recovery_stops_and_never_reserves_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_after: str
) -> None:
    root = _initialized(tmp_path)
    _reserve(
        root,
        request_id="failed",
        bucket_id="anchor-1",
        requests=1,
        raw_bytes=10,
        stored_bytes=10,
        width=1,
        at_ns=1_000,
        physical_free_bytes=DISK_FLOOR,
    )
    controller.mark_started(root, request_id="failed", at_ns=1_001, physical_free_bytes=DISK_FLOOR)
    if crash_after == "result":
        monkeypatch.setattr(
            controller, "seal", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("crash"))
        )
    else:
        monkeypatch.setattr(
            controller, "stop", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("crash"))
        )
    with pytest.raises(OSError, match="crash"):
        controller.record_result(
            root,
            request_id="failed",
            status_value="failed",
            actual_requests=1,
            actual_raw_bytes=0,
            actual_stored_bytes=0,
            receipt_ns=1_010,
            availability_ns=None,
        )
    monkeypatch.undo()
    with pytest.raises(controller.ControllerError, match="provider_request_failed"):
        controller.recover(root, at_ns=1_020)
    marker = json.loads((root / "STOP").read_bytes())
    assert marker["reason"] == "provider_request_failed"
    with pytest.raises(controller.ControllerError, match="lease_stopped"):
        _reserve(
            root,
            request_id="must-not-replay",
            bucket_id="frame-1",
            requests=1,
            raw_bytes=1,
            stored_bytes=1,
            width=1,
            at_ns=3_000,
            physical_free_bytes=DISK_FLOOR,
        )


def test_day_caps_are_independent_of_finite_lease_caps(tmp_path: Path) -> None:
    plan = _plan()
    plan["caps"]["daily_requests"] = 1
    plan["buckets"].insert(
        1, {"bucket_id": "anchor-2", "kind": "anchor", "start_ns": 2_100, "end_ns": 2_900}
    )
    plan["buckets"][2]["start_ns"] = 3_000
    plan["buckets"][2]["end_ns"] = 4_000
    root = tmp_path / "lease"
    controller.initialize(root, plan, now_ns=NOW)
    _capture_and_seal(root, tmp_path, bucket="anchor-1", request_id="first", t=1_000)
    with pytest.raises(controller.ControllerError, match="utc_day_budget_exhausted"):
        _reserve(
            root,
            request_id="second",
            bucket_id="anchor-2",
            requests=1,
            raw_bytes=1,
            stored_bytes=1,
            width=1,
            at_ns=2_100,
            physical_free_bytes=DISK_FLOOR,
        )


def test_ambiguous_started_attempt_stops_without_replay(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    _reserve(
        root,
        request_id="ambiguous",
        bucket_id="anchor-1",
        requests=1,
        raw_bytes=10,
        stored_bytes=10,
        width=1,
        at_ns=1_000,
        physical_free_bytes=DISK_FLOOR,
    )
    controller.mark_started(
        root, request_id="ambiguous", at_ns=1_001, physical_free_bytes=DISK_FLOOR
    )
    with pytest.raises(controller.ControllerError, match="ambiguous_started_request_no_replay"):
        controller.recover(root, at_ns=1_100)
    first = json.loads((root / "STOP").read_bytes())
    assert first["reason"] == "ambiguous_started_request_no_replay"
    assert controller.stop(root, reason="later_reason", at_ns=1_200) == first
    with pytest.raises(controller.ControllerError, match="lease_stopped"):
        _reserve(
            root,
            request_id="retry",
            bucket_id="anchor-1",
            requests=1,
            raw_bytes=1,
            stored_bytes=1,
            width=1,
            at_ns=1_300,
            physical_free_bytes=DISK_FLOOR,
        )


def test_capture_preserves_distinct_times_and_binds_raw_hash(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    _reserve(
        root,
        request_id="r",
        bucket_id="anchor-1",
        requests=1,
        raw_bytes=8,
        stored_bytes=8,
        width=1,
        at_ns=1_000,
        physical_free_bytes=DISK_FLOOR,
    )
    controller.mark_started(root, request_id="r", at_ns=1_001, physical_free_bytes=DISK_FLOOR)
    artifact = tmp_path / "r.raw"
    artifact.write_bytes(b"12345678")
    controller.record_capture(
        root,
        request_id="r",
        artifact_path=artifact,
        event_time={"slot": 1234},
        receipt_ns=1_010,
        availability_ns=1_020,
    )
    capture = next(
        json.loads(x)
        for x in (root / "events-v1.jsonl").read_text().splitlines()
        if json.loads(x)["kind"] == "capture"
    )
    assert capture["event_time"] == {"slot": 1234}
    assert capture["receipt_ns"] == 1_010
    assert capture["availability_ns"] == 1_020
    assert capture["decision_ns"] is None
    assert capture["artifact_sha256"] == hashlib.sha256(b"12345678").hexdigest()


def test_hash_tampering_stops_recovery(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    path = root / "events-v1.jsonl"
    line = json.loads(path.read_text())
    line["at_ns"] += 1
    path.write_text(json.dumps(line) + "\n")
    with pytest.raises(controller.ControllerError, match="ledger_sequence_or_hash_invalid"):
        controller.recover(root, at_ns=150)
    stop_record = json.loads((root / "STOP").read_bytes())
    assert stop_record["reason"] == "local_evidence_uncertain"


def test_generation_close_transfers_frontier_and_gaps_to_new_reviewed_lease(
    tmp_path: Path,
) -> None:
    previous = _initialized(tmp_path)
    _capture_and_seal(previous, tmp_path, bucket="anchor-1", request_id="anchor", t=1_000)
    controller.record_gap(previous, bucket_id="frame-1", at_ns=4_000, reason="elapsed_unobserved")
    successor = tmp_path / "lease-next"
    next_plan = _plan()
    next_plan.update({"lease_id": "lease-test-002", "created_ns": 4_100, "expires_ns": 9_000})
    next_plan["buckets"] = [
        {"bucket_id": "anchor-next", "kind": "anchor", "start_ns": 5_000, "end_ns": 6_000}
    ]
    next_plan["review"] = {"review_id": "sol-decision-002", "decision_sha256": "f" * 64}

    initialized = controller.initialize_successor(
        previous, successor, next_plan, frontier_slot=501, at_ns=4_100
    )

    terminal = json.loads((previous / "TERMINAL.json").read_bytes())
    carried_plan = json.loads((successor / "plan-v1.json").read_bytes())
    assert terminal["status"] == "lease_closed"
    assert terminal["frontier_slot"] == 501
    assert json.loads((previous / "STOP").read_bytes())["reason"] == "finite_lease_complete"
    assert (
        carried_plan["predecessor"]["terminal_receipt_sha256"]
        == hashlib.sha256((previous / "TERMINAL.json").read_bytes()).hexdigest()
    )
    assert carried_plan["predecessor"]["frontier_slot"] == 501
    assert initialized["lease_id"] == "lease-test-002"
    assert not (successor / "STOP").exists()
