"""Stage the reviewed B03 root without altering old STOPs or the original budget/expiry."""

import shutil
import subprocess
import time
from pathlib import Path

from memecoin_brain_quality.policy import HOUR, inspect_capture
from memecoin_brain_quality.runtime import stage
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import verify

BASE = Path(__file__).resolve().parents[1]
OLD = BASE / "work/brain-b02/prospective01"
NEW = BASE / "work/brain-b03/prospective01"


def main():
    qualification = BASE / "work/brain-b03/recorded01"
    verify(qualification)
    assert read(qualification / "result.json")["full_cycle_pass"]
    for label in ("ai.memecoin.brain.dev01", "ai.memecoin.brain.b02", "ai.memecoin.brain.b03"):
        assert subprocess.run(["launchctl", "list", label], capture_output=True).returncode != 0
    assert (OLD / "STOP").exists()
    config = read(OLD / "config.json")
    assert config["expires_ns"] > time.time_ns() and config["max_requests"] == 12000
    assert config["paid_budget_usd"] == 0
    universe = read(OLD / "universe.json")
    assert len(universe["pools"]) == 12
    conflicts = {}
    imports = []
    requests = sorted((OLD / "requests").iterdir())
    for cap in requests:
        verify(cap)
        imports.append(dict(name=cap.name, manifest_sha=sha(cap / "manifest.json")))
        if not (cap / "receipt.json").exists():
            continue
        request = read(cap / "request.json")
        if request["lane"] == "discovery":
            continue
        pool = request["url"].split("/pools/")[1].split("/")[0]
        if inspect_capture(cap, pool)["status"] == "schema_quarantine":
            conflicts.setdefault(pool, []).append(
                dict(path=str(cap), sha=sha(cap / "manifest.json"))
            )
    assert 1 <= len(conflicts) <= 3
    total = len(requests) + read(OLD / "external-request-debit.json")["requests"]
    assert total == 103
    NEW.mkdir(exist_ok=False)
    for name in ("requests", "models", "training", "shadow"):
        shutil.copytree(OLD / name, NEW / name)
    for name in ("quality", "cycles", "history"):
        (NEW / name).mkdir()
    for name in ("config.json", "universe.json", "external-request-debit.json"):
        shutil.copyfile(OLD / name, NEW / name)
    now = time.time_ns()
    seed = dict(
        created_ns=now,
        pools={
            p["pool"]: dict(status="active", probes=0, streak=0, pending=None, next_probe_ns=0)
            for p in universe["pools"]
        },
        evidence=conflicts,
    )
    for pool in conflicts:
        seed["pools"][pool].update(status="quarantined", next_probe_ns=now + HOUR)
    write(NEW / "quality-seed.json", seed)
    write(
        NEW / "import.json",
        dict(
            primary=str(BASE / "work/brain-b01/dev01"),
            parent_root=str(OLD),
            parent_stop_sha=sha(OLD / "STOP"),
            original_global_attempts=total,
            requests=imports,
            record_bytes_and_timestamps_unchanged=True,
            original_expiry_ns=config["expires_ns"],
        ),
    )
    write(
        NEW / "activation.json",
        dict(
            decision="approved",
            scope="brain_B03_qualified_pool_isolation",
            paid_budget_usd=0,
            approved_ns=now,
            F01=False,
            D43_changes=False,
            import_sha=sha(NEW / "import.json"),
            quality_seed_sha=sha(NEW / "quality-seed.json"),
            qualification_sha=sha(qualification / "manifest.json"),
            prior_consecutive_acquisition_failures=0,
            previous_schema_failures_mapped_to_pool_quarantine=True,
            previous_source_stops_unchanged=True,
            basis=(
                "User accepted B02 and authorized B03; fixtures/recorded pass; "
                "new classified failure scope, same expiry/quota, no paid sources"
            ),
        ),
    )
    stage(
        NEW, BASE / "work/brain-b03/runtime01", BASE / "work/brain-b03/ai.memecoin.brain.b03.plist"
    )
    for model in (NEW / "models").iterdir():
        verify(model)
    decision = dict(
        root=str(NEW),
        deployment=read(NEW / "deployment.json"),
        imported_attempts=total,
        seed_quarantined=sorted(conflicts),
        expiry_ns=config["expires_ns"],
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        phase="bounded_live_qualification_before_job_install",
        job_installed=False,
    )
    write(BASE / "work/brain-b03/activation-decision.json", decision)
    print(decision)


if __name__ == "__main__":
    main()
