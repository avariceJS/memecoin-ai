"""Immutable input-only eligibility views: retain payloads, population and rejection reasons."""

import json
import math
import os
import shutil
import struct
import tempfile
from collections import Counter
from pathlib import Path

from .catalog import Writer
from .schema import feature_names
from .storage import (
    Snapshot,
    atomic_json,
    digest,
    file_hash,
    lock,
    object_path,
    seal_files,
    sync_dir,
    verify_files,
)

REASONS = (
    "eligible",
    "insufficient_activity",
    "insufficient_volume",
    "unknown_input",
    "not_selected",
)


def validate_policy(policy):
    if set(policy) != {"name", "min_positive_volume_fraction", "min_context_volume_usd"}:
        raise ValueError("experience_admission_policy")
    if policy["name"] != "causal_activity_v1":
        raise ValueError("experience_admission_policy")
    for key, maximum in (("min_positive_volume_fraction", 1), ("min_context_volume_usd", 1e300)):
        value = policy[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError("experience_admission_policy")
    return policy


def decide(frames, names, policy):
    """Receives input frames only. No labels, future support, survival or current liquidity."""
    volume, zero = names.index("log_volume_usd"), names.index("zero_volume")
    if not frames or any(
        f[volume] is None
        or f[zero] not in (0.0, 1.0)
        or not math.isfinite(f[volume])
        or f[volume] < 0
        or (f[volume] == 0) != (f[zero] == 1)
        for f in frames
    ):
        return 3
    if sum(f[zero] == 0 for f in frames) / len(frames) < policy["min_positive_volume_fraction"]:
        return 1
    # Clamp each contribution to the threshold: no overflow, identical threshold decision.
    threshold = policy["min_context_volume_usd"]
    log_threshold = math.log1p(threshold)
    total = math.fsum(
        threshold if f[volume] >= log_threshold else math.expm1(f[volume]) for f in frames
    )
    return 0 if total >= threshold else 2


def validate_selection(selection):
    if selection is None:
        return
    if (
        set(selection) != {"before_ns", "entities"}
        or type(selection["before_ns"]) is not int
        or selection["before_ns"] < 0
        or not isinstance(selection["entities"], list)
        or not selection["entities"]
        or any(not isinstance(s, str) or not s for s in selection["entities"])
        or selection["entities"] != sorted(set(selection["entities"]))
    ):
        raise ValueError("experience_admission_selection")


def create(store, parent, policy, *, selection=None):
    """One byte per physical row, bounded partitions. Features/targets are shared unchanged."""
    validate_policy(policy)
    validate_selection(selection)
    snapshot = Snapshot(store, parent)
    if (
        snapshot.schema["schema"] != "brain-experience-v2"
        or "admission" in snapshot.manifest["binding"]
    ):
        raise ValueError("experience_admission_parent")
    names = feature_names(snapshot.schema)
    if not {"log_volume_usd", "zero_volume"} <= set(names):
        raise ValueError("experience_admission_features")
    columns = {c["name"]: c for c in snapshot.schema["features"]}
    if (
        columns["log_volume_usd"]["unit"] != "log1p_USD"
        or columns["zero_volume"]["unit"] != "indicator"
        or any(columns[k]["modality"] != "ohlcv" for k in ("log_volume_usd", "zero_volume"))
    ):
        raise ValueError("experience_admission_feature_semantics")
    binding = dict(
        version="input-admission-v1",
        policy=policy,
        selection=selection,
        implementation=file_hash(Path(__file__)),
    )
    allowed = set(selection["entities"]) if selection else None
    store = snapshot.store
    with lock(store / ".publication.lock"):
        catalog, counts = Writer(store), Counter()
        for index, entry in enumerate(snapshot.parts):
            path, rows = snapshot.partition(index)
            codes = bytearray()
            frame_size = snapshot.schema["context"] * len(names)
            with (path / "x.f64").open("rb") as stream:
                for row in rows:
                    values = struct.unpack(f"<{frame_size}d", stream.read(frame_size * 8))
                    frames = [values[i : i + len(names)] for i in range(0, frame_size, len(names))]
                    code = decide(frames, names, policy)
                    if (
                        selection
                        and row["decision_ns"] < selection["before_ns"]
                        and row["pool"] not in allowed
                    ):
                        code = 4
                    codes.append(code)
                    counts[REASONS[code]] += 1
            stage = Path(tempfile.mkdtemp(prefix="admission-", dir=store / "staging"))
            try:
                atomic_json(
                    stage / "admission.json",
                    dict(binding=binding, partition=entry["pin"], count=len(rows)),
                )
                with (stage / "codes.u8").open("wb") as stream:
                    stream.write(codes)
                    stream.flush()
                    os.fsync(stream.fileno())
                pin = seal_files(stage)
                target = object_path(store, pin)
                target.parent.mkdir(parents=True, exist_ok=True)
                sync_dir(store / "objects")
                if target.exists():
                    verify_files(target, pin)
                else:
                    os.rename(stage, target)
                    sync_dir(target.parent)
                catalog.append(dict(entry, admission=pin))
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        manifest = {
            k: v
            for k, v in snapshot.manifest.items()
            if k not in {"partition_pages", "partition_count", "partition_index", "partitions"}
        }
        manifest.update(catalog.finish())
        manifest["binding"] = dict(
            snapshot.manifest["binding"], admission=binding, admission_parent=parent
        )
        manifest["admission_counts"] = dict(counts)
        pin = digest(manifest)
        atomic_json(store / "snapshots" / (pin + ".json"), manifest)
        return dict(
            snapshot=pin, parent=parent, counts=dict(counts), decisions=manifest["decisions"]
        )


def apply(snapshot, entry, rows):
    binding = snapshot.manifest["binding"].get("admission")
    if (binding is None) != ("admission" not in entry):
        raise ValueError("experience_admission_binding")
    if any("_admission" in row for row in rows):
        raise ValueError("experience_admission_reserved_metadata")
    if binding is None:
        return
    path = object_path(snapshot.store, entry["admission"])
    verify_files(path, entry["admission"])
    expected = dict(binding=binding, partition=entry["pin"], count=len(rows))
    if json.loads((path / "admission.json").read_text()) != expected:
        raise ValueError("experience_admission_binding")
    codes = (path / "codes.u8").read_bytes()
    if len(codes) != len(rows) or any(c >= len(REASONS) for c in codes):
        raise ValueError("experience_admission_codes")
    for row, code in zip(rows, codes, strict=True):
        row["_admission"] = REASONS[code]
