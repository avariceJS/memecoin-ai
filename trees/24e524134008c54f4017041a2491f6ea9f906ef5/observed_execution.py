"""Read-only address-related fee/commitment evidence, not own-order calibration."""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from cold_connection import settings

from memecoin_ai.domain import ValidationError, digest
from memecoin_ai.prospective import hashes, read, write
from memecoin_ai.sources import Capture, ReadOnlyRPC


def select(captures, limit):
    selected = {}
    for cap in captures:
        for row in cap.result():
            sig = row["signature"]
            if sig in selected and selected[sig]["slot"] != row["slot"]:
                raise ValidationError("signature slot differs across address observations")
            selected.setdefault(
                sig,
                dict(
                    signature=sig,
                    slot=row["slot"],
                    first_seen_ns=cap.received_ns,
                    listed_error=row["err"],
                    block_time=row["blockTime"],
                ),
            )
    return sorted(selected.values(), key=lambda r: (-r["slot"], r["signature"]))[:limit]


def transaction_evidence(cap, candidate):
    if cap.received_ns < candidate["first_seen_ns"]:
        raise ValidationError("transaction receipt precedes signature discovery")
    tx = cap.result()
    if tx is None:
        return dict(
            signature=candidate["signature"],
            status="unavailable_at_finalized",
            available_ns=cap.received_ns,
        )
    if (
        tx["slot"] != candidate["slot"]
        or tx["transaction"]["signatures"][0] != candidate["signature"]
    ):
        raise ValidationError("transaction/signature observation linkage differs")
    meta = tx["meta"]
    if meta is None:
        return dict(
            signature=candidate["signature"], status="missing_meta", available_ns=cap.received_ns
        )
    fee = meta["fee"]
    if isinstance(fee, bool) or not isinstance(fee, int) or fee < 0:
        raise ValidationError("invalid observed transaction fee")
    if meta["err"] != candidate["listed_error"]:
        raise ValidationError("listed/finalized execution error differs")
    blocktime = tx.get("blockTime")
    return dict(
        signature=candidate["signature"],
        slot=tx["slot"],
        status="observed",
        failed=meta["err"] is not None,
        error=meta["err"],
        fee_lamports=fee,
        compute_units=meta.get("computeUnitsConsumed"),
        cost_units=meta.get("costUnits"),
        block_time=blocktime,
        available_ns=cap.received_ns,
        receipt_minus_block_time_seconds=None
        if blocktime is None
        else cap.received_ns / 1e9 - blocktime,
        submission_to_inclusion_seconds=None,
        priority_fee_lamports=None,
        total_strategy_cost=None,
        classification="address_related; swap not established",
    )


def timing(candidate, polls):
    values = []
    for cap in polls:
        floor = values[-1]["available_ns"] if values else candidate["first_seen_ns"]
        if cap.received_ns < floor:
            raise ValidationError("status receipt chronology regressed")
        req = json.loads(cap.request_json)
        idx = req["params"][0].index(candidate["signature"])
        result = cap.result()
        row = result["value"][idx]
        if row is not None and row["slot"] != candidate["slot"]:
            raise ValidationError("status/signature slot differs")
        values.append(
            dict(
                available_ns=cap.received_ns,
                status=None if row is None else row["confirmationStatus"],
            )
        )
    fin = next((v["available_ns"] for v in values if v["status"] == "finalized"), None)
    if values and values[0]["status"] == "finalized":
        censor = "left_censored"
    elif fin is None:
        censor = "right_censored"
    else:
        censor = "observed_between_polls"
    # Observation elapsed time only. Listing was already confirmed, submission time unknown.
    return dict(
        signature=candidate["signature"],
        first_seen_ns=candidate["first_seen_ns"],
        polls=values,
        censoring=censor,
        first_finalized_observed_ns=fin,
        observed_elapsed_seconds=None if fin is None else (fin - candidate["first_seen_ns"]) / 1e9,
        inclusion_latency_seconds=None,
    )


def analyze(root):
    spec = read(root / "plan.json")
    captures = [Capture.load(p) for p in sorted(root.glob("capture-*"))]
    listings = [c for c in captures if c.method == "getSignaturesForAddress"]
    selected = select(listings, spec["execution"]["transaction_limit"])
    polls = [c for c in captures if c.method == "getSignatureStatuses"]
    bysig = {r["signature"]: r for r in selected}
    tx = [
        transaction_evidence(c, bysig[json.loads(c.request_json)["params"][0]])
        for c in captures
        if c.method == "getTransaction"
    ]
    timing_rows = [timing(r, polls) for r in selected] if polls else []
    observed = [r for r in tx if r["status"] == "observed"]
    return dict(
        kind="observed_address_execution",
        selected=selected,
        transactions=tx,
        timing=timing_rows,
        counts=dict(
            captures=len(captures),
            selected=len(selected),
            observed=len(observed),
            failed_observed=sum(r["failed"] for r in observed),
            missing_transactions=len(selected) - len(observed),
        ),
        fees_lamports=[r["fee_lamports"] for r in observed],
        timing_censoring=dict(Counter(r["censoring"] for r in timing_rows)),
        source_pinned=True,
        fee_semantics="total observed transaction fee; not total strategy cost",
        inclusion_calibrated=False,
        submission_times_known=False,
        net_pnl=None,
        training_ready=False,
        seed=None,
    )


def collect(root, spec, pools):
    root.mkdir(parents=True, exist_ok=False)
    write(root / "plan.json", spec)
    (root / "audit_source.py").write_bytes(Path(__file__).read_bytes())
    client = ReadOnlyRPC(
        settings()["MEMECOIN_ALCHEMY_HTTP_URL"],
        "execution-alchemy",
        timeout=15,
        max_bytes=1_000_000,
        keep_alive=True,
    )
    if client.endpoint_origin != spec["endpoint_origin"]:
        raise ValidationError("execution origin differs")
    calls = []
    caps = []
    failure = None

    def fetch(method, params):
        if len(calls) >= spec["execution_requests_per_window"]:
            raise ValidationError("execution request budget exhausted")
        row = dict(index=len(calls), method=method, params=params, started_ns=time.time_ns())
        calls.append(row)
        try:
            c = client.fetch(method, params)
            c.save(root / f"capture-{row['index']:02}")
            c.result()
            caps.append(c)
            row.update(status="ok", available_ns=c.received_ns)
            return c
        except Exception as exc:
            row.update(
                status="error",
                category=getattr(exc, "reason", "rpc_or_validation"),
                error_type=type(exc).__name__,
                available_ns=time.time_ns(),
            )
            print(json.dumps(row), flush=True)
            raise
        finally:
            row["transport"] = client.transport_measurement()
            with (root / "attempts.jsonl").open("a") as f:
                f.write(json.dumps(row) + "\n")

    try:
        listings = [
            fetch("getSignaturesForAddress", [p, dict(commitment="confirmed", limit=8)])
            for p in pools
        ]
        selected = select(listings, 6)
        write(root / "selection.json", selected)
        if selected:
            start = time.monotonic()
            for offset in spec["execution"]["status_offsets_seconds"]:
                time.sleep(max(0, start + offset - time.monotonic()))
                fetch(
                    "getSignatureStatuses",
                    [[r["signature"] for r in selected], dict(searchTransactionHistory=False)],
                )
            for r in selected:
                cap = fetch(
                    "getTransaction",
                    [
                        r["signature"],
                        dict(
                            commitment="finalized",
                            encoding="json",
                            maxSupportedTransactionVersion=0,
                        ),
                    ],
                )
                transaction_evidence(cap, r)
    except Exception as exc:
        failure = dict(
            category=getattr(exc, "reason", "rpc_or_validation"), error_type=type(exc).__name__
        )
    finally:
        client.close()
    write(
        root / "completion.json",
        dict(complete=failure is None, failure=failure, requests=len(calls)),
    )
    try:
        write(root / "report.json", analyze(root))
    finally:
        write(root / "manifest.json", hashes(root))
    return failure is None


def verify(root):
    files = hashes(root)
    files.pop("manifest.json")
    if files != read(root / "manifest.json") or digest(
        (root / "audit_source.py").read_bytes()
    ) != digest(Path(__file__).read_bytes()):
        raise ValidationError("execution evidence/source mismatch")
    result = analyze(root)
    if result != read(root / "report.json"):
        raise ValidationError("execution reproduction differs")
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["collect", "verify"])
    p.add_argument("root", type=Path)
    p.add_argument("--plan", type=Path)
    p.add_argument("--campaign", type=Path)
    a = p.parse_args()
    if a.action == "collect":
        ok = collect(a.root, read(a.plan)["spec"], read(a.campaign / "plan.json")["pools"])
        raise SystemExit(0 if ok else 1)
    print(json.dumps(verify(a.root), indent=2))


if __name__ == "__main__":
    main()
