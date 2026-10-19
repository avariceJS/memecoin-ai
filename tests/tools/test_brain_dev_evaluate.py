"""Bounded tests for paired B13 evaluation and its frozen empirical compatibility path."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def tool():
    pytest.importorskip("torch")
    path = Path(__file__).resolve().parents[2] / "tools/brain_dev_evaluate.py"
    spec = importlib.util.spec_from_file_location("brain_dev_evaluate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evaluate_uses_pinned_b13_support_and_marks_empirical_fallback(tool, monkeypatch, tmp_path):
    candidate, dataset, champion = (
        tmp_path / name for name in ("candidate", "dataset", "champion")
    )
    candidate_card = {
        "track": "brain_development",
        "confirmatory_F01": False,
        "dataset_sha": "dataset-sha",
        "recipe": tool.RECIPE,
        "selected": "gru",
        "support": {"validation": {"membership": "val-pin"}, "test": {"membership": "test-pin"}},
    }
    champion_card = {
        "track": "brain_development",
        "confirmatory_F01": False,
        "source_identity": {"older": "source"},
        "selected": "empirical",
        "baseline": {"1": [-0.1, 0.0, 0.1], "3": [-0.2, 0.0, 0.2]},
    }
    validation_samples = [{"id": "validation", "targets": [{"horizon": 1, "log_return": 0.0}]}]
    test_samples = [{"id": "test", "targets": [{"horizon": 1, "log_return": 0.0}]}]
    bundle = {"track": "brain_development", "F01": False, "provenance": {"as_of_ns": 123}}
    monkeypatch.setattr(tool, "load_dataset", lambda path: bundle)
    monkeypatch.setattr(tool, "verify", lambda path: None)
    monkeypatch.setattr(
        tool,
        "_read_card",
        lambda path: candidate_card if path == candidate else champion_card,
    )
    monkeypatch.setattr(
        tool,
        "sha",
        lambda path: "dataset-sha" if path == dataset / "manifest.json" else str(path),
    )
    monkeypatch.setattr(tool, "identity", lambda: {"current": "source"})
    monkeypatch.setattr(
        tool,
        "partitions",
        lambda bundle, cutoff: (
            {"validation": validation_samples, "test": test_samples},
            {"train_end_s": 1},
        ),
    )
    monkeypatch.setattr(tool, "bounded", lambda values, cap: values)
    monkeypatch.setattr(
        tool,
        "membership",
        lambda values: {
            "membership": "val-pin" if values[0]["id"] == "validation" else "test-pin",
            "decisions": 1,
        },
    )
    predictions = []

    def predict(path, _bundle, values):
        predictions.append(path)
        return [[-0.05, 0.0, 0.05]]

    monkeypatch.setattr(tool, "predict", predict)
    monkeypatch.setattr(
        tool, "scoring", lambda _samples, qs: {"pinball": sum(abs(x) for x in qs[0])}
    )
    monkeypatch.setattr(tool.torch, "set_num_threads", lambda n: None)

    result = tool.evaluate(candidate, dataset, champion)

    assert predictions == [candidate, candidate]
    assert result["fits"] == 0
    assert result["promoted"] is False
    assert result["folds"]["test"]["support"]["membership"] == "test-pin"
    assert result["folds"]["test"]["champion_prediction_method"] == (
        "frozen_empirical_card_fallback_source_identity_mismatch"
    )
    assert result["folds"]["test"]["champion"]["pinball"] == pytest.approx(0.2)


def test_non_empirical_old_champion_is_rejected_without_predict(tool, monkeypatch, tmp_path):
    monkeypatch.setattr(tool, "identity", lambda: {"current": "source"})
    monkeypatch.setattr(tool, "predict", lambda *args: pytest.fail("must preserve source guard"))
    with pytest.raises(ValueError, match="champion_source_identity_mismatch"):
        tool._champion_predictions(
            tmp_path / "champion",
            {"source_identity": {"old": "source"}, "selected": "gru"},
            {},
            [],
        )


def test_protected_dataset_is_rejected(tool, monkeypatch, tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("brain-evaluation")
    paths = [tmp_path / name for name in ("candidate", "dataset", "champion")]
    monkeypatch.setattr(
        tool,
        "load_dataset",
        lambda _path: {"track": "brain_development", "F01": True, "provenance": {"as_of_ns": 1}},
    )
    monkeypatch.setattr(tool, "verify", lambda _path: None)
    with pytest.raises(ValueError, match="dataset_not_unprotected_brain_development"):
        tool.evaluate(*paths)


@pytest.mark.parametrize("name", ["brain-f01-exam", "brain_d43_capture", "reserved-corpus"])
def test_protected_paths_are_rejected_before_loading(tool, monkeypatch, tmp_path_factory, name):
    tmp_path = tmp_path_factory.mktemp("brain-evaluation")
    monkeypatch.setattr(tool, "load_dataset", lambda *_: pytest.fail("protected payload read"))
    with pytest.raises(ValueError, match="protected_input_path"):
        tool.evaluate(tmp_path / "candidate", tmp_path / name, tmp_path / "champion")


def test_output_must_be_new_and_outside_input_artifact(tool, tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    output = artifact / "result.json"
    with pytest.raises(ValueError, match="output_inside_input_artifact"):
        tool._check_output(output, (artifact,))


def test_output_is_create_only(tool, tmp_path):
    output = tmp_path / "already-exists.json"
    output.write_text("{}")
    with pytest.raises(ValueError, match="immutable_output_exists"):
        tool._check_output(output, ())
    with pytest.raises(FileExistsError):
        tool.write(output, {"replacement": True})
