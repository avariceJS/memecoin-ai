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
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import torch
from torch import nn

from .models import GRU, QUANTILES, construct, input_tensor, model_spec
from .records import split_plan
from .schema import feature_names, membership, target_names
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

Network = GRU  # Backward-compatible research import; dimensions come from artifact specs.


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
        model_kind="gru-quantiles-v1",
        normalization="legacy_variance_floor_v1",
        objective="masked_pinball_v1",
        sampler="content_pin_v1",
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
    from .models import KINDS

    if c["normalization"] not in {"legacy_variance_floor_v1", "constant_feature_scale_one_v1"}:
        raise ValueError("experience_training_config")
    if c["model_kind"] not in KINDS:
        raise ValueError("experience_training_config")
    if c["objective"] not in {
        "masked_pinball_v1",
        "partial_count_lower_bound_pinball_v1",
    }:
        raise ValueError("experience_training_config")
    if c["sampler"] not in {"content_pin_v1", "role_isolated_partition_v1"}:
        raise ValueError("experience_training_config")
    return c


def partition_indices(snapshot, seed, epoch, start=0, *, sampler="content_pin_v1"):
    """Page/partition permutation: bounded metadata and sequential catalog-page consumption."""
    if sampler not in {"content_pin_v1", "role_isolated_partition_v1"}:
        raise ValueError("experience_training_sampler")
    if "partition_pages" not in snapshot.manifest:
        order = list(range(len(snapshot.parts)))
        if seed is not None:
            random.Random(f"{seed}:{epoch}:partitions").shuffle(order)
        yield from enumerate(order[start:], start)
        return
    pages = snapshot.manifest["partition_pages"]
    order = list(range(len(pages)))
    if seed is not None:
        random.Random(f"{seed}:{epoch}:catalog-pages").shuffle(order)
    position = 0
    for page_index in order:
        count = pages[page_index]["count"]
        if position + count <= start:
            position += count
            continue
        begin = snapshot.parts.ends[page_index - 1] if page_index else 0
        indices = list(range(begin, begin + count))
        if seed is not None:
            identity = (
                pages[page_index]["pin"]
                if sampler == "content_pin_v1"
                else f"role_isolated_partition_v1:page:{page_index}"
            )
            random.Random(f"{seed}:{epoch}:{identity}:partitions").shuffle(indices)
        for local in range(max(0, start - position), count):
            yield position + local, indices[local]
        position += count


def _censor_bounds(row, schema, role, plan, cutoff_ns=None):
    """Return identified positive lower bounds for right-censored process counts."""
    bounds = [0.0] * len(target_names(schema))
    mask = [False] * len(bounds)
    if (
        schema.get("schema") == "brain-experience-v2"
        and row.get("_admission", "eligible") != "eligible"
    ):
        return bounds, mask
    if role not in {"train", "validation", "test"} or schema.get("schema") not in {
        "brain-experience-v2",
        "brain-experience-v3",
    }:
        return bounds, mask
    if schema.get("schema") == "brain-experience-v3":
        from .coordinate_samples import eligibility

        allowed = eligibility(row, schema, plan, role)["lower_bound_mask"]
        for i, (target, detail) in enumerate(
            zip(schema["targets"], row["target_details"], strict=True)
        ):
            lower = detail.get("lower_bound")
            if (
                target["kind"].startswith("observed_process_count@")
                and allowed[i]
                and type(lower) in (int, float)
                and math.isfinite(lower)
                and lower > 0
            ):
                bounds[i], mask[i] = float(lower), True
        return bounds, mask
    role_start, role_end = {
        "train": (plan["train_start_s"], plan["train_end_s"]),
        "validation": (plan["train_end_s"], plan["validation_end_s"]),
        "test": (plan["validation_end_s"], plan["test_end_s"]),
    }[role]
    for i, (target, status, maturity, detail) in enumerate(
        zip(
            schema["targets"],
            row["target_status"],
            row["target_maturity_ns"],
            row.get("target_details", [{}] * len(bounds)),
            strict=True,
        )
    ):
        if target["kind"].startswith("observed_process_count@") and status == "censored":
            if not isinstance(detail, dict):
                raise ValueError("experience_censor_detail")
            lower = detail.get("lower_bound")
            if lower is not None and (type(lower) is not int or lower < 0):
                raise ValueError("experience_censor_lower_bound")
            at = detail.get("lower_bound_available_ns")
            if lower and (type(maturity) is not int or maturity < row["decision_ns"]):
                raise ValueError("experience_censor_target_maturity")
            if lower and (type(at) is not int or at < row["decision_ns"]):
                raise ValueError("experience_censor_bound_availability")
        if (
            target["kind"].startswith("observed_process_count@")
            and status == "censored"
            and role_start * 10**9 <= row["decision_ns"] < role_end * 10**9
            and maturity < role_end * 10**9
            and type(detail.get("lower_bound")) in (int, float)
            and math.isfinite(detail["lower_bound"])
            and detail["lower_bound"] > 0
            and type(detail.get("lower_bound_available_ns")) is int
            and cutoff_ns is not None
            and maturity <= cutoff_ns
            and detail["lower_bound_available_ns"] <= cutoff_ns
            and (
                schema["availability"] == "historical_replay"
                or detail["lower_bound_available_ns"] < role_end * 10**9
            )
        ):
            bounds[i] = float(detail["lower_bound"])
            mask[i] = True
    return bounds, mask


def _snapshot_censor_cutoff(snapshot):
    """Resolve composition lineage only for the v2 count-bound contract."""
    if snapshot.schema.get("schema") != "brain-experience-v2" or not any(
        target["kind"].startswith("observed_process_count@")
        for target in snapshot.schema.get("targets", [])
    ):
        return None
    return snapshot.censor_cutoff_ns()


def batches(
    snapshot,
    plan,
    role,
    batch,
    *,
    seed=None,
    epoch=0,
    cursor=(0, 0),
    with_metadata=False,
    with_censoring=False,
    sampler="content_pin_v1",
):
    """Permutation of partitions then rows: exactly once per role per epoch, bounded payload."""
    xs, ys, ms, ids = [], [], [], []

    lower_bounds, censor_masks = [], []
    censor_cutoff_ns = _snapshot_censor_cutoff(snapshot) if with_censoring else None

    def packed(next_cursor):
        core = (np.concatenate(xs), np.concatenate(ys), np.concatenate(ms))
        if with_censoring:
            return (
                *core,
                np.concatenate(lower_bounds),
                np.concatenate(censor_masks),
                ids,
                next_cursor,
            )
        return (*core, ids, next_cursor)

    if role not in {"train", "validation", "test"}:
        raise ValueError("experience_batch_role")
    if sampler not in {"content_pin_v1", "role_isolated_partition_v1"}:
        raise ValueError("experience_training_sampler")
    if snapshot.schema.get("schema") == "brain-experience-v3":
        from .coordinate_samples import domain_registry, role_intervals

        if plan["coordinate_domain_pin"] != snapshot.schema["coordinate_domain_pin"] or plan[
            "coordinate_domain_pins"
        ] != sorted(domain_registry(snapshot.schema)):
            raise ValueError("experience_coordinate_split_domain")

        intervals = role_intervals(plan, role)
    else:
        old_intervals = {
            "train": (plan["train_start_s"], plan["train_end_s"]),
            "validation": (plan["train_end_s"], plan["validation_end_s"]),
            "test": (plan["validation_end_s"], plan["test_end_s"]),
        }
        intervals = [tuple(v * 10**9 for v in old_intervals[role])]
    if snapshot.schema.get("schema") != "brain-experience-v3":
        start_ns, end_ns = intervals[0]
    for position, index in partition_indices(snapshot, seed, epoch, cursor[0], sampler=sampler):
        extent = snapshot.parts[index].get("bounds")
        if extent and snapshot.schema.get("schema") == "brain-experience-v3":
            if extent["coordinate_domain_pin"] != plan["coordinate_domain_pin"]:
                raise ValueError("experience_coordinate_partition_domain")
            if not any(
                extent["last_coordinate"] >= start and extent["first_coordinate"] < end
                for start, end in intervals
            ):
                continue
        elif extent and (extent["last_ns"] < start_ns or extent["first_ns"] >= end_ns):
            # Authenticated bounds exclude payloads that this role cannot use.
            continue
        path, rows = snapshot.partition(index)
        chosen, masks, bounds, censored = [], [], [], []
        for i, row in enumerate(rows):
            row_role, mask = membership(row, plan, snapshot.schema)
            if with_censoring:
                lower, lower_mask = _censor_bounds(
                    row,
                    snapshot.schema,
                    role,
                    plan,
                    cutoff_ns=censor_cutoff_ns,
                )
            else:
                lower, lower_mask = [0.0] * len(mask), [False] * len(mask)
            if row_role == role and (any(mask) or (with_censoring and any(lower_mask))):
                chosen.append(i)
                masks.append(mask)
                bounds.append(lower)
                censored.append(lower_mask)
        if not chosen:
            continue
        indices = list(range(len(chosen)))
        if seed is not None:
            identity = (
                snapshot.parts[index]["pin"]
                if sampler == "content_pin_v1"
                else f"role_isolated_partition_v1:partition:{index}"
            )
            random.Random(f"{seed}:{epoch}:{identity}").shuffle(indices)
        # mmap only selected role's slices, after metadata establishes role membership.
        x = np.memmap(
            path / "x.f64",
            dtype="<f8",
            mode="r",
            shape=(len(rows), snapshot.schema["context"], len(feature_names(snapshot.schema))),
        )
        y = np.memmap(
            path / "y.f64",
            dtype="<f8",
            mode="r",
            shape=(len(rows), len(target_names(snapshot.schema))),
        )
        offset = cursor[1] if position == cursor[0] else 0
        while offset < len(indices):
            take = indices[offset : offset + batch - len(ids)]
            selected = [chosen[i] for i in take]
            xs.append(np.array(x[selected]))
            ys.append(np.array(y[selected]))
            ms.append(np.array([masks[i] for i in take], dtype=bool))
            if with_censoring:
                lower_bounds.append(np.array([bounds[i] for i in take], dtype=np.float64))
                censor_masks.append(np.array([censored[i] for i in take], dtype=bool))
            ids.extend(rows[i] if with_metadata else rows[i]["id"] for i in selected)
            offset += len(take)
            if len(ids) == batch:
                next_cursor = (position + 1, 0) if offset == len(indices) else (position, offset)
                yield packed(next_cursor)
                xs, ys, ms, ids = [], [], [], []
                lower_bounds, censor_masks = [], []
        del x, y
    if ids:
        yield packed((len(snapshot.parts), 0))


def prepare(
    snapshot,
    plan,
    output,
    batch,
    normalization="legacy_variance_floor_v1",
    objective="masked_pinball_v1",
):
    """Train-only Welford statistics and exact empirical quantiles via disposable disk sort."""
    names, horizons = feature_names(snapshot.schema), target_names(snapshot.schema)
    if objective == "partial_count_lower_bound_pinball_v1" and not any(
        target.get("kind", "").startswith("observed_process_count@")
        for target in snapshot.schema.get("targets", [])
    ):
        raise ValueError("experience_objective_requires_count_target")
    counts = np.zeros(len(names), dtype=np.int64)
    means, m2 = np.zeros(len(names)), np.zeros(len(names))
    population = Counter()
    denominators = Counter()
    censor_cutoff_ns = _snapshot_censor_cutoff(snapshot)
    for i in range(len(snapshot.parts)):
        _, rows = snapshot.partition(i)
        for row in rows:
            role, mask = membership(row, plan, snapshot.schema)
            population[role] += 1
            population[role + "_usable"] += int(any(mask))
            lower, lower_mask = _censor_bounds(
                row,
                snapshot.schema,
                role,
                plan,
                cutoff_ns=censor_cutoff_ns,
            )
            population[role + "_partial_count_usable"] += int(any(lower_mask))
            population[role + "_objective_usable"] += int(any(mask) or any(lower_mask))
            for h, valid, status in zip(horizons, mask, row["target_status"], strict=True):
                denominators[f"{role}/{h}/{status}"] += 1
                denominators[f"{role}/{h}/usable"] += int(valid)
            if snapshot.schema.get("schema") in {"brain-experience-v2", "brain-experience-v3"}:
                target_details = row.get("target_details")
                if target_details is None and not any(
                    target["kind"].startswith("observed_process_count@")
                    for target in snapshot.schema["targets"]
                ):
                    # Older price-only rows predate per-target censor details.
                    target_details = [{} for _ in snapshot.schema["targets"]]
                if target_details is None:
                    target_details = []
                for h, (target, status, detail) in enumerate(
                    zip(
                        snapshot.schema["targets"],
                        row["target_status"],
                        target_details,
                        strict=True,
                    )
                ):
                    if target["kind"].startswith("observed_process_count@") and status == (
                        "lower_bound"
                        if snapshot.schema["schema"] == "brain-experience-v3"
                        else "censored"
                    ):
                        lower = detail.get("lower_bound")
                        if (
                            snapshot.schema["schema"] == "brain-experience-v2"
                            and row.get("_admission", "eligible") != "eligible"
                        ):
                            bucket = "input_admission_rejected"
                        elif lower is not None and lower > 0 and lower_mask[h]:
                            bucket = "informative_lower_bound"
                        elif lower == 0:
                            bucket = "zero_lower_bound"
                        else:
                            bucket = "lower_bound_unavailable_or_invalid"
                        denominators[f"{role}/{h}/{bucket}"] += 1
    fd, name = tempfile.mkstemp(prefix=".baseline-", suffix=".sqlite", dir=output)
    os.close(fd)
    path = Path(name)  # Unique scratch: a killed SQLite journal can never contaminate a retry.
    db = sqlite3.connect(path)
    try:
        db.execute("pragma temp_store=FILE")
        db.execute("pragma cache_size=-8192")
        db.execute("create table labels(h integer, y real)")
        for packed in batches(
            snapshot,
            plan,
            "train",
            batch,
            with_censoring=objective == "partial_count_lower_bound_pinball_v1",
        ):
            if objective == "partial_count_lower_bound_pinball_v1":
                x, y, mask, _, _, _, _ = packed
            else:
                x, y, mask, _, _ = packed
            flat = x.reshape(-1, len(names))
            for j in range(len(names)):
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
                        for h in range(len(horizons))
                        if mask[i, h]
                    ),
                )
        db.execute("create index ordered_labels on labels(h,y)")
        baseline = []
        for h in range(len(horizons)):
            n = db.execute("select count(*) from labels where h=?", (h,)).fetchone()[0]
            if not n:
                is_count = (
                    snapshot.schema.get("targets", [{}] * len(horizons))[h]
                    .get("kind", "")
                    .startswith("observed_process_count@")
                )
                if (
                    objective == "partial_count_lower_bound_pinball_v1"
                    and is_count
                    and population["train_partial_count_usable"]
                ):
                    baseline.append(None)
                    continue
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
    if any(
        not (
            population[r + "_usable"]
            or (
                r == "train"
                and objective == "partial_count_lower_bound_pinball_v1"
                and population[r + "_partial_count_usable"]
            )
        )
        for r in ("train", "validation", "test")
    ):
        raise ValueError("experience_empty_role")
    population["train_objective_usable"] = (
        population["train_objective_usable"]
        if objective == "partial_count_lower_bound_pinball_v1"
        else population["train_usable"]
    )
    transforms = [
        "log1p_nonnegative"
        if objective == "partial_count_lower_bound_pinball_v1"
        and snapshot.schema["targets"][i]["kind"].startswith("observed_process_count@")
        else "identity"
        for i in range(len(horizons))
    ]
    scale = np.maximum(np.sqrt(m2 / np.maximum(counts, 1)), 1e-6)
    if normalization == "constant_feature_scale_one_v1":
        scale[m2 == 0] = 1.0
    elif normalization != "legacy_variance_floor_v1":
        raise ValueError("experience_normalization")
    return dict(
        mean=means.tolist(),
        scale=scale.tolist(),
        feature_observations=counts.tolist(),
        baseline=baseline,
        target_transforms=transforms,
        population=dict(population),
        denominators=dict(denominators),
        normalization=(
            (
                "train_objective_eligible_decision_frames_population_variance"
                if objective == "partial_count_lower_bound_pinball_v1"
                else "train_usable_decision_frames_population_variance"
            )
            if normalization == "legacy_variance_floor_v1"
            else normalization
        ),
        sampling=(
            "uniform_usable_origin_once_per_epoch_catalog_partition_row_shuffle"
            if "partition_pages" in snapshot.manifest
            else "uniform_usable_origin_once_per_epoch_partition_shuffle"
        ),
        loss=(
            "exact_pinball_plus_per_quantile_censor_lower_bound_surrogate_v1"
            if objective == "partial_count_lower_bound_pinball_v1"
            else "macro_mean_of_observed_horizon_pinball_means"
        ),
    )


def tensors(x, y, mask, stats, device):
    if np.isinf(x).any() or not np.isfinite(y[mask]).all():
        raise ValueError("experience_nonfinite_learning_input")
    if y.shape != mask.shape or y.ndim != 2 or y.shape[0] != x.shape[0]:
        raise ValueError("experience_label_shape")
    targets = np.where(mask, y, 0).astype(np.float64, copy=False)
    targets = transform_targets(
        targets, stats.get("target_transforms", ["identity"] * targets.shape[1]), mask
    )
    return (
        input_tensor(x, stats, device),
        torch.tensor(targets, dtype=torch.float32, device=device),
        torch.tensor(mask, dtype=torch.bool, device=device),
    )


def transform_targets(values, transforms, mask=None):
    result = np.asarray(values, dtype=np.float64).copy()
    if len(transforms) != result.shape[1]:
        raise ValueError("experience_target_transform_shape")
    for h, transform in enumerate(transforms):
        if transform == "log1p_nonnegative":
            valid = np.ones(len(result), dtype=bool) if mask is None else mask[:, h]
            if np.any(result[valid, h] < 0):
                raise ValueError("experience_negative_count_target")
            result[:, h] = np.log1p(np.maximum(result[:, h], 0))
        elif transform != "identity":
            raise ValueError("experience_target_transform")
    return result


def inverse_predictions(prediction, stats):
    result = np.asarray(prediction).copy()
    transforms = stats.get("target_transforms", ["identity"] * result.shape[1])
    if len(transforms) != result.shape[1]:
        raise ValueError("experience_target_transform_shape")
    for h, transform in enumerate(transforms):
        if transform == "log1p_nonnegative":
            result[:, h, :] = np.expm1(np.clip(result[:, h, :], 0, 700))
        elif transform != "identity":
            raise ValueError("experience_target_transform")
    return result


def loss(prediction, target, mask, *, lower_bound=None, censored=None):
    error = target[:, :, None] - prediction
    q = prediction.new_tensor(QUANTILES)
    values = torch.maximum(q * error, (q - 1) * error).mean(dim=-1)
    counts = mask.sum(dim=0)
    sums = (values * mask).sum(dim=0)
    if (lower_bound is None) != (censored is None):
        raise ValueError("experience_censor_loss_inputs")
    if lower_bound is not None:
        if lower_bound.shape != target.shape or censored.shape != mask.shape:
            raise ValueError("experience_censor_loss_shape")
        # For Y >= L, tau * max(L - q, 0) is a per-quantile lower envelope
        # of pinball loss over all completions. It is partial identification,
        # not a proper score or a full quantile likelihood.
        q = prediction.new_tensor(QUANTILES)
        one_sided = torch.relu(lower_bound[:, :, None] - prediction) * q
        sums = sums + (one_sided.mean(dim=-1) * censored).sum(dim=0)
        counts = counts + censored.sum(dim=0)
    active = counts > 0
    return (sums[active] / counts[active]).mean()


def evaluate(model, snapshot, plan, role, stats, config):
    horizons = target_names(snapshot.schema)
    transforms = stats.get("target_transforms", ["identity"] * len(horizons))
    accum = {name: np.zeros((len(horizons), 3)) for name in ("model", "empirical")}
    bound_totals = np.zeros((len(horizons), 5))
    selection_sums = np.zeros(len(horizons))
    selection_counts = np.zeros(len(horizons))
    model.eval()
    with torch.no_grad():
        for packed in batches(
            snapshot,
            plan,
            role,
            config["batch"],
            with_censoring=config["objective"] == "partial_count_lower_bound_pinball_v1",
        ):
            if config["objective"] == "partial_count_lower_bound_pinball_v1":
                x, y, mask, lower, censored, _, _ = packed
            else:
                x, y, mask, _, _ = packed
                lower = np.zeros_like(y)
                censored = np.zeros_like(mask)
            tx, _, _ = tensors(x, y, mask, stats, config["device"])
            transformed_prediction = model(tx).cpu().numpy()
            transformed_target = transform_targets(np.where(mask, y, 0), transforms, mask)
            predictions = {
                "model": inverse_predictions(transformed_prediction, stats),
                "empirical": np.broadcast_to(
                    np.array([v if v is not None else [0.0, 0.0, 0.0] for v in stats["baseline"]]),
                    (*y.shape, 3),
                ),
            }
            for name, p in predictions.items():
                for h in range(len(horizons)):
                    if name == "empirical" and stats["baseline"][h] is None:
                        continue
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
            if censored.any():
                for h, target in enumerate(snapshot.schema["targets"]):
                    if not target["kind"].startswith("observed_process_count@"):
                        continue
                    selected = censored[:, h]
                    if not selected.any():
                        continue
                    lower_h = lower[selected, h]
                    pred_h = predictions["model"][selected, h]
                    violation = np.maximum(lower_h[:, None] - pred_h, 0)
                    transformed_lower = transform_targets(lower, transforms)[selected, h]
                    transformed_violation = np.maximum(
                        transformed_lower[:, None] - transformed_prediction[selected, h], 0
                    )
                    selection_sums[h] += (
                        (transformed_violation * np.array(QUANTILES)).mean(axis=1).sum()
                    )
                    selection_counts[h] += len(transformed_lower)
                    bound_totals[h] += [
                        len(lower_h),
                        violation.mean(axis=1).sum(),
                        *(violation > 0).sum(axis=0),
                    ]
            for h in range(len(horizons)):
                selected = mask[:, h]
                if not selected.any():
                    continue
                error = transformed_target[selected, h, None] - transformed_prediction[selected, h]
                selection_sums[h] += (
                    np.maximum(
                        np.array(QUANTILES) * error,
                        (np.array(QUANTILES) - 1) * error,
                    )
                    .mean(axis=1)
                    .sum()
                )
                selection_counts[h] += int(selected.sum())
    result = {}
    for name, matrix in accum.items():
        result[name] = [
            dict(
                horizon=h,
                n=int(n),
                pinball=float(p / n) if n else None,
                coverage=float(c / n) if n else None,
            )
            for h, (n, p, c) in zip(horizons, matrix, strict=True)
        ]
    supported = [
        selection_sums[i] / selection_counts[i] for i in range(len(horizons)) if selection_counts[i]
    ]
    if not supported:
        raise ValueError("experience_empty_evaluation_horizon")
    result["selection_score"] = float(sum(supported) / len(supported))
    result["selection_metric"] = (
        "partial_identification_objective_space_v2_quantile_weighted"
        if config["objective"] == "partial_count_lower_bound_pinball_v1"
        else "observed_pinball_v1"
    )
    target_contracts = snapshot.schema.get("targets", [{}] * len(bound_totals))
    result["partial_count_bounds"] = [
        dict(
            target=horizons[i],
            cells=int(row[0]),
            mean_lower_bound_violation=float(row[1] / row[0]) if row[0] else None,
            quantile_violation_fraction=[
                float(row[index + 2] / row[0]) if row[0] else None
                for index in range(len(QUANTILES))
            ],
        )
        for i, row in enumerate(bound_totals)
        if target_contracts[i].get("kind", "").startswith("observed_process_count@")
    ]
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


def train(store, pin, output, *, plan, config=None, stop_after=None, parent=None, diversity=None):
    """Research-only job. stop_after is an absolute one-shot test interrupt; omit on resume."""
    output, plan, config = safe_path(output), split_plan(plan), recipe(config or {})
    output.mkdir(parents=True, exist_ok=True)
    if not (output / "recipe.json").exists() and any(
        p.name != ".training.lock" for p in output.iterdir()
    ):
        raise ValueError("experience_unowned_training_directory")
    snapshot = Snapshot(store, pin)
    if snapshot.manifest["binding"].get("role", "train") != "train":
        raise ValueError("experience_reserved_snapshot")
    if snapshot.schema.get("schema") == "brain-experience-v3":
        from .coordinate_samples import domain_registry

        if plan["coordinate_domain_pin"] != snapshot.schema["coordinate_domain_pin"] or plan[
            "coordinate_domain_pins"
        ] != sorted(domain_registry(snapshot.schema)):
            raise ValueError("experience_coordinate_split_domain")
    spec = model_spec(snapshot.schema, config["width"], config["model_kind"])
    ancestor = None
    if parent is not None:
        from .predict import Predictor

        if set(parent) != {"path", "pin"}:
            raise ValueError("experience_parent_reference")
        ancestor = Predictor(parent["path"], parent["pin"])
        if ancestor.spec != spec:
            raise ValueError("experience_parent_contract")
    binding = dict(
        model=spec,
        parent=parent["pin"] if parent else None,
        initialization="parent_weights_frozen_normalizer_fresh_optimizer"
        if parent
        else "seeded_fresh",
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
    if snapshot.schema.get("schema") == "brain-experience-v3":
        from .coordinate_samples import ELIGIBILITY_POLICY

        binding.update(
            coordinate_schema_pin=digest(snapshot.schema),
            eligibility_policy_pin=ELIGIBILITY_POLICY,
        )
    audit = None
    if diversity is not None:
        from memecoin_population.exposure import validate

        audit = validate(diversity, pin, plan, snapshot.schema)
        binding["diversity"] = diversity["pin"]
    if config["device"] == "cuda":
        workspace = os.environ.get("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        if workspace not in {":4096:8", ":16:8"}:
            raise ValueError("experience_cuda_workspace_config")
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = workspace
        binding["environment"]["cublas_workspace_config"] = workspace
        if not torch.cuda.is_available():
            raise ValueError("experience_cuda_unavailable")
        binding["environment"]["accelerator"] = torch.cuda.get_device_name()
    with lock(output / ".training.lock"), ExitStack() as resources:
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
            stats = prepare(
                snapshot,
                plan,
                output,
                config["batch"],
                config["normalization"],
                config["objective"],
            )
            if ancestor is not None:
                stats.update(
                    mean=ancestor.stats["mean"],
                    scale=ancestor.stats["scale"],
                    normalization="frozen_parent_normalizer",
                    normalizer_parent=parent["pin"],
                )
            atomic_json(stats_path, stats, replace=True)
        if (
            ancestor is not None
            and ancestor.stats.get(
                "target_transforms", ["identity"] * len(target_names(snapshot.schema))
            )
            != stats["target_transforms"]
        ):
            raise ValueError("experience_parent_target_transform")
        binding = dict(binding, statistics=digest(stats))
        atomic_json(output / "binding.json", binding)
        artifact = output / "artifact"
        if artifact.exists():
            verify_files(artifact)
            report = json.loads((artifact / "report.json").read_text())
            if report["binding"] != binding:
                raise ValueError("experience_artifact_binding")
            return report
        journal = None
        if audit is not None:
            from memecoin_population.exposure import Journal

            journal = resources.enter_context(Journal(diversity, output, audit))
        torch.set_num_threads(config["cpu_threads"])
        torch.manual_seed(config["seed"])
        torch.use_deterministic_algorithms(True)
        torch.backends.cudnn.benchmark = False
        torch.backends.cuda.matmul.allow_tf32 = False
        model = construct(spec).to(config["device"])
        if ancestor is not None:
            model.load_state_dict(ancestor.model.state_dict(), strict=True)
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
            if stop_after is not None and saved["steps"] >= stop_after:
                raise ValueError("experience_stop_after_already_reached")
            state = saved
            model.load_state_dict(state["model"])
            optimizer.load_state_dict(state["optimizer"])
            torch.set_rng_state(state["rng"])
            if config["device"] == "cuda":
                torch.cuda.set_rng_state_all(state["cuda_rng"])

        if journal is not None:
            journal.verify(state["steps"], state.get("exposure_head"), state["rows_seen"])

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
            for packed in batches(
                snapshot,
                plan,
                "train",
                config["batch"],
                seed=config["seed"],
                epoch=state["epoch"],
                cursor=state["cursor"],
                with_metadata=journal is not None,
                with_censoring=config["objective"] == "partial_count_lower_bound_pinball_v1",
                sampler=config["sampler"],
            ):
                if config["objective"] == "partial_count_lower_bound_pinball_v1":
                    x, y, mask, lower, censored, ids, cursor = packed
                else:
                    x, y, mask, ids, cursor = packed
                    lower = np.zeros_like(y)
                    censored = np.zeros_like(mask)
                tx, ty, tm = tensors(x, y, mask, stats, config["device"])
                tl = torch.as_tensor(
                    transform_targets(
                        lower,
                        stats.get(
                            "target_transforms", ["identity"] * len(target_names(snapshot.schema))
                        ),
                    ),
                    dtype=torch.float32,
                    device=config["device"],
                )
                tc = torch.as_tensor(censored, dtype=torch.bool, device=config["device"])
                optimizer.zero_grad()
                value = loss(
                    model(tx),
                    ty,
                    tm,
                    lower_bound=tl
                    if config["objective"] == "partial_count_lower_bound_pinball_v1"
                    else None,
                    censored=tc
                    if config["objective"] == "partial_count_lower_bound_pinball_v1"
                    else None,
                )
                if not torch.isfinite(value):
                    raise ValueError("experience_nonfinite_loss")
                value.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
                optimizer.step()
                state.update(
                    cursor=cursor, steps=state["steps"] + 1, rows_seen=state["rows_seen"] + len(ids)
                )
                if journal is not None:
                    state["exposure_head"] = journal.record(
                        state["steps"],
                        state["epoch"],
                        ids,
                        np.logical_or(mask, censored)
                        if config["objective"] == "partial_count_lower_bound_pinball_v1"
                        else mask,
                    )
                if state["steps"] % config["checkpoint_every"] == 0:
                    save()
                if stop_after is not None and state["steps"] >= stop_after:
                    save()
                    return dict(status="interrupted", steps=state["steps"])
            expected = (state["epoch"] + 1) * stats["population"]["train_objective_usable"]
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
            ancestors=([parent["pin"], *ancestor.report["ancestors"]] if ancestor else []),
            seen_end_s=(
                max(
                    plan["test_end_s"],
                    (ancestor.report.get("seen_end_s") or 0) if ancestor else 0,
                )
                if snapshot.schema.get("schema") != "brain-experience-v3"
                else (ancestor.report.get("seen_end_s") if ancestor else None)
            ),
        )
        if snapshot.schema.get("schema") == "brain-experience-v3":
            report["seen_end_coordinate"] = max(
                (end for start, end in plan["roles"]["test"]["intervals"]), default=None
            )
        if journal is not None:
            report["exposure"] = journal.finish(
                state["steps"], state["rows_seen"], output / "exposure-report"
            )
        staging = Path(tempfile.mkdtemp(prefix=".artifact-", dir=output))
        if audit is not None:
            atomic_json(staging / "diversity.json", audit)
            atomic_json(
                staging / "diversity-manifest.json",
                json.loads(safe_path(Path(diversity["path"]) / "manifest.json").read_text()),
            )
        save_checkpoint(staging / "weights.pt", {k: v.cpu() for k, v in model.state_dict().items()})
        atomic_json(staging / "model.json", spec)
        atomic_json(staging / "report.json", report)
        atomic_json(staging / "statistics.json", stats)
        seal_files(staging)
        os.rename(staging, artifact)
        sync_dir(output)
        return report
