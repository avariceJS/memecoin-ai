from dataclasses import replace

import pytest

from memecoin_ai.domain import Event, Origin, Pool, ValidationError
from memecoin_ai.execution import Intent, Ledger, ReferenceCPMM, RiskLimits, Side, liquidation_mark
from memecoin_ai.replay import CashPolicy, ExecutionAssumptions, ReferenceReplay


def pool(quote=1_000_000, tradable=True):
    return Pool("p", "TOKEN", "SOL", 1_000_000, quote, 30, tradable)


def limits():
    return RiskLimits(10_000, 100_000, 100_000, 1000, 500, 1000)


def events():
    return (Event.pool_event("start", 0, 10, pool()),)


class Once:
    def __init__(self, intent):
        self.intent = intent
        self.observations = []

    def decide(self, observation, portfolio):
        self.observations.append(observation)
        return (self.intent,) if observation.now_ns == 10 else ()


def run(policy, data=None, **kwargs):
    replay = ReferenceReplay(
        data or events(),
        ExecutionAssumptions(5, 2, **kwargs),
        limits(),
        quote_asset="SOL",
        initial_cash=100_000,
    )
    return replay.run(policy, (5, 10, 20), 30)


def test_cpmm_round_trip_cannot_create_money():
    model = ReferenceCPMM()
    for amount in (100, 1000, 10_000, 100_000):
        buy = model.quote(pool(), Side.BUY, amount)
        sell = model.quote(buy.pool_after, Side.SELL, buy.amount_out)
        assert sell.amount_out < amount
        assert buy.pool_after.base_reserve * buy.pool_after.quote_reserve >= 10**12


def test_ledger_reserves_and_double_settlement():
    ledger = Ledger("SOL", 1002)
    intent = Intent("1", "p", Side.BUY, 1000, 1)
    ledger.reserve(intent, pool(), 2)
    with pytest.raises(ValidationError):
        ledger.reserve(Intent("2", "p", Side.BUY, 1, 1), pool(), 2)
    ledger.settle(intent, pool(), network_fee=2)
    assert ledger.free("SOL") == 1000
    with pytest.raises(ValidationError):
        ledger.settle(intent, pool())


def test_no_future_observation_and_execution_uses_later_market():
    policy = Once(Intent("1", "p", Side.BUY, 1000, 990))
    data = events() + (Event.pool_event("jump", 12, 25, pool(2_000_000)),)
    result = run(policy, data)
    assert not policy.observations[0].pools
    assert policy.observations[1].pools["p"].pool.quote_reserve == 1_000_000
    assert result.records[0].reason == "slippage"
    assert result.records[0].time_ns == 15
    assert result.final_balances == {"SOL": 99_998}


@pytest.mark.parametrize(
    "options,status,fee",
    [
        ({"drop_probability": 1}, "dropped", 0),
        ({"fail_probability": 1}, "failed", 2),
        ({}, "filled", 2),
    ],
)
def test_outcome_fees(options, status, fee):
    result = run(Once(Intent("1", "p", Side.BUY, 1000, 1)), **options)
    assert result.records[0].status == status
    assert result.records[0].network_fee_quote == fee


def test_cash_and_determinism():
    a = run(CashPolicy())
    b = run(CashPolicy())
    assert a == b
    assert a.final_balances == {"SOL": 100_000}
    assert a.equity[-1]["liquidation_equity_quote"] == 100_000


def test_reject_real_data_and_ambiguous_order():
    with pytest.raises(ValidationError, match="synthetic"):
        run(CashPolicy(), (replace(events()[0], origin=Origin.CHAIN),))
    with pytest.raises(ValidationError, match="ambiguous"):
        run(CashPolicy(), events() + (replace(events()[0], event_id="other"),))


def test_no_magic_terminal_liquidation():
    data = events() + (Event.pool_event("dead", 20, 21, pool(0, False)),)
    result = run(Once(Intent("1", "p", Side.BUY, 1000, 1)), data)
    assert result.final_balances["TOKEN"] > 0
    assert result.equity[-1]["unpriced_assets"] == ("TOKEN",)
    assert result.equity[-1]["liquidation_equity_quote"] == 98_998


def test_no_gas_means_no_claimed_exit():
    ledger = Ledger("SOL", 1002)
    intent = Intent("1", "p", Side.BUY, 1000, 1)
    ledger.reserve(intent, pool(), 2)
    ledger.settle(intent, pool(), amount_out=996, network_fee=2, filled=True)
    mark = liquidation_mark(ledger.view(), {"p": pool()}, ReferenceCPMM(), 2)
    assert mark.liquidation_equity_quote == 0
    assert mark.unpriced_assets == ("TOKEN",)


def test_target_hold_and_exit_use_units():
    from memecoin_ai.observations import ObservationBook
    from memecoin_ai.portfolio import TargetPosition, rebalance_intent

    book = ObservationBook()
    book.ingest(events()[0], 10)
    ledger = Ledger("SOL", 100_000)
    model = ReferenceCPMM()
    kwargs = dict(order_id="buy", max_quote_input=2000, slippage_bps=50)
    assert (
        rebalance_intent(TargetPosition("p", 0), book.snapshot(10), ledger.view(), model, **kwargs)
        is None
    )
    intent = rebalance_intent(
        TargetPosition("p", 500), book.snapshot(10), ledger.view(), model, **kwargs
    )
    assert intent is not None
    quote = model.quote(pool(), intent.side, intent.amount_in)
    assert quote.amount_out >= 500
    ledger.reserve(intent, pool(), 2)
    ledger.settle(intent, pool(), amount_out=quote.amount_out, network_fee=2, filled=True)
    hold = rebalance_intent(
        TargetPosition("p", quote.amount_out), book.snapshot(10), ledger.view(), model, **kwargs
    )
    assert hold is None
    exit_intent = rebalance_intent(
        TargetPosition("p", 0),
        book.snapshot(10),
        ledger.view(),
        model,
        **{**kwargs, "order_id": "sell"},
    )
    assert exit_intent.side == Side.SELL
    assert exit_intent.amount_in == quote.amount_out


def test_risk_pending_staleness_and_order_limit():
    from memecoin_ai.execution import RiskManager
    from memecoin_ai.observations import ObservationBook

    book = ObservationBook()
    book.ingest(events()[0], 10)
    ledger = Ledger("SOL", 100_000)
    manager = RiskManager(limits(), ReferenceCPMM())
    assert (
        manager.check(Intent("big", "p", Side.BUY, 20_000, 1), book.snapshot(10), ledger.view())
        == "order_limit"
    )
    intent = Intent("1", "p", Side.BUY, 1000, 1)
    ledger.reserve(intent, pool(), 2)
    assert manager.check(intent, book.snapshot(10), ledger.view()) == "pending_order"
    ledger.settle(intent, pool())
    assert manager.check(intent, book.snapshot(2000), ledger.view()) == "stale_pool"


def test_out_of_domain_is_invalid_not_an_invented_fill_failure():
    tiny = replace(limits(), cumulative_input_bps=1)
    replay = ReferenceReplay(
        events(), ExecutionAssumptions(5, 2), tiny, quote_asset="SOL", initial_cash=100_000
    )
    with pytest.raises(ValidationError, match="impact domain"):
        replay.run(Once(Intent("1", "p", Side.BUY, 1000, 1)), (10,), 30)


def test_end_boundary_rejects_unresolvable_order():
    result = ReferenceReplay(
        events(), ExecutionAssumptions(50, 2), limits(), quote_asset="SOL", initial_cash=100_000
    ).run(Once(Intent("1", "p", Side.BUY, 1000, 1)), (10,), 30)
    assert result.records[0].reason == "execution_after_end"
    assert result.final_balances == {"SOL": 100_000}


def test_future_changes_do_not_change_prefix_decisions():
    a = Once(Intent("1", "p", Side.BUY, 1000, 1))
    b = Once(Intent("1", "p", Side.BUY, 1000, 1))
    first = run(a)
    second = run(b, events() + (Event.pool_event("future", 25, 26, pool(2_000_000)),))
    assert first.records == second.records
    assert a.observations == b.observations
    assert [r for r in first.equity if r["time_ns"] < 25] == [
        r for r in second.equity if r["time_ns"] < 25
    ]


def test_liquidity_disappears_during_latency_charges_failed_fee():
    data = events() + (Event.pool_event("gone", 12, 25, pool(0, False)),)
    result = run(Once(Intent("1", "p", Side.BUY, 1000, 1)), data)
    assert result.records[0].status == "failed"
    assert result.records[0].reason == "untradable"
    assert result.final_balances == {"SOL": 99_998}


def test_settlement_identity_is_bound_and_rejection_does_not_mutate():
    ledger = Ledger("SOL", 100_000)
    intent = Intent("1", "p", Side.BUY, 1000, 1)
    ledger.reserve(intent, pool(), 2)
    before = ledger.view()
    with pytest.raises(ValidationError, match="match reserved"):
        ledger.settle(replace(intent, amount_in=500), pool(), amount_out=500, filled=True)
    assert ledger.view() == before
    with pytest.raises(ValidationError, match="debit exceeds"):
        ledger.settle(intent, pool(), network_fee=100_000)
    assert ledger.view() == before


def test_roundtrip_invariants_across_random_reserves():
    import random

    rng = random.Random(7)
    model = ReferenceCPMM()
    for _ in range(200):
        state = Pool(
            "p",
            "TOKEN",
            "SOL",
            rng.randrange(10**6, 10**9),
            rng.randrange(10**6, 10**9),
            rng.randrange(0, 500),
        )
        amount = rng.randrange(10000, 100000)
        buy = model.quote(state, Side.BUY, amount)
        sell = model.quote(buy.pool_after, Side.SELL, buy.amount_out)
        assert sell.amount_out <= amount
        assert sell.pool_after.base_reserve == state.base_reserve
        assert sell.pool_after.quote_reserve >= state.quote_reserve
