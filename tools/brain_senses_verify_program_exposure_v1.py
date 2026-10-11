#!/usr/bin/env python3
"""Read-only verification of a frozen Pair64/c3 slot exposure projection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from memecoin_pumpswap.program_exposure_projection_verify import verify_projection  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="projection directory under repository work/")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    receipt = verify_projection(ROOT, output)
    print(json.dumps(receipt, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
