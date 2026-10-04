"""Concrete reviewed B02 handoff. Only Brain roots; no D43/F01 access."""

import fcntl
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from memecoin_brain_scale.audit import safe_corpus
from memecoin_brain_scale.runtime import stage
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
OLD = BASE / "work/brain-b01/dev01"
NEW = BASE / "work/brain-b02/prospective01"
HISTORY = BASE / "work/brain-b02/history01"


def main():
    with (OLD / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not NEW.exists()
        assert sha(OLD / "universe.json") == read(HISTORY / "plan.json")["primary_universe_sha"]
        rows, provenance = safe_corpus(OLD, time.time_ns())
        assert rows and any(
            q["reason"] == "duplicate_bar_in_response" for q in provenance["quarantined"]
        )
        # STOP is operational handoff, never alter the frozen config/runtime or delete failures.
        if not (OLD / "STOP").exists():
            publish(
                OLD / "STOP", dict(reason="B02_reviewed_quarantine_handoff", at_ns=time.time_ns())
            )
        stopped = subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/ai.memecoin.brain.dev01"],
            capture_output=True,
        )
        write(
            BASE / "work/brain-b02/old-service-stop.json",
            dict(exit_code=stopped.returncode, at_ns=time.time_ns()),
        )
        assert stopped.returncode == 0
        NEW.mkdir()
        for name in ("cycles", "requests"):
            (NEW / name).mkdir()
        for name in ("models", "training", "shadow"):
            shutil.copytree(OLD / name, NEW / name)
        requests = list((OLD / "requests").iterdir()) + list((HISTORY / "requests").iterdir())
        requests.sort(key=lambda p: read(p / "request.json")["started_ns"])
        imports = []
        for i, path in enumerate(requests):
            target = NEW / "requests" / f"{i:06d}"
            shutil.copytree(path, target)
            imports.append(
                dict(
                    source=str(path),
                    target=target.name,
                    manifest_sha=sha(path / "manifest.json")
                    if (path / "manifest.json").exists()
                    else None,
                )
            )
        for name in ("config.json", "universe.json", "external-request-debit.json"):
            shutil.copyfile(OLD / name, NEW / name)
        write(
            NEW / "import.json",
            dict(
                primary=str(OLD),
                historical=str(HISTORY),
                requests=imports,
                quarantined=provenance["quarantined"],
                old_stop_sha=sha(OLD / "STOP"),
                historical_attempts=1,
                timestamps_unchanged=True,
            ),
        )
        write(
            NEW / "activation.json",
            dict(
                decision="approved",
                scope="brain_B02_remaining_B01_envelope",
                paid_budget_usd=0,
                prior_consecutive_acquisition_failures=1,
                import_sha=sha(NEW / "import.json"),
                reason="Reviewed conflicting raw capture quarantine; keep original budget/expiry; user authorized B02 data quality and prospective continuation",
                F01=False,
                D43_changes=False,
                approved_ns=time.time_ns(),
            ),
        )
        stage(
            NEW,
            BASE / "work/brain-b02/runtime01",
            BASE / "work/brain-b02/ai.memecoin.brain.b02.plist",
        )
        for model in (NEW / "models").iterdir():
            verify(model)
        print(
            json.dumps(
                dict(
                    staged=str(NEW),
                    imported_requests=len(requests),
                    prior_failure_debit=1,
                    frozen=read(NEW / "deployment.json"),
                )
            )
        )


if __name__ == "__main__":
    main()
