from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from engineering.git_gate import VerificationPolicy
from engineering.review_packet import (
    MAX_DIFF_BYTES,
    MAX_PACKET_BYTES,
    ReviewPacketError,
    build_review_packet,
)


def candidate():
    return SimpleNamespace(
        submitted_sha="a" * 40,
        base_sha="b" * 40,
        integration_tip="c" * 40,
        sha="d" * 40,
        changed_paths=("src/example.py", "tests/test_example.py"),
    )


def checks():
    return [
        {
            "argv": ("python", "-m", "pytest"),
            "returncode": 0,
            "stdout": "2 passed",
            "stderr": "",
            "timed_out": False,
            "output_truncated": False,
        }
    ]


def task():
    return {
        "id": "EXP-010",
        "team": "Experience",
        "title": "Synthetic review packet",
        "brief": "Verify complete exact-candidate evidence.",
        "objective_id": "offline-brain-engineering",
        "base_sha": "b" * 40,
        "scope": ["src/example.py", "tests/test_example.py"],
        "acceptance": ["schema-v1:" + "e" * 64],
        "dependencies": [],
        "contracts": {"schema": 1},
        "capabilities": ["offline_code"],
        "resources": {"max_attempts": 1},
    }


def policy():
    return VerificationPolicy((("python", "-m", "pytest"),), (), "synthetic-policy-v1")


def packet_digest(packet):
    body = {key: value for key, value in packet.items() if key != "packet_sha256"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_packet_contains_complete_candidate_identity_diff_and_policy():
    diff = "diff --git a/src/example.py b/src/example.py\n+value = 1\n"
    packet = build_review_packet(
        task(), candidate(), policy(), checks(), diff, ("src/example.py", "tests/test_example.py")
    )
    assert packet["candidate"] == {
        "submitted_sha": "a" * 40,
        "base_sha": "b" * 40,
        "integration_tip": "c" * 40,
        "merged_candidate_sha": "d" * 40,
        "submitted_changed_paths": ["src/example.py", "tests/test_example.py"],
        "merged_changed_paths": ["src/example.py", "tests/test_example.py"],
    }
    assert packet["diff"] == diff
    assert packet["diff_sha256"] == hashlib.sha256(diff.encode()).hexdigest()
    assert packet["verifier_policy"]["policy_sha256"] == policy().digest
    assert packet["task"]["acceptance"] == task()["acceptance"]
    assert packet["task"]["brief"] == task()["brief"]
    assert packet["task"]["department_nomination"] is None
    assert packet["limits"]["diff_complete"] is True
    assert len(packet["packet_sha256"]) == 64
    assert (
        len(__import__("json").dumps(packet, sort_keys=True, separators=(",", ":")).encode())
        <= MAX_PACKET_BYTES
    )


def test_packet_binds_department_nomination_into_review_digest():
    provenance = {
        "schema": "department-plan-nomination-v1",
        "team": "Experience",
        "objective_id": "offline-brain-engineering",
        "card_id": "PLAT-001",
        "plan_revision": 1,
        "plan_sha256": "f" * 64,
        "owner": "sol-experience",
        "owner_epoch": 2,
        "evidence_refs": ["test:department-plan:Experience"],
        "summary": "Advance the synthetic producer-consumer cycle",
    }
    admitted_task = {**task(), "department_nomination": provenance}
    packet = build_review_packet(
        admitted_task,
        candidate(),
        policy(),
        checks(),
        "diff content",
        ("src/example.py", "tests/test_example.py"),
    )
    assert packet["task"]["department_nomination"] == provenance
    assert packet["packet_sha256"] == packet_digest(packet)


def test_packet_detaches_task_and_check_inputs_after_construction():
    task_data = task()
    check_data = checks()
    packet = build_review_packet(
        task_data,
        candidate(),
        policy(),
        check_data,
        "diff content",
        ("src/example.py", "tests/test_example.py"),
    )
    packet_before = json.dumps(packet, sort_keys=True, separators=(",", ":"))

    task_data["scope"].clear()
    task_data["contracts"]["schema"] = 99
    task_data["resources"]["max_attempts"] = 100
    check_data.append({"stdout": "new evidence"})
    check_data[0]["stdout"] = "changed evidence"
    check_data[0]["argv"] = ("different",)

    assert json.dumps(packet, sort_keys=True, separators=(",", ":")) == packet_before
    assert packet["task"]["scope"] == ["src/example.py", "tests/test_example.py"]
    assert packet["task"]["contracts"] == {"schema": 1}
    assert packet["task"]["resources"] == {"max_attempts": 1}
    assert packet["checks"][0]["stdout"] == "2 passed"
    assert packet["checks"][0]["argv"] == ("python", "-m", "pytest")
    assert packet["packet_sha256"] == packet_digest(packet)


def test_validation_hash_and_return_use_one_input_snapshot(monkeypatch):
    from engineering import review_packet

    task_data = task()
    check_data = checks()
    original_snapshot = review_packet._snapshot_mutable_inputs

    def snapshot_then_mutate(original_task, original_checks):
        snapshot = original_snapshot(original_task, original_checks)
        original_task["scope"].clear()
        original_task["contracts"].clear()
        original_checks[0]["stdout"] = "changed during construction"
        original_checks[0]["timed_out"] = True
        return snapshot

    monkeypatch.setattr(review_packet, "_snapshot_mutable_inputs", snapshot_then_mutate)
    packet = build_review_packet(
        task_data,
        candidate(),
        policy(),
        check_data,
        "diff content",
        ("src/example.py", "tests/test_example.py"),
    )

    assert task_data["scope"] == []
    assert packet["task"]["scope"] == ["src/example.py", "tests/test_example.py"]
    assert packet["task"]["contracts"] == {"schema": 1}
    assert packet["checks"][0]["stdout"] == "2 passed"
    assert packet["checks"][0]["timed_out"] is False
    assert packet["packet_sha256"] == packet_digest(packet)


def test_snapshot_does_not_allow_custom_copy_hooks_to_launder_exact_types():
    deepcopy_called = False

    class TaskLaunder:
        def __deepcopy__(self, _memo):
            nonlocal deepcopy_called
            deepcopy_called = True
            return task()

    with pytest.raises(ReviewPacketError, match="invalid_review_task_scope"):
        build_review_packet(
            TaskLaunder(),
            candidate(),
            policy(),
            checks(),
            "short diff",
            ("src/example.py",),
        )
    assert not deepcopy_called

    class ScopeLaunder(list):
        def __deepcopy__(self, _memo):
            nonlocal deepcopy_called
            deepcopy_called = True
            return list(self)

    task_data = task()
    task_data["scope"] = ScopeLaunder(task_data["scope"])
    with pytest.raises(ReviewPacketError, match="invalid_review_task_scope"):
        build_review_packet(
            task_data,
            candidate(),
            policy(),
            checks(),
            "short diff",
            ("src/example.py",),
        )
    assert not deepcopy_called

    class CheckLaunder(dict):
        def __deepcopy__(self, _memo):
            nonlocal deepcopy_called
            deepcopy_called = True
            return dict(self)

    check_data = checks()
    check_data[0] = CheckLaunder(check_data[0])
    with pytest.raises(ReviewPacketError, match="incomplete_review_check_result"):
        build_review_packet(
            task(),
            candidate(),
            policy(),
            check_data,
            "short diff",
            ("src/example.py",),
        )
    assert not deepcopy_called


def test_snapshot_rejects_cycles_as_invalid_packet_json():
    task_data = task()
    cyclic_resource = {}
    cyclic_resource["self"] = cyclic_resource
    task_data["resources"] = cyclic_resource

    with pytest.raises(ReviewPacketError, match="invalid_review_packet_json"):
        build_review_packet(
            task_data,
            candidate(),
            policy(),
            checks(),
            "short diff",
            ("src/example.py",),
        )


def test_oversized_diff_is_explicitly_rejected_without_truncation():
    with pytest.raises(ReviewPacketError, match="review_diff_exceeds_limit"):
        build_review_packet(
            task(), candidate(), policy(), checks(), "x" * (MAX_DIFF_BYTES + 1), ("src/example.py",)
        )


def test_oversized_complete_packet_is_explicitly_rejected():
    results = checks()
    results[0]["stdout"] = "x" * MAX_PACKET_BYTES
    with pytest.raises(ReviewPacketError, match="review_packet_exceeds_limit"):
        build_review_packet(
            task(), candidate(), policy(), results, "short diff", ("src/example.py",)
        )


def test_missing_candidate_or_check_identity_fails_closed():
    incomplete = SimpleNamespace(submitted_sha="a" * 40)
    with pytest.raises(ReviewPacketError, match="incomplete_candidate_identity"):
        build_review_packet(
            task(), incomplete, policy(), checks(), "short diff", ("src/example.py",)
        )
    with pytest.raises(ReviewPacketError, match="missing_review_checks"):
        build_review_packet(task(), candidate(), policy(), [], "short diff", ("src/example.py",))


def test_binary_summary_is_blocked_instead_of_claimed_complete():
    with pytest.raises(ReviewPacketError, match="review_binary_diff_unsupported"):
        build_review_packet(
            task(),
            candidate(),
            policy(),
            checks(),
            "Binary files a/data.bin and b/data.bin differ\n",
            ("data.bin",),
        )


def test_merged_paths_must_match_task_scope():
    with pytest.raises(ReviewPacketError, match="merged_candidate_path_outside_scope"):
        build_review_packet(
            task(), candidate(), policy(), checks(), "diff content", ("private/secret.txt",)
        )
