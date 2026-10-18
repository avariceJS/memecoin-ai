"""Immutable development registry. No holdout scan, scheduler or collection methods."""

from __future__ import annotations

import copy
import fcntl
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from memecoin_ai.causal import FEATURE_NAMES
from memecoin_ai.prospective import verify_campaign
from memecoin_ml.data import load_dataset, read, sha, write
from memecoin_registry.contracts import digest, seal, verify, verify_inventory
from memecoin_registry.registry import _admit, _publish, _scenario_grid
from memecoin_sampling.contract import NS, development_slot, load, paths_disjoint

Row = dict[str, Any]


def initialize(proposal: Path, root: Path) -> None:
    binding, _ = load(proposal)
    paths_disjoint(Path(binding["source_root"]), root)
    paths_disjoint(proposal, root)
    root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(proposal, root / "contract")
    for name in ("objects", "revisions", "attempts"):
        (root / name).mkdir()


def _records(binding: Row) -> list[Row]:
    return [dict(index=s["index"], status="pending") for s in binding["windows"][:224]]


def verify_registry(root: Path) -> Row:
    binding, spec = load(root / "contract")
    for name in ("", "objects", "revisions", "attempts", ".writer.lock"):
        if (root / name).is_symlink():
            raise ValueError("registry_symlink")
    previous = None
    records = _records(binding)
    latest: Row = dict(sequence=-1, as_of_ns=binding["created_ns"], records=records, head=None)
    seen_objects = set()
    for i, path in enumerate(sorted((root / "revisions").iterdir())):
        if path.name != f"{i:06d}" or path.is_symlink():
            raise ValueError("revision_sequence_changed")
        verify(path)
        value = read(path / "revision.json")
        if (
            set(value) != {"sequence", "as_of_ns", "parent_sha256", "contract_sha256", "records"}
            or value["sequence"] != i
            or value["parent_sha256"] != previous
            or value["contract_sha256"] != sha(root / "contract/manifest.json")
            or value["as_of_ns"] < latest["as_of_ns"]
            or len(value["records"]) != 224
        ):
            raise ValueError("revision_binding_changed")
        changes = 0
        for index, (old, new) in enumerate(zip(records, value["records"], strict=True)):
            if new["index"] != index or new["status"] not in (
                "pending",
                "completed",
                "failed",
                "missed",
            ):
                raise ValueError("invalid_window_record")
            if old != new:
                if old["status"] != "pending" or new["status"] == "pending":
                    raise ValueError("terminal_window_rewritten")
                changes += 1
            if new["status"] == "completed":
                key = new["seal_sha256"]
                if key not in seen_objects:
                    obj = root / "objects" / key
                    verify(obj)
                    if read(obj / "converted/entry.json")["record"] != new:
                        raise ValueError("object_record_changed")
                    raw = obj / "raw"
                    source_seal = read(raw / "seal.json")
                    verify_inventory(raw, source_seal["files"], exclude="seal.json")
                    if sha(raw / "seal.json") != key:
                        raise ValueError("source_seal_changed")
                    seen_objects.add(key)
        if changes != 1:
            raise ValueError("revision_must_add_one_terminal_window")
        records, previous = value["records"], sha(path / "manifest.json")
        latest = dict(sequence=i, as_of_ns=value["as_of_ns"], records=records, head=previous)
    # Validate identities across all admitted source windows, independent of publication order.
    combine(root, binding, records)
    return dict(binding=binding, spec=spec, **latest)


def combine(root: Path, binding: Row, records: list[Row]) -> Row:
    frames: Row = {}
    samples: list[Row] = []
    parents = []
    seen: set[tuple[str, int]] = set()
    for record in records:
        if record["status"] != "completed":
            continue
        identity = record["seal_sha256"]
        obj = root / "objects" / identity
        verify(obj)
        data = load_dataset(obj / "converted/dataset")
        metadata = read(obj / "converted/entry.json")
        if (
            metadata["record"] != record
            or metadata["protocol_sha256"] != digest(binding)
            or metadata["dataset_sha256"] != sha(obj / "converted/dataset/dataset.json")
            or data["source"]["seal_sha256"] != identity
        ):
            raise ValueError("converted_object_binding_changed")
        parents.append(
            dict(
                index=record["index"],
                seal_sha256=identity,
                feature_quality=metadata["feature_quality"],
                data_kind=record["data_kind"],
            )
        )
        for key, frame in data["frames"].items():
            frames[identity + ":" + key] = dict(frame, group=identity + ":" + frame["group"])
        for original in data["samples"]:
            sample = copy.deepcopy(original)
            _, pool, _ = sample["group"].split(":")
            natural = pool, sample["decision_ns"]
            if natural in seen:
                raise ValueError("duplicate_pool_decision")
            seen.add(natural)
            sample.update(
                id=identity + ":" + sample["id"],
                pool_id=pool,
                group=identity + ":" + sample["group"],
                window_index=record["index"],
                history=[identity + ":" + k for k in sample["history"]],
            )
            samples.append(sample)
    return dict(
        schema=1,
        qualification="engineering_only",
        feature_names=list(FEATURE_NAMES),
        history_policy=binding["history_policy"],
        frames=frames,
        samples=sorted(samples, key=lambda s: (s["decision_ns"], s["id"])),
        source=dict(
            kind="D48_F01_development_registry",
            data_kinds=sorted({r["data_kind"] for r in records if r["status"] == "completed"}),
            parents=parents,
            scenario_grid=_scenario_grid(binding["source_semantics"]),
            sampling_sha256=digest(binding),
            holdout_included=False,
            target_semantics=binding["source_semantics"]["targets"],
        ),
    )


def _completed_record(root: Path, source: Path, binding: Row, index: int, now: int) -> Row:
    slot = development_slot(binding, index)
    expected = Path(binding["source_root"]) / "windows" / f"{index:03d}"
    if source.absolute() != expected:
        raise ValueError("source_path_not_bound_to_window_no_read")
    if any(p.is_symlink() for p in (source, *source.parents)):
        raise ValueError("source_symlink")
    paths_disjoint(source, root)
    seal_value = read(source / "seal.json")
    reservation, outcome = read(source / "reservation.json"), seal_value["outcome"]
    due, start, end = slot["due_ns"], reservation["started_ns"], outcome["finished_ns"]
    if (
        reservation
        != dict(
            index=index,
            due_ns=due,
            started_ns=start,
            reserved_rpc=180,
            plan_sha256=sha(root / "contract/binding.json"),
        )
        or not due <= start <= due + 900 * NS
        or not start <= end <= min(now, start + 600 * NS)
        or outcome["index"] != index
        or outcome["status"] != "completed"
        or any(
            outcome.get(k) != 0
            for k in ("corpus_exit_code", "execution_or_collection_exit_code", "qa_exit_code")
        )
    ):
        raise ValueError("source_reservation_outcome_or_time_invalid")
    verify_inventory(source, seal_value["files"], exclude="seal.json")
    campaign = read(source / "campaign/plan.json")
    if any(
        campaign.get(k) != v
        for k, v in dict(
            max_segments=2,
            rounds=30,
            max_requests=180,
            timeout_seconds=15,
            max_response_bytes=1000000,
        ).items()
    ):
        raise ValueError("campaign_budget_changed")
    state = verify_campaign(source / "campaign")
    if (
        not state["complete"]
        or state["successful_snapshots"] != 180
        or state["reserved_requests"] != 180
    ):
        raise ValueError("completed_campaign_not_complete")
    if outcome.get("data_kind") not in ("prospective", "synthetic_fixture"):
        raise ValueError("explicit_source_data_kind_required")
    return dict(
        data_kind=outcome["data_kind"],
        index=index,
        status="completed",
        started_ns=start,
        finished_ns=end,
        seal_sha256=sha(source / "seal.json"),
    )


def ingest(
    root: Path,
    index: int,
    *,
    source: Path | None = None,
    status: str = "completed",
    now_ns: int | None = None,
) -> Row:
    # Binding can be read; source path must stay unopened for any protected slot.
    binding, _ = load(root / "contract")
    slot = development_slot(binding, index)
    now = time.time_ns() if now_ns is None else now_ns
    with (root / ".writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        latest = verify_registry(root)
        attempt = root / "attempts" / uuid.uuid4().hex
        attempt.mkdir()
        write(attempt / "started.json", dict(index=index, status=status, as_of_ns=now))
        try:
            if now < latest["as_of_ns"]:
                raise ValueError("registry_clock_regression")
            if status == "completed":
                if source is None:
                    raise ValueError("sealed_source_required")
                record = _completed_record(root, source, binding, index, now)
            elif status in ("failed", "missed"):
                if source is not None or now < slot["due_ns"] + (900 + 600) * NS:
                    raise ValueError("terminal_metadata_before_deadline_or_with_source")
                record = dict(index=index, status=status)
            else:
                raise ValueError("unsupported_terminal_status")
            old = latest["records"][index]
            if old["status"] != "pending":
                if old != record:
                    raise ValueError("terminal_window_conflict")
                write(attempt / "result.json", dict(status="no_change", head=latest["head"]))
                return dict(status="no_change", head=latest["head"])
            if status == "completed":
                assert source is not None
                target = root / "objects" / record["seal_sha256"]
                if not target.exists():
                    staged = attempt / "object"
                    staged.mkdir()
                    # Retain source bytes for independent offline regeneration, not only hashes.
                    shutil.copytree(source, staged / "raw")
                    _admit(
                        staged / "raw",
                        staged / "converted",
                        record,
                        binding,
                        dict(semantics=binding["source_semantics"]),
                    )
                    quality = read(staged / "converted/entry.json")["feature_quality"]
                    if quality["feature_starts"] != 180:
                        raise ValueError("completed_source_missing_planned_starts")
                    if _completed_record(root, source, binding, index, now) != record:
                        raise ValueError("source_changed_during_admission")
                    seal(staged)
                    _publish(staged, target)
            records = copy.deepcopy(latest["records"])
            records[index] = record
            combine(root, binding, records)  # Reject natural duplicate before revision publication.
            if load(root / "contract")[0] != binding:
                raise ValueError("source_changed_during_ingestion")
            staged = attempt / "revision"
            staged.mkdir()
            sequence = latest["sequence"] + 1
            write(
                staged / "revision.json",
                dict(
                    sequence=sequence,
                    as_of_ns=now,
                    parent_sha256=latest["head"],
                    contract_sha256=sha(root / "contract/manifest.json"),
                    records=records,
                ),
            )
            seal(staged)
            _publish(staged, root / "revisions" / f"{sequence:06d}")
            result = dict(
                status="published",
                sequence=sequence,
                head=sha(root / "revisions" / f"{sequence:06d}/manifest.json"),
            )
            write(attempt / "result.json", result)
            return result
        except Exception as exc:
            # Only our fixed safe categories, never raw provider content/exception payloads.
            write(attempt / "failure.json", dict(category=type(exc).__name__, no_retry=True))
            raise


def reproduce_sources(root: Path, output: Path) -> Row:
    """Regenerate features/targets/conversion from retained raw with pinned old modules."""
    paths_disjoint(root, output)
    current = verify_registry(root)
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for record in current["records"]:
        if record["status"] != "completed":
            continue
        obj = root / "objects" / record["seal_sha256"]
        regenerated = output / f"{record['index']:03d}"
        _admit(
            obj / "raw",
            regenerated,
            record,
            current["binding"],
            dict(semantics=current["binding"]["source_semantics"]),
        )
        expected, actual = (
            load_dataset(obj / "converted/dataset"),
            load_dataset(regenerated / "dataset"),
        )
        # Dataset path documents the physical conversion location, not information timing.
        expected["source"].pop("path", None)
        actual["source"].pop("path", None)
        if (
            expected != actual
            or read(obj / "converted/entry.json")["feature_quality"]
            != read(regenerated / "entry.json")["feature_quality"]
        ):
            raise ValueError("raw_conversion_reproduction_changed")
        results.append(
            dict(
                index=record["index"],
                semantic_dataset_sha256=digest(actual),
                exact_except_conversion_path=True,
            )
        )
    result = dict(windows=results, holdout_opened=False, research_launch_authorized=False)
    write(output / "report.json", result)
    seal(output)
    return result
