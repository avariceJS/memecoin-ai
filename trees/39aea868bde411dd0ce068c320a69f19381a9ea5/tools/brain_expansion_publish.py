"""Publish completed external comparison artifacts in the operator's immutable registry."""

import argparse
import json
import shutil
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_operator.adapters import register, registered
from memecoin_operator.deployment import guard
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish


def publish_comparison(root, experiment, name):
    guard(root)
    verify(experiment)
    if read(experiment / "result.json")["fits"] != 8:
        raise ValueError("comparison_incomplete")
    folder = root / "external" / name
    folder.mkdir(parents=True, exist_ok=False)
    records = {}
    for arm in ("control", "broad"):
        snapshot = folder / f"{name}-{arm}-dataset"
        shutil.copytree(experiment / (arm + "-snapshot"), snapshot)
        verify(snapshot)
        coverage = read(snapshot / "coverage.json")
        dataset = register(
            root,
            "datasets",
            snapshot,
            dict(
                status="completed",
                **coverage,
                origin="external_data_comparison",
                experiment_sha=sha(experiment / "manifest.json"),
            ),
        )
        artifact = folder / f"{name}-{arm}-candidate"
        artifact.mkdir()
        model = artifact / "model"
        shutil.copytree(experiment / (arm + "-model"), model)
        verify(model)
        card = read(model / "model-card.json")
        result = dict(
            status="completed",
            origin="external_data_comparison",
            dataset_id=dataset["id"],
            dataset_sha=dataset["dataset_sha"],
            model_sha=sha(model / "manifest.json"),
            selected=card["selected"],
            finished_ns=card["finished_ns"],
            recipe=card["recipe"],
            validation=card["validation"],
            test=card["test"],
            support=card["support"],
            promoted=False,
            new_fits=4,
            operator_reservations_consumed=0,
            experiment_sha=sha(experiment / "manifest.json"),
            trial_reservation=str(experiment / "binding.json"),
        )
        publish(artifact / "result.json", result)
        seal(artifact)
        candidate = register(root, "candidates", artifact, result)
        registered(root, "datasets", dataset["id"])
        registered(root, "candidates", candidate["id"])
        records[arm] = dict(
            dataset=dataset["id"], candidate=candidate["id"], model_sha=candidate["model_sha"]
        )
    publish(
        folder / "publication.json",
        dict(
            records=records,
            experiment=str(experiment),
            experiment_sha=sha(experiment / "manifest.json"),
            imported_fits=8,
            fits_performed_here=0,
            existing_operator_state_unchanged=True,
            automatic_promotion=False,
        ),
    )
    seal(folder)
    print(json.dumps(records))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("experiment", type=Path)
    p.add_argument("--name", default="expansion01")
    a = p.parse_args()
    from memecoin_operator.adapters import identifier

    identifier(a.name)
    publish_comparison(a.root.resolve(), a.experiment.resolve(), a.name)


if __name__ == "__main__":
    main()
