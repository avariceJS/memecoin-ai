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
    scratch_parent=None,
):
    """Recompute train-only statistics in disposable scratch and compare with a sealed artifact.

    The caller supplies expected snapshot and split pins independently. A false `matches` result
    means the sealed statistics are structurally valid but do not derive from that exact source,
    split, and current numerical/code environment.
    """
    artifact_root, store_root = safe_path(artifact), safe_path(store)
    predictor = Predictor(artifact_root, artifact_pin)
    binding = predictor.report.get("binding", {})
    if binding.get("snapshot") != snapshot_pin:
        raise ValueError("experience_statistics_audit_snapshot_binding")
    expected_plan = split_plan(plan)
    if binding.get("plan") != expected_plan:
        raise ValueError("experience_statistics_audit_plan_binding")
    if (
        binding.get("parent") is not None
        or predictor.stats.get("normalization") == "frozen_parent_normalizer"
        or "normalizer_parent" in predictor.stats
    ):
        raise ValueError("experience_statistics_audit_unsupported_parent_normalizer")
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
    if any(
        resolved_parent == root.resolve() or root.resolve() in resolved_parent.parents
        for root in (artifact_root, store_root)
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
    artifact_statistics_pin = digest(predictor.stats)
    derived_statistics_pin = digest(derived)
    return dict(
        status="matched" if artifact_statistics_pin == derived_statistics_pin else "mismatch",
        matches=artifact_statistics_pin == derived_statistics_pin,
        artifact_pin=artifact_pin,
        snapshot_pin=snapshot_pin,
        plan_pin=digest(expected_plan),
        artifact_statistics_pin=artifact_statistics_pin,
        derived_statistics_pin=derived_statistics_pin,
        derivation_code_pin=digest(code_identity()),
    )
