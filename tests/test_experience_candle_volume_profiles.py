"""Strict sparse authentication for the sealed candle volume-context profile.

These tests publish independently constructed synthetic observation sources. They do
not require the sibling continuous adapter implementation to be present.
"""

import hashlib

import pytest

from memecoin_experience import observations
from memecoin_experience.observations import Source
from memecoin_experience.sparse import materialize


STREAM = "qualified_price_bar"
LEGACY = [
    {"name": "close_usd", "unit": "USD", "modality": "ohlcv"},
    {"name": "log_high_low", "unit": "log_ratio", "modality": "ohlcv"},
    {"name": "log_close_open", "unit": "log_ratio", "modality": "ohlcv"},
    {"name": "log_volume_usd", "unit": "log1p_USD", "modality": "ohlcv"},
]
MORPHOLOGY = [
    {"name": "upper_wick_fraction", "unit": "fraction_of_range", "modality": "ohlcv"},
    {"name": "lower_wick_fraction", "unit": "fraction_of_range", "modality": "ohlcv"},
    {"name": "signed_body_fraction", "unit": "signed_fraction_of_range", "modality": "ohlcv"},
    {"name": "close_position", "unit": "fraction_of_range", "modality": "ohlcv"},
    {"name": "zero_range", "unit": "indicator", "modality": "ohlcv"},
]
CONTEXT = [
    {"name": "prior_close_log_return", "unit": "log_ratio", "modality": "ohlcv"},
    {"name": "recent_close_position", "unit": "fraction_of_observed_range", "modality": "ohlcv"},
    {"name": "context_count", "unit": "observed_bar_count", "modality": "ohlcv"},
    {"name": "context_span_s", "unit": "seconds", "modality": "ohlcv"},
    {"name": "context_max_gap_s", "unit": "seconds", "modality": "ohlcv"},
    {"name": "context_contiguous", "unit": "indicator", "modality": "ohlcv"},
    {"name": "context_ready", "unit": "indicator", "modality": "ohlcv"},
    {"name": "context_zero_range", "unit": "indicator", "modality": "ohlcv"},
]
VOLUME = [
    {"name": "prior_volume_count", "unit": "observed_bar_count", "modality": "ohlcv"},
    {"name": "volume_log1p_delta", "unit": "log1p_USD_difference", "modality": "ohlcv"},
]
VOLUME_RULE = (
    "prior_volume_count is retained observed contributor count excluding current. "
    "volume_log1p_delta is log1p(current.volume_usd) minus the arithmetic mean of "
    "log1p(volume_usd) for all strictly preceding retained contributors, defined only "
    "with at least two priors and every consecutive gap through current exactly 300 "
    "seconds; otherwise missing. Zero volume is valid; this log1p USD difference is "
    "not an exact raw volume ratio."
)
CONTEXT_RULE = (
    "At each current open, retain at most 12 observed bars in the same pool and same "
    "original selected half-open enrollment interval with open time in [current-3600, "
    "current]; reset at every original interval boundary, including touching intervals; "
    "evict by time then count; retain gaps without filling. prior_close_log_return is "
    "stable log(current.close / immediately preceding retained close) only when the "
    "preceding open is exactly 300 seconds earlier, otherwise missing. For at least two "
    "bars, range bounds are min(low) and max(high), including current; position is "
    "(current.close-min(low))/(max(high)-min(low)) when range is positive, otherwise "
    "missing with zero_range=1. With fewer than two bars position and zero_range are "
    "missing. count is retained observed count; span is latest minus earliest open, "
    "zero for a singleton; max gap is missing for a singleton and otherwise the largest "
    "consecutive open-time gap. contiguous is missing for a singleton and otherwise "
    "one iff every gap is 300 seconds. ready is one iff count is 12 and contiguous. "
    "Event availability is the maximum receipt availability of every retained "
    "contributor; evidence is the deterministic unique capture hashes of all contributors."
)
BASE_PROCESS = "qualified_provider_bar_closes; missing bars do not certify inactivity"
IMPLEMENTATION = hashlib.sha256(b"synthetic adapter implementation").hexdigest()
NUMERICS = hashlib.sha256(b"synthetic numerics").hexdigest()
SOURCE_PIN = hashlib.sha256(b"synthetic continuous source").hexdigest()
CUTOFF = 10**18


def _profile():
    formulas = {
        "prior_close_log_return": (
            "log(current.close / immediately_preceding_retained.close) only "
            "when immediately_preceding_retained.open == current.open-300s"
        ),
        "recent_close_position": (
            "(current.close-min(contributor.low))/(max(contributor.high)-"
            "min(contributor.low)) when range positive"
        ),
        "context_count": "retained observed contributor count including current",
        "context_span_s": "latest contributor open minus earliest contributor open",
        "context_max_gap_s": "maximum consecutive contributor open-time difference; missing for singleton",
        "context_contiguous": "one iff count>=2 and all consecutive gaps equal 300s; missing for singleton",
        "context_ready": "one iff count=12 and context_contiguous=1, otherwise zero",
        "context_zero_range": "one iff count>=2 and max(high)==min(low); missing below count two",
        "prior_volume_count": "retained observed contributor count excluding current",
        "volume_log1p_delta": (
            "log1p(current.volume_usd) - mean(log1p(prior.volume_usd)); "
            "missing unless prior_volume_count>=2 and all consecutive gaps through current equal 300s"
        ),
    }
    return {
        "name": "candle-volume-context-v1",
        "version": 1,
        "features": LEGACY + MORPHOLOGY + CONTEXT + VOLUME,
        "parameters": {
            "bar_seconds": 300,
            "max_count": 12,
            "max_span_s": 3600,
            "min_count": 2,
            "ready_count": 12,
            "min_prior_volume_count": 2,
        },
        "revision_selection": "geckoterminal/usd/base/5min/first-qualified-receipt",
        "formulas": formulas,
        "morphology_rules": (
            "For R=high-low>0: upper_wick_fraction=(high-max(open,close))/R; "
            "lower_wick_fraction=(min(open,close)-low)/R; "
            "signed_body_fraction=(close-open)/R; close_position=(close-low)/R; "
            "zero_range=0. For R=0: upper_wick_fraction=0, lower_wick_fraction=0, "
            "signed_body_fraction=0, close_position=None, zero_range=1."
        ),
        "rules": CONTEXT_RULE + " " + VOLUME_RULE,
        "identity": {
            "source_pin": SOURCE_PIN,
            "cutoff_ns": CUTOFF,
            "implementation_sha256": IMPLEMENTATION,
            "numerics_sha256": NUMERICS,
        },
    }


def _schema():
    return {
        "version": "market-observations-v1",
        "role": "train",
        "availability": "historical_replay",
        "streams": {
            STREAM: {
                "process": (
                    BASE_PROCESS
                    + "; candle-morphology-v1: " + _profile()["morphology_rules"]
                    + "; candle-context-v1: " + CONTEXT_RULE
                    + "; candle-volume-context-v1: " + VOLUME_RULE
                ),
                "features": LEGACY + MORPHOLOGY + CONTEXT + VOLUME,
            }
        },
    }


def _plan():
    return {
        "stream": STREAM,
        "context": 1,
        "max_history_ns": 10**12,
        "targets": [
            {"name": "count", "kind": "observed_process_count", "horizon_ns": 300, "feature": None}
        ],
        "lifecycle": [],
        "lifecycle_origins": False,
        "relative_features": [],
    }


def _sealed_source(tmp_path, mutate=None):
    schema = _schema()
    profile = _profile()
    producer = {
        "producer": "continuous-observation-adapter-v4",
        "implementation": IMPLEMENTATION,
        "numerics": NUMERICS,
        "profile": profile,
    }
    if mutate:
        mutate(schema, producer)
    sources = [
        {"kind": "continuous", "path": "synthetic-continuous", "pin": SOURCE_PIN},
        producer,
    ]
    evidence = [hashlib.sha256(b"synthetic capture").hexdigest()]
    values = [1.0] * 19
    # A valid singleton context has no priors or volume delta and uses integer
    # context counts; unrelated feature placeholders remain synthetic.
    values[11:15] = [1, 0, None, None]
    values[17:19] = [0, None]
    rows = [
        {"kind": "enrollment", "entity": "chain/market/pool-1", "start_ns": 0, "end_ns": 600_000_000_000,
         "available_ns": CUTOFF, "evidence": evidence},
        {"kind": "event", "entity": "chain/market/pool-1", "stream": STREAM, "id": "bar-300",
         "event_ns": 300_000_000_000, "available_ns": CUTOFF, "evidence": evidence,
         "values": values},
    ]
    pin = observations.publish(tmp_path / "observations", rows, schema=schema, cutoff=CUTOFF, sources=sources)
    return pin


def test_v4_profile_authenticates_exact_shape_and_identity(tmp_path):
    pin = _sealed_source(tmp_path)
    Source(tmp_path / "observations", pin, role="train")
    out_pin = materialize(tmp_path / "observations", pin, tmp_path / "sparse", plan=_plan(), profile="candle-volume-context-v1")
    assert len(out_pin["snapshot"]) == 64
    assert out_pin["binding"]["selection"]["observation_profile"]["name"] == "candle-volume-context-v1"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda s, p: p["profile"].update(name="candle-context-v1"), id="profile-name"),
        pytest.param(lambda s, p: p["profile"]["features"].reverse(), id="feature-order"),
        pytest.param(lambda s, p: p["profile"]["features"][-1].update(unit="log_ratio"), id="feature-unit"),
        pytest.param(lambda s, p: p["profile"]["parameters"].update(min_prior_volume_count=1), id="parameters"),
        pytest.param(lambda s, p: p["profile"]["formulas"].update(volume_log1p_delta="raw ratio"), id="formula"),
        pytest.param(lambda s, p: p["profile"].update(rules="allow missing history"), id="rules"),
        pytest.param(lambda s, p: p["profile"]["identity"].update(source_pin="f" * 64), id="identity"),
        pytest.param(lambda s, p: p.update(producer="continuous-observation-adapter-v3"), id="producer"),
        pytest.param(lambda s, p: s["streams"][STREAM]["features"].reverse(), id="schema-feature-order"),
        pytest.param(lambda s, p: s["streams"][STREAM].update(process="different process"), id="schema-process"),
    ],
)
def test_authenticated_contract_mismatches_fail_before_output_creation(tmp_path, mutate):
    pin = _sealed_source(tmp_path, mutate)
    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="observation_profile"):
        materialize(tmp_path / "observations", pin, output, plan=_plan(), profile="candle-volume-context-v1")
    assert not output.exists()


def test_wrong_requested_profile_is_rejected_before_output_creation(tmp_path):
    pin = _sealed_source(tmp_path)
    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="observation_profile"):
        materialize(tmp_path / "observations", pin, output, plan=_plan(), profile="candle-context-v1")
    assert not output.exists()
