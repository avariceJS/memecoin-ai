"""Independent finite candle-context regressions over qualified synthetic captures.

The candle-context assertions deliberately fail against the pre-context adapter
at observation_profile. The legacy and morphology controls remain executable on
that baseline; no capture or export step is mocked.
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

PROFILE = "candle-context-v1"
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
CONTEXT = [
    ("prior_close_log_return", "log_ratio"),
    ("recent_close_position", "fraction_of_observed_range"),
    ("context_count", "observed_bar_count"),
    ("context_span_s", "seconds"),
    ("context_max_gap_s", "seconds"),
    ("context_contiguous", "indicator"),
    ("context_ready", "indicator"),
    ("context_zero_range", "indicator"),
]


def _qualified_source(tmp_path, captures, *, intervals=None, end_s=318000):
    """Build a sealed synthetic capture inventory, index, and ordinary export."""
    pools = {}
    index = tmp_path / "index.sqlite"
    for number, item in enumerate(captures):
        parent = tmp_path / f"capture-parent-{number}"
        parent.mkdir(parents=True)
        cap, birth = capture(parent)
        pool = item["pool"]
        response_path = cap / "response.bin"
        response = json.loads(response_path.read_text())
        response["meta"]["base"]["address"] = birth["base_mint"]
        response["meta"]["quote"]["address"] = birth["quote_mint"]
        bars = item["bars"]
        response["data"]["attributes"]["ohlcv_list"] = [list(bar) for bar in bars]
        response_path.write_text(json.dumps(response))

        request_path = cap / "request.json"
        request = json.loads(request_path.read_text())
        request["url"] = request["url"].replace(birth["pool"], pool)
        available_ns = item.get("available_ns", 10**18)
        request["started_ns"] = available_ns - 2
        request_path.write_text(json.dumps(request))
        receipt_path = cap / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["received_ns"] = available_ns - 1
        receipt["available_ns"] = available_ns
        receipt["response_sha256"] = file_hash(response_path)
        receipt_path.write_text(json.dumps(receipt))
        (cap / "manifest.json").unlink()
        seal_files(cap)

        enrollment = dict(
            pool=pool,
            base_mint=birth["base_mint"],
            quote_mint=birth["quote_mint"],
        )
        pools[pool] = enrollment
        with connect(index) as db:
            ingest(db, cap, pool)

    plan = dict(
        role="train",
        cutoff_ns=10**18,
        start_s=300000,
        end_s=end_s,
        selection="synthetic finite candle context regression",
        pools=[pools[p] for p in sorted(pools)],
    )
    if intervals is not None:
        plan["intervals"] = intervals
    report = export(index, tmp_path / "continuous", plan)
    return dict(kind="continuous", path=str(tmp_path / "continuous"), pin=report["pin"])


def _events(store, pin):
    return {
        (row["entity"], int(row["id"])): row
        for row in Source(store, pin, role="train").rows()
        if row["kind"] == "event"
    }


def _feature_defs(schema):
    return [(item["name"], item["unit"]) for item in schema["streams"]["qualified_price_bar"]["features"]]


def _bars(opens, *, close0=10.0):
    """Valid finite OHLCV where each close varies to expose return selection."""
    return [(t, close0 + i, close0 + i + 2, close0 + i - 2, close0 + i + 1, 100.0 + i) for i, t in enumerate(opens)]


def _context(row):
    return row["values"][9:]


def test_legacy_and_morphology_controls_remain_unchanged(tmp_path):
    pool = "A" * 32
    ref = _qualified_source(tmp_path, [dict(pool=pool, bars=_bars([300000, 300300]))])
    legacy_pin = from_continuous(ref, tmp_path / "legacy")
    legacy = Source(tmp_path / "legacy", legacy_pin, role="train")
    assert _feature_defs(legacy.manifest["binding"]["contract"]) == LEGACY
    legacy_events = _events(tmp_path / "legacy", legacy_pin)
    assert len(legacy_events) == 2
    assert legacy_events[("solana/pool/" + pool, 300000)]["values"] == pytest.approx(
        [11.0, math.log(12 / 8), math.log(11 / 10), math.log1p(100.0)]
    )

    morphology_pin = from_continuous(
        ref, tmp_path / "morphology", profile="candle-morphology-v1"
    )
    morphology = Source(tmp_path / "morphology", morphology_pin, role="train")
    assert _feature_defs(morphology.manifest["binding"]["contract"]) == LEGACY + MORPHOLOGY
    assert _events(tmp_path / "morphology", morphology_pin)[("solana/pool/" + pool, 300000)]["values"][4:] == pytest.approx(
        [0.25, 0.5, 0.25, 0.75, 0.0]
    )


def test_context_feature_contract_formulas_and_profile_identity(tmp_path):
    pool = "A" * 32
    ref = _qualified_source(tmp_path, [dict(pool=pool, bars=_bars([300000, 300300]))])
    pin = from_continuous(ref, tmp_path / "context", profile=PROFILE)
    source = Source(tmp_path / "context", pin, role="train")
    features = source.manifest["binding"]["contract"]["streams"]["qualified_price_bar"]["features"]
    assert source.manifest["binding"]["contract"]["availability"] == "historical_replay"
    assert _feature_defs(source.manifest["binding"]["contract"]) == LEGACY + MORPHOLOGY + CONTEXT
    assert len(features) == 17
    assert [item["modality"] for item in features] == ["ohlcv"] * 17
    rows = _events(tmp_path / "context", pin)
    first = _context(rows[("solana/pool/" + pool, 300000)])
    second = _context(rows[("solana/pool/" + pool, 300300)])
    assert first == [None, None, 1, 0, None, None, 0, None]
    assert second == pytest.approx([math.log(12 / 11), 0.8, 2, 300, 300, 1, 0, 0])

    descriptor = json.dumps(source.manifest["binding"]["sources"], sort_keys=True)
    for term in (
        PROFILE,
        "prior_close_log_return",
        "recent_close_position",
        "max_count",
        "max_span_s",
        "3600",
        "300",
        "context_ready",
        "implementation",
        "numerics",
    ):
        assert term in descriptor
    profile = next(item["profile"] for item in source.manifest["binding"]["sources"]
                   if item.get("profile", {}).get("name") == PROFILE)
    assert profile["version"] == 1
    assert profile["parameters"] == dict(
        bar_seconds=300, max_count=12, max_span_s=3600, min_count=2, ready_count=12
    )
    assert [(item["name"], item["unit"]) for item in profile["features"]] == LEGACY + MORPHOLOGY + CONTEXT
    assert profile["identity"]["source_pin"] == ref["pin"]
    assert profile["identity"]["cutoff_ns"] == 10**18
    assert profile["revision_selection"] == "geckoterminal/usd/base/5min/first-qualified-receipt"


def test_gaps_short_history_and_exact_prior_return_semantics(tmp_path):
    pool = "A" * 32
    opens = [300000, 300300, 300900, 301200]
    ref = _qualified_source(tmp_path, [dict(pool=pool, bars=_bars(opens))])
    pin = from_continuous(ref, tmp_path / "context", profile=PROFILE)
    rows = _events(tmp_path / "context", pin)
    entity = "solana/pool/" + pool
    assert _context(rows[(entity, 300000)]) == [None, None, 1, 0, None, None, 0, None]
    assert _context(rows[(entity, 300300)]) == pytest.approx(
        [math.log(12 / 11), 0.8, 2, 300, 300, 1, 0, 0]
    )
    # At 300900 an older observation remains in context, but the immediate
    # previous 300-second close is missing so no return is substituted.
    at_gap = _context(rows[(entity, 300900)])
    assert at_gap[0] is None
    assert at_gap[1:] == pytest.approx([5 / 6, 3, 900, 600, 0, 0, 0])
    after_gap = _context(rows[(entity, 301200)])
    assert after_gap[0] == pytest.approx(math.log(14 / 13))
    assert after_gap[1:] == pytest.approx([6 / 7, 4, 1200, 600, 0, 0, 0])


def test_count_and_inclusive_time_bounds_evict_only_outside_finite_window(tmp_path):
    pool = "A" * 32
    # Fifteen contiguous bars force count eviction. A later bar is exactly
    # 3600 seconds after the last one, then advances another 300 seconds.
    opens = [300000 + i * 300 for i in range(15)] + [307800, 308100]
    ref = _qualified_source(tmp_path, [dict(pool=pool, bars=_bars(opens))], end_s=308400)
    pin = from_continuous(ref, tmp_path / "context", profile=PROFILE)
    rows = _events(tmp_path / "context", pin)
    entity = "solana/pool/" + pool
    at_count_limit = _context(rows[(entity, 304200)])
    assert at_count_limit[2:8] == pytest.approx([12, 3300, 300, 1, 1, 0])
    at_inclusive_span = _context(rows[(entity, 307800)])
    assert at_inclusive_span[2:8] == pytest.approx([2, 3600, 3600, 0, 0, 0])
    after_inclusive_span = _context(rows[(entity, 308100)])
    assert after_inclusive_span[2:8] == pytest.approx([2, 300, 300, 1, 0, 0])
    assert all(math.isfinite(value) for value in after_inclusive_span if value is not None)


def test_pool_and_original_touching_enrollment_intervals_reset_context(tmp_path):
    left, right = "A" * 32, "D" * 32
    opens = [300000, 300300, 300600, 300900]
    captures = [
        dict(pool=left, bars=_bars(opens)),
        dict(pool=right, bars=_bars(opens, close0=20.0)),
    ]
    intervals = {left: [[300000, 300600], [300600, 301200]], right: [[300000, 301200]]}
    ref = _qualified_source(tmp_path, captures, intervals=intervals, end_s=301200)
    pin = from_continuous(ref, tmp_path / "context", profile=PROFILE)
    rows = _events(tmp_path / "context", pin)
    left_entity, right_entity = "solana/pool/" + left, "solana/pool/" + right
    assert _context(rows[(left_entity, 300300)])[2] == 2
    # 300600 is the first selected open in the touching second interval.
    assert _context(rows[(left_entity, 300600)]) == [None, None, 1, 0, None, None, 0, None]
    # A second enrolled pool has its own independent history.
    assert _context(rows[(right_entity, 300600)])[2] == 3


@pytest.mark.parametrize(
    "bars,expected_position",
    [
        (
            [
                (300000, math.ulp(0.0), 3 * math.ulp(0.0), math.ulp(0.0), 2 * math.ulp(0.0), 1.0),
                (300300, 2 * math.ulp(0.0), 4 * math.ulp(0.0), math.ulp(0.0), 3 * math.ulp(0.0), 1.0),
            ],
            2 / 3,
        ),
        (
            [
                (300000, 8e307, 1.2e308, 1e-300, 9e307, 1.0),
                (300300, 9e307, 1.7e308, 1e-300, 1.6e308, 1.0),
            ],
            1.6e308 / 1.7e308,
        ),
    ],
    ids=["subnormal-range", "large-finite-range"],
)
def test_context_arithmetic_stays_finite_at_subnormal_and_large_scales(
    tmp_path, bars, expected_position
):
    pool = "A" * 32
    ref = _qualified_source(tmp_path, [dict(pool=pool, bars=bars)])
    pin = from_continuous(ref, tmp_path / "context", profile=PROFILE)
    row = _events(tmp_path / "context", pin)[("solana/pool/" + pool, 300300)]
    values = _context(row)
    assert values[1] == pytest.approx(expected_position)
    assert all(math.isfinite(value) for value in row["values"] if value is not None)


def test_observed_zero_range_is_distinct_from_singleton_missing_range(tmp_path):
    pool = "A" * 32
    bars = [(300000, 5.0, 5.0, 5.0, 5.0, 1.0), (300300, 5.0, 5.0, 5.0, 5.0, 1.0)]
    ref = _qualified_source(tmp_path, [dict(pool=pool, bars=bars)])
    pin = from_continuous(ref, tmp_path / "context", profile=PROFILE)
    rows = _events(tmp_path / "context", pin)
    entity = "solana/pool/" + pool
    assert _context(rows[(entity, 300000)])[1] is None
    observed = _context(rows[(entity, 300300)])
    assert observed[1] is None
    assert observed[2] == 2
    assert observed[7] == 1


def test_contributor_evidence_is_unique_and_invalid_capture_is_quarantined(tmp_path):
    pool = "A" * 32
    first = _bars([300000, 300300])
    last = _bars([300600], close0=12.0)
    good = _qualified_source(
        tmp_path / "good",
        [
            dict(pool=pool, bars=first, available_ns=10**18 - 1),
            dict(pool=pool, bars=last, available_ns=10**18 - 100),
        ],
    )
    pin = from_continuous(good, tmp_path / "good-observations", profile=PROFILE)
    event = _events(tmp_path / "good-observations", pin)[("solana/pool/" + pool, 300600)]
    inventory = [json.loads(line) for line in (tmp_path / "good" / "continuous" / "meta/captures.jsonl").read_text().splitlines()]
    assert event["evidence"] == sorted({item["pin"] for item in inventory})
    assert event["evidence"] == list(dict.fromkeys(event["evidence"]))
    assert event["available_ns"] == 10**18 - 1
    assert event["event_ns"] == 300900 * 10**9
    repeated_pin = from_continuous(
        good, tmp_path / "good-observations-repeated", profile=PROFILE
    )
    assert repeated_pin == pin
    assert _events(tmp_path / "good-observations-repeated", repeated_pin) == _events(
        tmp_path / "good-observations", pin
    )

    invalid = list(first + last)
    invalid[0] = (invalid[0][0], 20.0, 19.0, 21.0, 20.0, 100.0)
    bad = _qualified_source(tmp_path / "bad", [dict(pool=pool, bars=invalid)])
    bad_pin = from_continuous(bad, tmp_path / "bad-observations", profile=PROFILE)
    assert _events(tmp_path / "bad-observations", bad_pin) == {}
