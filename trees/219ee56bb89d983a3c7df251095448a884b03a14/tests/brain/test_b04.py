import copy

import pytest

from memecoin_brain.data import FEATURES, build, encode, scaler
from memecoin_brain_b04.normalization import apply, fit


def fixture():
    rows = [
        dict(
            pool="fixture",
            open_s=i * 300,
            ohlcv=[10, 12, 9, 10 + (i % 7) * 0.01, 100 + i],
            available_ns=100000 * 10**9,
            capture_sha="fixture",
        )
        for i in range(220)
    ]
    return build(rows, dict(as_of_ns=100001 * 10**9, kind="synthetic_fixture"))


def test_transform_does_not_learn_from_future_features_labels_or_repeated_windows():
    bundle = fixture()
    train = bundle["samples"][:80]
    expected = fit(bundle, train)
    assert fit(bundle, train + train) == expected
    keys = {k for s in train for k in s["history"]}
    for k, f in bundle["frames"].items():
        if k not in keys:
            f["values"] = [1e12] * len(FEATURES)
    for sample in bundle["samples"]:
        sample["targets"] = [dict(log_return=1e10)]
    assert fit(bundle, train) == expected


def test_clipping_preserves_raw_labels_none_masks_and_age():
    bundle = fixture()
    original = copy.deepcopy(bundle)
    train = bundle["samples"][:80]
    transform = fit(bundle, train)
    derived = apply(bundle, transform)
    assert bundle == original and derived["samples"] == bundle["samples"]
    for key, frame in derived["frames"].items():
        old = bundle["frames"][key]
        assert [v is None for v in frame["values"]] == [v is None for v in old["values"]]
        assert frame["values"][10:] == old["values"][10:]
        assert frame["available_ns"] == old["available_ns"]
    fitted = scaler(derived, train)
    sample = derived["samples"][-1]
    actual = encode(derived, sample, fitted)
    unmodified = encode(bundle, sample, scaler(bundle, train))
    assert [r[12:] for r in actual] == [r[12:] for r in unmodified]


def test_normalization_rejects_nonfinite_or_contract_mismatch():
    bundle = fixture()
    train = bundle["samples"][:80]
    fitted = fit(bundle, train)
    fitted["features"].reverse()
    with pytest.raises(ValueError, match="normalization_contract"):
        apply(bundle, fitted)
    bundle["frames"][train[0]["history"][0]]["values"][0] = float("nan")
    with pytest.raises(ValueError, match="nonfinite_training_feature"):
        fit(bundle, train)
