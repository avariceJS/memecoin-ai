"""Bounded matched-update curriculum experiment using the existing Brain encoders."""

import copy
import os
import threading
import time
from collections import Counter
from pathlib import Path

import torch

from memecoin_brain.data import CONTEXT, HORIZONS, load_dataset, scaler
from memecoin_brain.model import identity, scoring, tensors
from memecoin_curriculum.design import experiments, weighted_quantile
from memecoin_ml.data import read, sha
from memecoin_ml.models import Brain, ModelConfig
from memecoin_ml.training import losses
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import Ledger, publish
from memecoin_runner.scoring import quantile


@torch.no_grad()
def cached_infer(model, data):
    model.eval()
    x, actions, _ = data
    result = []
    for start in range(0, len(x), 128):
        xx = x[start : start + 128]
        q, _ = model(
            xx, torch.full((len(xx),), CONTEXT, dtype=torch.long), actions[start : start + 128]
        )
        if not torch.isfinite(q).all():
            raise ValueError("curriculum_nonfinite_prediction")
        result.extend(q.tolist())
    return result


def fit(data, validation, experiment, fitted, config, output, deadline, steps=1000):
    output.mkdir()
    torch.manual_seed(config["seed"])
    model = Brain(ModelConfig(config["kind"], 16, CONTEXT))
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.0001)
    ys = [t["log_return"] for s in experiment["train"] for t in s["targets"]]
    scale = max(1e-4, quantile(ys, 0.75) - quantile(ys, 0.25))
    generator = torch.Generator().manual_seed(config["seed"] + 10007)
    draws = torch.multinomial(
        torch.tensor(experiment["probability"], dtype=torch.float64),
        steps * 64,
        replacement=True,
        generator=generator,
    )
    best, best_weights, best_step = float("inf"), None, 0
    history = []
    x, actions, y = data
    for step in range(1, steps + 1):
        if time.monotonic() >= deadline:
            raise TimeoutError("curriculum_fit_deadline")
        model.train()
        ids = draws[(step - 1) * 64 : step * 64]
        variants = (ids[:, None] * len(HORIZONS) + torch.arange(len(HORIZONS))).flatten()
        xx, aa, yy = x[variants], actions[variants], y[variants]
        optimizer.zero_grad(set_to_none=True)
        q, logits = model(xx, torch.full((len(xx),), CONTEXT, dtype=torch.long), aa)
        pinball, _ = losses(
            q,
            logits,
            dict(
                returns=yy,
                return_mask=torch.isfinite(yy),
                classes=torch.full_like(yy, -1, dtype=torch.long),
            ),
        )
        loss = pinball / scale
        if not torch.isfinite(loss):
            raise ValueError("curriculum_nonfinite_loss")
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1, error_if_nonfinite=True)
        optimizer.step()
        if step % (steps // 10) == 0:
            metrics = scoring(experiment["validation"], cached_infer(model, validation))
            value = metrics["pool_macro_pinball"]
            if value < best:
                best, best_step = value, step
                best_weights = copy.deepcopy(model.state_dict())
            checkpoint = output / f"step-{step:06d}"
            checkpoint.mkdir()
            torch.save(
                dict(
                    model=model.state_dict(),
                    optimizer=optimizer.state_dict(),
                    rng=torch.get_rng_state(),
                    best_weights=best_weights,
                    step=step,
                ),
                checkpoint / "state.pt",
            )
            publish(checkpoint / "metrics.json", metrics)
            seal(checkpoint)
            history.append(
                dict(step=step, validation=value, manifest_sha=sha(checkpoint / "manifest.json"))
            )
    if best_weights is None:
        raise ValueError("curriculum_no_checkpoint")
    torch.save(best_weights, output / "weights.pt")
    model.load_state_dict(best_weights)
    unique = sorted(set(draws.tolist()))
    observed = [experiment["train"][i] for i in unique]
    result = dict(
        config=config,
        best_step=best_step,
        validation=best,
        checkpoints=history,
        scaler=fitted,
        steps=steps,
        sampled_decisions=steps * 64,
        unique_sampled_decisions=len(unique),
        sampled_pools=len({s["pool"] for s in observed}),
        sampled_days=len({s["event_close_s"] // 86400 for s in observed}),
        draws_sha=digest(draws.tolist()),
        sampled_per_pool=dict(Counter(experiment["train"][i]["pool"] for i in draws.tolist())),
        parameters=sum(p.numel() for p in model.parameters()),
        unused_classification_head=True,
    )
    publish(output / "fit.json", result)
    seal(output)
    return cached_infer(model, validation)


def choose(values):
    minimum = min(values.values())
    return next(k for k, v in values.items() if v <= minimum * 1.01)


def deadline_exit():
    print(dict(category="curriculum_fit_hard_deadline", no_retry=True), flush=True)
    os._exit(124)


def run(design_root, output):
    verify(design_root)
    binding = read(design_root / "binding.json")
    dataset = Path(binding["dataset"])
    if sha(dataset / "manifest.json") != binding["dataset_sha"]:
        raise ValueError("curriculum_dataset_binding")
    for name, expected in binding["code"].items():
        if sha(Path(binding["source_root"]) / name) != expected:
            raise ValueError("curriculum_code_binding")
    bundle = load_dataset(dataset)
    if bundle["provenance"]["as_of_ns"] > time.time_ns():
        raise ValueError("curriculum_future_snapshot")
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    output.mkdir(exist_ok=False)
    exps = list(experiments(bundle, binding["bounds"]))
    expected = read(design_root / "membership.json")
    for e in exps:
        assert expected[e["name"]] == e["support"]
    configs = [
        dict(kind=k, seed=s, width=16, context=8) for k in ("linear", "gru") for s in (7, 19)
    ]
    ledger_binding = dict(
        design_sha=sha(design_root / "manifest.json"),
        trial_ids=[f"{e['name']}/{c['kind']}-{c['seed']}" for e in exps for c in configs],
    )
    publish(output / "binding.json", ledger_binding)
    summaries = []
    with Ledger(output / "ledger", ledger_binding) as ledger, ledger.session(7200):
        for e in exps:
            target = output / e["name"]
            target.mkdir()
            fitted = scaler(bundle, e["train"])
            data = tensors(bundle, e["train"], fitted)
            validation = tensors(bundle, e["validation"], fitted)
            baseline = {
                str(h): [
                    weighted_quantile(
                        [s["targets"][j]["log_return"] for s in e["train"]], e["probability"], q
                    )
                    for q in (0.1, 0.5, 0.9)
                ]
                for j, h in enumerate(HORIZONS)
            }
            pred = dict(
                empirical=[
                    baseline[str(t["horizon"])] for s in e["validation"] for t in s["targets"]
                ]
            )
            per_fit = {}
            for c in configs:
                name = f"{c['kind']}-{c['seed']}"
                start = ledger.start(
                    f"{e['name']}/{name}",
                    dict(config=c, support=e["support"]),
                    fit_limit=180,
                    total_limit=7200,
                )
                clock = time.monotonic()
                timer = threading.Timer(180, deadline_exit)
                timer.daemon = True
                timer.start()
                try:
                    per_fit[name] = fit(data, validation, e, fitted, c, target / name, clock + 175)
                    ledger.end(
                        start,
                        time.monotonic() - clock,
                        status="completed",
                        artifacts=dict(manifest_sha=sha(target / name / "manifest.json")),
                    )
                except Exception as exc:
                    ledger.end(
                        start,
                        min(180, time.monotonic() - clock),
                        status="failed",
                        artifacts=dict(error_type=type(exc).__name__),
                    )
                    publish(
                        output / "STOP",
                        dict(
                            category="curriculum_fit",
                            error_type=type(exc).__name__,
                            trial=f"{e['name']}/{name}",
                            no_retry=True,
                        ),
                    )
                    raise
                finally:
                    timer.cancel()
                print(
                    dict(
                        trial=f"{e['name']}/{name}",
                        status="completed",
                        seconds=time.monotonic() - clock,
                    ),
                    flush=True,
                )
            for kind in ("linear", "gru"):
                pred[kind] = [
                    [(a + b) / 2 for a, b in zip(x, y, strict=True)]
                    for x, y in zip(per_fit[kind + "-7"], per_fit[kind + "-19"], strict=True)
                ]
            metrics = {k: scoring(e["validation"], v) for k, v in pred.items()}
            selected = choose({k: v["pool_macro_pinball"] for k, v in metrics.items()})
            summary = dict(
                name=e["name"],
                arm=e["arm"],
                fraction=e["fraction"],
                support=e["support"],
                validation=metrics,
                selected=selected,
            )
            summaries.append(summary)
            publish(target / "validation.json", summary)
            card = dict(
                track="brain_development",
                model_version="Brain-B12-curriculum",
                selected=selected,
                baseline=baseline,
                candidates=[
                    dict(name=k, validation=v["pool_macro_pinball"]) for k, v in metrics.items()
                ],
                bounds=binding["bounds"],
                training_as_of_ns=bundle["provenance"]["as_of_ns"],
                finished_ns=time.time_ns(),
                source_identity=identity(),
                dataset_sha=binding["dataset_sha"],
                design_sha=ledger_binding["design_sha"],
                curriculum=e["name"],
                support=e["support"],
                prospective_shadow_required=True,
                confirmatory_F01=False,
                execution_model="none; observed price log returns",
                production_trade_authorized=False,
            )
            publish(target / "model-card.json", card)
            seal(target)
        eligible = {
            s["name"]: s["validation"][s["selected"]]["pool_macro_pinball"]
            for s in summaries
            if s["fraction"] == 100
        }
        selected = choose(eligible)
        selection = dict(
            arm=selected,
            metrics=eligible,
            criterion="validation_only_within1pct_order",
            model_sha=sha(output / selected / "manifest.json"),
        )
        ledger.append("development_selection_before_test", **selection)
        publish(output / "selection.json", selection)
        # No test labels/scores are consulted for training, checkpoint or curriculum selection.
        from memecoin_brain.model import ensemble_predict

        test_records = []
        for e in exps:
            target = output / e["name"]
            card = read(target / "model-card.json")
            preds = dict(
                empirical=[
                    card["baseline"][str(t["horizon"])] for s in e["test"] for t in s["targets"]
                ]
            )
            for kind in ("linear", "gru"):
                preds[kind] = ensemble_predict(target, kind, bundle, e["test"])
            scores = {k: scoring(e["test"], v) for k, v in preds.items()}
            test_records.append(dict(name=e["name"], scores=scores))
            publish(output / (e["name"] + "-test-predictions.json"), preds)
        ledger.append(
            "exploratory_test_read",
            membership=digest([s["id"] for s in exps[0]["test"]]),
            trials=[e["name"] for e in exps],
        )
    head = ledger.head
    result = dict(
        selection=selection,
        curves=summaries,
        test=test_records,
        ledger_head=head,
        finished_ns=time.time_ns(),
        fits=36,
        new_provider_requests=0,
        edge_established=False,
    )
    publish(output / "result.json", result)
    seal(output)
    return result
