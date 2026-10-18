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
    parser.add_argument("--batch", type=int, default=1024)
    parser.add_argument("--fit-epochs", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 <= args.fit_epochs <= 10:
        parser.error("--fit-epochs must be between 0 and 10")
    if args.rows < 100:
        parser.error("--rows must be at least 100")
    if not 1 <= args.batch <= 4096:
        parser.error("--batch must be between 1 and 4096")

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
        train_count, reader_batches, previous_size = 0, 0, 0
        for _, _, _, ids, _ in batches(snapshot, plan, "train", args.batch, seed=42):
            if reader_batches:
                assert previous_size == args.batch
            previous_size = len(ids)
            train_count += previous_size
            reader_batches += 1
        assert train_count > args.rows * 0.79
        fitted = None
        if args.fit_epochs:
            from memecoin_experience.learning import train
            from memecoin_experience.storage import file_hash

            job = root / "capacity-fit"
            training = train(
                root,
                result["snapshot"],
                job,
                plan=plan,
                config=dict(width=16, batch=args.batch, epochs=args.fit_epochs, seed=42),
            )
            assert training["rows_seen"] == train_count * args.fit_epochs
            fitted = dict(
                epochs=training["epochs"],
                steps=training["steps"],
                rows_seen=training["rows_seen"],
                parameters=training["parameters"],
                artifact=file_hash(job / "artifact/manifest.json"),
                test=training["test"],
                device="cpu",
            )
        receipt = dict(
            qualification="synthetic_engineering_only_no_market_claim",
            optimizer_fit=fitted,
            input_bars=args.rows,
            decisions=observed,
            training_reader_origins=train_count,
            requested_batch=args.batch,
            training_reader_batches=reader_batches,
            final_batch_size=previous_size,
            all_nonfinal_batches_full=True,
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
