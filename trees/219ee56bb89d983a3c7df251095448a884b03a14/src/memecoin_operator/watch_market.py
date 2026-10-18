"""Isolated single-market watch lane; never schedules or mutates AUTO jobs."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from memecoin_brain import source
from memecoin_ml.data import read, sha
from memecoin_paper.engine import advance, summary
from memecoin_paper.projection import restore, save
from memecoin_paper.runtime import atomic
from memecoin_registry.contracts import digest, verify
from memecoin_runner.ledger import Ledger, publish

NS = 10**9
POLL_NS = 60 * NS
ADDRESS = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
_LOCK = threading.RLock()
Row = dict[str, Any]


def _root(operator_root: Path) -> Path:
    return Path(operator_root).resolve().parent / "watch-market"


def _prepare(root: Path) -> Path:
    root = Path(root)
    if root.is_symlink():
        raise ValueError("watch_root_symlink")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("resolutions", "sessions"):
        path = root / name
        if path.is_symlink():
            raise ValueError("watch_root_symlink")
        path.mkdir(exist_ok=True)
    provider = root / "provider"
    if provider.is_symlink():
        raise ValueError("watch_root_symlink")
    if not provider.exists():
        source.initialize(provider)
        config_path = provider / "config.json"
        config = read(config_path)
        config["track"] = "brain_watch_market"
        config["max_requests"] = 8640
        config["paid_budget_usd"] = 0
        atomic(config_path, config)
    state_path = root / "state.json"
    if state_path.is_symlink():
        raise ValueError("watch_root_symlink")
    if not state_path.exists():
        atomic(state_path, {"schema": "watch-market-v1", "status": "idle"})
    return root


def _session_path(root: Path, session_id: Any) -> Path:
    if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
        raise ValueError("watch_session_invalid")
    sessions = root / "sessions"
    path = sessions / session_id
    if sessions.is_symlink() or path.is_symlink():
        raise ValueError("watch_session_invalid")
    return path


def _address(value: Any) -> str:
    if not isinstance(value, str) or not ADDRESS.fullmatch(value):
        raise ValueError("invalid_address")
    return value


def _token_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    prefix = "solana_"
    address = value[len(prefix) :] if value.startswith(prefix) else value
    return address if ADDRESS.fullmatch(address) else None


def _pool_options(payload: Row, target_mint: str | None) -> list[Row]:
    included = {
        item.get("id"): item for item in payload.get("included", []) if isinstance(item, dict)
    }
    result = []
    data = payload.get("data")
    entries = data if isinstance(data, list) else [data]
    for item in entries:
        if not isinstance(item, dict) or item.get("type") != "pool":
            continue
        attrs = item.get("attributes")
        if not isinstance(attrs, dict):
            continue
        rel = item.get("relationships") or {}
        tokens = {}
        for side in ("base_token", "quote_token"):
            token_ref = ((rel.get(side) or {}).get("data") or {}).get("id")
            token = included.get(token_ref, {})
            token_attrs = token.get("attributes") or {}
            mint = _token_id(token_attrs.get("address")) or _token_id(token_ref)
            if mint:
                tokens[side] = dict(
                    mint=mint,
                    name=token_attrs.get("name"),
                    symbol=token_attrs.get("symbol"),
                )
        if target_mint and target_mint not in {v["mint"] for v in tokens.values()}:
            continue
        address = attrs.get("address")
        if not isinstance(address, str):
            address = _token_id(item.get("id"))
        if not address or not ADDRESS.fullmatch(address):
            continue
        dex_ref = ((rel.get("dex") or {}).get("data") or {}).get("id")
        result.append(
            dict(
                address=address,
                name=attrs.get("name"),
                dex=dex_ref,
                reserve_usd=attrs.get("reserve_in_usd"),
                base=tokens.get("base_token"),
                quote=tokens.get("quote_token"),
            )
        )
    return result[:20]


def resolve(operator_root: Path, address_kind: str, address: str) -> Row:
    """Resolve a pasted mint or pool through the public, keyless provider API."""
    if address_kind not in ("mint", "pool"):
        raise ValueError("invalid_address_kind")
    address = _address(address.strip())
    root = _prepare(_root(operator_root))
    provider = root / "provider"
    path = (
        f"/networks/solana/tokens/{address}/pools"
        if address_kind == "mint"
        else f"/networks/solana/pools/{address}"
    )
    params = {"include": "base_token,quote_token,dex"}
    with _LOCK:
        capture = source.fetch(provider, path, params, lane="prospective")
    payload = json.loads((capture / "response.bin").read_bytes())
    pools = _pool_options(payload, address if address_kind == "mint" else None)
    if address_kind == "pool":
        pools = [pool for pool in pools if pool["address"] == address]
    resolution_id = uuid.uuid4().hex
    row = dict(
        schema="watch-market-resolution-v1",
        resolution_id=resolution_id,
        created_ns=time.time_ns(),
        address_kind=address_kind,
        address=address,
        target_mint=address if address_kind == "mint" else None,
        provider="geckoterminal_public_api",
        capture_sha=sha(capture / "manifest.json"),
        pools=pools,
    )
    publish(root / "resolutions" / f"{resolution_id}.json", row)
    return {
        key: row[key]
        for key in (
            "schema",
            "resolution_id",
            "created_ns",
            "address_kind",
            "address",
            "target_mint",
            "provider",
            "pools",
        )
    }


def _model(operator_root: Path) -> tuple[Path, str]:
    config = read(Path(operator_root) / "config.json")
    path = Path(config["champion_model_path"])
    verify(path)
    model_sha = sha(path / "manifest.json")
    if model_sha != config.get("champion_model_sha"):
        raise ValueError("watch_champion_pin")
    card = read(path / "model-card.json")
    if card.get("finished_ns", time.time_ns()) > time.time_ns():
        raise ValueError("watch_model_not_ready")
    return path.resolve(), model_sha


def start(
    operator_root: Path,
    *,
    resolution_id: str,
    pool: str,
    target_mint: str,
    now_ns: int | None = None,
) -> Row:
    now = time.time_ns() if now_ns is None else now_ns
    pool, target_mint = _address(pool), _address(target_mint)
    if not re.fullmatch(r"[0-9a-f]{32}", resolution_id):
        raise ValueError("invalid_resolution")
    root = _prepare(_root(operator_root))
    with _LOCK:
        state = read(root / "state.json")
        if state.get("status") == "running":
            raise ValueError("watch_already_running")
        resolution_path = root / "resolutions" / f"{resolution_id}.json"
        if resolution_path.is_symlink() or not resolution_path.is_file():
            raise ValueError("invalid_resolution")
        resolution = read(resolution_path)
        choice = next((p for p in resolution["pools"] if p["address"] == pool), None)
        if choice is None or target_mint not in {
            (choice.get("base") or {}).get("mint"),
            (choice.get("quote") or {}).get("mint"),
        }:
            raise ValueError("watch_pool_token_mismatch")
        if resolution.get("target_mint") and resolution["target_mint"] != target_mint:
            raise ValueError("watch_mint_mismatch")
        source_config = read(root / "provider" / "config.json")
        if now >= source_config["expires_ns"]:
            raise ValueError("watch_provider_expired")
        model_path, model_sha = _model(Path(operator_root))
        session_id = uuid.uuid4().hex
        session = _session_path(root, session_id)
        (session / "shadow" / "predictions").mkdir(parents=True)
        (session / "shadow" / "outcomes").mkdir(parents=True)
        initial_config = dict(
            schema="watch-market-session-v1",
            session_id=session_id,
            resolution_id=resolution_id,
            address_kind=resolution["address_kind"],
            submitted_address=resolution["address"],
            mint=target_mint,
            pool=pool,
            pool_name=choice.get("name"),
            dex=choice.get("dex"),
            started_ns=now,
            expires_ns=min(now + 72 * 3600 * NS, source_config["expires_ns"]),
            model_path=str(model_path),
            model_sha=model_sha,
            champion="empirical",
            policy="B07 fixed-time price proxy",
            initial_cash_usd="1000",
            real_execution_enabled=False,
        )
        publish(session / "session.json", initial_config)
        atomic(session / "firsts.json", {"rows": [], "revisions": 0})
        atomic(
            root / "state.json",
            {
                "schema": "watch-market-v1",
                "status": "running",
                "session_id": session_id,
                "started_ns": now,
                "last_attempt_ns": 0,
                "last_success_ns": 0,
                "last_error": None,
            },
        )
        return {
            "ok": True,
            "session_id": session_id,
            "started_ns": now,
            "model_sha": model_sha,
            "pool": pool,
            "mint": target_mint,
        }


def stop(operator_root: Path) -> Row:
    root = _prepare(_root(operator_root))
    with _LOCK:
        state = read(root / "state.json")
        if state.get("status") != "running":
            return {"ok": True, "status": state.get("status", "idle"), "duplicate": True}
        state.update(status="stopped", stopped_ns=time.time_ns(), next_poll_ns=0)
        atomic(root / "state.json", state)
        return {"ok": True, "status": "stopped", "session_id": state["session_id"]}


def _rows(session: Path, capture: Path, pool: str) -> list[Row]:
    firsts_path = session / "firsts.json"
    firsts = read(firsts_path)
    by_open = {row["open_s"]: row for row in firsts["rows"]}
    revisions = firsts["revisions"]
    for row in source.bars(capture, pool):
        previous = by_open.get(row["open_s"])
        if previous is None:
            by_open[row["open_s"]] = row
        elif previous["ohlcv"] != row["ohlcv"]:
            revisions += 1
    output = sorted(by_open.values(), key=lambda row: row["open_s"])
    atomic(firsts_path, {"rows": output, "revisions": revisions})
    return output


def _public_error(root: Path) -> str:
    provider = root / "provider" / "requests"
    attempts = sorted(provider.iterdir()) if provider.exists() else []
    if not attempts:
        return "provider_unavailable"
    path = attempts[-1] / "failure.json"
    if not path.exists():
        return "provider_unavailable"
    status_code = read(path).get("http_status")
    if status_code in (401, 403):
        return "provider_access_denied"
    if status_code == 429:
        return "provider_cooldown"
    return "provider_unavailable"


def tick(operator_root: Path, *, now_ns: int | None = None) -> Row:
    """One bounded poll/inference/paper step; source work is isolated from operator.tick."""
    root = _prepare(_root(operator_root))
    now = time.time_ns() if now_ns is None else now_ns
    with _LOCK:
        state = read(root / "state.json")
        if state.get("status") != "running" or now < state.get("next_poll_ns", 0):
            return {"status": state.get("status", "idle"), "polled": False}
        session = _session_path(root, state.get("session_id"))
        config = read(session / "session.json")
        if now >= config["expires_ns"]:
            state.update(status="stopped", stopped_ns=now, last_error="watch_session_expired")
            atomic(root / "state.json", state)
            return {"status": "stopped", "polled": False, "reason": "watch_session_expired"}
        state["last_attempt_ns"] = now
        state["next_poll_ns"] = now + POLL_NS
        atomic(root / "state.json", state)
        try:
            price_capture = source.fetch(
                root / "provider",
                f"/networks/solana/pools/{config['pool']}",
                {"include": "base_token,quote_token,dex"},
                lane="prospective",
            )
            pool_payload = json.loads((price_capture / "response.bin").read_bytes())
            pool_data = pool_payload.get("data")
            attrs = pool_data.get("attributes") if isinstance(pool_data, dict) else {}
            attrs = attrs or {}
            resolution = read(root / "resolutions" / f"{config['resolution_id']}.json")
            choice = next(item for item in resolution["pools"] if item["address"] == config["pool"])
            side = "base" if (choice.get("base") or {}).get("mint") == config["mint"] else "quote"
            live_price = attrs.get(f"{side}_token_price_usd")
            try:
                if not isinstance(live_price, str) or float(live_price) <= 0:
                    live_price = None
            except ValueError:
                live_price = None
            price_receipt = read(price_capture / "receipt.json")
            capture = source.ohlcv(
                root / "provider",
                config["pool"],
                lane="prospective",
                limit=96,
                token=config["mint"],
            )
            rows = _rows(session, capture, config["pool"])
            if len(rows) < 8:
                raise ValueError("watch_warmup_insufficient")
            receipt = read(capture / "receipt.json")
            provenance = dict(as_of_ns=receipt["available_ns"], watch_session=config["session_id"])
            from memecoin_brain.shadow import tick as shadow_tick

            shadow_tick(
                session,
                rows,
                provenance,
                Path(config["model_path"]),
                prediction_pools={config["pool"]},
                prediction_start_ns=config["started_ns"],
            )
            latest = rows[-1]
            age_ns = now - latest["open_s"] * NS - 300 * NS
            market = dict(
                pool=config["pool"],
                price=str(latest["ohlcv"][3]),
                close_s=latest["open_s"] + 300,
                available_ns=latest["available_ns"],
                capture_sha=latest["capture_sha"],
                quality="active" if 0 <= age_ns <= 300 * NS else "quarantined",
                role="watch",
            )
            if live_price is None:
                live_price = market["price"]
                price_received_ns = latest["available_ns"]
                price_kind = "last_closed_bar"
            else:
                price_received_ns = price_receipt["available_ns"]
                price_kind = "provider_pool_snapshot"
            source_ok = 0 <= now - receipt["available_ns"] <= 180 * NS
            predictions = []
            predictions_dir = session / "shadow" / "predictions"
            for path in sorted(predictions_dir.glob("*.json")):
                prediction = read(path)
                if prediction["pool"] != config["pool"] or prediction["horizon"] != 3:
                    continue
                prediction_id = sha(path)
                if prediction_id in state.get("prediction_ids", []):
                    continue
                empirical = prediction["q_by_candidate"]["empirical"]
                predictions.append(
                    dict(
                        id=prediction_id,
                        pool=prediction["pool"],
                        predicted_ns=prediction["predicted_ns"],
                        input_available_ns=prediction["input_available_ns"],
                        event_close_s=prediction["event_close_s"],
                        model_sha=prediction["model_sha"],
                        q_by_candidate={"empirical": empirical},
                    )
                )
            binding = dict(
                scope="watch_market_virtual_only",
                session_id=config["session_id"],
                pool=config["pool"],
                mint=config["mint"],
                model_sha=config["model_sha"],
                policy=config["policy"],
            )
            with Ledger(session / "ledger", binding) as ledger:
                paper_state, records, last_records = restore(session, ledger.events)
                seen = set(paper_state["seen"])
                predictions = [p for p in predictions if p["id"] not in seen]
                paper_tick = dict(
                    at_ns=now,
                    activation_ns=config["started_ns"],
                    market=[market],
                    predictions=predictions,
                    source_ok=source_ok,
                    deployment_config_sha=sha(session / "session.json"),
                )
                due = any(book["pending"] for book in paper_state["books"].values())
                prior = ledger.events[-1]["payload"]["tick"] if ledger.events else None
                if (
                    prior is None
                    or prior["market"] != [market]
                    or predictions
                    or prior["source_ok"] != source_ok
                    or due
                ):
                    paper_state, last_records = advance(paper_state, paper_tick)
                    ledger.append(
                        "tick",
                        tick=paper_tick,
                        result_sha=digest(dict(state=paper_state, records=last_records)),
                    )
                    records.extend(last_records)
                    save(session, ledger.events, paper_state, records, last_records)
            state.update(last_success_ns=receipt["available_ns"], last_error=None)
            state["prediction_ids"] = sorted(
                set(state.get("prediction_ids", [])) | {p["id"] for p in predictions}
            )
            state["market"] = market
            state["live_price"] = str(live_price)
            state["live_price_received_ns"] = price_received_ns
            state["live_price_kind"] = price_kind
            state["forecasts"] = _forecasts(session, config)
            state["portfolio"] = summary(paper_state, [market], now)
            state["decisions"] = records[-30:]
            state["action"], state["reason"] = _action(paper_state, records)
            state["ledger_head"] = ledger.head
            atomic(root / "state.json", state)
            return {
                "status": "running",
                "polled": True,
                "bars": len(rows),
                "predictions": len(predictions),
            }
        except Exception as exc:
            state["last_error"] = (
                _public_error(root)
                if isinstance(exc, (OSError, ValueError))
                else "watch_inference_failed"
            )
            atomic(root / "state.json", state)
            return {"status": "running", "polled": False, "reason": state["last_error"]}


def _forecasts(session: Path, config: Row) -> list[Row]:
    latest = {}
    for path in (session / "shadow" / "predictions").glob("*.json"):
        prediction = read(path)
        if prediction["pool"] != config["pool"]:
            continue
        horizon = prediction["horizon"]
        if horizon not in latest or prediction["predicted_ns"] > latest[horizon]["predicted_ns"]:
            latest[horizon] = prediction
    return [
        dict(
            horizon_minutes=5 * horizon,
            predicted_ns=row["predicted_ns"],
            model_sha=row["model_sha"],
            q_by_model=row["q_by_candidate"],
            origin_price=row["origin_close"],
            input_available_ns=row["input_available_ns"],
        )
        for horizon, row in sorted(latest.items())
    ]


def _action(paper_state: Row, records: list[Row]) -> tuple[str, str]:
    latest_decision_ns = max(
        (row.get("decision_ns", 0) for row in records if row["kind"] == "decision"),
        default=0,
    )
    latest_exit = next((row for row in reversed(records) if row["kind"] == "exit"), None)
    if latest_exit and latest_exit["exit_ns"] >= latest_decision_ns:
        return "EXIT", latest_exit.get("reason", "precommitted_time_exit")
    book = next(iter(paper_state["books"].values()), None)
    if book:
        if book["positions"]:
            return "HOLD", "position_open_fixed_15m_exit"
        if book["pending"]:
            return "ENTER", "entry_waiting_for_next_observed_close"
        signal = book["signals"][-1] if book["signals"] else None
        if signal:
            if signal["reason"] == "enter_next_observed_close":
                return "ENTER", signal["reason"]
            if signal["reason"] == "already_exposed":
                return "HOLD", signal["reason"]
            return "WAIT", signal["reason"]
    recent = next((row for row in reversed(records) if row["kind"] == "decision"), None)
    if recent:
        return "WAIT", recent.get("reason", "waiting_for_forecast")
    return "WAIT", "waiting_for_first_forecast"


def status(operator_root: Path) -> Row:
    root = _root(operator_root)
    if root.is_symlink():
        raise ValueError("watch_root_symlink")
    state_path = root / "state.json"
    if state_path.is_symlink():
        raise ValueError("watch_root_symlink")
    if not state_path.exists():
        return {"schema": "watch-market-v1", "status": "idle", "auto_untouched": True}
    state = read(state_path)
    public = {
        "schema": state.get("schema", "watch-market-v1"),
        "status": state.get("status", "idle"),
        "session_id": state.get("session_id"),
        "started_ns": state.get("started_ns"),
        "stopped_ns": state.get("stopped_ns"),
        "last_attempt_ns": state.get("last_attempt_ns"),
        "last_success_ns": state.get("last_success_ns"),
        "last_error": state.get("last_error"),
        "market": state.get("market"),
        "live_price": state.get("live_price"),
        "live_price_received_ns": state.get("live_price_received_ns"),
        "live_price_kind": state.get("live_price_kind"),
        "forecasts": state.get("forecasts", []),
        "portfolio": state.get("portfolio", []),
        "decisions": state.get("decisions", []),
        "action": state.get("action", "WAIT"),
        "reason": state.get("reason", "waiting_for_first_forecast"),
        "ledger_head": state.get("ledger_head"),
        "prediction_ids_count": len(state.get("prediction_ids", [])),
        "auto_untouched": True,
        "real_execution_enabled": False,
        "provider": "geckoterminal_public_api",
        "provider_refresh_s": 60,
    }
    session_id = state.get("session_id")
    if session_id:
        session = _session_path(root, session_id)
        config_path = session / "session.json"
        firsts_path = session / "firsts.json"
        if config_path.is_symlink() or firsts_path.is_symlink():
            raise ValueError("watch_session_invalid")
        if config_path.exists():
            config = read(config_path)
            public.update(
                mint=config["mint"],
                pool=config["pool"],
                pool_name=config.get("pool_name"),
                dex=config.get("dex"),
                model_sha=config["model_sha"],
                champion=config["champion"],
                expires_ns=config["expires_ns"],
            )
            firsts = read(firsts_path)["rows"]
            public["price_history"] = [
                dict(time_ns=(row["open_s"] + 300) * NS, price=str(row["ohlcv"][3]))
                for row in firsts[-60:]
            ]
    return public
