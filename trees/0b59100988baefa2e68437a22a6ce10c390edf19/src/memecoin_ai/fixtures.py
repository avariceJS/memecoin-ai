from __future__ import annotations

from memecoin_ai.config import RunConfig
from memecoin_ai.domain import Event, Pool
from memecoin_ai.execution import Intent, Portfolio, ReferenceCPMM
from memecoin_ai.observations import Observation
from memecoin_ai.portfolio import TargetPosition, rebalance_intent

SECOND = 1_000_000_000


def synthetic_events() -> tuple[Event, ...]:
    return (
        Event.pool_event(
            "a0", 0, SECOND, Pool("pool-a", "FAKE_A", "SOL", 1_000_000, 1_000_000, 30)
        ),
        Event.pool_event(
            "a1", 20 * SECOND, 25 * SECOND, Pool("pool-a", "FAKE_A", "SOL", 1_000_000, 800_000, 30)
        ),
        Event.pool_event(
            "b0", 30 * SECOND, 35 * SECOND, Pool("pool-b", "FAKE_B", "SOL", 2_000_000, 500_000, 30)
        ),
        Event.pool_event(
            "a2", 45 * SECOND, 46 * SECOND, Pool("pool-a", "FAKE_A", "SOL", 1_000_000, 700_000, 30)
        ),
    )


class ScriptedFixturePolicy:
    def __init__(self, config: RunConfig) -> None:
        self.config = config

    def decide(self, observation: Observation, portfolio: Portfolio) -> tuple[Intent, ...]:
        now = observation.now_ns
        if now not in (self.config.entry_ns, self.config.exit_ns):
            return ()
        if self.config.pool_id not in observation.pools or portfolio.pending:
            return ()
        units = self.config.target_units if now == self.config.entry_ns else 0
        intent = rebalance_intent(
            TargetPosition(self.config.pool_id, units),
            observation,
            portfolio,
            ReferenceCPMM(),
            order_id=f"fixture-{now}",
            max_quote_input=self.config.risk.max_order_quote,
            slippage_bps=self.config.slippage_bps,
        )
        return (intent,) if intent is not None else ()
