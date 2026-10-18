import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.provider_audit import compare_captures, json_difference_summary
from memecoin_ai.sources import Capture

FIXTURE = Path(__file__).parent / "fixtures/helius_truncated"


def test_real_truncated_response_never_proves_complete_agreement():
    manifest = json.loads((FIXTURE / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        assert digest((FIXTURE / name).read_bytes()) == expected
    a = Capture.load(FIXTURE)
    b = replace(a, source_alias="other")
    result = compare_captures(a, b)
    assert result["status"] == "unavailable"
    assert result["truncated_log_transaction_indices"] == {"left": [0], "right": [0]}
    assert result["semantic_left_sha256"] == result["semantic_right_sha256"]
    # Reproduction retains the old contract; it does not upgrade archived conclusions.
    assert compare_captures(a, b, comparison_schema=2)["status"] == "match"


def test_full_block_reports_mismatch_and_truncated_indices_without_erasing_logs():
    tx = Capture.load(FIXTURE).result()
    params = [tx["slot"], dict(commitment="finalized", transactionDetails="full")]
    body = dict(
        blockhash="synthetic",
        previousBlockhash="parent",
        parentSlot=1,
        blockTime=tx["blockTime"],
        transactions=[tx],
    )
    a = Capture(
        "left",
        "getBlock",
        canonical(dict(id=1, jsonrpc="2.0", method="getBlock", params=params)),
        1,
        2,
        canonical(dict(id=1, jsonrpc="2.0", result=body)).encode(),
    )
    payload = json.loads(a.response)
    # Explicitly synthetic perturbation of the real trace, not a reconstructed missing tail.
    payload["result"]["transactions"][0]["meta"]["logMessages"][-1] = "synthetic complete tail"
    b = replace(a, source_alias="right", response=canonical(payload).encode())
    result = compare_captures(a, b)
    assert result["status"] == "mismatch"
    assert result["truncated_log_transaction_indices"] == {"left": [0], "right": []}
    assert result["differences"]["total_sites"] == 1
    assert result["differences"]["samples"][0]["path"].startswith(
        "/transactions/0/meta/logMessages/"
    )


def test_user_program_log_containing_marker_is_not_runtime_truncation():
    a = Capture.load(FIXTURE)
    payload = json.loads(a.response)
    payload["result"]["meta"]["logMessages"] = ["Program log: Log truncated"]
    a = replace(a, response=canonical(payload).encode())
    report = compare_captures(a, replace(a, source_alias="other"))
    assert report["status"] == "match"
    assert not report["log_truncation_observed"]


def test_diagnostic_counts_all_sites_and_bounds_payload_without_normalizing_values():
    a = {"~logs/key": list(range(100)), "missing": None, "number": 1}
    b = {"~logs/key": [-1] * 99, "number": 1.0}
    r = json_difference_summary(a, b, limit=2)
    assert r["total_sites"] == 102  # missing key, number type, length, 99 overlapping values
    assert len(r["samples"]) == 2 and not r["samples_complete"]
    full = json_difference_summary(a, b, limit=128)
    assert any(s["path"] == "/~0logs~1key" and s["kind"] == "array_length" for s in full["samples"])
    assert json_difference_summary({"a": 1, "b": 2}, {"b": 2, "a": 1})["total_sites"] == 0
    with pytest.raises(ValidationError):
        json_difference_summary(a, b, limit=129)


def test_offline_cli_reports_nonmatch_and_never_overwrites(tmp_path):
    a = Capture.load(FIXTURE)
    a.save(tmp_path / "left")
    replace(a, source_alias="other").save(tmp_path / "right")
    cmd = [
        sys.executable,
        "-m",
        "memecoin_ai",
        "compare-rpc-captures",
        str(tmp_path / "left"),
        str(tmp_path / "right"),
        "--out",
        str(tmp_path / "report.json"),
    ]
    run = subprocess.run(cmd, text=True, capture_output=True)
    assert run.returncode == 1, run.stderr
    assert json.loads(run.stdout)["status"] == "unavailable"
    before = (tmp_path / "report.json").read_bytes()
    assert subprocess.run(cmd, capture_output=True).returncode == 2
    assert (tmp_path / "report.json").read_bytes() == before
