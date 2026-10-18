"""Offline B01 reconstruction from installed wheel; new outputs, no provider requests."""

import argparse
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-path", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.installed_path.resolve()))
    import torch

    import memecoin_brain
    from memecoin_brain.data import build, corpus, load_dataset
    from memecoin_brain.model import ensemble_predict, predict, train
    from memecoin_brain.shadow import evaluate_prediction
    from memecoin_ml.data import read, sha, write
    from memecoin_registry.contracts import seal, verify

    assert Path(memecoin_brain.__file__).resolve().is_relative_to(args.installed_path.resolve())
    base = Path(__file__).resolve().parents[1] / "work/brain-b01"
    root = base / "dev01"
    args.output.mkdir(exist_ok=False)
    original = load_dataset(base / "dataset-initial-01")
    rows, provenance = corpus(root, original["provenance"]["as_of_ns"])
    assert build(rows, provenance) == original
    model = root / "models/000000"
    verify(model)
    card = read(model / "model-card.json")
    replay = args.output / "model"
    retrained = train(base / "dataset-initial-01", replay)
    for key in ("baseline", "bounds", "candidates", "selected", "test", "source_identity"):
        assert card[key] == retrained[key], key
    for kind in ("linear", "gru"):
        for seed in (7, 19):
            name = f"{kind}-{seed}"
            first = torch.load(model / name / "weights.pt", weights_only=True)
            second = torch.load(replay / name / "weights.pt", weights_only=True)
            assert all(torch.equal(first[k], second[k]) for k in first), name
    errors, prediction_hashes, outcome_hashes = [], {}, {}
    now = time.time_ns()
    current_rows, _ = corpus(root, now)
    for path in sorted((root / "shadow/predictions").glob("*.json")):
        p = read(path)
        artifact = Path(p["model_path"])
        assert artifact.resolve().is_relative_to((root / "models").resolve())
        assert sha(artifact / "manifest.json") == p["model_sha"]
        sample = dict(
            pool=p["pool"],
            event_close_s=p["event_close_s"],
            actual_available_ns=p["input_available_ns"],
            history=p["history"],
            targets=[dict(horizon=p["horizon"], log_return=None, available_ns=None)],
        )
        bundle = dict(frames=p["input_frames"])
        selected = predict(artifact, bundle, [sample])[0]
        values = {"empirical": read(artifact / "model-card.json")["baseline"][str(p["horizon"])]}
        for kind in ("linear", "gru"):
            values[kind] = ensemble_predict(artifact, kind, bundle, [sample])[0]
        for key, value in {"selected": selected, **values}.items():
            saved = p["q"] if key == "selected" else p["q_by_candidate"][key]
            errors.extend(abs(a - b) for a, b in zip(value, saved, strict=True))
        prediction_hashes[path.name] = sha(path)
        outcome = root / "shadow/outcomes" / path.name
        if outcome.exists():
            o = read(outcome)
            assert o["prediction_sha"] == sha(path)
            scored = o.get("scored_ns", o.get("observed_ns"))
            available = [r for r in current_rows if r["available_ns"] <= scored]
            reproduced = evaluate_prediction(p, available, now_ns=scored)
            assert o == dict(prediction_sha=sha(path), **reproduced)
            outcome_hashes[outcome.name] = sha(outcome)
    assert errors and max(errors) < 1e-5
    result = dict(
        track="brain_development",
        installed_package=str(Path(memecoin_brain.__file__).resolve()),
        dataset_rebuilt_exact=True,
        four_fit_weights_tensor_exact=True,
        validation_selection_test_exact=True,
        reproduction_fits=4,
        new_selection_trials=0,
        prediction_count=len(prediction_hashes),
        outcome_count=len(outcome_hashes),
        max_prediction_absolute_error=max(errors),
        tolerance=1e-5,
        prediction_hashes=prediction_hashes,
        outcome_hashes=outcome_hashes,
        original_dataset_manifest_sha=sha(base / "dataset-initial-01/manifest.json"),
        original_model_manifest_sha=sha(model / "manifest.json"),
        network_requests=0,
        F01_exposures=0,
        completed_ns=time.time_ns(),
    )
    write(args.output / "result.json", result)
    seal(args.output)
    print({k: v for k, v in result.items() if not k.endswith("_hashes")})


if __name__ == "__main__":
    main()
