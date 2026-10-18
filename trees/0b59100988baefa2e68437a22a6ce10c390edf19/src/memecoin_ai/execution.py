from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from fractions import Fraction
from types import MappingProxyType
from typing import Protocol

from memecoin_ai.domain import Pool, ValidationError, integer
from memecoin_ai.observations import Observation


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Intent:
    order_id: str
    pool_id: str
    side: Side
    amount_in: int
    min_out: int

    def __post_init__(self) -> None:
        if not self.order_id or not self.pool_id or not isinstance(self.side, Side):
            raise ValidationError("invalid order identity/side")
        integer(self.amount_in, "amount_in", 1)
        integer(self.min_out, "min_out", 1)


@dataclass(frozen=True)
class SwapQuote:
    amount_out: int
    pool_fee: int
    pool_after: Pool


class SwapModel(Protocol):
    name: str

    def quote(self, pool: Pool, side: Side, amount_in: int) -> SwapQuote: ...


class ReferenceCPMM:
    name = "reference_cpmm_v1"

    def quote(self, pool: Pool, side: Side, amount_in: int) -> SwapQuote:
        integer(amount_in, "amount_in", 1)
        if pool.mechanism != self.name:
            raise ValidationError(
                "unsupported mechanism; never substitute generic CPMM for a venue"
            )
        if not pool.tradable:
            raise ValidationError("pool is not tradable")
        if not isinstance(side, Side):
            raise ValidationError("invalid side")
        # This reference charges ceil(input * fee_bps / 10000), retained in reserves.
        # Actual venue rounding, protocol fees and token extensions require a different adapter.
        fee = (amount_in * pool.fee_bps + 9999) // 10000
        net = amount_in - fee
        reserve_in = pool.quote_reserve if side == Side.BUY else pool.base_reserve
        reserve_out = pool.base_reserve if side == Side.BUY else pool.quote_reserve
        output = reserve_out * net // (reserve_in + net)
        if output <= 0:
            raise ValidationError("input too small after rounding and fees")
        if side == Side.BUY:
            updated = replace(
                pool,
                base_reserve=pool.base_reserve - output,
                quote_reserve=pool.quote_reserve + amount_in,
            )
        else:
            updated = replace(
                pool,
                base_reserve=pool.base_reserve + amount_in,
                quote_reserve=pool.quote_reserve - output,
            )
        return SwapQuote(output, fee, updated)


@dataclass(frozen=True)
class Portfolio:
    quote_asset: str
    balances: Mapping[str, int]
    pending: tuple[str, ...]


class Ledger:
    def __init__(self, quote_asset: str, cash: int) -> None:
        integer(cash, "cash", 1)
        if not quote_asset:
            raise ValidationError("quote_asset is required")
        self.quote_asset = quote_asset
        self._balances: dict[str, int] = {quote_asset: cash}
        self._reservations: dict[str, dict[str, int]] = {}
        self._settled: set[str] = set()
        self._orders: dict[str, tuple[Intent, str, str]] = {}
        self.network_fees = 0

    def view(self) -> Portfolio:
        return Portfolio(
            self.quote_asset, MappingProxyType(dict(self._balances)), tuple(self._reservations)
        )

    def free(self, asset: str) -> int:
        return self._balances.get(asset, 0) - sum(
            reservation.get(asset, 0) for reservation in self._reservations.values()
        )

    def reserve(self, intent: Intent, pool: Pool, network_fee: int) -> None:
        integer(network_fee, "network_fee")
        if pool.quote_asset != self.quote_asset or intent.pool_id != pool.pool_id:
            raise ValidationError("ledger currency or pool mismatch")
        if intent.order_id in self._reservations or intent.order_id in self._settled:
            raise ValidationError("duplicate order ID")
        spend_asset = pool.quote_asset if intent.side == Side.BUY else pool.base_asset
        reserve = {self.quote_asset: network_fee}
        reserve[spend_asset] = reserve.get(spend_asset, 0) + intent.amount_in
        if any(self.free(asset) < amount for asset, amount in reserve.items()):
            raise ValidationError("insufficient unreserved balance")
        self._reservations[intent.order_id] = reserve
        self._orders[intent.order_id] = (intent, pool.base_asset, pool.quote_asset)

    def settle(
        self,
        intent: Intent,
        pool: Pool,
        *,
        amount_out: int = 0,
        network_fee: int = 0,
        filled: bool = False,
    ) -> None:
        integer(amount_out, "amount_out")
        integer(network_fee, "network_fee")
        reserve = self._reservations.get(intent.order_id)
        if reserve is None:
            raise ValidationError("order is not pending")
        if self._orders[intent.order_id] != (intent, pool.base_asset, pool.quote_asset):
            raise ValidationError("settlement does not match reserved order")
        if pool.pool_id != intent.pool_id:
            raise ValidationError("settlement pool mismatch")
        if filled and amount_out < intent.min_out:
            raise ValidationError("fill violates minimum output")
        if not filled and amount_out != 0:
            raise ValidationError("non-fill cannot credit output")
        spend = pool.quote_asset if intent.side == Side.BUY else pool.base_asset
        receive = pool.base_asset if intent.side == Side.BUY else pool.quote_asset
        debits = {self.quote_asset: network_fee}
        if filled:
            debits[spend] = debits.get(spend, 0) + intent.amount_in
        if any(amount > reserve.get(asset, 0) for asset, amount in debits.items()):
            raise ValidationError("debit exceeds reservation")
        balances = dict(self._balances)
        for asset, amount in debits.items():
            balances[asset] -= amount
        if filled:
            balances[receive] = balances.get(receive, 0) + amount_out
        self._balances = balances
        del self._reservations[intent.order_id]
        del self._orders[intent.order_id]
        self._settled.add(intent.order_id)
        self.network_fees += network_fee


@dataclass(frozen=True)
class RiskLimits:
    max_order_quote: int
    max_position_quote: int
    max_total_exposure_quote: int
    max_snapshot_age_ns: int
    max_input_bps: int
    cumulative_input_bps: int

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            integer(getattr(self, name), name, 1)
        if self.max_input_bps > 10000 or self.cumulative_input_bps > 10000:
            raise ValidationError("participation limits must be <= 10000 bps")


class RiskManager:
    def __init__(self, limits: RiskLimits, model: SwapModel) -> None:
        self.limits = limits
        self.model = model

    def check(self, intent: Intent, observation: Observation, portfolio: Portfolio) -> str | None:
        if not observation.healthy:
            return "data_gap"
        if portfolio.pending:
            return "pending_order"  # Conservative reference: serialize orders, no hidden leverage.
        state = observation.pools.get(intent.pool_id)
        if state is None:
            return "unknown_pool"
        if observation.now_ns - state.event_ns > self.limits.max_snapshot_age_ns:
            return "stale_pool"
        pool = state.pool
        if pool.quote_asset != portfolio.quote_asset:
            return "quote_asset_mismatch"
        try:
            quote = self.model.quote(pool, intent.side, intent.amount_in)
        except ValidationError:
            return "unsupported_or_untradable"
        reserve = pool.quote_reserve if intent.side == Side.BUY else pool.base_reserve
        if intent.amount_in * 10000 > reserve * self.limits.max_input_bps:
            return "input_participation"
        notional = intent.amount_in if intent.side == Side.BUY else quote.amount_out
        if notional > self.limits.max_order_quote:
            return "order_limit"
        if intent.side == Side.SELL:
            return None
        position = portfolio.balances.get(pool.base_asset, 0) + quote.amount_out
        exposure = Fraction(position * pool.quote_reserve, pool.base_reserve)
        if exposure > self.limits.max_position_quote:
            return "position_limit"
        total = exposure
        for asset, units in portfolio.balances.items():
            if asset in (portfolio.quote_asset, pool.base_asset) or units == 0:
                continue
            candidates = [p for p in observation.pools.values() if p.pool.base_asset == asset]
            if len(candidates) != 1:
                return "unknown_exposure"
            other = candidates[0]
            if (
                not other.pool.tradable
                or observation.now_ns - other.event_ns > self.limits.max_snapshot_age_ns
            ):
                return "unknown_exposure"
            total += Fraction(units * other.pool.quote_reserve, other.pool.base_reserve)
        if total > self.limits.max_total_exposure_quote:
            return "total_exposure_limit"
        return None


@dataclass(frozen=True)
class LiquidationMark:
    cash_quote: int
    liquidation_equity_quote: int
    unpriced_assets: tuple[str, ...]


def liquidation_mark(
    portfolio: Portfolio,
    pools: Mapping[str, Pool],
    model: SwapModel,
    network_fee: int,
) -> LiquidationMark:
    cash = portfolio.balances.get(portfolio.quote_asset, 0)
    value = cash
    unpriced = []
    for asset, units in portfolio.balances.items():
        if asset == portfolio.quote_asset or units == 0:
            continue
        candidates = [p for p in pools.values() if p.base_asset == asset]
        if len(candidates) != 1 or cash < network_fee:
            unpriced.append(asset)
            continue
        try:
            proceeds = model.quote(candidates[0], Side.SELL, units).amount_out - network_fee
            if proceeds <= 0:
                unpriced.append(asset)
                continue
            value += proceeds
            # Sell proceeds fund subsequent hypothetical exits; no realized balance is changed.
            cash += proceeds
        except ValidationError:
            unpriced.append(asset)
    return LiquidationMark(
        portfolio.balances.get(portfolio.quote_asset, 0), value, tuple(sorted(unpriced))
    )
