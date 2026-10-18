from __future__ import annotations

from dataclasses import dataclass

from memecoin_ai.domain import ValidationError, integer
from memecoin_ai.execution import Intent, Portfolio, Side, SwapModel
from memecoin_ai.observations import Observation


@dataclass(frozen=True)
class TargetPosition:
    pool_id: str
    base_units: int

    def __post_init__(self) -> None:
        integer(self.base_units, "base_units")


def rebalance_intent(
    target: TargetPosition,
    observation: Observation,
    portfolio: Portfolio,
    model: SwapModel,
    *,
    order_id: str,
    max_quote_input: int,
    slippage_bps: int,
) -> Intent | None:
    integer(max_quote_input, "max_quote_input", 1)
    integer(slippage_bps, "slippage_bps")
    if slippage_bps >= 10000:
        raise ValidationError("slippage must be below 10000 bps")
    if portfolio.pending:
        raise ValidationError("cannot rebalance while an order is pending")
    pool = observation.pools[target.pool_id].pool
    delta = target.base_units - portfolio.balances.get(pool.base_asset, 0)
    if delta == 0:
        return None
    side = Side.BUY if delta > 0 else Side.SELL
    if delta > 0:
        if model.quote(pool, side, max_quote_input).amount_out < delta:
            raise ValidationError("target exceeds quote input budget")
        low, high = 1, max_quote_input
        while low < high:
            mid = (low + high) // 2
            try:
                output = model.quote(pool, side, mid).amount_out
            except ValidationError:
                output = 0
            if output >= delta:
                high = mid
            else:
                low = mid + 1
        amount = low
    else:
        amount = -delta
    quote = model.quote(pool, side, amount)
    minimum = max(1, quote.amount_out * (10000 - slippage_bps) // 10000)
    return Intent(order_id, pool.pool_id, side, amount, minimum)
