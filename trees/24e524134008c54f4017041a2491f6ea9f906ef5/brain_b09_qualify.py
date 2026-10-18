"""20-minute bounded real-time qualification. No unattended activation by this tool."""

import json
import plistlib
import subprocess
import time
from pathlib import Path

from memecoin_continuous.hot import fresh_sample
from memecoin_continuous.qualification import assess
from memecoin_continuous.source import state
from memecoin_continuous.store import audit
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish

ROOT = Path(__file__).resolve().parents[1] / "work/brain-b09/source01"


def main():
    start = time.time_ns()
    samples = []
    out = ROOT / "qualification-samples"
    out.mkdir()
    command = plistlib.loads(
        (Path.home() / "Library/LaunchAgents/ai.memecoin.brain.b09.source.plist").read_bytes()
    )["ProgramArguments"]
    process = None
    counter = 0
    while time.time_ns() - start < 1200 * 10**9 or (process is not None and process.poll() is None):
        if (ROOT / "STOP").exists():
            break
        if process is None or process.poll() is not None:
            if process is not None:
                result = process.communicate()
                print(
                    dict(
                        worker=counter,
                        code=process.returncode,
                        log=result[0].strip(),
                        stderr=result[1][-1000:],
                    ),
                    flush=True,
                )
                if process.returncode:
                    break
            if time.time_ns() - start >= 1200 * 10**9:
                break
            if state(ROOT)["attempts"] >= 112:
                break
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            counter += 1
        sample = fresh_sample(ROOT, time.time_ns())
        publish(out / f"{len(samples):06d}.json", sample)
        samples.append(sample)
        if len(samples) % 6 == 0:
            print(
                dict(
                    elapsed_s=round((time.time_ns() - start) / 1e9),
                    fresh=len(sample["fresh"]),
                    samples=len(samples),
                ),
                flush=True,
            )
        time.sleep(10)
    if process is not None and process.poll() is None:
        process.wait(timeout=270)
    samples.append(fresh_sample(ROOT, time.time_ns()))
    publish(out / f"{len(samples) - 1:06d}.json", samples[-1])
    seal(out)
    report = assess(ROOT, samples, start)
    report.update(
        runtime_pin=read(ROOT / "deployment.json")["pin"],
        config_sha=sha(ROOT / "config.json"),
        samples_sha=sha(out / "manifest.json"),
        audit=audit(ROOT, time.time_ns()),
    )
    publish(ROOT / "qualification.json", report)
    if not report["approved"] and not (ROOT / "STOP").exists():
        publish(ROOT / "STOP", dict(reason="B09_qualification_failed", at_ns=time.time_ns()))
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
