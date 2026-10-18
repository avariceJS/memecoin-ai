from __future__ import annotations

from collections.abc import Iterator
from fractions import Fraction

from memecoin_ai.domain import Event, ValidationError, integer
from memecoin_ai.observations import Observation, ObservationBook
from memecoin_ai.research import Example, Features, Target


def observation_stream(
    events: tuple[Event, ...],
    times: tuple[int, ...],
    *,
    allow_assumed: bool = False,
) -> Iterator[Observation]:
    if tuple(sorted(set(times))) != times:
        raise ValidationError("observation times must be increasing and unique")
    for t in times:
        integer(t, "observation time")
    if any(e.available_ns is None for e in events):
        raise ValidationError("unknown availability requires qualification, not imputation")
    if len({e.event_id for e in events}) != len(events):
        raise ValidationError("duplicate event IDs")
    ordered = sorted(
        events, key=lambda e: (e.available_ns or 0, e.event_ns, e.sequence, e.event_id)
    )
    cursor = 0
    book = ObservationBook()
    for t in times:
        while cursor < len(ordered):
            event = ordered[cursor]
            assert event.available_ns is not None
            if event.available_ns > t:
                break
            book.ingest(event, event.available_ns, allow_assumed=allow_assumed)
            cursor += 1
        yield book.snapshot(t)


def pool_features(observation: Observation, max_age_ns: int) -> tuple[Features, ...]:
    integer(max_age_ns, "max_age_ns", 1)
    if not observation.healthy:
        raise ValidationError("cannot extract features across an unresolved data gap")
    rows = []
    for pool_id, state in sorted(observation.pools.items()):
        pool = state.pool
        if not pool.tradable or observation.now_ns - state.event_ns > max_age_ns:
            continue
        rows.append(
            Features(
                f"{pool_id}:{observation.now_ns}",
                pool.base_asset,
                observation.now_ns,
                state.available_ns,
                ("reserve_ratio", "state_age_seconds"),
                (
                    float(Fraction(pool.quote_reserve, pool.base_reserve)),
                    (observation.now_ns - state.event_ns) / 1_000_000_000,
                ),
            )
        )
    return tuple(rows)


def reference_examples(
    events: tuple[Event, ...],
    decision_times: tuple[int, ...],
    *,
    horizon_ns: int,
    coverage_end_ns: int,
    max_age_ns: int,
) -> tuple[Example, ...]:
    """Observed reserve-ratio labels for harness tests, NOT executable trade returns."""
    integer(horizon_ns, "horizon_ns", 1)
    integer(coverage_end_ns, "coverage_end_ns")
    if not decision_times or tuple(sorted(set(decision_times))) != decision_times:
        raise ValidationError("decision times must be increasing and nonempty")
    if any(t + horizon_ns > coverage_end_ns for t in decision_times):
        raise ValidationError("target horizon extends beyond declared coverage")
    times = tuple(sorted(set(decision_times) | {t + horizon_ns for t in decision_times}))
    snapshots = {o.now_ns: o for o in observation_stream(events, times)}
    examples = []
    definition = f"observed_reserve_ratio_return:horizon_ns={horizon_ns}:not_executable"
    for t in decision_times:
        current, future = snapshots[t], snapshots[t + horizon_ns]
        for features in pool_features(current, max_age_ns):
            pool_id = features.sample_id.rsplit(":", 1)[0]
            state = future.pools.get(pool_id)
            value, reason = None, None
            if not future.healthy:
                reason = "data_gap"
            elif state is None or not state.pool.tradable:
                reason = "no_observable_exit_state"
            elif future.now_ns - state.event_ns > max_age_ns:
                reason = "stale_target_state"
            else:
                price = float(Fraction(state.pool.quote_reserve, state.pool.base_reserve))
                value = price / features.values[0] - 1
            target = Target(
                features.sample_id, t + horizon_ns, t + horizon_ns, value, definition, reason
            )
            examples.append(Example(features, target))
    return tuple(examples)
