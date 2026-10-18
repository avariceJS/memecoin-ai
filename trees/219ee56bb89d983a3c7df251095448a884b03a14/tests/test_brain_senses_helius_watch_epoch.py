from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.brain_senses_helius_watch_epoch import (
    seconds_until_next_bucket,
    select_parent,
    utc_bucket,
)


def test_watch_epoch_bucket_uses_six_hour_utc_boundaries() -> None:
    instant = datetime(2026, 10, 9, 17, 58, tzinfo=UTC) + timedelta(minutes=3)

    assert utc_bucket(instant) == "20261009T180000Z"


def test_watch_epoch_requires_aware_time_and_ends_before_next_boundary() -> None:
    with pytest.raises(ValueError, match="timezone_aware_datetime_required"):
        utc_bucket(datetime(2026, 10, 9, 12, 0))
    assert seconds_until_next_bucket(datetime(2026, 10, 9, 12, 30, tzinfo=UTC)) == 19_800


def test_watch_epoch_links_prior_root_without_cursor_policy() -> None:
    initial = Path("initial")

    assert select_parent([], initial) == initial
    assert select_parent([Path("later"), Path("earlier")], initial) == Path("later")
