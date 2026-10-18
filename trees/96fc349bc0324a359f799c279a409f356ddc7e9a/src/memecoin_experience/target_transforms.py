"""Versioned target transforms used by research training and inference."""

from pathlib import Path

import numpy as np

from .storage import file_hash


def implementation_pin():
    """Return the digest of the exact transform implementation used by an artifact."""
    return file_hash(Path(__file__))


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
