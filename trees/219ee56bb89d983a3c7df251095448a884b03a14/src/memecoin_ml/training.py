"""Bounded engineering trainer, epoch-boundary resume and label-free inference."""

from __future__ import annotations

import math
import os
import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch
from torch import Tensor
from torch.nn import functional as F

from memecoin_ml.data import (
    CLASSES,
    Row,
    Split,
    encode_history,
    fit_scaler,
    load_dataset,
    partition,
    seal,
    sha,
    split_dict,
    verify,
    write,
)
from memecoin_ml.models import QUANTILES, Brain, ModelConfig


def source_identity() -> dict[str, str]:
    return {p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))}


def device_for(name: str) -> torch.device:
    if name not in ("cpu", "mps"):
        raise ValueError("only explicit CPU/MPS devices supported")
    if name == "mps" and os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise ValueError("MPS CPU fallback must be disabled")
    if name == "mps" and not torch.backends.mps.is_available():
        raise ValueError("MPS unavailable; no silent fallback")
    return torch.device(name)


@dataclass(frozen=True)
class TrainConfig:
    model: ModelConfig
    seed: int = 7
    epochs: int = 3
    batch_size: int = 32
    learning_rate: float = 0.001
    device: str = "cpu"
    patience: int = 3
    mode: str = "engineering_smoke"

    def __post_init__(self) -> None:
        if self.mode != "engineering_smoke":
            raise ValueError(
                "research training requires a separately approved corpus/protocol gate"
            )
        if not 1 <= self.epochs <= 5 or not 1 <= self.batch_size <= 256 or self.patience < 1:
            raise ValueError("smoke budget exceeded")
        if self.model.width > 64 or self.model.context > 32:
            raise ValueError("smoke model budget exceeded")
        if not 0 < self.learning_rate <= 0.1 or not 0 <= self.seed < 2**32:
            raise ValueError("invalid optimizer/seed")


def tensorize(bundle: Row, samples: list[Row], scaler: Row, context: int) -> dict[str, Any]:
    estimated_bytes = sum(len(s["targets"]) for s in samples) * (context * 25 * 4 + 64)
    if estimated_bytes > 256 * 1024**2:
        raise ValueError("eager smoke tensor budget exceeded; streaming loader required")
    histories: list[list[list[float]]] = []
    lengths: list[int] = []
    actions: list[list[float]] = []
    returns: list[float] = []
    classes: list[int] = []
    masks: list[bool] = []
    times: list[int] = []
    ids: list[str] = []
    for sample in samples:
        frames = [bundle["frames"][key] for key in sample["history"]]
        history = encode_history(frames, sample["decision_ns"], scaler, context)
        length = len(history)
        padded = history + [[0.0] * len(history[0]) for _ in range(context - length)]
        for target in sample["targets"]:
            histories.append(padded)
            lengths.append(length)
            actions.append(target["action"])
            returns.append(target["return"] if target["return"] is not None else 0.0)
            classes.append(CLASSES.index(target["status"]) if target["status"] in CLASSES else -1)
            masks.append(target["return"] is not None)
            times.append(sample["decision_ns"])
            ids.append(sample["id"])
    if not histories:
        raise ValueError("empty partition after group maturity purge")
    return dict(
        history=torch.tensor(histories, dtype=torch.float32),
        lengths=torch.tensor(lengths, dtype=torch.long),
        action=torch.tensor(actions, dtype=torch.float32),
        returns=torch.tensor(returns, dtype=torch.float32),
        classes=torch.tensor(classes, dtype=torch.long),
        return_mask=torch.tensor(masks, dtype=torch.bool),
        times=times,
        ids=ids,
    )


def batch(data: dict[str, Any], indices: Tensor, device: torch.device) -> dict[str, Tensor]:
    return {
        key: value[indices].to(device) for key, value in data.items() if isinstance(value, Tensor)
    }


def losses(q: Tensor, logits: Tensor, data: dict[str, Tensor]) -> tuple[Tensor, Tensor]:
    valid = data["return_mask"]
    # Empty heads contribute a differentiable zero, not fabricated observations.
    error = data["returns"][valid, None] - q[valid]
    levels = q.new_tensor(QUANTILES)
    pinball = (
        torch.maximum(levels * error, (levels - 1) * error).mean() if valid.any() else q.sum() * 0
    )
    labeled = data["classes"] >= 0
    ce = (
        F.cross_entropy(logits[labeled], data["classes"][labeled])
        if labeled.any()
        else logits.sum() * 0
    )
    return pinball, ce


def metrics(q: Tensor, logits: Tensor, data: dict[str, Any]) -> Row:
    valid = data["return_mask"]
    labeled = data["classes"] >= 0
    pb, ce = losses(q, logits, data)
    if not torch.isfinite(q).all() or not torch.isfinite(logits).all():
        raise ValueError("nonfinite predictions")
    probabilities = logits.softmax(-1)
    brier = ((probabilities[labeled] - F.one_hot(data["classes"][labeled], 3)) ** 2).sum(1)
    return dict(
        rows=len(q),
        decisions=len(set(data["ids"])),
        labeled_classes=int(labeled.sum()),
        labeled_returns=int(valid.sum()),
        censored=int((~labeled).sum()),
        pinball=float(pb) if valid.any() else None,
        log_loss=float(ce) if labeled.any() else None,
        brier=float(brier.mean()) if labeled.any() else None,
        quantile_coverage=[
            float((data["returns"][valid] <= q[valid, j]).float().mean()) for j in range(3)
        ]
        if valid.any()
        else None,
        class_counts={name: int((data["classes"] == i).sum()) for i, name in enumerate(CLASSES)},
    )


@torch.no_grad()
def evaluate(model: Brain, data: dict[str, Any], device: torch.device, batch_size: int) -> Row:
    model.eval()
    qs, ls = [], []
    for start in range(0, len(data["history"]), batch_size):
        b = batch(data, torch.arange(start, min(start + batch_size, len(data["history"]))), device)
        q, logits = model(b["history"], b["lengths"], b["action"])
        qs.append(q.cpu())
        ls.append(logits.cpu())
    q, logits = torch.cat(qs), torch.cat(ls)
    result = metrics(q, logits, data)
    times = sorted(set(data["times"]))
    # Descriptive temporal blocks, not independent observations or significance tests.
    blocks = []
    for k in range(3):
        chosen = set(times[len(times) * k // 3 : len(times) * (k + 1) // 3])
        indices = torch.tensor(
            [i for i, t in enumerate(data["times"]) if t in chosen], dtype=torch.long
        )
        if len(indices):
            subset: dict[str, Any] = batch(data, indices, torch.device("cpu"))
            subset["ids"] = [data["ids"][i] for i in indices.tolist()]
            blocks.append(
                dict(
                    start_ns=min(chosen),
                    end_ns=max(chosen),
                    metrics=metrics(q[indices], logits[indices], subset),
                )
            )
    result["temporal_blocks"] = blocks
    return result


def empirical(train: dict[str, Any], test: dict[str, Any]) -> Row:
    """Action-conditioned training-only baseline with explicit pooled fallback."""
    qs, logits = [], []
    fallbacks = 0
    for action in test["action"]:
        matching = (train["action"] == action).all(1)
        valid = matching & train["return_mask"]
        labeled = matching & (train["classes"] >= 0)
        if not valid.any():
            valid = train["return_mask"]
            fallbacks += 1
        if not labeled.any():
            labeled = train["classes"] >= 0
        if not valid.any() or not labeled.any():
            raise ValueError("empirical baseline has no observed training labels")
        qs.append(torch.quantile(train["returns"][valid], torch.tensor(QUANTILES)))
        counts = torch.bincount(train["classes"][labeled], minlength=3).float() + 1.0
        logits.append(counts.log())
    result = metrics(torch.stack(qs), torch.stack(logits), test)
    result["pooled_return_fallback_rows"] = fallbacks
    return result


def _binding(dataset: Path, split: Split, config: TrainConfig) -> Row:
    recipe = asdict(config)
    recipe.pop("epochs")  # Only requested final epoch may increase on resume.
    return dict(
        dataset_sha256=sha(dataset / "dataset.json"),
        split=split_dict(split),
        recipe=recipe,
        source=source_identity(),
        torch=str(torch.__version__),
    )


def _torch_save(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        torch.save(value, stream)


def load_checkpoint(root: Path, filename: str = "last.pt") -> Row:
    verify(root)
    checkpoint: Row = torch.load(root / filename, map_location="cpu", weights_only=True)
    return checkpoint


def train(
    dataset: Path, output: Path, split: Split, config: TrainConfig, *, resume: Path | None = None
) -> Row:
    if output.exists():
        raise ValueError("run output already exists")
    torch.set_num_threads(1)
    torch.manual_seed(config.seed)
    torch.use_deterministic_algorithms(config.device == "cpu")
    device = device_for(config.device)
    bundle = load_dataset(dataset)
    if config.model.context > bundle["history_policy"]["context"]:
        raise ValueError("requested context exceeds exported history")
    parts = partition(bundle, split)
    scaler = fit_scaler(bundle, parts["train"])
    data = {
        name: tensorize(bundle, parts[name], scaler, config.model.context)
        for name in ("train", "validation", "test")
    }
    for name in ("train", "validation"):
        if not data[name]["return_mask"].any() or not (data[name]["classes"] >= 0).any():
            raise ValueError(f"{name} has no support for both heads")
    binding = _binding(dataset, split, config)
    model = Brain(config.model).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
    history: list[Row] = []
    best_score, stale, start = math.inf, 0, 0
    best: dict[str, Tensor] = {}
    if resume is not None:
        state = load_checkpoint(resume)
        if state["binding"] != binding or state["scaler"] != scaler:
            raise ValueError("resume dataset/config/source mismatch")
        start, history, best_score, stale = (
            state["epoch"],
            state["history"],
            state["best_score"],
            state["stale"],
        )
        if start >= config.epochs or stale >= config.patience:
            raise ValueError("resume has no remaining epochs or was early-stopped")
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        best = state["best"]
        torch.set_rng_state(state["rng"])
        if config.device == "mps":
            torch.mps.set_rng_state(state["mps_rng"])
    output.mkdir(parents=True)
    write(
        output / "protocol.json",
        dict(
            config=asdict(config),
            binding=binding,
            qualification="engineering_only",
            source=bundle["source"],
            resume_from=str(resume.resolve()) if resume else None,
            resume_manifest_sha256=sha(resume / "manifest.json") if resume else None,
            partition_decisions={k: len(v) for k, v in parts.items()},
            environment=dict(
                python=platform.python_version(),
                platform=platform.platform(),
                machine=platform.machine(),
                torch=str(torch.__version__),
                device=str(device),
                threads=torch.get_num_threads(),
            ),
        ),
    )
    try:
        for epoch in range(start, config.epochs):
            model.train()
            generator = torch.Generator().manual_seed(config.seed + epoch)
            order = torch.randperm(len(data["train"]["history"]), generator=generator)
            for offset in range(0, len(order), config.batch_size):
                indices = order[offset : offset + config.batch_size]
                b = batch(data["train"], indices, device)
                # A fully censored batch must not cause optimizer weight decay/update.
                if not (b["classes"] >= 0).any():
                    continue
                optimizer.zero_grad(set_to_none=True)
                q, logits = model(b["history"], b["lengths"], b["action"])
                pb, ce = losses(q, logits, b)
                loss = pb + ce
                if not torch.isfinite(loss):
                    raise ValueError("nonfinite loss")
                loss.backward()  # type: ignore[no-untyped-call]
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
                optimizer.step()
            validation = evaluate(model, data["validation"], device, config.batch_size)
            score = validation["pinball"] + validation["log_loss"]
            history.append(dict(epoch=epoch + 1, validation=validation))
            if score < best_score:
                best_score, stale = score, 0
                best = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            else:
                stale += 1
            state = dict(
                binding=binding,
                scaler=scaler,
                model_config=config.model.as_dict(),
                epoch=epoch + 1,
                history=history,
                best_score=best_score,
                stale=stale,
                model={k: v.detach().cpu().clone() for k, v in model.state_dict().items()},
                optimizer=optimizer.state_dict(),
                best=best,
                rng=torch.get_rng_state(),
                mps_rng=torch.mps.get_rng_state() if config.device == "mps" else None,
            )
            _torch_save(output / f"epoch-{epoch + 1:03d}.pt", state)
            if stale >= config.patience:
                break
        _torch_save(output / "last.pt", state)
        model.load_state_dict(best)
        # Test is touched only after validation selection. Smoke test is not a protected holdout.
        report = dict(
            qualification="engineering_only",
            market_conclusions_allowed=False,
            epochs=history,
            selected_validation_score=best_score,
            test=evaluate(model, data["test"], device, config.batch_size),
            empirical_test=empirical(data["train"], data["test"]),
            parameters=sum(p.numel() for p in model.parameters()),
            resumed_test_is_not_fresh_holdout=resume is not None,
        )
        write(output / "report.json", report)
        _torch_save(
            output / "inference.pt",
            dict(
                model=best,
                model_config=config.model.as_dict(),
                scaler=scaler,
                binding=binding,
                qualification="engineering_only",
                history_policy=bundle["history_policy"],
            ),
        )
        seal(output)
        return report
    except Exception as exc:
        # No provider input/credentials included. Preserve partial artifacts and stop.
        write(
            output / "failure.json",
            dict(category="ml_engineering_failure", exception=type(exc).__name__),
        )
        seal(output)
        raise


class Predictor:
    """CPU inference accepts only causal features and known scenario, never labels."""

    def __init__(self, run: Path) -> None:
        state = load_checkpoint(run, "inference.pt")
        if state["binding"]["source"] != source_identity():
            raise ValueError("inference source mismatch")
        self.scaler = state["scaler"]
        self.history_policy = state["history_policy"]
        self.model = Brain(ModelConfig(**state["model_config"]))
        self.model.load_state_dict(state["model"])
        self.model.eval()

    @torch.no_grad()
    def predict(self, frames: list[Row], decision_ns: int, action: list[float]) -> Row:
        if (
            len(action) != 4
            or action[0] not in (0, 1)
            or any(not math.isfinite(v) or v < 0 for v in action)
        ):
            raise ValueError("invalid inference action")
        context = self.model.config.context
        if any(
            b["decision_ns"] - a["decision_ns"] > self.history_policy["max_gap_ns"]
            for a, b in zip(frames[-context:], frames[-context:][1:], strict=False)
        ):
            raise ValueError("inference history crosses time gap")
        history = encode_history(frames, decision_ns, self.scaler, context)
        length = len(history)
        history += [[0.0] * len(history[0]) for _ in range(context - length)]
        q, logits = self.model(
            torch.tensor([history], dtype=torch.float32),
            torch.tensor([length]),
            torch.tensor([action], dtype=torch.float32),
        )
        if not torch.isfinite(q).all() or not torch.isfinite(logits).all():
            raise ValueError("nonfinite inference")
        return dict(
            quantiles=q[0].tolist(),
            levels=list(QUANTILES),
            conditional_execution_probabilities=logits.softmax(-1)[0].tolist(),
            classes=list(CLASSES),
            qualification="engineering_only",
        )
