import json

import pytest

pytest.importorskip("numpy")
pytest.importorskip("torch")

from test_experience_generations import dataset, sealed_tiny_artifact  # noqa: E402
from test_experience_learning import CONFIG, PLAN  # noqa: E402

from memecoin_experience import learning, statistics_audit  # noqa: E402
from memecoin_experience.predict import Predictor  # noqa: E402
from memecoin_experience.storage import (  # noqa: E402
    Snapshot,
    atomic_json,
    digest,
    seal_files,
)


def auditable_artifact(tmp_path, store, snapshot_pin):
    artifact, _, _ = sealed_tiny_artifact(tmp_path / "artifact")
    config = learning.recipe(CONFIG)
    scratch = tmp_path / "statistics-scratch"
    scratch.mkdir()
    stats = learning.prepare(
        Snapshot(store, snapshot_pin),
        PLAN,
        scratch,
        config["batch"],
        config["normalization"],
        config["objective"],
    )
    report = json.loads((artifact / "report.json").read_text())
    report["statistics"] = stats
    report["binding"].update(
        snapshot=snapshot_pin,
        plan=PLAN,
        config=config,
        statistics=digest(stats),
    )
    atomic_json(artifact / "statistics.json", stats, replace=True)
    atomic_json(artifact / "report.json", report, replace=True)
    (artifact / "manifest.json").unlink()
    artifact_pin = seal_files(artifact)
    return artifact, artifact_pin, stats


def test_statistics_derivation_audit_matches_and_cleans_scratch(tmp_path):
    store, snapshot_pin = dataset(tmp_path)
    artifact, artifact_pin, _ = auditable_artifact(tmp_path, store, snapshot_pin)
    scratch = tmp_path / "audit-scratch"
    scratch.mkdir()

    result = statistics_audit.audit_statistics_derivation(
        artifact,
        artifact_pin,
        store,
        snapshot_pin,
        PLAN,
        scratch_parent=scratch,
    )

    assert result["status"] == "matched"
    assert result["matches"] is True
    assert result["artifact_statistics_pin"] == result["derived_statistics_pin"]
    assert result["snapshot_pin"] == snapshot_pin
    assert list(scratch.iterdir()) == []


def test_statistics_derivation_audit_detects_coherently_resealed_mutation(tmp_path):
    store, snapshot_pin = dataset(tmp_path)
    artifact, artifact_pin, _ = auditable_artifact(tmp_path, store, snapshot_pin)
    stats = json.loads((artifact / "statistics.json").read_text())
    stats["mean"][0] += 0.25
    report = json.loads((artifact / "report.json").read_text())
    report["statistics"] = stats
    report["binding"]["statistics"] = digest(stats)
    atomic_json(artifact / "statistics.json", stats, replace=True)
    atomic_json(artifact / "report.json", report, replace=True)
    (artifact / "manifest.json").unlink()
    artifact_pin = seal_files(artifact)

    # The regular structural loader accepts this internally consistent sealed mutation.
    assert Predictor(artifact, artifact_pin).stats == stats
    result = statistics_audit.audit_statistics_derivation(
        artifact, artifact_pin, store, snapshot_pin, PLAN
    )
    assert result["status"] == "mismatch"
    assert result["matches"] is False
    assert result["artifact_statistics_pin"] != result["derived_statistics_pin"]


def test_statistics_derivation_audit_rejects_wrong_snapshot_and_plan(tmp_path):
    store, snapshot_pin = dataset(tmp_path)
    artifact, artifact_pin, _ = auditable_artifact(tmp_path, store, snapshot_pin)
    wrong_store, wrong_snapshot_pin = dataset(tmp_path / "wrong-source", start=100_000)

    with pytest.raises(ValueError, match="experience_statistics_audit_snapshot_binding"):
        statistics_audit.audit_statistics_derivation(
            artifact, artifact_pin, wrong_store, wrong_snapshot_pin, PLAN
        )
    wrong_plan = dict(PLAN, test_end_s=PLAN["test_end_s"] + 30_000)
    with pytest.raises(ValueError, match="experience_statistics_audit_plan_binding"):
        statistics_audit.audit_statistics_derivation(
            artifact, artifact_pin, store, snapshot_pin, wrong_plan
        )


def test_statistics_derivation_audit_rejects_parent_normalizer_without_recomputing(
    tmp_path, monkeypatch
):
    store, snapshot_pin = dataset(tmp_path)
    artifact, artifact_pin, _ = auditable_artifact(tmp_path, store, snapshot_pin)
    report = json.loads((artifact / "report.json").read_text())
    report["binding"]["parent"] = "a" * 64
    atomic_json(artifact / "report.json", report, replace=True)
    (artifact / "manifest.json").unlink()
    artifact_pin = seal_files(artifact)
    scratch = tmp_path / "parent-audit-scratch"

    def should_not_recompute(*args, **kwargs):
        raise AssertionError("unsupported parent normalizer reached statistics derivation")

    monkeypatch.setattr(statistics_audit, "prepare", should_not_recompute)
    with pytest.raises(
        ValueError,
        match="experience_statistics_audit_unsupported_parent_normalizer",
    ):
        statistics_audit.audit_statistics_derivation(
            artifact,
            artifact_pin,
            store,
            snapshot_pin,
            PLAN,
            scratch_parent=scratch,
        )
    assert not scratch.exists()


def test_statistics_derivation_audit_rejects_artifact_schema_from_another_source(tmp_path):
    store, snapshot_pin = dataset(tmp_path)
    artifact, artifact_pin, _ = auditable_artifact(tmp_path, store, snapshot_pin)
    spec = json.loads((artifact / "model.json").read_text())
    spec["schema"]["features"][0]["name"] += "_other_source"
    report = json.loads((artifact / "report.json").read_text())
    report["binding"]["model"] = spec
    atomic_json(artifact / "model.json", spec, replace=True)
    atomic_json(artifact / "report.json", report, replace=True)
    (artifact / "manifest.json").unlink()
    artifact_pin = seal_files(artifact)

    with pytest.raises(
        ValueError,
        match="experience_statistics_audit_model_schema_binding",
    ):
        statistics_audit.audit_statistics_derivation(
            artifact, artifact_pin, store, snapshot_pin, PLAN
        )


@pytest.mark.parametrize("input_name", ["artifact", "store"])
def test_statistics_derivation_audit_refuses_scratch_inside_inputs(tmp_path, input_name):
    store, snapshot_pin = dataset(tmp_path)
    artifact, artifact_pin, _ = auditable_artifact(tmp_path, store, snapshot_pin)
    input_root = artifact if input_name == "artifact" else store
    scratch = input_root / "audit-scratch"
    before = {path.relative_to(input_root) for path in input_root.rglob("*")}

    with pytest.raises(
        ValueError,
        match="experience_statistics_audit_scratch_overlaps_input",
    ):
        statistics_audit.audit_statistics_derivation(
            artifact,
            artifact_pin,
            store,
            snapshot_pin,
            PLAN,
            scratch_parent=scratch,
        )
    after = {path.relative_to(input_root) for path in input_root.rglob("*")}
    assert after == before


def test_statistics_derivation_audit_checks_default_temp_parent(tmp_path, monkeypatch):
    store, snapshot_pin = dataset(tmp_path)
    artifact, artifact_pin, _ = auditable_artifact(tmp_path, store, snapshot_pin)
    before = {path.relative_to(store) for path in store.rglob("*")}
    monkeypatch.setattr(statistics_audit.tempfile, "tempdir", str(store))

    with pytest.raises(
        ValueError,
        match="experience_statistics_audit_scratch_overlaps_input",
    ):
        statistics_audit.audit_statistics_derivation(
            artifact, artifact_pin, store, snapshot_pin, PLAN
        )
    after = {path.relative_to(store) for path in store.rglob("*")}
    assert after == before


def test_statistics_derivation_audit_cleans_scratch_after_failure(tmp_path, monkeypatch):
    store, snapshot_pin = dataset(tmp_path)
    artifact, artifact_pin, _ = auditable_artifact(tmp_path, store, snapshot_pin)
    scratch = tmp_path / "failed-audit-scratch"
    scratch.mkdir()

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic derivation failure")

    monkeypatch.setattr(statistics_audit, "prepare", fail)
    with pytest.raises(RuntimeError, match="synthetic derivation failure"):
        statistics_audit.audit_statistics_derivation(
            artifact,
            artifact_pin,
            store,
            snapshot_pin,
            PLAN,
            scratch_parent=scratch,
        )
    assert list(scratch.iterdir()) == []
