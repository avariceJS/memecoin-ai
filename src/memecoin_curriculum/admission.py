"""Birth-selected USD bars admitted without inventing missing intervals or online receipts."""

import json
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from memecoin_brain.data import build
from memecoin_brain.source import EXCLUDED, bars
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import publish

NAMESPACE = "geckoterminal/usd/base/5min/first-observed"


def qualify(capture, birth):
    verify(capture)
    if birth["pool"] in EXCLUDED:
        raise ValueError("protected_pool")
    request, receipt = read(capture / "request.json"), read(capture / "receipt.json")
    if receipt.get("response_sha256") != sha(capture / "response.bin"):
        raise ValueError("admission_response_binding")
    url = urlparse(request["url"])
    query = parse_qs(url.query)
    expected = dict(aggregate="5", currency="usd", token="base", include_empty_intervals="false")
    if (
        url.netloc != "api.geckoterminal.com"
        or url.path != f"/api/v2/networks/solana/pools/{birth['pool']}/ohlcv/minute"
        or any(query.get(k) != [v] for k, v in expected.items())
        or query.get("before_timestamp") != [str(birth["event_s"] + 3 * 86400)]
    ):
        raise ValueError("admission_price_namespace")
    if not request["started_ns"] <= receipt["received_ns"] <= receipt["available_ns"]:
        raise ValueError("admission_receipt_order")
    payload = read(capture / "response.bin")
    metadata = payload.get("meta", {})
    if any(
        metadata.get(side, {}).get("address") != birth[side + "_mint"] for side in ("base", "quote")
    ):
        raise ValueError("admission_birth_mint_mismatch")
    rows = bars(capture, birth["pool"])
    if any(
        not birth["event_s"] // 300 * 300 <= r["open_s"] < birth["event_s"] + 3 * 86400
        for r in rows
    ):
        raise ValueError("admission_birth_range")
    bundle = build(rows, dict(as_of_ns=receipt["available_ns"]))
    eligible = [
        s for s in bundle["samples"] if all(t["log_return"] is not None for t in s["targets"])
    ]
    return rows, dict(
        pool=birth["pool"],
        birth=birth,
        bars=len(rows),
        eligible=len(eligible),
        context_decisions=len(bundle["samples"]),
        missing_targets=len(bundle["samples"]) - len(eligible),
        disjoint_decisions=disjoint(eligible),
        observed_hours=len({r["open_s"] // 3600 for r in rows}),
        observed_pool_days=len({r["open_s"] // 86400 for r in rows}),
        capture=str(capture.resolve()),
        capture_sha=sha(capture / "manifest.json"),
        status="admitted" if eligible else "no_contiguous_targets",
        missing_intervals=max(0, (rows[-1]["open_s"] - rows[0]["open_s"]) // 300 + 1 - len(rows))
        if rows
        else 0,
        complete_market_history=False,
        historical_online_availability_known=False,
    )


def disjoint(samples):
    last, count = {}, 0
    for s in sorted(samples, key=lambda s: (s["pool"], s["event_close_s"])):
        # Eight context frames, each depending on up to eight bars: 15 input bars.
        start = s["event_close_s"] - 15 * 300
        if start >= last.get(s["pool"], -1):
            last[s["pool"]] = s["maturity_s"]
            count += 1
    return count


def compile_corpus(captures, output, *, selection=None):
    """No source mutation. Inputs are (sealed capture, independently qualified birth)."""
    output.mkdir(parents=True, exist_ok=False)
    records, combined, rejected = [], {}, []
    pools = set()
    for capture, birth in captures:
        if birth["pool"] in pools:
            raise ValueError("admission_duplicate_pool")
        pools.add(birth["pool"])
        try:
            rows, report = qualify(capture, birth)
        except ValueError as exc:
            rejected.append(dict(pool=birth["pool"], capture=str(capture), reason=str(exc)))
            continue
        records.append(report)
        for row in rows:
            key = row["pool"], row["open_s"]
            if key in combined and row != combined[key]:
                raise ValueError("admission_conflicting_duplicate")
            combined[key] = row
    rows = [combined[k] for k in sorted(combined)]
    with (output / "rows.jsonl").open("x") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    counts = Counter({r["pool"]: r["eligible"] for r in records if r["eligible"]})
    n = sum(counts.values())
    report = dict(
        version="birth-usd-admission-v1",
        namespace=NAMESPACE,
        records=records,
        rejected=rejected,
        bars=len(rows),
        eligible_decisions=n,
        eligible_pools=len(counts),
        per_pool=dict(counts),
        pools_with_at_least={
            str(k): sum(v >= k for v in counts.values()) for k in (1, 16, 64, 256)
        },
        full_dependency_disjoint_decisions=sum(r["disjoint_decisions"] for r in records),
        largest_pool_share=max(counts.values(), default=0) / n if n else None,
        effective_pools=1 / sum((v / n) ** 2 for v in counts.values()) if n else 0,
        birth_months=dict(Counter(r["birth"]["month"] for r in records if r["eligible"])),
        rows_sha=sha(output / "rows.jsonl"),
        historical_online_availability_known=False,
        current_survivor_filter=False,
        excluded_pools=sorted(EXCLUDED),
        selection=selection or [],
    )
    publish(output / "admission.json", report)
    seal(output)
    return report


def load_corpus(reference, cutoff):
    path = Path(reference["path"])
    if sha(path / "manifest.json") != reference["sha"]:
        raise ValueError("admission_corpus_pin")
    verify(path)
    report = read(path / "admission.json")
    if report["namespace"] != NAMESPACE or report["rows_sha"] != sha(path / "rows.jsonl"):
        raise ValueError("admission_corpus_namespace")
    with (path / "rows.jsonl").open() as stream:
        rows = [json.loads(line) for line in stream]
    pins = {r["capture_sha"] for r in report["records"]}
    if any(
        r["pool"] in EXCLUDED or r["available_ns"] > cutoff or r["capture_sha"] not in pins
        for r in rows
    ):
        raise ValueError("admission_corpus_availability")
    return rows


def merge_history(rows, provenance, references):
    """First receipt wins, later differences remain explicit revision evidence."""
    combined = {(r["pool"], r["open_s"]): r for r in rows}
    revisions = []
    for ref in references:
        for row in load_corpus(ref, provenance["as_of_ns"]):
            key = row["pool"], row["open_s"]
            old = combined.get(key)
            if old and old["ohlcv"] != row["ohlcv"]:
                revisions.append(
                    dict(
                        pool=key[0],
                        open_s=key[1],
                        pins=sorted([old["capture_sha"], row["capture_sha"]]),
                    )
                )
            if old is None or row["available_ns"] < old["available_ns"]:
                combined[key] = row
    merged = [combined[k] for k in sorted(combined)]
    prov = dict(
        provenance,
        historical_training_corpora=references,
        historical_merge_revisions=revisions,
        merged_rows_sha=digest(merged),
    )
    return merged, prov
