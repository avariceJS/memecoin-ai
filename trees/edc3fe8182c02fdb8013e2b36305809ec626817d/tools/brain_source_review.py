"""Read-only forensic source journal slice. No inference of uptime from successful GETs."""

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest
from memecoin_runner.ledger import publish


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--start", type=int, required=True)
    p.add_argument("--end", type=int, required=True)
    a = p.parse_args()
    kinds, statuses, http, per_pool = Counter(), Counter(), Counter(), defaultdict(list)
    events, intervals, times, last, mismatch = [], [], [], None, []
    for path in sorted((a.root / "journal").glob("event-*.json")):
        e = read(path)
        checksum = e["sha256"]
        value = {k: v for k, v in e.items() if k != "sha256"}
        if digest(value) != checksum or (last is not None and e["previous"] != last):
            raise ValueError("source_journal_chain")
        last = checksum
        if e["time_ns"] > a.end:
            break
        if e["time_ns"] < a.start:
            continue
        events.append(dict(path=str(path.resolve()), sha=sha(path)))
        kinds[e["kind"]] += 1
        data = e["payload"]
        if e["kind"] == "outcome":
            status = data["status"]
            statuses[status] += 1
            pool = data["job"]["pool"]
            per_pool[pool].append(e["time_ns"])
            times.append(e["time_ns"])
            capture = Path(data["capture"])
            if sha(capture / "manifest.json") != data["capture_sha"]:
                mismatch.append(str(capture))
            if not (capture / "receipt.json").exists():
                failure = read(capture / "failure.json")
                http[str(failure.get("http_status", failure.get("category", "no_receipt")))] += 1
                continue
            r = read(capture / "receipt.json")
            http[str(r.get("http_status"))] += 1
            if r["available_ns"] < r["received_ns"]:
                raise ValueError("source_availability_order")
    intervals = [(b - a) / 10**9 for a, b in zip(times, times[1:], strict=False)]
    result = dict(
        start_ns=a.start,
        end_ns=a.end,
        kinds=dict(kinds),
        outcomes=dict(statuses),
        http_status=dict(http),
        max_outcome_gap_s=max(intervals, default=None),
        gaps_over_120s=sum(v > 120 for v in intervals),
        per_pool={
            p: dict(
                outcomes=len(v),
                max_gap_s=max(
                    ((b - a) / 10**9 for a, b in zip(v, v[1:], strict=False)), default=None
                ),
                first_ns=v[0],
                last_ns=v[-1],
            )
            for p, v in per_pool.items()
        },
        manifest_mismatches=mismatch,
        evidence=events,
        uptime_proven=False,
        full_raw_replay=False,
    )
    publish(a.output, result)
    print({k: v for k, v in result.items() if k not in ("evidence", "per_pool")})


if __name__ == "__main__":
    main()
