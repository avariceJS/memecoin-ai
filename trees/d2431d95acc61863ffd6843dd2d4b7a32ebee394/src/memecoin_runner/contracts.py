"""Byte-bound execution plans and separate admission/release decisions."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

from memecoin_ml.data import _validate, read, sha
from memecoin_registry.contracts import digest
from memecoin_registry.qualification import interval_end
from memecoin_research.framework import DAY, materialize, trial_plan, validate_spec

Row = dict[str, Any]


def sources() -> Row:
    import memecoin_ai
    import memecoin_ml
    import memecoin_registry
    import memecoin_research

    result = {}
    for package in (
        Path(__file__).parent,
        Path(memecoin_ai.__file__).parent,
        Path(memecoin_ml.__file__).parent,
        Path(memecoin_registry.__file__).parent,
        Path(memecoin_research.__file__).parent,
    ):
        for p in sorted(package.glob("*.py")):
            result[f"{package.name}/{p.name}"] = sha(p)
    return result


def partition(bundle: Row, bounds: Row) -> Row:
    _validate(bundle)
    a, b, c, d, embargo = (
        bounds[k] for k in ("start", "train_end", "validation_end", "test_end", "embargo")
    )
    if not 0 <= a < b < c <= d or embargo < 0:
        raise ValueError("invalid_chronological_boundaries")
    parts: Row = dict(train=[], validation=[], test=[], purged=[])
    for s in bundle["samples"]:
        role = "purged"
        for name, start, end in (
            ("train", a, b),
            ("validation", b + embargo, c),
            ("test", c + embargo, d),
        ):
            if start <= s["decision_ns"] < end and interval_end(s) < end:
                role = name
                break
        parts[role].append(s["id"])
    return parts


def plan(
    spec: Row,
    dataset: Path,
    *,
    mode: str,
    anchor_ns: int | None = None,
    sampling: Row | None = None,
    engineering_bounds: Row | None = None,
    engineering_epochs: int = 2,
) -> Row:
    validate_spec(spec)
    if mode not in ("engineering", "research"):
        raise ValueError("unknown_mode")
    if mode == "engineering":
        if engineering_bounds is None or not 1 <= engineering_epochs <= 5:
            raise ValueError("bounded_engineering_recipe_required")
        phases = {"fold0": engineering_bounds}
        epochs = engineering_epochs
    else:
        if sampling is None or anchor_ns is None:
            raise ValueError("prospective_sampling_binding_missing")
        design = materialize(spec, anchor_ns=anchor_ns, created_ns=sampling["created_ns"])
        if sampling.get("spec_sha256") != digest(spec) or sampling.get("anchor_ns") != anchor_ns:
            raise ValueError("sampling_binding_mismatch")
        phases = {
            f["id"]: dict(
                start=f["train_start_ns"],
                train_end=f["train_end_ns"],
                validation_end=f["validation_end_ns"],
                test_end=f["test_end_ns"],
                embargo=f["embargo_ns"],
            )
            for f in design["folds"]
        }
        phases["final_selection"] = dict(
            start=anchor_ns,
            train_end=anchor_ns + 49 * DAY,
            validation_end=anchor_ns + 56 * DAY,
            test_end=anchor_ns + 56 * DAY,
            embargo=spec["calendar"]["embargo_ns"],
        )
        epochs = spec["training"]["max_epochs"]
    return dict(
        schema=1,
        mode=mode,
        spec_sha256=digest(spec),
        dataset_sha256=sha(dataset),
        sources=sources(),
        python=platform.python_version(),
        platform=platform.platform(),
        phases=phases,
        max_epochs=epochs,
        sampling=sampling,
        trial_ids=[t["id"] for t in trial_plan(spec)["trials"] if t["phase"] in phases]
        + ([t["id"] for t in trial_plan(spec)["final_refit_slots"]] if mode == "research" else []),
    )


def validate_plan(spec: Row, binding: Row, dataset: Path) -> Row:
    validate_spec(spec)
    if binding["sources"] != sources() or binding["dataset_sha256"] != sha(dataset):
        raise ValueError("execution_source_or_data_changed")
    if binding["python"] != platform.python_version() or binding["spec_sha256"] != digest(spec):
        raise ValueError("execution_environment_or_spec_changed")
    first = binding["phases"]["fold0"]
    regenerated = plan(
        spec,
        dataset,
        mode=binding["mode"],
        anchor_ns=first["start"] if binding["mode"] == "research" else None,
        sampling=binding["sampling"],
        engineering_bounds=first,
        engineering_epochs=binding["max_epochs"],
    )
    if regenerated != binding:
        raise ValueError("execution_recipe_changed")
    bundle: Row = read(dataset)
    _validate(bundle)
    if binding["mode"] == "research":
        end = binding["phases"]["final_selection"]["validation_end"]
        if any(
            s["decision_ns"] < first["start"] or interval_end(s) >= end for s in bundle["samples"]
        ):
            raise ValueError("development_contains_protected_or_outside_data")
        if bundle["history_policy"] != {"context": 8, "max_gap_ns": 15 * 10**9}:
            raise ValueError("history_policy_changed")
        actions = {
            (i, size / 1e6, delay, 20) for i in (0, 1) for size in (100, 1000) for delay in (0, 5)
        }
        if any(
            s["pool_id"] not in spec["cohort"]
            or {tuple(t["action"]) for t in s["targets"]} != actions
            for s in bundle["samples"]
        ):
            raise ValueError("cohort_or_scenario_grid_changed")
    return bundle


def receipt(
    path: Path | None,
    trusted_sha256: str | None,
    *,
    kind: str,
    binding: Row,
    spec: Row,
    procedure_sha256: str | None = None,
) -> Row:
    """Caller pins a separately reviewed decision. This code NEVER issues decisions."""
    if path is None or trusted_sha256 is None or sha(path) != trusted_sha256:
        raise ValueError("separate_pinned_decision_required")
    r: Row = read(path)
    required = {
        "schema",
        "kind",
        "decision",
        "scope",
        "binding_sha256",
        "spec_sha256",
        "reviewer",
        "decided_ns",
        "evidence",
        "limitations",
        "procedure_sha256",
    }
    if (
        set(r) != required
        or r["schema"] != 1
        or r["kind"] != kind
        or r["decision"] != "approved"
        or r["scope"] != "conditional_forecast_only"
        or r["binding_sha256"] != digest(binding)
        or r["spec_sha256"] != digest(spec)
        or r["procedure_sha256"] != procedure_sha256
        or not isinstance(r["reviewer"], str)
        or not r["reviewer"].strip()
        or type(r["decided_ns"]) is not int
        or r["decided_ns"] <= 0
        or not r["limitations"]
    ):
        raise ValueError("invalid_or_stale_decision")
    expected = (
        set(spec["required_reviews"])
        if kind == "forecast_admission"
        else {
            "selected_refit_artifacts",
            "loss_ci_sources",
            "exposure_review",
            "evidence_completeness",
        }
    )
    if set(r["evidence"]) != expected:
        raise ValueError("incomplete_linked_reviews")
    for link in r["evidence"].values():
        p = Path(link["path"])
        if p.is_symlink() or sha(p) != link["sha256"]:
            raise ValueError("review_evidence_changed")
    return r
