import pytest

from memecoin_ml.data import sha
from memecoin_paper.service import exam_models
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish


def fixture(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    publish(model / "model-card.json", dict(training_as_of_ns=10, finished_ns=20))
    seal(model)
    return dict(
        frozen_exam_models=[dict(path=str(model), sha=sha(model / "manifest.json"), label="full")],
        activation_ns=30,
        exam_start_ns=30,
        training_max_runs=0,
    )


def test_exam_refuses_retraining_changed_model_or_backdated_start(tmp_path):
    c = fixture(tmp_path)
    assert exam_models(c) == [tmp_path / "model"]
    with pytest.raises(ValueError, match="bound"):
        exam_models(dict(c, training_max_runs=1))
    with pytest.raises(ValueError, match="training_boundary"):
        exam_models(dict(c, activation_ns=15, exam_start_ns=15))
    c["frozen_exam_models"][0]["sha"] = "changed"
    with pytest.raises(ValueError, match="model_changed"):
        exam_models(c)


def test_exam_rejects_duplicate_models(tmp_path):
    c = fixture(tmp_path)
    c["frozen_exam_models"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        exam_models(c)


def test_exam_does_not_issue_on_a_bar_closed_before_activation(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from memecoin_brain import shadow

    c = fixture(tmp_path)
    model = tmp_path / "model"
    root = tmp_path / "paper"
    (root / "shadow").mkdir(parents=True)
    sample = dict(id="s", pool="p", event_close_s=1500, actual_available_ns=1505 * 10**9)
    monkeypatch.setattr(shadow, "build", lambda *a: dict(samples=[sample]))
    monkeypatch.setattr(shadow.time, "time_ns", lambda: 1510 * 10**9)

    def forbidden(*args):
        raise AssertionError("old observation reached model")

    monkeypatch.setitem(
        sys.modules,
        "memecoin_brain.model",
        SimpleNamespace(predict=forbidden, ensemble_predict=forbidden),
    )
    assert c["activation_ns"] > 0
    result = shadow.tick(root, [], {}, model, prediction_start_ns=1506 * 10**9)
    assert result["new_predictions"] == 0
