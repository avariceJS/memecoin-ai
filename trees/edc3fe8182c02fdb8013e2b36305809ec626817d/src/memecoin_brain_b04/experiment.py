"""Sixteen frozen paired fits; B03 evaluation is exposed, never a confirmatory holdout."""

import time
from pathlib import Path

from memecoin_brain.data import load_dataset
from memecoin_brain.model import ensemble_predict, fit_one, identity, load_neural, scoring
from memecoin_brain_scale.audit import paired_summary
from memecoin_brain_scale.curves import folds, records
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import Ledger
from memecoin_runner.scoring import quantile

from .normalization import apply, fit


def run(dataset: Path, control: Path, output: Path):
    import torch

    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    verify(control)
    bundle = load_dataset(dataset)
    designs = [e for e in folds(bundle) if e["fraction"] == 100]
    if sha(dataset / "manifest.json") != read(control / "binding.json")["dataset_sha"]:
        raise ValueError("B03_dataset_binding")
    output.mkdir(exist_ok=False)
    binding = dict(
        dataset_sha=sha(dataset / "manifest.json"),
        control_sha=sha(control / "manifest.json"),
        sources=identity(),
        normalization_source=sha(Path(__file__).with_name("normalization.py")),
        runner_source=sha(Path(__file__)),
        seeds=[7, 19],
        epochs=10,
        max_fits=16,
        trial_ids=[
            f"{e['name']}/{a}/{k}-{s}"
            for e in designs
            for a in ["control", "winsor"]
            for k in ["linear", "gru"]
            for s in [7, 19]
        ],
        F01=False,
        execution_model="none",
        evaluation_already_exposed=True,
        memberships={e["name"]: e["membership"] for e in designs},
    )
    write(output / "binding.json", binding)
    results = []
    with Ledger(output / "ledger", binding) as ledger, ledger.session(600):
        for e in designs:
            parts = e["parts"]
            folder = output / e["name"]
            folder.mkdir()
            baseline = {
                h: [
                    quantile(
                        [
                            t["log_return"]
                            for s in parts["train"]
                            for t in s["targets"]
                            if t["horizon"] == h
                        ],
                        q,
                    )
                    for q in [0.1, 0.5, 0.9]
                ]
                for h in [1, 3]
            }
            values = {
                role: {
                    "empirical": [baseline[t["horizon"]] for s in parts[role] for t in s["targets"]]
                }
                for role in ["validation", "evaluation"]
            }
            transforms = {}
            seed_scores = {}
            control_exact = True
            for arm in ["control", "winsor"]:
                target = folder / arm
                target.mkdir()
                transform = (
                    fit(bundle, parts["train"]) if arm == "winsor" else dict(kind="identity")
                )
                transforms[arm] = transform
                write(target / "transform.json", transform)
                transformed = apply(bundle, transform) if arm == "winsor" else bundle
                for kind in ["linear", "gru"]:
                    for seed in [7, 19]:
                        name = f"{kind}-{seed}"
                        cfg = dict(
                            kind=kind,
                            seed=seed,
                            width=16,
                            context=8,
                            normalization_sha=sha(target / "transform.json"),
                        )
                        start = ledger.start(
                            f"{e['name']}/{arm}/{name}",
                            dict(config=cfg),
                            fit_limit=200,
                            total_limit=600,
                        )
                        began = time.monotonic()
                        try:
                            fit_one(transformed, parts, cfg, target / name, deadline=began + 195)
                            ledger.end(
                                start,
                                time.monotonic() - began,
                                status="completed",
                                artifacts=dict(manifest_sha=sha(target / name / "manifest.json")),
                            )
                        except Exception as exc:
                            ledger.end(
                                start,
                                min(200, time.monotonic() - began),
                                status="failed",
                                artifacts=dict(error_type=type(exc).__name__),
                            )
                            raise
                        if arm == "control":
                            a = torch.load(target / name / "weights.pt", weights_only=True)
                            b = torch.load(
                                control / e["name"] / name / "weights.pt", weights_only=True
                            )
                            if any(not torch.equal(a[k], b[k]) for k in a):
                                raise ValueError("B03_control_weights_not_exact")
                for role in values:
                    for kind in ["linear", "gru"]:
                        values[role][kind + "_" + arm] = ensemble_predict(
                            target, kind, transformed, parts[role]
                        )
                for role in values:
                    for kind in ["linear", "gru"]:
                        for seed in [7, 19]:
                            seed_scores[f"{role}/{arm}/{kind}-{seed}"] = scoring(
                                parts[role],
                                load_neural(target / f"{kind}-{seed}", transformed, parts[role]),
                            )
                seal(target)
            metrics = {
                role: {k: scoring(parts[role], v) for k, v in pred.items()}
                for role, pred in values.items()
            }
            comparison = {}
            for role in values:
                comparison[role] = {}
                for kind in ["linear", "gru"]:
                    paired = records(
                        parts[role],
                        dict(
                            empirical=values[role][kind + "_control"],
                            winsor=values[role][kind + "_winsor"],
                        ),
                    )
                    comparison[role][kind] = dict(
                        overall=paired_summary(paired),
                        by_horizon={
                            str(h): paired_summary([r for r in paired if r["horizon"] == h])
                            for h in [1, 3]
                        },
                    )
            entry = dict(
                name=e["name"],
                membership=e["membership"],
                counts={k: len(v) for k, v in parts.items()},
                control_weights_exact=control_exact,
                transforms=transforms,
                seed_scores=seed_scores,
                metrics=metrics,
                paired=comparison,
            )
            write(folder / "predictions.json", values)
            write(folder / "result.json", entry)
            seal(folder)
            results.append(entry)
            ledger.append("paired_fold_complete", manifest_sha=sha(folder / "manifest.json"))
            print(
                dict(
                    fold=e["name"],
                    validation={
                        k: v["pool_macro_pinball"] for k, v in metrics["validation"].items()
                    },
                ),
                flush=True,
            )
        decision = {}
        for kind in ["linear", "gru"]:
            improvements = [
                1
                - e["metrics"]["validation"][kind + "_winsor"]["pool_macro_pinball"]
                / e["metrics"]["validation"][kind + "_control"]["pool_macro_pinball"]
                for e in results
            ]
            decision[kind] = dict(
                validation_improvements=improvements,
                merits_future_shadow=sum(improvements) / len(improvements) >= 0.01
                and min(improvements) >= -0.01,
            )
        ledger.append("validation_only_development_decision", decision=decision, promoted=False)
        head = ledger.head
    report = dict(
        binding_sha=sha(output / "binding.json"),
        experiments=results,
        decision=decision,
        ledger_head=head,
        neural_fits=16,
        promoted=False,
        confirmatory=False,
    )
    write(output / "result.json", report)
    seal(output)
    return report


def reproduce(dataset: Path, original: Path, output: Path):
    import torch

    torch.set_num_threads(1)
    verify(original)
    bundle = load_dataset(dataset)
    if sha(dataset / "manifest.json") != read(original / "binding.json")["dataset_sha"]:
        raise ValueError("dataset_changed")
    for e in [e for e in folds(bundle) if e["fraction"] == 100]:
        folder = original / e["name"]
        expected = read(folder / "result.json")
        saved = read(folder / "predictions.json")
        assert e["membership"] == expected["membership"]
        for arm in ["control", "winsor"]:
            target = folder / arm
            verify(target)
            transform = read(target / "transform.json")
            if arm == "winsor":
                assert transform == fit(bundle, e["parts"]["train"])
            transformed = apply(bundle, transform) if arm == "winsor" else bundle
            for role in ["validation", "evaluation"]:
                for kind in ["linear", "gru"]:
                    for seed in [7, 19]:
                        assert read(target / f"{kind}-{seed}" / "fit.json")["config"][
                            "normalization_sha"
                        ] == sha(target / "transform.json")
                    pred = ensemble_predict(target, kind, transformed, e["parts"][role])
                    assert pred == saved[role][kind + "_" + arm]
                    assert (
                        scoring(e["parts"][role], pred)
                        == expected["metrics"][role][kind + "_" + arm]
                    )
        for role in ["validation", "evaluation"]:
            for kind in ["linear", "gru"]:
                paired = records(
                    e["parts"][role],
                    dict(
                        empirical=saved[role][kind + "_control"],
                        winsor=saved[role][kind + "_winsor"],
                    ),
                )
                assert paired_summary(paired) == expected["paired"][role][kind]["overall"]
    output.mkdir(exist_ok=False)
    report = dict(
        original_sha=sha(original / "manifest.json"),
        normalization_fit_exact=True,
        memberships_predictions_metrics_bootstrap_exact=True,
        neural_fits=0,
        network_requests=0,
    )
    write(output / "result.json", report)
    seal(output)
    return report


if __name__ == "__main__":
    import argparse
    import os
    import sys

    from memecoin_operational.supervisor import Watch, child
    from memecoin_operational.worker import lease_watchdog

    p = argparse.ArgumentParser()
    p.add_argument("dataset", type=Path)
    p.add_argument("control", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--worker", action="store_true")
    a = p.parse_args()
    if a.worker:
        lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
        run(a.dataset, a.control, a.output)
    else:
        watch = Watch(600)

        def guard():
            if watch.remaining() <= 0:
                raise ValueError("B04_wall_budget")

        result = child(
            [
                sys.executable,
                "-B",
                "-m",
                "memecoin_brain_b04.experiment",
                str(a.dataset),
                str(a.control),
                str(a.output),
                "--worker",
            ],
            a.output.with_suffix(".log"),
            600,
            guard,
        )
        write(a.output.with_suffix(".process.json"), result)
        print(result)
