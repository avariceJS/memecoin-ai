"""Synthetic scheduler timing and content coalescing; no invented market observations."""

import json
from pathlib import Path

import pytest

from memecoin_ai.hybrid import Reducer
from memecoin_ai.pubsub import CadenceGrid, ContentTriggers, StreamError


def test_completion_delay_does_not_round_five_seconds_up_to_ten():
    g = CadenceGrid(100, 5, 4)
    for tick in range(24):
        g.check(tick, g.deadline(tick) + 0.4)
        assert g.due(tick, 1, tick - 1, False, False)
        assert g.deadline(tick + 1) - g.deadline(tick) == 5
    with pytest.raises(StreamError, match="schedule_overrun"):
        g.check(3, 119.1)


def test_fixed_matched_pair_and_adaptive_heartbeat_share_absolute_ticks():
    ticks = range(24)
    fixed = [t for t in ticks if CadenceGrid.due(t, 2, None, False, False)]
    assert fixed == list(range(0, 24, 2))
    last = None
    quiet = []
    for tick in ticks:
        if CadenceGrid.due(tick, 2, last, False, True):
            quiet.append(tick)
            last = tick
    assert quiet == fixed
    assert CadenceGrid.due(1, 2, 0, True, True)
    # Content-triggered read resets heartbeat; it cannot cause a 20-second stall.
    assert not CadenceGrid.due(2, 2, 1, False, True)
    assert CadenceGrid.due(3, 2, 1, False, True)


def test_equal_content_new_slot_is_preserved_as_raw_but_not_dirty():
    t = ContentTriggers({"p": ["a"]})
    assert t.anchor_accounts("p", 10, {"a": {"data": "A"}}) == []
    for slot in range(11, 100):
        assert t.notification("a", slot, {"data": "A"}) == "unchanged"
    assert not t.needed("p")
    assert t.notification("a", 100, {"data": "B"}) == "hint"
    assert t.needed("p")


def test_future_hint_survives_older_anchor_and_shared_pools_are_independent():
    t = ContentTriggers({"p": ["a"], "q": ["a"]})
    t.anchor_accounts("p", 10, {"a": 1})
    t.anchor_accounts("q", 10, {"a": 1})
    t.notification("a", 20, 2)
    t.anchor_accounts("p", 15, {"a": 1})
    assert t.needed("p") and t.needed("q")
    t.anchor_accounts("p", 20, {"a": 2})
    assert not t.needed("p") and t.needed("q")
    assert t.anchor_accounts("q", 20, {"a": 2}) == []


def test_late_covered_values_do_not_replace_http_authority():
    t = ContentTriggers({"p": ["a"]})
    t.anchor_accounts("p", 20, {"a": "B"})
    t.notification("a", 20, "A")
    t.notification("a", 19, "A")
    assert not t.needed("p")
    assert t.notification("a", 21, "A") == "hint"
    assert t.needed("p")


def test_unrelated_hint_cannot_hide_missing_write_to_another_dependency():
    t = ContentTriggers({"p": ["a", "b"]})
    t.anchor_accounts("p", 10, {"a": 1, "b": 1})
    t.notification("a", 11, 2)
    missing = t.anchor_accounts("p", 12, {"a": 2, "b": 2})
    assert missing == ["b"]


def test_late_above_anchor_and_same_slot_distinct_values_are_conservative():
    t = ContentTriggers({"p": ["a"]})
    t.anchor_accounts("p", 10, {"a": 1})
    assert t.notification("a", 20, 1) == "unchanged"
    assert t.notification("a", 19, 2) == "late"
    assert t.needed("p")
    assert t.latest[("p", "a")][0] == 20
    assert t.notification("a", 20, 3) == "hint"


def test_v2_reducer_has_separate_books_and_triggers_for_both_wss_arms(tmp_path):
    plan = json.loads((Path(__file__).parents[1] / "configs/alchemy-pilot-v2.json").read_text())
    r = Reducer(tmp_path, plan)
    assert tuple(r.books) == ("http5", "http10", "wss10", "adaptive")
    assert r.triggers_by_arm["wss10"] is not r.triggers_by_arm["adaptive"]
    pool = plan["cohort"][0]["pool_id"]
    r.apply(dict(kind="gap", arm="wss10", available_ns=1, reason="synthetic fault"))
    assert pool in r.books["wss10"].gaps
    assert not r.books["adaptive"].gaps
