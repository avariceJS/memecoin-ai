"""Synthetic engineering capacity check; temporary payloads, no market or holdout access."""

import argparse
import resource
import tempfile
import time
from pathlib import Path

from memecoin_experience.storage import Snapshot, atomic_json, build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=520000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rows < 100:
        parser.error("--rows must be at least 100")

    def rows():
        for i in range(args.rows):
            close = 10 + (i % 19) / 10
            yield dict(
                pool="synthetic_capacity_only",
                open_s=i * 300,
                available_ns=(i * 300 + 301) * 10**9,
                ohlcv=[close, close + 1, close - 1, close, 100 + i % 100],
                capture_sha="a" * 64,
                lane="synthetic",
            )

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="experience-capacity-") as directory:
        root = Path(directory).resolve()
        result = build(
            root,
            rows(),
            sources=[dict(synthetic=True, rows=args.rows)],
            cutoff=(args.rows * 300 + 10000) * 10**9,
        )
        snapshot = Snapshot(root, result["snapshot"])
        observed, max_partition = 0, 0
        for i in range(len(snapshot.parts)):
            _, records = snapshot.partition(i)
            observed += len(records)
            max_partition = max(max_partition, len(records))
        assert observed == args.rows - 7 == result["decisions"]
        byte_count = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        # Optional ML environment: prove all eligible origins reach the actual training reader.
        from memecoin_experience.learning import batches

        end = args.rows * 300
        plan = dict(
            train_start_s=0,
            train_end_s=end * 8 // 10,
            validation_end_s=end * 9 // 10,
            test_end_s=end,
        )
        train_count = sum(
            len(ids) for _, _, _, ids, _ in batches(snapshot, plan, "train", 256, seed=42)
        )
        assert train_count > args.rows * 0.79
        receipt = dict(
            qualification="synthetic_engineering_only_no_market_claim",
            input_bars=args.rows,
            decisions=observed,
            training_reader_origins=train_count,
            partitions=len(snapshot.parts),
            max_partition=max_partition,
            bytes=byte_count,
            elapsed_seconds=time.monotonic() - started,
            max_rss_platform_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            rss_note="bytes on macOS, KiB on Linux; includes torch import; not a cluster benchmark",
            binding=result["binding"],
            snapshot=result["snapshot"],
            temporary_payloads_deleted_on_exit=True,
        )
    atomic_json(args.output, receipt)
    print(receipt)


if __name__ == "__main__":
    main()
