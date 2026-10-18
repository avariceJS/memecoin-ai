"""Stage a new collector-only root; preserve original request, expiry and probe debts."""

import shutil
import subprocess
import time
from pathlib import Path

from memecoin_brain_b04.collection import progress
from memecoin_brain_b04.runtime import inherited_seed, stage
from memecoin_brain_scale.audit import safe_corpus
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import seal, verify

BASE = Path(__file__).resolve().parents[1]
OLD = BASE / "work/brain-b03/prospective01"
NEW = BASE / "work/brain-b04/prospective01"


def main():
    verify(BASE / "work/brain-b04/audit01")
    assert (OLD / "STOP").exists()
    for label in [
        "ai.memecoin.brain.dev01",
        "ai.memecoin.brain.b02",
        "ai.memecoin.brain.b03",
        "ai.memecoin.brain.b04",
    ]:
        assert subprocess.run(["launchctl", "list", label], capture_output=True).returncode != 0
    c = read(OLD / "config.json")
    now = time.time_ns()
    assert c["expires_ns"] > now and c["max_requests"] == 12000 and c["paid_budget_usd"] == 0
    total = (
        len(list((OLD / "requests").iterdir()))
        + read(OLD / "external-request-debit.json")["requests"]
    )
    assert total == 135
    imports = []
    for p in sorted((OLD / "requests").iterdir()):
        verify(p)
        imports.append(dict(name=p.name, manifest_sha=sha(p / "manifest.json")))
    # Recorded B03 cycles qualify the new availability accounting without market queries.
    recorded = BASE / "work/brain-b04/recorded01"
    recorded.mkdir(exist_ok=False)
    (recorded / "cycles").mkdir()
    shutil.copyfile(OLD / "universe.json", recorded / "universe.json")
    write(recorded / "progress-seed.json", dict(last_closes={}, stagnant_cycles=0))
    records = []
    for i, p in enumerate(sorted((OLD / "cycles").iterdir())):
        output = recorded / "cycles" / f"{i:06d}"
        output.mkdir()
        records.append(progress(recorded, output, read(p / "collection.json")))
    write(
        recorded / "result.json",
        dict(data_kind="recorded_engineering", cycles=records, network_requests=0),
    )
    seal(recorded)
    rows, _ = safe_corpus(OLD, now)
    last = {
        p["pool"]: max(r["open_s"] + 300 for r in rows if r["pool"] == p["pool"])
        for p in read(OLD / "universe.json")["pools"]
    }
    NEW.mkdir(exist_ok=False)
    for name in ["requests", "models", "training", "shadow"]:
        shutil.copytree(OLD / name, NEW / name)
    for name in ["quality", "cycles"]:
        (NEW / name).mkdir()
    for name in ["config.json", "universe.json", "external-request-debit.json"]:
        shutil.copyfile(OLD / name, NEW / name)
    write(NEW / "quality-seed.json", inherited_seed(OLD, now))
    write(
        NEW / "progress-seed.json",
        dict(
            last_closes=last,
            stagnant_cycles=0,
            basis="new B04 stagnation policy; B03 data imported, no reset of existing B04 debt",
        ),
    )
    write(
        NEW / "import.json",
        dict(
            primary=str(BASE / "work/brain-b01/dev01"),
            parent_root=str(OLD),
            parent_stop_sha=sha(OLD / "STOP"),
            requests=imports,
            original_global_attempts=total,
            original_expiry_ns=c["expires_ns"],
            record_bytes_and_timestamps_unchanged=True,
            model_roots=[str((OLD / "models").resolve())],
        ),
    )
    write(
        NEW / "activation.json",
        dict(
            decision="approved",
            scope="brain_B04_collector_only",
            paid_budget_usd=0,
            training_enabled=False,
            approved_ns=now,
            F01=False,
            D43_changes=False,
            import_sha=sha(NEW / "import.json"),
            quality_seed_sha=sha(NEW / "quality-seed.json"),
            progress_seed_sha=sha(NEW / "progress-seed.json"),
            qualification_sha=sha(recorded / "manifest.json"),
            prior_consecutive_acquisition_failures=0,
            basis=(
                "User accepted B03 and authorized B04; new collector-only objective; "
                "B03 remains rejected"
            ),
        ),
    )
    stage(
        NEW, BASE / "work/brain-b04/runtime01", BASE / "work/brain-b04/ai.memecoin.brain.b04.plist"
    )
    for name in ["models", "training", "shadow"]:
        for p in (OLD / name).rglob("*"):
            if p.is_file():
                assert sha(p) == sha(NEW / p.relative_to(OLD))
    write(
        BASE / "work/brain-b04/staging.json",
        dict(
            root=str(NEW),
            deployment=read(NEW / "deployment.json"),
            code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            imported_attempts=total,
            training_enabled=False,
            service_installed=False,
        ),
    )
    print(read(NEW / "deployment.json"))


if __name__ == "__main__":
    main()
