from dataclasses import replace

import pytest

from memecoin_ai.cpmm import U64, EconomicState, Fees, collect_fees, liquidity, swap
from memecoin_ai.domain import ValidationError


@pytest.fixture
def state():
    return EconomicState((1_000_000, 2_000_000), (100, 200), (30, 40), (20, 60), 100_000)


def quote(state, amount=10000, **kwargs):
    options = dict(exact_input=True, creator_fee_on=0, creator_enabled=True)
    options.update(kwargs)
    return swap(
        state,
        Fees(2500, 120000, 40000, 1000),
        0,
        amount,
        0 if options["exact_input"] else U64,
        **options,
    )


def test_joint_fee_rounding_and_output_creator(state):
    # ceil(1001 * .0035)=4; creator floor(4/3.5)=1; trade=3.
    q = quote(state, 1001)
    assert (q.trade_fee, q.creator_fee, q.protocol_fee, q.fund_fee) == (3, 1, 0, 0)
    assert q.after.creator == (21, 60)
    output = quote(state, 1001, creator_fee_on=2)
    assert output.creator_on_input is False
    assert output.trade_fee == 3
    assert output.creator_fee == 2
    assert output.after.creator == (20, 62)
    assert output.after.vaults[1] == state.vaults[1] - output.output_amount


@pytest.mark.parametrize("mode", [0, 1, 2])
@pytest.mark.parametrize("direction", [0, 1])
@pytest.mark.parametrize("enabled", [True, False])
def test_swap_properties(state, mode, direction, enabled):
    fees = Fees(2500, 120000, 40000, 1000)
    for amount in (10, 1001, 10_000, 500_000):
        q = swap(
            state,
            fees,
            direction,
            amount,
            0,
            exact_input=True,
            creator_fee_on=mode,
            creator_enabled=enabled,
        )
        assert q.after.reserves[0] * q.after.reserves[1] >= state.reserves[0] * state.reserves[1]
        exact = swap(
            state,
            fees,
            direction,
            q.output_amount,
            U64,
            exact_input=False,
            creator_fee_on=mode,
            creator_enabled=enabled,
        )
        assert exact.input_amount <= q.input_amount
        assert exact.output_amount == q.output_amount
        back = swap(
            q.after,
            fees,
            1 - direction,
            q.output_amount,
            0,
            exact_input=True,
            creator_fee_on=mode,
            creator_enabled=enabled,
        )
        assert back.output_amount < amount
        if not enabled:
            assert q.creator_fee == 0 and q.after.creator == state.creator


def test_limits_empty_and_overflow(state):
    q = quote(state)
    with pytest.raises(ValidationError, match="minimum"):
        swap(
            state,
            Fees(2500, 0, 0, 0),
            0,
            10000,
            q.output_amount * 2,
            exact_input=True,
            creator_fee_on=0,
            creator_enabled=False,
        )
    with pytest.raises(ValidationError, match="maximum"):
        swap(
            state,
            Fees(2500, 0, 0, 0),
            0,
            10000,
            1,
            exact_input=False,
            creator_fee_on=0,
            creator_enabled=False,
        )
    for bad in (0, -1, U64 + 1, True):
        with pytest.raises(ValidationError):
            quote(state, bad)
    with pytest.raises(ValidationError, match="reserve"):
        quote(state, state.reserves[1], exact_input=False)
    with pytest.raises(ValidationError, match="64-bit"):
        quote(replace(state, vaults=(U64, U64)))
    with pytest.raises(ValidationError, match="zero denominator"):
        swap(
            state,
            Fees(0, 0, 0, 0),
            0,
            1000,
            0,
            exact_input=True,
            creator_fee_on=0,
            creator_enabled=False,
        )


def test_lp_rounding_matches_upstream_vectors():
    for reserves, lp, supply, amounts in [
        ((2, 49), 5, 10, (1, 25)),
        ((100, 202), 5, 101, (5, 10)),
        ((5, 501), 2, 10, (1, 101)),
    ]:
        state = EconomicState(reserves, (0, 0), (0, 0), (0, 0), supply)
        after, got = liquidity(state, lp, (U64, U64), deposit=True)
        assert got == amounts
        removed, returned = liquidity(after, lp, (0, 0), deposit=False)
        assert removed.lp_supply == supply
        assert all(a <= b for a, b in zip(returned, amounts, strict=True))
    with pytest.raises(ValidationError, match="zero liquidity"):
        liquidity(EconomicState((1, 1), (0, 0), (0, 0), (0, 0), 100), 1, (100, 100), deposit=True)


@pytest.mark.parametrize("kind", ["protocol", "fund", "creator"])
def test_collect_preserves_tradable_reserves(state, kind):
    after, amounts = collect_fees(state, kind, (1, U64))
    assert after.reserves == state.reserves
    assert after.lp_supply == state.lp_supply
    assert amounts[0] == (20 if kind == "creator" else 1)
    assert getattr(after, kind)[1] == 0
    if kind == "creator":
        with pytest.raises(ValidationError, match="no creator"):
            collect_fees(after, kind, (U64, U64))


def test_invalid_state_and_fees():
    with pytest.raises(ValidationError):
        Fees(999999, 0, 0, 1)
    with pytest.raises(ValidationError):
        Fees(1, 500001, 500000, 0)
    with pytest.raises(ValidationError):
        EconomicState((1, 2), (2, 0), (0, 0), (0, 0), 1)
