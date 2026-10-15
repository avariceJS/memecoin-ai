"""Adversarial test that isolates training sampling from held-out payload bytes."""

import hashlib
import json

import pytest

torch = pytest.importorskip("torch")

from memecoin_experience.learning import batches, train  # noqa: E402
from memecoin_experience.storage import Snapshot, build, file_hash  # noqa: E402

PLAN = dict(train_start_s=0, train_end_s=100, validation_end_s=200, test_end_s=300)
CONFIG = dict(
    width=8,
    batch=4,
    epochs=3,
    seed=19,
    device="cpu",
    cpu_threads=1,
    lr=0.001,
    objective="masked_pinball_v1",
)
SCHEMA = dict(
    schema="brain-experience-v2",
    layout="dense-sequence-f64-v1",
    context=2,
    features=[dict(name="signal", unit="indicator", modality="synthetic")],
    targets=[dict(name="response", unit="synthetic", kind="synthetic_regression")],
    representation=dict(
        name="training-role-isolation",
        version="1",
        code_sha=hashlib.sha256(b"training-role-isolation-fixture-v1").hexdigest(),
    ),
    availability="point_in_time",
)


def _fixture(*, perturb_test):
    rows = []
    for entity_index in range(4):
        pool = f"synthetic/entity/{entity_index:02d}"
        role_specs = (
            ("train", range(1, 7)),
            ("validation", range(101, 103)),
            ("test", range(201, 203)),
        )
        for role, decisions in role_specs:
            for offset, decision_s in enumerate(decisions):
                signal = (entity_index + offset) % 2
                feature = float(signal)
                target = 2.0 * signal + entity_index * 0.1
                if perturb_test and role == "test":
                    feature += 3.0
                    target += 17.0
                decision_ns = decision_s * 10**9
                rows.append(
                    dict(
                        id=f"{pool}/{role}/{offset:02d}",
                        pool=pool,
                        decision_ns=decision_ns,
                        input_start_ns=decision_ns - 1,
                        input_available_ns=decision_ns,
                        input_dependencies=[
                            dict(
                                artifact_sha="a" * 64,
                                event_ns=decision_ns - 1,
                                available_ns=decision_ns,
                            )
                        ],
                        values=[[feature], [None]],
                        targets=[target],
                        target_status=["observed"],
                        target_available_ns=[decision_ns + 10**9],
                        target_maturity_ns=[decision_ns + 10**9],
                        target_captures=[["b" * 64]],
                    )
                )
    return sorted(rows, key=lambda row: (row["pool"], row["decision_ns"], row["id"]))


def _build(tmp_path, name, *, perturb_test):
    rows = _fixture(perturb_test=perturb_test)
    result = build(
        tmp_path / name,
        rows,
        sources=[dict(kind="synthetic-role-isolation", pin=("d" if perturb_test else "c") * 64)],
        schema=SCHEMA,
        cutoff=300 * 10**9,
        chunk=10,
        role="train",
    )
    return tmp_path / name, result["snapshot"]


def _train_order(store, snapshot_pin, *, sampler=None):
    snapshot = Snapshot(store, snapshot_pin)
    batches_iter = batches(
        snapshot,
        PLAN,
        "train",
        CONFIG["batch"],
        seed=CONFIG["seed"],
        epoch=0,
        **({"sampler": sampler} if sampler is not None else {}),
    )
    return [identifier for packed in batches_iter for identifier in packed[3]]


def _weights(path):
    return torch.load(path / "artifact/weights.pt", weights_only=True)


def _equal_weights(left, right):
    return all(torch.equal(left[key], right[key]) for key in left)


def test_legacy_training_order_depends_on_test_payload(tmp_path):
    base_store, base_pin = _build(tmp_path, "baseline", perturb_test=False)
    changed_store, changed_pin = _build(tmp_path, "changed-test", perturb_test=True)
    base, changed = Snapshot(base_store, base_pin), Snapshot(changed_store, changed_pin)
    base_geometry = [(part["count"], part["bounds"]) for part in base.parts]
    changed_geometry = [(part["count"], part["bounds"]) for part in changed.parts]
    assert base_geometry == changed_geometry
    base_order = _train_order(base_store, base_pin)
    changed_order = _train_order(changed_store, changed_pin)
    assert sorted(base_order) == sorted(changed_order) and base_order != changed_order

    base_report = train(base_store, base_pin, tmp_path / "base-fit", plan=PLAN, config=CONFIG)
    changed_report = train(
        changed_store, changed_pin, tmp_path / "changed-fit", plan=PLAN, config=CONFIG
    )
    assert (
        base_report["statistics"] == changed_report["statistics"]
    )  # test payload must not affect train-only moments/baselines
    base_weights = _weights(tmp_path / "base-fit")
    changed_weights = _weights(tmp_path / "changed-fit")
    assert not _equal_weights(base_weights, changed_weights)

    receipt = dict(
        version="brain-training-role-isolation-legacy-result-v1",
        snapshots=[base_pin, changed_pin],
        partition_geometry=base_geometry,
        baseline_train_order_sha256=hashlib.sha256(
            json.dumps(base_order, separators=(",", ":")).encode()
        ).hexdigest(),
        perturbed_train_order_sha256=hashlib.sha256(
            json.dumps(changed_order, separators=(",", ":")).encode()
        ).hexdigest(),
        training_statistics_identical=True,
        baseline_weights_sha256=file_hash(tmp_path / "base-fit/artifact/weights.pt"),
        perturbed_weights_sha256=file_hash(tmp_path / "changed-fit/artifact/weights.pt"),
        legacy_order_changed=True,
        legacy_weights_changed=True,
    )
    (tmp_path / "legacy-result.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    )
