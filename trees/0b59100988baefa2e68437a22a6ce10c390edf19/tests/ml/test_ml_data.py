from __future__ import annotations

import copy
from pathlib import Path

import pytest

from memecoin_ml.data import (
    Split,
    encode_history,
    fit_scaler,
    load_dataset,
    partition,
    save_dataset,
    synthetic,
)


def test_group_maturity_purge_and_train_only_scaler(tmp_path: Path) -> None:
    synthetic(tmp_path / "data")
    bundle = load_dataset(tmp_path / "data")
    split = Split(0, 60 * 10**9, 90 * 10**9, 124 * 10**9, 10**9)
    parts = partition(bundle, split)
    assert len(parts["train"]) == 57
    assert all(s["maturity_ns"] < split.train_end_ns for s in parts["train"])
    assert all(s["decision_ns"] >= 61 * 10**9 for s in parts["validation"])
    assert len({s["id"] for p in parts.values() for s in p}) == 120
    scaler = fit_scaler(bundle, parts["train"])
    assert scaler["unique_frames"] == 57  # Not multiplied by scenarios/context overlap.
    for s in parts["test"]:
        bundle["frames"][s["history"][-1]]["values"] = [1e12] * 12
    assert fit_scaler(bundle, parts["train"]) == scaler
    # One late variant purges the entire decision, including earlier sibling labels.
    sample = parts["train"][-1]
    sample["targets"][0]["available_ns"] = split.train_end_ns
    sample["maturity_ns"] = split.train_end_ns
    assert sample in partition(bundle, split)["purged"]


def test_manifest_and_future_history_fail_closed(tmp_path: Path) -> None:
    synthetic(tmp_path / "data")
    bundle = load_dataset(tmp_path / "data")
    bad = copy.deepcopy(bundle)
    bad["frames"]["0"]["available_ns"] = 10**15
    with pytest.raises(ValueError, match="future availability"):
        save_dataset(tmp_path / "bad", bad)
    bad = copy.deepcopy(bundle)
    bad["samples"][0]["targets"][0]["return"] = 0
    with pytest.raises(ValueError, match="return mask"):
        save_dataset(tmp_path / "bad", bad)
    with (tmp_path / "data/dataset.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="checksum"):
        load_dataset(tmp_path / "data")


def test_label_free_transform_masks_and_group_boundary(tmp_path: Path) -> None:
    synthetic(tmp_path / "data")
    bundle = load_dataset(tmp_path / "data")
    scaler = fit_scaler(bundle, bundle["samples"][:30])
    frame = bundle["frames"]["0"]
    encoded = encode_history([frame], frame["decision_ns"], scaler, 8)
    assert encoded[0][3] == 0 and encoded[0][15] == 1
    with pytest.raises(ValueError, match="noncausal"):
        encode_history([frame], frame["decision_ns"] - 1, scaler, 8)
    other = copy.deepcopy(bundle["frames"]["1"])
    other["group"] = "gap"
    with pytest.raises(ValueError, match="crosses group"):
        encode_history([frame, other], other["decision_ns"], scaler, 8)
