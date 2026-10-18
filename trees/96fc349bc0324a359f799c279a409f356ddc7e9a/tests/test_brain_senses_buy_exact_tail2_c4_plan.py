from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import pytest

from tools import brain_senses_buy_exact_tail2_c4_manifest as manifest
from tools.brain_senses_buy_exact_tail2_c4_plan import (
    SOURCE_PATHS,
    TEST_RECEIPT_CONTRACT,
    TEST_SOURCE_PATHS,
    _path_hash,
    _scheduled_denominator_is_complete,
    _selection_is_first_four_successes,
    _test_binding,
)


def test_selection_rule_holds_for_all_six_bucket_state_sequences_with_four_successes():
    states = ("successful", "missing", "failed_or_partial", "ambiguous_or_partial")
    for sequence in product(states, repeat=6):
        if sequence.count("successful") < 4:
            continue
        buckets = [
            "20261010T230000Z",
            "20261011T000000Z",
            "20261011T010000Z",
            "20261011T020000Z",
            "20261011T030000Z",
            "20261011T040000Z",
        ]
        denominator = [
            {"bucket": bucket, "state": state}
            for bucket, state in zip(buckets, sequence, strict=True)
        ]
        all_successes = [
            bucket for bucket, state in zip(buckets, sequence, strict=True) if state == "successful"
        ]
        expected = all_successes[:4]
        frames = [{"bucket": bucket} for bucket in expected]
        assert _selection_is_first_four_successes(frames, denominator)
        if len(all_successes) > 4:
            replaced = list(frames)
            replaced[-1] = {"bucket": all_successes[4]}
            assert not _selection_is_first_four_successes(replaced, denominator)


def test_selection_rule_rejects_duplicate_buckets_and_unknown_states():
    selected = [{"bucket": f"b{index}"} for index in range(4)]
    duplicate_denominator = [
        {"bucket": f"b{index}", "state": "successful"} for index in range(3)
    ] + [{"bucket": "b2", "state": "successful"}]
    assert not _selection_is_first_four_successes(selected, duplicate_denominator)
    invalid = [{"bucket": f"b{index}", "state": "unknown_is_not_missing"} for index in range(4)]
    assert not _selection_is_first_four_successes(selected, invalid)


def test_plan_denominator_must_cover_every_scheduled_bucket_through_fourth_success():
    expected = manifest._scheduled_buckets("20261011T020000Z")
    denominator = [{"bucket": bucket, "state": "successful"} for bucket in expected]
    assert _scheduled_denominator_is_complete(denominator, "20261011T020000Z")
    assert not _scheduled_denominator_is_complete(denominator[1:], "20261011T020000Z")
    assert not _scheduled_denominator_is_complete(
        denominator + [{"bucket": "20261011T030000Z", "state": "missing"}],
        "20261011T020000Z",
    )


def test_synthetic_receipt_binds_clean_junit_result_and_test_sources(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    campaign_root = tmp_path / "campaign"
    campaign_root.mkdir()
    result_path = tmp_path / "pytest.xml"
    result_path.write_text(
        '<testsuites><testsuite name="focused" tests="24" failures="0" '
        'errors="0" skipped="0"/></testsuites>'
    )
    result_counts = {"tests": 24, "failures": 0, "errors": 0, "skipped": 0}
    receipt_path = tmp_path / "synthetic-test-receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "contract": TEST_RECEIPT_CONTRACT,
                "status": "passed_synthetic_only",
                "rpc_requests": 0,
                "semantic_campaign_payloads_opened": 0,
                "test_exit_code": 0,
                "test_source_hashes": {
                    name: _path_hash(repository_root / name)["sha256"] for name in TEST_SOURCE_PATHS
                },
                "tested_source_hashes": {
                    name: _path_hash(repository_root / name)["sha256"] for name in SOURCE_PATHS
                },
                "test_result_artifact": {
                    "path": str(result_path),
                    "sha256": _path_hash(result_path)["sha256"],
                },
                "test_result_counts": result_counts,
            }
        )
    )
    binding = _test_binding(receipt_path, repository_root, campaign_root)
    assert binding["result_artifact"]["counts"] == result_counts


def test_synthetic_receipt_rejects_junit_count_mismatch(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    campaign_root = tmp_path / "campaign"
    campaign_root.mkdir()
    result_path = tmp_path / "pytest.xml"
    result_path.write_text(
        '<testsuites><testsuite name="focused" tests="1" failures="1" '
        'errors="0" skipped="0"/></testsuites>'
    )
    receipt_path = tmp_path / "synthetic-test-receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "contract": TEST_RECEIPT_CONTRACT,
                "status": "passed_synthetic_only",
                "rpc_requests": 0,
                "semantic_campaign_payloads_opened": 0,
                "test_exit_code": 0,
                "test_source_hashes": {
                    name: _path_hash(repository_root / name)["sha256"] for name in TEST_SOURCE_PATHS
                },
                "tested_source_hashes": {
                    name: _path_hash(repository_root / name)["sha256"] for name in SOURCE_PATHS
                },
                "test_result_artifact": {
                    "path": str(result_path),
                    "sha256": _path_hash(result_path)["sha256"],
                },
                "test_result_counts": {"tests": 1, "failures": 0, "errors": 0, "skipped": 0},
            }
        )
    )
    with pytest.raises(ValueError, match="plan_synthetic_test_result_not_clean"):
        _test_binding(receipt_path, repository_root, campaign_root)
