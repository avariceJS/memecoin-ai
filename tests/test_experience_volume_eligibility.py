"""Focused checks for authenticated prior-volume eligibility inspection."""

import hashlib
import json
import sys

import pytest

from memecoin_experience import __main__ as cli
from memecoin_experience.volume_eligibility import _validate_row
from test_experience_candle_volume_context import _bars, _volume_source


def _tree_hashes(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_cli_emits_deterministic_authenticated_summary_without_store_mutation(tmp_path, monkeypatch, capsys):
    pool = "E" * 32
    reference, store, pin = _volume_source(
        tmp_path,
        [dict(pool=pool, bars=_bars([300000, 300300, 300600, 301200], [0.0, 5.0, 0.0, 1.0]))],
    )
    before = _tree_hashes(store)
    argv = ["memecoin_experience", "volume-eligibility", "--store", str(store), "--source", pin]

    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    first = capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    second = capsys.readouterr().out

    assert first == second
    result = json.loads(first)
    entity = "solana/pool/" + pool
    expected = {
        "observed": 4,
        "eligible": 1,
        "insufficient_prior": 2,
        "gapped_history": 1,
        "eligibility_fraction": 0.25,
    }
    assert result == {
        "schema": "candle-volume-eligibility-v1",
        "source_pin": pin,
        "continuous_source_pin": reference["pin"],
        "profile": {"name": "candle-volume-context-v1", "version": 1},
        "limitation": "historical_replay",
        "denominator": "qualified_price_bar",
        "global": expected,
        "entities": {entity: expected},
    }
    assert _tree_hashes(store) == before


def test_wrong_profile_fails_without_partial_cli_json(tmp_path, monkeypatch, capsys):
    pool = "F" * 32
    _, store, pin = _volume_source(
        tmp_path,
        [dict(pool=pool, bars=_bars([300000, 300300], [1.0, 2.0]))],
        profile="candle-context-v1",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["memecoin_experience", "volume-eligibility", "--store", str(store), "--source", pin],
    )
    with pytest.raises(ValueError, match="observation_profile"):
        cli.main()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("prior_volume_count", 1.0, "volume_eligibility_prior_count"),
        ("context_count", 2.0, "volume_eligibility_context_count"),
    ],
)
def test_counts_must_be_integers(field, value, error):
    names = [
        "context_count", "context_span_s", "context_max_gap_s", "context_contiguous",
        "prior_volume_count", "volume_log1p_delta",
    ]
    indices = {name: names.index(name) for name in names}
    values = [2, 300, 300, 1, 1, None]
    values[indices[field]] = value
    with pytest.raises(ValueError, match=error):
        _validate_row({"values": values}, indices)


def test_zero_delta_is_present_valid_delta():
    names = [
        "context_count", "context_span_s", "context_max_gap_s", "context_contiguous",
        "prior_volume_count", "volume_log1p_delta",
    ]
    indices = {name: names.index(name) for name in names}
    assert _validate_row({"values": [3, 600, 300, 1, 2, 0.0]}, indices) == "eligible"
