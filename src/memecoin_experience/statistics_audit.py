"""Opt-in derivation audit for sealed research statistics; never trains or scores a model."""

import tempfile
from pathlib import Path

from .learning import prepare, recipe
from .predict import Predictor
from .records import split_plan
from .storage import Snapshot, code_identity, digest, safe_path


def audit_statistics_derivation(
    artifact,
    artifact_pin,
    store,
    snapshot_pin,
    plan,
    *,
    parent_artifact=None,
    parent_pin=None,
    scratch_parent=None,
):
    """Recompute train-only stats, including one verified parent-normalizer overlay if needed.

    The caller supplies expected snapshot and split pins independently. A false `matches` result
    means the sealed statistics are structurally valid but do not derive from that exact source,
    split, and current numerical/code environment. A parent overlay verifies only the one-hop stats
    construction; it does not audit the parent's stats ancestry or training lineage.
    """
    artifact_root, store_root = safe_path(artifact), safe_path(store)
    predictor = Predictor(artifact_root, artifact_pin)
    binding = predictor.report.get("binding", {})
    if binding.get("snapshot") != snapshot_pin:
        raise ValueError("experience_statistics_audit_snapshot_binding")
    expected_plan = split_plan(plan)
    if binding.get("plan") != expected_plan:
        raise ValueError("experience_statistics_audit_plan_binding")
    recorded_parent = binding.get("parent")
    if (parent_artifact is None) != (parent_pin is None):
        raise ValueError("experience_statistics_audit_parent_reference")
    parent_predictor = None
    parent_root = None
    if recorded_parent is None:
        if parent_artifact is not None:
            raise ValueError("experience_statistics_audit_unexpected_parent")
        if (
            predictor.stats.get("normalization") == "frozen_parent_normalizer"
            or "normalizer_parent" in predictor.stats
        ):
            raise ValueError("experience_statistics_audit_parent_normalizer_binding")
    else:
        if parent_artifact is None:
            raise ValueError("experience_statistics_audit_parent_reference_required")
        parent_root = safe_path(parent_artifact)
        if parent_pin == artifact_pin or parent_root.resolve() == artifact_root.resolve():
            raise ValueError("experience_statistics_audit_self_parent")
        if parent_pin != recorded_parent:
            raise ValueError("experience_statistics_audit_parent_pin_binding")
        parent_predictor = Predictor(parent_root, parent_pin)
        if parent_predictor.spec != predictor.spec:
            raise ValueError("experience_statistics_audit_parent_model_binding")
        if _target_transforms(parent_predictor) != _target_transforms(predictor):
            raise ValueError("experience_statistics_audit_parent_transform_binding")
        if (
            predictor.stats.get("normalization") != "frozen_parent_normalizer"
            or predictor.stats.get("normalizer_parent") != parent_pin
        ):
            raise ValueError("experience_statistics_audit_parent_normalizer_binding")
    config = binding.get("config")
    try:
        valid_recipe = isinstance(config, dict) and recipe(config) == config
    except (TypeError, ValueError):
        valid_recipe = False
    if not valid_recipe:
        raise ValueError("experience_statistics_audit_unsupported_legacy_recipe")

    snapshot = Snapshot(store_root, snapshot_pin)
    if snapshot.manifest["binding"].get("role", "train") != "train":
        raise ValueError("experience_statistics_audit_reserved_snapshot")
    if predictor.spec.get("schema") != snapshot.schema:
        raise ValueError("experience_statistics_audit_model_schema_binding")

    raw_parent = tempfile.gettempdir() if scratch_parent is None else scratch_parent
    if scratch_parent is None:
        raw_parent = Path(raw_parent).resolve()
    parent = safe_path(raw_parent)
    resolved_parent = parent.resolve()
    protected_roots = (artifact_root, store_root) + ((parent_root,) if parent_root else ())
    if any(
        resolved_parent == root.resolve() or root.resolve() in resolved_parent.parents
        for root in protected_roots
    ):
        raise ValueError("experience_statistics_audit_scratch_overlaps_input")
    if not parent.is_dir():
        raise ValueError("experience_statistics_audit_scratch_parent")
    with tempfile.TemporaryDirectory(prefix="experience-statistics-audit-", dir=parent) as temp:
        derived = prepare(
            snapshot,
            expected_plan,
            Path(temp),
            config["batch"],
            config["normalization"],
            config["objective"],
        )
        if parent_predictor is not None:
            derived.update(
                mean=parent_predictor.stats["mean"],
                scale=parent_predictor.stats["scale"],
                normalization="frozen_parent_normalizer",
                normalizer_parent=parent_pin,
            )
    artifact_statistics_pin = digest(predictor.stats)
    derived_statistics_pin = digest(derived)
    matches = artifact_statistics_pin == derived_statistics_pin
    status = (
        "mismatch"
        if not matches
        else "matched_parent_overlay"
        if parent_predictor is not None
        else "matched"
    )
    return dict(
        status=status,
        matches=artifact_statistics_pin == derived_statistics_pin,
        artifact_pin=artifact_pin,
        parent_artifact_pin=parent_pin,
        parent_statistics_pin=(digest(parent_predictor.stats) if parent_predictor else None),
        snapshot_pin=snapshot_pin,
        plan_pin=digest(expected_plan),
        artifact_statistics_pin=artifact_statistics_pin,
        derived_statistics_pin=derived_statistics_pin,
        derivation_code_pin=digest(code_identity()),
    )


def _target_transforms(predictor):
    stats = predictor.stats
    if "target_transforms" in stats:
        return stats["target_transforms"]
    schema = predictor.spec["schema"]
    count = len(schema.get("targets", schema.get("horizons", [])))
    return ["identity"] * count
