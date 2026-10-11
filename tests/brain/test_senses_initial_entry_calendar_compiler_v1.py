from __future__ import annotations

import copy
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from memecoin_ai.domain import canonical
from memecoin_ai.sources import Capture
from tools import brain_senses_block_epoch_v3 as epoch_v3
from tools import brain_senses_finite_calendar_v3 as calendar
from tools import brain_senses_finite_calendar_verifier_v3 as handoff_verifier
from tools import brain_senses_initial_entry_calendar_compiler_v1 as compiler
from tools import brain_senses_initial_entry_calendar_verifier_v1 as verifier
from tools import brain_senses_operation_bundle_v2 as bridge

REPO_ROOT = Path(__file__).resolve().parents[2]
CERTIFICATE = REPO_ROOT / "docs/evidence/senses-initial-entry-discontinuity-v1-c4-2026-10-11.json"
NOW = 1_791_681_500_689_180_001
ANCHOR_SLOT = 455_430_170


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


@pytest.fixture(scope="module")
def certificate_data():
    raw = CERTIFICATE.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def inputs(certificate: dict[str, Any] | None = None) -> dict[str, Any]:
    certificate = certificate or json.loads(CERTIFICATE.read_text())
    return {
        "lease_id": "senses-initial-entry-candidate-20261011-a",
        "created_ns": NOW,
        "expires_ns": NOW + 120_000_000_000,
        "clock_source_id": "test-trusted-clock-01",
        "clock_observed_ns": NOW,
        "buckets": [
            {
                "bucket_id": "anchor-20261011T0000Z",
                "kind": "anchor",
                "start_ns": NOW + 1,
                "end_ns": NOW + 10_000_000_000,
            },
            {
                "bucket_id": "frame-20261011T0000Z",
                "kind": "frame",
                "start_ns": NOW + 10_000_000_000,
                "end_ns": NOW + 60_000_000_000,
            },
        ],
        "caps": {
            "requests": 100,
            "raw_bytes": 2_000_000_000,
            "stored_bytes": 2_000_000_000,
            "physical_bytes": 50 * 1024**3,
            "width": 64,
            "daily_requests": 100,
            "daily_raw_bytes": 2_000_000_000,
            "daily_stored_bytes": 2_000_000_000,
        },
        "source_sha256": {"source-contract": "a" * 64},
    }


def make_candidate(certificate_data, candidate_inputs=None):
    certificate, file_sha = certificate_data
    plan = compiler.plan_from_verified_certificate(
        certificate, file_sha, candidate_inputs or inputs(certificate)
    )
    body = {
        "schema": compiler.COMPILER_SCHEMA,
        "status": "inert_candidate",
        "certificate_file_sha256": file_sha,
        "predecessor_digest_sha256": compiler.EXPECTED_PREDECESSOR_DIGEST_SHA256,
        "plan_sha256": _sha(plan),
        "plan": plan,
    }
    return {**body, "candidate_sha256": _sha(body)}


def resign(candidate):
    body = {key: value for key, value in candidate.items() if key != "candidate_sha256"}
    candidate["candidate_sha256"] = _sha(body)
    return candidate


def test_valid_candidate_is_independently_verified_and_inert(certificate_data):
    certificate, file_sha = certificate_data
    candidate_inputs = inputs(certificate)
    candidate = make_candidate(certificate_data, candidate_inputs)
    proof = verifier.verify_candidate_data(candidate, certificate, file_sha, candidate_inputs)
    assert proof["status"] == "verified_inert_candidate"
    assert proof["activation_authorized"] is False
    assert candidate["plan"]["predecessor_digest_kind"] == compiler.PREDECESSOR_KIND
    assert (
        candidate["plan"]["predecessor"]["terminal_sha256"]
        == compiler.EXPECTED_PREDECESSOR_DIGEST_SHA256
    )
    assert candidate["plan"]["predecessor"]["certificate_file_sha256"] == file_sha
    assert candidate["plan"]["predecessor"]["frontier_slot"] == 455430160
    assert candidate["plan"]["predecessor"]["gaps"] == [gap["id"] for gap in certificate["gaps"]]
    assert candidate["plan"]["predecessor"]["unknown_intervals"] == certificate["unknown_intervals"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["predecessor"].__setitem__("terminal_sha256", "0" * 64),
        lambda p: p["predecessor"].__setitem__("certificate_file_sha256", "0" * 64),
        lambda p: p["predecessor"].__setitem__("gaps", p["predecessor"]["gaps"][1:]),
        lambda p: p["predecessor"].__setitem__("gaps", list(reversed(p["predecessor"]["gaps"]))),
        lambda p: p["predecessor"]["unknown_intervals"][0].__setitem__("end_exclusive", 455430161),
        lambda p: p.__setitem__("lease_id", "helius-inventory-v3-20261010-4"),
        lambda p: p["buckets"].reverse(),
        lambda p: p["buckets"][0].__setitem__("start_ns", NOW),
        lambda p: p["buckets"][1].__setitem__("end_ns", p["expires_ns"] + 1),
        lambda p: p["source_sha256"].__setitem__("source-contract", "b" * 64),
        lambda p: p["caps"].__setitem__("requests", p["caps"]["requests"] + 1),
        lambda p: p.__setitem__("clock_source_id", "other-clock"),
        lambda p: p["calendar_contract"].__setitem__("driver_sha256", "0" * 64),
        lambda p: p["candidate_code_sha256"].__setitem__("compiler_sha256", "0" * 64),
        lambda p: p["candidate_code_sha256"].__setitem__("child_v3_sha256", "0" * 64),
        lambda p: p.__setitem__("predecessor_digest_kind", "normal_v3_handoff"),
        lambda p: p.__setitem__("activation_authorized", True),
    ],
)
def test_plan_mutations_fail_even_with_recomputed_wrapper_hash(certificate_data, mutate):
    certificate, file_sha = certificate_data
    candidate_inputs = inputs(certificate)
    candidate = make_candidate(certificate_data, candidate_inputs)
    mutate(candidate["plan"])
    candidate["plan_sha256"] = _sha(candidate["plan"])
    resign(candidate)
    with pytest.raises(ValueError):
        verifier.verify_candidate_data(candidate, certificate, file_sha, candidate_inputs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("lease_id", "c4"),
        ("clock_observed_ns", NOW - 1),
        ("source_sha256", {"source-contract": "z" * 64}),
    ],
)
def test_independent_pins_reject_candidate_for_other_inputs(certificate_data, field, value):
    certificate, file_sha = certificate_data
    candidate_inputs = inputs(certificate)
    candidate = make_candidate(certificate_data, candidate_inputs)
    changed = copy.deepcopy(candidate_inputs)
    changed[field] = value
    with pytest.raises(ValueError):
        verifier.verify_candidate_data(candidate, certificate, file_sha, changed)


@pytest.mark.parametrize("identity", ["lease_id", "bucket_id"])
def test_independent_verifier_mirrors_safe_id_contract(certificate_data, identity):
    certificate, file_sha = certificate_data
    candidate_inputs = inputs(certificate)
    if identity == "lease_id":
        candidate_inputs[identity] = "../outside"
    else:
        candidate_inputs["buckets"][0][identity] = "../outside"
    with pytest.raises(ValueError):
        verifier._expected_plan(certificate, file_sha, candidate_inputs)


def test_independent_verifier_rejects_mutated_pinned_certificate_and_file_sha(certificate_data):
    certificate, file_sha = certificate_data
    candidate_inputs = inputs(certificate)
    candidate = make_candidate(certificate_data, candidate_inputs)
    changed_certificate = copy.deepcopy(certificate)
    changed_certificate["stop"]["reason"] = "forged"
    changed_file_sha = "0" * 64
    with pytest.raises(ValueError):
        verifier.verify_candidate_data(candidate, changed_certificate, file_sha, candidate_inputs)
    with pytest.raises(ValueError):
        verifier.verify_candidate_data(candidate, certificate, changed_file_sha, candidate_inputs)


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["buckets"][0].__setitem__("kind", "frame"),
        lambda p: p["buckets"][0].__setitem__("start_ns", p["created_ns"]),
        lambda p: p["buckets"][1].__setitem__("end_ns", p["expires_ns"] + 1),
        lambda p: p["buckets"][1].__setitem__("bucket_id", p["buckets"][0]["bucket_id"]),
    ],
)
def test_compiler_rejects_invalid_schedule_before_candidate(certificate_data, change):
    certificate, file_sha = certificate_data
    candidate_inputs = inputs(certificate)
    change(candidate_inputs)
    with pytest.raises(ValueError, match="candidate_schedule_invalid"):
        compiler.plan_from_verified_certificate(certificate, file_sha, candidate_inputs)


def test_deterministic_schedule_time_boundary_matrix(certificate_data):
    certificate, file_sha = certificate_data
    watermark = certificate["terminal_observation_ns"]
    cases = [
        (watermark + 1, watermark + 1, watermark + 2, watermark + 3, True),
        (watermark + 1, watermark + 1, watermark + 1, watermark + 2, False),
        (watermark + 1, watermark + 1, watermark + 2, watermark + 2, False),
        (watermark + 2, watermark + 1, watermark + 3, watermark + 4, False),
    ]
    for offset, (clock, created, start, end, accepted) in enumerate(cases):
        candidate_inputs = inputs(certificate)
        candidate_inputs["clock_observed_ns"] = clock
        candidate_inputs["created_ns"] = created
        candidate_inputs["expires_ns"] = max(candidate_inputs["expires_ns"], end)
        candidate_inputs["buckets"] = [
            {
                "bucket_id": f"boundary-{offset}",
                "kind": "anchor",
                "start_ns": start,
                "end_ns": end,
            }
        ]
        try:
            compiler.plan_from_verified_certificate(certificate, file_sha, candidate_inputs)
        except ValueError:
            assert not accepted
        else:
            assert accepted


def test_candidate_file_compiler_and_independent_file_verifier(certificate_data, tmp_path):
    candidate_inputs = inputs(certificate_data[0])
    candidate = compiler.compile_candidate(CERTIFICATE, candidate_inputs)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate, sort_keys=True, indent=2) + "\n")
    result = verifier.verify_candidate_files(candidate_path, CERTIFICATE, candidate_inputs)
    assert result["status"] == "verified_inert_candidate"
    assert result["candidate_sha256"] == candidate["candidate_sha256"]
    assert result["activation_authorized"] is False


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


def test_candidate_initializes_tmp_lease_anchor_resets_gaps_then_frame_handoff(
    certificate_data, tmp_path, monkeypatch
):
    certificate, file_sha = certificate_data
    candidate_inputs = inputs(certificate)
    candidate = make_candidate(certificate_data, candidate_inputs)
    plan = candidate["plan"]
    clock = [plan["buckets"][0]["start_ns"]]
    monkeypatch.setattr(bridge.time, "time_ns", lambda: clock[0])
    monkeypatch.setattr(
        bridge.shutil,
        "disk_usage",
        lambda _: shutil._ntuple_diskusage(200 * 1024**3, 100 * 1024**3, 100 * 1024**3),
    )
    root = tmp_path / "synthetic-owner" / plan["lease_id"]
    calendar.initialize(root, plan)

    def child_plan(_root, bucket, _bucket_id):
        is_anchor = bucket["kind"] == "anchor"
        return epoch_v3.build_epoch_plan(
            epoch_id=bucket["bucket_id"],
            bucket=bucket["bucket_id"],
            schedule_id="synthetic-initial-entry-test",
            schedule_sha256="c" * 64,
            seed="synthetic",
            bootstrap=is_anchor,
            next_start=None if is_anchor else ANCHOR_SLOT + 1,
            frame_width_cap=64,
            selection_count=16,
            request_budget=34,
            raw_byte_budget=256_000_000,
            stored_byte_budget=256_000_000,
            minimum_free_disk_bytes=50 * 1024**3,
            capture_storage_codec="raw",
        )

    runner = epoch_v3.run_epoch
    anchor_client = FakeRPC(anchor=ANCHOR_SLOT)
    anchor_result = calendar.drive_once(
        root,
        now_ns=clock[0],
        runner=runner,
        clients={"helius-free": anchor_client},
        child_root=lambda parent, bucket_id: parent / "children" / bucket_id,
        child_plan=child_plan,
    )
    assert anchor_result["status"] == "captured_bundle"
    assert len(anchor_client.calls) == 1
    assert bridge._frontier_before(root, plan, calendar._rows(root)) == (ANCHOR_SLOT + 1, False)

    clock[0] = plan["buckets"][1]["start_ns"]
    frame_client = FakeRPC(anchor=ANCHOR_SLOT + 64, start=ANCHOR_SLOT + 1, end=ANCHOR_SLOT + 65)
    frame_result = calendar.drive_once(
        root,
        now_ns=clock[0],
        runner=runner,
        clients={"helius-free": frame_client},
        child_root=lambda parent, bucket_id: parent / "children" / bucket_id,
        child_plan=child_plan,
    )
    assert frame_result["status"] == "captured_bundle"
    assert len(frame_client.calls) == 34
    reservations = [row for row in calendar._rows(root) if row["kind"] == "reservation"]
    assert reservations[1]["frontier_before"] == ANCHOR_SLOT + 1
    clock[0] += 1
    terminal = calendar.drive_once(root, now_ns=clock[0])
    assert terminal["terminal_reason"] == "calendar_exhausted"
    handoff = handoff_verifier.verify_handoff(root)
    assert handoff["valid"]
    assert handoff["frontier_slot"] == ANCHOR_SLOT + 65
    assert handoff["gaps"] == [
        {"bucket_id": gap_id, "reason": "inherited_predecessor_gap"}
        for gap_id in plan["predecessor"]["gaps"]
    ]
    assert handoff["unknown_intervals"] == plan["predecessor"]["unknown_intervals"]
    assert (
        verifier.verify_candidate_data(candidate, certificate, file_sha, candidate_inputs)[
            "activation_authorized"
        ]
        is False
    )


@pytest.mark.parametrize(
    "crash_at",
    [
        "after_reservation",
        "after_start_before_dispatch",
        "after_capture_fsync",
        "before_parent_seal",
    ],
)
def test_candidate_crash_prefixes_never_replay_started_anchor(
    certificate_data, tmp_path, monkeypatch, crash_at
):
    certificate, _ = certificate_data
    plan = make_candidate(certificate_data, inputs(certificate))["plan"]
    clock = [plan["buckets"][0]["start_ns"]]
    monkeypatch.setattr(bridge.time, "time_ns", lambda: clock[0])
    monkeypatch.setattr(
        bridge.shutil,
        "disk_usage",
        lambda _: shutil._ntuple_diskusage(200 * 1024**3, 100 * 1024**3, 100 * 1024**3),
    )
    root = tmp_path / crash_at / plan["lease_id"]
    calendar.initialize(root, plan)
    child = epoch_v3.build_epoch_plan(
        epoch_id="crash-anchor",
        bucket="crash-anchor",
        schedule_id="synthetic-crash-test",
        schedule_sha256="d" * 64,
        seed="synthetic",
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
    client = FakeRPC(anchor=ANCHOR_SLOT)
    injected = bridge.reserve_and_run(
        root,
        bucket_id=plan["buckets"][0]["bucket_id"],
        operation_id=f"crash-{crash_at}",
        child_root=root / "children" / "crash-anchor",
        child_plan=child,
        runner=epoch_v3.run_epoch,
        clients={"helius-free": client},
        crash_at=crash_at,
    )
    assert injected["status"].startswith(("crash_injected", "stopped_unknown"))
    calls_after_crash = len(client.calls)
    first_recovery = calendar.drive_once(root, now_ns=clock[0])
    assert len(client.calls) == calls_after_crash
    reservations = [row for row in calendar._rows(root) if row["kind"] == "reservation"]
    assert len(reservations) == 1
    assert first_recovery["terminal_reason"] in {
        "stopped",
        "abnormal_unsealed_prefix",
    }
    with pytest.raises(calendar.CalendarError, match="calendar_already_terminal"):
        calendar.drive_once(root, now_ns=clock[0] + 1)
    assert len(client.calls) == calls_after_crash
    assert len([row for row in calendar._rows(root) if row["kind"] == "reservation"]) == 1


def test_candidate_rejects_finalized_anchor_below_inherited_frontier(
    certificate_data, tmp_path, monkeypatch
):
    certificate, _ = certificate_data
    plan = make_candidate(certificate_data, inputs(certificate))["plan"]
    clock = [plan["buckets"][0]["start_ns"]]
    monkeypatch.setattr(bridge.time, "time_ns", lambda: clock[0])
    monkeypatch.setattr(
        bridge.shutil,
        "disk_usage",
        lambda _: shutil._ntuple_diskusage(200 * 1024**3, 100 * 1024**3, 100 * 1024**3),
    )
    root = tmp_path / plan["lease_id"]
    calendar.initialize(root, plan)

    def child_plan(_root, bucket, _bucket_id):
        return epoch_v3.build_epoch_plan(
            epoch_id=bucket["bucket_id"],
            bucket=bucket["bucket_id"],
            schedule_id="synthetic-regression-test",
            schedule_sha256="e" * 64,
            seed="synthetic",
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

    with pytest.raises((bridge.EnvelopeError, calendar.CalendarError)):
        calendar.drive_once(
            root,
            now_ns=clock[0],
            runner=epoch_v3.run_epoch,
            clients={"helius-free": FakeRPC(anchor=455430158)},
            child_root=lambda parent, bucket_id: parent / "children" / bucket_id,
            child_plan=child_plan,
        )
