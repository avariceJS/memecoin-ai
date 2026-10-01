from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ValidationError(ValueError):
    pass


def integer(value: object, name: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValidationError(f"{name} must be an integer >= {minimum}")


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Availability(StrEnum):
    OBSERVED = "observed"
    ASSUMED = "assumed"
    UNKNOWN = "unknown"


class Origin(StrEnum):
    SYNTHETIC = "synthetic"
    CHAIN = "chain"


class Kind(StrEnum):
    POOL = "pool"
    GAP = "gap"


@dataclass(frozen=True)
class Pool:
    pool_id: str
    base_asset: str
    quote_asset: str
    base_reserve: int
    quote_reserve: int
    fee_bps: int
    tradable: bool = True
    mechanism: str = "reference_cpmm_v1"

    def __post_init__(self) -> None:
        for name in ("pool_id", "base_asset", "quote_asset"):
            if not getattr(self, name):
                raise ValidationError(f"{name} is required")
        if self.base_asset == self.quote_asset:
            raise ValidationError("base and quote must differ")
        integer(self.base_reserve, "base_reserve")
        integer(self.quote_reserve, "quote_reserve")
        integer(self.fee_bps, "fee_bps")
        if self.fee_bps >= 10_000 or type(self.tradable) is not bool:
            raise ValidationError("invalid pool fee or tradability")
        if self.tradable and min(self.base_reserve, self.quote_reserve) == 0:
            raise ValidationError("tradable pool requires positive reserves")


@dataclass(frozen=True)
class Event:
    event_id: str
    source: str
    event_ns: int
    sequence: int
    available_ns: int | None
    received_ns: int | None
    availability: Availability
    origin: Origin
    kind: Kind
    payload_json: str
    raw_sha256: str | None = None
    assumption: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValidationError("unsupported event schema")
        if not self.event_id or not self.source:
            raise ValidationError("event_id and source are required")
        for name in ("event_ns", "sequence"):
            integer(getattr(self, name), name)
        if not isinstance(self.availability, Availability):
            raise ValidationError("invalid availability enum")
        if not isinstance(self.origin, Origin) or not isinstance(self.kind, Kind):
            raise ValidationError("invalid origin/kind enum")
        for name in ("available_ns", "received_ns"):
            if getattr(self, name) is not None:
                integer(getattr(self, name), name)
        if self.availability == Availability.UNKNOWN:
            if self.available_ns is not None:
                raise ValidationError("unknown availability cannot have an available timestamp")
        else:
            if self.available_ns is None or self.available_ns < self.event_ns:
                raise ValidationError("availability must not precede market event")
            if self.availability == Availability.OBSERVED:
                if self.received_ns is None or self.available_ns < self.received_ns:
                    raise ValidationError("observed availability requires receipt <= availability")
            elif not self.assumption:
                raise ValidationError("assumed availability requires a named assumption")
        if self.raw_sha256 is not None:
            if len(self.raw_sha256) != 64 or any(
                c not in "0123456789abcdef" for c in self.raw_sha256
            ):
                raise ValidationError("invalid raw digest")
        payload = json.loads(self.payload_json)
        if not isinstance(payload, dict) or canonical(payload) != self.payload_json:
            raise ValidationError("payload must be a canonical JSON object")
        if self.kind == Kind.POOL:
            Pool(**payload)
        elif not payload.get("reason"):
            raise ValidationError("gap requires reason")

    @property
    def pool(self) -> Pool:
        if self.kind != Kind.POOL:
            raise ValidationError("not a pool event")
        return Pool(**json.loads(self.payload_json))

    def to_json(self) -> str:
        return canonical(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Event:
        data: dict[str, Any] = json.loads(text)
        data["availability"] = Availability(data["availability"])
        data["origin"] = Origin(data["origin"])
        data["kind"] = Kind(data["kind"])
        return cls(**data)

    @classmethod
    def pool_event(
        cls,
        event_id: str,
        event_ns: int,
        available_ns: int,
        pool: Pool,
        *,
        sequence: int = 0,
    ) -> Event:
        return cls(
            event_id,
            "synthetic-fixture",
            event_ns,
            sequence,
            available_ns,
            available_ns,
            Availability.OBSERVED,
            Origin.SYNTHETIC,
            Kind.POOL,
            canonical(asdict(pool)),
        )
