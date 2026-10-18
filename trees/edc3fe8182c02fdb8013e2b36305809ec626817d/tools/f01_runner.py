"""Execute a pre-bound plan; never creates admission or release decisions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memecoin_ml.data import read
from memecoin_research.framework import load_protocol
from memecoin_runner.runner import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("protocol", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--admission", type=Path)
    parser.add_argument("--admission-sha256")
    args = parser.parse_args()
    result = run(
        load_protocol(args.protocol),
        read(args.plan),
        args.dataset,
        args.output,
        admission=args.admission,
        admission_sha256=args.admission_sha256,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
