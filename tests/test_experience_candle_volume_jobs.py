"""Offline CLI and persisted Snapshot regressions for volume aware context.

The new profile assertions intentionally fail at the adapter profile lookup on
the frozen baseline. They are the integration contract for the sibling producer
and validator changes, not a request to weaken existing profile validation.
"""

import json
import math
import struct
import sys
from pathlib import Path

import pytest
from curriculum.test_admission import capture

from memecoin_continuous.store import connect, ingest
from memecoin_experience import __main__ as experience_cli
from memecoin_experience import observation_adapters, observations, sparse
from memecoin_experience.continuous import export
from memecoin_experience.storage import Snapshot, file_hash, seal_files
from test_experience_candle_context_integration import _sparse_plan

NS = 10**9
PROFILE = "candle-volume-context-v1"
LEGACY = "legacy-v1"
MORPHOLOGY = "candle-morphology-v1"
CONTEXT = "candle-context-v1"


def _capture_source(root: Path, prior_volume: float):
    """Qualified synthetic capture whose only pairwise difference is old volume."""
    root.mkdir(parents=True, exist_ok=True)
    cap, birth = capture(root)
    response_path = cap / "response.bin"
    response = json.loads(response_path.read_text())
    bars = response["data"]["attributes"]["ohlcv_list"]
    # Identical prices, morphology, and current volume; two prior volumes vary.
    bars[0] = [300000, 2.0, 8.0, 2.0, 4.0, prior_volume]
    bars[1] = [300300, 2.0, 4.0, 1.0, 4.0, prior_volume]
    bars[2] = [300600, 3.0, 6.0, 2.0, 4.0, 100.0]
    response_path.write_text(json.dumps(response))
    receipt_path = cap / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["response_sha256"] = file_hash(response_path)
    receipt_path.write_text(json.dumps(receipt))
    (cap / "manifest.json").unlink()
    seal_files(cap)

    index = root / "continuous.sqlite"
    with connect(index) as db:
        ingest(db, cap, birth["pool"])
    plan = dict(
        role="train", cutoff_ns=10**18, start_s=300000, end_s=318000,
        selection="synthetic volume context CLI regression",
        pools=[{key: birth[key] for key in ("pool", "base_mint", "quote_mint")}],
    )
    exported = export(index, root / "continuous", plan)
    return dict(kind="continuous", path=str(root / "continuous"), pin=exported["pin"])


def _job(root, action, payload, capsys):
    path = root / f"{action}.json"
    path.write_text(json.dumps(payload, sort_keys=True))
    original = sys.argv
    try:
        sys.argv = ["memecoin_experience", action, "--job", str(path)]
        experience_cli.main()
    finally:
        sys.argv = original
    return json.loads(capsys.readouterr().out)


def _read_x(snapshot):
    width = len(snapshot.schema["features"])
    result = []
    for part_index in range(len(snapshot.parts)):
        path, metadata = snapshot.partition(part_index)
        tensor = struct.unpack(
            f"<{len(metadata) * width}d", (path / "x.f64").read_bytes()
        )
        result.extend(
            (row, tensor[index * width:(index + 1) * width])
            for index, row in enumerate(metadata)
        )
    return result


def _run_pair(root, reference, capsys, profile=PROFILE):
    root.mkdir(parents=True, exist_ok=True)
    store = root / "observations"
    produced = _job(root, "observations-from-continuous", dict(
        reference=reference, store=str(store), role="train", profile=profile,
    ), capsys)
    source = observations.Source(store, produced["source"], role="train")
    output = root / "snapshots"
    built = _job(root, "build-sparse", dict(
        store=str(store), pin=produced["source"], output=str(output),
        plan=_sparse_plan(), profile=profile, chunk=7,
    ), capsys)
    snapshot = Snapshot(
        output, built["snapshot"], expected_schema=sparse.schema(source, _sparse_plan())
    )
    rows = {row["observation_origin"]["event_ns"]: (row, values)
            for row, values in _read_x(snapshot)}
    return source, snapshot, rows


def test_volume_context_pair_persists_delta_masks_clocks_lineage_and_targets(tmp_path, capsys):
    low_ref = _capture_source(tmp_path / "low", 10.0)
    high_ref = _capture_source(tmp_path / "high", 100.0)
    low_source, low_snapshot, low = _run_pair(tmp_path / "low-jobs", low_ref, capsys)
    high_source, high_snapshot, high = _run_pair(tmp_path / "high-jobs", high_ref, capsys)

    # Index 300600 is current: two strictly earlier contributors, current=100.
    low_row, low_x = low[300900 * NS]
    high_row, high_x = high[300900 * NS]
    assert len(low_x) == len(high_x) == len(low_snapshot.schema["features"])
    assert low_x[:17] == pytest.approx(high_x[:17])
    assert (low_x[17], high_x[17]) == (2, 2)
    assert low_x[18] == pytest.approx(math.log(101) - math.log(11))
    assert high_x[18] == pytest.approx(0)

    for source, snapshot, row, values, reference in (
        (low_source, low_snapshot, low_row, low_x, low_ref),
        (high_source, high_snapshot, high_row, high_x, high_ref),
    ):
        features = source.schema["streams"]["qualified_price_bar"]["features"]
        assert len(features) == 19
        assert [(item["name"], item["unit"]) for item in features[17:]] == [
            ("prior_volume_count", "observed_bar_count"),
            ("volume_log1p_delta", "log1p_USD_difference"),
        ]
        assert all(item["modality"] == "ohlcv" for item in features)
        assert source.schema["availability"] == snapshot.schema["availability"] == "historical_replay"
        assert snapshot.manifest["binding"]["selection"]["observation_profile"]["source_pin"] == source.pin
        assert snapshot.manifest["binding"]["selection"]["observation_profile"]["continuous_source_pin"] == reference["pin"]
        assert {"kind": "observations", "pin": source.pin} in snapshot.manifest["binding"]["sources"]
        producer = next(item for item in source.manifest["binding"]["sources"] if "producer" in item)
        assert producer["implementation"] == file_hash(Path(observation_adapters.__file__))
        assert producer["numerics"] == file_hash(Path(observation_adapters.__file__).with_name("numerics.py"))
        assert producer["profile"]["identity"]["source_pin"] == reference["pin"]
        assert producer["profile"]["identity"]["cutoff_ns"] == 10**18
        assert row["decision_ns"] == row["observation_origin"]["event_ns"] == 300900 * NS
        assert row["input_available_ns"] <= source.manifest["binding"]["cutoff_ns"]
        assert row["target_status"] == ["observed"]
        assert row["target_details"][0]["reason"] == "exact_observed_endpoint"
        assert row["target_details"][0]["price_provenance"]["endpoint"]["event_ns"] == 301200 * NS
        assert row["input_dependencies"]
        assert all(math.isfinite(value) or math.isnan(value) for value in values)

    # Undefined early delta remains masked; a true zero delta remains finite.
    one_prior = low[300600 * NS][1]
    assert one_prior[17] == 1 and math.isnan(one_prior[18])
    first = low[300300 * NS][1]
    assert first[17] == 0 and math.isnan(first[18])

    # Both real JSON jobs are deterministic, including immutable source pins.
    repeat = _run_pair(tmp_path / "low-repeat", low_ref, capsys)
    assert repeat[0].pin == low_source.pin
    assert repeat[1].pin == low_snapshot.pin


@pytest.mark.parametrize("profile", [LEGACY, MORPHOLOGY, CONTEXT])
def test_existing_profiles_keep_semantic_dimensions_and_stable_cli_pins(
    tmp_path, capsys, profile
):
    reference = _capture_source(tmp_path / "capture", 10.0)
    source, snapshot, rows = _run_pair(tmp_path / profile, reference, capsys, profile)
    first, second = rows[300300 * NS][1], rows[300600 * NS][1]
    expected_width = {LEGACY: 4, MORPHOLOGY: 9, CONTEXT: 17}[profile]
    assert len(first) == len(second) == len(snapshot.schema["features"])
    assert len(source.schema["streams"]["qualified_price_bar"]["features"]) == expected_width
    assert source.schema["availability"] == snapshot.schema["availability"] == "historical_replay"
    selected = snapshot.manifest["binding"]["selection"]["observation_profile"]
    assert selected["name"] == profile
    assert selected["source_pin"] == source.pin
    assert selected["continuous_source_pin"] == reference["pin"]
    producer = next(item for item in source.manifest["binding"]["sources"] if "producer" in item)
    assert producer["implementation"] == file_hash(Path(observation_adapters.__file__))
    repeated = _run_pair(tmp_path / f"{profile}-repeat", reference, capsys, profile)
    assert repeated[0].pin == source.pin
    assert repeated[1].pin == snapshot.pin
