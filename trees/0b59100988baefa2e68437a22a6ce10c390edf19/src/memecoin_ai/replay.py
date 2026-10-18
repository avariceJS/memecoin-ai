from __future__ import annotations

import heapq
import random
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any, Protocol

from memecoin_ai.domain import Availability, Event, Kind, Origin, Pool, ValidationError, integer
from memecoin_ai.execution import (
    Intent,
    Ledger,
    Portfolio,
    ReferenceCPMM,
    RiskLimits,
    RiskManager,
    Side,
    liquidation_mark,
)
from memecoin_ai.observations import Observation, ObservationBook


class Policy(Protocol):
    def decide(self, observation: Observation, portfolio: Portfolio) -> tuple[Intent, ...]: ...


class CashPolicy:
    def decide(self, observation: Observation, portfolio: Portfolio) -> tuple[Intent, ...]:
        return ()


@dataclass(frozen=True)
class ExecutionAssumptions:
    latency_ns: int
    network_fee_quote: int
    drop_probability: float = 0.0
    fail_probability: float = 0.0
    seed: int = 0

    def __post_init__(self) -> None:
        integer(self.latency_ns, "latency_ns", 1)
        integer(self.network_fee_quote, "network_fee_quote")
        integer(self.seed, "seed")
        if not 0 <= self.drop_probability <= 1 or not 0 <= self.fail_probability <= 1:
            raise ValidationError("invalid outcome probability")
        if self.drop_probability + self.fail_probability > 1:
            raise ValidationError("outcome probabilities exceed one")


@dataclass(frozen=True)
class ExecutionRecord:
    order_id: str
    time_ns: int
    status: str
    reason: str
    amount_out: int = 0
    network_fee_quote: int = 0
    pool_fee_input: int = 0


@dataclass(frozen=True)
class ReplayResult:
    records: tuple[ExecutionRecord, ...]
    equity: tuple[dict[str, Any], ...]
    final_balances: dict[str, int]
    limitations: tuple[str, ...]


class ReferenceReplay:
    """Dual-clock synthetic replay. A policy never receives the market truth book."""

    def __init__(
        self,
        events: tuple[Event, ...],
        assumptions: ExecutionAssumptions,
        limits: RiskLimits,
        *,
        quote_asset: str,
        initial_cash: int,
        allow_assumed: bool = False,
    ) -> None:
        if not events:
            raise ValidationError("empty replay")
        if any(e.origin != Origin.SYNTHETIC for e in events):
            raise ValidationError(
                "reference replay accepts synthetic data only; venue not calibrated"
            )
        if any(e.available_ns is None for e in events):
            raise ValidationError("unknown availability cannot enter replay")
        if not allow_assumed and any(e.availability == Availability.ASSUMED for e in events):
            raise ValidationError("assumed availability requires opt-in")
        if any(e.kind != Kind.POOL for e in events):
            raise ValidationError("replay with declared data gaps is unsupported")
        if len({e.event_id for e in events}) != len(events):
            raise ValidationError("duplicate event IDs")
        identities: dict[str, tuple[str, str, str]] = {}
        clocks: set[tuple[str, int, int]] = set()
        bases: dict[str, str] = {}
        for event in events:
            pool = event.pool
            if pool.quote_asset != quote_asset or pool.mechanism != ReferenceCPMM.name:
                raise ValidationError("unsupported quote asset or mechanism")
            identity = (pool.base_asset, pool.quote_asset, event.source)
            if pool.pool_id in identities and identities[pool.pool_id] != identity:
                raise ValidationError("pool identity/source changed")
            identities[pool.pool_id] = identity
            if pool.base_asset in bases and bases[pool.base_asset] != pool.pool_id:
                raise ValidationError("reference replay requires one pool per base asset")
            bases[pool.base_asset] = pool.pool_id
            clock = (pool.pool_id, event.event_ns, event.sequence)
            if clock in clocks:
                raise ValidationError("ambiguous pool event ordering")
            clocks.add(clock)
        self.events = events
        self.assumptions = assumptions
        self.limits = limits
        self.quote_asset = quote_asset
        self.initial_cash = initial_cash
        self.allow_assumed = allow_assumed

    def run(self, policy: Policy, decision_times: tuple[int, ...], end_ns: int) -> ReplayResult:
        integer(end_ns, "end_ns")
        if not decision_times or tuple(sorted(set(decision_times))) != decision_times:
            raise ValidationError("decision times must be unique and increasing")
        for t in decision_times:
            integer(t, "decision time")
            if t > end_ns:
                raise ValidationError("decision after replay end")
        ledger = Ledger(self.quote_asset, self.initial_cash)
        model = ReferenceCPMM()
        risk = RiskManager(self.limits, model)
        observed = ObservationBook()
        truth: dict[str, Pool] = {}
        records: list[ExecutionRecord] = []
        equity: list[dict[str, Any]] = []
        usage: dict[str, Fraction] = {}
        pending: dict[str, tuple[Intent, Pool]] = {}
        seen_orders: set[str] = set()
        rng = random.Random(self.assumptions.seed)
        queue: list[tuple[int, int, int, object]] = []
        counter = 0

        def schedule(t: int, priority: int, payload: object) -> None:
            nonlocal counter
            if t <= end_ns:
                heapq.heappush(queue, (t, priority, counter, payload))
                counter += 1

        # Equal time order is explicit: market state, arrivals, executions, decisions.
        # Nanosecond fields express ordering precision, not claimed source clock accuracy.
        for event in sorted(self.events, key=lambda e: (e.event_ns, e.sequence, e.event_id)):
            schedule(event.event_ns, 0, event)
            assert event.available_ns is not None
            schedule(event.available_ns, 1, event)
        for t in decision_times:
            schedule(t, 3, None)
        schedule(end_ns, 4, None)
        while queue:
            now, priority, _, payload = heapq.heappop(queue)
            if priority == 0:
                assert isinstance(payload, Event)
                truth[payload.pool.pool_id] = payload.pool
            elif priority == 1:
                assert isinstance(payload, Event)
                observed.ingest(payload, now, allow_assumed=self.allow_assumed)
            elif priority == 2:
                assert isinstance(payload, Intent)
                intent = payload
                original = pending.pop(intent.order_id)[1]
                pool = truth[intent.pool_id]
                draw = rng.random()
                status, reason, output, pool_fee = "failed", "simulated_failure", 0, 0
                fee = self.assumptions.network_fee_quote
                if draw < self.assumptions.drop_probability:
                    status, reason, fee = "dropped", "simulated_drop", 0
                elif draw >= self.assumptions.drop_probability + self.assumptions.fail_probability:
                    try:
                        quote = model.quote(pool, intent.side, intent.amount_in)
                    except ValidationError:
                        reason = "untradable"
                    else:
                        reserve = (
                            pool.quote_reserve if intent.side == Side.BUY else pool.base_reserve
                        )
                        participation = Fraction(intent.amount_in, reserve)
                        total = usage.get(pool.pool_id, Fraction(0)) + participation
                        if (
                            participation * 10000 > self.limits.max_input_bps
                            or total * 10000 > self.limits.cumulative_input_bps
                        ):
                            raise ValidationError(
                                "impact domain exceeded; replay result is invalid"
                            )
                        if quote.amount_out < intent.min_out:
                            reason = "slippage"
                        else:
                            status, reason = "filled", "reference_fill"
                            output, pool_fee = quote.amount_out, quote.pool_fee
                            truth[pool.pool_id] = quote.pool_after
                            usage[pool.pool_id] = total
                ledger.settle(
                    intent, original, amount_out=output, network_fee=fee, filled=status == "filled"
                )
                records.append(
                    ExecutionRecord(intent.order_id, now, status, reason, output, fee, pool_fee)
                )
            elif priority == 3:
                observation = observed.snapshot(now)
                for intent in policy.decide(observation, ledger.view()):
                    if intent.order_id in seen_orders:
                        raise ValidationError("policy reused order ID")
                    seen_orders.add(intent.order_id)
                    rejection = risk.check(intent, observation, ledger.view())
                    if now + self.assumptions.latency_ns > end_ns:
                        rejection = "execution_after_end"
                    if rejection is None:
                        pool = observation.pools[intent.pool_id].pool
                        try:
                            ledger.reserve(intent, pool, self.assumptions.network_fee_quote)
                        except ValidationError:
                            rejection = "insufficient_balance"
                        else:
                            pending[intent.order_id] = (intent, pool)
                            schedule(now + self.assumptions.latency_ns, 2, intent)
                    if rejection is not None:
                        records.append(ExecutionRecord(intent.order_id, now, "rejected", rejection))
            mark = liquidation_mark(ledger.view(), truth, model, self.assumptions.network_fee_quote)
            equity.append({"time_ns": now, "phase": priority, **asdict(mark)})
        if pending:
            raise ValidationError("unresolved orders at replay end")
        return ReplayResult(
            tuple(records),
            tuple(equity),
            dict(ledger.view().balances),
            (
                "synthetic_only",
                "uncalibrated_execution",
                "single_quote_asset",
                "one_pool_per_base",
                "serial_pending_orders",
                "exogenous_snapshots_reset_own_impact_with_cumulative_budget",
                "liquidation_mark_is_not_realized_exit",
                "no_profitability_inference",
            ),
        )
