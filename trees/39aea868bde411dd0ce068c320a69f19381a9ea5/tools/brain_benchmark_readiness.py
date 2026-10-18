"""Read-only support/role preflight for the separate research benchmark."""

import argparse
import json
from pathlib import Path

from memecoin_curriculum.benchmark import readiness
from memecoin_ml.data import read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/brain-benchmark-v1.json"))
    args = parser.parse_args()
    config = read(args.config)
    print(json.dumps(readiness(config, config["assignments"]), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
