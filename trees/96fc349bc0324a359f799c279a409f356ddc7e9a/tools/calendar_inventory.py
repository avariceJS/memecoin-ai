"""Read-only current inventory of sealed calendar windows; future windows stay pending."""

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from observed_execution import verify as verify_execution

from memecoin_ai.corpus_quality import verify_quality
from memecoin_ai.coverage import verify_support
from memecoin_ai.domain import ValidationError, digest
from memecoin_ai.execution_targets import verify_execution_targets
from memecoin_ai.prospective import hashes, read, verify_campaign


def iso(ns):
    return datetime.fromtimestamp(ns / 1e9, tz=UTC).isoformat()


def inventory(root):
    plan = read(root / "plan.json")
    if digest((root / "plan.json").read_bytes()) != read(root / "plan-sha.json"):
        raise ValidationError("calendar plan checksum differs")
    windows = []
    hours = set()
    days = set()
    for index, due in enumerate(plan["due_ns"]):
        path = root / "windows" / f"{index:03}"
        out = root / "outcomes" / f"{index:03}.json"
        row = dict(
            index=index, due_utc=iso(due), status="pending" if not path.exists() else "reserved"
        )
        if out.exists():
            row.update(read(out))
            if (path / "seal.json").exists():
                seal = read(path / "seal.json")
                actual = hashes(path)
                actual.pop("seal.json")
                if actual != seal["files"]:
                    raise ValidationError("calendar window seal mismatch")
                if seal["outcome"] != read(out):
                    raise ValidationError("calendar outcome differs from sealed window")
                row["seal_sha256"] = digest((path / "seal.json").read_bytes())
                campaign = path / "campaign"
                state = verify_campaign(campaign)
                targets = path / "campaign-targets"
                if targets.exists():
                    row["targets"] = verify_execution_targets(campaign, targets)
                    row["quality"] = verify_quality(campaign, targets, path / "campaign-quality")
                    row["support"] = verify_support(campaign, targets, path / "campaign-support")
                row["snapshots"] = state["successful_snapshots"]
                times = []
                for capdir in campaign.glob("segments/*/corpus/captures/*"):
                    receipt = read(capdir / "receipt.json")
                    times.append(receipt["received_ns"])
                # Frame/attempt times also preserve failed window coverage without inventing data.
                if not times:
                    for f in campaign.glob("segments/*/corpus/attempts.jsonl"):
                        times.extend(
                            r["available_ns"]
                            for r in map(json.loads, f.read_text().splitlines())
                            if r["status"] == "advanced"
                        )
                if times:
                    row["first_receipt_utc"] = iso(min(times))
                    row["last_receipt_utc"] = iso(max(times))
                    hours.update(iso(t)[:13] for t in times)
                    days.update(iso(t)[:10] for t in times)
                if (path / "execution/report.json").exists():
                    row["execution"] = verify_execution(path / "execution")
        windows.append(row)
    return dict(
        stage="D43",
        plan_sha256=digest((root / "plan.json").read_bytes()),
        code_commit=plan["code_commit"],
        windows=windows,
        window_statuses=dict(Counter(r["status"] for r in windows)),
        accepted_snapshots=sum(w.get("snapshots") or 0 for w in windows),
        observed_utc_hours=sorted(hours),
        observed_utc_days=sorted(days),
        calendar_complete=all(w["status"] in ("completed", "missed", "failed") for w in windows),
        training_ready=False,
        execution_qualified=False,
        blockers=[
            "fixed cohort; no qualified market universe or regime coverage",
            "per-window cutoffs are not a global ML split or protected holdout",
            "calendar breadth and independent target support still need assessment",
            "own admission/inclusion, full costs, FX/rent/impact uncalibrated",
        ],
        seed=None,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    a = p.parse_args()
    print(json.dumps(inventory(a.root), indent=2))


if __name__ == "__main__":
    main()
