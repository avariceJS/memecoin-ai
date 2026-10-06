"""Prospective comparisons reject unpaired data and reproduce ledger metrics independently."""

import json

import pytest

from memecoin_curriculum.future import compare_exam
from memecoin_ml.data import sha
from memecoin_runner.ledger import publish


def exam(tmp_path):
    refs = [dict(label=name, sha=name) for name in ("control", "broad")]
    publish(tmp_path / "config.json", dict(frozen_exam_models=refs, activation_ns=300 * 10**9))
    for ref in refs:
        root = tmp_path / "candidate-shadows" / ref["sha"] / "shadow"
        (root / "predictions").mkdir(parents=True)
        (root / "outcomes").mkdir()
        for h in (1, 3):
            q = [-0.1, 0.0, 0.1]
            value = dict(pinball=(0.01 + 0 + 0.01) / 3, coverage=[False, True, True])
            p = root / "predictions" / f"{h}.json"
            publish(
                p,
                dict(
                    model_sha=ref["sha"],
                    predicted_ns=650 * 10**9,
                    input_available_ns=640 * 10**9,
                    event_close_s=600,
                    pool="pool",
                    horizon=h,
                    history=["x"],
                    input_frames=dict(x=dict(values=[0.1])),
                    origin_close=1,
                    q_by_candidate={f: q for f in ("empirical", "linear", "gru")},
                ),
            )
            publish(
                root / "outcomes" / p.name,
                dict(
                    prediction_sha=sha(p),
                    status="scored",
                    target_available_ns=(600 + 300 * h + 10) * 10**9,
                    scored_ns=2000 * 10**9,
                    log_return=0,
                    source_captures=["source"],
                    candidate_metrics={f: value for f in ("empirical", "linear", "gru")},
                ),
            )
    return tmp_path


def rewrite(path, change):
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))


def test_future_membership_and_metrics_are_exact(tmp_path):
    root = exam(tmp_path)
    result = compare_exam(root, 2000 * 10**9)
    assert result["common_variants"] == 2
    assert result["common_decisions"] == 1
    assert result["all_predictions_published_prospectively"]
    assert result["metrics"]["broad"]["15"]["gru"]["coverage"] == [0, 1, 1]
    assert compare_exam(root, 1999 * 10**9)["common_variants"] == 0


def test_future_refuses_backdated_or_unpaired_evidence(tmp_path):
    root = exam(tmp_path)
    prediction = root / "candidate-shadows/broad/shadow/predictions/1.json"
    outcome = root / "candidate-shadows/broad/shadow/outcomes/1.json"
    rewrite(prediction, lambda p: p.update(origin_close=2))
    rewrite(outcome, lambda o: o.update(prediction_sha=sha(prediction)))
    with pytest.raises(ValueError, match="input_mismatch"):
        compare_exam(root, 2000 * 10**9)
    rewrite(prediction, lambda p: p.update(origin_close=1, predicted_ns=1000 * 10**9))
    rewrite(outcome, lambda o: o.update(prediction_sha=sha(prediction)))
    with pytest.raises(ValueError, match="target_boundary"):
        compare_exam(root, 2000 * 10**9)


def test_outcome_loss_is_recomputed_not_trusted(tmp_path):
    root = exam(tmp_path)
    path = root / "candidate-shadows/broad/shadow/outcomes/1.json"

    def corrupt(o):
        o["candidate_metrics"]["gru"]["pinball"] = 0

    rewrite(path, corrupt)
    with pytest.raises(ValueError, match="metric_reproduction"):
        compare_exam(root, 2000 * 10**9)
