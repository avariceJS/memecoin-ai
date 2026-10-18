"""Single-writer append-only registry of a frozen D43 calendar; no collector calls."""

from __future__ import annotations

import copy
import fcntl
import os
import time
import uuid
from collections import Counter
from pathlib import Path

from memecoin_ml.data import export_window, load_dataset, read, rows, save_dataset, sha, write
from memecoin_registry.contracts import (
    RegistryError,
    Row,
    digest,
    protocol_for_calendar,
    seal,
    source_identity,
    validate_protocol,
    verify,
    verify_inventory,
)
from memecoin_registry.qualification import report

CAMPAIGN_FIELDS = (
    "schema_version",
    "kind",
    "pools",
    "config_layout_version",
    "source_alias",
    "endpoint_origin",
    "transport_profile",
    "interval_seconds",
    "boundary_policy",
)


def _plan(calendar: Path) -> Row:
    if calendar.is_symlink() or sha(calendar / "plan.json") != read(calendar / "plan-sha.json"):
        raise RegistryError("calendar_plan_changed")
    plan: Row = read(calendar / "plan.json")
    if plan.get("stage") != "D43":
        raise RegistryError("unsupported_calendar")
    return plan


def _window_seal(window: Path, outcome: Row) -> str:
    value = read(window / "seal.json")
    if value["outcome"] != outcome:
        raise RegistryError("sealed_outcome_changed")
    verify_inventory(window, value["files"], exclude="seal.json")
    return sha(window / "seal.json")


def _semantics(window: Path) -> Row:
    campaign = read(window / "campaign/plan.json")
    return dict(
        campaign={k: campaign[k] for k in CAMPAIGN_FIELDS},
        targets=read(window / "campaign-targets/spec.json"),
    )


def _scenario_grid(semantics: Row) -> list[list[float]]:
    spec = semantics["targets"]
    return sorted(
        [
            [direction, size / 1e6, delay / 1e9, spec["holding_ns"] / 1e9]
            for direction in spec["input_indices"]
            for size in spec["sizes_ppm"]
            for delay in spec["assumed_delays_ns"]
        ]
    )


def _publish(staged: Path, final: Path) -> None:
    # Flush complete files/directories before the atomic commit point.
    for path in sorted(staged.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    for path in [*sorted((p for p in staged.rglob("*") if p.is_dir()), reverse=True), staged]:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    os.rename(staged, final)
    fd = os.open(final.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def initialize(root: Path, calendar: Path, *, now_ns: int | None = None) -> Row:
    now = time.time_ns() if now_ns is None else now_ns
    calendar, root = calendar.resolve(), root.absolute()
    if root.resolve().is_relative_to(calendar) or calendar.is_relative_to(root.resolve()):
        raise RegistryError("registry_must_be_outside_calendar")
    plan = _plan(calendar)
    protocol = protocol_for_calendar(plan, sha(calendar / "plan.json"), now)
    # Window0 is explicitly exposed development, required to pin supported semantics.
    first = calendar / "windows/000"
    outcome = read(calendar / "outcomes/000.json")
    if outcome["status"] != "completed" or outcome["finished_ns"] > now:
        raise RegistryError("development_reference_not_complete")
    _window_seal(first, outcome)
    if outcome["finished_ns"] >= protocol["holdout"]["start_ns"]:
        raise RegistryError("development_reference_overlaps_holdout")
    semantics = _semantics(first)
    root.mkdir(parents=True, exist_ok=False)
    config = root / "contract"
    config.mkdir()
    write(config / "protocol.json", protocol)
    with (config / "calendar.json").open("xb") as stream:
        stream.write((calendar / "plan.json").read_bytes())
    write(
        config / "binding.json",
        dict(
            calendar_root=str(calendar),
            source=source_identity(),
            semantics=semantics,
            exposed_seal_sha256=sha(first / "seal.json"),
        ),
    )
    seal(config)
    for name in ("objects", "revisions", "attempts"):
        (root / name).mkdir()
    return protocol


def contract(root: Path) -> tuple[Row, Row]:
    if any(
        (root / name).is_symlink()
        for name in ("", "contract", "objects", "revisions", "attempts", ".writer.lock")
    ):
        raise RegistryError("symlink_forbidden")
    verify(root / "contract")
    protocol: Row = read(root / "contract/protocol.json")
    binding: Row = read(root / "contract/binding.json")
    validate_protocol(protocol)
    if binding["source"] != source_identity():
        raise RegistryError("registry_source_changed_use_pinned_package")
    if sha(root / "contract/calendar.json") != protocol["calendar_plan_sha256"]:
        raise RegistryError("calendar_snapshot_changed")
    return protocol, binding


def _scan(calendar: Path, protocol: Row, now_ns: int) -> list[Row]:
    plan = _plan(calendar)
    if sha(calendar / "plan.json") != protocol["calendar_plan_sha256"]:
        raise RegistryError("calendar_plan_changed")
    records = []
    for index, due in enumerate(plan["due_ns"]):
        window = calendar / "windows" / f"{index:03d}"
        path = calendar / "outcomes" / f"{index:03d}.json"
        row = dict(
            index=index,
            due_ns=due,
            admission="awaiting_completion" if window.exists() else "pending",
        )
        if path.exists():
            outcome = read(path)
            if outcome["index"] != index or outcome["status"] not in (
                "completed",
                "failed",
                "missed",
            ):
                raise RegistryError("invalid_outcome")
            if outcome.get("finished_ns", now_ns) > now_ns:
                raise RegistryError("future_outcome")
            row.update(outcome_sha256=sha(path), outcome_status=outcome["status"])
            if (window / "seal.json").exists():
                row["seal_sha256"] = _window_seal(window, outcome)
            if outcome["status"] != "completed":
                row["admission"] = "excluded_" + outcome["status"]
            else:
                if "seal_sha256" not in row:
                    raise RegistryError("completed_without_seal")
                if any(
                    outcome.get(k) != 0
                    for k in (
                        "corpus_exit_code",
                        "execution_or_collection_exit_code",
                        "qa_exit_code",
                    )
                ):
                    raise RegistryError("completed_with_failed_qa")
                reservation = read(window / "reservation.json")
                if (
                    reservation["index"] != index
                    or reservation["due_ns"] != due
                    or reservation["plan_sha256"] != protocol["calendar_plan_sha256"]
                ):
                    raise RegistryError("reservation_binding_changed")
                start, end = reservation["started_ns"], outcome["finished_ns"]
                if not (due <= start <= end <= now_ns):
                    raise RegistryError("invalid_window_time")
                row.update(started_ns=start, finished_ns=end)
                # Whole-window quarantine prevents a crossing source from smuggling future labels.
                if end >= protocol["holdout"]["start_ns"] or due >= protocol["holdout"]["start_ns"]:
                    row["admission"] = (
                        "holdout_quarantine"
                        if start < protocol["holdout"]["end_ns"]
                        else "post_holdout_quarantine"
                    )
                else:
                    row["admission"] = "development"
        records.append(row)
    return records


def _unchanged(previous: list[Row], current: list[Row]) -> None:
    for old, new in zip(previous, current, strict=True):
        if old["admission"] == "awaiting_completion" and new["admission"] == "pending":
            raise RegistryError("reservation_disappeared")
        if "outcome_sha256" in old and old != new:
            raise RegistryError("previous_window_changed")


def _admit(window: Path, destination: Path, record: Row, protocol: Row, binding: Row) -> None:
    if _semantics(window) != binding["semantics"]:
        raise RegistryError("source_semantics_changed")
    # Existing D44 adapter independently recomputes target/protocol semantics from raw captures.
    export_window(window, destination / "dataset", **protocol["history_policy"])
    bundle = load_dataset(destination / "dataset")
    expected_grid = _scenario_grid(binding["semantics"])
    decisions = set()
    for sample in bundle["samples"]:
        _, pool, _ = sample["group"].split(":")
        identity = (pool, sample["decision_ns"])
        if identity in decisions:
            raise RegistryError("duplicate_decision_identity")
        decisions.add(identity)
        if sorted(t["action"] for t in sample["targets"]) != expected_grid:
            raise RegistryError("scenario_grid_changed")
        if (
            not record["started_ns"]
            <= sample["decision_ns"]
            <= sample["maturity_ns"]
            <= record["finished_ns"]
        ):
            raise RegistryError("source_availability_outside_window")
        if any(
            bundle["frames"][key]["decision_ns"] < record["started_ns"] for key in sample["history"]
        ):
            raise RegistryError("history_predates_window")
    features = [
        row
        for path in sorted((window / "campaign/segments").glob("*/corpus/features.jsonl"))
        for row in rows(path)
    ]
    quality = dict(
        feature_starts=len(features),
        usable=sum(bool(f["usable"]) for f in features),
        unusable=sum(not f["usable"] for f in features),
        unusable_reasons=dict(Counter(reason for f in features for reason in f["reasons"])),
    )
    if quality["usable"] != len(bundle["samples"]):
        raise RegistryError("feature_inventory_changed")
    destination.mkdir(exist_ok=True)
    write(
        destination / "entry.json",
        dict(
            record=record,
            feature_quality=quality,
            protocol_sha256=digest(protocol),
            dataset_sha256=sha(destination / "dataset/dataset.json"),
        ),
    )
    seal(destination)
    if sha(window / "seal.json") != record["seal_sha256"]:
        raise RegistryError("window_changed_during_admission")
    _window_seal(window, read(window / "seal.json")["outcome"])


def combine(root: Path, records: list[Row], protocol: Row, binding: Row) -> Row:
    from memecoin_ai.causal import FEATURE_NAMES

    frames: Row = {}
    samples = []
    seen: set[tuple[str, int]] = set()
    parents = []
    for entry in records:
        if entry["admission"] != "development":
            continue
        identity = entry["seal_sha256"]
        obj = root / "objects" / identity
        verify(obj)
        metadata = read(obj / "entry.json")
        if metadata["record"] != entry or metadata["protocol_sha256"] != digest(protocol):
            raise RegistryError("object_binding_changed")
        bundle = load_dataset(obj / "dataset")
        if bundle["source"]["seal_sha256"] != identity or metadata["dataset_sha256"] != sha(
            obj / "dataset/dataset.json"
        ):
            raise RegistryError("object_source_changed")
        parents.append(
            dict(
                index=entry["index"],
                seal_sha256=identity,
                dataset_sha256=metadata["dataset_sha256"],
                feature_quality=metadata["feature_quality"],
            )
        )
        for key, frame in bundle["frames"].items():
            frames[identity + ":" + key] = dict(frame, group=identity + ":" + frame["group"])
        for original in bundle["samples"]:
            sample = copy.deepcopy(original)
            _, pool, _ = sample["group"].split(":")
            key = (pool, sample["decision_ns"])
            if key in seen:
                raise RegistryError("overlapping_decision_identity")
            seen.add(key)
            sample.update(
                id=identity + ":" + sample["id"],
                pool_id=pool,
                group=identity + ":" + sample["group"],
                window_index=entry["index"],
                history=[identity + ":" + k for k in sample["history"]],
            )
            samples.append(sample)
    return dict(
        schema=1,
        qualification="engineering_only",
        feature_names=list(FEATURE_NAMES),
        history_policy=protocol["history_policy"],
        frames=frames,
        samples=sorted(samples, key=lambda s: (s["decision_ns"], s["id"])),
        source=dict(
            kind="D45_development_registry",
            parents=parents,
            scenario_grid=_scenario_grid(binding["semantics"]),
            protocol_sha256=digest(protocol),
            holdout_included=False,
            target_semantics=binding["semantics"]["targets"],
        ),
    )


def verify_registry(root: Path) -> Row | None:
    protocol, binding = contract(root)
    previous_hash = None
    previous_records: list[Row] | None = None
    latest = None
    for index, rev in enumerate(sorted((root / "revisions").iterdir())):
        if rev.name != f"{index:06d}" or not rev.is_dir():
            raise RegistryError("revision_sequence_changed")
        verify(rev)
        meta = read(rev / "revision.json")
        if meta["parent_sha256"] != previous_hash or meta["contract_sha256"] != sha(
            root / "contract/manifest.json"
        ):
            raise RegistryError("revision_chain_changed")
        if meta["sequence"] != index:
            raise RegistryError("revision_index_changed")
        records = read(rev / "inventory.json")
        if previous_records is not None:
            _unchanged(previous_records, records)
        # Reproduce from immutable admitted objects; calendar can be offline/unmounted.
        bundle = combine(root, records, protocol, binding)
        if bundle != load_dataset(rev / "development"):
            raise RegistryError("development_reproduction_changed")
        expected_report, expected_folds = report(bundle, protocol, records, meta["as_of_ns"])
        if expected_report != read(rev / "qualification.json") or expected_folds != read(
            rev / "folds.json"
        ):
            raise RegistryError("qualification_reproduction_changed")
        latest = dict(path=str(rev), metadata=meta, records=records, report=expected_report)
        previous_hash = sha(rev / "manifest.json")
        previous_records = records
    return latest


def sync(root: Path, *, now_ns: int | None = None) -> Row:
    now = time.time_ns() if now_ns is None else now_ns
    protocol, binding = contract(root)
    calendar = Path(binding["calendar_root"])
    with (root / ".writer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RegistryError("writer_busy") from exc
        latest = verify_registry(root)
        if now < protocol["created_ns"] or (latest and now < latest["metadata"]["as_of_ns"]):
            raise RegistryError("registry_clock_regression")
        attempt = root / "attempts" / f"{now}-{uuid.uuid4().hex}"
        attempt.mkdir()
        write(attempt / "started.json", dict(as_of_ns=now))
        try:
            records = _scan(calendar, protocol, now)
            if records[0].get("seal_sha256") != binding["exposed_seal_sha256"]:
                raise RegistryError("development_reference_changed")
            if latest:
                _unchanged(latest["records"], records)
                if latest["records"] == records:
                    write(
                        attempt / "result.json", dict(status="no_change", revision=latest["path"])
                    )
                    return dict(status="no_change", revision=latest["path"])
            for entry in records:
                if entry["admission"] != "development":
                    continue
                final = root / "objects" / entry["seal_sha256"]
                if not final.exists():
                    staged = attempt / f"object-{entry['index']:03d}"
                    _admit(
                        calendar / "windows" / f"{entry['index']:03d}",
                        staged,
                        entry,
                        protocol,
                        binding,
                    )
                    _publish(staged, final)
            bundle = combine(root, records, protocol, binding)
            qualification, folds = report(bundle, protocol, records, now)
            # A new window never changes old membership, even if it arrives out of due order.
            if latest:
                old_folds = read(Path(latest["path"]) / "folds.json")
                old_ids = {
                    s["id"] for s in load_dataset(Path(latest["path"]) / "development")["samples"]
                }
                for name in folds:
                    for part in ("train", "validation", "test"):
                        if (
                            set(old_folds[name]["partitions"][part])
                            != set(folds[name]["partitions"][part]) & old_ids
                        ):
                            raise RegistryError("historical_membership_changed")
            staged = attempt / "revision"
            staged.mkdir()
            sequence = 0 if latest is None else latest["metadata"]["sequence"] + 1
            write(
                staged / "revision.json",
                dict(
                    sequence=sequence,
                    as_of_ns=now,
                    parent_sha256=sha(Path(latest["path"]) / "manifest.json") if latest else None,
                    contract_sha256=sha(root / "contract/manifest.json"),
                ),
            )
            write(staged / "inventory.json", records)
            save_dataset(staged / "development", bundle)
            write(staged / "folds.json", folds)
            write(staged / "qualification.json", qualification)
            seal(staged)
            # If a window is finishing concurrently, retry manually in a new attempt, not in a loop.
            if records != _scan(calendar, protocol, now):
                raise RegistryError("source_changed_during_sync")
            final = root / "revisions" / f"{sequence:06d}"
            _publish(staged, final)
            write(attempt / "result.json", dict(status="published", revision=str(final)))
            return dict(status="published", revision=str(final), qualification=qualification)
        except Exception as exc:
            category = str(exc) if isinstance(exc, RegistryError) else type(exc).__name__
            write(attempt / "failure.json", dict(category=category, no_retry=True))
            raise


def export_fold(root: Path, output: Path, *, revision: int, fold: str, partition: str) -> Row:
    if partition not in ("train", "validation", "test"):
        raise RegistryError("holdout_release_not_implemented")
    _, binding = contract(root)
    if output.resolve().is_relative_to(root.resolve()) or output.resolve().is_relative_to(
        Path(binding["calendar_root"])
    ):
        raise RegistryError("export_must_be_outside_registry_and_calendar")
    verify_registry(root)
    rev = root / "revisions" / f"{revision:06d}"
    folds = read(rev / "folds.json")
    if fold not in folds:
        raise RegistryError("unknown_fold")
    bundle = load_dataset(rev / "development")
    ids = set(folds[fold]["partitions"][partition])
    bundle["samples"] = [s for s in bundle["samples"] if s["id"] in ids]
    keys = {key for s in bundle["samples"] for key in s["history"]}
    bundle["frames"] = {k: v for k, v in bundle["frames"].items() if k in keys}
    bundle["source"] = dict(
        bundle["source"],
        revision_manifest_sha256=sha(rev / "manifest.json"),
        fold=fold,
        partition=partition,
    )
    save_dataset(output, bundle)
    return dict(decisions=len(ids), research_training_ready=False)
