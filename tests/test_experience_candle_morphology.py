"""Independent morphology regression candidate over qualified synthetic captures.

On the pre-morphology baseline the legacy control passes. The profile, descriptor,
and added-feature failures below are deliberate until the sibling adapter change
is assembled; they document the contract against the real continuous adapter.
"""

import json
import math

import pytest
from curriculum.test_admission import capture

from memecoin_continuous.store import connect, ingest
from memecoin_experience.continuous import export
from memecoin_experience.observation_adapters import from_continuous
from memecoin_experience.observations import Source
from memecoin_experience.storage import file_hash, seal_files


LEGACY = [
    ("close_usd", "USD"),
    ("log_high_low", "log_ratio"),
    ("log_close_open", "log_ratio"),
    ("log_volume_usd", "log1p_USD"),
]
MORPHOLOGY = [
    ("upper_wick_fraction", "fraction_of_range"),
    ("lower_wick_fraction", "fraction_of_range"),
    ("signed_body_fraction", "signed_fraction_of_range"),
    ("close_position", "fraction_of_range"),
    ("zero_range", "indicator"),
]


def _qualified_source(tmp_path, bars, report_out=None):
    """Build an ordinary sealed capture/index/export using only synthetic OHLCV."""
    cap, birth = capture(tmp_path)
    response = json.loads((cap / "response.bin").read_text())
    template = response["data"]["attributes"]["ohlcv_list"]
    assert len(bars) <= len(template)
    response["data"]["attributes"]["ohlcv_list"] = [
        [template[i][0], *bar, template[i][5]] for i, bar in enumerate(bars)
    ]
    (cap / "response.bin").write_text(json.dumps(response))
    receipt = json.loads((cap / "receipt.json").read_text())
    receipt["response_sha256"] = file_hash(cap / "response.bin")
    (cap / "receipt.json").write_text(json.dumps(receipt))
    (cap / "manifest.json").unlink()
    seal_files(cap)

    index = tmp_path / "index.sqlite"
    with connect(index) as db:
        ingest(db, cap, birth["pool"])
    plan = dict(
        role="train",
        cutoff_ns=10**18,
        start_s=300000,
        end_s=318000,
        selection="synthetic candle morphology regression",
        pools=[{k: birth[k] for k in ("pool", "base_mint", "quote_mint")}],
    )
    report = export(index, tmp_path / "continuous", plan)
    if report_out is not None:
        report_out.append(report)
    return dict(kind="continuous", path=str(tmp_path / "continuous"), pin=report["pin"])


def _events(store, pin):
    return {
        int(row["id"]): row
        for row in Source(store, pin, role="train").rows()
        if row["kind"] == "event"
    }


def _feature_defs(schema):
    return [(item["name"], item["unit"]) for item in schema["streams"]["qualified_price_bar"]["features"]]


def _profile_values(row):
    # Existing four legacy columns precede the frozen five morphology columns.
    return row["values"][4:]


def test_legacy_default_keeps_the_four_existing_features_and_values(tmp_path):
    ref = _qualified_source(tmp_path, [(2.0, 8.0, 1.0, 5.0)] * 2)
    pin = from_continuous(ref, tmp_path / "default")
    source = Source(tmp_path / "default", pin, role="train")
    schema = source.manifest["binding"]["contract"]
    assert schema["version"] == "market-observations-v1"
    assert _feature_defs(schema) == LEGACY
    events = _events(tmp_path / "default", pin)
    assert len(events) == 2
    assert events[300000]["values"] == pytest.approx(
        [5.0, math.log(8.0), math.log(2.5), math.log1p(100.0)]
    )


def test_explicit_legacy_profile_matches_omitted_profile(tmp_path):
    ref = _qualified_source(tmp_path, [(2.0, 8.0, 1.0, 5.0)] * 2)
    omitted_pin = from_continuous(ref, tmp_path / "omitted")
    explicit_pin = from_continuous(ref, tmp_path / "explicit", profile="legacy-v1")
    assert omitted_pin == explicit_pin
    assert list(Source(tmp_path / "omitted", omitted_pin, role="train").rows()) == list(
        Source(tmp_path / "explicit", explicit_pin, role="train").rows()
    )


@pytest.mark.parametrize("profile", ["unknown-v9", "", None, ["candle-morphology-v1"]])
def test_unknown_or_malformed_profile_is_rejected(tmp_path, profile):
    ref = _qualified_source(tmp_path, [(2.0, 8.0, 1.0, 5.0)])
    with pytest.raises(ValueError, match="observation_profile"):
        from_continuous(ref, tmp_path / "rejected", profile=profile)


def test_opt_in_feature_order_units_modality_and_persisted_profile_identity(tmp_path):
    ref = _qualified_source(tmp_path, [(2.0, 8.0, 1.0, 5.0)])
    pin = from_continuous(ref, tmp_path / "opt-in", profile="candle-morphology-v1")
    source = Source(tmp_path / "opt-in", pin, role="train")
    schema = source.manifest["binding"]["contract"]
    features = schema["streams"]["qualified_price_bar"]["features"]
    assert _feature_defs(schema) == LEGACY + MORPHOLOGY
    assert [item["modality"] for item in features] == ["ohlcv"] * 9

    # Treat the persisted source as a semantic descriptor; do not constrain its
    # container shape. Its serialized meaning must bind identity and definitions.
    descriptor = source.manifest["binding"]["sources"]
    semantic_text = json.dumps(descriptor, sort_keys=True)
    for term in (
        "candle-morphology-v1",
        "upper_wick_fraction",
        "lower_wick_fraction",
        "signed_body_fraction",
        "close_position",
        "zero_range",
        "fraction_of_range",
        "signed_fraction_of_range",
        "indicator",
    ):
        assert term in semantic_text


def test_up_down_doji_and_wick_categories_obey_frozen_identities(tmp_path):
    # up, down, doji, upper-only, lower-only, both-wick, no-wick
    bars = [
        (2.0, 8.0, 1.0, 5.0),
        (6.0, 8.0, 2.0, 4.0),
        (4.0, 8.0, 1.0, 4.0),
        (2.0, 8.0, 2.0, 2.0),
        (4.0, 4.0, 1.0, 4.0),
        (4.0, 8.0, 1.0, 4.0),
        (2.0, 6.0, 2.0, 6.0),
    ]
    ref = _qualified_source(tmp_path, bars)
    pin = from_continuous(ref, tmp_path / "observations", profile="candle-morphology-v1")
    events = _events(tmp_path / "observations", pin)
    expected = [
        [3 / 7, 1 / 7, 3 / 7, 4 / 7, 0],
        [2 / 6, 2 / 6, -2 / 6, 2 / 6, 0],
        [4 / 7, 3 / 7, 0, 3 / 7, 0],
        [1, 0, 0, 0, 0],
        [0, 1, 0, 1, 0],
        [4 / 7, 3 / 7, 0, 3 / 7, 0],
        [0, 0, 1, 1, 0],
    ]
    assert len(events) == len(expected)
    for i, target in enumerate(expected):
        got = _profile_values(events[300000 + i * 300])
        assert got == pytest.approx(target)
        upper, lower, body, position, zero = got
        assert 0 <= upper <= 1 and 0 <= lower <= 1 and -1 <= body <= 1
        assert upper + lower + abs(body) == pytest.approx(1)
        assert position == pytest.approx(lower + max(body, 0))
        assert 0 <= position <= 1 and zero == 0


def test_zero_range_is_explicit_and_does_not_invent_close_position(tmp_path):
    ref = _qualified_source(tmp_path, [(5.0, 5.0, 5.0, 5.0)])
    pin = from_continuous(ref, tmp_path / "observations", profile="candle-morphology-v1")
    got = _profile_values(_events(tmp_path / "observations", pin)[300000])
    assert got == [0, 0, 0, None, 1]


@pytest.mark.parametrize(
    "low,high",
    [
        (math.ulp(0.0), 2 * math.ulp(0.0)),
        (math.nextafter(0.0, 1.0), math.nextafter(math.nextafter(0.0, 1.0), 1.0)),
        (float.fromhex("0x1.fffffffffffffp+1022"), float.fromhex("0x1.fffffffffffffp+1023")),
        (math.ulp(0.0), float.fromhex("0x1.fffffffffffffp+1023")),
    ],
    ids=["minimum-subnormal", "two-subnormal-steps", "maximum-finite", "full-finite-range"],
)
def test_finite_positive_extremes_keep_legacy_and_morphology_values_finite(tmp_path, low, high):
    ref = _qualified_source(tmp_path, [(low, high, low, high)])
    pin = from_continuous(ref, tmp_path / "observations", profile="candle-morphology-v1")
    row = _events(tmp_path / "observations", pin)[300000]
    assert all(math.isfinite(value) for value in row["values"] if value is not None)
    assert _profile_values(row) == pytest.approx([0, 0, 1, 1, 0])


def test_invalid_ohlcv_keeps_existing_qualification_quarantine_behavior(tmp_path):
    reports = []
    ref = _qualified_source(
        tmp_path,
        [(2.0, 1.0, 1.0, 1.5)] + [(2.0, 8.0, 1.0, 5.0)] * 2,
        report_out=reports,
    )
    from memecoin_experience.continuous import source

    qualified = list(source(ref, 10**18, role="train"))
    assert reports[0]["counts"]["invalid_OHLCV"] == 1
    # Qualification rejects the malformed capture as a whole, including its
    # otherwise valid bars; the adapter must not recover quarantined values.
    assert qualified == []
    pin = from_continuous(ref, tmp_path / "observations", profile="candle-morphology-v1")
    assert _events(tmp_path / "observations", pin) == {}
