from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from memecoin_ai.domain import Availability, Event, Kind, Pool, ValidationError


@dataclass(frozen=True)
class ObservedPool:
    pool: Pool
    event_ns: int
    available_ns: int
    version: tuple[int, int]


@dataclass(frozen=True)
class Observation:
    now_ns: int
    pools: Mapping[str, ObservedPool]
    healthy: bool


class ObservationBook:
    def __init__(self) -> None:
        self._pools: dict[str, ObservedPool] = {}
        self._healthy = True
        self._now = -1

    def ingest(self, event: Event, now_ns: int, *, allow_assumed: bool = False) -> None:
        if now_ns < self._now:
            raise ValidationError("observation clock cannot move backwards")
        if event.available_ns is None or event.available_ns > now_ns:
            raise ValidationError("event is not available at observation time")
        if event.availability == Availability.ASSUMED and not allow_assumed:
            raise ValidationError("assumed availability requires explicit opt-in")
        self._now = now_ns
        if event.kind == Kind.GAP:
            # Recovery requires rebuilding from a verified checkpoint, not a guessed timeout.
            self._healthy = False
            return
        pool = event.pool
        current = self._pools.get(pool.pool_id)
        version = (event.event_ns, event.sequence)
        if current is None or version > current.version:
            self._pools[pool.pool_id] = ObservedPool(
                pool, event.event_ns, event.available_ns, version
            )

    def snapshot(self, now_ns: int) -> Observation:
        if now_ns < self._now:
            raise ValidationError("snapshot predates ingested data")
        self._now = now_ns
        return Observation(now_ns, MappingProxyType(dict(self._pools)), self._healthy)
