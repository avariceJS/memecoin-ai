"""Paired descriptive OLD/NEW inference; no fit, no promotion, no backdated publication."""

import argparse
import time
from pathlib import Path

import torch

from memecoin_brain.data import load_dataset
from memecoin_brain.model import ensemble_predict, scoring
from memecoin_ml.data import read, sha
from memecoin_operator.learning import partitions
from memecoin_registry.contracts import digest, verify
from memecoin_runner.ledger import publish


def compare(models, bundle, samples):
    results = {}
    for label, model in models.items():
        verify(model)
        card = read(model / "model-card.json")
        results[label] = dict(
            model_sha=sha(model / "manifest.json"), selected=card["selected"], metrics={}
        )
        for horizon in [1, 3]:
            subset = [
                dict(s, targets=[t for t in s["targets"] if t["horizon"] == horizon])
                for s in samples
            ]
            subset = [
                s for s in subset if s["targets"] and s["targets"][0]["log_return"] is not None
            ]
            if not subset:
                continue
            results[label]["metrics"][str(horizon * 5)] = {}
            for family in ["empirical", "linear", "gru"]:
                pred = (
                    [card["baseline"][str(horizon)]] * len(subset)
                    if family == "empirical"
                    else ensemble_predict(model, family, bundle, subset)
                )
                results[label]["metrics"][str(horizon * 5)][family] = scoring(subset, pred)
    return dict(
        models=results, decisions=len(samples), membership=digest(sorted(s["id"] for s in samples))
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--cutoff-ns", type=int, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise ValueError("immutable_output_exists")
    torch.set_num_threads(1)
    cards = sorted(
        (read(f) for f in (a.root / "candidates").glob("*.json")), key=lambda c: c["created_ns"]
    )
    if len(cards) != 2:
        raise ValueError("exact_two_candidates_required")
    models = {
        label: Path(card["artifact"]) / "model"
        for label, card in zip(["old", "new"], cards, strict=True)
    }
    entry = read(a.root / "datasets" / (cards[-1]["dataset_id"] + ".json"))
    bundle = load_dataset(Path(entry["artifact"]) / "dataset")
    parts, bounds = partitions(bundle, entry["as_of_ns"])
    result = dict(
        schema="paired-refit-review-v1",
        cutoff_ns=a.cutoff_ns,
        evaluated_ns=time.time_ns(),
        fits=0,
        bounds=bounds,
        paired_descriptive_test=compare(models, bundle, parts["test"]),
        independent_confirmatory=False,
        promotion=False,
    )
    exam = next(
        read(f)
        for f in (a.root / "exams").glob("*.json")
        if read(f)["candidate_id"] == cards[-1]["id"]
    )
    lane = Path(exam["exam_root"]) / "candidate-shadows" / cards[-1]["model_sha"] / "shadow"
    frames, samples = {}, {}
    for path in sorted((lane / "predictions").glob("*.json")):
        pred = read(path)
        outcome = lane / "outcomes" / path.name
        if pred["predicted_ns"] > a.cutoff_ns or not outcome.exists():
            continue
        out = read(outcome)
        if out["status"] != "scored" or out["scored_ns"] > a.cutoff_ns:
            continue
        if out["prediction_sha"] != sha(path):
            raise ValueError("prediction_hash")
        for key, frame in pred["input_frames"].items():
            if key in frames and frames[key] != frame:
                raise ValueError("input_revision")
            frames[key] = frame
        s = samples.setdefault(
            pred["id"],
            dict(
                id=pred["id"],
                pool=pred["pool"],
                event_close_s=pred["event_close_s"],
                actual_available_ns=pred["input_available_ns"],
                history=pred["history"],
                targets=[],
            ),
        )
        s["targets"].append(dict(horizon=pred["horizon"], log_return=out["log_return"]))
    result["future_input_replay"] = compare(models, dict(frames=frames), list(samples.values()))
    result["future_input_replay"]["old_predictions_published_prospectively"] = False
    result["future_input_replay"]["note"] = (
        "Both bundles frozen before inputs, but OLD inference made offline now; "
        "no simulated past trades."
    )
    a.output.parent.mkdir(parents=True, exist_ok=True)
    publish(a.output, result)
    print(a.output, sha(a.output))


if __name__ == "__main__":
    main()
