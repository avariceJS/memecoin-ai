"""Read-only sealed clipping receipts and synthetic optimizer algebra, not a GRU ablation."""

import argparse
import json
import math
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import verify


def clipped(gradient, *, loss_scale=1.0, threshold=1.0):
    scaled = [g / loss_scale for g in gradient]
    norm = math.sqrt(sum(g * g for g in scaled))
    factor = min(1.0, threshold / (norm + 1e-6))
    return [g * factor for g in scaled]


def adam_steps(gradients):
    """Synthetic Adam adaptive component, zero moments; common decay omitted in both arms."""
    m, v, parameter = [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]
    for step, gradient in enumerate(gradients, 1):
        for j, g in enumerate(gradient):
            m[j] = 0.9 * m[j] + 0.1 * g
            v[j] = 0.999 * v[j] + 0.001 * g * g
            parameter[j] -= (
                0.001 * (m[j] / (1 - 0.9**step)) / (math.sqrt(v[j] / (1 - 0.999**step)) + 1e-8)
            )
    return parameter


def audit(root):
    verify(root)
    expected = "3d81f4eb1239cf7dacfc4ade2650e6c5d752a0de4f97d19be76175258e07415a"
    if sha(root / "manifest.json") != expected:
        raise ValueError("unexpected_lr_experiment")
    records = read(root / "result.json")["receipts"]
    if len(records) != 8:
        raise ValueError("incomplete_lr_receipts")
    fields = ("arm", "seed", "lr", "clip_fraction", "gradient_norm_mean", "gradient_norm_max")
    observed = [{k: r[k] for k in fields} for r in records]
    gradient = [3.0, 4.0]
    a, b = clipped(gradient), clipped(gradient, loss_scale=0.01)
    stream = [[100.0, 1.0], [1.0, 1.0]]
    raw_adam = adam_steps(stream)
    clipped_adam = adam_steps([clipped(g) for g in stream])
    return dict(
        schema="brain-clipping-diagnostic-v1",
        source_manifest_sha=expected,
        observed=observed,
        per_step_norms_saved=False,
        median_clip_factor=None,
        gradient_directions_saved=False,
        causal_gru_clipping_ablation="not_run",
        synthetic_algebra=dict(
            saturated_iqr_scale_max_difference=max(abs(x - y) for x, y in zip(a, b, strict=True)),
            adam_raw=raw_adam,
            adam_clipped=clipped_adam,
            demonstrates="batch-dependent norm scaling can change adaptive moments",
            real_model_quality_evidence=False,
        ),
        new_training_runs=0,
        test_scoring=0,
        provider_requests=0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
