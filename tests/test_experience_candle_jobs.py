"""Actual offline JSON jobs and persisted inputs over qualified synthetic captures."""

import json
import math
import struct
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from memecoin_experience import observation_adapters, observations, sparse
from memecoin_experience import __main__ as experience_cli
from memecoin_experience.continuous import export
from memecoin_experience.storage import Snapshot, file_hash
from memecoin_continuous.store import connect, ingest
from test_experience_candle_context_integration import _capture_piece, _capture_source, _sparse_plan

NS = 10**9
PROFILES = ("candle-morphology-v1", "candle-context-v1")


def _job(tmp_path, action, payload, *, capsys):
    job_path = tmp_path / f"{action}.json"
    job_path.write_text(json.dumps(payload, sort_keys=True))
    prior = sys.argv
    try:
        sys.argv = ["memecoin_experience", action, "--job", str(job_path)]
        experience_cli.main()
    finally:
        sys.argv = prior
    return json.loads(capsys.readouterr().out)


def _read_tensor(snapshot):
    rows = []
    width = len(snapshot.schema["features"])
    for index in range(len(snapshot.parts)):
        path, metadata = snapshot.partition(index)
        values = struct.unpack(
            f"<{len(metadata) * width}d", (path / "x.f64").read_bytes()
        )
        rows.extend(
            (row, values[offset * width : (offset + 1) * width])
            for offset, row in enumerate(metadata)
        )
    return rows


def test_legacy_json_jobs_remain_unchanged_and_deterministic(tmp_path, capsys):
    reference = _capture_source(tmp_path)
    observation_store = tmp_path / "legacy-observations"
    producer_job = dict(
        reference=reference,
        store=str(observation_store),
        role="train",
        # Omitted profile is the long-standing four-value representation.
    )
    produced = _job(tmp_path, "observations-from-continuous", producer_job, capsys=capsys)
    source_pin = produced["source"]
    source = observations.Source(observation_store, source_pin, role="train")
    assert [item["name"] for item in source.schema["streams"]["qualified_price_bar"]["features"]] == [
        "close_usd",
        "log_high_low",
        "log_close_open",
        "log_volume_usd",
    ]
    explicit_store = tmp_path / "legacy-explicit-observations"
    explicit = _job(
        tmp_path,
        "observations-from-continuous",
        dict(
            reference=reference,
            store=str(explicit_store),
            role="train",
            profile="legacy-v1",
        ),
        capsys=capsys,
    )
    explicit_source = observations.Source(explicit_store, explicit["source"], role="train")
    assert source.schema == explicit_source.schema
    assert list(source.rows()) == list(explicit_source.rows())

    plan = _sparse_plan()
    output = tmp_path / "legacy-snapshots"
    first = _job(
        tmp_path,
        "build-sparse",
        dict(store=str(observation_store), pin=source_pin, output=str(output), plan=plan, chunk=7),
        capsys=capsys,
    )
    repeat_output = tmp_path / "legacy-snapshots-repeat"
    second = _job(
        tmp_path,
        "build-sparse",
        dict(
            store=str(observation_store),
            pin=source_pin,
            output=str(repeat_output),
            plan=plan,
            chunk=7,
        ),
        capsys=capsys,
    )
    assert first["snapshot"] == second["snapshot"]
    # Use the CLI-returned pin and require ordinary reload/partition verification.
    snapshot = Snapshot(output, first["snapshot"])
    assert snapshot.schema["availability"] == "historical_replay"
    assert len(snapshot.schema["features"]) == 9
    rows = _read_tensor(snapshot)
    by_origin = {row["observation_origin"]["event_ns"]: (row, values) for row, values in rows}
    flat, flat_values = by_origin[300600 * NS]
    assert flat["target_details"][0]["reason"] == "exact_observed_endpoint"
    assert all(math.isfinite(value) for value in flat_values[:4])
    assert len(rows) == 60


@pytest.mark.parametrize("profile", PROFILES)
def test_profile_json_jobs_bind_reader_to_exact_producer_contract(
    tmp_path, capsys, profile
):
    """CLI-returned pins reach strict reload with exact clocks and profile lineage."""
    reference = _capture_source(tmp_path, flat_prefix=2)
    observation_store = tmp_path / f"{profile}-observations"
    produced = _job(
        tmp_path,
        "observations-from-continuous",
        dict(
            reference=reference,
            store=str(observation_store),
            role="train",
            profile=profile,
        ),
        capsys=capsys,
    )
    source_pin = produced["source"]
    observed = observations.Source(observation_store, source_pin, role="train")
    assert observed.schema["availability"] == "historical_replay"
    features = observed.schema["streams"]["qualified_price_bar"]["features"]
    assert len(features) == (9 if profile == "candle-morphology-v1" else 17)

    output = tmp_path / f"{profile}-snapshots"
    result = _job(
        tmp_path,
        "build-sparse",
        dict(
            store=str(observation_store),
            pin=source_pin,
            output=str(output),
            plan=_sparse_plan(),
            profile=profile,
            chunk=7,
        ),
        capsys=capsys,
    )
    assert "snapshot" in result
    expected = sparse.schema(observed, _sparse_plan())
    snapshot = Snapshot(output, result["snapshot"], expected_schema=expected)
    wrong_schema = deepcopy(expected)
    wrong_schema["context"] += 1
    with pytest.raises(ValueError, match="experience_snapshot_contract"):
        Snapshot(output, result["snapshot"], expected_schema=wrong_schema)
    selection = snapshot.manifest["binding"]["selection"]["observation_profile"]
    assert selection["name"] == profile
    assert selection["source_pin"] == source_pin
    assert selection["continuous_source_pin"] == reference["pin"]
    assert selection["features"] == features
    assert selection["availability"] == "historical_replay"
    assert selection["cutoff_ns"] == observed.manifest["binding"]["cutoff_ns"]
    producer = next(item for item in observed.manifest["binding"]["sources"] if "producer" in item)
    assert selection["producer"] == producer
    assert producer["implementation"] == file_hash(Path(observation_adapters.__file__))
    assert producer["numerics"] == file_hash(Path(observation_adapters.__file__).with_name("numerics.py"))
    assert {"kind": "observations", "pin": source_pin} in snapshot.manifest["binding"]["sources"]
    assert snapshot.schema["features"][: len(features)] == [
        {**feature, "name": "value/" + feature["name"]} for feature in features
    ]

    rows = _read_tensor(snapshot)
    by_origin = {row["observation_origin"]["event_ns"]: (row, values) for row, values in rows}
    flat, flat_values = by_origin[300600 * NS]
    value_names = [feature["name"] for feature in features]
    if "close_position" in value_names:
        assert math.isnan(flat_values[value_names.index("close_position")])
    assert flat["target_details"][0]["reason"] == "exact_observed_endpoint"
    assert flat["decision_ns"] == 300600 * NS
    assert flat["target_details"][0]["price_provenance"]["endpoint"]["event_ns"] == 300900 * NS
    assert flat["input_available_ns"] <= observed.manifest["binding"]["cutoff_ns"]
    events = {row["event_ns"]: row for row in observed.rows() if row["kind"] == "event"}
    assert len(rows) == len(events) == 60
    for row, values in rows:
        fact = events[row["observation_origin"]["event_ns"]]
        assert row["decision_ns"] == fact["event_ns"]
        assert row["input_available_ns"] == fact["available_ns"]
        assert set(fact["evidence"]) <= {dep["artifact_sha"] for dep in row["input_dependencies"]}
        for actual, original in zip(values, fact["values"], strict=False):
            assert math.isnan(actual) if original is None else actual == pytest.approx(original)
    if profile == "candle-context-v1":
        singleton = by_origin[300300 * NS][1]
        assert all(math.isnan(singleton[i]) for i in (9, 10, 13, 14, 16))
        assert (singleton[11], singleton[12], singleton[15]) == (1, 0, 0)
        assert flat_values[9] == 0 and math.isnan(flat_values[10])
        assert flat_values[11:17] == (2, 300, 300, 1, 0, 1)
        full = by_origin[303600 * NS][1]
        assert full[11:17] == (12, 3300, 300, 1, 1, 0)
        assert producer["profile"]["identity"]["source_pin"] == reference["pin"]
        assert producer["profile"]["identity"]["cutoff_ns"] == selection["cutoff_ns"]

    repeated = _job(tmp_path, "build-sparse", dict(
        store=str(observation_store), pin=source_pin, output=str(tmp_path / "repeat"),
        plan=_sparse_plan(), profile=profile, chunk=7,
    ), capsys=capsys)
    assert repeated["snapshot"] == result["snapshot"]

    wrong_profile_output = tmp_path / f"{profile}-wrong-profile"
    incompatible_profile = (
        "candle-context-v1"
        if profile == "candle-morphology-v1"
        else "candle-morphology-v1"
    )
    with pytest.raises(ValueError, match="observation_profile"):
        _job(
            tmp_path,
            "build-sparse",
            dict(
                store=str(observation_store),
                pin=source_pin,
                output=str(wrong_profile_output),
                plan=_sparse_plan(),
                profile=incompatible_profile,
                chunk=7,
            ),
            capsys=capsys,
        )

    relative_plan = _sparse_plan()
    relative_plan["relative_features"] = ["close_usd"]
    assert not wrong_profile_output.exists()
    with pytest.raises(ValueError, match="observation_profile"):
        _job(
            tmp_path,
            "build-sparse",
            dict(
                store=str(observation_store),
                pin=source_pin,
                output=str(tmp_path / f"{profile}-relative"),
                plan=relative_plan,
                profile=profile,
                chunk=7,
            ),
            capsys=capsys,
        )
    assert not (tmp_path / f"{profile}-relative").exists()


def _through_jobs(root, reference, capsys, profile="candle-context-v1"):
    root.mkdir(parents=True, exist_ok=True)
    store = root / "observations"
    produced = _job(root, "observations-from-continuous", dict(
        reference=reference, store=str(store), profile=profile,
    ), capsys=capsys)
    source = observations.Source(store, produced["source"], role="train")
    built = _job(root, "build-sparse", dict(
        store=str(store), pin=produced["source"], output=str(root / "snapshots"),
        plan=_sparse_plan(), profile=profile,
    ), capsys=capsys)
    snapshot = Snapshot(root / "snapshots", built["snapshot"],
                        expected_schema=sparse.schema(source, _sparse_plan()))
    rows = {row["observation_origin"]["event_ns"]: (row, values)
            for row, values in _read_tensor(snapshot)}
    return source, snapshot, rows


def _export_piece(index, root, birth, cutoff):
    plan = dict(role="train", cutoff_ns=cutoff, start_s=300000, end_s=318000,
                selection="synthetic CLI profile cutoff regression",
                pools=[{k: birth[k] for k in ("pool", "base_mint", "quote_mint")}])
    result = export(index, root, plan)
    return dict(kind="continuous", path=str(root), pin=result["pin"])


def test_cli_inputs_preserve_candle_distinctions_that_legacy_loses(tmp_path, capsys):
    reference = _capture_source(tmp_path)
    for profile in PROFILES:
        _, _, rows = _through_jobs(tmp_path / profile, reference, capsys, profile)
        first, second = rows[300300 * NS][1], rows[300600 * NS][1]
        assert first[:4] == pytest.approx(second[:4])
        assert first[4:9] != second[4:9]
        assert first[4:9] == pytest.approx((2 / 3, 0, 1 / 3, 1 / 3, 0))
        assert second[4:9] == pytest.approx((0, 1 / 3, 2 / 3, 1, 0))
        if profile == "candle-context-v1":
            assert first[11] == 1 and second[11] == 2


def test_gapped_context_cli_reload_preserves_missing_movement_and_exact_targets(tmp_path, capsys):
    cap, birth = _capture_piece(tmp_path / "capture", [0, 1, 3, 4], 2 * 10**15)
    index = tmp_path / "index.sqlite"
    with connect(index) as db:
        ingest(db, cap, birth["pool"])
    reference = _export_piece(index, tmp_path / "export", birth, 3 * 10**15)
    source, snapshot, rows = _through_jobs(tmp_path / "jobs", reference, capsys)
    assert len(rows) == 4
    before_gap, _ = rows[300600 * NS]
    assert before_gap["target_status"] == ["missing_future"]
    assert before_gap["target_details"][0]["reason"] == "no_exact_endpoint_unknown_return"
    after_gap, values = rows[301200 * NS]
    assert math.isnan(values[9])
    assert values[11:17] == (3, 900, 600, 0, 0, 0)
    assert after_gap["target_status"] == ["observed"]
    assert after_gap["target_details"][0]["price_provenance"]["endpoint"]["event_ns"] == 301500 * NS
    assert snapshot.schema["availability"] == "historical_replay"
    assert all(row["status"] == "unknown" for row in source.rows() if row["kind"] == "support")


def test_delayed_contributors_and_revision_cutoffs_survive_actual_jobs(tmp_path, capsys):
    prior, birth = _capture_piece(tmp_path / "prior", range(13), 2 * 10**15)
    current, _ = _capture_piece(tmp_path / "current", range(13, 21), 15 * 10**14)
    revision, _ = _capture_piece(tmp_path / "revision", [12], 25 * 10**14, revise=1.4)
    index = tmp_path / "index.sqlite"
    with connect(index) as db:
        for cap in (prior, current, revision):
            ingest(db, cap, birth["pool"])
    early_ref = _export_piece(index, tmp_path / "early-export", birth, 16 * 10**14)
    late_ref = _export_piece(index, tmp_path / "late-export", birth, 3 * 10**15)
    early, early_snapshot, early_rows = _through_jobs(tmp_path / "early-jobs", early_ref, capsys)
    late, late_snapshot, late_rows = _through_jobs(tmp_path / "late-jobs", late_ref, capsys)
    assert len(early_rows) == 8 and len(late_rows) == 21
    assert 303900 * NS not in early_rows  # unavailable contributor at open 303600
    early_row, early_values = early_rows[304200 * NS]
    late_row, late_values = late_rows[304200 * NS]
    assert math.isnan(early_values[9]) and math.isnan(early_values[10])
    assert (early_values[11], early_values[15]) == (1, 0)
    assert early_row["input_available_ns"] == 15 * 10**14
    assert late_values[9:17] == pytest.approx([
        math.log(1.0013 / 1.0012), (1.0013 - 0.9) / (1.1 - 0.9),
        12, 3300, 300, 1, 1, 0,
    ])
    assert late_row["decision_ns"] == 304200 * NS < late_row["input_available_ns"] == 2 * 10**15
    expected_evidence = {file_hash(prior / "manifest.json"), file_hash(current / "manifest.json")}
    late_event = next(row for row in late.rows() if row["kind"] == "event" and row["id"] == "303900")
    assert set(late_event["evidence"]) == expected_evidence
    assert file_hash(revision / "manifest.json") not in late_event["evidence"]
    assert expected_evidence <= {dep["artifact_sha"] for dep in late_row["input_dependencies"]}
    assert late_event["available_ns"] == late_row["input_available_ns"]
    assert late_event["values"][9:17] == pytest.approx(late_values[9:17])
    for source, snapshot, reference, cutoff in (
        (early, early_snapshot, early_ref, 16 * 10**14),
        (late, late_snapshot, late_ref, 3 * 10**15),
    ):
        selected = snapshot.manifest["binding"]["selection"]["observation_profile"]
        assert snapshot.schema["availability"] == selected["availability"] == "historical_replay"
        assert selected["source_pin"] == source.pin
        assert selected["continuous_source_pin"] == reference["pin"]
        assert snapshot.manifest["binding"]["cutoff_ns"] == selected["cutoff_ns"] == cutoff
        assert selected["producer"]["profile"]["identity"]["cutoff_ns"] == cutoff
        assert selected["producer"]["profile"]["identity"]["source_pin"] == reference["pin"]
