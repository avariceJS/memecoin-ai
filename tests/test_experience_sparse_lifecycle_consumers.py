"""Persisted sparse lifecycle consumer and representation compatibility regressions.

Fixtures use only synthetic public observations and standard-library tensor readback.
"""

import hashlib
import copy
import json
import math
import struct

import pytest

from memecoin_experience import observations, sparse
from memecoin_experience.schema import checked_samples, feature_names, membership
from memecoin_experience.storage import Snapshot, atomic_json, build, digest

NS = 10**9
ENTITY = "test/market/lifecycle-consumer"


def pin(label):
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def source_contract(availability="historical_replay"):
    return {
        "version": "market-observations-v1",
        "role": "train",
        "availability": availability,
        "streams": {
            "ticks": {
                "process": "synthetic qualified ticks",
                "features": [{"name": "price", "unit": "USD", "modality": "price"}],
            }
        },
    }


def plan(*, context=3, relative=(), lifecycle=("listed",), history_ns=20):
    return {
        "stream": "ticks",
        "context": context,
        "max_history_ns": history_ns * NS,
        "lifecycle": list(lifecycle),
        "lifecycle_origins": True,
        "relative_features": list(relative),
        "targets": [
            {"name": "return", "kind": "endpoint_log_return", "horizon_ns": 5 * NS, "feature": "price"},
            {"name": "count", "kind": "observed_process_count", "horizon_ns": 5 * NS, "feature": None},
        ],
    }


def event(at, price, name, *, available=None):
    return {
        "kind": "event",
        "entity": ENTITY,
        "stream": "ticks",
        "id": name,
        "event_ns": at * NS,
        "available_ns": (at if available is None else available) * NS,
        "values": [price],
        "evidence": [pin("event:" + name)],
    }


def lifecycle(at, name="listed", *, available=None):
    return {
        "kind": "lifecycle",
        "entity": ENTITY,
        "id": name,
        "event_ns": at * NS,
        "available_ns": (at if available is None else available) * NS,
        "transition": name,
        "evidence": [pin("lifecycle:" + name)],
    }


def publish(tmp_path, events, *, availability="historical_replay", projection=None, support_available=40):
    cutoff = 50 * NS
    facts = [
        {
            "kind": "enrollment",
            "entity": ENTITY,
            "start_ns": 0,
            "end_ns": 40 * NS,
            "available_ns": 0,
            "evidence": [pin("enrollment")],
        },
        {
            "kind": "support",
            "entity": ENTITY,
            "stream": "ticks",
            "start_ns": 0,
            "end_ns": 40 * NS,
            "available_ns": support_available * NS,
            "status": "complete",
            "evidence": [pin("support")],
        },
        *events,
    ]
    source_store = tmp_path / "observations"
    source_pin = observations.publish(
        source_store,
        sorted(facts, key=observations.key),
        schema=source_contract(availability),
        cutoff=cutoff,
        sources=[{"synthetic": True}],
    )
    output = tmp_path / "experience"
    built = sparse.materialize(
        source_store,
        source_pin,
        output,
        plan=projection or plan(),
        chunk=4096,
    )
    snapshot = Snapshot(output, built["snapshot"])
    parts = [snapshot.partition(i) for i in range(len(snapshot.parts))]
    rows = [row for _, values in parts for row in values]
    return source_store, source_pin, output, snapshot, parts, rows


def x_row(partition, row_index, schema):
    path, rows = partition
    width = len(feature_names(schema)) * schema["context"]
    values = struct.unpack(f"<{len(rows) * width}d", (path / "x.f64").read_bytes())
    start = row_index * width
    return values[start : start + width]


def y_row(partition, row_index, target_count):
    path, rows = partition
    values = struct.unpack(f"<{len(rows) * target_count}d", (path / "y.f64").read_bytes())
    start = row_index * target_count
    return values[start : start + target_count]


def locate(parts, row):
    offset = 0
    for part in parts:
        if row in part[1]:
            return part, part[1].index(row)
        offset += len(part[1])
    raise AssertionError("row missing from persisted partitions")


def split():
    return dict(train_start_s=0, train_end_s=45, validation_end_s=48, test_end_s=50)


def test_lifecycle_history_prices_and_dependencies_survive_persisted_readback(tmp_path):
    _, _, _, snapshot, parts, rows = publish(
        tmp_path,
        [event(0, 11.0, "boundary"), event(19, 17.0, "recent"), event(25, 23.0, "future"), lifecycle(20)],
        projection=plan(context=3, history_ns=20),
    )
    row = next(r for r in rows if r["id"] == "lifecycle:ticks:listed")
    assert row["observation_origin"]["kind"] == "lifecycle"
    path_rows, index = locate(parts, row)
    values = x_row(path_rows, index, snapshot.schema)
    feature_index = feature_names(snapshot.schema).index("value/price")
    features_per_frame = len(feature_names(snapshot.schema))
    assert values[feature_index] == pytest.approx(11.0)
    assert values[features_per_frame + feature_index] == pytest.approx(17.0)
    assert math.isnan(values[2 * features_per_frame + feature_index])
    # Historical price, lifecycle transition and their actual source clocks/evidence all survive.
    assert {dep["artifact_sha"] for dep in row["input_dependencies"]} >= {
        pin("event:boundary"), pin("event:recent"), pin("lifecycle:listed")
    }
    price_dep = next(dep for dep in row["input_dependencies"] if dep["artifact_sha"] == pin("event:recent"))
    assert price_dep == {"artifact_sha": pin("event:recent"), "event_ns": 19 * NS, "available_ns": 19 * NS}
    assert membership(row, split(), snapshot.schema) == ("train", [False, True])
    assert row["target_status"] == ["unsupported", "observed"]
    assert math.isnan(y_row(path_rows, index, len(snapshot.schema["targets"]))[0])
    assert y_row(path_rows, index, len(snapshot.schema["targets"]))[1] == 1.0


def test_context_one_lifecycle_tensor_has_no_price_basis_or_unused_dependency(tmp_path):
    _, _, _, snapshot, parts, rows = publish(
        tmp_path,
        [event(10, 20.0, "same-time-price"), event(15, 25.0, "future"), lifecycle(10)],
        projection=plan(context=1, relative=("price",)),
    )
    row = next(r for r in rows if r["id"] == "lifecycle:ticks:listed")
    path_rows, index = locate(parts, row)
    tensor = x_row(path_rows, index, snapshot.schema)
    value_column = feature_names(snapshot.schema).index("value/price")
    assert math.isnan(tensor[value_column])
    assert row["target_details"][0]["reason"] == "no_origin_value"
    assert pin("event:same-time-price") not in {dep["artifact_sha"] for dep in row["input_dependencies"]}
    assert row["target_status"] == ["unsupported", "observed"]
    assert math.isnan(y_row(path_rows, index, 2)[0])
    assert y_row(path_rows, index, 2)[1] == 1.0
    assert membership(row, split(), snapshot.schema) == ("train", [False, True])


@pytest.mark.parametrize("availability", ["historical_replay", "point_in_time"])
def test_relative_lifecycle_history_persists_with_true_base_clock(tmp_path, availability):
    _, _, _, snapshot, parts, rows = publish(
        tmp_path,
        [event(8, 10.0, "prior"), event(10, 20.0, "base", available=50),
         event(15, 30.0, "endpoint"), lifecycle(10)],
        availability=availability,
        projection=plan(relative=("price",)),
        support_available=50,
    )
    row = next(r for r in rows if r["id"] == "lifecycle:ticks:listed")
    part, index = locate(parts, row)
    tensor = x_row(part, index, snapshot.schema)
    width = len(feature_names(snapshot.schema))
    price = feature_names(snapshot.schema).index("value/price")
    prices = [tensor[frame * width + price] for frame in range(3)]
    if availability == "historical_replay":
        assert prices[:2] == pytest.approx([math.log(0.5), 0.0])
        assert math.isnan(prices[2])
        assert row["input_available_ns"] == 50 * NS
        assert {"artifact_sha": pin("event:base"), "event_ns": 10 * NS,
                "available_ns": 50 * NS} in row["input_dependencies"]
        relabeled = dict(snapshot.schema, availability="point_in_time")
        with pytest.raises(ValueError, match="sample_contract|dependency|provenance"):
            list(checked_samples([row], relabeled, 50 * NS))
    else:
        assert all(math.isnan(value) for value in prices)
        assert row["input_available_ns"] == 10 * NS
        assert pin("event:base") not in {dep["artifact_sha"] for dep in row["input_dependencies"]}
    assert row["target_status"] == ["unsupported", "observed"]
    assert row["target_details"][0]["reason"] == "no_origin_value"
    assert math.isnan(y_row(part, index, 2)[0])
    assert y_row(part, index, 2)[1] == 1.0
    # PIT training ends at 45s, before the count-support receipt at 50s.
    assert membership(row, split(), snapshot.schema) == (
        "train", [False, availability == "historical_replay"]
    )


def test_sealed_replay_late_base_persists_but_pit_and_relabeling_reject_it(tmp_path):
    facts = [event(10, 20.0, "late-base", available=12), event(15, 40.0, "endpoint")]
    _, _, _, replay, replay_parts, replay_rows = publish(
        tmp_path / "replay", facts, availability="historical_replay", projection=plan(lifecycle=())
    )
    replay_row = next(row for row in replay_rows if row["id"] == "event:ticks:late-base")
    replay_part, replay_index = locate(replay_parts, replay_row)
    assert replay_row["decision_ns"] == 10 * NS
    assert replay_row["input_available_ns"] == 12 * NS
    assert replay_row["target_status"][0] == "observed"
    assert replay_row["target_details"][0]["price_provenance"]["base"] == {
        "event_ns": 10 * NS,
        "available_ns": 12 * NS,
        "evidence": [pin("event:late-base")],
    }
    assert pin("event:late-base") in {dep["artifact_sha"] for dep in replay_row["input_dependencies"]}
    assert y_row(replay_part, replay_index, 2)[0] == pytest.approx(math.log(2.0))
    assert membership(replay_row, split(), replay.schema) == ("train", [True, True])

    relabeled = dict(replay.schema)
    relabeled["availability"] = "point_in_time"
    with pytest.raises(ValueError, match="sample_contract|dependency|provenance"):
        list(checked_samples([replay_row], relabeled, 50 * NS))

    _, _, _, pit, _, pit_rows = publish(
        tmp_path / "pit", facts, availability="point_in_time", projection=plan(lifecycle=())
    )
    pit_row = next(row for row in pit_rows if row["id"] == "event:ticks:late-base")
    assert pit_row["decision_ns"] == 12 * NS
    assert pit_row["target_status"][0] == "unsupported"
    assert pit_row["target_details"][0]["reason"] == "no_decision_price"
    assert membership(pit_row, split(), pit.schema) == ("train", [False, True])


def test_smallest_sparse_representation_boundary_is_v4_and_binds_full_schema(tmp_path):
    _, _, _, snapshot, _, _ = publish(
        tmp_path,
        [event(2, 11.0, "old"), event(8, 17.0, "recent"), event(15, 23.0, "future"), lifecycle(10)],
    )
    # brain-experience-v3 is the coordinate metadata schema; this assertion addresses
    # the independent sparse-event-sequence representation version.
    assert snapshot.schema["schema"] == "brain-experience-v2"
    assert snapshot.schema["representation"]["name"] == "sparse-event-sequence"
    assert snapshot.manifest["binding"]["contract"] == snapshot.schema
    assert snapshot.schema["representation"]["code_sha"] != "0" * 64
    partition_path, _ = snapshot.partition(0)
    assert json.loads((partition_path / "partition.json").read_text())["contract"] == snapshot.schema
    assert snapshot.schema["representation"]["version"] == "4"
    assert len(snapshot.schema["representation"]["code_sha"]) == 64


def test_explicit_immutable_v3_representation_fixture_remains_readable(tmp_path):
    _source_store, _source_pin, _output, current, parts, rows = publish(
        tmp_path,
        [event(2, 11.0, "old"), event(8, 17.0, "recent"), event(15, 23.0, "future"), lifecycle(10)],
    )
    old_schema = dict(current.schema)
    old_schema["representation"] = {
        "name": "sparse-event-sequence",
        "version": "3",
        "code_sha": "a" * 64,
    }
    before = rows[0]
    current_part, current_index = locate(parts, before)
    current_bytes = {
        name: (current_part[0] / name).read_bytes() for name in ("rows.jsonl", "x.f64", "y.f64")
    }
    width = len(feature_names(current.schema)) * current.schema["context"]
    numeric_x = struct.unpack(
        f"<{len(current_part[1]) * width}d", (current_part[0] / "x.f64").read_bytes()
    )
    numeric_y = struct.unpack(
        f"<{len(current_part[1]) * len(current.schema['targets'])}d",
        (current_part[0] / "y.f64").read_bytes(),
    )
    legacy_row = dict(before)
    x_start = current_index * width
    legacy_row["values"] = [
        [None if math.isnan(value) else value for value in numeric_x[start : start + len(current.schema["features"])]]
        for start in range(x_start, x_start + width, len(current.schema["features"]))
    ]
    y_start = current_index * len(current.schema["targets"])
    legacy_row["targets"] = []
    for target_index, value in enumerate(
        numeric_y[y_start : y_start + len(current.schema["targets"])]
    ):
        if math.isnan(value):
            legacy_row["targets"].append(None)
        elif current.schema["targets"][target_index]["kind"].startswith(
            "observed_process_count@"
        ):
            legacy_row["targets"].append(int(value))
        else:
            legacy_row["targets"].append(value)
    legacy_root = tmp_path / "legacy-v3"
    legacy = build(
        legacy_root,
        [legacy_row],
        sources=[{"kind": "synthetic-static-v3", "pin": "b" * 64}],
        cutoff=50 * NS,
        schema=old_schema,
        chunk=1,
    )
    legacy_snapshot = Snapshot(legacy_root, legacy["snapshot"])
    part_path, persisted = legacy_snapshot.partition(0)
    original = {name: (part_path / name).read_bytes() for name in ("rows.jsonl", "x.f64", "y.f64")}
    reread = Snapshot(legacy_root, legacy["snapshot"])
    reread_path, reread_rows = reread.partition(0)
    assert reread.schema["representation"]["version"] == "3"
    assert reread_rows == persisted == [before]
    assert {name: (reread_path / name).read_bytes() for name in original} == original
    assert current.pin != legacy_snapshot.pin
    assert {name: (current_part[0] / name).read_bytes() for name in current_bytes} == current_bytes
    assert legacy_snapshot.manifest["binding"]["contract"] == legacy_snapshot.schema
    assert json.loads((reread_path / "partition.json").read_text())["contract"] == legacy_snapshot.schema

    mismatched = copy.deepcopy(legacy_snapshot.manifest)
    mismatched["binding"]["contract"] = dict(old_schema)
    mismatched["binding"]["contract"]["representation"] = {
        "name": "sparse-event-sequence",
        "version": "4",
        "code_sha": "c" * 64,
    }
    mismatch_pin = digest(mismatched)
    atomic_json(legacy_root / "snapshots" / (mismatch_pin + ".json"), mismatched)
    with pytest.raises(ValueError, match="experience_partition_contract"):
        Snapshot(legacy_root, mismatch_pin).partition(0)
