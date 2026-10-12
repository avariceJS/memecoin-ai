from __future__ import annotations

from hypothesis import example, given, settings
from hypothesis import strategies as st

from tools import brain_senses_external_rolling_policy_v1 as policy


@given(now_ns=st.integers(min_value=1, max_value=10**20))
@settings(max_examples=300, deadline=None)
def test_anchor_is_utc_hour_aligned_and_always_prospective(now_ns: int) -> None:
    anchor = policy.next_fresh_anchor_ns(now_ns)
    assert anchor % policy.HOUR_NS == 0
    assert anchor >= now_ns + 2 * policy.MINUTE_NS
    assert anchor - now_ns <= policy.HOUR_NS + 2 * policy.MINUTE_NS


@given(anchor_hours=st.integers(min_value=1, max_value=60_000_000))
@settings(max_examples=200, deadline=None)
def test_epoch_schedule_has_unique_forward_only_windows(anchor_hours: int) -> None:
    anchor = anchor_hours * policy.HOUR_NS
    windows = policy.build_epoch(anchor)
    assert len(windows) == 25
    assert windows[0].kind == "anchor"
    assert windows[0].start_ns == anchor
    assert windows[0].request_cap == 1
    assert all(row.kind == "frame" for row in windows[1:])
    assert all(row.request_cap == 34 for row in windows[1:])
    assert len({row.bucket for row in windows}) == len(windows)
    assert all(row.start_ns >= anchor for row in windows)
    assert all(
        right.start_ns - left.start_ns == policy.HOUR_NS
        for left, right in zip(windows[:-1], windows[1:], strict=True)
    )
    assert sum(row.request_cap for row in windows) == policy.MAX_EPOCH_REQUESTS == 817


@given(offset_ns=st.integers(min_value=0, max_value=policy.WINDOW_WIDTH_NS * 2))
@example(offset_ns=policy.WINDOW_WIDTH_NS - 1)
@example(offset_ns=policy.WINDOW_WIDTH_NS)
@settings(max_examples=200, deadline=None)
def test_expired_window_never_becomes_dispatchable_again(offset_ns: int) -> None:
    window = policy.build_epoch(policy.HOUR_NS)[0]
    current = window.start_ns + offset_ns
    state = policy.disposition(window, current)
    assert state == ("due" if offset_ns < policy.WINDOW_WIDTH_NS else "expired_unobserved")
    assert policy.disposition(window, current + policy.HOUR_NS) == "expired_unobserved"


@given(
    requests=st.integers(min_value=0, max_value=policy.MAX_ROLLING_REQUESTS + 1),
    credits=st.integers(min_value=0, max_value=policy.MAX_ROLLING_CREDITS + 10),
    stored=st.integers(min_value=0, max_value=policy.MAX_ROLLING_STORED_BYTES),
    epoch_requests=st.integers(min_value=0, max_value=policy.MAX_EPOCH_REQUESTS),
    epoch_stored=st.integers(min_value=0, max_value=policy.MAX_EPOCH_STORED_BYTES),
)
@settings(max_examples=250, deadline=None)
def test_request_admission_never_crosses_any_provider_or_storage_cap(
    requests: int,
    credits: int,
    stored: int,
    epoch_requests: int,
    epoch_stored: int,
) -> None:
    usage = policy.RollingUsage(requests, credits, stored)
    allowed = policy.request_admissible(
        usage,
        epoch_requests=epoch_requests,
        epoch_stored_bytes=epoch_stored,
        now_ns=10**18,
    )
    if allowed:
        assert requests + 1 <= policy.MAX_ROLLING_REQUESTS
        assert credits + policy.CONSERVATIVE_CREDITS_PER_REQUEST <= policy.MAX_ROLLING_CREDITS
        assert stored + policy.MAX_RESPONSE_BYTES <= policy.MAX_ROLLING_STORED_BYTES
        assert epoch_requests + 1 <= policy.MAX_EPOCH_REQUESTS
        assert epoch_stored + policy.MAX_RESPONSE_BYTES <= policy.MAX_EPOCH_STORED_BYTES


@given(
    offsets=st.lists(
        st.tuples(
            st.integers(min_value=-policy.ROLLING_WINDOW_NS, max_value=policy.ROLLING_WINDOW_NS),
            st.integers(min_value=0, max_value=policy.MAX_RESPONSE_BYTES),
        ),
        max_size=60,
    )
)
@settings(max_examples=200, deadline=None)
def test_rolling_usage_counts_only_receipted_forward_window(offsets: list[tuple[int, int]]) -> None:
    now = 50 * policy.ROLLING_WINDOW_NS
    rows = [
        {"decision_ns": now + offset, "stored_response_bytes": size} for offset, size in offsets
    ]
    usage = policy.conservative_rolling_usage(rows, now_ns=now)
    expected = [
        (offset, size) for offset, size in offsets if -policy.ROLLING_WINDOW_NS <= offset <= 0
    ]
    assert usage.requests == len(expected)
    assert usage.modeled_credits == len(expected) * policy.CONSERVATIVE_CREDITS_PER_REQUEST
    assert usage.stored_bytes == sum(size for _offset, size in expected)


def test_ambiguous_intent_reserves_one_maximum_response_in_rolling_storage() -> None:
    now = 100 * policy.ROLLING_WINDOW_NS
    usage = policy.conservative_rolling_usage(
        [{"decision_ns": now, "stored_response_bytes": None}], now_ns=now
    )
    assert usage.requests == 1
    assert usage.modeled_credits == policy.CONSERVATIVE_CREDITS_PER_REQUEST
    assert usage.stored_bytes == policy.MAX_RESPONSE_BYTES
