"""Sealed-source CLI regressions for prior-volume eligibility.

Fixtures use sealed synthetic captures, the continuous export, and the existing
observation producer before exercising the read-only diagnostic.
"""

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from memecoin_experience import __main__ as cli
from memecoin_experience.observations import Source, publish
from memecoin_experience.storage import object_path
from test_experience_candle_volume_context import PROFILE, _bars, _qualified_source

NS = 10**9


def _hash_tree(root):
    root = Path(root)
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _invoke(action, payload, root, capsys):
    job = root / f"{action}.json"
    job.write_text(json.dumps(payload, sort_keys=True))
    old = sys.argv
    try:
        sys.argv = ["memecoin_experience", action, "--job", str(job)]
        cli.main()
    finally:
        sys.argv = old
    return json.loads(capsys.readouterr().out)


def _publish(root, captures, capsys, *, profile=PROFILE, intervals=None):
    reference = _qualified_source(root / "capture-source", captures, intervals=intervals)
    store = root / "observations"
    result = _invoke(
        "observations-from-continuous",
        {"reference": reference, "store": str(store), "role": "train", "profile": profile},
        root,
        capsys,
    )
    source = Source(store, result["source"], role="train")
    contract = source.manifest["binding"]["contract"]
    assert contract["availability"] == "historical_replay"
    if profile == PROFILE:
        assert [(f["name"], f["unit"]) for f in contract["streams"]["qualified_price_bar"]["features"][-2:]] == [
            ("prior_volume_count", "observed_bar_count"),
            ("volume_log1p_delta", "log1p_USD_difference"),
        ]
    assert reference["pin"] in json.dumps(source.manifest["binding"]["sources"], sort_keys=True)
    return reference, store, source


def _run_eligibility(root, reference, store, source, capsys):
    old = sys.argv
    try:
        sys.argv = ["memecoin_experience", "volume-eligibility", "--store", str(store),
                    "--source", source.pin, "--role", "train"]
        cli.main()
    finally:
        sys.argv = old
    return json.loads(capsys.readouterr().out)


def test_cli_partitions_qualified_events_by_prior_history_and_entity(tmp_path, capsys):
    a, b = "A" * 32, "B" * 32
    captures = [
        # A: singleton; one prior; eligible contiguous row with genuine zero delta;
        # then a gap where enough priors exist, so gapped_history wins.
        dict(pool=a, bars=_bars([300000, 300300, 300600, 301200], [0.0, 0.0, 0.0, 3.0])),
        # B: two insufficient rows, then an eligible zero-volume current row.
        dict(pool=b, bars=_bars([300000, 300300, 300600], [4.0, 4.0, 0.0])),
    ]
    reference, store, source = _publish(tmp_path, captures, capsys)
    before_store, before_pin = _hash_tree(store), _hash_tree(reference["path"])

    result = _run_eligibility(tmp_path, reference, store, source, capsys)
    expected_counts = dict(observed=7, eligible=2, insufficient_prior=4, gapped_history=1)
    assert result == {
        "schema": "candle-volume-eligibility-v1",
        "source_pin": source.pin,
        "continuous_source_pin": reference["pin"],
        "profile": {"name": PROFILE, "version": 1},
        "limitation": "historical_replay",
        "denominator": "qualified_price_bar",
        "global": {**expected_counts, "eligibility_fraction": 2 / 7},
        "entities": {
            "solana/pool/" + a: dict(observed=4, eligible=1, insufficient_prior=2, gapped_history=1, eligibility_fraction=1 / 4),
            "solana/pool/" + b: dict(observed=3, eligible=1, insufficient_prior=2, gapped_history=0, eligibility_fraction=1 / 3),
        },
    }
    assert sum(v["observed"] for v in result["entities"].values()) == result["global"]["observed"]
    for category in ("eligible", "insufficient_prior", "gapped_history"):
        assert sum(v[category] for v in result["entities"].values()) == result["global"][category]
    # The producer's independent rows prove the zero-delta and missing-delta controls.
    assert any(row["kind"] != "event" for row in source.rows())
    rows = {(row["entity"], row["id"]): row for row in source.rows() if row["kind"] == "event"}
    assert rows[("solana/pool/" + a, "300600")]["values"][18] == 0.0
    assert rows[("solana/pool/" + a, "301200")]["values"][18] is None
    assert rows[("solana/pool/" + b, "300600")]["values"][17] == 2

    # Machine output and all sealed inputs stay byte stable across repeated reads.
    repeated = _run_eligibility(tmp_path, reference, store, source, capsys)
    assert json.dumps(repeated, sort_keys=True, separators=(",", ":")) == json.dumps(
        result, sort_keys=True, separators=(",", ":")
    )
    assert _hash_tree(store) == before_store
    assert _hash_tree(reference["path"]) == before_pin

    # Exercise the installed module entry point in a real interpreter process.
    process = subprocess.run(
        [sys.executable, "-S", "-m", "memecoin_experience", "volume-eligibility",
         "--store", str(store), "--source", source.pin],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(process.stdout) == result
    assert process.stderr == ""
    assert _hash_tree(store) == before_store
    assert _hash_tree(reference["path"]) == before_pin


def test_empty_observation_source_has_null_zero_denominator(tmp_path, capsys):
    # A valid enrollment with no observed bars gives a nonempty source whose
    # qualified event denominator is genuinely zero.
    reference, store, source = _publish(
        tmp_path,
        [dict(pool="H" * 32, bars=[])],
        capsys,
    )
    result = _run_eligibility(tmp_path, reference, store, source, capsys)
    assert result["global"] == {
        "observed": 0,
        "eligible": 0,
        "insufficient_prior": 0,
        "gapped_history": 0,
        "eligibility_fraction": None,
    }
    assert result["entities"] == {}


def test_touching_original_intervals_reset_prior_history(tmp_path, capsys):
    pool = "D" * 32
    captures = [dict(pool=pool, bars=_bars([300000, 300300, 300600, 300900], [2.0] * 4))]
    reference, store, source = _publish(
        tmp_path,
        captures,
        capsys,
        intervals={pool: [[300000, 300600], [300600, 301200]]},
    )
    rows = [row for row in source.rows() if row["kind"] == "event"]
    # The producer, rather than a handwritten summary, verifies the reset at the
    # shared endpoint: no event carries prior-volume history across either row.
    assert [row["values"][17] for row in rows] == [0, 1, 0, 1]
    result = _run_eligibility(tmp_path, reference, store, source, capsys)
    entity = "solana/pool/" + pool
    assert result["global"] == dict(
        observed=4,
        eligible=0,
        insufficient_prior=4,
        gapped_history=0,
        eligibility_fraction=0,
    )
    assert result["entities"][entity] == result["global"]


def test_one_prior_gap_is_insufficient_before_gapped_history(tmp_path, capsys):
    reference, store, source = _publish(
        tmp_path,
        [dict(pool="K" * 32, bars=_bars([300000, 300600], [0.0, 0.0]))],
        capsys,
    )
    rows = [row for row in source.rows() if row["kind"] == "event"]
    assert rows[-1]["values"][14] == 0.0
    assert rows[-1]["values"][17:] == [1, None]
    result = _run_eligibility(tmp_path, reference, store, source, capsys)
    assert result["global"] == dict(
        observed=2, eligible=0, insufficient_prior=2, gapped_history=0,
        eligibility_fraction=0,
    )


def test_wrong_profile_source_is_rejected(tmp_path, capsys):
    reference, store, source = _publish(
        tmp_path,
        [dict(pool="E" * 32, bars=_bars([300000, 300300, 300600], [1.0, 1.0, 2.0]))],
        capsys,
        profile="candle-context-v1",
    )
    assert len(source.schema["streams"]["qualified_price_bar"]["features"]) == 17
    _assert_cli_rejects(tmp_path, store, source.pin, capsys)


def test_cli_rejects_wrong_role_and_source_pin(tmp_path, capsys):
    _, store, source = _publish(
        tmp_path,
        [dict(pool="F" * 32, bars=_bars([300000, 300300, 300600], [1.0, 1.0, 2.0]))],
        capsys,
    )
    _assert_cli_rejects(tmp_path, store, source.pin, capsys, role="research_evaluation")
    _assert_cli_rejects(tmp_path, store, "0" * 64, capsys)


def test_cli_rejects_corrupt_sealed_source_file(tmp_path, capsys):
    _, store, source = _publish(
        tmp_path,
        [dict(pool="I" * 32, bars=_bars([300000, 300300, 300600], [1.0, 1.0, 2.0]))],
        capsys,
    )
    altered_store = tmp_path / "corrupt-observations"
    shutil.copytree(store, altered_store)
    part = source.parts[0]
    facts = object_path(altered_store, part["pin"]) / "facts.jsonl"
    facts.write_bytes(facts.read_bytes() + b" ")
    _assert_cli_rejects(tmp_path, altered_store, source.pin, capsys)


@pytest.mark.parametrize(
    ("tamper", "event_id", "feature", "value"),
    [
        ("missing_contiguous_delta", "300600", 18, None),
        ("delta_on_gap", "301200", 18, 0.0),
        ("wrong_prior_count", "300600", 17, 1),
        ("false_contiguous", "301200", 14, 1.0),
        ("false_gap", "300600", 14, 0.0),
        ("invalid_contiguous", "300600", 14, 0.5),
        ("impossible_max_gap", "300600", 13, 600),
        ("impossible_count", "300600", 11, 13),
    ],
)
def test_cli_rejects_resealed_volume_state_contradictions(
    tmp_path, capsys, tamper, event_id, feature, value
):
    reference, store, source = _publish(
        tmp_path,
        [dict(pool="G" * 32, bars=_bars([300000, 300300, 300600, 301200], [1.0] * 4))],
        capsys,
    )
    original_rows = list(source.rows())
    row = next(r for r in original_rows if r["kind"] == "event" and r["id"] == event_id)
    row["values"][feature] = value
    altered_store = tmp_path / f"altered-{tamper}"
    altered_pin = publish(
        altered_store,
        original_rows,
        schema=source.schema,
        cutoff=source.manifest["binding"]["cutoff_ns"],
        sources=source.manifest["binding"]["sources"],
    )
    altered = Source(altered_store, altered_pin, role="train")
    assert list(altered.rows())
    _assert_cli_rejects(tmp_path, altered_store, altered.pin, capsys)
    # The original source and continuous pin remain byte stable after the failed read.
    assert Source(store, source.pin, role="train").pin == source.pin
    assert reference["pin"] in json.dumps(source.manifest["binding"]["sources"], sort_keys=True)


def test_cli_rejects_resealed_continuous_source_identity_mismatch(tmp_path, capsys):
    _, store, source = _publish(
        tmp_path,
        [dict(pool="J" * 32, bars=_bars([300000, 300300, 300600], [1.0] * 3))],
        capsys,
    )
    rows = list(source.rows())
    sources = [dict(item) for item in source.manifest["binding"]["sources"]]
    producer = next(item for item in sources if "producer" in item)
    producer["profile"]["identity"]["source_pin"] = "0" * 64
    altered_store = tmp_path / "altered-identity"
    altered_pin = publish(
        altered_store,
        rows,
        schema=source.schema,
        cutoff=source.manifest["binding"]["cutoff_ns"],
        sources=sources,
    )
    _assert_cli_rejects(tmp_path, altered_store, altered_pin, capsys)


@pytest.mark.parametrize("alteration", ["profile_version", "profile_features", "contract_unit"])
def test_cli_rejects_resealed_altered_profile_schema(tmp_path, capsys, alteration):
    _, _, source = _publish(
        tmp_path,
        [dict(pool="L" * 32, bars=_bars([300000, 300300, 300600], [1.0] * 3))],
        capsys,
    )
    sources = copy.deepcopy(source.manifest["binding"]["sources"])
    schema = copy.deepcopy(source.schema)
    producer = next(item for item in sources if "producer" in item)
    if alteration == "profile_version":
        producer["profile"]["version"] = 2
    elif alteration == "profile_features":
        producer["profile"]["features"].reverse()
    else:
        schema["streams"]["qualified_price_bar"]["features"][-1]["unit"] = "raw_ratio"
    altered_store = tmp_path / alteration
    pin = publish(
        altered_store, list(source.rows()), schema=schema,
        cutoff=source.manifest["binding"]["cutoff_ns"], sources=sources,
    )
    _assert_cli_rejects(tmp_path, altered_store, pin, capsys)


def _assert_cli_rejects(root, store, pin, capsys, *, role="train"):
    # A missing subcommand also exits nonzero, so prove the opt-in CLI action is
    # registered before treating any negative case as meaningful.
    old = sys.argv
    try:
        sys.argv = ["memecoin_experience", "volume-eligibility", "--help"]
        with pytest.raises(SystemExit) as result:
            cli.main()
        assert result.value.code == 0, "baseline: volume-eligibility CLI is not implemented"
    finally:
        sys.argv = old
    capsys.readouterr()
    old = sys.argv
    try:
        sys.argv = ["memecoin_experience", "volume-eligibility", "--store", str(store),
                    "--source", pin, "--role", role]
        with pytest.raises((ValueError, FileNotFoundError)):
            cli.main()
        assert capsys.readouterr().out == ""
    finally:
        sys.argv = old
