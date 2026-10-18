"""Exploratory quantile training, reusing D44 encoders/loss and D47 durable trial ledger."""

from __future__ import annotations

import argparse
import math
import time
from pathlib import Path
from typing import Any

import torch

from memecoin_ml.data import read, sha, write
from memecoin_ml.models import Brain, ModelConfig
from memecoin_ml.training import losses
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import Ledger
from memecoin_runner.scoring import quantile

from .data import CONTEXT, HORIZONS, encode, fingerprint, load_dataset, scaler, split

Row = dict[str, Any]


def identity() -> Row:
    import memecoin_ml.models
    import memecoin_ml.training
    import memecoin_runner.ledger
    import memecoin_runner.scoring

    files = [Path(__file__), Path(__file__).with_name("data.py")]
    files += [
        Path(m.__file__)
        for m in (
            memecoin_ml.models,
            memecoin_ml.training,
            memecoin_runner.ledger,
            memecoin_runner.scoring,
        )
    ]
    return {f"{p.parent.name}/{p.name}": sha(p) for p in files}


def tensors(
    bundle: Row, samples: list[Row], fitted: Row
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    histories, actions, returns = [], [], []
    for sample in samples:
        history = encode(bundle, sample, fitted)
        for target in sample["targets"]:
            histories.append(history)
            actions.append([0.0, 0.0, 0.0, float(target["horizon"] * 300)])
            returns.append(
                target["log_return"] if target["log_return"] is not None else float("nan")
            )
    return (
        torch.tensor(histories, dtype=torch.float32),
        torch.tensor(actions, dtype=torch.float32),
        torch.tensor(returns, dtype=torch.float32),
    )


@torch.no_grad()
def infer(model: Brain, bundle: Row, samples: list[Row], fitted: Row) -> list[list[float]]:
    model.eval()
    output = []
    for i in range(0, len(samples), 64):
        x, action, _ = tensors(bundle, samples[i : i + 64], fitted)
        q, _ = model(x, torch.full((len(x),), CONTEXT, dtype=torch.long), action)
        if not torch.isfinite(q).all():
            raise ValueError("nonfinite_prediction")
        output.extend(q.tolist())
    return output


def scoring(samples: list[Row], predictions: list[list[float]]) -> Row:
    if len(predictions) != sum(len(s["targets"]) for s in samples):
        raise ValueError("prediction_membership")
    values = []
    per_pool = {}
    covers = [0, 0, 0]
    n = 0
    for (sample, target), q in zip(
        ((s, t) for s in samples for t in s["targets"]), predictions, strict=True
    ):
        if len(q) != 3 or q != sorted(q) or any(not math.isfinite(v) for v in q):
            raise ValueError("invalid_quantiles")
        y = target["log_return"]
        if y is None:
            continue
        v = (
            sum(
                max(level * (y - p), (level - 1) * (y - p))
                for level, p in zip((0.1, 0.5, 0.9), q, strict=True)
            )
            / 3
        )
        values.append(v)
        per_pool.setdefault(sample["pool"], []).append(v)
        covers = [a + int(y <= p) for a, p in zip(covers, q, strict=True)]
        n += 1
    if not values:
        raise ValueError("no_scoring_support")
    return dict(
        pinball=sum(values) / len(values),
        pool_macro_pinball=sum(sum(v) / len(v) for v in per_pool.values()) / len(per_pool),
        labeled_variants=n,
        decisions=len(samples),
        pools=len(per_pool),
        coverage=[v / n for v in covers],
        per_pool={p: dict(variants=len(v), pinball=sum(v) / len(v)) for p, v in per_pool.items()},
        confirmatory=False,
        edge_established=False,
    )


def fit_one(bundle: Row, parts: Row, config: Row, output: Path, *, deadline: float) -> Row:
    output.mkdir()
    torch.manual_seed(config["seed"])
    model = Brain(ModelConfig(config["kind"], 16, CONTEXT))
    fitted = scaler(bundle, parts["train"])
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.0001)
    ys = [t["log_return"] for s in parts["train"] for t in s["targets"]]
    scale = max(1e-4, quantile(ys, 0.75) - quantile(ys, 0.25))
    best = float("inf")
    best_epoch = 0
    best_weights = None
    history = []
    for epoch in range(10):
        model.train()
        order = torch.randperm(len(parts["train"])).tolist()
        for offset in range(0, len(order), 64):
            if time.monotonic() >= deadline:
                raise TimeoutError("fit_deadline")
            rows = [parts["train"][i] for i in order[offset : offset + 64]]
            x, action, y = tensors(bundle, rows, fitted)
            optimizer.zero_grad(set_to_none=True)
            q, logits = model(x, torch.full((len(x),), CONTEXT, dtype=torch.long), action)
            pinball, _ = losses(
                q,
                logits,
                dict(
                    returns=y,
                    return_mask=torch.isfinite(y),
                    classes=torch.full_like(y, -1, dtype=torch.long),
                ),
            )
            loss = pinball / scale
            if not torch.isfinite(loss):
                raise ValueError("nonfinite_loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1, error_if_nonfinite=True)
            optimizer.step()
        metrics = scoring(parts["validation"], infer(model, bundle, parts["validation"], fitted))
        value = metrics["pool_macro_pinball"]
        if value < best:
            best, best_epoch = value, epoch + 1
            best_weights = {k: v.detach().clone() for k, v in model.state_dict().items()}
        checkpoint = output / f"epoch-{epoch + 1:03d}"
        checkpoint.mkdir()
        with (checkpoint / "state.pt").open("xb") as f:
            torch.save(
                dict(
                    model=model.state_dict(),
                    optimizer=optimizer.state_dict(),
                    rng=torch.get_rng_state(),
                    best_weights=best_weights,
                    epoch=epoch + 1,
                ),
                f,
            )
        write(checkpoint / "metrics.json", metrics)
        seal(checkpoint)
        history.append(
            dict(
                epoch=epoch + 1, validation=value, checkpoint_sha=sha(checkpoint / "manifest.json")
            )
        )
    if best_weights is None:
        raise ValueError("no_checkpoint")
    with (output / "weights.pt").open("xb") as f:
        torch.save(best_weights, f)
    model.load_state_dict(best_weights)
    result = dict(
        config=config,
        best_epoch=best_epoch,
        validation=best,
        epochs=history,
        scaler=fitted,
        parameters=sum(p.numel() for p in model.parameters()),
        unused_classification_head=True,
    )
    write(output / "fit.json", result)
    seal(output)
    return result


def train(dataset: Path, output: Path) -> Row:
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    bundle = load_dataset(dataset)
    parts, bounds = split(bundle)
    now = time.time_ns()
    if bundle["provenance"]["as_of_ns"] > now:
        raise ValueError("future_training_dataset")
    output.mkdir(exist_ok=False)
    configs = [
        dict(kind=k, seed=s, width=16, context=CONTEXT) for k in ("linear", "gru") for s in (7, 19)
    ]
    binding = dict(
        track="brain_development",
        dataset_sha=sha(dataset / "manifest.json"),
        sources=identity(),
        bounds=bounds,
        trial_ids=[f"{c['kind']}-{c['seed']}" for c in configs],
        torch=str(torch.__version__),
        max_epochs=10,
    )
    write(output / "binding.json", binding)
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
        for h in HORIZONS
    }
    baseline_val = scoring(
        parts["validation"],
        [baseline[str(t["horizon"])] for s in parts["validation"] for t in s["targets"]],
    )
    candidates = [dict(name="empirical", validation=baseline_val["pool_macro_pinball"])]
    with Ledger(output / "ledger", binding) as ledger, ledger.session(900):
        for config in configs:
            name = f"{config['kind']}-{config['seed']}"
            start = ledger.start(
                name,
                dict(config=config, dataset=binding["dataset_sha"]),
                fit_limit=200,
                total_limit=900,
            )
            t = time.monotonic()
            try:
                fit_one(bundle, parts, config, output / name, deadline=t + 195)
                ledger.end(
                    start,
                    time.monotonic() - t,
                    status="completed",
                    artifacts=dict(manifest_sha=sha(output / name / "manifest.json")),
                )
            except Exception as exc:
                ledger.end(
                    start,
                    min(time.monotonic() - t, 200),
                    status="failed",
                    artifacts=dict(error_type=type(exc).__name__),
                )
                raise
        for kind in ("linear", "gru"):
            predictions = ensemble_predict(output, kind, bundle, parts["validation"])
            value = scoring(parts["validation"], predictions)["pool_macro_pinball"]
            candidates.append(dict(name=kind, validation=value, seeds=[7, 19]))
        # Exploration only. Fixed simplicity tie preference within1%; test is not selected on.
        minimum = min(c["validation"] for c in candidates)
        selected = next(c["name"] for c in candidates if c["validation"] <= minimum * 1.01)
        ledger.append("development_selection", selected=selected, candidates=candidates)
        tests = {}
        for candidate in candidates:
            name = candidate["name"]
            if name == "empirical":
                pred = [baseline[str(t["horizon"])] for s in parts["test"] for t in s["targets"]]
            else:
                pred = ensemble_predict(output, name, bundle, parts["test"])
            tests[name] = scoring(parts["test"], pred)
        ledger.append(
            "exploratory_test_read",
            membership=digest([s["id"] for s in parts["test"]]),
            candidates=[c["name"] for c in candidates],
        )
        head = ledger.head
    card = dict(
        track="brain_development",
        model_version="Brain-v1-dev",
        selected=selected,
        baseline=baseline,
        candidates=candidates,
        test=tests,
        bounds=bounds,
        trained_ns=now,
        finished_ns=time.time_ns(),
        training_as_of_ns=bundle["provenance"]["as_of_ns"],
        source_identity=identity(),
        dataset_sha=binding["dataset_sha"],
        data_fingerprint=fingerprint(bundle),
        ledger_head=head,
        prospective_shadow_required=True,
        confirmatory_F01=False,
        execution_model="none; observed price log returns",
        historical_availability_assumed=False,
        historical_event_time_evaluation=True,
        production_trade_authorized=False,
    )
    write(output / "model-card.json", card)
    seal(output)
    return card


def load_neural(path: Path, bundle: Row, samples: list[Row]) -> list[list[float]]:
    verify(path)
    fit = read(path / "fit.json")
    c = fit["config"]
    model = Brain(ModelConfig(c["kind"], c["width"], c["context"]))
    model.load_state_dict(torch.load(path / "weights.pt", map_location="cpu", weights_only=True))
    return infer(model, bundle, samples, fit["scaler"])


def predict(path: Path, bundle: Row, samples: list[Row]) -> list[list[float]]:
    verify(path)
    card = read(path / "model-card.json")
    if card["source_identity"] != identity():
        raise ValueError("model_source_changed")
    if any(s["actual_available_ns"] > time.time_ns() for s in samples):
        raise ValueError("future_shadow_input")
    if card["selected"] == "empirical":
        return [card["baseline"][str(t["horizon"])] for s in samples for t in s["targets"]]
    return ensemble_predict(path, card["selected"], bundle, samples)


def ensemble_predict(path: Path, kind: str, bundle: Row, samples: list[Row]) -> list[list[float]]:
    first = load_neural(path / f"{kind}-7", bundle, samples)
    second = load_neural(path / f"{kind}-19", bundle, samples)
    return [
        [(x + y) / 2 for x, y in zip(a, b, strict=True)] for a, b in zip(first, second, strict=True)
    ]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("dataset", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    train(a.dataset, a.output)


if __name__ == "__main__":
    main()
