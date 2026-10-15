"""Frozen synthetic signal qualification for the research GRU path."""

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from memecoin_experience import evaluation  # noqa: E402
from memecoin_experience.learning import train  # noqa: E402
from memecoin_experience.storage import build, file_hash  # noqa: E402

PREREG = (
    Path(__file__).parents[1]
    / "docs/evidence/brain-controlled-signal-learnability-prereg-2026-10-15.json"
)
PREREG_PIN = hashlib.sha256(PREREG.read_bytes()).hexdigest()
QUANTILES = np.array([0.1, 0.5, 0.9])


def _schema():
    return dict(
        schema="brain-experience-v2",
        layout="dense-sequence-f64-v1",
        context=2,
        features=[dict(name="binary_signal", unit="indicator", modality="synthetic_control")],
        targets=[
            dict(
                name="mature_next_window_event_count",
                unit="events",
                kind="observed_process_count@synthetic_control",
            )
        ],
        representation=dict(
            name="frozen-controlled-signal",
            version="1",
            code_sha=hashlib.sha256(b"brain-controlled-signal-learnability-v1").hexdigest(),
        ),
        availability="point_in_time",
    )


def _rows(*, start, count, role, censored_positive=0):
    rows = []
    for index in range(count):
        decision_s = start + index
        signal = index % 2
        decision_ns = decision_s * 10**9
        maturity_ns = decision_ns + 10**9
        is_censored = role == "train" and signal == 1 and index < 2 * censored_positive
        input_pin = hashlib.sha256(f"{role}:controlled-input".encode()).hexdigest()
        target_pin = hashlib.sha256(f"{role}:controlled-target".encode()).hexdigest()
        row = dict(
            id=f"{role}:entity:{decision_s:06d}",
            pool=f"synthetic-controlled-signal/{role}/{decision_s:06d}",
            decision_ns=decision_ns,
            input_start_ns=decision_ns - 1,
            input_available_ns=decision_ns,
            input_dependencies=[
                dict(
                    artifact_sha=input_pin,
                    event_ns=decision_ns - 1,
                    available_ns=decision_ns,
                )
            ],
            values=[[float(signal)], [None]],
            targets=[None if is_censored else 4 * signal],
            target_status=["censored" if is_censored else "observed"],
            target_available_ns=[None if is_censored else maturity_ns],
            target_maturity_ns=[maturity_ns],
            target_captures=[[] if is_censored else [target_pin]],
            target_details=[
                dict(lower_bound=2, lower_bound_available_ns=decision_ns) if is_censored else {}
            ],
        )
        rows.append(row)
    return rows


def _pinball(truth, prediction):
    error = np.asarray(truth)[:, None] - np.asarray(prediction)
    return float(np.maximum(QUANTILES * error, (QUANTILES - 1) * error).mean())


def test_frozen_controlled_signal_is_learnable_and_registered(tmp_path, monkeypatch):
    declaration = json.loads(PREREG.read_text())
    assert declaration["recipe"]["seed"] == 19
    schema = _schema()
    train_rows = sorted(
        _rows(start=1, count=96, role="train", censored_positive=8)
        + _rows(start=121, count=32, role="validation")
        + _rows(start=161, count=32, role="test"),
        key=lambda row: (row["pool"], row["decision_ns"], row["id"]),
    )
    train_store = tmp_path / "train-store"
    train_build = build(
        train_store,
        train_rows,
        sources=[dict(kind="synthetic-controlled-signal", pin="c" * 64)],
        schema=schema,
        cutoff=200 * 10**9,
        role="train",
    )
    split = dict(train_start_s=0, train_end_s=120, validation_end_s=160, test_end_s=200)
    config = dict(
        width=8,
        batch=16,
        epochs=100,
        seed=19,
        device="cpu",
        cpu_threads=1,
        lr=0.01,
        objective="partial_count_lower_bound_pinball_v1",
        model_kind="gru-quantiles-v1",
    )
    fit = train(train_store, train_build["snapshot"], tmp_path / "fit", plan=split, config=config)
    artifact = tmp_path / "fit/artifact"
    artifact_pin = file_hash(artifact / "manifest.json")

    eval_rows = _rows(start=210, count=64, role="research-evaluation")
    eval_store = tmp_path / "evaluation-store"
    eval_build = build(
        eval_store,
        eval_rows,
        sources=[dict(kind="synthetic-controlled-signal-evaluation", pin="d" * 64)],
        schema=schema,
        cutoff=300 * 10**9,
        role="research_evaluation",
    )
    protocol = evaluation.register(
        tmp_path / "protocols",
        artifact,
        artifact_pin,
        start_s=210,
        end_s=300,
        pools=[row["pool"] for row in eval_rows],
        scope="retrospective",
    )
    scored = evaluation.score(
        tmp_path / "protocols",
        protocol["protocol"],
        eval_store,
        eval_build["snapshot"],
        artifact,
        tmp_path / "score",
    )
    metric = scored["metrics"][0]
    prediction_lines = (tmp_path / "score/predictions.jsonl").read_text().splitlines()
    predictions = [json.loads(line) for line in prediction_lines]
    prediction_values = np.asarray([row["prediction"][0] for row in predictions])
    assert np.isfinite(prediction_values).all()
    assert metric["n"] == 64
    assert scored["enrollment"] == 64 and scored["supported_pools"] == 64

    truth = np.asarray([4 * (index % 2) for index in range(64)], dtype=float)
    oracle = np.column_stack([truth, truth, truth])
    oracle_loss = _pinball(truth, oracle)
    model_loss = metric["pinball"]
    baseline_loss = metric["empirical_pinball"]
    receipt = dict(
        version="brain-synthetic-learnability-result-v1",
        preregistration_pin=PREREG_PIN,
        train_snapshot=train_build["snapshot"],
        evaluation_snapshot=eval_build["snapshot"],
        training_source_pin="c" * 64,
        evaluation_source_pin="d" * 64,
        artifact_pin=artifact_pin,
        protocol_pin=protocol["protocol"],
        recipe=config,
        training_population=fit["statistics"]["population"],
        training_denominators=fit["statistics"]["denominators"],
        evaluation_population=scored["population"],
        evaluation_denominators=scored["denominators"],
        metrics=dict(
            evaluation_exact_n=metric["n"],
            model_raw_count_pinball=model_loss,
            train_empirical_raw_count_pinball=baseline_loss,
            known_oracle_raw_count_pinball=oracle_loss,
            finite_predictions=bool(np.isfinite(prediction_values).all()),
        ),
        outcome="pass"
        if metric["n"] >= 60
        and model_loss <= 0.25 * baseline_loss
        and model_loss <= oracle_loss + 0.25
        and np.isfinite(prediction_values).all()
        else "fail",
        interpretation="synthetic conditional learnability only; no market or production claim",
    )
    (tmp_path / "result.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")

    assert receipt["outcome"] == "pass", json.dumps(receipt["metrics"], sort_keys=True)
    assert metric["n"] >= 60
    assert model_loss <= 0.25 * baseline_loss
    assert model_loss <= oracle_loss + 0.25
    assert math.isfinite(model_loss) and math.isfinite(baseline_loss)
