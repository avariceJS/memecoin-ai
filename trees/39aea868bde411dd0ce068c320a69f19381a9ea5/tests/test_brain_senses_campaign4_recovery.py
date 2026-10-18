from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from memecoin_ai.domain import canonical
from memecoin_ai.sources import Capture
from tools import brain_senses_campaign4_recovery as c4


class FakeRPC:
    def __init__(self, *, slot: int = 455_216_100, fail: bool = False) -> None:
        self.slot = slot
        self.fail = fail
        self.calls: list[tuple[str, list[Any]]] = []

    def fetch(self, method: str, params: list[Any]) -> Capture:
        self.calls.append((method, params))
        if self.fail:
            raise TimeoutError("fixture transport timeout")
        request = canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        response = canonical({"jsonrpc": "2.0", "id": 1, "result": self.slot}).encode()
        now = 1_800_000_000_000_000_000
        return Capture("fixture", method, request, now, now + 10, response)

    def close(self) -> None:
        return


def _policy(root: Path, *, bucket: str = "20261010T120000Z") -> dict[str, Any]:
    root.mkdir(mode=0o700)
    value = {
        "schema_version": 1,
        "lease_id": c4.LEASE_VERSION,
        "campaign_id": c4.CAMPAIGN_ID,
        "root": str(root),
        "code_sha256": c4._code_hashes(),
        "predecessor": c4._c3_evidence(),
        "predecessor_lineage": {
            "c1_attempted_requests": 0,
            "c2_attempted_requests": 1,
            "c3_attempted_requests": 205,
        },
        "bootstrap": {
            "bucket": bucket,
            "request_limit": 1,
            "raw_byte_cap": c4.BOOTSTRAP_RAW_CAP,
            "stored_byte_cap": c4.BOOTSTRAP_STORED_CAP,
            "method": "getSlot",
            "params": [{"commitment": "finalized"}],
            "same_bucket_replay": False,
        },
        "bounds": {
            "lease_expires_ns": 1_900_000_000_000_000_000,
            "future_hourly_frame_attempts": c4.MAX_ATTEMPTS,
            "total_requests": c4.MAX_REQUESTS,
            "raw_bytes": c4.CAMPAIGN_RAW_BYTES,
            "stored_bytes": c4.CAMPAIGN_STORED_BYTES,
            "requests_per_frame": c4.REQUESTS_PER_FRAME,
        },
    }
    (root / "lease-v1.json").write_text(canonical(value) + "\n")
    return value


def test_prepare_records_only_a_fresh_future_bucket_and_c3_terminal_lineage(monkeypatch, tmp_path):
    root = tmp_path / "c4"
    monkeypatch.setattr(
        c4.shutil, "disk_usage", lambda _path: SimpleNamespace(free=100_000_000_000)
    )
    result = c4.prepare(now=datetime(2026, 10, 10, 11, 16, tzinfo=UTC), root=root)
    policy = json.loads((root / "lease-v1.json").read_text())

    assert result["network_requests"] == 0
    assert policy["bootstrap"]["bucket"] == "20261010T120000Z"
    assert policy["bootstrap"]["bucket"] != "20261010T110000Z"
    assert policy["predecessor_lineage"] == {
        "c1_attempted_requests": 0,
        "c2_attempted_requests": 1,
        "c3_attempted_requests": 205,
    }
    assert policy["bounds"]["total_requests"] == 817
    assert policy["bounds"]["future_hourly_frame_attempts"] == 24


def test_bootstrap_is_one_anchor_only_and_seals_the_uncovered_interval(monkeypatch, tmp_path):
    root = tmp_path / "c4"
    policy = _policy(root)
    monkeypatch.setattr(
        c4.shutil, "disk_usage", lambda _path: SimpleNamespace(free=100_000_000_000)
    )
    rpc = FakeRPC(slot=455_216_200)

    result = c4._bootstrap(
        root, policy, now=datetime(2026, 10, 10, 12, 5, tzinfo=UTC), clients={"helius-free": rpc}
    )

    assert result["status"] == "bootstrap_complete"
    assert rpc.calls == [("getSlot", [{"commitment": "finalized"}])]
    receipt = json.loads((root / "bootstrap-receipt.json").read_text())
    lineage = json.loads((root / "predecessor-lineage.json").read_text())
    assert receipt["request_identity"] == {
        "campaign_id": c4.CAMPAIGN_ID,
        "utc_bucket": "20261010T120000Z",
        "phase": "bootstrap_anchor",
        "sequence": 1,
    }
    assert receipt["uncovered_gap"]["start"] == 455_216_025
    assert receipt["uncovered_gap"]["end_exclusive"] == 455_216_201
    assert lineage["last_observed_anchor_slot"] == 455_216_200
    frontier = json.loads((root / "frontier-v3.json").read_text())
    assert frontier["observed_frontier"] == 455_216_201
    assert not (root / "bootstrap" / "20261010T120000Z" / "frame.json").exists()


def test_bootstrap_reservation_ambiguity_stops_without_replay(monkeypatch, tmp_path):
    root = tmp_path / "c4"
    policy = _policy(root)
    (root / "bootstrap-reservation.json").write_text('{"status":"reserved_before_io"}\n')
    rpc = FakeRPC()
    monkeypatch.setattr(
        c4.shutil, "disk_usage", lambda _path: SimpleNamespace(free=100_000_000_000)
    )

    result = c4._bootstrap(
        root, policy, now=datetime(2026, 10, 10, 12, 5, tzinfo=UTC), clients={"helius-free": rpc}
    )

    assert result["status"] == "campaign_stopped"
    assert result["reason"] == "bootstrap_reservation_ambiguous_no_replay"
    assert rpc.calls == []


def test_failed_bootstrap_is_terminal_and_request_identity_is_not_reissued(monkeypatch, tmp_path):
    root = tmp_path / "c4"
    policy = _policy(root)
    monkeypatch.setattr(
        c4.shutil, "disk_usage", lambda _path: SimpleNamespace(free=100_000_000_000)
    )
    rpc = FakeRPC(fail=True)

    result = c4._bootstrap(
        root, policy, now=datetime(2026, 10, 10, 12, 5, tzinfo=UTC), clients={"helius-free": rpc}
    )
    again = c4._bootstrap(
        root, policy, now=datetime(2026, 10, 10, 12, 6, tzinfo=UTC), clients={"helius-free": rpc}
    )

    assert result["status"] == "campaign_stopped"
    assert again["reason"] == "bootstrap_anchor_unknown_or_regressed"
    assert len(rpc.calls) == 1
    request = json.loads(
        (root / "bootstrap" / "20261010T120000Z" / "requests/000000000001.json").read_text()
    )
    assert request["method"] == "getSlot"
    assert request["params"] == [{"commitment": "finalized"}]


def test_missed_frame_bucket_stops_without_anchor_or_backfill(monkeypatch, tmp_path):
    root = tmp_path / "c4"
    policy = _policy(root)
    monkeypatch.setattr(
        c4.shutil, "disk_usage", lambda _path: SimpleNamespace(free=100_000_000_000)
    )
    bootstrap_rpc = FakeRPC(slot=455_216_200)
    c4._bootstrap(
        root,
        policy,
        now=datetime(2026, 10, 10, 12, 5, tzinfo=UTC),
        clients={"helius-free": bootstrap_rpc},
    )
    frame_rpc = FakeRPC()
    bootout_calls = []
    monkeypatch.setattr(c4, "_bootout", lambda label, runner: bootout_calls.append(label) or True)

    result = c4.tick(
        root,
        now=datetime(2026, 10, 10, 14, 5, tzinfo=UTC),
        clients={"helius-free": frame_rpc},
    )

    assert result["reason"] == "missed_scheduled_bucket_no_backfill"
    assert frame_rpc.calls == []
    assert bootout_calls == [c4.LABEL]


def test_request_envelope_for_one_bootstrap_and_all_frame_attempts_is_817():
    totals = [1 + n * c4.REQUESTS_PER_FRAME for n in range(c4.MAX_ATTEMPTS + 1)]
    assert totals[0] == 1
    assert totals[-1] == c4.MAX_REQUESTS == 817
    assert all(left < right for left, right in zip(totals, totals[1:], strict=False))
    assert all(total <= c4.MAX_REQUESTS for total in totals)


def test_launch_agent_is_calendar_only_and_has_no_secret_arguments(tmp_path):
    plist = c4._plist(tmp_path / "c4")

    assert plist["RunAtLoad"] is False
    assert plist["KeepAlive"] is False
    assert plist["StartCalendarInterval"] == {"Minute": 5}
    assert all("API" not in arg and "key" not in arg.lower() for arg in plist["ProgramArguments"])
    assert "--root" in plist["ProgramArguments"]
