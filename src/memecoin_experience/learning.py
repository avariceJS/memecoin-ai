"""Full-coverage streaming research training. Optional torch/numpy, no AUTO integration."""

import copy
import json
import math
import os
import platform
import random
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from torch import nn

from .records import FEATURES, HORIZONS, membership, split_plan
from .storage import (
    Snapshot,
    atomic_json,
    code_identity,
    digest,
    lock,
    safe_path,
    seal_files,
    sync_dir,
    verify_files,
)

QUANTILES = (0.1, 0.5, 0.9)


def recipe(config):
    defaults = dict(
        width=64,
        batch=256,
        epochs=10,
        seed=20261007,
        device="cpu",
        lr=0.001,
        checkpoint_every=100,
        cpu_threads=1,
    )
    if set(config) - set(defaults):
        raise ValueError("experience_training_config")
    c = dict(defaults, **config)
    for key, low, high in (
        ("width", 4, 4096),
        ("batch", 1, 4096),
        ("epochs", 1, 100000),
        ("seed", 0, 2**31 - 1),
        ("checkpoint_every", 1, 100000),
        ("cpu_threads", 1, 256),
    ):
        if type(c[key]) is not int or not low <= c[key] <= high:
            raise ValueError("experience_training_config")
    if (
        not isinstance(c["device"], str)
        or c["device"] not in {"cpu", "cuda"}
        or type(c["lr"]) not in (int, float)
        or not 0 < c["lr"] <= 0.1
    ):
        raise ValueError("experience_training_config")
    return c


def batches(snapshot, plan, role, batch, *, seed=None, epoch=0, cursor=(0, 0)):
    """Permutation of partitions then rows: exactly once per role per epoch, bounded payload."""
    order = list(range(len(snapshot.parts)))
    if seed is not None:
        random.Random(f"{seed}:{epoch}:partitions").shuffle(order)
    xs, ys, ms, ids = [], [], [], []

    def packed(next_cursor):
        return np.concatenate(xs), np.concatenate(ys), np.concatenate(ms), ids, next_cursor

    for position in range(cursor[0], len(order)):
        index = order[position]
        path, rows = snapshot.partition(index)
        chosen, masks = [], []
        for i, row in enumerate(rows):
            row_role, mask = membership(row, plan)
            if row_role == role and any(mask):
                chosen.append(i)
                masks.append(mask)
        if not chosen:
            continue
        indices = list(range(len(chosen)))
        if seed is not None:
            random.Random(f"{seed}:{epoch}:{snapshot.parts[index]['pin']}").shuffle(indices)
        # mmap only selected role's slices, after metadata establishes role membership.
        x = np.memmap(
            path / "x.f64",
            dtype="<f8",
            mode="r",
            shape=(len(rows), snapshot.schema["context"], len(FEATURES)),
        )
        y = np.memmap(path / "y.f64", dtype="<f8", mode="r", shape=(len(rows), len(HORIZONS)))
        offset = cursor[1] if position == cursor[0] else 0
        while offset < len(indices):
            take = indices[offset : offset + batch - len(ids)]
            selected = [chosen[i] for i in take]
            xs.append(np.array(x[selected]))
            ys.append(np.array(y[selected]))
            ms.append(np.array([masks[i] for i in take], dtype=bool))
            ids.extend(rows[i]["id"] for i in selected)
            offset += len(take)
            if len(ids) == batch:
                next_cursor = (position + 1, 0) if offset == len(indices) else (position, offset)
                yield packed(next_cursor)
                xs, ys, ms, ids = [], [], [], []
        del x, y
    if ids:
        yield packed((len(order), 0))


def prepare(snapshot, plan, output, batch):
    """Train-only Welford statistics and exact empirical quantiles via disposable disk sort."""
    counts = np.zeros(len(FEATURES), dtype=np.int64)
    means, m2 = np.zeros(len(FEATURES)), np.zeros(len(FEATURES))
    population = Counter()
    denominators = Counter()
    for i in range(len(snapshot.parts)):
        _, rows = snapshot.partition(i)
        for row in rows:
            role, mask = membership(row, plan)
            population[role] += 1
            population[role + "_usable"] += int(any(mask))
            for h, valid, status in zip(HORIZONS, mask, row["target_status"], strict=True):
                denominators[f"{role}/{h}/{status}"] += 1
                denominators[f"{role}/{h}/usable"] += int(valid)
    fd, name = tempfile.mkstemp(prefix=".baseline-", suffix=".sqlite", dir=output)
    os.close(fd)
    path = Path(name)  # Unique scratch: a killed SQLite journal can never contaminate a retry.
    db = sqlite3.connect(path)
    try:
        db.execute("pragma temp_store=FILE")
        db.execute("pragma cache_size=-8192")
        db.execute("create table labels(h integer, y real)")
        for x, y, mask, _, _ in batches(snapshot, plan, "train", batch):
            flat = x.reshape(-1, len(FEATURES))
            for j in range(len(FEATURES)):
                observed = flat[:, j][np.isfinite(flat[:, j])]
                n = len(observed)
                if not n:
                    continue
                mean = float(observed.mean())
                delta, total = mean - means[j], counts[j] + n
                m2[j] += float(((observed - mean) ** 2).sum()) + delta**2 * counts[j] * n / total
                means[j] += delta * n / total
                counts[j] = total
            with db:
                db.executemany(
                    "insert into labels values(?,?)",
                    (
                        (h, float(y[i, h]))
                        for i in range(len(y))
                        for h in range(len(HORIZONS))
                        if mask[i, h]
                    ),
                )
        db.execute("create index ordered_labels on labels(h,y)")
        baseline = []
        for h in range(len(HORIZONS)):
            n = db.execute("select count(*) from labels where h=?", (h,)).fetchone()[0]
            if not n:
                raise ValueError("experience_empty_training_horizon")
            baseline.append(
                [
                    db.execute(
                        "select y from labels where h=? order by y limit 1 offset ?",
                        (h, max(0, math.ceil(q * n) - 1)),
                    ).fetchone()[0]
                    for q in QUANTILES
                ]
            )
    finally:
        db.close()
        path.unlink(missing_ok=True)
    if any(not population[r + "_usable"] for r in ("train", "validation", "test")):
        raise ValueError("experience_empty_role")
    return dict(
        mean=means.tolist(),
        scale=np.maximum(np.sqrt(m2 / np.maximum(counts, 1)), 1e-6).tolist(),
        feature_observations=counts.tolist(),
        baseline=baseline,
        population=dict(population),
        denominators=dict(denominators),
        normalization="train_usable_decision_frames_population_variance",
        sampling="uniform_usable_origin_once_per_epoch_partition_shuffle",
        loss="macro_mean_of_observed_horizon_pinball_means",
    )


class Network(nn.Module):
    def __init__(self, width):
        super().__init__()
        self.gru = nn.GRU(len(FEATURES) * 2 + 1, width, batch_first=True)
        self.head = nn.Linear(width, len(HORIZONS) * 3)

    def forward(self, x):
        states, _ = self.gru(x)
        p = self.head(states[:, -1]).reshape(-1, len(HORIZONS), 3)
        center = p[:, :, 0]
        return torch.stack(
            (
                center - nn.functional.softplus(p[:, :, 1]),
                center,
                center + nn.functional.softplus(p[:, :, 2]),
            ),
            dim=-1,
        )


def tensors(x, y, mask, stats, device):
    if np.isinf(x).any() or not np.isfinite(y[mask]).all():
        raise ValueError("experience_nonfinite_learning_input")
    present = np.isfinite(x)
    normalized = np.where(present, (x - np.array(stats["mean"])) / stats["scale"], 0)
    age = np.broadcast_to(
        np.linspace(-1, 0, x.shape[1])[None, :, None], (x.shape[0], x.shape[1], 1)
    )
    inputs = np.concatenate((np.clip(normalized, -8, 8), present.astype(float), age), axis=2)
    return (
        torch.tensor(inputs, dtype=torch.float32, device=device),
        torch.tensor(np.where(mask, y, 0), dtype=torch.float32, device=device),
        torch.tensor(mask, dtype=torch.bool, device=device),
    )


def loss(prediction, target, mask):
    error = target[:, :, None] - prediction
    q = prediction.new_tensor(QUANTILES)
    values = torch.maximum(q * error, (q - 1) * error).mean(dim=-1)
    counts = mask.sum(dim=0)
    return ((values * mask).sum(dim=0) / counts.clamp(min=1))[counts > 0].mean()


def evaluate(model, snapshot, plan, role, stats, config):
    accum = {name: np.zeros((len(HORIZONS), 3)) for name in ("model", "empirical")}
    model.eval()
    with torch.no_grad():
        for x, y, mask, _, _ in batches(snapshot, plan, role, config["batch"]):
            tx, _, _ = tensors(x, y, mask, stats, config["device"])
            predictions = dict(
                model=model(tx).cpu().numpy(),
                empirical=np.broadcast_to(stats["baseline"], (*y.shape, 3)),
            )
            for name, p in predictions.items():
                for h in range(len(HORIZONS)):
                    truth, pred = y[mask[:, h], h], p[mask[:, h], h]
                    error = truth[:, None] - pred
                    acc = accum[name][h]
                    acc[0] += len(truth)
                    acc[1] += (
                        np.maximum(np.array(QUANTILES) * error, (np.array(QUANTILES) - 1) * error)
                        .mean(axis=1)
                        .sum()
                    )
                    acc[2] += ((truth >= pred[:, 0]) & (truth <= pred[:, 2])).sum()
    result = {}
    for name, matrix in accum.items():
        result[name] = [
            dict(
                horizon=h,
                n=int(n),
                pinball=float(p / n) if n else None,
                coverage=float(c / n) if n else None,
            )
            for h, (n, p, c) in zip(HORIZONS, matrix, strict=True)
        ]
    if any(r["n"] == 0 for r in result["model"]):
        raise ValueError("experience_empty_evaluation_horizon")
    result["selection_score"] = sum(r["pinball"] for r in result["model"]) / len(HORIZONS)
    if not math.isfinite(result["selection_score"]):
        raise ValueError("experience_nonfinite_metric")
    return result


def save_checkpoint(path, state):
    fd, name = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            torch.save(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_dir(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def train(store, pin, output, *, plan, config=None, stop_after=None):
    """Research-only job. stop_after is an absolute one-shot test interrupt; omit on resume."""
    output, plan, config = safe_path(output), split_plan(plan), recipe(config or {})
    output.mkdir(parents=True, exist_ok=True)
    if not (output / "recipe.json").exists() and any(
        p.name != ".training.lock" for p in output.iterdir()
    ):
        raise ValueError("experience_unowned_training_directory")
    snapshot = Snapshot(store, pin)
    binding = dict(
        snapshot=pin,
        plan=plan,
        config=config,
        code=code_identity(),
        environment=dict(
            torch=str(torch.__version__),
            numpy=np.__version__,
            python=platform.python_version(),
            machine=platform.machine(),
            device=config["device"],
            cuda=torch.version.cuda,
        ),
    )
    if config["device"] == "cuda":
        if not torch.cuda.is_available():
            raise ValueError("experience_cuda_unavailable")
        binding["environment"]["accelerator"] = torch.cuda.get_device_name()
    with lock(output / ".training.lock"):
        atomic_json(output / "recipe.json", binding)
        stats_path = safe_path(output / "statistics.json")
        bound_path = safe_path(output / "binding.json")
        if bound_path.exists():
            bound = json.loads(bound_path.read_text())
            stats = json.loads(stats_path.read_text())
            if bound != dict(binding, statistics=digest(stats)):
                raise ValueError("experience_statistics_binding")
        else:
            # statistics.json without a final binding is an uncommitted derived file.
            stats = prepare(snapshot, plan, output, config["batch"])
            atomic_json(stats_path, stats, replace=True)
        binding = dict(binding, statistics=digest(stats))
        atomic_json(output / "binding.json", binding)
        artifact = output / "artifact"
        if artifact.exists():
            verify_files(artifact)
            report = json.loads((artifact / "report.json").read_text())
            if report["binding"] != binding:
                raise ValueError("experience_artifact_binding")
            return report
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        torch.set_num_threads(config["cpu_threads"])
        torch.manual_seed(config["seed"])
        torch.use_deterministic_algorithms(True)
        torch.backends.cudnn.benchmark = False
        torch.backends.cuda.matmul.allow_tf32 = False
        model = Network(config["width"]).to(config["device"])
        optimizer = torch.optim.AdamW(model.parameters(), lr=config["lr"])
        checkpoint = safe_path(output / "checkpoint.pt")
        state = dict(
            binding=digest(binding),
            statistics=digest(stats),
            epoch=0,
            cursor=(0, 0),
            steps=0,
            rows_seen=0,
            best_score=None,
            best=None,
            history=[],
        )
        if checkpoint.exists():
            saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
            if saved["binding"] != state["binding"] or saved["statistics"] != state["statistics"]:
                raise ValueError("experience_checkpoint_binding")
            state = saved
            model.load_state_dict(state["model"])
            optimizer.load_state_dict(state["optimizer"])
            torch.set_rng_state(state["rng"])
            if config["device"] == "cuda":
                torch.cuda.set_rng_state_all(state["cuda_rng"])

        def save():
            state.update(
                model=model.state_dict(),
                optimizer=optimizer.state_dict(),
                rng=torch.get_rng_state(),
                cuda_rng=torch.cuda.get_rng_state_all() if config["device"] == "cuda" else [],
            )
            save_checkpoint(checkpoint, state)

        while state["epoch"] < config["epochs"]:
            model.train()
            for x, y, mask, ids, cursor in batches(
                snapshot,
                plan,
                "train",
                config["batch"],
                seed=config["seed"],
                epoch=state["epoch"],
                cursor=state["cursor"],
            ):
                tx, ty, tm = tensors(x, y, mask, stats, config["device"])
                optimizer.zero_grad()
                value = loss(model(tx), ty, tm)
                if not torch.isfinite(value):
                    raise ValueError("experience_nonfinite_loss")
                value.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
                optimizer.step()
                state.update(
                    cursor=cursor, steps=state["steps"] + 1, rows_seen=state["rows_seen"] + len(ids)
                )
                if state["steps"] % config["checkpoint_every"] == 0:
                    save()
                if stop_after is not None and state["steps"] >= stop_after:
                    save()
                    return dict(status="interrupted", steps=state["steps"])
            expected = (state["epoch"] + 1) * stats["population"]["train_usable"]
            if state["rows_seen"] != expected:
                raise ValueError("experience_incomplete_epoch")
            validation = evaluate(model, snapshot, plan, "validation", stats, config)
            score = validation["selection_score"]
            state["history"].append(dict(epoch=state["epoch"], validation=validation))
            if state["best_score"] is None or score < state["best_score"]:
                state.update(best_score=score, best=copy.deepcopy(model.state_dict()))
            state.update(epoch=state["epoch"] + 1, cursor=(0, 0))
            save()
        model.load_state_dict(state["best"])
        # Selection is complete before the descriptive test is evaluated. This is not a holdout.
        test = evaluate(model, snapshot, plan, "test", stats, config)
        report = dict(
            status="completed",
            binding=binding,
            statistics=stats,
            steps=state["steps"],
            rows_seen=state["rows_seen"],
            epochs=state["epoch"],
            parameters=sum(p.numel() for p in model.parameters()),
            history=state["history"],
            test=test,
            production_eligible=False,
            evaluation_scope="descriptive_temporal_research_no_edge_claim",
        )
        staging = Path(tempfile.mkdtemp(prefix=".artifact-", dir=output))
        save_checkpoint(staging / "weights.pt", {k: v.cpu() for k, v in model.state_dict().items()})
        atomic_json(staging / "report.json", report)
        atomic_json(staging / "statistics.json", stats)
        seal_files(staging)
        os.rename(staging, artifact)
        sync_dir(output)
        return report
