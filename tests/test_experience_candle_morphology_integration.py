"""Persisted sparse-reader regressions for opt-in candle morphology.

All inputs are synthetic qualified captures.  The profile candidate is expected
to fail against the pre-profile adapter; the legacy control remains runnable.
"""

import json
import math
import struct

import pytest
from curriculum.test_admission import capture

from memecoin_continuous.store import connect, ingest
from memecoin_experience import observations, sparse
from memecoin_experience.continuous import export
from memecoin_experience.observation_adapters import from_continuous
from memecoin_experience.storage import Snapshot, file_hash, seal_files

NS = 10**9
PROFILE = "candle-morphology-v1"
MORPHOLOGY = [
    ("upper_wick_fraction", "fraction_of_range"),
    ("lower_wick_fraction", "fraction_of_range"),
    ("signed_body_fraction", "signed_fraction_of_range"),
    ("close_position", "fraction_of_range"),
    ("zero_range", "indicator"),
]


def _capture_source(tmp_path):
    cap, birth = capture(tmp_path)
    response = json.loads((cap / "response.bin").read_text())
    bars = response["data"]["attributes"]["ohlcv_list"]
    # Same legacy values, different candle structure. The third bar is a true
    # zero-range candle; its missing close position must survive as a mask.
    bars[0] = [300000, 2.0, 8.0, 2.0, 4.0, 10.0]
    bars[1] = [300300, 2.0, 4.0, 1.0, 4.0, 10.0]
    bars[2] = [300600, 3.0, 3.0, 3.0, 3.0, 10.0]
    (cap / "response.bin").write_text(json.dumps(response))
    receipt = json.loads((cap / "receipt.json").read_text())
    receipt["response_sha256"] = file_hash(cap / "response.bin")
    (cap / "receipt.json").write_text(json.dumps(receipt))
    (cap / "manifest.json").unlink()
    seal_files(cap)

    index = tmp_path / "continuous.sqlite"
    with connect(index) as db:
        ingest(db, cap, birth["pool"])
    plan = dict(
        role="train",
        cutoff_ns=10**18,
        start_s=300000,
        end_s=318000,
        selection="synthetic candle morphology integration",
        pools=[{k: birth[k] for k in ("pool", "base_mint", "quote_mint")}],
    )
    exported = export(index, tmp_path / "continuous", plan)
    return dict(kind="continuous", path=str(tmp_path / "continuous"), pin=exported["pin"])


def _sparse_plan():
    return dict(
        stream="qualified_price_bar",
        context=1,
        max_history_ns=3600 * NS,
        lifecycle=[],
        lifecycle_origins=False,
        relative_features=[],
        targets=[
            dict(
                name="next_close_return",
                kind="endpoint_log_return",
                horizon_ns=300 * NS,
                feature="close_usd",
            )
        ],
    )


def _snapshot_rows(root, pin):
    snapshot = Snapshot(root, pin)
    rows = []
    width = len(snapshot.schema["features"])
    for i in range(len(snapshot.parts)):
        path, metadata = snapshot.partition(i)
        tensor = struct.unpack(f"<{len(metadata) * width}d", (path / "x.f64").read_bytes())
        rows.extend((row, tensor[j * width : (j + 1) * width]) for j, row in enumerate(metadata))
    return snapshot, rows


def test_morphology_survives_observation_source_and_sparse_snapshot_reads(tmp_path):
    reference = _capture_source(tmp_path)
    observation_store = tmp_path / "observations"
    pin = from_continuous(reference, observation_store, role="train", profile=PROFILE)
    source = observations.Source(observation_store, pin, role="train")
    stream = source.schema["streams"]["qualified_price_bar"]
    names = [feature["name"] for feature in stream["features"]]
    assert names == [
        "close_usd",
        "log_high_low",
        "log_close_open",
        "log_volume_usd",
        *(name for name, _ in MORPHOLOGY),
    ]
    assert [(item["name"], item["unit"]) for item in stream["features"][4:]] == MORPHOLOGY
    assert all(item["modality"] == "ohlcv" for item in stream["features"])
    assert PROFILE in stream["process"]
    assert source.schema["availability"] == "historical_replay"
    assert source.manifest["binding"]["cutoff_ns"] == 10**18
    assert {"kind": "continuous", "pin": reference["pin"]} in source.manifest["binding"]["sources"]
    assert PROFILE in json.dumps(source.manifest["binding"]["sources"], sort_keys=True)
    assert from_continuous(reference, tmp_path / "observations-repeat", profile=PROFILE) == pin

    facts = list(source.rows(with_provenance=True))
    events = [row for row, _ in facts if row["kind"] == "event"]
    by_id = {row["id"]: row for row in events}
    first, second, flat = (by_id[str(t)] for t in (300000, 300300, 300600))

    def legacy(row):
        return row["values"][:4]

    assert legacy(first) == pytest.approx(legacy(second))
    assert first["values"][4:] == pytest.approx([2 / 3, 0, 1 / 3, 1 / 3, 0])
    assert second["values"][4:] == pytest.approx([0, 1 / 3, 2 / 3, 1, 0])
    assert flat["values"][4:7] == [0, 0, 0]
    assert flat["values"][7] is None
    assert flat["values"][8] == 1
    assert first["event_ns"] == (300000 + 300) * NS
    assert first["available_ns"] <= source.manifest["binding"]["cutoff_ns"]
    assert first["evidence"] and all(len(value) == 64 for value in first["evidence"])
    assert all(shard and len(shard) == 64 for row, shard in facts if row["kind"] == "event")
    assert all(row["status"] == "unknown" for row, _ in facts if row["kind"] == "support")

    plan = _sparse_plan()
    output = tmp_path / "snapshots"
    result = sparse.materialize(observation_store, pin, output, plan=plan, chunk=7)
    snapshot, rows = _snapshot_rows(output, result["snapshot"])
    assert [(f["name"], f["unit"]) for f in snapshot.schema["features"][:9]] == [
        ("value/" + f["name"], f["unit"]) for f in stream["features"]
    ]
    assert snapshot.schema["availability"] == "historical_replay"
    assert {"kind": "observations", "pin": pin} in snapshot.manifest["binding"]["sources"]
    assert len(rows) == len(events) == 60
    by_origin = {row["observation_origin"]["event_ns"]: (row, values) for row, values in rows}
    first_read, second_read, flat_read = (by_origin[t * NS] for t in (300300, 300600, 300900))

    def projected(row):
        return row[1][4:9]

    assert projected(first_read) == pytest.approx(first["values"][4:])
    assert projected(second_read) == pytest.approx(second["values"][4:])
    assert projected(first_read) != projected(second_read)
    first_metadata = first_read[0]
    assert first_metadata["target_status"] == ["observed"]
    assert first_metadata["target_details"][0]["reason"] == "exact_observed_endpoint"
    assert (
        first_metadata["target_details"][0]["price_provenance"]["endpoint"]["event_ns"]
        == 300600 * NS
    )
    # The persisted f64 consumer encodes missing input cells as NaN, while
    # actual observed zeros retain a finite zero. No close placement is invented.
    assert math.isnan(projected(flat_read)[3])
    assert projected(flat_read)[4] == 1
    assert projected(first_read)[1] == 0 and math.isfinite(projected(first_read)[1])
    assert first_metadata["input_available_ns"] == first["available_ns"]
    assert first_metadata["input_dependencies"]
    assert set(first["evidence"]) <= {
        d["artifact_sha"] for d in first_metadata["input_dependencies"]
    }

    repeat = sparse.materialize(observation_store, pin, output, plan=plan, chunk=7)
    assert repeat["snapshot"] == result["snapshot"]
    assert _snapshot_rows(output, repeat["snapshot"])[0].pin == snapshot.pin


def test_legacy_profile_control_keeps_existing_four_values_and_replay_lineage(tmp_path):
    reference = _capture_source(tmp_path)
    implicit_pin = from_continuous(reference, tmp_path / "implicit")
    explicit_pin = from_continuous(reference, tmp_path / "explicit", profile="legacy-v1")
    implicit = observations.Source(tmp_path / "implicit", implicit_pin, role="train")
    explicit = observations.Source(tmp_path / "explicit", explicit_pin, role="train")
    stream = implicit.schema["streams"]["qualified_price_bar"]
    assert [f["name"] for f in stream["features"]] == [
        "close_usd",
        "log_high_low",
        "log_close_open",
        "log_volume_usd",
    ]
    assert implicit.schema == explicit.schema
    assert [row for row in implicit.rows()] == [row for row in explicit.rows()]
    assert implicit.schema["availability"] == "historical_replay"
    assert implicit.manifest["binding"]["cutoff_ns"] == 10**18
