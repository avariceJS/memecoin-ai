"""Sparse admission must authenticate every sealed volume-context state row.

The malformed-source cases intentionally fail on the frozen implementation: the
read-only volume diagnostic rejects them, while build-sparse currently publishes
a Snapshot. They become passing regressions with the sibling admission fix.
"""

import json
import math
import struct
import sys

import pytest

from memecoin_experience import __main__ as experience_cli, sparse
from memecoin_experience import observations
from memecoin_experience.observations import Source
from memecoin_experience.storage import Snapshot, file_hash
from test_experience_candle_context_integration import _snapshot_rows, _sparse_plan
from test_experience_candle_volume_context import PROFILE, _bars, _events, _volume_source

NS = 10**9
PROFILE_NAME = "candle-volume-context-v1"


def _cli_job(root, action, payload, capsys):
    job = root / f"{action}.json"
    job.write_text(json.dumps(payload, sort_keys=True))
    prior = sys.argv
    try:
        sys.argv = ["memecoin_experience", action, "--job", str(job)]
        experience_cli.main()
    finally:
        sys.argv = prior
    return json.loads(capsys.readouterr().out)


def _eligibility_cli(store, pin, capsys):
    prior = sys.argv
    try:
        sys.argv = [
            "memecoin_experience", "volume-eligibility", "--store", str(store),
            "--source", pin, "--role", "train",
        ]
        experience_cli.main()
    finally:
        sys.argv = prior
    return json.loads(capsys.readouterr().out)


def _store_hashes(root):
    return {
        str(path.relative_to(root)): file_hash(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _mutated_source(tmp_path, case):
    pool = "V" * 32
    reference, original_store, original_pin = _volume_source(
        tmp_path / "origin",
        [dict(pool=pool, bars=_bars([300000, 300300, 300600], [0.0, 0.0, 0.0]))],
    )
    original = Source(original_store, original_pin, role="train")
    rows = list(original.rows())
    events = {int(row["id"]): row for row in rows if row["kind"] == "event"}
    target_id = 300600
    if case == "ineligible_delta_present":
        target_id = 300300
    values = events[target_id]["values"]
    if case == "delta_missingness":
        values[18] = None
    elif case == "ineligible_delta_present":
        values[18] = 0.0
    elif case == "fractional_prior_count":
        values[17] = 1.5
    elif case == "fractional_context_count":
        values[11] = 3.5
    elif case == "negative_prior_count":
        values[17] = -1
    elif case == "zero_context_count":
        values[11] = 0
    elif case == "count_mismatch":
        values[17] = 1
    elif case == "singleton_gap_present":
        events[300000]["values"][13] = 300
    elif case == "impossible_span":
        values[12] = 300
    elif case == "impossible_gap":
        values[13] = 900
    elif case == "contradictory_contiguity":
        values[14] = 0.0
        values[13] = 300
    else:
        raise AssertionError(case)

    # Publish an authenticated, newly sealed source with the original feature
    # contract and continuous-source binding. Only the chosen row value differs.
    store = tmp_path / f"source-{case}"
    pin = observations.publish(
        store,
        rows,
        schema=original.schema,
        cutoff=original.manifest["binding"]["cutoff_ns"],
        sources=original.manifest["binding"]["sources"],
        chunk=original.manifest["binding"]["chunk"],
    )
    sealed = Source(store, pin, role="train")
    assert sealed.schema == original.schema
    assert sealed.manifest["binding"]["sources"] == original.manifest["binding"]["sources"]
    return reference, store, pin, original_store, original_pin


@pytest.mark.parametrize(
    "case",
    [
        "delta_missingness",
        "ineligible_delta_present",
        "fractional_prior_count",
        "fractional_context_count",
        "negative_prior_count",
        "zero_context_count",
        "count_mismatch",
        "singleton_gap_present",
        "impossible_span",
        "impossible_gap",
        "contradictory_contiguity",
    ],
)
def test_build_sparse_rejects_authenticated_volume_state_contradictions(
    tmp_path, capsys, case
):
    """Diagnostic and materializer must agree before any output is committed."""
    _reference, store, pin, original_store, original_pin = _mutated_source(tmp_path, case)
    before_source = _store_hashes(store)
    before_original = _store_hashes(original_store)

    # Baseline comparison: the diagnostic validates the selected source rows;
    # build-sparse must apply the same validation before writing a Snapshot.
    with pytest.raises(ValueError, match="^volume_eligibility_"):
        _eligibility_cli(store, pin, capsys)
    assert capsys.readouterr().out == ""

    output = tmp_path / "snapshots"
    job = dict(
        store=str(store), pin=pin, output=str(output), plan=_sparse_plan(),
        profile=PROFILE_NAME, role="train", chunk=7,
    )
    job_path = tmp_path / "build-sparse.json"
    job_path.write_text(json.dumps(job, sort_keys=True))
    prior = sys.argv
    try:
        sys.argv = ["memecoin_experience", "build-sparse", "--job", str(job_path)]
        with pytest.raises(ValueError, match="^volume_eligibility_"):
            experience_cli.main()
    finally:
        sys.argv = prior
    assert capsys.readouterr().out == ""  # no success JSON
    assert not output.exists() or not list(output.rglob("manifest.json"))
    assert _store_hashes(store) == before_source
    assert _store_hashes(original_store) == before_original
    assert Source(original_store, original_pin, role="train").pin == original_pin


def test_volume_eligibility_cli_reports_baseline_sealed_source_summary(tmp_path, capsys):
    """The diagnostic is a meaningful baseline comparator, not a parser probe."""
    pool = "W" * 32
    _reference, store, pin = _volume_source(
        tmp_path,
        [dict(pool=pool, bars=_bars([300000, 300300, 300600], [0.0, 0.0, 0.0]))],
    )
    report = _eligibility_cli(store, pin, capsys)
    assert report["schema"] == "candle-volume-eligibility-v1"
    assert report["source_pin"] == pin
    assert report["profile"]["name"] == PROFILE_NAME
    assert report["global"] == {
        "observed": 3,
        "eligible": 1,
        "insufficient_prior": 2,
        "gapped_history": 0,
        "eligibility_fraction": pytest.approx(1 / 3),
    }


def test_admission_checks_source_row_omitted_by_sparse_sample_selection(
    tmp_path, capsys, monkeypatch
):
    """Admission validates sealed input before sparse selection can omit a row."""
    _reference, store, pin, _original_store, _original_pin = _mutated_source(
        tmp_path, "delta_missingness"
    )
    with pytest.raises(ValueError, match="^volume_eligibility_delta_missingness$"):
        _eligibility_cli(store, pin, capsys)

    # Model a valid sparse selection that emits the first two origins but does
    # not select the third. Source validation must precede sample generation,
    # so a malformed row hidden by that selection still blocks publication.
    actual_samples = sparse.samples

    def omit_third_origin(db, source, plan, counts=None):
        return (
            sample
            for sample in actual_samples(db, source, plan, counts)
            if sample["observation_origin"]["event_ns"] != 300900 * NS
        )

    monkeypatch.setattr(sparse, "samples", omit_third_origin)
    output = tmp_path / "omitted-origin-snapshot"
    job = dict(
        store=str(store), pin=pin, output=str(output), plan=_sparse_plan(),
        profile=PROFILE_NAME, role="train", chunk=7,
    )
    path = tmp_path / "omitted-origin-build.json"
    path.write_text(json.dumps(job, sort_keys=True))
    prior = sys.argv
    try:
        sys.argv = ["memecoin_experience", "build-sparse", "--job", str(path)]
        with pytest.raises(ValueError, match="^volume_eligibility_delta_missingness$"):
            experience_cli.main()
    finally:
        sys.argv = prior
    assert capsys.readouterr().out == ""
    assert not output.exists() or not list(output.rglob("manifest.json"))


def test_valid_zero_volume_sparse_cli_retains_masks_clocks_and_provenance(tmp_path, capsys):
    """Valid singleton/prior/contiguous rows still materialize with exact lineage."""
    pool = "X" * 32
    reference, store, pin = _volume_source(
        tmp_path,
        [dict(pool=pool, bars=_bars([300000, 300300, 300600], [0.0, 0.0, 0.0]))],
    )
    source = Source(store, pin, role="train")
    output = tmp_path / "valid-snapshots"
    result = _cli_job(
        tmp_path,
        "build-sparse",
        dict(
            store=str(store), pin=pin, output=str(output), plan=_sparse_plan(),
            profile=PROFILE, role="train", chunk=7,
        ),
        capsys,
    )
    snapshot = Snapshot(output, result["snapshot"])
    rows = _events(store, pin)
    entity = "solana/pool/" + pool
    assert rows[(entity, 300000)]["values"][17:19] == [0, None]
    assert rows[(entity, 300300)]["values"][17:19] == [1, None]
    assert rows[(entity, 300600)]["values"][17] == 2
    assert rows[(entity, 300600)]["values"][18] == pytest.approx(0.0)
    assert snapshot.schema["availability"] == "historical_replay"
    assert [f["name"] for f in source.schema["streams"]["qualified_price_bar"]["features"]][-2:] == [
        "prior_volume_count", "volume_log1p_delta",
    ]
    assert {"kind": "observations", "pin": pin} in snapshot.manifest["binding"]["sources"]
    assert snapshot.manifest["binding"]["selection"]["observation_profile"]["source_pin"] == pin
    assert snapshot.manifest["binding"]["selection"]["observation_profile"]["continuous_source_pin"] == reference["pin"]


def test_valid_gapped_multi_entity_reset_history_matches_default_snapshot(tmp_path, capsys):
    """Admission preserves tensors, exact targets, clocks and reset missingness."""
    left, right = "Y" * 32, "Z" * 32
    opens = [300000, 300300, 300600, 300900, 301500]
    reference, store, pin = _volume_source(
        tmp_path,
        [dict(pool=pool, bars=_bars(opens, [0.0] * len(opens))) for pool in (left, right)],
        intervals={left: [[300000, 300600], [300600, 302100]], right: [[300000, 302100]]},
        end_s=302100,
    )
    before = _store_hashes(store)
    report = _eligibility_cli(store, pin, capsys)
    assert report["global"]["observed"] == 10
    assert report["global"]["gapped_history"] == 2
    events = _events(store, pin)
    left_entity, right_entity = "solana/pool/" + left, "solana/pool/" + right
    assert events[(left_entity, 300600)]["values"][17:19] == [0, None]
    assert events[(right_entity, 300600)]["values"][17:19] == [2, 0.0]
    assert events[(right_entity, 301500)]["values"][18] is None
    source = Source(store, pin, role="train")
    features = source.schema["streams"]["qualified_price_bar"]["features"]
    snapshots = []
    for label, profile in (("explicit", PROFILE), ("default", None)):
        output = tmp_path / (label + "-snapshots")
        job = dict(store=str(store), pin=pin, output=str(output), plan=_sparse_plan(), role="train", chunk=3)
        if profile is not None:
            job["profile"] = profile
        result = _cli_job(tmp_path, "build-sparse", job, capsys)
        snapshot, persisted = _snapshot_rows(output, result["snapshot"])
        assert snapshot.schema == sparse.schema(source, _sparse_plan())
        assert snapshot.schema["availability"] == "historical_replay"
        assert [(f["name"], f["unit"]) for f in snapshot.schema["features"][:19]] == [
            ("value/" + f["name"], f["unit"]) for f in features
        ]
        ys = []
        for i in range(len(snapshot.parts)):
            path, rows = snapshot.partition(i)
            ys.extend(struct.unpack(f"<{len(rows)}d", (path / "y.f64").read_bytes()))
        snapshots.append((snapshot, persisted, ys))
    explicit, persisted, ys = snapshots[0]
    _, default_rows, default_ys = snapshots[1]
    assert len(persisted) == len(default_rows) == len(events)
    observed = missing = 0
    for (row, values), (default_row, default_values), y, default_y in zip(
        persisted, default_rows, ys, default_ys, strict=True
    ):
        assert row == default_row
        assert values == pytest.approx(default_values, nan_ok=True)
        assert y == pytest.approx(default_y, nan_ok=True)
        origin = row["observation_origin"]["event_ns"]
        event = events[(row["pool"], origin // NS - 300)]
        assert row["decision_ns"] == origin == event["event_ns"]
        assert row["input_available_ns"] == event["available_ns"]
        assert set(event["evidence"]) <= {d["artifact_sha"] for d in row["input_dependencies"]}
        delta = event["values"][18]
        assert math.isnan(values[18]) if delta is None else values[18] == delta
        if row["target_status"] == ["observed"]:
            endpoint = events[(row["pool"], origin // NS)]
            assert y == pytest.approx(math.log(endpoint["values"][0] / event["values"][0]))
            observed += 1
        else:
            assert math.isnan(y)
            missing += 1
    assert observed and missing
    assert explicit.manifest["binding"]["selection"]["observation_profile"]["continuous_source_pin"] == reference["pin"]
    assert _store_hashes(store) == before
