"""Algebra checks are synthetic, not evidence of forecast improvement."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def tool():
    path = Path(__file__).resolve().parents[2] / "tools/brain_clipping_audit.py"
    spec = importlib.util.spec_from_file_location("clipping_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_saturated_loss_scale_cancels_approximately(tool):
    a = tool.clipped([3, 4])
    b = tool.clipped([3, 4], loss_scale=0.01)
    assert a == pytest.approx(b, abs=2e-7)
    assert a[0] / a[1] == pytest.approx(3 / 4)


def test_unsaturated_loss_scale_does_not_cancel(tool):
    assert tool.clipped([0.003, 0.004]) != pytest.approx(
        tool.clipped([0.003, 0.004], loss_scale=0.01)
    )


def test_batch_dependent_scaling_is_not_constant_adam_rescaling(tool):
    gradients = [[100.0, 1.0], [1.0, 1.0]]
    raw = tool.adam_steps(gradients)
    constant = tool.adam_steps([[g * 10 for g in row] for row in gradients])
    normalized = tool.adam_steps([tool.clipped(g) for g in gradients])
    assert raw == pytest.approx(constant, abs=1e-10)
    assert abs(raw[1] - normalized[1]) > 0.0001
