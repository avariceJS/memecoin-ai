"""Synthetic 72h-shaped ledger workload; never evidence of real uptime or model edge."""

import json
import time
from pathlib import Path

from memecoin_paper.engine import advance, initial
from memecoin_paper.projection import restore, save
from memecoin_registry.contracts import digest
from memecoin_runner.ledger import Ledger, publish

ROOT = Path(__file__).resolve().parents[1] / "work/brain-b09/projection-benchmark"


def main():
    ROOT.mkdir()
    start = time.perf_counter()
    state = initial()
    records = []
    with Ledger(ROOT / "ledger", dict(synthetic=True, scope="capacity_only")) as ledger:
        for i in range(864):
            close = 1200 + i * 300
            now = (close + 1) * 10**9
            market = [
                dict(
                    pool=str(p),
                    price="1",
                    close_s=close,
                    available_ns=now,
                    capture_sha="fixture",
                    quality="active",
                )
                for p in range(6)
            ]
            predictions = [
                dict(
                    id=f"{i}:{p}:{model}",
                    pool=str(p),
                    predicted_ns=now,
                    input_available_ns=now,
                    event_close_s=close,
                    model_sha=model,
                    q_by_candidate={n: [-0.1, 0, 0.1] for n in ("empirical", "linear", "gru")},
                )
                for model in ("fixture-a", "fixture-b")
                for p in range(6)
            ]
            tick = dict(
                at_ns=now, activation_ns=0, source_ok=True, market=market, predictions=predictions
            )
            state, last = advance(state, tick)
            records.extend(last)
            ledger.append("tick", tick=tick, result_sha=digest(dict(state=state, records=last)))
        build_s = time.perf_counter() - start
        start = time.perf_counter()
        rebuilt, rr, rl = restore(ROOT, ledger.events)
        full = time.perf_counter() - start
        assert state == rebuilt and records == rr
        save(ROOT, ledger.events, state, records, last)
        start = time.perf_counter()
        cached, cr, cl = restore(ROOT, ledger.events)
        fast = time.perf_counter() - start
        assert cached == state and cr == rr and cl == rl
        result = dict(
            synthetic=True,
            real_uptime_measured=False,
            ticks=864,
            decisions=len(records),
            distinct_predictions=len(state["seen"]),
            build_s=build_s,
            full_replay_s=full,
            checkpoint_restore_s=fast,
            exact=True,
            policy="cash-only fixture",
        )
        publish(ROOT / "result.json", result)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
