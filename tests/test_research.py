from dataclasses import replace

import pytest

from memecoin_ai.domain import ValidationError
from memecoin_ai.experiments import PromotionEvidence, promotion_blockers
from memecoin_ai.research import (
    EmpiricalForecaster,
    Example,
    Features,
    Fold,
    Forecast,
    Standardizer,
    Target,
    TrainingSet,
    block_bootstrap_mean_interval,
    pinball,
    split_examples,
    walk_forward,
)


def example(identity, t, end, available, value=0.1, feature=1.0):
    return Example(
        Features(identity, "TOKEN", t, t, ("x",), (feature,)),
        Target(identity, end, available, value, "raw_return_h10"),
    )


def test_feature_leakage_and_unmatured_labels():
    with pytest.raises(ValidationError, match="leakage"):
        Features("1", "TOKEN", 10, 11, ("x",), (1.0,))
    with pytest.raises(ValidationError, match="unavailable"):
        TrainingSet((example("1", 1, 10, 20),), 15)


def test_purging_label_intervals_and_availability():
    examples = (
        example("ok", 1, 5, 6),
        example("cross", 8, 12, 12),
        example("late", 2, 5, 11),
        example("test", 10, 15, 16),
    )
    split = split_examples(examples, Fold(0, 10, 10, 20))
    assert [e.features.sample_id for e in split.train.examples] == ["ok"]
    assert set(split.purged_ids) == {"cross", "late"}
    assert [e.features.sample_id for e in split.test] == ["test"]


def test_standardizer_fits_train_only_and_prediction_has_no_label():
    train = TrainingSet((example("1", 1, 3, 3, feature=1), example("2", 2, 4, 4, feature=3)), 10)
    standardizer = Standardizer.fit(train)
    future = Features("test", "TOKEN", 11, 11, ("x",), (1000.0,))
    assert standardizer.means == (2.0,)
    assert standardizer.transform(future) == (998.0,)
    model = EmpiricalForecaster()
    model.fit(train)
    predicted = model.predict(future)
    assert predicted.mean == pytest.approx(0.1)
    assert pinball(predicted, Target("test", 20, 20, 0.1, "raw_return_h10")) == 0
    with pytest.raises(ValidationError, match="cutoff"):
        model.predict(train.examples[0].features)


def test_censoring_is_not_silently_dropped():
    e = example("censored", 1, 5, 5)
    e = replace(e, target=replace(e.target, value=None, censored_reason="no_exit"))
    with pytest.raises(ValidationError, match="censored"):
        EmpiricalForecaster().fit(TrainingSet((e,), 10))


def test_walkforward_and_quantile_crossing():
    folds = walk_forward(start_ns=0, train_ns=10, test_ns=5, gap_ns=2, end_ns=22)
    assert folds == (Fold(0, 10, 12, 17), Fold(5, 15, 17, 22))
    with pytest.raises(ValidationError, match="crossing"):
        Forecast("1", "return", 0.0, ((0.1, 1.0), (0.9, -1.0)))


def test_bootstrap_reproducibility_and_insufficient_blocks():
    values = tuple(float(i) for i in range(20))
    assert block_bootstrap_mean_interval(values, block_size=4, seed=7) == (
        block_bootstrap_mean_interval(values, block_size=4, seed=7)
    )
    with pytest.raises(ValidationError):
        block_bootstrap_mean_interval((1.0,), block_size=1, seed=0)


def test_high_pnl_is_not_enough_for_promotion():
    evidence = PromotionEvidence(False, False, True, False, True, 1000)
    assert set(promotion_blockers(evidence, 0)) == {
        "real_oos",
        "execution_calibrated",
        "paper_complete",
    }
