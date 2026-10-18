"""One bounded read-only raw lane; every attempted call is durably charged before network."""

import json
import time
from pathlib import Path

from memecoin_ai.sources import HTTPSourceError, ReadOnlyRPC, TransportSourceError
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import Ledger, publish

from .events import ingest, transaction_evidence, view

COSTS = {"getMultipleAccounts": 20, "getSignaturesForAddress": 40, "getTransaction": 40}


def run(root: Path, client: ReadOnlyRPC, *, pacing=1.0) -> dict:
    binding = read(root / "binding.json")
    begin = time.monotonic()
    (root / "attempts").mkdir(exist_ok=False)

    def call(method, params):
        attempts = sorted((root / "attempts").iterdir())
        used = sum(read(p / "reservation.json")["cu"] for p in attempts)
        if method not in COSTS or len(attempts) >= 13 or used + COSTS[method] > 500:
            raise ValueError("RPC_budget")
        if time.monotonic() - begin >= 175:
            raise TimeoutError("probe_deadline")
        if attempts:
            elapsed = (time.time_ns() - read(attempts[-1] / "reservation.json")["started_ns"]) / 1e9
            time.sleep(max(0, pacing - elapsed))
        p = root / "attempts" / f"{len(attempts):06d}"
        p.mkdir()
        publish(
            p / "reservation.json", dict(method=method, cu=COSTS[method], started_ns=time.time_ns())
        )
        try:
            cap = client.fetch(method, params)
            cap.save(p / "capture")
            publish(p / "availability.json", dict(available_ns=time.time_ns()))
            seal(p)
            return cap.result(), p
        except Exception as exc:
            failure = dict(
                error_type=type(exc).__name__,
                category="http"
                if isinstance(exc, HTTPSourceError)
                else "transport_" + exc.reason
                if isinstance(exc, TransportSourceError)
                else "source_or_integrity",
                status=getattr(exc, "status", None),
                no_retry=True,
            )
            publish(root / "STOP", failure)
            raise RuntimeError(failure["category"]) from None

    inventory, pages, txs = [], {}, []
    try:
        result, path = call(
            "getMultipleAccounts",
            [
                binding["universe"],
                dict(commitment="finalized", encoding="base64", dataSlice=dict(offset=0, length=0)),
            ],
        )
        context = result["context"]["slot"]
        if len(result["value"]) != len(binding["universe"]):
            raise ValueError("account_membership")
        for pool, account in zip(binding["universe"], result["value"], strict=True):
            inventory.append(
                dict(
                    pool=pool,
                    owner=account["owner"] if account else None,
                    context_slot=context,
                    checkpoint=False,
                )
            )
        for pool in binding["cohort"]:
            rows = []
            cursor = None
            refs = []
            for _ in range(2):
                config = dict(commitment="finalized", limit=20, minContextSlot=context)
                if cursor:
                    config["before"] = cursor
                page, path = call("getSignaturesForAddress", [pool, config])
                if not isinstance(page, list) or len(page) > 20:
                    raise ValueError("signature_schema")
                if any(a["slot"] < b["slot"] for a, b in zip(page, page[1:], strict=False)):
                    raise ValueError("signature_order")
                if rows and page and page[0]["slot"] > rows[-1]["slot"]:
                    raise ValueError("page_after_cursor")
                seen = {r["signature"]: r for r in rows}
                for row in page:
                    if row["signature"] in seen and seen[row["signature"]] != row:
                        raise ValueError("signature_revision")
                    if row["signature"] not in seen:
                        rows.append(row)
                        seen[row["signature"]] = row
                refs.append(
                    dict(
                        path=str(path.resolve()),
                        manifest_sha=sha(path / "manifest.json"),
                        returned=len(page),
                    )
                )
                if not page:
                    break
                new_cursor = page[-1]["signature"]
                if cursor == new_cursor:
                    raise ValueError("cursor_no_progress")
                cursor = new_cursor
            pages[pool] = dict(
                rows=rows,
                captures=refs,
                coverage="bounded_account_references_only",
                history_complete=False,
            )
            latest = next((r for r in rows if r["err"] is None), None)
            if latest:
                _, path = call(
                    "getTransaction",
                    [
                        latest["signature"],
                        dict(
                            commitment="finalized",
                            encoding="jsonParsed",
                            maxSupportedTransactionVersion=0,
                        ),
                    ],
                )
                txs.append(dict(pool=pool, evidence=transaction_evidence(path)))
        with Ledger(
            root / "events",
            dict(scope="B05_raw_evidence_only", binding_sha=sha(root / "binding.json")),
        ) as ledger:
            for t in txs:
                ingest(ledger, t["evidence"])
            event_view = view(ledger, time.time_ns())
        result = dict(
            inventory=inventory,
            pages=pages,
            transactions=txs,
            event_view=event_view,
            attempts=len(list((root / "attempts").iterdir())),
            nominal_cu=sum(
                read(p / "reservation.json")["cu"] for p in (root / "attempts").iterdir()
            ),
            elapsed_seconds=time.monotonic() - begin,
            decoder_qualified=False,
            training_eligible=False,
        )
        publish(root / "result.json", result)
        seal(root)
        return result
    except Exception as exc:
        if not (root / "STOP").exists():
            publish(
                root / "STOP",
                dict(category="integrity_or_bound", error_type=type(exc).__name__, no_retry=True),
            )
        raise
    finally:
        client.close()


if __name__ == "__main__":
    import os
    import sys

    from memecoin_collector.qualification import endpoint, identity
    from memecoin_operational.worker import lease_watchdog

    root = Path(sys.argv[1])
    lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
    try:
        binding = read(root / "binding.json")
        url = endpoint(Path(binding["settings"]))
        if identity(url) != binding["credential_sha"]:
            raise ValueError("credential_changed")
        if time.time_ns() > binding["expires_ns"]:
            raise ValueError("probe_expired")
        result = run(
            root,
            ReadOnlyRPC(url, "brain-b05-alchemy", timeout=10, max_bytes=1000000, keep_alive=True),
        )
        print(
            json.dumps(
                dict(
                    status="completed", attempts=result["attempts"], nominal_cu=result["nominal_cu"]
                )
            ),
            flush=True,
        )
    except Exception as exc:
        print(
            json.dumps(
                dict(status="failed", category="bounded_raw_probe", error_type=type(exc).__name__)
            ),
            flush=True,
        )
        raise SystemExit(1) from None
