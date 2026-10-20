"""Bounded, complete independent-review packets for exact merged candidates."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class ReviewPacketError(ValueError):
    """A complete review packet cannot be represented within the explicit limits."""


MAX_DIFF_BYTES = 50_000
MAX_PACKET_BYTES = 256_000


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ReviewPacketError("invalid_review_packet_json") from None


def build_review_packet(
    task, candidate, policy, command_results, diff: str, merged_changed_paths
) -> dict:
    """Include the whole bounded diff and the exact identities needed to review it."""
    if type(diff) is not str:
        raise ReviewPacketError("invalid_review_diff")
    diff_bytes = diff.encode("utf-8")
    if len(diff_bytes) > MAX_DIFF_BYTES:
        raise ReviewPacketError("review_diff_exceeds_limit")
    if any(
        line.startswith("Binary files ") or line == "GIT binary patch" for line in diff.splitlines()
    ):
        raise ReviewPacketError("review_binary_diff_unsupported")
    required_candidate = (
        "submitted_sha",
        "base_sha",
        "integration_tip",
        "sha",
        "changed_paths",
    )
    if any(not hasattr(candidate, key) for key in required_candidate):
        raise ReviewPacketError("incomplete_candidate_identity")
    if (
        type(merged_changed_paths) is not tuple
        or not merged_changed_paths
        or any(type(path) is not str or not path or "\x00" in path for path in merged_changed_paths)
    ):
        raise ReviewPacketError("invalid_merged_candidate_paths")
    if len(set(merged_changed_paths)) != len(merged_changed_paths):
        raise ReviewPacketError("duplicate_merged_candidate_path")
    if type(candidate.changed_paths) is not tuple or any(
        type(path) is not str or not path or "\x00" in path for path in candidate.changed_paths
    ):
        raise ReviewPacketError("invalid_submitted_candidate_paths")
    scope = task.get("scope") if type(task) is dict else None
    if (
        type(scope) is not list
        or not scope
        or any(type(path) is not str or not path for path in scope)
    ):
        raise ReviewPacketError("invalid_review_task_scope")
    if any(
        not any(path == root or path.startswith(root.rstrip("/") + "/") for root in scope)
        for path in merged_changed_paths
    ):
        raise ReviewPacketError("merged_candidate_path_outside_scope")
    if type(command_results) is not list or not command_results:
        raise ReviewPacketError("missing_review_checks")
    if any(
        type(result) is not dict
        or not {"argv", "returncode", "stdout", "stderr", "timed_out", "output_truncated"}
        <= set(result)
        for result in command_results
    ):
        raise ReviewPacketError("incomplete_review_check_result")
    if type(task) is not dict or type(task.get("id")) is not str:
        raise ReviewPacketError("invalid_review_task")
    candidate_manifest = {
        "submitted_sha": candidate.submitted_sha,
        "base_sha": candidate.base_sha,
        "integration_tip": candidate.integration_tip,
        "merged_candidate_sha": candidate.sha,
        "submitted_changed_paths": list(candidate.changed_paths),
        "merged_changed_paths": list(merged_changed_paths),
    }
    packet = {
        "schema": "brain-independent-review-packet-v1",
        "task": {
            key: task.get(key)
            for key in (
                "id",
                "team",
                "title",
                "brief",
                "objective_id",
                "base_sha",
                "scope",
                "acceptance",
                "dependencies",
                "contracts",
                "capabilities",
                "resources",
            )
        },
        "candidate": candidate_manifest,
        "verifier_policy": {
            "policy_id": policy.policy_id,
            "policy_sha256": policy.digest,
            "commands": [list(command) for command in policy.commands],
            "env": [list(item) for item in policy.env],
        },
        "checks": command_results,
        "diff": diff,
        "diff_sha256": hashlib.sha256(diff_bytes).hexdigest(),
        "limits": {
            "diff_limit_bytes": MAX_DIFF_BYTES,
            "packet_limit_bytes": MAX_PACKET_BYTES,
            "diff_complete": True,
            "truncation": "reject_candidate_before_independent_review",
            "candidate_identity_bound": True,
        },
    }
    body_bytes = _canonical(packet)
    packet["packet_sha256"] = hashlib.sha256(body_bytes).hexdigest()
    wire_bytes = _canonical(packet)
    if len(wire_bytes) > MAX_PACKET_BYTES:
        raise ReviewPacketError("review_packet_exceeds_limit")
    return packet
