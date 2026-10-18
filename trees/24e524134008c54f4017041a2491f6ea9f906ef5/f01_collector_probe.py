"""One six-request qualification attempt. No scheduler or activation."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from memecoin_ai.sources import Capture, ReadOnlyRPC
from memecoin_collector.qualification import endpoint, identity, probe
from memecoin_ml.data import read, write
from memecoin_registry.contracts import seal
from memecoin_sampling.contract import load


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    binding, _ = load(Path("work/sampling-d48/proposal"))
    value = endpoint(Path(".env.local"))
    if args.worker:
        client = ReadOnlyRPC(
            value, "calendar-alchemy", timeout=15, max_bytes=1000000, keep_alive=True
        )
        refs = [
            Capture.load(Path(p))
            for p in read(Path("configs/qualified-http-corpus-v1.json"))["references"]
        ]
        try:
            probe(root, refs, client)
        finally:
            client.close()
        return
    root.mkdir(parents=True, exist_ok=False)
    write(
        root / "reservation.json",
        dict(
            started_ns=time.time_ns(),
            max_rpc=6,
            max_wall_seconds=100,
            retries=0,
            credential_sha256=identity(value),
            source_semantics=binding["source_semantics"],
            F01_corpus=False,
        ),
    )
    with (root / "worker.log").open("x") as log:
        try:
            code = subprocess.run(
                [sys.executable, __file__, str(root), "--worker"],
                stdout=log,
                stderr=log,
                timeout=100,
                check=False,
            ).returncode
        except subprocess.TimeoutExpired:
            code = 124
    if not (root / "result.json").exists():
        write(
            root / "result.json",
            dict(
                complete=False,
                category="worker_deadline_or_failure",
                exit_code=code,
                reserved_rpc=6,
            ),
        )
    seal(root)
    print(json.dumps(read(root / "result.json")), flush=True)


if __name__ == "__main__":
    main()
