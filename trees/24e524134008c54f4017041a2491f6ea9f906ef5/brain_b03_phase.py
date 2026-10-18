"""Run a single bounded B03 phase through the frozen runtime; never overwrite attempts."""

import argparse
import fcntl
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE / "work/brain-b03/prospective01"
sys.path.insert(0, str(BASE / "work/brain-b03/runtime01/runtime"))

from memecoin_brain_quality.runtime import verify_runtime  # noqa: E402
from memecoin_brain_quality.service import run_phase, tick  # noqa: E402
from memecoin_brain_scale.audit import safe_corpus  # noqa: E402
from memecoin_ml.data import read, write  # noqa: E402
from memecoin_registry.contracts import seal  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["history", "cycle"])
    args = parser.parse_args()
    verify_runtime(ROOT)
    if args.phase == "cycle":
        print(json.dumps(tick(ROOT, read(ROOT / "deployment.json")["pin"])), flush=True)
        return
    with (ROOT / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output = ROOT / "history/000000"
        output.mkdir(exist_ok=False)
        rows, _ = safe_corpus(ROOT, time.time_ns())
        original = read(BASE / "work/brain-b02/history01/plan.json")
        tasks = []
        for t in original["tasks"]:
            cursor = min(r["open_s"] for r in rows if r["pool"] == t["pool"])
            tasks.append(
                dict(pool=t["pool"], floor=t["floor"], cursor=cursor, needed=cursor > t["floor"])
            )
        write(
            output / "binding.json",
            dict(
                tasks=tasks,
                max_requests=24,
                anchor_s=original["anchor_s"],
                common_root_budget=True,
                created_ns=time.time_ns(),
                original_expiry_ns=read(ROOT / "config.json")["expires_ns"],
            ),
        )
        write(output / "reservation.json", dict(max_requests=24, max_seconds=600, no_retry=True))
        result = run_phase(ROOT, output, "history", 600)
        write(output / "result.json", result)
        seal(output)
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
