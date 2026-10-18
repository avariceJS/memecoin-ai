from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from memecoin_ai.domain import canonical
from memecoin_ai.sources import Capture
from tools import brain_senses_block_epoch_v3 as epoch_v3
from tools import brain_senses_finite_calendar_v3 as calendar
from tools import brain_senses_finite_calendar_verifier_v3 as verifier
from tools import brain_senses_operation_bundle_v2 as bridge

DAY = bridge.UTC_DAY_NS


def plan(now: int, buckets: list[dict] | None = None) -> dict:
    if buckets is None:
        buckets = [
            {"bucket_id": "a0", "kind": "anchor", "start_ns": now, "end_ns": now + 10},
            {"bucket_id": "f0", "kind": "frame", "start_ns": now + 10, "end_ns": now + 20},
            {"bucket_id": "a1", "kind": "anchor", "start_ns": now + 20, "end_ns": now + 30},
        ]
    return {
        "schema": bridge.SCHEMA,
        "calendar_contract": calendar.contract_binding(),
        "lease_id": "lease-a",
        "created_ns": now,
        "expires_ns": now + 2 * DAY,
        "source_sha256": {"contract": "a" * 64},
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
        "buckets": buckets,
        "predecessor": {"terminal_sha256": "b" * 64, "frontier_slot": 84, "gaps": []},
    }


@pytest.fixture
def host(monkeypatch):
    wall = [1_800_000_000_000_000_000]
    monkeypatch.setattr(bridge.time, "time_ns", lambda: wall[0])
    monkeypatch.setattr(
        bridge.shutil,
        "disk_usage",
        lambda _: shutil._ntuple_diskusage(200 * 1024**3, 100 * 1024**3, 100 * 1024**3),
    )
    return wall


def test_missed_frame_becomes_gap_and_only_later_anchor_is_eligible(tmp_path: Path, host):
    root = tmp_path / "lease"
    p = plan(host[0])
    calendar.initialize(root, p)
    result = calendar.drive_once(root, now_ns=host[0] + 25)
    assert result["status"] == "eligible"
    assert result["bucket_id"] == "a1"
    rows = calendar._rows(root)
    assert [(r["kind"], r.get("bucket_id")) for r in rows if r["kind"] == "gap"] == [
        ("gap", "a0"),
        ("gap", "f0"),
    ]
    # Anchor is a reset boundary; the later frame is never eligible until it seals.
    assert bridge._frontier_before(root, p, rows) == (84, True)


def test_missed_anchor_blocks_frames_until_later_anchor_without_stopping(tmp_path: Path, host):
    root = tmp_path / "lease"
    p = plan(
        host[0],
        [
            {"bucket_id": "a0", "kind": "anchor", "start_ns": host[0], "end_ns": host[0] + 10},
            {
                "bucket_id": "f0",
                "kind": "frame",
                "start_ns": host[0] + 10,
                "end_ns": host[0] + 20,
            },
            {
                "bucket_id": "a1",
                "kind": "anchor",
                "start_ns": host[0] + 20,
                "end_ns": host[0] + 30,
            },
            {
                "bucket_id": "f1",
                "kind": "frame",
                "start_ns": host[0] + 30,
                "end_ns": host[0] + 40,
            },
        ],
    )
    calendar.initialize(root, p)

    blocked = calendar.drive_once(root, now_ns=host[0] + 10)
    assert blocked == {
        "status": "waiting_for_anchor",
        "bucket_id": "f0",
        "eligible_at_ns": host[0] + 20,
        "reason": "gap_requires_future_anchor",
    }
    assert not any(row["kind"] == "stop" for row in calendar._rows(root))

    recovered = calendar.drive_once(root, now_ns=host[0] + 20)
    assert recovered == {"status": "eligible", "bucket_id": "a1", "kind": "anchor"}
    assert [
        (row["bucket_id"], row["reason"]) for row in calendar._rows(root) if row["kind"] == "gap"
    ] == [
        ("a0", "elapsed_without_start"),
        ("f0", "elapsed_without_start"),
    ]


def test_expiry_handoff_is_independently_verified_and_binds_conservative_usage(
    tmp_path: Path, host
):
    root = tmp_path / "lease"
    p = plan(host[0])
    calendar.initialize(root, p)
    result = calendar.drive_once(root, now_ns=p["expires_ns"])
    assert result["terminal_reason"] == "lease_expired"
    checked = verifier.verify_handoff(root)
    assert checked["valid"]
    assert checked["frontier_slot"] == 84
    assert checked["usage"] == {"requests": 0, "raw_bytes": 0, "stored_bytes": 0}
    assert checked["activation_authorized"] is False


def test_independent_verifier_rejects_unbacked_early_terminal_reason(tmp_path: Path, host):
    root = tmp_path / "lease"
    p = plan(host[0])
    calendar.initialize(root, p)
    handoff = calendar.drive_once(root, now_ns=p["expires_ns"])
    handoff["terminal_reason"] = "calendar_exhausted"
    handoff["terminal_at_ns"] = host[0]
    body = dict(handoff)
    body.pop("handoff_sha256")
    handoff["handoff_sha256"] = calendar._sha(calendar._canonical(body))
    (root / "terminal-handoff-v3.json").write_bytes(calendar._canonical(handoff) + b"\n")
    with pytest.raises(verifier.VerificationError, match="precedes_terminal_event"):
        verifier.verify_handoff(root)


def test_initialize_rejects_calendar_source_hash_mismatch(tmp_path: Path, host):
    p = plan(host[0])
    p["calendar_contract"]["driver_sha256"] = "0" * 64
    with pytest.raises(calendar.CalendarError, match="contract_binding_invalid"):
        calendar.initialize(tmp_path / "bad-lease", p)


def test_initialize_and_drive_reject_symlink_root(tmp_path: Path, host):
    target = tmp_path / "target"
    p = plan(host[0])
    calendar.initialize(target, p)
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(calendar.CalendarError, match="root_identity_invalid"):
        calendar.initialize(alias, plan(host[0]))
    with pytest.raises(calendar.CalendarError, match="root_identity_invalid"):
        calendar.drive_once(alias, now_ns=host[0])


def test_utc_clock_regression_stops_and_hands_off(tmp_path: Path, host):
    root = tmp_path / "lease"
    p = plan(host[0])
    calendar.initialize(root, p)
    # A forward decision can record elapsed buckets; a later backward wall clock is abnormal.
    calendar.drive_once(root, now_ns=host[0] + 25)
    out = calendar.drive_once(root, now_ns=host[0] + 24)
    assert out["terminal_reason"] == "utc_clock_regressed"
    assert verifier.verify_handoff(root)["valid"]


def test_disk_floor_drop_stops_before_any_bucket_operation(tmp_path: Path, host):
    root = tmp_path / "lease"
    calendar.initialize(root, plan(host[0]))
    out = calendar.drive_once(root, now_ns=host[0], physical_free_bytes=1)
    assert out["terminal_reason"] == "disk_floor_failed"
    rows = calendar._rows(root)
    assert not any(r["kind"] == "reservation" for r in rows)
    assert verifier.verify_handoff(root)["valid"]


def test_concurrent_or_existing_terminal_is_not_reentered(tmp_path: Path, host):
    root = tmp_path / "lease"
    p = plan(host[0])
    calendar.initialize(root, p)
    calendar.drive_once(root, now_ns=p["expires_ns"])
    with pytest.raises(calendar.CalendarError, match="already_terminal"):
        calendar.drive_once(root, now_ns=p["expires_ns"])


def test_active_second_driver_fails_closed(tmp_path: Path, host):
    root = tmp_path / "lease"
    calendar.initialize(root, plan(host[0]))
    with calendar._driver_lock(root):
        with pytest.raises(calendar.CalendarError, match="writer_already_active"):
            calendar.drive_once(root, now_ns=host[0])


def test_successor_requires_handoff_transfer_and_fresh_digest(tmp_path: Path, host):
    root = tmp_path / "lease"
    p = plan(host[0])
    calendar.initialize(root, p)
    handoff = calendar.drive_once(root, now_ns=p["expires_ns"])
    new_now = p["expires_ns"]
    successor = plan(
        new_now,
        [
            {
                "bucket_id": "next-anchor",
                "kind": "anchor",
                "start_ns": new_now,
                "end_ns": new_now + 10,
            }
        ],
    )
    successor["lease_id"] = "lease-b"
    successor["created_ns"] = new_now
    successor["expires_ns"] = new_now + DAY
    successor["predecessor"] = {
        "terminal_sha256": handoff["handoff_sha256"],
        "frontier_slot": handoff["frontier_slot"],
        "gaps": [gap["bucket_id"] for gap in handoff["gaps"]],
        "unknown_intervals": handoff["unknown_intervals"],
    }
    stale = dict(successor)
    stale["created_ns"] = handoff["terminal_at_ns"] - 1
    stale["buckets"] = [dict(successor["buckets"][0], start_ns=handoff["terminal_at_ns"] - 1)]
    with pytest.raises(calendar.CalendarError, match="successor.*review_digest"):
        calendar.initialize_successor(
            root, tmp_path / "stale-successor", stale, review_digest="c" * 64
        )
    calendar.initialize_successor(root, tmp_path / "successor", successor, review_digest="c" * 64)
    assert (tmp_path / "successor" / "plan-v2.json").exists()


class FakeRPC:
    def __init__(self, *, anchor: int, start: int | None = None, end: int | None = None):
        self.anchor, self.start, self.end = anchor, start, end
        self.calls: list[str] = []

    def fetch(self, method: str, params: list):
        self.calls.append(method)
        if method == "getSlot":
            value = self.anchor
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
        stamp = time.time_ns()
        return Capture(
            "helius-free",
            method,
            canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
            stamp,
            stamp,
            canonical({"jsonrpc": "2.0", "id": 1, "result": value}).encode(),
        )

    def close(self):
        pass


def test_driver_runs_anchor_then_34_request_frame_across_utc_day(tmp_path: Path, host):
    midnight = (host[0] // DAY + 1) * DAY
    start = midnight - 5_000_000_000
    host[0] = start
    p = plan(
        start,
        [
            {"bucket_id": "anchor", "kind": "anchor", "start_ns": start, "end_ns": midnight - 1},
            {
                "bucket_id": "frame",
                "kind": "frame",
                "start_ns": midnight,
                "end_ns": midnight + 60_000_000_000,
            },
        ],
    )
    p["expires_ns"] = midnight + DAY
    p["caps"].update(
        {
            "requests": 100,
            "raw_bytes": 12_000_000_000,
            "stored_bytes": 12_000_000_000,
            "daily_requests": 100,
            "daily_raw_bytes": 12_000_000_000,
            "daily_stored_bytes": 12_000_000_000,
        }
    )
    root = tmp_path / "lease"
    calendar.initialize(root, p)
    runner = epoch_v3.run_epoch

    def child(root: Path, bucket: dict, bucket_id: str):
        return epoch_v3.build_epoch_plan(
            epoch_id=bucket_id,
            bucket=bucket_id,
            schedule_id="caller-calendar",
            schedule_sha256="c" * 64,
            seed="fake",
            bootstrap=bucket["kind"] == "anchor",
            next_start=None if bucket["kind"] == "anchor" else 85,
            frame_width_cap=64,
            selection_count=16,
            request_budget=34,
            raw_byte_budget=256_000_000,
            stored_byte_budget=256_000_000,
            minimum_free_disk_bytes=50 * 1024**3,
            capture_storage_codec="raw",
        )

    out = calendar.drive_once(
        root,
        now_ns=start,
        runner=runner,
        clients={"helius-free": FakeRPC(anchor=84)},
        child_root=lambda parent, bucket_id: parent / "children" / bucket_id,
        child_plan=child,
    )
    assert out["status"] == "captured_bundle"
    host[0] = midnight
    frame_client = FakeRPC(anchor=102, start=85, end=103)
    out = calendar.drive_once(
        root,
        now_ns=midnight,
        runner=runner,
        clients={"helius-free": frame_client},
        child_root=lambda parent, bucket_id: parent / "children" / bucket_id,
        child_plan=child,
    )
    assert out["status"] == "captured_bundle"
    assert len(frame_client.calls) == 34
    reservations = [r for r in calendar._rows(root) if r["kind"] == "reservation"]
    assert reservations[0]["utc_day_index"] != reservations[1]["utc_day_index"]
    # Simulate restart after durable parent seal but before terminal publication.
    terminal = calendar.drive_once(root, now_ns=midnight + 1)
    assert terminal["terminal_reason"] == "calendar_exhausted"
    assert not any(r["kind"] == "stop" for r in calendar._rows(root))
    assert verifier.verify_handoff(root)["usage"]["requests"] == 35


@pytest.mark.parametrize(
    "crash_at",
    [
        "after_reservation",
        "after_start_before_dispatch",
        "after_capture_fsync",
        "before_parent_seal",
    ],
)
def test_crash_prefix_is_terminal_and_charged_without_replay(tmp_path: Path, host, crash_at):
    root = tmp_path / "lease"
    p = plan(
        host[0],
        [
            {
                "bucket_id": "anchor",
                "kind": "anchor",
                "start_ns": host[0],
                "end_ns": host[0] + 60_000_000_000,
            }
        ],
    )
    calendar.initialize(root, p)
    child_root = root / "children" / "anchor"
    cp = epoch_v3.build_epoch_plan(
        epoch_id="anchor",
        bucket="anchor",
        schedule_id="fixture",
        schedule_sha256="c" * 64,
        seed="fixture",
        bootstrap=True,
        next_start=None,
        frame_width_cap=64,
        selection_count=16,
        request_budget=34,
        raw_byte_budget=256_000_000,
        stored_byte_budget=256_000_000,
        minimum_free_disk_bytes=50 * 1024**3,
        capture_storage_codec="raw",
    )
    client = FakeRPC(anchor=84)
    bridge.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="crashed-anchor",
        child_root=child_root,
        child_plan=cp,
        runner=epoch_v3.run_epoch,
        clients={"helius-free": client},
        crash_at=crash_at,
    )
    if crash_at in {"after_reservation", "after_start_before_dispatch"}:
        assert not client.calls
    else:
        assert len(client.calls) == 1
    handoff = calendar.drive_once(root, now_ns=host[0])
    assert handoff["terminal_reason"] == (
        "stopped" if crash_at == "after_capture_fsync" else "abnormal_unsealed_prefix"
    )
    assert handoff["usage"]["requests"] >= len(client.calls)
    if crash_at != "after_capture_fsync":
        assert handoff["usage"] == {
            "requests": 1,
            "raw_bytes": 256_000_000,
            "stored_bytes": 256_000_000,
        }
    assert handoff["frontier_slot"] == 84
    assert handoff["unknown_intervals"]
    assert all(row["unknown_interval"] is True for row in handoff["unknown_intervals"])
    operation_ids = [row.get("operation_id") for row in handoff["unknown_intervals"]]
    assert len(operation_ids) == len(set(operation_ids))
    assert verifier.verify_handoff(root)["valid"]
    with pytest.raises(calendar.CalendarError, match="already_terminal"):
        calendar.drive_once(root, now_ns=host[0])


def test_unknown_intervals_survive_two_successor_handoffs(tmp_path: Path, host):
    root = tmp_path / "first"
    p = plan(
        host[0],
        [{"bucket_id": "anchor", "kind": "anchor", "start_ns": host[0], "end_ns": host[0] + DAY}],
    )
    calendar.initialize(root, p)
    child_root = root / "children/anchor"
    cp = epoch_v3.build_epoch_plan(
        epoch_id="anchor",
        bucket="anchor",
        schedule_id="fixture",
        schedule_sha256="c" * 64,
        seed="fixture",
        bootstrap=True,
        next_start=None,
        frame_width_cap=64,
        selection_count=16,
        request_budget=34,
        raw_byte_budget=256_000_000,
        stored_byte_budget=256_000_000,
        minimum_free_disk_bytes=50 * 1024**3,
    )
    bridge.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="crashed-anchor",
        child_root=child_root,
        child_plan=cp,
        runner=epoch_v3.run_epoch,
        clients={"helius-free": FakeRPC(anchor=84)},
        crash_at="after_start_before_dispatch",
    )
    first = calendar.drive_once(root, now_ns=host[0])
    assert first["unknown_intervals"]

    successor_root = tmp_path / "successor"
    created = first["terminal_at_ns"]
    successor = plan(
        created,
        [
            {
                "bucket_id": "next-anchor",
                "kind": "anchor",
                "start_ns": created,
                "end_ns": created + DAY,
            }
        ],
    )
    successor["lease_id"] = "lease-b"
    successor["expires_ns"] = created + DAY
    successor["predecessor"] = {
        "terminal_sha256": first["handoff_sha256"],
        "frontier_slot": first["frontier_slot"],
        "gaps": [gap["bucket_id"] for gap in first["gaps"]],
        "unknown_intervals": first["unknown_intervals"],
    }
    calendar.initialize_successor(root, successor_root, successor, review_digest="d" * 64)
    second = calendar.drive_once(successor_root, now_ns=successor["expires_ns"])
    assert verifier.verify_handoff(successor_root)["valid"]
    assert (
        second["unknown_intervals"][: len(first["unknown_intervals"])] == first["unknown_intervals"]
    )
