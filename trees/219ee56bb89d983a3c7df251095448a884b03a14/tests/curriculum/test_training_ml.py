import importlib.util
import time

import pytest

if importlib.util.find_spec("torch") is None:
    pytest.skip("optional ML environment", allow_module_level=True)

import torch

from memecoin_brain.data import scaler
from memecoin_brain.model import infer, load_neural, tensors
from memecoin_curriculum.design import experiments
from memecoin_curriculum.training import cached_infer, fit
from memecoin_ml.models import Brain, ModelConfig
from tests.curriculum.test_design import BOUNDS, fixture_bundle


def test_cached_inputs_preserve_inference_and_frozen_checkpoint(tmp_path):
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    bundle = fixture_bundle()
    e = list(experiments(bundle, BOUNDS))[-1]
    fitted = scaler(bundle, e["train"])
    validation = tensors(bundle, e["validation"], fitted)
    model = Brain(ModelConfig("gru", 16, 8))
    assert cached_infer(model, validation) == infer(model, bundle, e["validation"], fitted)
    output = tmp_path / "fit"
    pred = fit(
        tensors(bundle, e["train"], fitted),
        validation,
        e,
        fitted,
        dict(kind="gru", seed=7, width=16, context=8),
        output,
        time.monotonic() + 30,
        steps=20,
    )
    assert pred == load_neural(output, bundle, e["validation"])
