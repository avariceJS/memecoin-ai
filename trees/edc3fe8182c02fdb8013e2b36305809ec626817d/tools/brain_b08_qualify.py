"""One post-cooldown qualification: at most 32 GET, frozen children, no budget reset."""

import json
import plistlib
import subprocess
import time
from pathlib import Path

from memecoin_continuous.source import state
from memecoin_continuous.store import audit, stats, view
from memecoin_ml.data import read
from memecoin_runner.ledger import publish

ROOT = Path(__file__).resolve().parents[1] / "work/brain-b08/source01"


def main():
    previous = ROOT / "qualification.json"
    resume = previous.exists()
    if resume and (read(previous)["approved"] or state(ROOT)["attempts"] != 24):
        raise ValueError("qualification_resume_budget")
    output = ROOT / ("qualification-v2.json" if resume else "qualification.json")
    until = state(ROOT)["cooldown_ns"]
    while time.time_ns() < until:
        print(
            dict(status="cooldown", remaining_s=round((until - time.time_ns()) / 1e9)), flush=True
        )
        time.sleep(min(45, max(0.01, (until - time.time_ns()) / 1e9)))
    plist = Path.home() / "Library/LaunchAgents/ai.memecoin.brain.b08.source.plist"
    command = plistlib.loads(plist.read_bytes())["ProgramArguments"]
    for i in range(1 if resume else 4):
        result = subprocess.run(command, capture_output=True, text=True, timeout=270)
        print(dict(cycle=i, code=result.returncode, safe_log=result.stdout.strip()), flush=True)
        if result.returncode or (ROOT / "STOP").exists():
            publish(
                output,
                dict(approved=False, reason="source_failed", stderr=result.stderr[-2000:]),
            )
            return
    cut = time.time_ns()
    rows, _ = view(ROOT, cut)
    latest = {}
    for r in rows:
        latest[r["pool"]] = r
    health = read(ROOT / "health.json")
    old = {
        p["pool"] for p in read(Path(read(ROOT / "seed.json")["parent"]) / "universe.json")["pools"]
    }
    fresh = [
        p
        for p, r in latest.items()
        if health["pools"][p]["status"] == "active" and 0 <= cut / 1e9 - r["open_s"] - 300 <= 300
    ]
    new = [p for p in latest if p not in old]
    exact = audit(ROOT, cut)
    approved = len(health["pools"]) == 24 and len(fresh) >= 6 and len(new) >= 4
    report = dict(
        approved=approved,
        cutoff_ns=cut,
        fresh=fresh,
        new_observed=new,
        denominator=len(health["pools"]),
        attempts=health["attempts"],
        audit=exact,
        stats=stats(ROOT),
        runtime_pin=read(ROOT / "deployment.json")["pin"],
    )
    publish(output, report)
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
