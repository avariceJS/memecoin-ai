"""Persisted bounded candle-context regressions using qualified synthetic captures."""

import json
import math
import struct
from pathlib import Path

import pytest
from curriculum.test_admission import capture

from memecoin_continuous.store import connect, ingest
from memecoin_experience import observations, sparse, observation_adapters
from memecoin_experience.continuous import export
from memecoin_experience.observation_adapters import from_continuous
from memecoin_experience.storage import Snapshot, file_hash, seal_files

NS = 10**9
CONTEXT_PROFILE = "candle-context-v1"


def _capture_source(tmp_path, *, flat_prefix=0):
    cap, birth = capture(tmp_path)
    response = json.loads((cap / "response.bin").read_text())
    bars = response["data"]["attributes"]["ohlcv_list"]
    # Same legacy values, different candle structure. The third bar is a true
    # zero-range candle; its missing close position must survive as a mask.
    bars[0] = [300000, 2.0, 8.0, 2.0, 4.0, 10.0]
    bars[1] = [300300, 2.0, 4.0, 1.0, 4.0, 10.0]
    bars[2] = [300600, 3.0, 3.0, 3.0, 3.0, 10.0]
    for i in range(flat_prefix):
        bars[i] = [300000 + i * 300, 1.0, 1.0, 1.0, 1.0, 10.0]
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



def _capture_piece(root, indexes, available_ns, *, revise=None):
    """Seal a qualified synthetic subset with a caller-controlled receipt clock."""
    root.mkdir(parents=True, exist_ok=True)
    cap, birth = capture(root)
    response = json.loads((cap / "response.bin").read_text())
    all_bars = response["data"]["attributes"]["ohlcv_list"]
    response["data"]["attributes"]["ohlcv_list"] = [all_bars[i] for i in indexes]
    if revise is not None:
        row = response["data"]["attributes"]["ohlcv_list"][0]
        row[4] = revise
        row[1] = min(row[1], revise)
        row[2] = max(row[2], revise)
    (cap / "response.bin").write_text(json.dumps(response))
    receipt = json.loads((cap / "receipt.json").read_text())
    receipt["received_ns"] = available_ns - 1
    receipt["available_ns"] = available_ns
    receipt["response_sha256"] = file_hash(cap / "response.bin")
    (cap / "receipt.json").write_text(json.dumps(receipt))
    request = json.loads((cap / "request.json").read_text())
    request["started_ns"] = available_ns - 2
    (cap / "request.json").write_text(json.dumps(request))
    (cap / "manifest.json").unlink()
    seal_files(cap)
    return cap, birth



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



def test_context_profile_is_persisted_with_finite_history_and_exact_sparse_targets(tmp_path):
    """Context lineage/clocks must survive the actual adapter and sparse writer.

    On the frozen base, adapter calls deliberately fail with observation_profile;
    these assertions define the sibling implementation's integration contract.
    """
    reference = _capture_source(tmp_path, flat_prefix=2)
    observation_store = tmp_path / "context-observations"
    pin = from_continuous(reference, observation_store, role="train", profile=CONTEXT_PROFILE)
    source = observations.Source(observation_store, pin, role="train")
    stream = source.schema["streams"]["qualified_price_bar"]
    names = [feature["name"] for feature in stream["features"]]
    context = [
        ("prior_close_log_return", "log_ratio"),
        ("recent_close_position", "fraction_of_observed_range"),
        ("context_count", "observed_bar_count"),
        ("context_span_s", "seconds"),
        ("context_max_gap_s", "seconds"),
        ("context_contiguous", "indicator"),
        ("context_ready", "indicator"),
        ("context_zero_range", "indicator"),
    ]
    assert names == [
        "close_usd", "log_high_low", "log_close_open", "log_volume_usd",
        "upper_wick_fraction", "lower_wick_fraction", "signed_body_fraction",
        "close_position", "zero_range", *(name for name, _ in context),
    ]
    features = stream["features"]
    assert len(features) == 17
    assert [(feature["name"], feature["unit"]) for feature in features[9:]] == context
    assert all(feature["modality"] == "ohlcv" for feature in features)
    assert source.schema["availability"] == "historical_replay"
    assert source.manifest["binding"]["cutoff_ns"] == 10**18
    assert {"kind": "continuous", "pin": reference["pin"]} in source.manifest["binding"]["sources"]
    descriptor = json.dumps(source.manifest["binding"]["sources"], sort_keys=True)
    for frozen in (
        CONTEXT_PROFILE,
        "3600",
        "300",
        "12",
        "first-qualified",
        "context_zero_range",
        "implementation",
        file_hash(Path(observation_adapters.__file__)),
    ):
        assert frozen in descriptor

    facts = list(source.rows(with_provenance=True))
    events = {int(row["id"]): row for row, _ in facts if row["kind"] == "event"}
    # Early history remains explicitly sparse. A gap or unavailable predecessor
    # cannot be substituted with a zero-valued observed feature.
    singleton = events[300000]["values"]
    assert singleton[9:] == [None, None, 1, 0, None, None, 0, None]
    adjacent = events[300300]["values"]
    assert adjacent[9] == pytest.approx(0)
    assert adjacent[10] is None
    assert adjacent[11:] == [2, 300, 300, 1, 0, 1]
    assert all(row["status"] == "unknown" for row, _ in facts if row["kind"] == "support")
    assert all(row["event_ns"] == (int(row["id"]) + 300) * NS for row in events.values())
    assert all(row["available_ns"] <= 10**18 for row in events.values())

    output = tmp_path / "context-snapshots"
    plan = _sparse_plan()
    result = sparse.materialize(observation_store, pin, output, plan=plan, chunk=7)
    snapshot, persisted = _snapshot_rows(output, result["snapshot"])
    assert snapshot.schema["availability"] == "historical_replay"
    assert {"kind": "observations", "pin": pin} in snapshot.manifest["binding"]["sources"]
    assert [(item["name"], item["unit"]) for item in snapshot.schema["features"][:17]] == [
        ("value/" + feature["name"], feature["unit"]) for feature in features
    ]
    by_origin = {row["observation_origin"]["event_ns"]: (row, values) for row, values in persisted}
    current, current_x = by_origin[301200 * NS]
    prior, prior_x = by_origin[301500 * NS]
    flat_row, flat_x = by_origin[300600 * NS]
    assert flat_row["target_status"] == ["observed"]
    assert math.isnan(flat_x[10])  # undefined range position stays missing
    assert flat_x[16] == 1 and math.isfinite(flat_x[16])
    assert current["target_status"] == ["observed"]
    assert current["target_details"][0]["reason"] == "exact_observed_endpoint"
    assert current["target_details"][0]["price_provenance"]["endpoint"]["event_ns"] == 301500 * NS
    # Actual consumer tensors preserve the profile and differ as retained context moves.
    assert current_x[9:17] == pytest.approx(events[300900]["values"][9:17])
    assert prior_x[9:17] == pytest.approx(events[301200]["values"][9:17])
    assert current_x[9:17] != prior_x[9:17]
    assert current["input_available_ns"] == events[300900]["available_ns"]
    assert current["input_dependencies"]
    assert set(events[300900]["evidence"]) <= {
        dependency["artifact_sha"] for dependency in current["input_dependencies"]
    }

    # The actual zero-range candle survives as finite zeros and a finite flag;
    # its undefined range position remains NaN in the persisted f64 input.
    assert flat_x[4:7] == (0, 0, 0)
    assert current_x[10] == events[300900]["values"][10]
    assert from_continuous(reference, tmp_path / "context-repeat", profile=CONTEXT_PROFILE) == pin



def test_context_uses_all_contributor_receipts_and_capture_lineage_after_export(tmp_path):
    """Late history gates the row; late revisions cannot replace first receipt."""
    prior, birth = _capture_piece(tmp_path / "prior", range(13), 2 * 10**15)
    current, _ = _capture_piece(tmp_path / "current", range(13, 21), 15 * 10**14)
    revision, _ = _capture_piece(tmp_path / "revision", [12], 25 * 10**14, revise=1.4)
    index = tmp_path / "revision-index.sqlite"
    from memecoin_continuous.store import connect, ingest

    with connect(index) as db:
        for cap in (prior, current, revision):
            ingest(db, cap, birth["pool"])

    plan = dict(
        role="train", start_s=300000, end_s=318000,
        selection="synthetic contributor receipt regression",
        pools=[{key: birth[key] for key in ("pool", "base_mint", "quote_mint")}],
    )
    from memecoin_experience.continuous import export as export_continuous, source as continuous_source

    early = export_continuous(
        index, tmp_path / "early", dict(plan, cutoff_ns=16 * 10**14)
    )
    early_ref = dict(kind="continuous", path=str(tmp_path / "early"), pin=early["pin"])
    early_rows = list(continuous_source(early_ref, 16 * 10**14, role="train"))
    assert 303600 not in {row["open_s"] for row in early_rows}
    early_store = tmp_path / "early-observations"
    early_pin = from_continuous(early_ref, early_store, profile=CONTEXT_PROFILE)
    early_source = observations.Source(early_store, early_pin, role="train")
    early_event = next(fact for fact in early_source.rows()
                       if fact["kind"] == "event" and fact["id"] == "303900")
    assert early_event["values"][9:] == [None, None, 1, 0, None, None, 0, None]
    assert early_event["available_ns"] == 15 * 10**14
    assert early_event["evidence"] == [file_hash(current / "manifest.json")]
    early_result = sparse.materialize(
        early_store, early_pin, tmp_path / "early-snapshots", plan=_sparse_plan()
    )
    _, early_persisted = _snapshot_rows(tmp_path / "early-snapshots", early_result["snapshot"])
    _, early_values = next(pair for pair in early_persisted
                          if pair[0]["observation_origin"]["event_ns"] == 304200 * NS)
    assert math.isnan(early_values[9]) and math.isnan(early_values[10])
    assert early_values[11] == 1

    late = export_continuous(
        index, tmp_path / "late", dict(plan, cutoff_ns=3 * 10**15)
    )
    late_ref = dict(kind="continuous", path=str(tmp_path / "late"), pin=late["pin"])
    late_rows = list(continuous_source(late_ref, 3 * 10**15, role="train"))
    revised_open = next(row for row in late_rows if row["open_s"] == 303600)
    assert revised_open["ohlcv"][3] != pytest.approx(1.4)
    assert revised_open["available_ns"] == 2 * 10**15

    store = tmp_path / "multi-observations"
    pin = from_continuous(late_ref, store, role="train", profile=CONTEXT_PROFILE)
    source = observations.Source(store, pin, role="train")
    row = next(
        row for row in source.rows()
        if row["kind"] == "event" and row["id"] == "303900"
    )
    assert row["event_ns"] == 304200 * NS
    assert row["available_ns"] == 2 * 10**15
    current_bar = next(item for item in late_rows if item["open_s"] == 303900)
    assert current_bar["available_ns"] == 15 * 10**14
    assert row["available_ns"] > current_bar["available_ns"]
    assert row["available_ns"] > row["event_ns"]  # historical replay may be late
    assert set(row["evidence"]) == {file_hash(prior / "manifest.json"), file_hash(current / "manifest.json")}
    assert file_hash(revision / "manifest.json") not in row["evidence"]
    assert row["values"][9:] == pytest.approx(
        [math.log(1.0013 / 1.0012), (1.0013 - 0.9) / (1.1 - 0.9), 12, 3300, 300, 1, 1, 0]
    )

    result = sparse.materialize(
        store, pin, tmp_path / "multi-snapshots", plan=_sparse_plan(), chunk=5
    )
    snapshot, rows = _snapshot_rows(tmp_path / "multi-snapshots", result["snapshot"])
    persisted, values = next(
        pair for pair in rows
        if pair[0]["observation_origin"]["event_ns"] == 304200 * NS
    )
    assert persisted["input_available_ns"] == 2 * 10**15
    assert persisted["decision_ns"] == 304200 * NS
    assert values[9:17] == pytest.approx(row["values"][9:17])
    assert persisted["input_dependencies"]
    assert set(row["evidence"]) <= {
        dependency["artifact_sha"] for dependency in persisted["input_dependencies"]
    }
    assert snapshot.schema["availability"] == "historical_replay"
    assert all(status == "unknown" for fact in source.rows() if fact["kind"] == "support"
               for status in [fact["status"]])


def test_gapped_history_masks_prior_movement_and_never_substitutes_target_endpoint(tmp_path):
    cap, birth = _capture_piece(tmp_path / "gapped", [0, 1, 3, 4], 2 * 10**15)
    index = tmp_path / "gapped.sqlite"
    with connect(index) as db:
        ingest(db, cap, birth["pool"])
    plan = dict(role="train", cutoff_ns=3 * 10**15, start_s=300000, end_s=318000,
                selection="synthetic gapped context persisted regression",
                pools=[{k: birth[k] for k in ("pool", "base_mint", "quote_mint")}])
    exported = export(index, tmp_path / "gapped-source", plan)
    reference = dict(kind="continuous", path=str(tmp_path / "gapped-source"), pin=exported["pin"])
    store = tmp_path / "gapped-observations"
    pin = from_continuous(reference, store, profile=CONTEXT_PROFILE)
    source = observations.Source(store, pin, role="train")
    events = {fact["id"]: fact for fact in source.rows() if fact["kind"] == "event"}
    assert events["300900"]["values"][9] is None
    assert events["300900"]["values"][11:16] == [3, 900, 600, 0, 0]
    output = tmp_path / "gapped-snapshots"
    result = sparse.materialize(store, pin, output, plan=_sparse_plan())
    _, persisted = _snapshot_rows(output, result["snapshot"])
    by_origin = {row["observation_origin"]["event_ns"]: (row, values) for row, values in persisted}
    before_gap, _ = by_origin[300600 * NS]
    assert before_gap["target_status"] == ["missing_future"]
    assert before_gap["target_details"][0]["reason"] == "no_exact_endpoint_unknown_return"
    gap_row, gap_values = by_origin[301200 * NS]
    assert math.isnan(gap_values[9])
    assert gap_values[11:16] == (3, 900, 600, 0, 0)
    assert gap_row["target_status"] == ["observed"]
