"""Independent prior-volume boundary regressions over sealed synthetic captures.

This candidate deliberately exposes ``observation_profile`` failures on the
pre-volume adapter. Legacy and context17 controls still exercise the ordinary
capture, index, export, and observation paths on that baseline.
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

PROFILE = "candle-volume-context-v1"
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
VOLUME = [
    ("prior_volume_count", "observed_bar_count"),
    ("volume_log1p_delta", "log1p_USD_difference"),
]
NS = 10**9


def _qualified_source(tmp_path, captures, *, intervals=None, end_s=318000, cutoff_ns=10**18):
    """Build a sealed synthetic capture inventory, index, and ordinary export."""
    pools = {}
    identities = {}
    index = tmp_path / "index.sqlite"
    for number, item in enumerate(captures):
        parent = tmp_path / f"capture-parent-{number}"
        parent.mkdir(parents=True)
        cap, birth = capture(parent)
        pool = item["pool"]
        identity = identities.setdefault(pool, birth)
        response_path = cap / "response.bin"
        response = json.loads(response_path.read_text())
        response["meta"]["base"]["address"] = identity["base_mint"]
        response["meta"]["quote"]["address"] = identity["quote_mint"]
        response["data"]["attributes"]["ohlcv_list"] = [list(bar) for bar in item["bars"]]
        response_path.write_text(json.dumps(response))

        request_path = cap / "request.json"
        request = json.loads(request_path.read_text())
        request["url"] = request["url"].replace(birth["pool"], pool)
        available_ns = item.get("available_ns", cutoff_ns - 1)
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

        pools[pool] = dict(
            pool=pool,
            base_mint=identity["base_mint"],
            quote_mint=identity["quote_mint"],
        )
        with connect(index) as db:
            ingest(db, cap, pool)

    plan = dict(
        role="train",
        cutoff_ns=cutoff_ns,
        start_s=300000,
        end_s=end_s,
        selection="synthetic finite prior-volume boundary regression",
        pools=[pools[p] for p in sorted(pools)],
    )
    if intervals is not None:
        plan["intervals"] = intervals
    report = export(index, tmp_path / "continuous", plan)
    return dict(kind="continuous", path=str(tmp_path / "continuous"), pin=report["pin"])


def _bars(opens, volumes, *, close0=10.0):
    assert len(opens) == len(volumes)
    return [
        (t, close0 + i, close0 + i + 2, close0 + i - 2, close0 + i + 1, volume)
        for i, (t, volume) in enumerate(zip(opens, volumes, strict=True))
    ]


def _events(store, pin):
    return {
        (row["entity"], int(row["id"])): row
        for row in Source(store, pin, role="train").rows()
        if row["kind"] == "event"
    }


def _feature_defs(schema):
    return [
        (item["name"], item["unit"])
        for item in schema["streams"]["qualified_price_bar"]["features"]
    ]


def _volume(row):
    return row["values"][17:19]


def _volume_source(tmp_path, captures, *, profile=PROFILE, **kwargs):
    reference = _qualified_source(tmp_path / "source", captures, **kwargs)
    store = tmp_path / "observations"
    pin = from_continuous(reference, store, role="train", profile=profile)
    return reference, store, pin


def test_legacy_and_context17_controls_keep_their_existing_contract(tmp_path):
    pool = "A" * 32
    bars = _bars([300000, 300300, 300600], [10.0, 100.0, 100.0])
    ref = _qualified_source(tmp_path, [dict(pool=pool, bars=bars)])
    legacy_pin = from_continuous(ref, tmp_path / "legacy")
    legacy = Source(tmp_path / "legacy", legacy_pin, role="train")
    assert _feature_defs(legacy.manifest["binding"]["contract"]) == LEGACY
    legacy_event = _events(tmp_path / "legacy", legacy_pin)[("solana/pool/" + pool, 300000)]
    assert legacy_event["values"] == pytest.approx(
        [11.0, math.log(12 / 8), math.log(11 / 10), math.log1p(10.0)]
    )

    context_pin = from_continuous(ref, tmp_path / "context17", profile="candle-context-v1")
    context = Source(tmp_path / "context17", context_pin, role="train")
    assert _feature_defs(context.manifest["binding"]["contract"]) == LEGACY + MORPHOLOGY + CONTEXT
    assert (
        len(
            context.manifest["binding"]["contract"]["streams"]["qualified_price_bar"]["features"]
        )
        == 17
    )


def test_denominator_excludes_current_and_distinguishes_prior_volume_histories(tmp_path):
    pool_a, pool_b = "A" * 32, "B" * 32
    opens = [300000, 300300, 300600]

    def same_prices(pool, vols):
        return dict(pool=pool, bars=_bars(opens, vols))

    ref, store, pin = _volume_source(
        tmp_path,
        [same_prices(pool_a, [10.0, 10.0, 100.0]), same_prices(pool_b, [100.0, 100.0, 100.0])],
    )
    source = Source(store, pin, role="train")
    contract = source.manifest["binding"]["contract"]
    assert _feature_defs(contract) == LEGACY + MORPHOLOGY + CONTEXT + VOLUME
    assert len(contract["streams"]["qualified_price_bar"]["features"]) == 19
    events = _events(store, pin)
    entity_a, entity_b = "solana/pool/" + pool_a, "solana/pool/" + pool_b
    for entity in (entity_a, entity_b):
        assert _volume(events[(entity, 300000)]) == [0, None]
        assert _volume(events[(entity, 300300)]) == [1, None]
        assert _volume(events[(entity, 300600)])[0] == 2
    delta_a = _volume(events[(entity_a, 300600)])[1]
    delta_b = _volume(events[(entity_b, 300600)])[1]
    assert delta_a == pytest.approx(math.log1p(100.0) - math.log1p(10.0))
    assert delta_b == pytest.approx(0.0)
    assert delta_a != delta_b
    assert ref["pin"] in json.dumps(source.manifest["binding"]["sources"], sort_keys=True)


def test_singleton_one_prior_gap_and_zero_volume_semantics(tmp_path):
    pool = "C" * 32
    opens = [300000, 300300, 300600, 301200]
    ref, store, pin = _volume_source(
        tmp_path,
        [dict(pool=pool, bars=_bars(opens, [0.0, 8.0, 0.0, 0.0]))],
    )
    rows = _events(store, pin)
    entity = "solana/pool/" + pool
    assert _volume(rows[(entity, 300000)]) == [0, None]
    assert _volume(rows[(entity, 300300)]) == [1, None]
    adjacent_zero = _volume(rows[(entity, 300600)])
    assert adjacent_zero[0] == 2
    assert adjacent_zero[1] == pytest.approx(
        math.log1p(0.0) - math.fsum(map(math.log1p, [0.0, 8.0])) / 2
    )  # Zero volume is observed and retained in the prior mean.
    at_gap = _volume(rows[(entity, 301200)])
    assert at_gap[0] == 3
    assert at_gap[1] is None  # Every consecutive gap through current must be 300 s.
    assert ref["pin"]


def test_extreme_finite_volumes_produce_finite_stable_log_difference(tmp_path):
    pool = "D" * 32
    huge = 1.7e308
    bars = _bars([300000, 300300, 300600], [huge, huge, huge])
    _, store, pin = _volume_source(tmp_path, [dict(pool=pool, bars=bars)])
    event = _events(store, pin)[("solana/pool/" + pool, 300600)]
    assert _volume(event)[0] == 2
    assert _volume(event)[1] == pytest.approx(0.0)
    assert math.isfinite(_volume(event)[1])


def test_all_zero_prior_baseline_has_zero_delta(tmp_path):
    pool = "I" * 32
    bars = _bars([300000, 300300, 300600], [0.0, 0.0, 0.0])
    _, store, pin = _volume_source(tmp_path, [dict(pool=pool, bars=bars)])
    event = _events(store, pin)[("solana/pool/" + pool, 300600)]
    assert _volume(event) == pytest.approx([2, 0.0])


def test_count_and_inclusive_span_bounds_limit_prior_contributors(tmp_path):
    pool = "E" * 32
    # Thirteen bars force count eviction; at 303600, twelve retained include
    # current and exactly eleven prior bars. At 307200, only the 303600 bar
    # lies on the inclusive 3600-second boundary; at 307500 it has expired.
    opens = [300000 + i * 300 for i in range(13)] + [307200, 307500]
    volumes = [float(i + 1) for i in range(len(opens))]
    _, store, pin = _volume_source(
        tmp_path,
        [dict(pool=pool, bars=_bars(opens, volumes))],
        end_s=307800,
    )
    rows = _events(store, pin)
    entity = "solana/pool/" + pool
    at_count_limit = _volume(rows[(entity, 303600)])
    assert at_count_limit[0] == 11
    assert at_count_limit[1] == pytest.approx(
        math.log1p(13.0) - math.fsum(math.log1p(float(v)) for v in range(2, 13)) / 11
    )
    at_exact_span = _volume(rows[(entity, 307200)])
    # The 303600 prior open is exactly 3600 seconds earlier and remains
    # included; 303300 and older are outside the inclusive time window. This
    # leaves one prior, so the delta remains missing.
    assert at_exact_span == [1, None]
    beyond_span = _volume(rows[(entity, 307500)])
    # At 307500 the 303600 bar is now 3900 seconds old and is evicted; only
    # the immediately preceding 307200 bar remains.
    assert beyond_span[0] == 1
    assert beyond_span[1] is None


def test_other_pool_and_touching_original_intervals_have_independent_history(tmp_path):
    left, right = "F" * 32, "G" * 32
    opens = [300000, 300300, 300600, 300900]
    captures = [
        dict(pool=left, bars=_bars(opens, [10.0, 10.0, 10.0, 10.0])),
        dict(pool=right, bars=_bars(opens, [100.0, 100.0, 100.0, 100.0], close0=20.0)),
    ]
    intervals = {left: [[300000, 300600], [300600, 301200]], right: [[300000, 301200]]}
    _, store, pin = _volume_source(tmp_path, captures, intervals=intervals, end_s=301200)
    rows = _events(store, pin)
    left_entity, right_entity = "solana/pool/" + left, "solana/pool/" + right
    assert _volume(rows[(left_entity, 300300)]) == [1, None]
    assert _volume(rows[(left_entity, 300600)]) == [0, None]
    assert _volume(rows[(left_entity, 300900)]) == [1, None]
    right_current = _volume(rows[(right_entity, 300600)])
    assert right_current[0] == 2
    assert right_current[1] == pytest.approx(0.0)


def test_late_capture_cutoff_and_first_qualified_revision_control_contributors(tmp_path):
    """A too-late capture cannot contribute; first qualified receipt owns a bar."""
    pool = "H" * 32
    cutoff = 10**18
    early = _bars([300000, 300300], [10.0, 10.0])
    late = _bars([300600], [100.0])
    later_revision = _bars([300300], [1000.0])
    beyond_cutoff = _bars([300900], [5000.0])
    ref = _qualified_source(
        tmp_path / "source",
        [
            dict(pool=pool, bars=early, available_ns=cutoff - 10),
            dict(pool=pool, bars=late, available_ns=cutoff - 5),
            dict(pool=pool, bars=later_revision, available_ns=cutoff - 1),
            dict(pool=pool, bars=beyond_cutoff, available_ns=cutoff + 1),
        ],
        cutoff_ns=cutoff,
    )
    pin = from_continuous(ref, tmp_path / "observations", role="train", profile=PROFILE)
    rows = _events(tmp_path / "observations", pin)
    entity = "solana/pool/" + pool
    event = rows[(entity, 300600)]
    assert _volume(event) == pytest.approx(
        [2, math.log1p(100.0) - math.log1p(10.0)]
    )
    assert event["available_ns"] == cutoff - 5
    assert event["event_ns"] == 300900 * NS
    assert (entity, 300900) not in rows
    inventory = [
        json.loads(line)
        for line in (
            tmp_path / "source" / "continuous" / "meta/captures.jsonl"
        ).read_text().splitlines()
    ]
    capture_root = tmp_path / "source" / "continuous" / "captures"
    inventoried_receipts = [
        (
            item["pin"],
            json.loads((capture_root / item["pin"] / "receipt.json").read_text())["available_ns"],
        )
        for item in inventory
    ]
    all_capture_pins = {pin for pin, _ in inventoried_receipts}
    first_receipt_and_current = {
        pin for pin, available in inventoried_receipts if available in (cutoff - 10, cutoff - 5)
    }
    assert len(all_capture_pins) == 3  # The post-cutoff capture is absent from the sealed export.
    assert event["evidence"] == sorted(first_receipt_and_current)
    assert len(event["evidence"]) == 2
    assert event["evidence"] == list(dict.fromkeys(event["evidence"]))
