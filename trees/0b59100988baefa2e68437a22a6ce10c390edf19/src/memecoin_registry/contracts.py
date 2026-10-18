"""Immutable artifact and absolute chronological protocol contracts (stdlib only)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from memecoin_ml.data import read, sha, write

Row = dict[str, Any]


class RegistryError(ValueError):
    """Stable safe category, without provider responses or credentials."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def source_identity() -> Row:
    import memecoin_ml.data as data

    result = {p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    result["memecoin_ml.data"] = sha(Path(data.__file__))
    return result


def inventory(root: Path, *, exclude: str | None = None) -> dict[str, str]:
    if root.is_symlink():
        raise RegistryError("symlink_forbidden")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RegistryError("symlink_forbidden")
        if path.is_file():
            name = path.relative_to(root).as_posix()
            if name != exclude:
                result[name] = sha(path)
    return result


def verify_inventory(root: Path, expected: Row, *, exclude: str) -> None:
    for name in expected:
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or p.as_posix() != name:
            raise RegistryError("unsafe_manifest_path")
    if inventory(root, exclude=exclude) != expected:
        raise RegistryError("artifact_inventory_mismatch")


def seal(root: Path) -> None:
    write(root / "manifest.json", inventory(root, exclude="manifest.json"))


def verify(root: Path) -> None:
    verify_inventory(root, read(root / "manifest.json"), exclude="manifest.json")


def integer(value: Any) -> bool:
    return type(value) is int and value >= 0


def validate_protocol(protocol: Row) -> None:
    if protocol.get("schema") != 1 or protocol.get("qualification") != "engineering_only":
        raise RegistryError("unsupported_protocol")
    if not integer(protocol["created_ns"]):
        raise RegistryError("invalid_protocol_time")
    holdout = protocol["holdout"]
    if not all(integer(holdout[k]) for k in ("start_ns", "end_ns")) or not (
        protocol["created_ns"] < holdout["start_ns"] < holdout["end_ns"]
    ):
        raise RegistryError("holdout_must_be_future")
    if protocol["research_training_ready"] is not False:
        raise RegistryError("research_gate_closed")
    history = protocol["history_policy"]
    if not integer(history["context"]) or not 1 <= history["context"] <= 256:
        raise RegistryError("invalid_history_policy")
    if not integer(history["max_gap_ns"]) or history["max_gap_ns"] == 0:
        raise RegistryError("invalid_history_policy")
    previous_end, previous_train, previous_validation = -1, -1, -1
    ids = set()
    for fold in protocol["folds"]:
        fields = (
            "train_start_ns",
            "train_end_ns",
            "validation_end_ns",
            "test_end_ns",
            "embargo_ns",
        )
        if not all(integer(fold[k]) for k in fields):
            raise RegistryError("invalid_fold_time")
        a, b, c, d, e = (fold[k] for k in fields)
        if not (a < b < b + e + 1 <= c < c + e + 1 <= d <= holdout["start_ns"]):
            raise RegistryError("invalid_fold_boundaries")
        if c < previous_end or b < previous_train or c <= previous_validation or fold["id"] in ids:
            raise RegistryError("overlapping_or_unordered_outer_tests")
        ids.add(fold["id"])
        previous_end, previous_train, previous_validation = d, b, c
    if not ids:
        raise RegistryError("empty_folds")
    floors = protocol["engineering_floors"]
    if set(floors) != {
        "windows",
        "utc_days",
        "utc_hours",
        "partition_decisions",
        "partition_nonoverlap",
        "labels_per_action_class",
    } or not all(integer(v) and v > 0 for v in floors.values()):
        raise RegistryError("invalid_qualification_floors")


def protocol_for_calendar(plan: Row, plan_sha: str, now_ns: int) -> Row:
    due = plan["due_ns"]
    if len(due) != 6 or any(not integer(t) for t in due) or due != sorted(set(due)):
        raise RegistryError("unsupported_calendar")
    protocol = dict(
        schema=1,
        qualification="engineering_only",
        created_ns=now_ns,
        calendar_plan_sha256=plan_sha,
        research_training_ready=False,
        history_policy=dict(context=8, max_gap_ns=15 * 10**9),
        folds=[
            dict(
                id=f"fold{i}",
                train_start_ns=due[0],
                train_end_ns=due[i + 1],
                validation_end_ns=due[i + 2],
                test_end_ns=due[i + 3],
                embargo_ns=30 * 10**9,
            )
            for i in range(2)
        ],
        holdout=dict(
            start_ns=due[4],
            end_ns=due[5] + 1800 * 10**9,
            policy="quarantine entire intersecting window; no label release",
        ),
        engineering_floors=dict(
            windows=3,
            utc_days=2,
            utc_hours=3,
            partition_decisions=20,
            partition_nonoverlap=3,
            labels_per_action_class=3,
        ),
        unresolved=[
            "market universe and regime coverage",
            "own execution/cost qualification",
            "research specification/model-selection budget",
            "formal readiness decision",
        ],
        known_development_exposure="D44 trained/evaluated window0; never a protected holdout",
    )
    validate_protocol(protocol)
    return protocol
