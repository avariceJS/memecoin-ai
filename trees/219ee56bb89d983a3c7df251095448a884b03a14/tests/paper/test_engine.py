"""Virtual accounting properties; all prices/times here are explicit fixtures."""

from copy import deepcopy
from decimal import Decimal as D

import pytest

from memecoin_paper.engine import NS, advance, initial, summary
from memecoin_registry.contracts import digest
from memecoin_runner.ledger import Ledger


def obs(close=300, price="1", pool="pool", available=None, quality="active"):
    return dict(
        pool=pool,
        close_s=close,
        price=price,
        available_ns=(close + 5) * NS if available is None else available,
        quality=quality,
        capture_sha=str(close),
    )


def prediction(id="p", at=310, pool="pool", q=None):
    return dict(
        id=id,
        pool=pool,
        predicted_ns=at * NS,
        input_available_ns=305 * NS,
        event_close_s=300,
        model_sha="fixture-model",
        q_by_candidate={"gru": q or [-0.1, 0.03, 0.1]},
    )


def tick(at=310, market=None, predictions=None, source_ok=True):
    return dict(
        at_ns=at * NS,
        activation_ns=309 * NS,
        market=[obs()] if market is None else market,
        predictions=[] if predictions is None else predictions,
        source_ok=source_ok,
    )


def opened():
    s, _ = advance(initial(), tick(predictions=[prediction()]))
    s, r = advance(s, tick(610, [obs(600)]))
    assert r[0]["kind"] == "entry"
    return s


def test_future_observation_fill_and_precommitted_exit():
    s, _ = advance(initial(), tick(predictions=[prediction()]))
    original = deepcopy(s)
    # Repeated or newly received OLD candle cannot fill the decision.
    s, r = advance(s, tick(320, [obs(300, available=319 * NS)]))
    assert not r and s["books"]["fixture-model:gru"]["pending"]
    assert original["last_ns"] == 310 * NS
    s, r = advance(s, tick(610, [obs(600)]))
    assert r[0]["entry_ns"] > r[0]["decision_ns"]
    assert r[0]["entry_close_s"] * NS > r[0]["decision_ns"]
    assert r[0]["exit_due_s"] == 1500
    s, r = advance(s, tick(1210, [obs(1200, "10")]))
    assert not r  # no hindsight take-profit at the attractive intervening price
    s, r = advance(s, tick(1510, [obs(1500, "1")]))
    b = s["books"]["fixture-model:gru"]
    assert b["trades"] == 1 and not b["positions"]
    expected = D(100) / D("1.005") * D(".995") * D(".9975") - D("100.25")
    assert abs(D(r[0]["net_pnl"]) - expected) < D("1e-20")
    assert D(b["cash"]) == D(1000) + D(b["realized"])
    assert D(r[0]["stress_net_pnl"]["200"]) < D(r[0]["net_pnl"])


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(source_ok=False),
        dict(market=[obs(1500, quality="quarantined")]),
        dict(market=[obs(600)]),
    ],
)
def test_source_failure_or_stale_price_never_fabricates_exit(kwargs):
    s = opened()
    s, r = advance(s, tick(1510, **kwargs))
    assert not r and s["books"]["fixture-model:gru"]["positions"]
    s, r = advance(s, tick(1810, [obs(1800)]))
    assert r[0]["kind"] == "exit" and r[0]["exit_ns"] == 1810 * NS


def test_duplicate_pending_ttl_and_no_backfill():
    s, _ = advance(initial(), tick(predictions=[prediction(), prediction()]))
    assert len(s["books"]["fixture-model:gru"]["signals"]) == 1
    s, r = advance(s, tick(920, [obs(900)]))
    assert r[0]["kind"] == "cancel" and not s["books"]["fixture-model:gru"]["positions"]
    s, r = advance(s, tick(930, [obs(900)], [prediction("late", at=310)]))
    assert r[0]["reason"] == "late_prediction_no_backfill"


def test_shared_future_observation_and_bounded_exposure():
    markets = [obs(pool=str(i)) for i in range(8)]
    preds = [prediction(str(i), pool=str(i)) for i in range(8)]
    s, records = advance(initial(), tick(market=markets, predictions=preds))
    assert sum(r["reason"] == "enter_next_observed_close" for r in records) == 3
    s, _ = advance(s, tick(610, [obs(600, pool=str(i)) for i in range(8)]))
    b = s["books"]["fixture-model:gru"]
    assert len(b["positions"]) == 3 and D(b["cash"]) == D("699.25")


def test_empirical_cash_is_valid_and_mark_is_not_realized():
    p = prediction(q=[-0.03, -0.001, 0.03])
    p["q_by_candidate"] = {"empirical": p["q_by_candidate"]["gru"]}
    s, r = advance(initial(), tick(predictions=[p]))
    assert r[0]["reason"] == "median_below_cost_buffer"
    assert summary(s, [obs()], 310 * NS)[0]["equity"] == "1000"
    s = opened()
    report = summary(s, [obs(600, "2")], 2000 * NS)[0]
    assert report["mark_stale"] and report["realized"] == "0"


@pytest.mark.parametrize("change", ["future_input", "before_activation", "nan", "clock"])
def test_reject_invalid_temporality_or_schema(change):
    p = prediction()
    t = tick(predictions=[p])
    state = initial()
    if change == "future_input":
        p["input_available_ns"] = 999 * NS
    elif change == "before_activation":
        p["predicted_ns"] = 300 * NS
    elif change == "nan":
        t["market"][0]["price"] = "NaN"
    else:
        state["last_ns"] = 999 * NS
    with pytest.raises(ValueError):
        advance(state, t)


def test_ledger_restart_exact_replay_and_external_head(tmp_path):
    ts = [tick(predictions=[prediction()]), tick(610, [obs(600)]), tick(1510, [obs(1500)])]
    binding = dict(fixture=True)
    state = initial()
    with Ledger(tmp_path, binding) as ledger:
        for t in ts:
            state, records = advance(state, t)
            ledger.append("tick", tick=t, result_sha=digest(dict(state=state, records=records)))
        head = ledger.head
    restored = initial()
    with Ledger(tmp_path, binding) as ledger:
        for e in ledger.audit(head):
            restored, records = advance(restored, e["payload"]["tick"])
            assert digest(dict(state=restored, records=records)) == e["payload"]["result_sha"]
        assert restored == state
        with pytest.raises(ValueError, match="head_changed"):
            ledger.audit("tampered")
    # A crash after commit but before publishing the dashboard cannot duplicate a fill.
    restored, records = advance(restored, ts[-1])
    assert not records and restored["books"]["fixture-model:gru"]["trades"] == 1
