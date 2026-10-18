import time
from pathlib import Path

import pytest

from memecoin_ml.data import sha, write
from memecoin_operator import watch_market
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish

MINT = "So11111111111111111111111111111111111111112"
QUOTE = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
POOL = "7XawhbbxtsRcQA8KTkHT9f9nc6d69UwqCDh6U5EEbEmX"


def pool_payload():
    return {
        "data": [
            {
                "id": f"solana_{POOL}",
                "type": "pool",
                "attributes": {
                    "address": POOL,
                    "name": "TOKEN / SOL",
                    "reserve_in_usd": "12000",
                    "base_token_price_usd": "0.005",
                    "quote_token_price_usd": "150",
                },
                "relationships": {
                    "base_token": {"data": {"id": f"solana_{MINT}", "type": "token"}},
                    "quote_token": {"data": {"id": f"solana_{QUOTE}", "type": "token"}},
                    "dex": {"data": {"id": "raydium", "type": "dex"}},
                },
            }
        ],
        "included": [
            {
                "id": f"solana_{MINT}",
                "type": "token",
                "attributes": {"address": MINT, "symbol": "TKN"},
            },
            {
                "id": f"solana_{QUOTE}",
                "type": "token",
                "attributes": {"address": QUOTE, "symbol": "SOL"},
            },
        ],
    }


def install_model(operator_root, tmp_path):
    model = tmp_path / "frozen-model"
    model.mkdir()
    write(model / "model-card.json", {"finished_ns": time.time_ns() - 10})
    seal(model)
    write(
        operator_root / "config.json",
        {
            "champion_model_path": str(model),
            "champion_model_sha": sha(model / "manifest.json"),
            "paid_budget_usd": 0,
            "real_execution_enabled": False,
        },
    )
    return model


def test_resolve_mint_and_pool_are_bounded_and_sanitized(tmp_path, monkeypatch):
    operator = tmp_path / "owner" / "operator"
    operator.mkdir(parents=True)
    paths = []

    def fake_fetch(provider, path, params, *, lane):
        paths.append((path, params, lane))
        capture = provider / "requests" / f"{len(paths) - 1:06d}"
        capture.mkdir()
        write(capture / "response.bin", pool_payload())
        seal(capture)
        return capture

    monkeypatch.setattr(watch_market.source, "fetch", fake_fetch)
    resolved = watch_market.resolve(operator, "mint", MINT)
    assert resolved["provider"] == "geckoterminal_public_api"
    assert resolved["target_mint"] == MINT
    assert resolved["pools"][0]["address"] == POOL
    assert resolved["pools"][0]["base"]["symbol"] == "TKN"
    assert paths[-1][0] == f"/networks/solana/tokens/{MINT}/pools"
    assert paths[-1][2] == "prospective"

    direct = watch_market.resolve(operator, "pool", POOL)
    assert direct["address_kind"] == "pool"
    assert [row["address"] for row in direct["pools"]] == [POOL]
    assert paths[-1][0] == f"/networks/solana/pools/{POOL}"
    assert "capture_sha" not in direct


def test_watch_starts_with_pinned_model_and_does_not_touch_operator_config(tmp_path, monkeypatch):
    operator = tmp_path / "owner" / "operator"
    operator.mkdir(parents=True)
    model = install_model(operator, tmp_path)
    candidate = tmp_path / "new-unpromoted-candidate"
    candidate.mkdir()
    write(candidate / "model-card.json", {"finished_ns": time.time_ns() - 5})
    seal(candidate)
    registry = operator / "candidates"
    registry.mkdir()
    write(
        registry / "job_newer.json",
        {
            "id": "job_newer",
            "status": "completed",
            "selected": "gru",
            "promoted": False,
            "artifact": str(candidate),
            "model_sha": sha(candidate / "manifest.json"),
        },
    )
    original_config = sha(operator / "config.json")

    def fake_fetch(provider, path, params, *, lane):
        capture = provider / "requests" / f"{len(list((provider / 'requests').iterdir())):06d}"
        capture.mkdir()
        write(capture / "request.json", {"started_ns": time.time_ns() - 1000, "lane": lane})
        write(
            capture / "receipt.json",
            {
                "received_ns": time.time_ns() - 500,
                "available_ns": time.time_ns(),
                "http_status": 200,
            },
        )
        write(capture / "response.bin", pool_payload())
        seal(capture)
        return capture

    monkeypatch.setattr(watch_market.source, "fetch", fake_fetch)
    resolution = watch_market.resolve(operator, "mint", MINT)
    result = watch_market.start(
        operator,
        resolution_id=resolution["resolution_id"],
        pool=POOL,
        target_mint=MINT,
    )
    view = watch_market.status(operator)
    assert result["model_sha"] == sha(model / "manifest.json")
    assert result["model_sha"] != sha(candidate / "manifest.json")
    assert view["status"] == "running"
    assert view["auto_untouched"] is True and view["real_execution_enabled"] is False
    assert view["champion"] == "empirical"
    assert sha(operator / "config.json") == original_config
    with pytest.raises(ValueError, match="watch_already_running"):
        watch_market.start(
            operator,
            resolution_id=resolution["resolution_id"],
            pool=POOL,
            target_mint=MINT,
        )
    assert watch_market.stop(operator)["status"] == "stopped"
    assert watch_market.tick(operator)["polled"] is False


def test_live_tick_builds_forecast_and_isolated_virtual_entry(tmp_path, monkeypatch):
    operator = tmp_path / "owner" / "operator"
    operator.mkdir(parents=True)
    model = install_model(operator, tmp_path)
    fetch_clock = [time.time_ns()]

    def fake_fetch(provider, path, params, *, lane):
        capture_path = provider / "requests" / f"{len(list((provider / 'requests').iterdir())):06d}"
        capture_path.mkdir()
        write(capture_path / "request.json", {"started_ns": fetch_clock[0] - 1000, "lane": lane})
        write(
            capture_path / "receipt.json",
            {
                "received_ns": fetch_clock[0] - 500,
                "available_ns": fetch_clock[0] - 100,
                "http_status": 200,
            },
        )
        payload = (
            _pool_detail_payload() if path == f"/networks/solana/pools/{POOL}" else pool_payload()
        )
        write(capture_path / "response.bin", payload)
        seal(capture_path)
        return capture_path

    monkeypatch.setattr(watch_market.source, "fetch", fake_fetch)
    resolution = watch_market.resolve(operator, "mint", MINT)
    started = watch_market.start(
        operator,
        resolution_id=resolution["resolution_id"],
        pool=POOL,
        target_mint=MINT,
        now_ns=time.time_ns() - 2_000_000,
    )
    now = time.time_ns()
    fetch_clock[0] = now
    latest_close = now // (300 * 10**9) * 300
    capture = _bar_capture(tmp_path / "capture", now, latest_close)

    def fake_ohlcv(*args, **kwargs):
        assert kwargs["token"] == MINT
        return capture

    monkeypatch.setattr(watch_market.source, "ohlcv", fake_ohlcv)

    def fake_shadow(session, rows, provenance, model_path, **kwargs):
        assert model_path == model
        assert len(rows) >= 8 and provenance["as_of_ns"] <= time.time_ns()
        destination = session / "shadow" / "predictions"
        for horizon in (1, 3):
            publish(
                destination / f"prediction-{horizon}.json",
                {
                    "id": f"origin-{horizon}",
                    "pool": POOL,
                    "event_close_s": rows[-1]["open_s"] + 300,
                    "horizon": horizon,
                    "predicted_ns": now,
                    "model_sha": started["model_sha"],
                    "q": [0.02, 0.04, 0.08],
                    "q_by_candidate": {
                        "empirical": [0.02, 0.04, 0.08],
                        "linear": [0.01, 0.03, 0.07],
                        "gru": [0.0, 0.02, 0.06],
                    },
                    "origin_close": rows[-1]["ohlcv"][3],
                    "input_available_ns": now - 1,
                },
            )

    monkeypatch.setattr("memecoin_brain.shadow.tick", fake_shadow)
    result = watch_market.tick(operator, now_ns=now)
    view = watch_market.status(operator)
    assert result["polled"] is True
    assert len(view["forecasts"]) == 2
    assert view["live_price"] == "0.005"
    assert view["live_price_kind"] == "provider_pool_snapshot"
    assert view["action"] == "ENTER"
    assert view["portfolio"][0]["pending"][0]["pool"] == POOL
    assert view["ledger_head"]
    assert view["auto_untouched"] and not view["real_execution_enabled"]


def test_watch_decision_projection_covers_wait_enter_hold_exit():
    empty = {"books": {}}
    assert watch_market._action(empty, []) == ("WAIT", "waiting_for_first_forecast")
    waiting = {
        "books": {
            "book": {
                "positions": {},
                "pending": {},
                "signals": [{"reason": "median_below_cost_buffer"}],
            }
        }
    }
    assert watch_market._action(waiting, []) == ("WAIT", "median_below_cost_buffer")
    pending = {"books": {"book": {"positions": {}, "pending": {"pool": {}}, "signals": []}}}
    assert watch_market._action(pending, []) == ("ENTER", "entry_waiting_for_next_observed_close")
    holding = {"books": {"book": {"positions": {"pool": {}}, "pending": {}, "signals": []}}}
    assert watch_market._action(holding, []) == ("HOLD", "position_open_fixed_15m_exit")
    exited = [{"kind": "exit", "exit_ns": 20, "reason": "precommitted_time_exit"}]
    assert watch_market._action(empty, exited) == ("EXIT", "precommitted_time_exit")


def _resolution_capture(provider, payload):
    capture = provider / "requests" / "000000"
    capture.mkdir()
    write(capture / "response.bin", payload)
    seal(capture)
    return capture


def _pool_detail_payload():
    listing = pool_payload()
    return {"data": listing["data"][0], "included": listing["included"]}


def _bar_capture(path: Path, now_ns: int, latest_close_s: int) -> Path:
    path.mkdir()
    opened = latest_close_s - 9 * 300
    rows = [
        [
            opened + index * 300,
            1.0 + index * 0.001,
            1.01 + index * 0.001,
            0.99 + index * 0.001,
            1.005 + index * 0.001,
            1200.0,
        ]
        for index in range(10)
    ]
    write(
        path / "request.json",
        {"started_ns": now_ns - 1_000_000, "lane": "prospective"},
    )
    write(
        path / "receipt.json",
        {"received_ns": now_ns - 500_000, "available_ns": now_ns, "http_status": 200},
    )
    write(path / "response.bin", {"data": {"attributes": {"ohlcv_list": rows}}})
    seal(path)
    return path
