"""Pinned Raydium classic-SPL economic transitions, not transaction admission/inclusion."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from memecoin_ai.domain import ValidationError

PPM = 1_000_000
U64 = 2**64 - 1
Pair = tuple[int, int]


def u64(value: int) -> int:
    if type(value) is not int or not 0 <= value <= U64:
        raise ValidationError("expected unsigned 64-bit integer")
    return value


def ceil_div(a: int, b: int) -> int:
    if a < 0 or b <= 0:
        raise ValidationError("invalid integer division")
    return (a + b - 1) // b


@dataclass(frozen=True)
class Fees:
    trade_ppm: int
    protocol_share_ppm: int
    fund_share_ppm: int
    creator_ppm: int

    def __post_init__(self) -> None:
        for x in (self.trade_ppm, self.protocol_share_ppm, self.fund_share_ppm, self.creator_ppm):
            u64(x)
        if (
            self.trade_ppm + self.creator_ppm >= PPM
            or self.protocol_share_ppm + self.fund_share_ppm > PPM
        ):
            raise ValidationError("invalid CPMM fee rates")


@dataclass(frozen=True)
class EconomicState:
    vaults: Pair
    protocol: Pair
    fund: Pair
    creator: Pair
    lp_supply: int

    def __post_init__(self) -> None:
        u64(self.lp_supply)
        for values in (self.vaults, self.protocol, self.fund, self.creator):
            if len(values) != 2:
                raise ValidationError("expected two vaults/counters")
            for x in values:
                u64(x)
        if min(self.reserves) < 0:
            raise ValidationError("excluded fees exceed vault balance")

    @property
    def reserves(self) -> Pair:
        return (
            self.vaults[0] - self.protocol[0] - self.fund[0] - self.creator[0],
            self.vaults[1] - self.protocol[1] - self.fund[1] - self.creator[1],
        )


@dataclass(frozen=True)
class SwapQuote:
    input_amount: int
    output_amount: int
    trade_fee: int
    protocol_fee: int
    fund_fee: int
    creator_fee: int
    creator_on_input: bool
    after: EconomicState


def add(values: Pair, index: int, delta: int) -> Pair:
    result = list(values)
    result[index] += delta
    return result[0], result[1]


def swap(
    state: EconomicState,
    fees: Fees,
    input_index: int,
    amount: int,
    limit: int,
    *,
    exact_input: bool,
    creator_fee_on: int,
    creator_enabled: bool,
) -> SwapQuote:
    """Amounts include pool fees; callers must verify status, clock and token eligibility."""
    u64(amount)
    u64(limit)
    if (
        type(input_index) is not int
        or input_index not in (0, 1)
        or type(creator_fee_on) is not int
        or creator_fee_on not in (0, 1, 2)
        or type(exact_input) is not bool
        or type(creator_enabled) is not bool
        or amount == 0
        or min(state.reserves) <= 0
    ):
        raise ValidationError("unsupported direction/creator mode or empty swap")
    output_index = 1 - input_index
    x, y = state.reserves[input_index], state.reserves[output_index]
    on_input = creator_fee_on == 0 or creator_fee_on == input_index + 1
    cr = fees.creator_ppm if creator_enabled else 0
    rate = fees.trade_ppm + (cr if on_input else 0)
    # On-input creator fee is a floor split of a joint rounded fee, not two ceilings.
    if on_input and rate == 0:
        raise ValidationError("pinned on-input fee split has zero denominator")
    if exact_input:
        gross_in = amount
        total_fee = ceil_div(gross_in * rate, PPM)
        net_in = gross_in - total_fee
        gross_out = net_in * y // (x + net_in)
        creator = total_fee * cr // rate if on_input else ceil_div(gross_out * cr, PPM)
        trade = total_fee - creator if on_input else total_fee
        net_out = gross_out if on_input else gross_out - creator
        if net_out < limit:
            raise ValidationError("minimum output not met")
    else:
        net_out = amount
        gross_out = net_out if on_input else ceil_div(net_out * PPM, PPM - cr)
        if gross_out >= y:
            raise ValidationError("insufficient output reserve")
        net_in = ceil_div(x * gross_out, y - gross_out)
        gross_in = ceil_div(net_in * PPM, PPM - rate)
        total_fee = gross_in - net_in
        creator = total_fee * cr // rate if on_input else gross_out - net_out
        trade = total_fee - creator if on_input else total_fee
        if gross_in > limit:
            raise ValidationError("maximum input exceeded")
    if gross_in <= 0 or net_out <= 0 or (x + net_in) * (y - gross_out) < x * y:
        raise ValidationError("zero trading tokens or constant product decreased")
    u64(gross_in)
    u64(net_out)
    protocol = trade * fees.protocol_share_ppm // PPM
    fund = trade * fees.fund_share_ppm // PPM
    after = replace(
        state,
        vaults=add(add(state.vaults, input_index, gross_in), output_index, -net_out),
        protocol=add(state.protocol, input_index, protocol),
        fund=add(state.fund, input_index, fund),
        creator=add(state.creator, input_index if on_input else output_index, creator),
    )
    return SwapQuote(gross_in, net_out, trade, protocol, fund, creator, on_input, after)


def liquidity(
    state: EconomicState, lp_amount: int, limits: Pair, *, deposit: bool
) -> tuple[EconomicState, Pair]:
    u64(lp_amount)
    for value in limits:
        u64(value)
    if type(deposit) is not bool or not lp_amount or not state.lp_supply:
        raise ValidationError("invalid liquidity change")
    if not deposit and lp_amount > state.lp_supply:
        raise ValidationError("LP burn exceeds supply")
    amounts = []
    for reserve, limit in zip(state.reserves, limits, strict=True):
        amount, rem = divmod(lp_amount * reserve, state.lp_supply)
        # The deployed-source algorithm deliberately leaves sub-unit deposits at zero.
        if deposit and rem and amount:
            amount += 1
        if not amount or (deposit and amount > limit) or (not deposit and amount < limit):
            raise ValidationError("zero liquidity tokens or slippage limit")
        amounts.append(amount)
    pair = amounts[0], amounts[1]
    sign = 1 if deposit else -1
    return replace(
        state,
        vaults=(state.vaults[0] + sign * pair[0], state.vaults[1] + sign * pair[1]),
        lp_supply=state.lp_supply + sign * lp_amount,
    ), pair


def collect_fees(
    state: EconomicState, kind: Literal["protocol", "fund", "creator"], requested: Pair
) -> tuple[EconomicState, Pair]:
    if kind not in ("protocol", "fund", "creator"):
        raise ValidationError("unknown fee counter")
    for value in requested:
        u64(value)
    counters: Pair = getattr(state, kind)
    if kind == "creator":
        if not any(counters):
            raise ValidationError("no creator fee to collect")
        amounts = counters
    else:
        amounts = min(requested[0], counters[0]), min(requested[1], counters[1])
    remaining = (counters[0] - amounts[0], counters[1] - amounts[1])
    after = replace(
        state,
        vaults=(state.vaults[0] - amounts[0], state.vaults[1] - amounts[1]),
        protocol=remaining if kind == "protocol" else state.protocol,
        fund=remaining if kind == "fund" else state.fund,
        creator=remaining if kind == "creator" else state.creator,
    )
    return after, amounts
