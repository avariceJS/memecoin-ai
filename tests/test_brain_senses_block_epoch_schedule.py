from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.brain_senses_block_epoch_schedule import campaign_usage, select_parent, utc_bucket


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
    (epoch / "epoch-plan.json").write_text('{"providers":["helius-free","solana-public"]}')
    (epoch / "window-plan.json").write_text('{"selected_slot_range_exclusive":[100,164]}')
    (epoch / "paired-block-capture" / "requests" / "0001.json").write_text(
        '{"provider":"helius-free","credit_reservation":1}'
    )
    (epoch / "paired-block-capture" / "request-results" / "0001.json").write_text(
        '{"response_bytes":1234}'
    )

    assert campaign_usage(tmp_path) == {
        "helius_credits": 1,
        "raw_bytes": 1234,
        "helius_slots": 64,
        "public_pair_slots": 64,
        "public_windows": 1,
    }
