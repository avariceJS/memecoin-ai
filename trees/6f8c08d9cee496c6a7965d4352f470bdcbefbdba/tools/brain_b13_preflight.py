"""Read-only legacy inventory plus separately sealed consistent incident evidence."""

import json
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

from memecoin_continuous.store import audit
from memecoin_ml.data import sha
from memecoin_paper.audit import snapshot
from memecoin_paper.health import operational
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]


def main():
    out = BASE / "work/brain-b13/preflight01"
    out.mkdir(exist_ok=False)
    source = BASE / "work/brain-b09/source01"
    incident = BASE / "work/brain-b12/paper01"
    cutoff = time.time_ns()
    with sqlite3.connect(f"file:{source / 'index.sqlite'}?mode=ro", uri=True) as src:
        with sqlite3.connect(out / "index.sqlite") as dest:
            src.backup(dest)
    src.close()
    dest.close()
    result = dict(
        at_ns=cutoff,
        source_raw_audit=audit(out, cutoff),
        b12_prefix=snapshot(incident, out / "b12-paper"),
        services=subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, check=True
        ).stdout.splitlines(),
    )
    result["services"] = [x for x in result["services"] if "ai.memecoin" in x]
    result["pins"] = {}
    for label in ["brain-b09/source01", "brain-b10/paper01", "brain-b12/paper01"]:
        p = BASE / "work" / label
        result["pins"][label] = {
            n: sha(p / n)
            for n in ["config.json", "deployment.json", "activation.json", "STOP"]
            if (p / n).exists()
        }
    result["health"] = {
        name: operational(BASE / "work" / name / "paper01") for name in ("brain-b10", "brain-b12")
    }
    for p in [
        incident / "STOP",
        *incident.glob("incident-*.json"),
        *incident.glob("paper-worker-179114291*.log"),
    ]:
        shutil.copyfile(p, out / p.name)
    publish(out / "result.json", result)
    seal(out)
    publish(
        BASE / "docs/evidence/brain-operator-preflight-2026-10-04.json",
        dict(**result, artifact=str(out), manifest_sha=sha(out / "manifest.json")),
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
