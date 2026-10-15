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


def auditable_parent_child(tmp_path, store, snapshot_pin, *, parent_transform="identity"):
    parent_artifact, parent_pin, _ = sealed_tiny_artifact(
        tmp_path / "parent", target_transform=parent_transform
    )
    child_artifact, _, _ = sealed_tiny_artifact(tmp_path / "child")
    config = learning.recipe(CONFIG)
    scratch = tmp_path / "child-statistics-scratch"
    scratch.mkdir()
    stats = learning.prepare(
        Snapshot(store, snapshot_pin),
        PLAN,
        scratch,
        config["batch"],
        config["normalization"],
        config["objective"],
    )
    parent_stats = json.loads((parent_artifact / "statistics.json").read_text())
    stats.update(
        mean=parent_stats["mean"],
        scale=parent_stats["scale"],
        normalization="frozen_parent_normalizer",
        normalizer_parent=parent_pin,
    )
    report = json.loads((child_artifact / "report.json").read_text())
    report["statistics"] = stats
    report["binding"].update(
        parent=parent_pin,
        snapshot=snapshot_pin,
        plan=PLAN,
        config=config,
        statistics=digest(stats),
    )
    atomic_json(child_artifact / "statistics.json", stats, replace=True)
    atomic_json(child_artifact / "report.json", report, replace=True)
    (child_artifact / "manifest.json").unlink()
    child_pin = seal_files(child_artifact)
    return parent_artifact, parent_pin, child_artifact, child_pin, stats


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


def test_statistics_derivation_audit_requires_verified_parent_reference(tmp_path, monkeypatch):
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
        match="experience_statistics_audit_parent_reference_required",
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


def test_statistics_derivation_audit_matches_verified_parent_overlay(tmp_path):
    store, snapshot_pin = dataset(tmp_path)
    parent_artifact, parent_pin, child_artifact, child_pin, _ = auditable_parent_child(
        tmp_path, store, snapshot_pin
    )
    scratch = tmp_path / "parent-overlay-scratch"
    scratch.mkdir()
    parent_before = {
        path.relative_to(parent_artifact): path.read_bytes()
        for path in parent_artifact.iterdir()
        if path.is_file()
    }

    result = statistics_audit.audit_statistics_derivation(
        child_artifact,
        child_pin,
        store,
        snapshot_pin,
        PLAN,
        parent_artifact=parent_artifact,
        parent_pin=parent_pin,
        scratch_parent=scratch,
    )

    assert result["status"] == "matched_parent_overlay"
    assert result["matches"] is True
    assert result["parent_artifact_pin"] == parent_pin
    assert result["parent_statistics_pin"] == digest(
        json.loads((parent_artifact / "statistics.json").read_text())
    )
    assert list(scratch.iterdir()) == []
    assert parent_before == {
        path.relative_to(parent_artifact): path.read_bytes()
        for path in parent_artifact.iterdir()
        if path.is_file()
    }


@pytest.mark.parametrize("field", ["baseline", "denominator", "mean", "scale"])
def test_statistics_derivation_audit_detects_parent_child_statistics_mutations(tmp_path, field):
    store, snapshot_pin = dataset(tmp_path)
    parent_artifact, parent_pin, child_artifact, child_pin, _ = auditable_parent_child(
        tmp_path, store, snapshot_pin
    )
    stats = json.loads((child_artifact / "statistics.json").read_text())
    if field == "baseline":
        stats["baseline"][0][0] += 0.0001
    elif field == "denominator":
        key = next(key for key in stats["denominators"] if key.startswith("train/"))
        stats["denominators"][key] += 1
    elif field == "mean":
        stats["mean"][0] += 0.5
    else:
        stats["scale"][0] *= 1.1
    report = json.loads((child_artifact / "report.json").read_text())
    report["statistics"] = stats
    report["binding"]["statistics"] = digest(stats)
    atomic_json(child_artifact / "statistics.json", stats, replace=True)
    atomic_json(child_artifact / "report.json", report, replace=True)
    (child_artifact / "manifest.json").unlink()
    child_pin = seal_files(child_artifact)

    result = statistics_audit.audit_statistics_derivation(
        child_artifact,
        child_pin,
        store,
        snapshot_pin,
        PLAN,
        parent_artifact=parent_artifact,
        parent_pin=parent_pin,
    )
    assert result["status"] == "mismatch"
    assert result["matches"] is False


@pytest.mark.parametrize("problem", ["pin", "self-pin", "self-path", "model", "transform"])
def test_statistics_derivation_audit_rejects_unmatched_parent_before_scratch(tmp_path, problem):
    store, snapshot_pin = dataset(tmp_path)
    parent_artifact, parent_pin, child_artifact, child_pin, _ = auditable_parent_child(
        tmp_path,
        store,
        snapshot_pin,
        parent_transform="log1p_nonnegative" if problem == "transform" else "identity",
    )
    if problem == "pin":
        parent_pin = "f" * 64
        error = "experience_statistics_audit_parent_pin_binding"
    elif problem == "self-pin":
        parent_pin = child_pin
        error = "experience_statistics_audit_self_parent"
    elif problem == "self-path":
        parent_artifact = child_artifact
        error = "experience_statistics_audit_self_parent"
    elif problem == "model":
        spec = json.loads((parent_artifact / "model.json").read_text())
        spec["schema"]["features"][0]["name"] += "_other"
        report = json.loads((parent_artifact / "report.json").read_text())
        report["binding"]["model"] = spec
        atomic_json(parent_artifact / "model.json", spec, replace=True)
        atomic_json(parent_artifact / "report.json", report, replace=True)
        (parent_artifact / "manifest.json").unlink()
        parent_pin = seal_files(parent_artifact)
        child_stats = json.loads((child_artifact / "statistics.json").read_text())
        child_stats["normalizer_parent"] = parent_pin
        child_report = json.loads((child_artifact / "report.json").read_text())
        child_report["statistics"] = child_stats
        child_report["binding"]["parent"] = parent_pin
        child_report["binding"]["statistics"] = digest(child_stats)
        atomic_json(child_artifact / "statistics.json", child_stats, replace=True)
        atomic_json(child_artifact / "report.json", child_report, replace=True)
        (child_artifact / "manifest.json").unlink()
        child_pin = seal_files(child_artifact)
        error = "experience_statistics_audit_parent_model_binding"
    else:
        error = "experience_statistics_audit_parent_transform_binding"

    scratch = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match=error):
        statistics_audit.audit_statistics_derivation(
            child_artifact,
            child_pin,
            store,
            snapshot_pin,
            PLAN,
            parent_artifact=parent_artifact,
            parent_pin=parent_pin,
            scratch_parent=scratch,
        )
    assert not scratch.exists()


def test_statistics_derivation_audit_refuses_parent_scratch_and_preserves_parent(tmp_path):
    store, snapshot_pin = dataset(tmp_path)
    parent_artifact, parent_pin, child_artifact, child_pin, _ = auditable_parent_child(
        tmp_path, store, snapshot_pin
    )
    before = {
        path.relative_to(parent_artifact): path.read_bytes()
        for path in parent_artifact.iterdir()
        if path.is_file()
    }
    scratch = parent_artifact / "audit-scratch"

    with pytest.raises(
        ValueError,
        match="experience_statistics_audit_scratch_overlaps_input",
    ):
        statistics_audit.audit_statistics_derivation(
            child_artifact,
            child_pin,
            store,
            snapshot_pin,
            PLAN,
            parent_artifact=parent_artifact,
            parent_pin=parent_pin,
            scratch_parent=scratch,
        )
    assert not scratch.exists()
    assert before == {
        path.relative_to(parent_artifact): path.read_bytes()
        for path in parent_artifact.iterdir()
        if path.is_file()
    }


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


def test_statistics_derivation_audit_cleans_parent_scratch_after_failure(tmp_path, monkeypatch):
    store, snapshot_pin = dataset(tmp_path)
    parent_artifact, parent_pin, child_artifact, child_pin, _ = auditable_parent_child(
        tmp_path, store, snapshot_pin
    )
    scratch = tmp_path / "failed-parent-audit-scratch"
    scratch.mkdir()
    parent_before = {
        path.relative_to(parent_artifact): path.read_bytes()
        for path in parent_artifact.iterdir()
        if path.is_file()
    }

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic derivation failure")

    monkeypatch.setattr(statistics_audit, "prepare", fail)
    with pytest.raises(RuntimeError, match="synthetic derivation failure"):
        statistics_audit.audit_statistics_derivation(
            child_artifact,
            child_pin,
            store,
            snapshot_pin,
            PLAN,
            parent_artifact=parent_artifact,
            parent_pin=parent_pin,
            scratch_parent=scratch,
        )
    assert list(scratch.iterdir()) == []
    assert parent_before == {
        path.relative_to(parent_artifact): path.read_bytes()
        for path in parent_artifact.iterdir()
        if path.is_file()
    }
