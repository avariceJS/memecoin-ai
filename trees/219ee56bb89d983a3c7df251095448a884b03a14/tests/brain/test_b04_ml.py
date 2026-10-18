import time

import pytest

torch = pytest.importorskip("torch", reason="optional ML environment")
from test_b04 import fixture  # noqa: E402

from memecoin_brain.data import save_dataset  # noqa: E402
from memecoin_brain.model import fit_one  # noqa: E402
from memecoin_brain_b04.experiment import reproduce, run  # noqa: E402
from memecoin_brain_scale.curves import folds  # noqa: E402
from memecoin_ml.data import sha, write  # noqa: E402
from memecoin_registry.contracts import seal, verify  # noqa: E402


def test_paired_fixture_matches_controls_and_replays_sealed_transform(tmp_path):
    torch.set_num_threads(1)
    bundle = fixture()
    dataset = tmp_path / "dataset"
    save_dataset(dataset, bundle)
    control = tmp_path / "control"
    control.mkdir()
    write(control / "binding.json", dict(dataset_sha=sha(dataset / "manifest.json")))
    for e in [e for e in folds(bundle) if e["fraction"] == 100]:
        folder = control / e["name"]
        folder.mkdir()
        for kind in ["linear", "gru"]:
            for seed in [7, 19]:
                fit_one(
                    bundle,
                    e["parts"],
                    dict(kind=kind, seed=seed, width=16, context=8),
                    folder / f"{kind}-{seed}",
                    deadline=time.monotonic() + 60,
                )
    seal(control)
    result = run(dataset, control, tmp_path / "run")
    assert result["neural_fits"] == 16 and not result["promoted"]
    assert all(e["control_weights_exact"] for e in result["experiments"])
    verify(tmp_path / "run")
    assert reproduce(dataset, tmp_path / "run", tmp_path / "replay")["neural_fits"] == 0
