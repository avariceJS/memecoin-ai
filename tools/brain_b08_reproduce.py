"""Offline independent joins: paper ledger -> published forecasts -> first-observed raw bars."""

import argparse
from pathlib import Path

from memecoin_continuous.store import audit, view
from memecoin_ml.data import read, sha
from memecoin_paper.audit import replay
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import Ledger


def reproduce(snapshot, index_root=None):
    verify(snapshot)
    c = read(snapshot / "config.json")
    source = Path(c["source"])
    indexed = index_root or source
    if index_root is not None and Path(read(indexed / "seed.json")["parent"]) != source:
        raise ValueError("index_parent_mismatch")
    deployment = read(snapshot / "deployment.json")
    bundle = Path(deployment["bundle"])
    verify(bundle)
    if sha(bundle / "manifest.json") != deployment["pin"]:
        raise ValueError("deployment_pin")
    paper = Path(read(bundle / "binding.json")["root"])
    with Ledger(snapshot / "ledger", read(snapshot / "ledger/binding.json")) as ledger:
        events = ledger.audit(read(snapshot / "external-head.json")["ledger_head"])
    cutoff = events[-1]["payload"]["tick"]["at_ns"]
    index_audit = audit(indexed, cutoff)
    rows, _ = view(indexed, cutoff)
    migration = None
    if c.get("migration_sha"):
        migration = read(snapshot / "migration.json")
        if sha(snapshot / "migration.json") != c["migration_sha"]:
            raise ValueError("migration_pin")
        if events[migration["prefix_events"] - 1]["sha256"] != migration["prefix_head"]:
            raise ValueError("migration_prefix")
    observations = {(r["pool"], r["open_s"] + 300, r["capture_sha"]): r for r in rows}
    prediction_files = {}
    parents = [source, *sorted((paper / "candidate-shadows").glob("*"))]
    for parent in parents:
        for path in (parent / "shadow/predictions").glob("*.json"):
            prediction_files[sha(path)] = path
    markets, predictions, model_pins = 0, 0, set()
    for e in events:
        tick = e["payload"]["tick"]
        for o in tick["market"]:
            r = observations[(o["pool"], o["close_s"], o["capture_sha"])]
            if str(r["ohlcv"][3]) != o["price"] or r["available_ns"] != o["available_ns"]:
                raise ValueError("raw_market_join")
            if o["available_ns"] > tick["at_ns"]:
                raise ValueError("future_raw")
            markets += 1
        for p in tick["predictions"]:
            original = read(prediction_files[p["id"]])
            for key in (
                "pool",
                "predicted_ns",
                "input_available_ns",
                "event_close_s",
                "model_sha",
                "q_by_candidate",
            ):
                if original[key] != p[key]:
                    raise ValueError("published_forecast_join")
            model = Path(original["model_path"])
            if p["model_sha"] not in model_pins:
                verify(model)
                if sha(model / "manifest.json") != p["model_sha"]:
                    raise ValueError("model_pin")
                model_pins.add(p["model_sha"])
            card = read(model / "model-card.json")
            if not card["training_as_of_ns"] <= card["finished_ns"] <= p["predicted_ns"]:
                raise ValueError("future_model")
            for frame in original["input_frames"].values():
                r = observations[(frame["pool"], frame["event_close_s"], frame["capture_sha"])]
                if r["available_ns"] > original["predicted_ns"]:
                    raise ValueError("future_forecast_input")
            predictions += 1
    result = replay(snapshot, read(snapshot / "external-head.json")["ledger_head"])
    if result != read(snapshot / "result.json"):
        raise ValueError("ledger_reproduction")
    return dict(
        raw_market_joins=markets,
        index_audit=index_audit,
        carried_ticks=migration["prefix_events"] if migration else len(events),
        new_ticks=len(events) - migration["prefix_events"] if migration else 0,
        published_forecast_joins=predictions,
        model_pins=sorted(model_pins),
        exact_replay=True,
        provider_requests=0,
    )


if __name__ == "__main__":
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--source-index", type=Path)
    args = parser.parse_args()
    print(json.dumps(reproduce(args.snapshot, args.source_index), indent=2))
