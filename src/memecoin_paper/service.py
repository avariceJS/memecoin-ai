"""Live observation, paper decisions, candidate refresh and safe public projection."""

import time
from pathlib import Path

from memecoin_brain.data import build, save_dataset
from memecoin_brain_quality.policy import states
from memecoin_brain_scale.audit import safe_corpus
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import Ledger, publish

from .engine import advance, initial, summary
from .runtime import atomic


def models(root):
    return [
        p / "model"
        for p in sorted((root / "training").glob("*"))
        if (p / "model/manifest.json").exists()
    ]


def source_rows(c, cutoff, training=False):
    if c.get("source_backend") == "continuous":
        from memecoin_continuous.store import view

        return view(
            Path(c["source"]), cutoff, since=cutoff // 10**9 - (7 if training else 2) * 86400
        )
    return safe_corpus(Path(c["source"]), cutoff)


def candidate_tick(root, rows, prov, healthy):
    from memecoin_brain.shadow import tick

    c = read(root / "config.json")
    candidates = models(root)
    active_models = candidates[-1:]
    if c.get("champion_model_path"):
        active_models = [Path(c["champion_model_path"]), *active_models]
    if not active_models or not rows:
        return
    last = max(r["open_s"] for r in rows)
    recent = [r for r in rows if r["open_s"] >= last - 86400]
    selected = {}
    for model in active_models:
        lane = root / "candidate-shadows" / sha(model / "manifest.json")
        (lane / "shadow").mkdir(parents=True, exist_ok=True)
        selected[lane] = model
    for lane in sorted((root / "candidate-shadows").glob("*")):
        tick(lane, recent, prov, selected.get(lane) if healthy else None, prediction_pools=healthy)


def paper(root):
    c = read(root / "config.json")
    source = Path(c["source"])
    cut = time.time_ns()
    rows, prov = source_rows(c, cut)
    if c.get("source_backend") == "continuous":
        quality = read(source / "health.json")["pools"]
    else:
        quality, _ = states(source)
    active = {p for p, q in quality.items() if q["status"] == "active"}
    source_ok = not (source / "STOP").exists() and cut < read(source / "config.json")["expires_ns"]
    candidate_tick(root, rows, prov, active if source_ok else set())
    latest = {}
    for r in rows:
        if r["pool"] not in latest or r["open_s"] > latest[r["pool"]]["open_s"]:
            latest[r["pool"]] = r
    market = [
        dict(
            pool=p,
            price=str(r["ohlcv"][3]),
            close_s=r["open_s"] + 300,
            available_ns=r["available_ns"],
            capture_sha=r["capture_sha"],
            quality="active" if p in active else "quarantined",
        )
        for p, r in sorted(latest.items())
    ]
    predictions = []
    now = time.time_ns()
    parents = [source, *sorted((root / "candidate-shadows").glob("*"))]
    for parent in parents:
        for path in (parent / "shadow/predictions").glob("*.json"):
            p = read(path)
            if p["horizon"] != 3 or not c["activation_ns"] <= p["predicted_ns"] <= now:
                continue
            if now - p["predicted_ns"] > 120 * 10**9:
                continue
            verify(Path(p["model_path"]))
            if sha(Path(p["model_path"]) / "manifest.json") != p["model_sha"]:
                raise ValueError("paper_model_pin")
            predictions.append(
                dict(
                    id=sha(path),
                    pool=p["pool"],
                    predicted_ns=p["predicted_ns"],
                    input_available_ns=p["input_available_ns"],
                    event_close_s=p["event_close_s"],
                    model_sha=p["model_sha"],
                    q_by_candidate=p["q_by_candidate"],
                )
            )
    binding = dict(config_sha=sha(root / "config.json"), scope="virtual_only_B07")
    migration = None
    if c.get("migration_sha"):
        if sha(root / "migration.json") != c["migration_sha"]:
            raise ValueError("paper_migration_pin")
        migration = read(root / "migration.json")
        binding = migration["ledger_binding"]
    with Ledger(root / "ledger", binding) as ledger:
        if migration:
            prefix = ledger.events[: migration["prefix_events"]]
            if not prefix or prefix[-1]["sha256"] != migration["prefix_head"]:
                raise ValueError("paper_migration_prefix")
        state = initial()
        all_records = []
        last_tick = None
        for e in ledger.audit():
            t = e["payload"]["tick"]
            state, records = advance(state, t)
            if digest(dict(state=state, records=records)) != e["payload"]["result_sha"]:
                raise ValueError("paper_replay_mismatch")
            all_records.extend(records)
            last_tick = t
        ids = set(state["seen"])
        predictions = [p for p in predictions if p["id"] not in ids]
        tick = dict(
            at_ns=now,
            activation_ns=c["activation_ns"],
            market=market,
            predictions=predictions,
            source_ok=source_ok,
            deployment_config_sha=sha(root / "config.json"),
        )
        due = any(b["pending"] for b in state["books"].values())
        if (
            last_tick is None
            or market != last_tick["market"]
            or predictions
            or source_ok != last_tick["source_ok"]
            or due
        ):
            state, records = advance(state, tick)
            ledger.append("tick", tick=tick, result_sha=digest(dict(state=state, records=records)))
            all_records.extend(records)
        books = summary(state, market, now)
        head = ledger.head
    daily = {}
    for r in all_records:
        if r["kind"] != "exit":
            continue
        k = str(r["exit_ns"] // (86400 * 10**9)) + ":" + r["book"]
        d = daily.setdefault(
            k, dict(UTC_day=r["exit_ns"] // (86400 * 10**9), book=r["book"], trades=0, net_pnl=0.0)
        )
        d["trades"] += 1
        d["net_pnl"] += float(r["net_pnl"])
    forecasts = {}
    for parent in parents:
        for p in (parent / "shadow/outcomes").glob("*.json"):
            o = read(p)
            if o["status"] != "scored":
                continue
            pred = parent / "shadow/predictions" / p.name
            if not pred.exists():
                continue
            pred = read(pred)
            if pred["predicted_ns"] < c["activation_ns"]:
                continue
            k = str((o["scored_ns"] // (86400 * 10**9), pred["model_sha"], pred["horizon"]))
            stat = forecasts.setdefault(
                k,
                dict(
                    UTC_day=o["scored_ns"] // (86400 * 10**9),
                    model_sha=pred["model_sha"],
                    horizon=pred["horizon"],
                    n=0,
                    loss_sums={},
                ),
            )
            stat["n"] += 1
            for name, m in o["candidate_metrics"].items():
                stat["loss_sums"][name] = stat["loss_sums"].get(name, 0) + m["pinball"]
    info = []
    for p in sorted((root / "training").glob("*")):
        if (p / "result.json").exists():
            info.append(read(p / "result.json"))
        elif (p / "reservation.json").exists():
            info.append(dict(status="reserved_or_running", **read(p / "reservation.json")))
    behavior = {}
    for record in all_records:
        k = record["book"]
        b = behavior.setdefault(k, dict(decisions=0, entries=0, exits=0, reasons={}))
        field = {"decision": "decisions", "entry": "entries", "exit": "exits"}.get(record["kind"])
        if field:
            b[field] += 1
        if record["kind"] == "decision":
            reason = record["reason"]
            b["reasons"][reason] = b["reasons"].get(reason, 0) + 1
    extra = {}
    if c.get("source_backend") == "continuous":
        from memecoin_continuous.store import stats

        h = read(source / "health.json")
        extra = dict(
            source_health=dict(
                **stats(source),
                attempts=h["attempts"],
                cooldown_ns=h["cooldown_ns"],
                max_requests=read(source / "config.json")["max_requests"],
                admitted_pools=len(quality),
            ),
            model_behavior=behavior,
        )
    public = dict(
        schema="brain-paper-v1",
        generated_ns=now,
        source_ok=source_ok,
        expires_ns=c["expires_ns"],
        activation_ns=c["activation_ns"],
        champion_model_sha=c["champion_model_sha"],
        champion="empirical",
        mode="VIRTUAL • GT price proxy • execution not calibrated",
        market=market,
        books=books,
        daily=list(daily.values()),
        forecast_scores=list(forecasts.values()),
        training=info,
        ledger_head=head,
        events=len(all_records),
        paper_live_authorized=True,
        real_execution_enabled=False,
        source_requests_added=0,
        corpus_bars=extra.get("source_health", {}).get("bars", len(rows)),
        **extra,
    )
    atomic(root / "dashboard.json", public)
    atomic(
        root / "health.json",
        dict(at_ns=time.time_ns(), status="running" if source_ok else "source_paused"),
    )
    print(
        dict(paper_books=len(books), new_predictions=len(predictions), source_ok=source_ok),
        flush=True,
    )


def training(root):
    from memecoin_brain.model import train

    c = read(root / "config.json")
    runs = sorted((root / "training").glob("*"))
    now = time.time_ns()
    if len(runs) >= c.get("training_max_runs", 3):
        return
    if runs and now - read(runs[-1] / "reservation.json")["started_ns"] < 86400 * 10**9:
        return
    rows, prov = source_rows(c, now, training=True)
    latest = max(r["open_s"] for r in rows)
    rows = [r for r in rows if r["open_s"] >= latest - 7 * 86400]
    if c.get("training_bar_cap") and len(rows) > c["training_bar_cap"]:
        floor = sorted(r["open_s"] for r in rows)[-c["training_bar_cap"]]
        rows = [r for r in rows if r["open_s"] >= floor]
    bundle = build(rows, prov)
    matured = sum(
        all(t["log_return"] is not None and t["available_ns"] <= now for t in s["targets"])
        for s in bundle["samples"]
    )
    previous_cut = read(runs[-1] / "reservation.json")["started_ns"] if runs else 0
    new_matured = sum(
        all(t["log_return"] is not None and t["available_ns"] <= now for t in s["targets"])
        and max(t["available_ns"] for t in s["targets"]) > previous_cut
        for s in bundle["samples"]
    )
    if runs and new_matured < 100:
        return
    run = root / "training" / f"{len(runs):06d}"
    run.mkdir()
    publish(
        run / "reservation.json",
        dict(
            started_ns=now,
            matured=matured,
            max_seconds=600,
            max_fits=4,
            new_matured=new_matured,
            paper_ledger_events_available=len(list((root / "ledger").glob("event-*.json"))),
            paper_pnl_used_as_training_truth=False,
        ),
    )
    try:
        save_dataset(run / "dataset", bundle)
        start = time.monotonic()
        train(run / "dataset", run / "model")
        card = read(run / "model/model-card.json")
        publish(
            run / "result.json",
            dict(
                status="completed",
                finished_ns=time.time_ns(),
                seconds=time.monotonic() - start,
                model_sha=sha(run / "model/manifest.json"),
                matured=matured,
                candidates=card["candidates"],
                selected_development=card["selected"],
                promoted=False,
                new_fits=4,
            ),
        )
        seal(run)
    except Exception as exc:
        if not (run / "result.json").exists():
            publish(
                run / "result.json",
                dict(status="failed", error_type=type(exc).__name__, finished_ns=time.time_ns()),
            )
        raise


def worker(root, phase):
    if phase == "paper":
        paper(root)
    elif phase == "train":
        training(root)
    else:
        raise ValueError("paper_phase")
