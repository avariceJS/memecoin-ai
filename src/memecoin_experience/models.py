"""Dense quantile model implementations; the artifact spec owns dimensions and semantics."""

import hashlib
from pathlib import Path

import numpy as np
import torch
from torch import nn

from .schema import feature_names, target_names, validate

QUANTILES = (0.1, 0.5, 0.9)
KINDS = {"gru-quantiles-v1", "temporal-mlp-quantiles-v1"}


def model_spec(schema, width, kind="gru-quantiles-v1"):
    validate(schema)
    if kind not in KINDS or type(width) is not int or not 4 <= width <= 4096:
        raise ValueError("experience_model_spec")
    return dict(
        kind=kind,
        width=width,
        schema=schema,
        quantiles=list(QUANTILES),
        implementation_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        input_encoding="standardize_clip8_missing_mask_relative_position_v1",
    )


def ordered(p):
    center = p[:, :, 0]
    return torch.stack(
        (
            center - nn.functional.softplus(p[:, :, 1]),
            center,
            center + nn.functional.softplus(p[:, :, 2]),
        ),
        dim=-1,
    )


class GRU(nn.Module):
    def __init__(self, width, features=12, targets=2):
        super().__init__()
        self.gru = nn.GRU(features * 2 + 1, width, batch_first=True)
        self.head = nn.Linear(width, targets * 3)
        self.targets = targets

    def forward(self, x):
        states, _ = self.gru(x)
        return ordered(self.head(states[:, -1]).reshape(-1, self.targets, 3))


class TemporalMLP(nn.Module):
    def __init__(self, width, features, targets, context):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Flatten(),
            nn.Linear(context * (features * 2 + 1), width),
            nn.GELU(),
            nn.Linear(width, targets * 3),
        )
        self.targets = targets

    def forward(self, x):
        return ordered(self.layers(x).reshape(-1, self.targets, 3))


def construct(spec):
    required = {"kind", "width", "schema", "quantiles"}
    optional_bindings = {"implementation_sha", "input_encoding"}
    if not isinstance(spec, dict) or not required <= spec.keys() or spec.keys() - required - optional_bindings:
        raise ValueError("experience_model_spec")
    try:
        expected = model_spec(spec["schema"], spec["width"], spec["kind"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("experience_model_spec") from None
    if type(spec["quantiles"]) is not list or spec["quantiles"] != expected["quantiles"]:
        raise ValueError("experience_model_spec")
    if any(
        type(spec[key]) is not str or spec[key] != expected[key]
        for key in optional_bindings
        if key in spec
    ):
        raise ValueError("experience_model_implementation")
    schema = spec["schema"]
    args = (spec["width"], len(feature_names(schema)), len(target_names(schema)))
    return (
        GRU(*args) if spec["kind"] == "gru-quantiles-v1" else TemporalMLP(*args, schema["context"])
    )


def input_tensor(x, stats, device, *, context=None, features=None):
    x = np.asarray(x, dtype=np.float64)
    mean, scale = np.asarray(stats["mean"]), np.asarray(stats["scale"])
    if (
        x.ndim != 3
        or not x.shape[0]
        or not x.shape[1]
        or (context is not None and x.shape[1] != context)
        or (features is not None and x.shape[2] != features)
        or mean.shape != (x.shape[2],)
        or scale.shape != mean.shape
        or not np.isfinite(mean).all()
        or not np.isfinite(scale).all()
        or (scale <= 0).any()
        or np.isinf(x).any()
    ):
        raise ValueError("experience_model_input")
    present = np.isfinite(x)
    normalized = np.where(present, (x - mean) / scale, 0)
    normalized = np.clip(normalized, -8, 8)
    age = np.broadcast_to(np.linspace(-1, 0, x.shape[1])[None, :, None], (*x.shape[:2], 1))
    packed = np.concatenate((normalized, present.astype(np.float64), age), axis=-1)
    return torch.as_tensor(packed, dtype=torch.float32, device=device)
