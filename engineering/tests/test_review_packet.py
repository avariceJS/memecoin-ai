from __future__ import annotations

import hashlib
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
    assert packet["limits"]["diff_complete"] is True
    assert len(packet["packet_sha256"]) == 64
    assert (
        len(__import__("json").dumps(packet, sort_keys=True, separators=(",", ":")).encode())
        <= MAX_PACKET_BYTES
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
