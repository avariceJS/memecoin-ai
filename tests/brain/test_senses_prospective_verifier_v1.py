from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import brain_senses_prospective_controller_v1 as controller
from tools import brain_senses_prospective_verifier_v1 as verifier

NOW = 100
DISK_FLOOR = 50 * 1024**3


def _plan() -> dict:
    return {
        "schema": controller.SCHEMA,
        "lease_id": "lease-verifier-001",
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


def _init(tmp_path: Path, *, plan: dict | None = None, name: str = "lease") -> Path:
    root = tmp_path / name
    controller.initialize(root, plan or _plan(), now_ns=NOW)
    return root


def _reserve(root: Path, request_id: str = "request-1") -> None:
    controller.reserve(
        root,
        request_id=request_id,
        bucket_id="anchor-1",
        method="getSlot",
        request_sha256="e" * 64,
        requests=1,
        raw_bytes=100,
        stored_bytes=80,
        width=1,
        at_ns=1_000,
        physical_free_bytes=DISK_FLOOR,
    )


def _start(root: Path, request_id: str = "request-1") -> None:
    controller.mark_started(
        root, request_id=request_id, at_ns=1_001, physical_free_bytes=DISK_FLOOR
    )


def _capture(root: Path, request_id: str = "request-1") -> None:
    artifact = root / f"{request_id}.raw"
    artifact.write_bytes(b"raw-proof")
    artifact.chmod(0o400)
    controller.record_capture(
        root,
        request_id=request_id,
        artifact_path=artifact,
        event_time={"slot": 500},
        receipt_ns=1_002,
        availability_ns=1_003,
    )


def _result(root: Path, request_id: str = "request-1") -> None:
    controller.record_result(
        root,
        request_id=request_id,
        status_value="captured",
        actual_requests=1,
        actual_raw_bytes=9,
        actual_stored_bytes=9,
        receipt_ns=1_002,
        availability_ns=1_003,
    )


def _seal(root: Path, request_id: str = "request-1") -> None:
    controller.seal(root, request_id=request_id, at_ns=1_004)


def _verify(root: Path, *, now_ns: int = 1_100, **kwargs: object) -> dict:
    return verifier.verify_lease(root, now_ns=now_ns, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("prefix", "state", "recommendation"),
    [
        ("initialized", "open_consistent", "ledger_consistent_offline_only"),
        ("reserved", "reservation_outstanding", "stop_outstanding_reservation_no_replay"),
        ("started", "started_ambiguous", "stop_ambiguous_started_request_no_replay"),
        ("captured", "started_ambiguous", "stop_ambiguous_started_request_no_replay"),
        ("result", "reservation_outstanding", "stop_outstanding_reservation_no_replay"),
        ("sealed", "open_consistent", "ledger_consistent_offline_only"),
    ],
)
def test_every_durable_request_prefix_is_verified_without_repair(
    tmp_path: Path, prefix: str, state: str, recommendation: str
) -> None:
    root = _init(tmp_path)
    if prefix != "initialized":
        _reserve(root)
    if prefix in {"started", "captured", "result", "sealed"}:
        _start(root)
    if prefix in {"captured", "result", "sealed"}:
        _capture(root)
    if prefix in {"result", "sealed"}:
        _result(root)
    if prefix == "sealed":
        _seal(root)

    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    result = _verify(root)
    after = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    assert result["valid"] is True
    assert result["state"] == state
    assert result["recovery_recommendation"] == recommendation
    assert before == after


def test_source_and_review_expectations_are_external_bindings(tmp_path: Path) -> None:
    root = _init(tmp_path)
    plan = json.loads((root / "plan-v1.json").read_bytes())
    matched = _verify(root, expected_source_sha256=plan["source_sha256"])
    assert matched["valid"] is True
    assert matched["source_hashes_matched_external_expectation"] is True
    mismatch = _verify(root, expected_source_sha256={"runner": "f" * 64})
    assert mismatch["valid"] is False
    assert mismatch["error"] == "expected_source_binding_mismatch"

    reviewed_plan = _plan()
    reviewed_plan["review"] = {"review_id": "offline-review-1", "decision_sha256": "f" * 64}
    reviewed_root = _init(tmp_path, plan=reviewed_plan, name="reviewed")
    assert _verify(reviewed_root, expected_review_sha256="f" * 64)[
        "review_hash_matched_external_expectation"
    ]
    assert _verify(reviewed_root, expected_review_sha256="0" * 64)["valid"] is False


def test_read_only_cli_emits_machine_readable_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _init(tmp_path)
    assert verifier.main([str(root), "--now-ns", "1000"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert report["activation_authorized"] is False


def test_artifact_mutation_and_unallowlisted_capture_fail_closed(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _reserve(root)
    _start(root)
    _capture(root)
    artifact = root / "request-1.raw"
    artifact.chmod(0o600)
    assert _verify(root)["error"] == "artifact_not_read_only"

    artifact.write_bytes(b"tampered")
    artifact.chmod(0o400)
    assert _verify(root)["error"] == "capture_artifact_hash_or_length_mismatch"


def test_external_capture_requires_explicit_artifact_allowlist(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _reserve(root)
    _start(root)
    artifact = tmp_path / "outside.raw"
    artifact.write_bytes(b"external")
    artifact.chmod(0o400)
    controller.record_capture(
        root,
        request_id="request-1",
        artifact_path=artifact,
        event_time={"slot": 500},
        receipt_ns=1_002,
        availability_ns=1_003,
    )
    assert _verify(root)["error"] == "capture_artifact_outside_allowlist"
    assert _verify(root, allowed_artifact_roots=(tmp_path,))["valid"] is True


def test_stop_marker_written_before_ledger_is_reported_without_mutation(tmp_path: Path) -> None:
    root = _init(tmp_path)
    marker = {
        "schema": controller.SCHEMA,
        "status": "stopped",
        "reason": "local_evidence_uncertain",
        "stopped_ns": 500,
    }
    (root / "STOP").write_bytes(controller._canonical(marker) + b"\n")
    (root / "STOP").chmod(0o600)
    before = (root / "events-v1.jsonl").read_bytes()
    result = _verify(root)
    assert result["valid"] is True
    assert result["state"] == "stop_marker_without_event"
    assert result["recovery_recommendation"] == "reconcile_stop_ledger_only"
    assert (root / "events-v1.jsonl").read_bytes() == before


def test_stop_event_without_marker_requires_marker_restore_only(tmp_path: Path) -> None:
    root = _init(tmp_path)
    controller.stop(root, reason="disk_floor_failed", at_ns=500)
    (root / "STOP").unlink()
    result = _verify(root)
    assert result["valid"] is True
    assert result["state"] == "stop_event_without_marker"
    assert result["recovery_recommendation"] == "restore_stop_marker_only"


def test_durable_started_append_then_caller_error_never_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init(tmp_path)
    _reserve(root)
    append = controller._append_locked

    def append_then_raise(lease_root: Path, event: dict) -> dict:
        row = append(lease_root, event)
        if event.get("kind") == "started":
            raise OSError("simulated caller lost acknowledgement after fsync")
        return row

    monkeypatch.setattr(controller, "_append_locked", append_then_raise)
    with pytest.raises(OSError):
        _start(root)
    monkeypatch.setattr(controller, "_append_locked", append)
    result = _verify(root)
    assert result["valid"] is True
    assert result["state"] == "started_ambiguous"
    assert result["recovery_recommendation"] == "stop_ambiguous_started_request_no_replay"
    assert len((root / "events-v1.jsonl").read_text().splitlines()) == 3


def test_ledger_tampering_is_reported_without_writing_stop(tmp_path: Path) -> None:
    root = _init(tmp_path)
    path = root / "events-v1.jsonl"
    first = json.loads(path.read_text())
    first["at_ns"] += 1
    path.write_text(json.dumps(first) + "\n")
    before = path.read_bytes()
    result = _verify(root)
    assert result["valid"] is False
    assert result["recovery_recommendation"] == "stop_and_never_replay_ambiguous_request"
    assert not (root / "STOP").exists()
    assert path.read_bytes() == before


def test_caps_and_floor_are_checked_at_reservation_and_dispatch(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _reserve(root)
    _start(root)
    _capture(root)
    _result(root)
    _seal(root)
    assert _verify(root)["usage"] == {"requests": 1, "raw_bytes": 9, "stored_bytes": 9}

    events_path = root / "events-v1.jsonl"
    rows = [json.loads(line) for line in events_path.read_text().splitlines()]
    reservation = rows[1]
    reservation["physical_free_bytes"] = DISK_FLOOR - 1
    body = dict(reservation)
    body.pop("event_sha256")
    body["event_sha256"] = hashlib.sha256(controller._canonical(body)).hexdigest()
    rows[1] = body
    for index, row in enumerate(rows):
        row["seq"] = index
        row["previous_sha256"] = "0" * 64 if index == 0 else rows[index - 1]["event_sha256"]
        if index != 1:
            body = dict(row)
            body.pop("event_sha256")
            row["event_sha256"] = hashlib.sha256(controller._canonical(body)).hexdigest()
    events_path.write_bytes(b"".join(controller._canonical(row) + b"\n" for row in rows))
    assert _verify(root)["error"] == "reservation_disk_floor_failed"


def test_terminal_frontier_gaps_and_successor_transfer_are_independently_verified(
    tmp_path: Path,
) -> None:
    prior = _init(tmp_path, name="prior")
    _reserve(prior)
    _start(prior)
    _capture(prior)
    _result(prior)
    _seal(prior)
    terminal = controller.close_lease(prior, frontier_slot=501, at_ns=1_100)
    closed = _verify(prior)
    assert closed["valid"] is True
    assert closed["state"] == "closed"
    assert closed["normal_terminal_verified"] is True
    assert closed["activation_authorized"] is False
    assert terminal["frontier_slot"] == 501

    successor_plan = _plan()
    successor_plan.update(
        {"lease_id": "lease-verifier-002", "created_ns": 1_200, "expires_ns": 9_000}
    )
    successor_plan["buckets"] = [
        {"bucket_id": "anchor-next", "kind": "anchor", "start_ns": 2_000, "end_ns": 2_500}
    ]
    successor_plan["review"] = {"review_id": "fresh-review", "decision_sha256": "f" * 64}
    successor = tmp_path / "successor"
    controller.initialize_successor(
        prior, successor, successor_plan, frontier_slot=501, at_ns=1_200
    )
    checked = _verify(
        successor,
        now_ns=1_300,
        previous_root=prior,
        expected_review_sha256="f" * 64,
    )
    assert checked["valid"] is True
    assert checked["predecessor_state"] == "normal_terminal_verified"
    assert checked["activation_authorized"] is False


def test_abnormal_predecessor_stop_is_never_accepted_for_successor(tmp_path: Path) -> None:
    abnormal = tmp_path / "abnormal"
    abnormal.mkdir(mode=0o700)
    stop = {
        "schema": controller.SCHEMA,
        "status": "stopped",
        "reason": "provider_request_failed",
        "stopped_ns": 100,
    }
    stop_bytes = controller._canonical(stop) + b"\n"
    (abnormal / "STOP").write_bytes(stop_bytes)
    (abnormal / "STOP").chmod(0o600)
    terminal = {
        "schema": f"{controller.SCHEMA}-terminal-v1",
        "status": "lease_closed",
        "terminal_sha256": "1" * 64,
    }
    terminal_bytes = controller._canonical(terminal) + b"\n"
    (abnormal / "TERMINAL.json").write_bytes(terminal_bytes)
    (abnormal / "TERMINAL.json").chmod(0o600)

    plan = _plan()
    plan["predecessor"]["terminal_receipt_sha256"] = hashlib.sha256(terminal_bytes).hexdigest()
    plan["predecessor"]["stop_sha256"] = hashlib.sha256(stop_bytes).hexdigest()
    root = _init(tmp_path, plan=plan, name="candidate")
    result = _verify(root, previous_root=abnormal)
    assert result["valid"] is True
    assert result["predecessor_state"] == "abnormal_stop_rejected"
    assert result["activation_authorized"] is False


def test_gap_requires_new_anchor_and_crash_between_close_and_successor_does_not_reopen(
    tmp_path: Path,
) -> None:
    plan = _plan()
    plan["expires_ns"] = 9_000
    plan["buckets"].extend(
        [
            {"bucket_id": "anchor-2", "kind": "anchor", "start_ns": 4_100, "end_ns": 4_300},
            {"bucket_id": "frame-2", "kind": "frame", "start_ns": 4_500, "end_ns": 4_800},
        ]
    )
    root = _init(tmp_path, plan=plan)
    _reserve(root)
    _start(root)
    _capture(root)
    _result(root)
    _seal(root)
    controller.record_gap(root, bucket_id="frame-1", at_ns=4_001, reason="missed_tick")
    before = _verify(root)
    assert before["valid"] is True
    assert before["gap_count"] == 1
    controller.record_gap(root, bucket_id="anchor-2", at_ns=4_301, reason="missed_anchor")
    with pytest.raises(controller.ControllerError, match="gap_requires_future_anchor_only"):
        controller.reserve(
            root,
            request_id="bad-frame",
            bucket_id="frame-2",
            method="getSlot",
            request_sha256="e" * 64,
            requests=1,
            raw_bytes=1,
            stored_bytes=1,
            width=1,
            at_ns=4_500,
            physical_free_bytes=DISK_FLOOR,
        )

    prior = _init(tmp_path, name="closed-prior")
    _reserve(prior)
    _start(prior)
    _capture(prior)
    _result(prior)
    _seal(prior)
    controller.close_lease(prior, frontier_slot=501, at_ns=1_100)
    # Simulate process death after predecessor close and before successor creation.
    after_crash = _verify(prior)
    assert after_crash["valid"] is True
    assert after_crash["state"] == "closed"
    assert after_crash["recovery_recommendation"] == "closed_requires_new_reviewed_successor"
    assert after_crash["normal_terminal_verified"] is True
    assert after_crash["activation_authorized"] is False
