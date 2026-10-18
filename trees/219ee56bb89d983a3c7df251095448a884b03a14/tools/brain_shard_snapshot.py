"""Stream an exact sealed B13 SQLite snapshot to immutable lazy training shards."""

import argparse
import json
import resource
import sqlite3
import time
from pathlib import Path

from memecoin_curriculum.streaming import write_archive
from memecoin_ml.data import sha
from memecoin_registry.contracts import digest, verify
from memecoin_runner.ledger import publish


def main():
    p = argparse.ArgumentParser()
    p.add_argument("snapshot", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--as-of-ns", type=int, required=True)
    a = p.parse_args()
    verify(a.snapshot)
    started = time.monotonic()
    with sqlite3.connect(f"file:{a.snapshot / 'index.sqlite'}?mode=ro&immutable=1", uri=True) as db:

        def rows():
            for raw, checksum in db.execute(
                "select payload,checksum from firsts where available<=? order by pool,open_s",
                (a.as_of_ns,),
            ):
                row = json.loads(raw)
                if digest(row) != checksum:
                    raise ValueError("shard_source_checksum")
                yield row

        result = write_archive(
            rows(),
            dict(as_of_ns=a.as_of_ns, source_manifest_sha=sha(a.snapshot / "manifest.json")),
            a.output,
        )
    publish(
        a.output.parent / (a.output.name + "-measurement.json"),
        dict(
            seconds=time.monotonic() - started,
            peak_rss_platform_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            platform_units="bytes_on_macOS",
            samples=result["samples"],
            shards=len(result["shards"]),
            manifest_sha=sha(a.output / "manifest.json"),
        ),
    )


if __name__ == "__main__":
    main()
