import io
import json
import sqlite3
import urllib.error

import pytest

from memecoin_experience.storage import digest, verify_files
from memecoin_population.acquisition import capture, validate


def plan():
    return dict(
        version="population-public-capture-v1",
        created_ns=1,
        expires_ns=10**15,
        spacing_s=3,
        max_requests=2,
        max_bytes=10 * 1024**2,
        metadata={},
        protocol={},
        cases=[dict(pool="synthetic", base_mint="B", quote_mint="Q")],
        jobs=[dict(pool="synthetic", start_s=0, end_s=300)],
    )


def test_one_request_immutable_completion_and_shared_fence(tmp_path):
    p = plan()
    p["created_ns"] = 10**12
    guards = []
    calls = []

    class Response(io.BytesIO):
        status = 200

    def opener(*args, **kwargs):
        calls.append(True)
        return Response(b"{}")

    def guard():
        guards.append(True)

    result = capture(tmp_path, p, guard=guard, opener=opener, clock=lambda: 10**12)
    assert result["requests"] == 1 and len(guards) == len(calls) == 1
    verify_files(tmp_path / "requests/0000000")
    assert capture(tmp_path, p, guard=guard, opener=opener) == result
    assert len(calls) == 1
    with pytest.raises(ValueError, match="immutable_conflict"):
        capture(tmp_path, dict(p, spacing_s=4), guard=guard, opener=opener)


def test_unknown_request_never_retried(tmp_path):
    p = plan()
    p["created_ns"] = 10**12

    def killed(*args, **kwargs):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        capture(tmp_path, p, guard=lambda: None, opener=killed, clock=lambda: 10**12)

    def forbidden(*args, **kwargs):
        raise AssertionError("must never issue another request")

    with pytest.raises(ValueError, match="unresolved_reservation"):
        capture(tmp_path, p, guard=lambda: None, opener=forbidden)
    assert json.loads((tmp_path / "STOP.json").read_text())["automatic_retry"] is False


def test_sealed_capture_recovers_without_network_and_404_retained(tmp_path):
    p = plan()
    p["created_ns"] = 10**12

    def gone(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://example.invalid", 404, "missing", {}, io.BytesIO(b"{}")
        )

    result = capture(tmp_path, p, guard=lambda: None, opener=gone, clock=lambda: 10**12)
    assert result["statuses"] == {"unsupported": 1} and result["population"] == 1
    (tmp_path / "complete.json").unlink()
    with sqlite3.connect(tmp_path / "requests.sqlite") as db:
        db.execute("update requests set status='reserved',result=null")

    def forbidden(*args, **kwargs):
        raise AssertionError("no retry")

    assert (
        capture(tmp_path, p, guard=lambda: None, opener=forbidden, clock=lambda: 10**12) == result
    )


def test_duplicate_and_protected_plan_before_network():
    p = plan()
    p["created_ns"] = 10**12
    p["jobs"] *= 2
    with pytest.raises(ValueError, match="duplicate_job"):
        validate(p)
    p = plan()
    p["created_ns"] = 10**12
    p["cases"][0]["base_mint"] = "So11111111111111111111111111111111111111112"
    with pytest.raises(ValueError, match="protected"):
        validate(p)
    assert digest(p)
