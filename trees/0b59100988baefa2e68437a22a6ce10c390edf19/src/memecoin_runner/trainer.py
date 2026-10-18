"""Optional CPU trainer. Streaming tensors; epoch-boundary exact resume."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import torch

from memecoin_ml.data import fit_scaler, read, sha
from memecoin_ml.models import Brain, ModelConfig
from memecoin_ml.training import losses, tensorize
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import publish
from memecoin_runner.scoring import return_scale, score

Row = dict[str, Any]


def entries(samples: list[Row]) -> list[Row]:
    return [{**s, "targets": [t]} for s in samples for t in s["targets"]]


@torch.no_grad()
def predict(model: Brain, bundle: Row, samples: list[Row], scaler: Row) -> list[Row]:
    model.eval()
    flat = entries(samples)
    result: list[Row] = []
    for start in range(0, len(flat), 64):
        rows = flat[start : start + 64]
        b = tensorize(bundle, rows, scaler, 8)
        q, logits = model(b["history"], b["lengths"], b["action"])
        p = logits.softmax(-1)
        if not torch.isfinite(q).all() or not torch.isfinite(p).all():
            raise ValueError("nonfinite_predictions")
        result.extend(
            dict(id=s["id"], action=s["targets"][0]["action"], q=qi, p=pi)
            for s, qi, pi in zip(rows, q.tolist(), p.tolist(), strict=True)
        )
    return result


def fit(
    bundle: Row,
    train: list[Row],
    validation: list[Row],
    config: Row,
    output: Path,
    *,
    identity: Row,
    pools: list[str],
    max_epochs: int,
    fixed_epochs: bool = False,
    stop_after_epoch: int | None = None,
) -> Row:
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    identity = dict(
        identity=identity,
        torch=str(torch.__version__),
        config=config,
        train=digest(train),
        validation=digest(validation),
        fixed_epochs=fixed_epochs,
        max_epochs=max_epochs,
        pools=pools,
    )
    output.mkdir(parents=True, exist_ok=True)
    if (output / "identity.json").exists():
        if read(output / "identity.json") != identity:
            raise ValueError("checkpoint_identity_changed")
    else:
        publish(output / "identity.json", identity)
    if (output / "result.json").exists():
        raise ValueError("fit_already_completed")
    torch.manual_seed(config["seed"])
    model = Brain(ModelConfig(config["kind"], config["width"], 8))
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.0001)
    scaler, scale = fit_scaler(bundle, train), return_scale(train)
    flat = entries(train)
    if not flat or (not validation and not fixed_epochs):
        raise ValueError("empty_training_or_validation")
    start, best_epoch, best_score, stale = 1, 0, float("inf"), 0
    best_weights: Any = None
    checkpoints = sorted(output.glob("epoch-*"))
    previous = digest(identity)
    last = None
    for i, path in enumerate(checkpoints, 1):
        verify(path)
        meta = read(path / "meta.json")
        if (
            path.name != f"epoch-{i:03d}"
            or meta["previous"] != previous
            or meta["identity"] != digest(identity)
        ):
            raise ValueError("checkpoint_chain_changed")
        previous = sha(path / "manifest.json")
        last = path
    if last is not None:
        saved = torch.load(last / "state.pt", map_location="cpu", weights_only=True)
        model.load_state_dict(saved["model"])
        optimizer.load_state_dict(saved["optimizer"])
        torch.set_rng_state(saved["rng"])
        start = saved["epoch"] + 1
        best_epoch, best_score, stale = saved["best_epoch"], saved["best_score"], saved["stale"]
        best_weights = saved["best_weights"]
    for epoch in range(start, max_epochs + 1):
        if not fixed_epochs and stale >= 10:
            break
        model.train()
        order = torch.randperm(len(flat)).tolist()
        for first in range(0, len(order), 64):
            rows = [flat[i] for i in order[first : first + 64]]
            b = tensorize(bundle, rows, scaler, 8)
            if not b["return_mask"].any() and not (b["classes"] >= 0).any():
                continue
            optimizer.zero_grad(set_to_none=True)
            q, logits = model(b["history"], b["lengths"], b["action"])
            pb, ce = losses(q, logits, b)
            loss = pb / scale + ce
            if not torch.isfinite(loss):
                raise ValueError("nonfinite_training_loss")
            loss.backward()  # type: ignore[no-untyped-call]
            norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
            if not torch.isfinite(norm):
                raise ValueError("nonfinite_gradient")
            optimizer.step()
        val_predictions = predict(model, bundle, validation, scaler) if validation else []
        report = (
            score(
                validation,
                {"candidate": val_predictions},
                pools=pools,
                scale=scale,
                fold="validation",
            )
            if validation
            else None
        )
        value = report["aggregate"]["candidate"]["joint"] if report else 0.0
        if value is None:
            raise ValueError("missing_validation_head_or_cohort")
        if fixed_epochs or value < best_score:
            best_epoch, best_score, stale = epoch, value, 0
            best_weights = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            stale += 1
        directory = output / f"epoch-{epoch:03d}"
        directory.mkdir(exist_ok=False)
        with (directory / "state.pt").open("xb") as stream:
            torch.save(
                dict(
                    model=model.state_dict(),
                    optimizer=optimizer.state_dict(),
                    rng=torch.get_rng_state(),
                    epoch=epoch,
                    best_weights=best_weights,
                    best_epoch=best_epoch,
                    best_score=best_score,
                    stale=stale,
                    scaler=scaler,
                    scale=scale,
                ),
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        publish(
            directory / "meta.json",
            dict(previous=previous, identity=digest(identity), epoch=epoch, validation=report),
        )
        seal(directory)
        previous = sha(directory / "manifest.json")
        if stop_after_epoch == epoch:
            return dict(status="suspended", checkpoint_sha256=previous)
    if best_weights is None:
        raise ValueError("no_checkpoint")
    model.load_state_dict(best_weights)
    predictions = predict(model, bundle, validation, scaler) if validation else []
    result = dict(
        status="completed",
        best_epoch=best_epoch,
        score=best_score,
        checkpoint_sha256=previous,
        identity_sha256=digest(identity),
        validation_predictions=predictions,
        scale=scale,
        scaler=scaler,
        parameters=sum(p.numel() for p in model.parameters()),
    )
    publish(output / "result.json", result)
    return result


def load_predict(output: Path, bundle: Row, samples: list[Row]) -> list[Row]:
    result = read(output / "result.json")
    checkpoint = sorted(output.glob("epoch-*"))[-1]
    verify(checkpoint)
    if sha(checkpoint / "manifest.json") != result["checkpoint_sha256"]:
        raise ValueError("inference_checkpoint_changed")
    identity = read(output / "identity.json")
    if digest(identity) != result["identity_sha256"]:
        raise ValueError("inference_identity_changed")
    config = identity["config"]
    model = Brain(ModelConfig(config["kind"], config["width"], 8))
    saved = torch.load(checkpoint / "state.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(saved["best_weights"])
    return predict(model, bundle, samples, saved["scaler"])
