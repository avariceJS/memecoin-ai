"""Fixed-fold nested data curves, identical candidate/seed budget; no champion promotion."""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from memecoin_brain.data import BAR, load_dataset, scaler
from memecoin_ml.data import sha, write
from memecoin_registry.contracts import digest, seal
from memecoin_runner.ledger import Ledger
from memecoin_runner.scoring import quantile

from .audit import paired_summary


def folds(bundle: dict) -> list[dict]:
    times = sorted({s["event_close_s"] for s in bundle["samples"]})
    if len(times) < 100:
        raise ValueError("curve_time_support")
    result = []
    for i, (a, b, c) in enumerate(((50, 65, 80), (70, 85, 100))):
        cut = [
            times[0],
            times[len(times) * a // 100],
            times[len(times) * b // 100],
            times[len(times) * c // 100] if c < 100 else times[-1] + BAR,
        ]
        parts = {k: [] for k in ("train", "validation", "evaluation", "purged")}
        for sample in bundle["samples"]:
            role = "purged"
            for j, name in enumerate(("train", "validation", "evaluation")):
                if cut[j] <= sample["event_close_s"] < sample["maturity_s"] < cut[j + 1] and all(
                    t["log_return"] is not None for t in sample["targets"]
                ):
                    role = name
            parts[role].append(sample)
        train_times = sorted({s["event_close_s"] for s in parts["train"]})
        for fraction in (25, 50, 100):
            if not train_times:
                raise ValueError("empty_curve_train")
            start = train_times[len(train_times) * (100 - fraction) // 100]
            subset = [s for s in parts["train"] if s["event_close_s"] >= start]
            result.append(
                dict(
                    name=f"fold{i}-size{fraction}",
                    fold=i,
                    fraction=fraction,
                    cutoffs=cut,
                    train_start_s=start,
                    parts={**parts, "train": subset},
                    membership={
                        k: digest([s["id"] for s in v])
                        for k, v in {**parts, "train": subset}.items()
                    },
                )
            )
    return result


def records(samples: list, predictions: dict) -> list:
    out = []
    for index, (sample, target) in enumerate((s, t) for s in samples for t in s["targets"]):
        losses = {}
        for name, qs in predictions.items():
            q = qs[index]
            y = target["log_return"]
            losses[name] = (
                sum(
                    max(level * (y - p), (level - 1) * (y - p))
                    for level, p in zip((0.1, 0.5, 0.9), q, strict=True)
                )
                / 3
            )
        out.append(
            dict(
                pool=sample["pool"],
                block=sample["event_close_s"] // 3600,
                horizon=target["horizon"],
                losses=losses,
            )
        )
    return out


def feature_diagnostics(bundle, parts):
    fitted = scaler(bundle, parts["train"])
    result = {}
    for role in ("train", "validation", "evaluation"):
        keys = {k for s in parts[role] for k in s["history"]}
        channels = []
        for j, name in enumerate(bundle["feature_names"]):
            values = [bundle["frames"][k]["values"][j] for k in keys]
            observed = [v for v in values if v is not None]
            zs = [abs((v - fitted["mean"][j]) / fitted["scale"][j]) for v in observed]
            channels.append(
                dict(
                    name=name,
                    missing=len(values) - len(observed),
                    max_abs_z=max(zs, default=0),
                    above10=sum(z > 10 for z in zs),
                    above100=sum(z > 100 for z in zs),
                    frames=len(keys),
                )
            )
        result[role] = channels
    return result


def run(dataset: Path, output: Path) -> dict:
    import torch

    from memecoin_brain.model import ensemble_predict, fit_one, identity, load_neural, scoring

    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    bundle = load_dataset(dataset)
    if bundle["provenance"]["as_of_ns"] > time.time_ns():
        raise ValueError("future_curve_dataset")
    experiments = folds(bundle)
    output.mkdir(exist_ok=False)
    binding = dict(
        track="Brain_B02_exploratory_curves",
        dataset_sha=sha(dataset / "manifest.json"),
        sources=identity(),
        curve_source_sha=sha(Path(__file__)),
        seeds=[7, 19],
        epochs=10,
        max_fits=24,
        execution_model="none; observed price log returns",
        F01=False,
        folds=[{k: v for k, v in e.items() if k != "parts"} for e in experiments],
    )
    write(output / "binding.json", binding)
    results = []
    with Ledger(output / "ledger", binding) as ledger, ledger.session(600):
        for e in experiments:
            parts = e["parts"]
            if any(len(parts[k]) < 16 for k in ("train", "validation", "evaluation")):
                results.append(dict(name=e["name"], status="insufficient_support"))
                continue
            target = output / e["name"]
            target.mkdir()
            fits = []
            baseline = {
                str(h): [
                    quantile(
                        [
                            t["log_return"]
                            for s in parts["train"]
                            for t in s["targets"]
                            if t["horizon"] == h
                        ],
                        q,
                    )
                    for q in (0.1, 0.5, 0.9)
                ]
                for h in (1, 3)
            }
            seed_scores = {}
            for kind in ("linear", "gru"):
                for seed in (7, 19):
                    config = dict(kind=kind, seed=seed, width=16, context=8)
                    name = f"{kind}-{seed}"
                    start = ledger.start(
                        f"{e['name']}/{name}", dict(config=config), fit_limit=200, total_limit=600
                    )
                    began = time.monotonic()
                    try:
                        fit = fit_one(
                            bundle,
                            {**parts, "test": parts["evaluation"]},
                            config,
                            target / name,
                            deadline=began + 195,
                        )
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
                    fits.append(
                        dict(
                            kind=kind,
                            seed=seed,
                            best_epoch=fit["best_epoch"],
                            elapsed_seconds=time.monotonic() - began,
                        )
                    )
            scores = {}
            for role in ("train", "validation", "evaluation"):
                samples = parts[role]
                pred = dict(
                    empirical=[baseline[str(t["horizon"])] for s in samples for t in s["targets"]]
                )
                for kind in ("linear", "gru"):
                    pred[kind] = ensemble_predict(target, kind, bundle, samples)
                    if role != "train":
                        for seed in (7, 19):
                            name = f"{kind}-{seed}"
                            seed_scores[f"{role}/{name}"] = scoring(
                                samples, load_neural(target / name, bundle, samples)
                            )
                scores[role] = {k: scoring(samples, v) for k, v in pred.items()}
                if role == "evaluation":
                    paired = records(samples, pred)
                    comparison = dict(
                        overall=paired_summary(paired),
                        by_horizon={
                            str(h): paired_summary([r for r in paired if r["horizon"] == h])
                            for h in (1, 3)
                        },
                    )
                    write(
                        target / "evaluation-predictions.json",
                        dict(ids=[s["id"] for s in samples], predictions=pred),
                    )
            entry = dict(
                name=e["name"],
                status="completed",
                fold=e["fold"],
                fraction=e["fraction"],
                cutoffs=e["cutoffs"],
                train_start_s=e["train_start_s"],
                membership=e["membership"],
                counts={k: len(v) for k, v in parts.items()},
                pool_counts={k: dict(Counter(s["pool"] for s in v)) for k, v in parts.items()},
                fits=fits,
                metrics=scores,
                seed_scores=seed_scores,
                paired=comparison,
                feature_diagnostics=feature_diagnostics(bundle, parts),
                fixed_epochs_not_fixed_optimizer_steps=True,
                champion_changed=False,
            )
            write(target / "result.json", entry)
            seal(target)
            ledger.append(
                "curve_completed", name=e["name"], manifest_sha=sha(target / "manifest.json")
            )
            results.append(entry)
            print(
                json.dumps(
                    dict(
                        curve=e["name"],
                        train=len(parts["train"]),
                        evaluation={
                            k: v["pool_macro_pinball"] for k, v in scores["evaluation"].items()
                        },
                    )
                ),
                flush=True,
            )
        head = ledger.head
    result = dict(
        experiments=results,
        ledger_head=head,
        confirmatory=False,
        architecture_selected=False,
        dataset_sha=binding["dataset_sha"],
        completed_ns=time.time_ns(),
    )
    write(output / "result.json", result)
    seal(output)
    return result


if __name__ == "__main__":
    import argparse
    import os
    import sys

    from memecoin_operational.supervisor import Watch, child
    from memecoin_operational.worker import lease_watchdog

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.worker:
        lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
        run(args.dataset, args.output)
    else:
        watch = Watch(600)

        def guard():
            if watch.remaining() <= 0:
                raise ValueError("curve_wall_budget")

        process = child(
            [
                sys.executable,
                "-B",
                "-m",
                "memecoin_brain_scale.curves",
                str(args.dataset),
                str(args.output),
                "--worker",
            ],
            args.output.with_suffix(".log"),
            600,
            guard,
        )
        write(args.output.with_suffix(".process.json"), process)
        print(json.dumps(process))
