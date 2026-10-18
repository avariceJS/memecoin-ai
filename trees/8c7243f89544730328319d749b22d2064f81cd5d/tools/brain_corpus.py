"""Build immutable historical corpus from qualified birth metadata and swap candidates."""

import argparse
from pathlib import Path

from memecoin_pumpswap.corpus import build

p = argparse.ArgumentParser()
p.add_argument("output", type=Path)
p.add_argument("--births", required=True, type=Path)
p.add_argument("--swaps", required=True, nargs="+", type=Path)
a = p.parse_args()
print(build(a.births, a.swaps, a.output))
