"""One reviewed B05 allocation, isolated from the D51 receipt and Brain bar collector."""

import sys
import time
from pathlib import Path

from memecoin_collector.qualification import endpoint, identity
from memecoin_ml.data import read, sha
from memecoin_operational.supervisor import Watch, child
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE / "work/brain-b05/raw01"


def main():
    attestation = read(BASE / "docs/evidence/f01-d51-owner-attestation-2026-10-04.json")
    settings = BASE / ".env.local"
    credential = endpoint(settings)
    assert identity(credential) == attestation["credential_sha256"]
    universe = read(BASE / "work/brain-b04/prospective01/universe.json")
    pools = [p["pool"] for p in universe["pools"]]
    prefixes = ["58hou", "8E1Z", "FXgV", "Chhu"]
    cohort = [next(p for p in pools if p.startswith(prefix)) for prefix in prefixes]
    ROOT.mkdir(exist_ok=False)
    binding = dict(
        scope="B05_one_raw_diagnostic",
        settings=str(settings),
        credential_sha=identity(credential),
        universe=pools,
        cohort=cohort,
        created_ns=time.time_ns(),
        expires_ns=time.time_ns() + 180 * 10**9,
        max_RPC=13,
        max_nominal_CU=500,
        paid_USD=0,
        training=False,
        trades=False,
        D51_receipt_reused=False,
        no_paid_basis=(
            "same-day owner no-PAYG/no-card attestation, same credential, "
            "new autonomous B05 allocation"
        ),
        attestation_sha=sha(BASE / "docs/evidence/f01-d51-owner-attestation-2026-10-04.json"),
        mcp_usage_observation=dict(
            amount=8020,
            unit="CU",
            usd="0.00",
            dataThrough="2026-10-04T09:04:00.000Z",
            independent_plan_verification=False,
        ),
        plan_commit="fba806a",
        code={p.name: sha(p) for p in (BASE / "src/memecoin_brain_sources").glob("*.py")},
    )
    publish(ROOT / "binding.json", binding)
    publish(
        ROOT / "allocation.json",
        dict(max_RPC=13, max_CU=500, new_B05_allocation=True, no_retry=True),
    )
    watch = Watch(180)

    def guard():
        if watch.remaining() <= 0:
            raise ValueError("probe_wall_budget")

    result = child(
        [sys.executable, "-B", "-m", "memecoin_brain_sources.probe", str(ROOT)],
        ROOT.parent / "raw01.log",
        180,
        guard,
    )
    publish(ROOT.parent / "raw01-process.json", result)
    print(result)


if __name__ == "__main__":
    main()
