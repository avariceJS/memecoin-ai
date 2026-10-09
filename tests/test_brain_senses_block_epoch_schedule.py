from datetime import UTC, datetime, timedelta

import pytest

from tools.brain_senses_block_epoch_schedule import utc_bucket


def test_utc_bucket_uses_utc_not_host_local_clock() -> None:
    instant = datetime(2026, 10, 9, 14, 23, tzinfo=UTC) + timedelta(hours=3)

    assert utc_bucket(instant, 6) == "20261009T120000Z"


def test_utc_bucket_requires_aware_time_and_supported_interval() -> None:
    with pytest.raises(ValueError, match="timezone_aware_datetime_required"):
        utc_bucket(datetime(2026, 10, 9, 12, 0))
    with pytest.raises(ValueError, match="unsupported_interval_hours"):
        utc_bucket(datetime(2026, 10, 9, 12, 0, tzinfo=UTC), 5)
