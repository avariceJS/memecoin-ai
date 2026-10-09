import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools import brain_senses_block_epoch_schedule as scheduler
from tools.brain_senses_block_epoch import stratified_slot_windows
from tools.brain_senses_block_epoch_schedule import (
    campaign_usage,
    select_parent,
    utc_bucket,
)


def test_utc_bucket_uses_utc_not_host_local_clock() -> None:
    instant = datetime(2026, 10, 9, 14, 23, tzinfo=UTC) + timedelta(hours=3)

    assert utc_bucket(instant, 6) == "20261009T120000Z"


def test_utc_bucket_requires_aware_time_and_supported_interval() -> None:
    with pytest.raises(ValueError, match="timezone_aware_datetime_required"):
        utc_bucket(datetime(2026, 10, 9, 12, 0))
    with pytest.raises(ValueError, match="unsupported_interval_hours"):
        utc_bucket(datetime(2026, 10, 9, 12, 0, tzinfo=UTC), 5)


def test_schedule_links_latest_scheduled_epoch_or_bootstrap_epoch() -> None:
    initial = Path("initial")
    assert select_parent([], initial) == initial
    assert select_parent([Path("later"), Path("earlier")], initial) == Path("later")


def test_campaign_usage_accounts_nested_requests_and_selected_ranges(tmp_path: Path) -> None:
    epoch = tmp_path / "20261009T120000Z"
    (epoch / "paired-block-capture" / "requests").mkdir(parents=True)
    (epoch / "paired-block-capture" / "request-results").mkdir(parents=True)
    (epoch / "captures" / "helius-free" / "0001").mkdir(parents=True)
    (epoch / "epoch-plan.json").write_text('{"providers":["helius-free","solana-public"]}')
    (epoch / "window-plan.json").write_text('{"selected_slot_range_exclusive":[100,164]}')
    (epoch / "paired-block-capture" / "requests" / "0001.json").write_text(
        '{"provider":"helius-free","credit_reservation":1}'
    )
    (epoch / "paired-block-capture" / "request-results" / "0001.json").write_text(
        '{"response_bytes":1234}'
    )
    (epoch / "captures" / "helius-free" / "0001" / "receipt.json").write_text(
        '{"response_bytes":1234}'
    )

    assert campaign_usage(tmp_path) == {
        "helius_credits": 1,
        "raw_bytes": 1234,
        "stored_bytes": 1234,
        "helius_slots": 64,
        "public_pair_slots": 64,
        "public_windows": 1,
        "alchemy_pair_slots": 0,
    }


def test_shared_helius_daily_reservations_count_standard_and_watch_ledgers(tmp_path: Path):
    standard_root = tmp_path / "helius-blocks"
    watch_root = tmp_path / "brain-senses-live-helius-watch-v3"
    standard = standard_root / "requests" / "0001.json"
    watch = watch_root / "epoch" / "requests" / "0001.json"
    public = standard_root / "requests" / "0002.json"
    for path in (standard, watch, public):
        path.parent.mkdir(parents=True, exist_ok=True)
    standard.write_text('{"provider":"helius-free","credit_reservation":1}')
    watch.write_text('{"method":"getTransaction","outcome":"reserved_before_io"}')
    public.write_text('{"provider":"solana-public","credit_reservation":0}')
    same_day = datetime(2026, 10, 9, 12, 0, tzinfo=UTC).timestamp()
    for path in (standard, watch, public):
        os.utime(path, (same_day, same_day))

    usage = scheduler.shared_helius_reservations_for_utc_day(
        (standard_root, watch_root), "2026-10-09"
    )

    assert usage["total"] == 2
    assert usage["by_root"][str(standard_root)] == 1
    assert usage["by_root"][str(watch_root)] == 1


def test_campaign_usage_counts_compressed_storage_and_temporal_sample_size(tmp_path: Path) -> None:
    epoch = tmp_path / "20261009T120000Z"
    captures = epoch / "paired-block-capture" / "captures" / "helius-free" / "0001"
    captures.mkdir(parents=True)
    (epoch / "epoch-plan.json").write_text('{"providers":["helius-free"]}')
    (epoch / "window-plan.json").write_text(
        '{"selected_slot_range_exclusive":[100,200],"full_sample_slots":[101,102]}'
    )
    (captures / "receipt.json").write_text(
        '{"response_bytes":1234,"storage":{"bytes":150,"codec":"zstd"}}'
    )
    (epoch / "paired-block-capture" / "request-results").mkdir(parents=True)
    (epoch / "paired-block-capture" / "request-results" / "0001.json").write_text(
        '{"response_bytes":1234,"stored_response_bytes":150}'
    )

    usage = campaign_usage(tmp_path)

    assert usage["raw_bytes"] == 1234
    assert usage["stored_bytes"] == 150
    assert usage["helius_slots"] == 2


def test_stratified_sample_spans_hour_with_reproducible_exact_inclusion_probabilities():
    windows, slots, probabilities = stratified_slot_windows(
        10_000,
        23_000,
        window_slots=256,
        stratum_count=8,
        seed="fixed-sample-seed",
    )
    repeated = stratified_slot_windows(
        10_000,
        23_000,
        window_slots=256,
        stratum_count=8,
        seed="fixed-sample-seed",
    )

    assert (windows, slots, probabilities) == repeated
    assert [window["stratum"] for window in windows] == list(range(8))
    assert len(slots) == len(set(slots)) == 256
    assert all(len(window["selected_slot_range_exclusive"]) == 2 for window in windows)
    assert all(window["candidate_start_count"] > 1 for window in windows)
    assert set(slots) <= {int(slot) for slot, p in probabilities.items() if p != "0/1"}
    assert probabilities["10000"] == "1/1594"


def test_new_campaign_can_bind_256_stratified_helius_slots_and_compressed_storage(
    tmp_path: Path, monkeypatch
):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

    captured = {}

    def fake_start_epoch(root, **kwargs):
        captured.update(kwargs)
        return {"status": "fixture_no_rpc"}

    monkeypatch.setattr(scheduler, "datetime", FrozenDateTime)
    monkeypatch.setattr(scheduler, "start_epoch", fake_start_epoch)
    root = tmp_path / "next-campaign"

    result = scheduler.run_bucket(
        root,
        window_slots=256,
        interval_hours=1,
        campaign_hours=72,
        capture_storage_codec="zstd",
        stratum_count=8,
        public_crosscheck_utc_hours=(),
    )

    campaign = json.loads((root / "campaign.json").read_text())
    assert result["status"] == "epoch_finished"
    assert campaign["helius_expected_credits_total"] == 37_008
    assert campaign["max_helius_credit_reservations"] == 45_000
    assert campaign["max_new_stored_bytes"] == 16 * 1024**3
    assert campaign["public_crosscheck_utc_hours"] == []
    assert captured["window_slots"] == 256
    assert captured["stratum_count"] == 8
    assert captured["providers"] == ("helius-free",)


def test_independent_verification_campaign_binds_512_helius_and_32_alchemy_pairs(
    tmp_path: Path, monkeypatch
):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

    captured = {}

    def fake_start_epoch(root, **kwargs):
        captured.update(kwargs)
        return {"status": "fixture_no_rpc"}

    monkeypatch.setattr(scheduler, "datetime", FrozenDateTime)
    monkeypatch.setattr(scheduler, "start_epoch", fake_start_epoch)
    root = tmp_path / "independent-verification-campaign"
    shared_roots = (
        tmp_path / "helius-hourly-active",
        tmp_path / "helius-watch-active",
        tmp_path / "helius-historical-active",
    )

    result = scheduler.run_bucket(
        root,
        window_slots=512,
        interval_hours=1,
        campaign_hours=24,
        capture_storage_codec="zstd",
        stratum_count=16,
        paired_provider="alchemy-free",
        paired_slots_per_stratum=2,
        public_crosscheck_utc_hours=(),
        helius_credit_cap=26_000,
        max_new_raw_bytes=300 * 1024**3,
        max_new_stored_bytes=18 * 1024**3,
        max_single_epoch_raw_bytes=4 * 1024**3,
        max_single_epoch_stored_bytes=1024**3,
        expected_epoch_stored_bytes=800 * 1024**2,
        minimum_free_disk_bytes=30 * 1024**3,
        shared_helius_daily_credit_cap=36_000,
        shared_helius_reservation_roots=shared_roots,
    )

    campaign = json.loads((root / "campaign.json").read_text())
    assert result["status"] == "epoch_finished"
    assert campaign["helius_expected_credits_total"] == 24_624
    assert campaign["max_helius_credit_reservations"] == 26_000
    assert campaign["max_alchemy_pair_slots"] == 768
    assert campaign["providers"] == ["helius-free", "alchemy-free"]
    assert campaign["max_new_stored_bytes"] == 18 * 1024**3
    assert campaign["minimum_free_disk_bytes"] == 30 * 1024**3
    assert campaign["shared_helius_daily_credit_cap"] == 36_000
    assert campaign["shared_helius_reservation_roots"] == [str(root) for root in shared_roots]
    assert captured["window_slots"] == 512
    assert captured["stratum_count"] == 16
    assert captured["paired_provider"] == "alchemy-free"
    assert captured["paired_slots_per_stratum"] == 2
    assert captured["providers"] == ("helius-free", "alchemy-free")
