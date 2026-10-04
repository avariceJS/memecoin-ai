"""Bounded 2025 archival qualification; no GT requests, no trade calls, no training admission."""

import json
import time
from pathlib import Path

from memecoin_collector.qualification import endpoint, identity
from memecoin_ml.data import read, sha
from memecoin_operational.supervisor import child
from memecoin_pumpswap.indexed import IndexedRPC
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE / "work/brain-b11/archive-probe02"


def worker():
    import os

    from memecoin_operational.worker import lease_watchdog

    lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
    binding = read(ROOT / "binding.json")
    if sha(Path(__file__)) != binding["worker_sha"]:
        raise ValueError("worker_binding")
    url = endpoint(BASE / ".env.local")
    if identity(url) != binding["credential_sha"] or time.time_ns() >= binding["expires_ns"]:
        raise ValueError("credential_or_expiry")
    client = IndexedRPC(
        url, "b11-alchemy-archive", timeout=10, max_bytes=8 * 1024**2, keep_alive=True
    )
    (ROOT / "attempts").mkdir()
    used, calls, first, pages = 0, 0, None, []

    def call(method, params):
        nonlocal used, calls
        cost = 100 if method == "getTransactionsForAddress" else 40
        if calls >= 5 or used + cost > 440 or time.time_ns() >= binding["expires_ns"]:
            raise ValueError("probe_budget")
        if calls:
            time.sleep(1)
        p = ROOT / "attempts" / f"{calls:06d}"
        p.mkdir()
        used += cost
        calls += 1
        publish(p / "reservation.json", dict(method=method, cu=cost, started_ns=time.time_ns()))
        cap = client.fetch(method, params)
        cap.save(p / "capture")
        publish(p / "availability.json", dict(available_ns=time.time_ns()))
        seal(p)
        return cap.result()

    try:
        for pool in binding["cohort"]:
            cursor, seen = None, set()
            for _ in range(2):
                cfg = dict(
                    transactionDetails="full",
                    encoding="jsonParsed",
                    commitment="finalized",
                    maxSupportedTransactionVersion=0,
                    limit=100,
                    sortOrder="asc",
                    filters=dict(blockTime=dict(gte=1735689600, lt=1767225600)),
                )
                if cursor:
                    cfg["paginationToken"] = cursor
                value = call("getTransactionsForAddress", [pool, cfg])
                field = "transactions" if "transactions" in value else "data"
                rows = value.get(field)
                if not isinstance(rows, list) or len(rows) > 100:
                    raise ValueError("archive_response_schema")
                times = []
                for tx in rows:
                    sig = tx["transaction"]["signatures"][0]
                    keys = tx["transaction"]["message"]["accountKeys"]
                    if pool not in [k["pubkey"] if isinstance(k, dict) else k for k in keys]:
                        raise ValueError("pool_reference")
                    if sig in seen or not 1735689600 <= tx["blockTime"] < 1767225600:
                        raise ValueError("duplicate_or_time_filter")
                    seen.add(sig)
                    times.append(tx["blockTime"])
                    if first is None:
                        first = tx
                if times != sorted(times):
                    raise ValueError("ascending_order")
                next_cursor = value.get("paginationToken")
                if next_cursor is not None and (
                    not isinstance(next_cursor, str) or next_cursor == cursor
                ):
                    raise ValueError("cursor_no_progress")
                pages.append(
                    dict(
                        pool=pool,
                        rows=len(rows),
                        response_field=field,
                        earliest=min(times, default=None),
                        latest=max(times, default=None),
                        has_more=next_cursor is not None,
                    )
                )
                cursor = next_cursor
                if not cursor:
                    break
        equal = None
        if first is not None:
            standard = call(
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
            equal = isinstance(standard, dict) and all(
                standard.get(k) == first.get(k)
                for k in ("slot", "blockTime", "transaction", "meta", "version")
            )
            if not equal:
                raise ValueError("standard_core_mismatch")
        result = dict(
            pages=pages,
            attempts=calls,
            nominal_cu=used,
            core_equal=equal,
            history_complete=False,
            training_eligible=False,
            paid_usd=0,
        )
        publish(ROOT / "result.json", result)
        print(json.dumps(result), flush=True)
    except Exception as exc:
        # The RPC body is immutable evidence on disk; never print provider messages/endpoints.
        failure = dict(
            category="bounded_archive_qualification",
            error_type=type(exc).__name__,
            http_status=getattr(exc, "status", None),
            attempts=calls,
            nominal_cu=used,
            no_retry=True,
        )
        publish(ROOT / "STOP", failure)
        print(json.dumps(failure), flush=True)
    finally:
        client.close()
        seal(ROOT)


def main():
    url = endpoint(BASE / ".env.local")
    owner = BASE / "docs/evidence/f01-d51-owner-attestation-2026-10-04.json"
    if identity(url) != read(owner)["credential_sha256"]:
        raise ValueError("owner_mapping_changed")
    pools = read(BASE / "work/brain-b09/source01/universe.json")["pools"]
    cohort = sorted(
        [u for u in pools if 0 < u["pool_created_ns"] < 1767225600 * 10**9],
        key=lambda u: (u["pool_created_ns"], u["pool"]),
    )[:2]
    if len(cohort) != 2:
        raise ValueError("insufficient_old_cohort")
    ROOT.mkdir(parents=True, exist_ok=False)
    publish(
        ROOT / "binding.json",
        dict(
            cohort=[u["pool"] for u in cohort],
            creation_metadata=cohort,
            credential_sha=identity(url),
            expires_ns=time.time_ns() + 120 * 10**9,
            created_ns=time.time_ns(),
            max_RPC=5,
            max_nominal_CU=440,
            paid_usd=0,
            owner_attestation_sha=sha(owner),
            owner_no_paid_not_independent_verification=True,
            previous_allocation_reused=False,
            preflight_parent="archive-probe01",
            parent_rpc_attempts=0,
            mcp_usage=dict(
                cu=4480, usd=0, dataThrough="2026-10-04T17:06:00Z", not_charge_delta=True
            ),
            plan_commit="d48e99e",
            worker_sha=sha(Path(__file__)),
            training_eligible=False,
        ),
    )
    import sys

    result = child(
        [sys.executable, "-B", str(Path(__file__).resolve()), "worker"],
        ROOT.parent / "archive-probe02.log",
        120,
        lambda: None,
    )
    publish(ROOT.parent / "archive-probe02-process.json", result)
    if not (ROOT / "manifest.json").exists():
        if not (ROOT / "STOP").exists():
            publish(
                ROOT / "STOP",
                dict(result, category="subprocess_failure", child_category=result["category"]),
            )
        seal(ROOT)
    print(json.dumps(result))


if __name__ == "__main__":
    import sys

    worker() if sys.argv[-1] == "worker" else main()
