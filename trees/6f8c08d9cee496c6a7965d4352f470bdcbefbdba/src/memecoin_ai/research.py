from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from typing import Protocol

from memecoin_ai.domain import ValidationError, integer


@dataclass(frozen=True)
class Features:
    sample_id: str
    asset: str
    decision_ns: int
    available_ns: int
    names: tuple[str, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        integer(self.decision_ns, "decision_ns")
        integer(self.available_ns, "available_ns")
        if not isinstance(self.names, tuple) or not isinstance(self.values, tuple):
            raise ValidationError("features must use immutable tuples")
        if self.available_ns > self.decision_ns:
            raise ValidationError("feature leakage: not available at decision time")
        if not self.sample_id or not self.asset or not self.names:
            raise ValidationError("sample identity and feature schema are required")
        if len(self.names) != len(self.values) or len(set(self.names)) != len(self.names):
            raise ValidationError("invalid feature schema")
        if not all(math.isfinite(v) for v in self.values):
            raise ValidationError("features must be finite; missingness needs explicit encoding")


@dataclass(frozen=True)
class Target:
    sample_id: str
    end_ns: int
    available_ns: int
    value: float | None
    definition: str
    censored_reason: str | None = None

    def __post_init__(self) -> None:
        integer(self.end_ns, "target end")
        integer(self.available_ns, "target availability")
        if self.available_ns < self.end_ns or not self.definition:
            raise ValidationError("invalid target timing or definition")
        if self.value is None:
            if not self.censored_reason:
                raise ValidationError("missing outcome requires a censoring reason")
        elif not math.isfinite(self.value) or self.censored_reason is not None:
            raise ValidationError("invalid observed target")


@dataclass(frozen=True)
class Example:
    features: Features
    target: Target

    def __post_init__(self) -> None:
        if self.features.sample_id != self.target.sample_id:
            raise ValidationError("target identity mismatch")
        if self.target.end_ns <= self.features.decision_ns:
            raise ValidationError("target must end after decision")


@dataclass(frozen=True)
class Fold:
    train_start_ns: int
    train_end_ns: int
    test_start_ns: int
    test_end_ns: int

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            integer(getattr(self, field), field)
        if not self.train_start_ns < self.train_end_ns <= self.test_start_ns < self.test_end_ns:
            raise ValidationError("invalid chronological fold")


@dataclass(frozen=True)
class TrainingSet:
    examples: tuple[Example, ...]
    cutoff_ns: int

    def __post_init__(self) -> None:
        integer(self.cutoff_ns, "training cutoff")
        if not isinstance(self.examples, tuple):
            raise ValidationError("training examples must be immutable")
        if not self.examples:
            raise ValidationError("empty training set")
        if len({e.features.sample_id for e in self.examples}) != len(self.examples):
            raise ValidationError("duplicate training samples")
        for example in self.examples:
            if example.target.available_ns > self.cutoff_ns:
                raise ValidationError("label unavailable at training cutoff")
            if example.features.decision_ns >= self.cutoff_ns:
                raise ValidationError("training features after cutoff")


@dataclass(frozen=True)
class Split:
    train: TrainingSet
    test: tuple[Example, ...]
    purged_ids: tuple[str, ...]


def split_examples(examples: tuple[Example, ...], fold: Fold) -> Split:
    ids = [e.features.sample_id for e in examples]
    if len(set(ids)) != len(ids):
        raise ValidationError("duplicate sample IDs")
    train, test, purged = [], [], []
    for example in examples:
        decision = example.features.decision_ns
        if fold.train_start_ns <= decision < fold.train_end_ns:
            if (
                example.target.end_ns >= fold.test_start_ns
                or example.target.available_ns > fold.train_end_ns
            ):
                purged.append(example.features.sample_id)
            else:
                train.append(example)
        elif fold.test_start_ns <= decision < fold.test_end_ns:
            test.append(example)
    return Split(TrainingSet(tuple(train), fold.train_end_ns), tuple(test), tuple(purged))


def walk_forward(
    *,
    start_ns: int,
    train_ns: int,
    test_ns: int,
    end_ns: int,
    gap_ns: int = 0,
) -> tuple[Fold, ...]:
    integer(start_ns, "start")
    integer(train_ns, "train length", 1)
    integer(test_ns, "test length", 1)
    integer(gap_ns, "gap")
    integer(end_ns, "end")
    folds = []
    cursor = start_ns + train_ns + gap_ns
    while cursor + test_ns <= end_ns:
        folds.append(Fold(cursor - gap_ns - train_ns, cursor - gap_ns, cursor, cursor + test_ns))
        cursor += test_ns
    if not folds:
        raise ValidationError("not enough time for one fold")
    return tuple(folds)


@dataclass(frozen=True)
class Standardizer:
    names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    fit_cutoff_ns: int

    @classmethod
    def fit(cls, training: TrainingSet) -> Standardizer:
        names = training.examples[0].features.names
        if any(e.features.names != names for e in training.examples):
            raise ValidationError("mixed feature schemas")
        columns = tuple(zip(*(e.features.values for e in training.examples), strict=True))
        return cls(
            names,
            tuple(statistics.fmean(c) for c in columns),
            tuple(statistics.pstdev(c) or 1.0 for c in columns),
            training.cutoff_ns,
        )

    def transform(self, features: Features) -> tuple[float, ...]:
        if features.names != self.names:
            raise ValidationError("feature schema mismatch")
        return tuple(
            (x - m) / s for x, m, s in zip(features.values, self.means, self.scales, strict=True)
        )


@dataclass(frozen=True)
class Forecast:
    sample_id: str
    definition: str
    mean: float
    quantiles: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if not self.definition or not math.isfinite(self.mean) or not self.quantiles:
            raise ValidationError("invalid forecast")
        previous_q, previous_value = -1.0, -math.inf
        for q, value in self.quantiles:
            if not 0 < q < 1 or q <= previous_q or not math.isfinite(value):
                raise ValidationError("invalid quantile")
            if value < previous_value:
                raise ValidationError("crossing quantiles")
            previous_q, previous_value = q, value


class Forecaster(Protocol):
    def fit(self, training: TrainingSet) -> None: ...

    def predict(self, features: Features) -> Forecast: ...


class EmpiricalForecaster:
    """Unconditional learned baseline; censored outcomes cannot silently disappear."""

    def __init__(self) -> None:
        self._values: tuple[float, ...] = ()
        self._definition = ""
        self._cutoff = -1
        self._names: tuple[str, ...] = ()

    def fit(self, training: TrainingSet) -> None:
        values = []
        definitions = {e.target.definition for e in training.examples}
        if len(definitions) != 1:
            raise ValidationError("mixed target definitions")
        for example in training.examples:
            if example.target.value is None:
                raise ValidationError(
                    "baseline cannot fit censored outcomes; choose explicit treatment"
                )
            values.append(example.target.value)
        self._values = tuple(sorted(values))
        self._definition = next(iter(definitions))
        self._cutoff = training.cutoff_ns
        self._names = training.examples[0].features.names
        if any(e.features.names != self._names for e in training.examples):
            raise ValidationError("mixed feature schemas")

    def predict(self, features: Features) -> Forecast:
        if not self._values:
            raise ValidationError("model is not fitted")
        if features.decision_ns < self._cutoff:
            raise ValidationError("prediction precedes model training cutoff")
        if features.names != self._names:
            raise ValidationError("feature schema mismatch")
        return Forecast(
            features.sample_id,
            self._definition,
            statistics.fmean(self._values),
            tuple(
                (q, self._values[min(len(self._values) - 1, int(q * len(self._values)))])
                for q in (0.1, 0.5, 0.9)
            ),
        )


def pinball(forecast: Forecast, target: Target) -> float:
    if forecast.sample_id != target.sample_id or forecast.definition != target.definition:
        raise ValidationError("forecast/target mismatch")
    if target.value is None:
        raise ValidationError("cannot score a censored target without explicit treatment")
    return statistics.fmean(
        max(q * (target.value - value), (q - 1) * (target.value - value))
        for q, value in forecast.quantiles
    )


def block_bootstrap_mean_interval(
    ordered_values: tuple[float, ...],
    *,
    block_size: int,
    seed: int,
    repetitions: int = 1000,
    alpha: float = 0.05,
) -> tuple[float, float]:
    integer(block_size, "block_size", 1)
    integer(repetitions, "repetitions", 2)
    integer(seed, "seed")
    n = len(ordered_values)
    if n < 2 * block_size or not 0 < alpha < 1:
        raise ValidationError("insufficient blocks or invalid confidence level")
    if not all(math.isfinite(v) for v in ordered_values):
        raise ValidationError("nonfinite returns")
    rng = random.Random(seed)
    means = []
    for _ in range(repetitions):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(n - block_size + 1)
            sample.extend(ordered_values[start : start + block_size])
        means.append(statistics.fmean(sample[:n]))
    means.sort()
    return means[int(repetitions * alpha / 2)], means[
        min(repetitions - 1, int(repetitions * (1 - alpha / 2)))
    ]
