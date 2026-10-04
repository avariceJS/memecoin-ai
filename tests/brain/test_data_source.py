import json

import pytest

from memecoin_brain.data import build, corpus, encode, features, scaler, split
from memecoin_brain.shadow import eligible, evaluate_prediction
from memecoin_brain.source import BAR, NS, SourceFailure, bars, fetch, initialize, select_universe
from memecoin_ml.data import write
from memecoin_registry.contracts import seal

POOL = "A" * 32


def rows(count=100):
    return [
        dict(
            pool=POOL,
            open_s=i * BAR,
            ohlcv=[10 + i * 0.01, 11 + i * 0.01, 9 + i * 0.01, 10.1 + i * 0.01, 100 + i],
            available_ns=40000 * NS,
            lane="historical",
            capture_sha="fixture",
        )
        for i in range(count)
    ]


def capture(root, data, *, lane="historical", available=40000 * NS):
    root.mkdir(parents=True)
    write(
        root / "request.json",
        dict(
            url=f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{POOL}/ohlcv/minute",
            lane=lane,
            started_ns=available - 2,
        ),
    )
    write(
        root / "receipt.json",
        dict(available_ns=available, received_ns=available - 1, status="completed"),
    )
    (root / "response.bin").write_text(
        json.dumps(dict(data=dict(attributes=dict(ohlcv_list=data))))
    )
    seal(root)
    return root


def test_historical_receipt_is_not_event_time_and_incomplete_bars_removed(tmp_path):
    p = capture(
        tmp_path / "cap", [[0, 10, 11, 9, 10, 1], [300, 10, 11, 9, 10, 1]], available=500 * NS
    )
    result = bars(p, POOL)
    assert len(result) == 1 and result[0]["available_ns"] == 500 * NS
    assert result[0]["open_s"] == 0


def test_invalid_ohlcv_and_duplicate_rejected(tmp_path):
    for i, data in enumerate(([[0, 10, 11, 12, 10, 1]], [[0, 10, 11, 9, 10, 1]] * 2)):
        p = capture(tmp_path / str(i), data)
        with pytest.raises(ValueError):
            bars(p, POOL)


def test_first_version_immutable_and_revisions_audited(tmp_path):
    root = tmp_path / "root"
    initialize(root)
    write(root / "universe.json", dict(admitted_ns=0, pools=[dict(pool=POOL)]))
    capture(root / "requests/000000", [[0, 10, 11, 9, 10, 1]])
    capture(
        root / "requests/000001", [[0, 10, 12, 9, 11, 1]], lane="prospective", available=50000 * NS
    )
    result, provenance = corpus(root, 60000 * NS)
    assert len(result) == 1 and result[0]["ohlcv"][3] == 10
    assert len(provenance["revisions"]) == 1
    result, provenance = corpus(root, 45000 * NS)
    assert not provenance["revisions"]


def test_future_rows_and_duplicate_identity_fail():
    r = rows()
    with pytest.raises(ValueError, match="future"):
        build(r, dict(as_of_ns=1))
    with pytest.raises(ValueError, match="duplicate"):
        build(r + [r[0]], dict(as_of_ns=50000 * NS))


def test_gap_resets_history_and_masks_cross_gap_targets():
    r = rows()
    r.pop(20)
    bundle = build(r, dict(as_of_ns=50000 * NS))
    assert not any(s["event_close_s"] == 23 * BAR for s in bundle["samples"])
    before = next(s for s in bundle["samples"] if s["event_close_s"] == 20 * BAR)
    assert all(t["log_return"] is None for t in before["targets"])


def test_purge_global_and_scaler_train_only():
    r = rows()
    bundle = build(r, dict(as_of_ns=50000 * NS))
    parts, bounds = split(bundle)
    assert all(s["maturity_s"] < bounds["train_end_s"] for s in parts["train"])
    fitted = scaler(bundle, parts["train"])
    future = parts["test"][-1]["history"][-1]
    bundle["frames"][future]["values"] = [1e12] * 12
    assert scaler(bundle, parts["train"]) == fitted
    assert len(encode(bundle, parts["train"][0], fitted)[0]) == 25


def test_features_do_not_depend_on_future():
    r = rows()
    expected = features(r[:8])
    r[8]["ohlcv"] = [1e8] * 5
    assert features(r[:8]) == expected


def test_shadow_strips_labels_and_rejects_future_availability():
    r = rows()
    bundle = build(r, dict(as_of_ns=50000 * NS))
    now = 100 * BAR * NS + 1
    # Synthetic prospective availability; test never presents hindsight bars as real history.
    for s in bundle["samples"]:
        s["actual_available_ns"] = now - 1
    candidates = eligible(bundle, model_finished_ns=now - 2, now_ns=now)
    assert candidates and all(t["log_return"] is None for s in candidates for t in s["targets"])
    bundle["samples"][-1]["actual_available_ns"] = now + 1
    with pytest.raises(ValueError, match="future"):
        eligible(bundle, model_finished_ns=1, now_ns=now)


def test_outcomes_require_prediction_before_target_availability():
    pred = dict(
        pool=POOL,
        event_close_s=BAR,
        horizon=1,
        predicted_ns=BAR * NS + 1,
        origin_close=10,
        q=[-0.1, 0, 0.1],
    )
    r = rows(2)
    r[1]["available_ns"] = 2 * BAR * NS + 1
    assert evaluate_prediction(pred, r, now_ns=2 * BAR * NS) is None
    assert evaluate_prediction(pred, r, now_ns=3 * BAR * NS)["status"] == "scored"
    pred["predicted_ns"] = 3 * BAR * NS
    assert evaluate_prediction(pred, r, now_ns=4 * BAR * NS)["status"] == "invalid"
    assert evaluate_prediction(pred, [], now_ns=5000 * NS)["status"] == "censored"


def test_budget_stop_before_network(tmp_path, monkeypatch):
    root = tmp_path / "root"
    initialize(root)
    (root / "STOP").touch()
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: pytest.fail("network"))
    with pytest.raises(SourceFailure, match="stop"):
        fetch(root, "/networks/solana/pools", dict(page=1), lane="discovery")
    assert not list((root / "requests").iterdir())


def test_provider_cooldown_and_scope_before_network(tmp_path, monkeypatch):
    import time

    root = tmp_path / "root"
    initialize(root)
    p = root / "requests/000000"
    p.mkdir()
    write(p / "request.json", dict(started_ns=time.time_ns()))
    write(p / "failure.json", dict(http_status=429, received_ns=time.time_ns()))
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: pytest.fail("network"))
    with pytest.raises(SourceFailure, match="cooldown"):
        fetch(root, "/networks/solana/pools", dict(page=1), lane="discovery")
    with pytest.raises(SourceFailure, match="scope"):
        fetch(root, "/sendTransaction", {}, lane="discovery")


def test_null_discovery_metadata_is_explicit_exclusion(tmp_path):
    p = tmp_path / "discovery"
    p.mkdir()
    item = dict(
        attributes=dict(address=POOL, reserve_in_usd=None, pool_created_at="2026-01-01T00:00:00Z"),
        relationships=dict(
            base_token=dict(data=dict(id="solana_base")),
            quote_token=dict(data=dict(id="solana_quote")),
            dex=dict(data=dict(id="x")),
        ),
    )
    valid = json.loads(json.dumps(item))
    valid["attributes"].update(address="B" * 32, reserve_in_usd="12000")
    valid["relationships"]["base_token"]["data"]["id"] = "solana_other"
    (p / "response.bin").write_text(json.dumps(dict(data=[item, valid])))
    write(p / "receipt.json", dict(available_ns=0))
    seal(p)
    result = select_universe([p], now_ns=1800000000 * NS)
    assert len(result["pools"]) == 1 and result["exclusions"]["missing_liquidity_or_creation"] == 1
