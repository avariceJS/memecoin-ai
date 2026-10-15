import copy
import json
import struct
from collections import Counter

import pytest

pytest.importorskip("numpy")

from test_experience_coordinate_samples import _row, _schema  # noqa: E402
from test_experience_schema import custom_samples  # noqa: E402

from memecoin_experience.storage import (  # noqa: E402
    Snapshot,
    atomic_json,
    audit_partition_columns,
    build,
    digest,
    object_path,
    seal_files,
)


def synthetic_snapshot(tmp_path, version):
    if version == "v2":
        schema, rows = custom_samples(2)
        rows = copy.deepcopy(rows)
        rows[1].update(
            targets=[None],
            target_status=["pending"],
            target_available_ns=[None],
            target_captures=[[]],
        )
        cutoff, masked_row = 10**18, 1
    else:
        schema = _schema()
        rows = [
            _row(1, status="exact"),
            _row(2, status="lower_bound", value=2),
            _row(3, status="masked"),
            _row(4, status="pending"),
        ]
        cutoff, masked_row = 1000, 2
    store = tmp_path / f"{version}-store"
    result = build(
        store,
        rows,
        sources=[dict(kind="synthetic", pin="a" * 64)],
        cutoff=cutoff,
        chunk=16,
        schema=schema,
    )
    return store, result["snapshot"], masked_row


def reseal_binary_mutation(store, snapshot_pin, mutation, masked_row):
    snapshot = Snapshot(store, snapshot_pin)
    path, _ = snapshot.partition(0)
    x_path, y_path = path / "x.f64", path / "y.f64"
    x_bytes, y_bytes = bytearray(x_path.read_bytes()), bytearray(y_path.read_bytes())
    if mutation == "input-infinity":
        struct.pack_into("<d", x_bytes, 0, float("inf"))
    else:
        row_index = 0 if mutation == "exact-nan" else masked_row
        value = float("nan") if mutation == "exact-nan" else 1.25
        struct.pack_into("<d", y_bytes, row_index * 8, value)
    x_path.write_bytes(x_bytes)
    y_path.write_bytes(y_bytes)
    (path / "manifest.json").unlink()
    new_partition_pin = seal_files(path)
    new_path = object_path(store, new_partition_pin)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    path.rename(new_path)
    manifest = copy.deepcopy(snapshot.manifest)
    manifest["partitions"][0]["pin"] = new_partition_pin
    new_snapshot_pin = digest(manifest)
    atomic_json(store / "snapshots" / f"{new_snapshot_pin}.json", manifest)
    return Snapshot(store, new_snapshot_pin)


@pytest.mark.parametrize("version", ["v2", "v3"])
def test_partition_column_audit_matches_synthetic_v2_and_v3(tmp_path, version):
    store, pin, _ = synthetic_snapshot(tmp_path, version)
    snapshot = Snapshot(store, pin)
    partition_path, _ = snapshot.partition(0)
    before = {path.name: path.read_bytes() for path in partition_path.iterdir() if path.is_file()}

    result = audit_partition_columns(snapshot, 0, row_chunk=1)

    assert result["status"] == "matched"
    assert result["matches"] is True
    assert result["rows"] == (2 if version == "v2" else 4)
    assert result["target_status_counts"] == (
        {"observed": 1, "pending": 1}
        if version == "v2"
        else {"exact": 1, "lower_bound": 1, "masked": 1, "pending": 1}
    )
    assert result["partition_pin"] == snapshot.parts[0]["pin"]
    assert before == {
        path.name: path.read_bytes() for path in partition_path.iterdir() if path.is_file()
    }


@pytest.mark.parametrize("version", ["v2", "v3"])
@pytest.mark.parametrize("mutation", ["input-infinity", "exact-nan", "masked-finite"])
def test_partition_column_audit_finds_coherently_resealed_cell_mismatch(
    tmp_path, version, mutation
):
    store, pin, masked_row = synthetic_snapshot(tmp_path, version)
    snapshot = reseal_binary_mutation(store, pin, mutation, masked_row)

    # The ordinary metadata/manifest reader accepts this deliberately coherent reseal.
    path, _ = snapshot.partition(0)
    assert path.exists()
    result = audit_partition_columns(snapshot, 0, row_chunk=1)

    assert result["status"] == "mismatch"
    assert result["matches"] is False
    failure_key = {
        "input-infinity": "input_infinities",
        "exact-nan": "exact_target_nonfinite",
        "masked-finite": "masked_target_non_nan",
    }[mutation]
    assert result["failures"][failure_key] == 1


def test_partition_column_audit_rejects_unknown_status_and_reserved_snapshot(tmp_path):
    store, pin, _ = synthetic_snapshot(tmp_path, "v2")
    snapshot = Snapshot(store, pin)
    path, _ = snapshot.partition(0)
    rows_path = path / "rows.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
    rows[0]["target_status"][0] = "unknown-status"
    rows_path.write_bytes(
        b"".join(json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in rows)
    )
    (path / "manifest.json").unlink()
    new_partition_pin = seal_files(path)
    new_path = object_path(store, new_partition_pin)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    path.rename(new_path)
    manifest = copy.deepcopy(snapshot.manifest)
    manifest["partitions"][0]["pin"] = new_partition_pin
    manifest["partitions"][0]["statuses"] = dict(
        Counter(status for row in rows for status in row["target_status"])
    )
    new_snapshot_pin = digest(manifest)
    atomic_json(store / "snapshots" / f"{new_snapshot_pin}.json", manifest)
    malformed = Snapshot(store, new_snapshot_pin)
    # The generic v2 reader preserves status strings; the opt-in audit rejects unknown ones.
    malformed.partition(0)
    with pytest.raises(ValueError, match="experience_column_audit_unknown_status"):
        audit_partition_columns(malformed, 0)

    reserved_store = tmp_path / "reserved-store"
    schema, rows = custom_samples(1)
    reserved = build(
        reserved_store,
        rows,
        sources=[dict(kind="synthetic", pin="b" * 64)],
        cutoff=10**18,
        schema=schema,
        role="research_evaluation",
    )
    with pytest.raises(ValueError, match="experience_column_audit_reserved_snapshot"):
        audit_partition_columns(Snapshot(reserved_store, reserved["snapshot"]), 0)

    train_store, train_pin, _ = synthetic_snapshot(tmp_path, "v2")
    train_snapshot = Snapshot(train_store, train_pin)
    missing_role = copy.deepcopy(train_snapshot.manifest)
    missing_role["binding"].pop("role")
    missing_role_pin = digest(missing_role)
    atomic_json(train_store / "snapshots" / f"{missing_role_pin}.json", missing_role)
    missing_role_snapshot = Snapshot(train_store, missing_role_pin)
    with pytest.raises(ValueError, match="experience_column_audit_reserved_snapshot"):
        audit_partition_columns(missing_role_snapshot, 0)


@pytest.mark.parametrize("row_chunk", [0, 4097, True])
def test_partition_column_audit_bounds_requested_chunk(tmp_path, row_chunk):
    store, pin, _ = synthetic_snapshot(tmp_path, "v2")
    with pytest.raises(ValueError, match="experience_column_audit_chunk"):
        audit_partition_columns(Snapshot(store, pin), 0, row_chunk=row_chunk)


def test_partition_column_audit_rejects_single_row_above_memory_cap():
    snapshot = Snapshot.__new__(Snapshot)
    snapshot.schema = {
        "schema": "brain-experience-v2",
        "context": 4096,
        "features": [{"name": f"feature-{index}"} for index in range(513)],
        "targets": [{"name": "target"}],
    }
    snapshot.manifest = {"binding": {"role": "train"}}
    with pytest.raises(ValueError, match="experience_column_audit_row_size"):
        audit_partition_columns(snapshot, 0, row_chunk=1)
