"""Read-only, paired B13 descriptive evaluation of a saved candidate and frozen champion."""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Any

import torch

from memecoin_brain.data import load_dataset
from memecoin_brain.model import identity, predict, scoring
from memecoin_ml.data import read, sha, write
from memecoin_operator.learning import RECIPE, bounded, membership, partitions
from memecoin_registry.contracts import verify

Row = dict[str, Any]
EVALUATION_CAP = RECIPE["evaluation_cap"]
PROTECTED_PATH_PARTS = {"f01", "d43", "holdout", "reserved", "protected"}


def _guard_input_path(path: Path) -> None:
    for candidate in (path.absolute(), path.resolve()):
        for part in candidate.parts:
            tokens = set(re.split(r"[^a-z0-9]+", part.casefold()))
            if PROTECTED_PATH_PARTS.intersection(tokens):
                raise ValueError("protected_input_path")


def _read_card(path: Path) -> Row:
    verify(path)
    card = read(path / "model-card.json")
    if card.get("track") != "brain_development" or card.get("confirmatory_F01") is not False:
        raise ValueError("model_not_brain_development")
    return card


def _empirical_predictions(card: Row, samples: list[Row]) -> list[list[float]]:
    baseline = card.get("baseline")
    if not isinstance(baseline, dict):
        raise ValueError("missing_empirical_baseline")
    try:
        return [
            baseline[str(target["horizon"])] for sample in samples for target in sample["targets"]
        ]
    except KeyError as exc:
        raise ValueError("missing_empirical_horizon") from exc


def _champion_predictions(
    path: Path, card: Row, bundle: Row, samples: list[Row]
) -> tuple[list[list[float]], str]:
    if card.get("source_identity") == identity():
        return predict(path, bundle, samples), "current_predict"
    if card.get("selected") != "empirical":
        raise ValueError("champion_source_identity_mismatch")
    method = "frozen_empirical_card_fallback_source_identity_mismatch"
    return _empirical_predictions(card, samples), method


def evaluate(model: Path, dataset: Path, champion: Path) -> Row:
    """Evaluate exact saved B13 validation/test memberships without fitting or publishing."""
    for path in (model, dataset, champion):
        _guard_input_path(path)

    bundle = load_dataset(dataset)  # Verifies the immutable dataset manifest.
    if bundle.get("track") != "brain_development" or bundle.get("F01") is not False:
        raise ValueError("dataset_not_unprotected_brain_development")

    model_card = _read_card(model)
    champion_card = _read_card(champion)
    dataset_sha = sha(dataset / "manifest.json")
    if model_card.get("dataset_sha") != dataset_sha:
        raise ValueError("candidate_dataset_pin")
    if model_card.get("recipe") != RECIPE:
        raise ValueError("unsupported_recipe")

    parts, bounds = partitions(bundle, bundle["provenance"]["as_of_ns"])
    result: Row = {
        "schema": "brain-dev-paired-evaluation-v1",
        "evaluated_ns": time.time_ns(),
        "code_identity": identity(),
        "evaluation_tool_sha256": sha(Path(__file__)),
        "execution": {"device": "cpu", "torch": torch.__version__, "threads": 1},
        "evaluation_cap": EVALUATION_CAP,
        "dataset_sha": dataset_sha,
        "candidate_model_sha": sha(model / "manifest.json"),
        "champion_model_sha": sha(champion / "manifest.json"),
        "candidate_selected": model_card.get("selected"),
        "champion_selected": champion_card.get("selected"),
        "bounds": bounds,
        "fits": 0,
        "promoted": False,
        "confirmatory": False,
        "folds": {},
    }
    torch.set_num_threads(1)
    for fold in ("validation", "test"):
        samples = bounded(parts[fold], EVALUATION_CAP)
        support = membership(samples)
        registered = model_card.get("support", {}).get(fold, {})
        if support.get("membership") != registered.get("membership"):
            raise ValueError(f"{fold}_membership_mismatch")
        candidate = scoring(samples, predict(model, bundle, samples))
        champion_pred, champion_method = _champion_predictions(
            champion, champion_card, bundle, samples
        )
        champion_score = scoring(samples, champion_pred)
        result["folds"][fold] = {
            "support": support,
            "candidate": candidate,
            "champion": champion_score,
            "champion_prediction_method": champion_method,
        }
    return result


def _check_output(output: Path, inputs: tuple[Path, ...]) -> Path:
    resolved = output.resolve()
    if resolved.exists():
        raise ValueError("immutable_output_exists")
    for raw in inputs:
        source = raw.resolve()
        if resolved == source or source in resolved.parents:
            raise ValueError("output_inside_input_artifact")
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True, help="saved candidate model artifact")
    parser.add_argument("--dataset", type=Path, required=True, help="pinned B13 dataset artifact")
    parser.add_argument("--champion", type=Path, required=True, help="frozen champion artifact")
    parser.add_argument(
        "--output", type=Path, required=True, help="new JSON receipt path (create-only)"
    )
    args = parser.parse_args()
    inputs = (args.model, args.dataset, args.champion)
    output = _check_output(args.output, inputs)
    result = evaluate(*inputs)
    output.parent.mkdir(parents=True, exist_ok=True)
    write(output, result)
    print(f"{output} {sha(output)}")


if __name__ == "__main__":
    main()
