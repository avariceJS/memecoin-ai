"""Review exact B04 collector-only evidence, publish receipt, then install one bounded job."""

import os
import shutil
import subprocess
import time
from pathlib import Path

from memecoin_brain_b04.runtime import verify_runtime
from memecoin_brain_b04.service import check_activation
from memecoin_brain_quality.policy import states
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE / "work/brain-b04/prospective01"


def main():
    verify_runtime(ROOT)
    assert not (ROOT / "STOP").exists()
    config = read(ROOT / "config.json")
    dep = read(ROOT / "deployment.json")
    assert config["expires_ns"] > time.time_ns() and config["max_requests"] == 12000
    assert (
        config["paid_budget_usd"] == 0
        and read(ROOT / "activation.json")["training_enabled"] is False
    )
    imported = read(ROOT / "import.json")
    old = Path(imported["parent_root"])
    assert sha(old / "STOP") == imported["parent_stop_sha"]
    assert config["expires_ns"] == imported["original_expiry_ns"]
    cycles = sorted((ROOT / "cycles").iterdir())
    assert len(cycles) == 2
    refs = []
    new_attempts = 0
    for p in cycles:
        verify(p)
        c = read(p / "collection.json")
        a = read(p / "availability.json")
        assert read(p / "result.json")["status"] == "completed"
        assert c["denominator"] == 12 and len(c["statuses"]) == 12
        assert a["valid_captures"] >= 1 and a["stagnant_cycles"] < 6
        assert {r["pool"] for r in c["statuses"]} == {
            r["pool"] for r in read(ROOT / "universe.json")["pools"]
        }
        refs.append(dict(name=p.name, sha=sha(p / "manifest.json")))
        new_attempts += c["attempted"]
    assert new_attempts <= 24
    quality, head = states(ROOT)
    assert sum(s["status"] == "quarantined" for s in quality.values()) < 4
    total = (
        len(list((ROOT / "requests").iterdir()))
        + read(ROOT / "external-request-debit.json")["requests"]
    )
    assert total == 135 + new_attempts and total < 12000
    assert len(list((ROOT / "training").iterdir())) == len(list((old / "training").iterdir()))
    for name in ["requests", "models", "training"]:
        for p in (old / name).rglob("*"):
            if p.is_file():
                assert sha(p) == sha(ROOT / p.relative_to(old))
    assert shutil.disk_usage(ROOT).free > config["free_floor_bytes"]
    staged = BASE / "work/brain-b04/ai.memecoin.brain.b04.plist"
    assert sha(staged) == dep["service_plist_sha"]
    for label in [
        "ai.memecoin.brain.dev01",
        "ai.memecoin.brain.b02",
        "ai.memecoin.brain.b03",
        "ai.memecoin.brain.b04",
    ]:
        assert subprocess.run(["launchctl", "list", label], capture_output=True).returncode != 0
    receipt = dict(
        decision="approved_collector_only",
        root=str(ROOT),
        runtime_pin=dep["pin"],
        plist_sha=sha(staged),
        expiry_ns=config["expires_ns"],
        training_enabled=False,
        cycles=refs,
        request_debit_at_activation=total,
        request_ceiling=12000,
        paid_usd=0,
        review_ns=time.time_ns(),
        quality_head=head,
        B03_activation="remains_rejected",
        rationale=(
            "Record full-denominator observations and missingness; "
            "forecast freshness unchanged; no new training"
        ),
        limitations=[
            "three quarantined pools",
            "two stale pools with unknown cause",
            "Mac awake/login/network required",
            "no promise of continuous uptime",
        ],
    )
    publish(BASE / "work/brain-b04/activation-review.json", receipt)
    publish(ROOT / "service-activation.json", receipt)
    check_activation(ROOT, dep["pin"])
    target = Path.home() / "Library/LaunchAgents/ai.memecoin.brain.b04.plist"
    assert not target.exists()
    with target.open("xb") as stream:
        stream.write(staged.read_bytes())
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)],
        capture_output=True,
        text=True,
    )
    publish(
        BASE / "work/brain-b04/install-result.json",
        dict(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            plist=str(target),
            sha=sha(target),
            at_ns=time.time_ns(),
        ),
    )
    if result.returncode:
        raise RuntimeError("launchctl_bootstrap_failed_no_retry")
    print(dict(installed=True, root=str(ROOT), pin=dep["pin"], request_debit=total, training=False))


if __name__ == "__main__":
    main()
