"""One new B06 allocation; no inherited receipt/budget reuse or service activation."""

import sys
import time
from pathlib import Path

from memecoin_collector.qualification import endpoint, identity
from memecoin_ml.data import read, sha
from memecoin_operational.supervisor import Watch, child
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE / "work/brain-b06/indexed01"


def main():
    settings = BASE / ".env.local"
    url = endpoint(settings)
    attestation = BASE / "docs/evidence/f01-d51-owner-attestation-2026-10-04.json"
    if identity(url) != read(attestation)["credential_sha256"]:
        raise ValueError("owner_mapping_changed")
    prior = read(BASE / "work/brain-b05/raw01/binding.json")
    cohort = prior["cohort"][:2]
    pages = read(BASE / "work/brain-b05/raw01/result.json")["pages"]
    ROOT.mkdir(exist_ok=False)
    publish(
        ROOT / "binding.json",
        dict(
            scope="B06_one_indexed_probe",
            cohort=cohort,
            anchors={p: pages[p]["rows"][0]["signature"] for p in cohort},
            settings=str(settings),
            credential_sha=identity(url),
            attestation_sha=sha(attestation),
            created_ns=time.time_ns(),
            expires_ns=time.time_ns() + 120 * 10**9,
            max_RPC=5,
            max_nominal_CU=440,
            paid_USD=0,
            no_paid_basis=(
                "same-day owner no-PAYG/no-card, unchanged credential; "
                "new B06 autonomous read-only allocation"
            ),
            previous_allocation_reused=False,
            plan_commit="eafed8e",
            mcp_usage=dict(
                amount=8520,
                usd="0.00",
                dataThrough="2026-10-04T09:36:00.000Z",
                provider_plan_verified=False,
            ),
            code={str(p): sha(p) for p in (BASE / "src/memecoin_pumpswap").glob("*.py")},
            protected_data_access=False,
            training=False,
            trades=False,
        ),
    )
    publish(ROOT / "allocation.json", dict(max_RPC=5, max_CU=440, no_retry=True))
    watch = Watch(120)

    def guard():
        if watch.remaining() <= 0:
            raise ValueError("probe_wall_budget")

    result = child(
        [sys.executable, "-B", "-m", "memecoin_pumpswap.indexed", str(ROOT)],
        ROOT.parent / "indexed01.log",
        120,
        guard,
    )
    publish(ROOT.parent / "indexed01-process.json", result)
    print(result)


if __name__ == "__main__":
    main()
