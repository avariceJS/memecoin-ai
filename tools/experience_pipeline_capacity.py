"""Whole research pipeline qualification on bounded synthetic captures; no network/market data."""

import argparse
import json
import resource
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

from memecoin_continuous.store import connect, ingest
from memecoin_experience.pipeline import run
from memecoin_experience.storage import atomic_json, code_identity, file_hash, seal_files


def exercise(root, *, pools=8, bars_per_pool=12500, epochs=2):
    started = time.monotonic()
    capture_root = root / "captures"
    capture_root.mkdir()
    index = root / "index.sqlite"
    enrollment = []
    available = 10**18
    with connect(index) as db:
        for p in range(pools):
            pool = f"synthetic-pipeline-pool-{p:06d}"
            identity = dict(
                pool=pool, base_mint=f"synthetic-base-{p}", quote_mint="synthetic-quote"
            )
            enrollment.append(identity)
            for begin in range(0, bars_per_pool, 1000):
                cap = capture_root / f"{p:06d}-{begin:09d}"
                cap.mkdir()
                values = []
                for i in range(begin, min(begin + 1000, bars_per_pool)):
                    price = 10 + (i % 19) / 10
                    values.append([i * 300, price, price + 1, price - 1, price, 100 + i % 100])
                atomic_json(
                    cap / "request.json",
                    dict(
                        started_ns=available - 2,
                        lane="historical",
                        url=f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/minute?"
                        + urlencode(
                            dict(
                                aggregate=5,
                                currency="usd",
                                token="base",
                                include_empty_intervals="false",
                                limit=1000,
                            )
                        ),
                    ),
                )
                atomic_json(
                    cap / "response.bin",
                    dict(
                        data=dict(attributes=dict(ohlcv_list=values)),
                        meta=dict(
                            base=dict(address=identity["base_mint"]),
                            quote=dict(address=identity["quote_mint"]),
                        ),
                    ),
                )
                atomic_json(
                    cap / "receipt.json",
                    dict(
                        received_ns=available - 1,
                        available_ns=available,
                        response_sha256=file_hash(cap / "response.bin"),
                    ),
                )
                seal_files(cap)
                ingest(db, cap, pool)
    source = dict(
        role="train",
        cutoff_ns=available,
        start_s=0,
        end_s=(bars_per_pool * 8 // 10) * 300,
        selection="synthetic fixed enrollment, not market data",
        pools=enrollment,
    )
    plan = dict(
        name="capacity-V1",
        owner="synthetic-qualification",
        index=str(index),
        training_source=source,
        evaluation_source=dict(
            source, role="research_evaluation", start_s=source["end_s"], end_s=bars_per_pool * 300
        ),
        representation=dict(context=8, horizons=[1, 3, 6]),
        split=dict(
            train_start_s=0,
            train_end_s=(bars_per_pool * 6 // 10) * 300,
            validation_end_s=(bars_per_pool * 7 // 10) * 300,
            test_end_s=source["end_s"],
        ),
        config=dict(width=16, batch=1024, epochs=epochs, seed=42),
    )
    indexed_seconds = time.monotonic() - started
    receipt = run(root / "pipeline", plan)
    elapsed = time.monotonic() - started
    assert receipt["qualified_bars"] == pools * (bars_per_pool * 8 // 10)
    assert receipt["persisted_examples"] == receipt["qualified_bars"] - pools * 7
    assert receipt["rows_seen"] == pools * (bars_per_pool * 6 // 10 - 9) * epochs
    assert len(receipt["metrics"]) == 3
    assert (root / "pipeline/evaluation/predictions.jsonl").is_file()
    return dict(
        qualification="synthetic_full_pipeline_only_no_market_or_GPU_claim",
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        code=code_identity(),
        tool_sha=file_hash(Path(__file__)),
        pools=pools,
        input_bars=pools * bars_per_pool,
        epochs=epochs,
        device="cpu",
        indexed_seconds=indexed_seconds,
        elapsed_seconds=elapsed,
        max_rss_platform_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        rss_note="bytes on macOS; KiB on Linux; full process including torch",
        payload_bytes=sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
        result=receipt,
        temporary_payloads_deleted_on_exit=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pools", type=int, default=8)
    parser.add_argument("--bars-per-pool", type=int, default=12500)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.pools <= 10000 or args.bars_per_pool < 500 or not 1 <= args.epochs <= 10:
        parser.error("positive bounded pool/epoch counts and at least 500 bars per pool required")
    with tempfile.TemporaryDirectory(prefix="brain-pipeline-capacity-") as directory:
        receipt = exercise(
            Path(directory).resolve(),
            pools=args.pools,
            bars_per_pool=args.bars_per_pool,
            epochs=args.epochs,
        )
    atomic_json(args.output, receipt)
    print(
        json.dumps(
            {
                k: receipt[k]
                for k in (
                    "input_bars",
                    "pools",
                    "epochs",
                    "elapsed_seconds",
                    "max_rss_platform_units",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
