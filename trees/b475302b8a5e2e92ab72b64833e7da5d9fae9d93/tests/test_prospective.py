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
        endpoint_origin="https://example.invalid",
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


@pytest.mark.parametrize("name", ["first", "second"])
def test_real_clock_context_mismatch_remains_quarantined(name):
    from pathlib import Path

    from memecoin_ai.sources import Capture
    from memecoin_ai.tokens import decode_supported_checkpoint

    cap = Capture.load(Path(__file__).parent / "fixtures/clock-mismatch" / name)
    pool = json.loads(cap.request_json)["params"][0][0]
    with pytest.raises(ValidationError, match="Clock does not match checkpoint bank"):
        decode_supported_checkpoint(cap, pool)


def test_concurrent_resume_does_not_collect(tmp_path):
    import fcntl

    root = tmp_path / "campaign"
    t, client = setup(root)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValidationError, match="already collecting"):
            resume(root, t, client)
    assert t.calls == 0


def test_two_failed_segments_block_third_even_with_remaining_budget(tmp_path):
    t = FakeTime()
    root = tmp_path / "campaign"
    create_campaign(
        root,
        [BASE],
        source_alias="synthetic-campaign",
        endpoint_origin="https://example.invalid",
        segments=3,
        rounds=4,
        clock=t.clock,
    )
    calls = []

    def fail(*args):
        calls.append(1)
        raise TransportSourceError("timeout")

    client = ReadOnlyRPC(
        "https://example.invalid",
        "synthetic-campaign",
        timeout=15,
        max_bytes=1_000_000,
        transport=fail,
        clock=t.clock,
    )
    resume(root, t, client)
    t.sleep(31)
    resume(root, t, client)
    t.sleep(31)
    with pytest.raises(ValidationError, match="consecutive failed"):
        resume(root, t, client)
    assert len(calls) == 2


def test_resume_cannot_switch_endpoint_under_same_alias(tmp_path):
    root = tmp_path / "campaign"
    t, client = setup(root)
    wrong = ReadOnlyRPC(
        "https://another.invalid/?api-key=secret",
        client.source_alias,
        timeout=15,
        max_bytes=1_000_000,
        transport=t.transport,
        clock=t.clock,
    )
    with pytest.raises(ValidationError, match="endpoint origin"):
        resume(root, t, wrong)
    assert t.calls == 0 and list((root / "segments").iterdir()) == []


def test_origin_excludes_secrets_and_closed_campaign_cannot_resume(tmp_path):
    root = tmp_path / "campaign"
    t, client = setup(root)
    assert (
        ReadOnlyRPC("https://example.invalid/private-key?api-key=secret", "test").endpoint_origin
        == "https://example.invalid"
    )
    write(root / "closure.json", dict(reason="explicit termination"))
    with pytest.raises(ValidationError, match="explicitly closed"):
        resume(root, t, client)
    assert t.calls == 0


@pytest.mark.parametrize(
    "origin", ["https://example.invalid/?api-key=secret", "https://example.invalid/secret"]
)
def test_campaign_rejects_secret_bearing_origin(tmp_path, origin):
    with pytest.raises(ValidationError, match="origin only"):
        create_campaign(tmp_path / "campaign", [BASE], source_alias="test", endpoint_origin=origin)
    assert not (tmp_path / "campaign").exists()
