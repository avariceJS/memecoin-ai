"""Opt-in derivation audit for sealed research statistics; never trains or scores a model."""

import tempfile
from pathlib import Path

from .learning import prepare, recipe
from .predict import Predictor
from .records import split_plan
from .storage import PIN, Snapshot, code_identity, digest, safe_path


def audit_statistics_lineage(nodes, *, scratch_parent=None):
    """Audit an explicit child-to-root statistics lineage of at most three artifacts.

    Each descriptor has exactly ``artifact``, ``artifact_pin``, ``store``,
    ``snapshot_pin``, and ``plan``. The final artifact must be seeded-fresh;
    every preceding artifact must name the next descriptor's pin as its parent.
    No ancestry is discovered implicitly.
    """
    if not isinstance(nodes, (list, tuple)) or not 1 <= len(nodes) <= 3:
        raise ValueError("experience_statistics_lineage_node_count")
    required = {"artifact", "artifact_pin", "store", "snapshot_pin", "plan"}
    prepared = []
    artifact_paths = set()
    artifact_pins = set()

    # Validate every supplied node and all chain edges before any derivation scratch is made.
    for descriptor in nodes:
        if not isinstance(descriptor, dict) or set(descriptor) != required:
            raise ValueError("experience_statistics_lineage_descriptor")
        artifact = safe_path(descriptor["artifact"])
        store = safe_path(descriptor["store"])
        artifact_key = artifact.resolve()
        pin = descriptor["artifact_pin"]
        if not isinstance(pin, str) or not PIN.fullmatch(pin):
            raise ValueError("experience_statistics_lineage_artifact_pin")
        if artifact_key in artifact_paths or pin in artifact_pins:
            raise ValueError("experience_statistics_lineage_duplicate")
        artifact_paths.add(artifact_key)
        artifact_pins.add(pin)

        predictor = Predictor(artifact, pin)
        binding = predictor.report.get("binding", {})
        if binding.get("snapshot") != descriptor["snapshot_pin"]:
            raise ValueError("experience_statistics_audit_snapshot_binding")
        expected_plan = split_plan(descriptor["plan"])
        if binding.get("plan") != expected_plan:
            raise ValueError("experience_statistics_audit_plan_binding")
        snapshot = Snapshot(store, descriptor["snapshot_pin"])
        if snapshot.manifest["binding"].get("role", "train") != "train":
            raise ValueError("experience_statistics_audit_reserved_snapshot")
        if predictor.spec.get("schema") != snapshot.schema:
            raise ValueError("experience_statistics_audit_model_schema_binding")
        config = binding.get("config")
        try:
            valid_recipe = isinstance(config, dict) and recipe(config) == config
        except (TypeError, ValueError):
            valid_recipe = False
        if not valid_recipe:
            raise ValueError("experience_statistics_audit_unsupported_legacy_recipe")
        prepared.append(
            dict(
                artifact=artifact,
                artifact_pin=pin,
                store=store,
                snapshot_pin=descriptor["snapshot_pin"],
                plan=expected_plan,
                predictor=predictor,
            )
        )

    for index in range(len(prepared) - 1):
        child, parent = prepared[index], prepared[index + 1]
        child_predictor, parent_predictor = child["predictor"], parent["predictor"]
        if (
            child_predictor.report["binding"].get("initialization")
            != "parent_weights_frozen_normalizer_fresh_optimizer"
        ):
            raise ValueError("experience_statistics_lineage_initialization")
        if child_predictor.report["binding"].get("parent") != parent["artifact_pin"]:
            raise ValueError("experience_statistics_lineage_parent_adjacency")
        if child_predictor.spec != parent_predictor.spec:
            raise ValueError("experience_statistics_audit_parent_model_binding")
        if _target_transforms(child_predictor) != _target_transforms(parent_predictor):
            raise ValueError("experience_statistics_audit_parent_transform_binding")
        if (
            child_predictor.stats.get("normalization") != "frozen_parent_normalizer"
            or child_predictor.stats.get("normalizer_parent") != parent["artifact_pin"]
        ):
            raise ValueError("experience_statistics_audit_parent_normalizer_binding")
    if prepared[-1]["predictor"].report["binding"].get("parent") is not None:
        raise ValueError("experience_statistics_lineage_omitted_parent")
    if prepared[-1]["predictor"].report["binding"].get("initialization") != "seeded_fresh":
        raise ValueError("experience_statistics_lineage_terminal_initialization")
    if (
        prepared[-1]["predictor"].stats.get("normalization") == "frozen_parent_normalizer"
        or "normalizer_parent" in prepared[-1]["predictor"].stats
    ):
        raise ValueError("experience_statistics_audit_parent_normalizer_binding")

    raw_parent = tempfile.gettempdir() if scratch_parent is None else scratch_parent
    if scratch_parent is None:
        raw_parent = Path(raw_parent).resolve()
    scratch = safe_path(raw_parent)
    scratch_resolved = scratch.resolve()
    if not scratch.is_dir():
        raise ValueError("experience_statistics_audit_scratch_parent")
    protected_roots = [node[key] for node in prepared for key in ("artifact", "store")]
    if any(
        scratch_resolved == root.resolve() or root.resolve() in scratch_resolved.parents
        for root in protected_roots
    ):
        raise ValueError("experience_statistics_audit_scratch_overlaps_input")

    results = []
    for index, node in enumerate(prepared):
        parent = prepared[index + 1] if index + 1 < len(prepared) else None
        result = audit_statistics_derivation(
            node["artifact"],
            node["artifact_pin"],
            node["store"],
            node["snapshot_pin"],
            node["plan"],
            parent_artifact=parent["artifact"] if parent else None,
            parent_pin=parent["artifact_pin"] if parent else None,
            scratch_parent=scratch,
        )
        results.append(result)
    matches = all(result["matches"] for result in results)
    return dict(
        status="matched_lineage" if matches else "mismatch",
        matches=matches,
        nodes=results,
        derivation_code_pin=digest(code_identity()),
    )


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
