"""Prerecorded program/date cohort: bounded read-only historical qualification."""

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

from memecoin_collector.qualification import endpoint, identity
from memecoin_ml.data import read, sha
from memecoin_operational.supervisor import child
from memecoin_pumpswap.decoder import PROGRAM, decode
from memecoin_pumpswap.indexed import IndexedRPC
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]


def worker(root):
    from memecoin_operational.worker import lease_watchdog

    lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
    binding = read(root / "binding.json")
    url = endpoint(BASE / ".env.local")
    if identity(url) != binding["credential_sha"] or sha(Path(__file__)) != binding["worker_sha"]:
        raise ValueError("probe_binding")
    client = IndexedRPC(
        url, "vacuum-alchemy-qualification", timeout=15, max_bytes=10 * 1024**2, keep_alive=True
    )
    (root / "attempts").mkdir()
    calls, used = 0, 0
    pages = []

    def call(method, params):
        nonlocal calls, used
        cost = 100 if method == "getTransactionsForAddress" else 40
        if calls >= 12 or used + cost > 960 or time.time_ns() >= binding["expires_ns"]:
            raise ValueError("probe_budget")
        if calls:
            time.sleep(1)
        p = root / "attempts" / f"{calls:06d}"
        p.mkdir()
        publish(p / "reservation.json", dict(method=method, cu=cost, started_ns=time.time_ns()))
        calls += 1
        used += cost
        cap = client.fetch(method, params)
        cap.save(p / "capture")
        publish(p / "availability.json", dict(available_ns=time.time_ns()))
        seal(p)
        return cap.result(), p

    try:
        for case in binding["cases"]:
            start, address = case["start"], case["address"]
            cursor = None
            first = None
            seen = set()
            for _ in range(2):
                cfg = dict(
                    transactionDetails="full",
                    encoding="jsonParsed",
                    commitment="finalized",
                    maxSupportedTransactionVersion=0,
                    limit=100,
                    sortOrder="asc",
                    filters=dict(blockTime=dict(gte=start, lt=start + 86400)),
                )
                if cursor is not None:
                    cfg["paginationToken"] = cursor
                value, p = call("getTransactionsForAddress", [address, cfg])
                rows = value.get("data", value.get("transactions"))
                if not isinstance(rows, list) or len(rows) > 100:
                    raise ValueError("indexed_schema")
                next_cursor = value.get("paginationToken")
                if next_cursor is not None and (
                    not isinstance(next_cursor, str) or next_cursor == cursor
                ):
                    raise ValueError("cursor_nonprogress")
                statuses = {}
                pools = set()
                for tx in rows:
                    sig = tx["transaction"]["signatures"][0]
                    keys = [
                        k["pubkey"] if isinstance(k, dict) else k
                        for k in tx["transaction"]["message"]["accountKeys"]
                    ]
                    if (
                        sig in seen
                        or not start <= tx["blockTime"] < start + 86400
                        or address not in keys
                    ):
                        raise ValueError("indexed_membership")
                    seen.add(sig)
                    first = first or tx
                    decoded = decode(tx)
                    statuses[decoded["status"]] = statuses.get(decoded["status"], 0) + 1
                    pools.update(s["roles"]["pool"] for s in decoded["swaps"] if "roles" in s)
                pages.append(
                    dict(
                        day=start,
                        capture=str(p.resolve()),
                        rows=len(rows),
                        statuses=statuses,
                        referenced_swap_pools=sorted(pools),
                        has_more=next_cursor is not None,
                        bytes=sum(f.stat().st_size for f in (p / "capture").iterdir()),
                    )
                )
                cursor = next_cursor
                if cursor is None:
                    break
            if first is not None:
                standard, _ = call(
                    "getTransaction",
                    [
                        first["transaction"]["signatures"][0],
                        dict(
                            commitment="finalized",
                            encoding="jsonParsed",
                            maxSupportedTransactionVersion=0,
                        ),
                    ],
                )
                if not isinstance(standard, dict) or any(
                    standard.get(k) != first.get(k)
                    for k in ["slot", "blockTime", "transaction", "meta", "version"]
                ):
                    raise ValueError("standard_core_mismatch")
        publish(
            root / "result.json",
            dict(
                pages=pages,
                calls=calls,
                nominal_cu=used,
                training_eligible=False,
                history_complete=False,
            ),
        )
    except Exception as exc:
        publish(
            root / "STOP",
            dict(error_type=type(exc).__name__, calls=calls, nominal_cu=used, no_retry=True),
        )
    finally:
        client.close()
        seal(root)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--worker", action="store_true")
    p.add_argument("--control-accounts", action="store_true")
    p.add_argument("--migration-cohort", action="store_true")
    a = p.parse_args()
    if a.worker:
        worker(a.root)
        return
    owner = BASE / "docs/evidence/f01-d51-owner-attestation-2026-10-04.json"
    credential = identity(endpoint(BASE / ".env.local"))
    if credential != read(owner)["credential_sha256"]:
        raise ValueError("owner_mapping_changed")
    a.root.mkdir(parents=True, exist_ok=False)
    days = [
        int(datetime.datetime.fromisoformat(d + "T00:00:00+00:00").timestamp())
        for d in ["2025-04-01", "2025-07-01", "2026-01-01", "2026-09-01"]
    ]
    target = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg" if a.migration_cohort else PROGRAM
    publish(
        a.root / "binding.json",
        dict(
            days=days,
            cases=(
                [
                    dict(address=x, start=days[0])
                    for x in [
                        "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw",
                        "GS4CU59F31iL7aR2Q8zVS8DRrcRnXX1yjQ66TqNVQnaR",
                        "2AXXcN6oN9bBT5owwmTH53C7QHUXvhLeu718Kqt8rvY2",
                    ]
                ]
                + [dict(address="ChhuEYyL8QeZq6YDVivaMhNibxmEfZVyttp1crXVngpo", start=1791072000)]
                if a.control_accounts
                else [dict(address=target, start=d) for d in days]
            ),
            program=PROGRAM,
            credential_sha=credential,
            owner_attestation_sha=sha(owner),
            worker_sha=sha(Path(__file__)),
            expires_ns=time.time_ns() + 300 * 10**9,
            plan_commit="520731f",
            paid_usd=0,
            max_rpc=12,
            max_cu=960,
        ),
    )
    result = child(
        [sys.executable, "-B", str(Path(__file__).resolve()), str(a.root.resolve()), "--worker"],
        a.root.parent / (a.root.name + "-worker.log"),
        300,
        lambda: None,
    )
    publish(a.root.parent / (a.root.name + "-process.json"), result)
    if not (a.root / "manifest.json").exists():
        if not (a.root / "STOP").exists():
            publish(a.root / "STOP", dict(category="subprocess_failure", result=result))
        seal(a.root)
    print(json.dumps(dict(root=str(a.root), stopped=(a.root / "STOP").exists(), process=result)))


if __name__ == "__main__":
    main()
