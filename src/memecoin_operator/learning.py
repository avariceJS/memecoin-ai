"""B13 preregistered refit protocol. Historical tests are descriptive, never promotion."""

import itertools
import time
from collections import defaultdict
from pathlib import Path

from memecoin_brain.data import load_dataset, save_dataset
from memecoin_curriculum.archive import assemble, shards, summary
from memecoin_curriculum.design import support, weights
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import Ledger, publish

NS = 10**9
RECIPE = dict(
    version="B13-pool-day-v1",
    steps=1000,
    seeds=[7, 19],
    kinds=["linear", "gru"],
    train_cap=48000,
    evaluation_cap=6000,
    validation_hours=3,
    test_hours=3,
    selection="validation_pool_macro_1pct_simplicity",
    automatic_promotion=False,
)


def bounded(samples, cap):
    """Outcome-independent nested round-robin: pools, then UTC days, then SHA-ranked IDs."""
    pools = defaultdict(lambda: defaultdict(list))
    for s in samples:
        pools[s["pool"]][s["event_close_s"] // 86400].append(s)
    queues = {}
    for p, days in sorted(pools.items()):
        queues[p] = [
            iter(sorted(v, key=lambda s: digest([7, s["id"]]))) for _, v in sorted(days.items())
        ]
    selected = []
    cursors = defaultdict(int)
    while queues and len(selected) < cap:
        for p in list(queues):
            group = queues[p]
            while group:
                i = cursors[p] % len(group)
                value = next(group[i], None)
                if value is None:
                    group.pop(i)
                    continue
                selected.append(value)
                cursors[p] = i + 1
                break
            if not group:
                del queues[p]
            if len(selected) >= cap:
                break
    return sorted(selected, key=lambda s: (s["pool"], s["event_close_s"]))


def partitions(bundle, cutoff):
    end = cutoff // NS // 300 * 300
    bounds = dict(
        train_end_s=end - 21600,
        validation_end_s=end - 10800,
        test_end_s=end,
        group_maturity_purge=True,
        retrospective_not_online=True,
    )
    parts = dict(train=[], validation=[], test=[], purged=[])
    seen = set()
    for s in bundle["samples"]:
        if s["id"] in seen:
            raise ValueError("operator_duplicate_sample")
        seen.add(s["id"])
        if s["actual_available_ns"] > cutoff:
            raise ValueError("operator_future_input")
        for key in s["history"]:
            if bundle["frames"][key]["available_ns"] > s["actual_available_ns"]:
                raise ValueError("operator_future_frame")
        complete = all(t["log_return"] is not None for t in s["targets"])
        if complete and any(t["available_ns"] > cutoff for t in s["targets"]):
            raise ValueError("operator_future_target")
        role = "purged"
        if complete:
            for name, start, stop in [
                ("train", 0, end - 21600),
                ("validation", end - 21600, end - 10800),
                ("test", end - 10800, end),
            ]:
                if start <= s["event_close_s"] < s["maturity_s"] < stop:
                    role = name
        parts[role].append(s)
    return parts, bounds


def membership(samples):
    if not samples:
        return dict(decisions=0, pools=0, days=0, per_pool={}, membership=digest([]))
    return support(samples, weights(samples, True))


def prepare(rows, provenance, output):
    full = assemble(shards(rows, provenance), max_decisions=500000)
    parts, bounds = partitions(full, provenance["as_of_ns"])
    retained = {
        k: bounded(parts[k], RECIPE["train_cap"] if k == "train" else RECIPE["evaluation_cap"])
        for k in ("train", "validation", "test")
    }
    if any(len(retained[k]) < 16 for k in retained):
        raise ValueError("operator_insufficient_fold_support")
    samples = list(itertools.chain.from_iterable(retained.values()))
    keys = {k for s in samples for k in s["history"]}
    bundle = dict(full, samples=samples, frames={k: full["frames"][k] for k in keys})
    save_dataset(output / "dataset", bundle)
    report = dict(
        recipe=RECIPE,
        recipe_sha=digest(RECIPE),
        bounds=bounds,
        archive=summary(rows),
        original={k: membership(v) for k, v in parts.items()},
        retained={k: membership(v) for k, v in retained.items()},
        matured_samples=sum(len(parts[k]) for k in retained),
        dataset_sha=sha(output / "dataset/manifest.json"),
        as_of_ns=provenance["as_of_ns"],
        historical_test_independent=False,
        new_fits=0,
        training_pnl_used=False,
    )
    report["unseen_test_pools"] = sorted(
        {s["pool"] for s in retained["test"]} - {s["pool"] for s in retained["train"]}
    )
    publish(output / "coverage.json", report)
    return report


def train(snapshot, output, progress):
    """Fixed four fits; publish only after full validation selection and descriptive test."""
    import torch

    from memecoin_brain.data import HORIZONS, scaler
    from memecoin_brain.model import ensemble_predict, identity, scoring, tensors
    from memecoin_curriculum.design import weighted_quantile
    from memecoin_curriculum.training import choose, fit
    from memecoin_paper.runtime import atomic

    verify(snapshot)
    coverage = read(snapshot / "coverage.json")
    if coverage["recipe_sha"] != digest(RECIPE):
        raise ValueError("operator_recipe_changed")
    bundle = load_dataset(snapshot / "dataset")
    cut = bundle["provenance"]["as_of_ns"]
    if cut > time.time_ns():
        raise ValueError("operator_future_snapshot")
    parts, bounds = partitions(bundle, cut)
    if any(
        membership(parts[k])["membership"] != coverage["retained"][k]["membership"]
        for k in ("train", "validation", "test")
    ):
        raise ValueError("operator_snapshot_membership")
    output.mkdir(exist_ok=False)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    fitted = scaler(bundle, parts["train"])
    probability = weights(parts["train"], True)
    experiment = dict(train=parts["train"], validation=parts["validation"], probability=probability)
    data = tensors(bundle, parts["train"], fitted)
    validation = tensors(bundle, parts["validation"], fitted)
    baseline = {
        str(h): [
            weighted_quantile(
                [s["targets"][j]["log_return"] for s in parts["train"]], probability, q
            )
            for q in (0.1, 0.5, 0.9)
        ]
        for j, h in enumerate(HORIZONS)
    }
    binding = dict(
        recipe=RECIPE,
        dataset_sha=coverage["dataset_sha"],
        bounds=bounds,
        source_identity=identity(),
        operator_learning_sha=sha(Path(__file__)),
        trial_ids=[f"{k}-{s}" for k in RECIPE["kinds"] for s in RECIPE["seeds"]],
    )
    publish(output / "binding.json", binding)
    start_ns = time.time_ns()
    vals = {
        "empirical": [
            baseline[str(t["horizon"])] for s in parts["validation"] for t in s["targets"]
        ]
    }
    predictions = {}
    with Ledger(output / "ledger", binding) as ledger, ledger.session(850):
        for kind in RECIPE["kinds"]:
            for seed in RECIPE["seeds"]:
                name = f"{kind}-{seed}"
                atomic(
                    progress,
                    dict(
                        status="running",
                        fit=name,
                        completed_fits=len(predictions),
                        total_fits=4,
                        started_ns=start_ns,
                        at_ns=time.time_ns(),
                    ),
                )
                attempt = ledger.start(
                    name, dict(kind=kind, seed=seed), fit_limit=180, total_limit=850
                )
                clock = time.monotonic()
                try:
                    predictions[name] = fit(
                        data,
                        validation,
                        experiment,
                        fitted,
                        dict(kind=kind, seed=seed, width=16, context=8),
                        output / name,
                        clock + 175,
                        steps=RECIPE["steps"],
                    )
                    ledger.end(
                        attempt,
                        time.monotonic() - clock,
                        status="completed",
                        artifacts=dict(manifest_sha=sha(output / name / "manifest.json")),
                    )
                except Exception as exc:
                    ledger.end(
                        attempt,
                        min(180, time.monotonic() - clock),
                        status="failed",
                        artifacts=dict(error_type=type(exc).__name__),
                    )
                    raise
            vals[kind] = [
                [(a + b) / 2 for a, b in zip(x, y, strict=True)]
                for x, y in zip(predictions[f"{kind}-7"], predictions[f"{kind}-19"], strict=True)
            ]
        metrics = {k: scoring(parts["validation"], v) for k, v in vals.items()}
        selected = choose({k: v["pool_macro_pinball"] for k, v in metrics.items()})
        ledger.append("development_selection_before_test", selected=selected, validation=metrics)
        tests = {}
        for kind in ("empirical", "linear", "gru"):
            prediction = (
                [baseline[str(t["horizon"])] for s in parts["test"] for t in s["targets"]]
                if kind == "empirical"
                else ensemble_predict(output, kind, bundle, parts["test"])
            )
            tests[kind] = scoring(parts["test"], prediction)
        ledger.append(
            "descriptive_test_read",
            historical_test_independent=False,
            membership=membership(parts["test"])["membership"],
        )
        head = ledger.head
    card = dict(
        track="brain_development",
        model_version="Brain-B13-autonomous",
        selected=selected,
        baseline=baseline,
        candidates=[dict(name=k, validation=v["pool_macro_pinball"]) for k, v in metrics.items()],
        validation=metrics,
        test=tests,
        bounds=bounds,
        training_as_of_ns=cut,
        finished_ns=time.time_ns(),
        source_identity=identity(),
        dataset_sha=coverage["dataset_sha"],
        recipe=RECIPE,
        support=coverage["retained"],
        ledger_head=head,
        prospective_shadow_required=True,
        confirmatory_F01=False,
        execution_model="none; observed price log returns",
        production_trade_authorized=False,
    )
    publish(output / "model-card.json", card)
    seal(output)
    atomic(
        progress,
        dict(
            status="completed",
            completed_fits=4,
            total_fits=4,
            started_ns=start_ns,
            at_ns=time.time_ns(),
            selected=selected,
        ),
    )
    return card
