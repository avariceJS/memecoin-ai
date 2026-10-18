from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memecoin_ai.domain import ValidationError, integer
from memecoin_ai.execution import RiskLimits
from memecoin_ai.replay import ExecutionAssumptions


@dataclass(frozen=True)
class RunConfig:
    quote_asset: str
    initial_cash: int
    decision_times_ns: tuple[int, ...]
    end_ns: int
    policy: str
    entry_ns: int
    exit_ns: int
    pool_id: str
    target_units: int
    slippage_bps: int
    execution: ExecutionAssumptions
    risk: RiskLimits
    allow_assumed: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise ValidationError("unsupported config version")
        for name in ("initial_cash", "end_ns", "target_units"):
            integer(getattr(self, name), name, 1)
        for name in ("entry_ns", "exit_ns", "slippage_bps"):
            integer(getattr(self, name), name)
        if not self.quote_asset or not self.pool_id or type(self.allow_assumed) is not bool:
            raise ValidationError("invalid config fields")
        if (
            not self.decision_times_ns
            or tuple(sorted(set(self.decision_times_ns))) != self.decision_times_ns
        ):
            raise ValidationError("decision_times_ns must be increasing and unique")
        for t in self.decision_times_ns:
            integer(t, "decision time")
            if t > self.end_ns:
                raise ValidationError("decision after end")
        if self.policy not in ("cash", "scripted_fixture"):
            raise ValidationError("only cash and scripted fixture CLI policies are implemented")
        if self.entry_ns >= self.exit_ns or self.slippage_bps >= 10000:
            raise ValidationError("invalid policy timing or slippage")
        if self.policy == "scripted_fixture" and not {self.entry_ns, self.exit_ns}.issubset(
            self.decision_times_ns
        ):
            raise ValidationError("scripted times must be scheduled decisions")

    @classmethod
    def load(cls, path: Path) -> RunConfig:
        with path.open("rb") as f:
            values: dict[str, Any] = tomllib.load(f)
        values["execution"] = ExecutionAssumptions(**values["execution"])
        values["risk"] = RiskLimits(**values["risk"])
        values["decision_times_ns"] = tuple(values["decision_times_ns"])
        return cls(**values)
