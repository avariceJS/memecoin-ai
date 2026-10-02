"""Synthetic clocks/transport only; never network tests."""

import json

import pytest
from test_causal import BASE, FakeTime

from memecoin_ai.domain import ValidationError, digest
from memecoin_ai.prospective import create_campaign, resume_campaign, verify_campaign, write
from memecoin_ai.sources import HTTPSourceError, ReadOnlyRPC, TransportSourceError


def setup(root, transport=None):
    t = FakeTime()
    create_campaign(
        root,
        [BASE],
        source_alias="synthetic-campaign",
        segments=2,
        rounds=4,
        interval_seconds=10,
        clock=t.clock,
    )
    client = ReadOnlyRPC(
        "https://example.invalid",
        "synthetic-campaign",
        timeout=15,
        max_bytes=1_000_000,
        transport=transport or t.transport,
        clock=t.clock,
    )
    return t, client


def resume(root, t, client):
    return resume_campaign(root, client, clock=t.clock, monotonic=t.mono, sleep=t.sleep)


def test_resume_is_new_segment_and_budget_exhaustion_is_noop(tmp_path):
    root = tmp_path / "campaign"
    t, client = setup(root)
    first = resume(root, t, client)
    before = (root / "segments/000/seal.json").read_bytes()
    second = resume(root, t, client)
    assert first["successful_snapshots"] == 4 and second["successful_snapshots"] == 8
    assert second["complete"] and verify_campaign(root) == second
    assert resume(root, t, client) == second and t.calls == 8
    assert (root / "segments/000/seal.json").read_bytes() == before
    for segment in ("000", "001"):
        rows = [
            json.loads(s)
            for s in (root / f"segments/{segment}/corpus/features.jsonl").read_text().splitlines()
        ]
        assert rows[0]["reasons"] == ["history_warmup"]


def test_timeout_cooldown_and_consecutive_failure_budget(tmp_path):
    root = tmp_path / "campaign"

    def fail(*args):
        raise TransportSourceError("timeout")

    t, client = setup(root, fail)
    assert resume(root, t, client)["segments"][0]["report"]["requests"] == 1
    with pytest.raises(ValidationError, match="cooldown"):
        resume(root, t, client)
    t.sleep(31)
    state = resume(root, t, client)
    assert not state["complete"] and len(state["segments"]) == 2
    assert resume(root, t, client) == state


def test_http_error_is_not_retried_even_after_cooldown(tmp_path):
    def fail(*args):
        raise HTTPSourceError(429)

    root = tmp_path / "campaign"
    t, client = setup(root, fail)
    resume(root, t, client)
    t.sleep(31)
    with pytest.raises(ValidationError, match="non-transient"):
        resume(root, t, client)


def test_crash_bytes_preserved_and_budget_reserved(tmp_path):
    root = tmp_path / "campaign"
    t, client = setup(root)
    abandoned = root / "segments/000"
    abandoned.mkdir()
    write(
        abandoned / "reservation.json",
        dict(index=0, started_ns=t.clock(), plan_sha256=digest((root / "plan.json").read_bytes())),
    )
    (abandoned / "partial.raw").write_bytes(b"interrupted bytes")
    with pytest.raises(ValidationError, match="cooldown"):
        resume(root, t, client)
    assert (abandoned / "partial.raw").read_bytes() == b"interrupted bytes"
    t.sleep(31)
    state = resume(root, t, client)
    assert state["reserved_requests"] == 8 and t.calls == 4
    assert state["segments"][0]["interrupted"] and not state["complete"]


def test_plan_tampering_stops_before_network(tmp_path):
    root = tmp_path / "campaign"
    t, client = setup(root)
    (root / "plan.json").write_text("{}")
    with pytest.raises(ValidationError, match="checksum"):
        resume(root, t, client)
    assert t.calls == 0
