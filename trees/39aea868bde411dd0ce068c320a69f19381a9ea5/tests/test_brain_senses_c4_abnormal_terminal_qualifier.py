from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import brain_senses_c4_abnormal_terminal_qualifier as qualifier


def _buckets(start: datetime, count: int) -> list[str]:
    return [(start + timedelta(hours=index)).strftime("%Y%m%dT%H%M%SZ") for index in range(count)]


def test_consecutive_bucket_predicate_over_generated_hourly_sequences() -> None:
    for day in range(1, 29):
        start = datetime(2026, 10, day, 0, 0, tzinfo=UTC)
        for count in range(1, 25):
            buckets = _buckets(start, count)
            assert qualifier._hourly_buckets_are_consecutive(buckets)
            if count > 2:
                for omitted in range(1, count - 1):
                    assert not qualifier._hourly_buckets_are_consecutive(
                        buckets[:omitted] + buckets[omitted + 1 :]
                    )


def test_uncovered_continuation_never_invents_an_end_slot() -> None:
    frontiers = [0, 1, 2, 455430160, 2**32, 2**63, 2**64 - 1]
    for frontier in frontiers:
        gap = qualifier._uncovered_continuation(frontier, qualifier.STOP_REASON)
        assert gap["start_frontier_slot"] == frontier
        assert gap["end_exclusive"] is None
        assert gap["end_known"] is False
    with pytest.raises(ValueError, match="abnormal_continuation_input_invalid"):
        qualifier._uncovered_continuation(2**64, qualifier.STOP_REASON)


def test_all_in_byte_and_request_accounting_is_additive_over_generated_values() -> None:
    for frame_count in range(0, 12):
        raw = [index * 17 + frame_count for index in range(frame_count)]
        stored = [index * 7 + frame_count for index in range(frame_count)]
        requests = frame_count * 34
        totals = qualifier._all_in_totals(raw, stored, requests, 43, 56, 1)
        assert totals["frame_raw_bytes"] == sum(raw)
        assert totals["all_in_raw_bytes"] == sum(raw) + 43
        assert totals["frame_stored_bytes"] == sum(stored)
        assert totals["all_in_stored_bytes"] == sum(stored) + 56
        assert totals["all_in_requests"] == requests + 1


@pytest.mark.parametrize(
    "raw,stored,requests",
    [(-1, 0, 0), (0, -1, 0), (0, 0, -1), (True, 0, 0), (0.5, 1, 1)],
)
def test_accounting_rejects_wrong_dimensions_and_negative_values(raw, stored, requests) -> None:
    with pytest.raises(ValueError, match="abnormal_accounting_dimension_invalid"):
        qualifier._all_in_totals([raw], [stored], requests, 43, 56, 1)


def test_accounting_rejects_unpaired_frame_byte_vectors() -> None:
    with pytest.raises(ValueError, match="abnormal_frame_byte_vector_length_mismatch"):
        qualifier._all_in_totals([1], [], 34, 43, 56, 1)


def test_log_bounds_convert_stop_utc_to_host_local_timezone() -> None:
    stop_ns = 1_791_677_100_418_875_000
    local, covered_ns, zone, offset_seconds = qualifier._local_log_bound(stop_ns)
    assert local == "2026-10-11 03:05:00"
    assert zone == "Europe/Moscow"
    assert offset_seconds == 10_800
    assert covered_ns <= stop_ns < covered_ns + 1_000_000_000


def test_campaign_log_query_uses_local_time_arguments(monkeypatch) -> None:
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        return SimpleNamespace(returncode=0, stdout=b"[]", stderr=b"")

    monkeypatch.setattr(qualifier.subprocess, "run", fake_run)
    qualifier._query_campaign_logs(
        1_791_677_100_418_875_000,
        1_791_677_291_890_361_000,
    )
    args = seen["args"]
    assert args[args.index("--start") + 1] == "2026-10-11 03:05:00"
    assert args[args.index("--end") + 1] == "2026-10-11 03:08:11"
    assert args[0] == "/usr/bin/log"
    assert "getBlock" not in str(args)


def test_campaign_log_query_allows_only_known_os_scheduler_records(monkeypatch) -> None:
    events = [
        {
            "timestamp": "2026-10-11 03:05:00.427125+0300",
            "processImagePath": "/sbin/launchd",
            "subsystem": "gui/501 [100023]",
            "eventMessage": f"service state {qualifier.normal.LABEL}",
            "category": "",
        }
    ]
    monkeypatch.setattr(
        qualifier.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(events).encode(), stderr=b""
        ),
    )
    raw, processes, count = qualifier._query_campaign_logs(
        1_791_677_100_418_875_000, 1_791_677_200_000_000_000
    )
    assert json.loads(raw) == events
    assert processes == ["/sbin/launchd"]
    assert count == 1


def test_campaign_log_query_fails_closed_for_unclassified_label_match(monkeypatch) -> None:
    events = [
        {
            "timestamp": "2026-10-11 03:05:00.427125+0300",
            "processImagePath": "/usr/bin/python3",
            "subsystem": "com.example.process",
            "eventMessage": f"{qualifier.normal.LABEL} tick",
            "category": "main",
        }
    ]
    monkeypatch.setattr(
        qualifier.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(events).encode(), stderr=b""
        ),
    )
    with pytest.raises(ValueError, match="abnormal_terminal_unclassified_log_process"):
        qualifier._query_campaign_logs(1_791_677_100_418_875_000, 1_791_677_200_000_000_000)


def test_external_receipt_writer_is_create_only_and_private(tmp_path: Path) -> None:
    path = tmp_path / "external" / "receipt.json"
    qualifier._write_create_only(path, b"first\n")
    assert path.read_bytes() == b"first\n"
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="abnormal_terminal_output_already_exists"):
        qualifier._write_create_only(path, b"second\n")
    assert path.read_bytes() == b"first\n"


def test_metadata_inventory_stats_but_never_reads_capture_bodies(
    tmp_path: Path, monkeypatch
) -> None:
    capture = tmp_path / "epochs" / "one" / "captures" / "000000000001"
    capture.mkdir(parents=True)
    body = capture / "response.json.zst"
    body.write_bytes(b"opaque response body")
    receipt = capture / "receipt.json"
    receipt.write_text('{"response_bytes": 21}\n')
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == body:
            raise AssertionError("opaque capture body read attempted")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    inventory = qualifier._metadata_inventory(tmp_path)
    assert inventory["file_count"] == 2
    assert inventory["logical_bytes"] == body.stat().st_size + receipt.stat().st_size
