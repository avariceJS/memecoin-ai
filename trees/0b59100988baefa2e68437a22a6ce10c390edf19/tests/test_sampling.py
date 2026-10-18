from dataclasses import replace

import pytest

from memecoin_ai.domain import ValidationError
from memecoin_ai.fixtures import SECOND, synthetic_events
from memecoin_ai.research import EmpiricalForecaster, Fold, pinball, split_examples
from memecoin_ai.sampling import observation_stream, pool_features, reference_examples


def test_features_respect_arrival_universe_and_delayed_state():
    snapshots = list(
        observation_stream(
            synthetic_events(), (0, 10 * SECOND, 21 * SECOND, 30 * SECOND, 35 * SECOND)
        )
    )
    assert pool_features(snapshots[0], 60 * SECOND) == ()
    assert pool_features(snapshots[1], 60 * SECOND)[0].values[0] == 1.0
    assert pool_features(snapshots[2], 60 * SECOND)[0].values[0] == 1.0
    assert len(pool_features(snapshots[3], 60 * SECOND)) == 1
    assert len(pool_features(snapshots[4], 60 * SECOND)) == 2


def test_offline_labels_train_and_predict_through_temporal_contracts():
    examples = reference_examples(
        synthetic_events(),
        tuple(t * SECOND for t in (5, 10, 15, 20, 25, 30, 35)),
        horizon_ns=5 * SECOND,
        coverage_end_ns=46 * SECOND,
        max_age_ns=60 * SECOND,
    )
    split = split_examples(examples, Fold(0, 25 * SECOND, 25 * SECOND, 40 * SECOND))
    assert any(identity.endswith(str(20 * SECOND)) for identity in split.purged_ids)
    model = EmpiricalForecaster()
    model.fit(split.train)
    losses = [pinball(model.predict(e.features), e.target) for e in split.test]
    assert losses
    assert all(loss >= 0 for loss in losses)
    with pytest.raises(ValidationError, match="coverage"):
        reference_examples(
            synthetic_events(),
            (45 * SECOND,),
            horizon_ns=10 * SECOND,
            coverage_end_ns=46 * SECOND,
            max_age_ns=60 * SECOND,
        )


def test_late_untradable_state_is_censored_not_removed():
    events = list(synthetic_events())
    last = events[-1]
    from memecoin_ai.domain import Event

    events[-1] = Event.pool_event(
        last.event_id,
        last.event_ns,
        last.available_ns,
        replace(last.pool, tradable=False, base_reserve=0, quote_reserve=0),
    )
    examples = reference_examples(
        tuple(events),
        (40 * SECOND,),
        horizon_ns=6 * SECOND,
        coverage_end_ns=46 * SECOND,
        max_age_ns=60 * SECOND,
    )
    a = next(e for e in examples if e.features.asset == "FAKE_A")
    assert a.target.value is None
    assert a.target.censored_reason == "no_observable_exit_state"
