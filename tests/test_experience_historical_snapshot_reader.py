import json
import shutil

import pytest

from memecoin_experience import coordinate_samples
from memecoin_experience.storage import Snapshot, build, digest, seal_files


def artifact(tmp_path, *, availability="historical_replay", observed=False):
    schema = dict(
        schema="brain-experience-v2", layout="dense-sequence-f64-v1", context=1,
        features=[dict(name="price", unit="USD", modality="price")],
        targets=[dict(name="return", unit="ratio", kind="endpoint_log_return")],
        representation=dict(name="synthetic", version="1", code_sha="c" * 64),
        availability=availability,
    )
    detail = dict(reason="exact_observed_endpoint", price_provenance=dict(
        base=dict(event_ns=10, available_ns=10, evidence=["b" * 64]),
        endpoint=dict(event_ns=20, available_ns=25, evidence=["d" * 64]),
    )) if observed else dict(reason="origin_not_available_at_decision")
    row = dict(
        id="synthetic:1", pool="synthetic", decision_ns=10, input_start_ns=5,
        input_available_ns=20 if availability == "historical_replay" else 10,
        input_dependencies=[dict(artifact_sha="a" * 64, event_ns=5,
                                 available_ns=20 if availability == "historical_replay" else 10)],
        values=[[1.0]], targets=[0.1] if observed else [None],
        target_status=["observed"] if observed else ["unsupported"],
        target_available_ns=[25] if observed else [None], target_maturity_ns=[20],
        target_captures=[["b" * 64, "d" * 64]] if observed else [[]], target_details=[detail],
    )
    result = build(tmp_path, [row], sources=[dict(synthetic=True)], cutoff=30, schema=schema)
    return tmp_path, result["snapshot"]


def tamper_and_reseal(store, snapshot_pin, mutate):
    snapshot_path = store / "snapshots" / (snapshot_pin + ".json")
    manifest = json.loads(snapshot_path.read_text())
    entry = manifest["partitions"][0]
    old = store / "objects" / entry["pin"][:2] / entry["pin"]
    staging = store / "staging" / "resealed-test"
    shutil.copytree(old, staging)
    (staging / "manifest.json").unlink()
    rows_path = staging / "rows.jsonl"
    row = json.loads(rows_path.read_text().splitlines()[0])
    mutate(row)
    rows_path.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    new_pin = seal_files(staging)
    new_path = store / "objects" / new_pin[:2] / new_pin
    new_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(staging, new_path)
    entry["pin"] = new_pin
    new_snapshot_pin = digest(manifest)
    (store / "snapshots" / (new_snapshot_pin + ".json")).write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )
    return new_snapshot_pin


def test_resealed_metadata_cannot_bypass_pit_clock_gate(tmp_path):
    store, pin = artifact(tmp_path, availability="point_in_time")
    original = Snapshot(store, pin)
    original.partition(0)
    # Reseal both the partition and snapshot after changing only the receipt clock.
    new_pin = tamper_and_reseal(store, pin, lambda row: row.update(
        input_available_ns=20,
        input_dependencies=[dict(artifact_sha="a" * 64, event_ns=5, available_ns=20)],
    ))
    with pytest.raises(ValueError, match="sample_contract|dependency"):
        Snapshot(store, new_pin).partition(0)


def test_readback_keeps_valid_historical_late_receipt(tmp_path):
    store, pin = artifact(tmp_path)
    _path, rows = Snapshot(store, pin).partition(0)
    assert rows[0]["input_available_ns"] == 20
    assert rows[0]["target_details"][0]["reason"] == "origin_not_available_at_decision"


def test_resealed_target_provenance_cannot_outlive_observed_receipt(tmp_path):
    store, pin = artifact(tmp_path, observed=True)
    Snapshot(store, pin).partition(0)

    def forge(row):
        row["target_details"][0]["price_provenance"]["endpoint"]["available_ns"] = 26

    forged_pin = tamper_and_reseal(store, pin, forge)
    with pytest.raises(ValueError, match="experience_target_provenance"):
        Snapshot(store, forged_pin).partition(0)


def test_legacy_v1_readback_remains_compatible(tmp_path):
    from test_experience import bars

    result = build(
        tmp_path, bars(12), sources=[dict(synthetic=True)], cutoff=20_000 * 10**9, context=2
    )
    assert Snapshot(tmp_path, result["snapshot"]).partition(0)[1]


def test_coordinate_v3_readback_remains_compatible(tmp_path):
    schema = coordinate_samples.schema(
        context=1,
        features=[dict(name="prior_count", unit="events", modality="process")],
        targets=[dict(name="next_count", unit="events", kind="observed_process_count@1slots")],
        availability="historical_replay",
    )
    domain_pin = coordinate_samples.digest(coordinate_samples.SLOT_DOMAIN)
    row = dict(
        id="synthetic:1", pool="synthetic",
        origin=dict(domain_pin=domain_pin, coordinate=1, position=[]), decision_ns=None,
        input_dependencies=[dict(artifact_sha="a" * 64, domain_pin=domain_pin,
                                 start_coordinate=1, end_coordinate_exclusive=2, available_ns=90)],
        input_available_ns=90, values=[[1.0]], targets=[1], target_status=["exact"],
        target_windows=[dict(domain_pin=domain_pin, start_coordinate=2, end_coordinate_exclusive=3,
                             maturity_coordinate_exclusive=3, completion_status="complete",
                             completion_evidence=["b" * 64], support_reasons=[])],
        target_available_ns=[90], target_details=[{}], provenance=["c" * 64],
    )
    result = build(tmp_path, [row], sources=[dict(synthetic=True)], cutoff=100, schema=schema)
    assert Snapshot(tmp_path, result["snapshot"]).partition(0)[1]


@pytest.mark.parametrize("availability", ["historical_replay", "point_in_time"])
def test_real_producer_late_base_through_storage_and_reader(tmp_path, availability):
    from test_experience_sparse_sealed_replay import CUTOFF, NS, _read

    _, produced, checked, snapshot, persisted = _read(
        tmp_path,
        [("old-trigger", 3, 5, 10.0), ("late-base", 5, 6, 20.0),
         ("endpoint", 15, 16, 40.0)],
        availability=availability,
    )
    for collection in (produced, checked, persisted):
        origin = 5 if availability == "historical_replay" else 3
        row = next(r for r in collection if r["decision_ns"] == 5 * NS
                   and r["observation_origin"]["event_ns"] == origin * NS)
        if availability == "historical_replay":
            assert row["target_status"][0] == "observed"
            assert row["target_details"][0]["price_provenance"]["base"] == dict(
                event_ns=5 * NS, available_ns=6 * NS,
                evidence=row["target_details"][0]["price_provenance"]["base"]["evidence"],
            )
            assert row["target_available_ns"][0] <= CUTOFF
        else:
            assert row["target_status"][0] == "unsupported"
            assert row["target_details"][0]["reason"] == "origin_not_available_at_decision"
            assert row["input_available_ns"] <= row["decision_ns"]
    assert snapshot.schema["availability"] == availability


@pytest.mark.parametrize("mutation, error", [
    (lambda r: r.update(input_available_ns=31), "sample_contract"),
    (lambda r: r["input_dependencies"][0].update(artifact_sha="unbound"), "dependency"),
    (lambda r: r["target_details"][0]["price_provenance"]["base"].update(event_ns=9), "provenance"),
    (lambda r: r["target_details"][0]["price_provenance"]["endpoint"].update(event_ns=19), "provenance"),
    (lambda r: r["target_details"][0]["price_provenance"]["base"].update(available_ns=31), "provenance"),
])
def test_resealed_metadata_rejects_inconsistent_clocks_and_lineage(tmp_path, mutation, error):
    store, pin = artifact(tmp_path, observed=True)
    forged = tamper_and_reseal(store, pin, mutation)
    with pytest.raises(ValueError, match=error):
        Snapshot(store, forged).partition(0)


def test_older_snapshot_without_optional_price_metadata_is_readable(tmp_path):
    # The pre-change v2 contract did not require price_provenance or target_details.
    store, pin = artifact(tmp_path, observed=True)
    older = tamper_and_reseal(store, pin, lambda r: r.pop("target_details"))
    _, rows = Snapshot(store, older).partition(0)
    assert rows[0]["target_status"] == ["observed"]
    assert "target_details" not in rows[0]
