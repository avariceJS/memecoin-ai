"""Local research job ownership and immutable model lineage, separate from production registry."""

import json
import re
import time

from .records import split_plan
from .storage import Snapshot, atomic_json, code_identity, digest, file_hash, lock, safe_path


def run(
    root,
    *,
    name,
    owner,
    store,
    snapshot,
    split,
    config,
    parent=None,
    stop_after=None,
    diversity=None,
):
    """Same declaration resumes; changing recipe/data/parent requires a new generation name."""
    from .learning import recipe, train

    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", name):
        raise ValueError("experience_generation_name")
    if not isinstance(owner, str) or not owner:
        raise ValueError("experience_generation_owner")
    dataset = Snapshot(store, snapshot)
    if dataset.manifest["binding"].get("role", "train") != "train":
        raise ValueError("experience_reserved_snapshot")
    declaration = dict(
        namespace="brain-research-generation-v1",
        name=name,
        owner=owner,
        snapshot=snapshot,
        split=split_plan(split),
        trainer="dense-quantile-v1",
        config=recipe(config),
        parent=parent["pin"] if parent else None,
        code=code_identity(),
    )
    if diversity is not None:
        declaration["diversity"] = diversity["pin"]
    identity = digest(declaration)
    root = safe_path(root)
    root.mkdir(parents=True, exist_ok=True)
    with lock(root / ".registry.lock"):
        for part in ("names", "jobs", "generations"):
            safe_path(root / part).mkdir(exist_ok=True)
        atomic_json(root / "names" / (name + ".json"), dict(id=identity, declaration=declaration))
        job = safe_path(root / "jobs" / identity)
        job.mkdir(exist_ok=True)
        atomic_json(job / "declaration.json", declaration)
    with lock(job / ".owner.lock"):
        if (root / "generations" / (identity + ".json")).exists():
            record, _ = get(root, identity)
            outcome = dict(status="completed", generation=identity, artifact=record["artifact"])
            atomic_json(job / "status.json", outcome, replace=True)
            return dict(outcome, job=str(job))
        attempts = job / "attempts"
        attempts.mkdir(exist_ok=True)
        attempt = sum(p.stem.isdigit() for p in attempts.glob("*.json"))
        started = time.time_ns()
        atomic_json(attempts / f"{attempt:08d}.json", dict(status="started", at_ns=started))
        atomic_json(job / "status.json", dict(status="running", attempt=attempt), replace=True)
        try:
            report = train(
                store,
                snapshot,
                job / "training",
                plan=split,
                config=config,
                parent=parent,
                stop_after=stop_after,
                diversity=diversity,
            )
        except Exception as exc:
            atomic_json(
                job / "status.json",
                dict(status="failed", attempt=attempt, error_type=type(exc).__name__),
                replace=True,
            )
            atomic_json(
                attempts / f"{attempt:08d}-outcome.json",
                dict(status="failed", error_type=type(exc).__name__),
            )
            raise
        outcome = dict(status=report["status"], attempt=attempt)
        if report["status"] == "completed":
            artifact = job / "training/artifact"
            generation = dict(
                id=identity,
                declaration=declaration,
                artifact=file_hash(artifact / "manifest.json"),
                ancestors=report["ancestors"],
                seen_end_s=report["seen_end_s"],
                model=report["binding"]["model"],
                rows_seen=report["rows_seen"],
                production_eligible=False,
            )
            if "seen_end_coordinate" in report:
                generation["seen_end_coordinate"] = report["seen_end_coordinate"]
            atomic_json(root / "generations" / (identity + ".json"), generation)
            outcome.update(generation=identity, artifact=generation["artifact"])
        atomic_json(attempts / f"{attempt:08d}-outcome.json", outcome)
        atomic_json(job / "status.json", outcome, replace=True)
        return dict(outcome, job=str(job))


def get(root, identity):
    from .storage import PIN

    if not isinstance(identity, str) or not PIN.fullmatch(identity):
        raise ValueError("experience_generation_pin")
    root = safe_path(root)
    record = json.loads(safe_path(root / "generations" / (identity + ".json")).read_text())
    if record["id"] != identity or digest(record["declaration"]) != identity:
        raise ValueError("experience_generation_binding")
    artifact = root / "jobs" / identity / "training/artifact"
    from .predict import Predictor

    model = Predictor(artifact, record["artifact"])
    declaration = record["declaration"]
    binding = model.report["binding"]
    if (
        record["model"] != model.spec
        or record["ancestors"] != model.report["ancestors"]
        or any(
            binding[k] != declaration[d]
            for k, d in (
                ("snapshot", "snapshot"),
                ("plan", "split"),
                ("config", "config"),
                ("parent", "parent"),
                ("code", "code"),
            )
        )
        or binding.get("diversity") != declaration.get("diversity")
        or record["rows_seen"] != model.report["rows_seen"]
        or record["seen_end_s"] != model.report["seen_end_s"]
    ):
        raise ValueError("experience_generation_binding")
    return record, artifact
