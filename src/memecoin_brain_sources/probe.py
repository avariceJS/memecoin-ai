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


def _new_scope(cohort):
    if len(set(cohort)) != len(cohort):
        raise ValueError("duplicate_planned_pool")
    return dict(
        schema_version=1,
        denominator="ordered_binding_cohort_index_and_pool",
        coverage="bounded_account_references_only",
        history_complete=False,
        global_completeness="unknown",
        planned=len(cohort), attempted=0, completed=0, partial=0, unattempted=0,
        entries=[
            dict(index=i, pool=p, status="unattempted", reason="not_started",
                 enumeration_reserved_calls=0, hydration_reserved=False,
                 validated_responses=0, page_returned_counts=[], raw_count=0,
                 unique_signatures=[], unique_count=0, selected_signature=None,
                 hydration_status="not_selected", hydration_completed=False,
                 further_enumeration="unknown_after_bounded_work")
            for i, p in enumerate(cohort)
        ],
        inventory_status="not_attempted",
        total_reserved_calls=0, nominal_cu=0,
        trust_limit="Only fully validated returned pages count; provider scope is bounded."
    )


def _finish_scope(scope):
    completed = partial = unattempted = attempted = 0
    for e in scope["entries"]:
        if e["status"] == "completed":
            completed += 1
        elif e["status"] == "partial":
            partial += 1
        else:
            unattempted += 1
        if e["enumeration_reserved_calls"]:
            attempted += 1
    scope.update(attempted=attempted, completed=completed, partial=partial,
                 unattempted=unattempted)
    return scope


def _validate_page(page, prior_rows, cursor):
    if not isinstance(page, list) or len(page) > 20:
        raise ValueError("signature_schema")
    staged = []
    seen = {r["signature"]: r for r in prior_rows}
    previous = None
    for row in page:
        if not isinstance(row, dict) or not {"signature", "slot", "err"} <= row.keys():
            raise ValueError("signature_schema")
        if (not isinstance(row["signature"], str) or not row["signature"]
                or type(row["slot"]) is not int or row["slot"] < 0
                or ("blockTime" in row and row["blockTime"] is not None and type(row["blockTime"]) is not int)):
            raise ValueError("signature_schema")
        if previous is not None and row["slot"] > previous:
            raise ValueError("signature_order")
        if previous is not None and row["signature"] == staged[-1]["signature"] and staged[-1] != row:
            raise ValueError("signature_revision")
        previous = row["slot"]
        old = seen.get(row["signature"])
        if old is not None and old != row:
            raise ValueError("signature_revision")
        if prior_rows and not staged and row["slot"] > prior_rows[-1]["slot"]:
            raise ValueError("page_after_cursor")
        staged.append(dict(row))
        if old is None:
            seen[row["signature"]] = row
    if cursor is not None and page and page[-1]["signature"] == cursor:
        raise ValueError("cursor_no_progress")
    return staged


def run(root: Path, client: ReadOnlyRPC, *, pacing=1.0) -> dict:
    binding = read(root / "binding.json")
    cohort = binding["cohort"]
    scope = _new_scope(cohort)
    begin = time.monotonic()
    (root / "attempts").mkdir(exist_ok=False)
    inventory, pages, txs = [], {}, []
    context = None

    def attempts_now():
        return sorted((root / "attempts").iterdir())

    def stop(exc, category=None):
        if (root / "STOP").exists():
            return
        failure = dict(
            error_type=type(exc).__name__,
            category=category or ("transport_" + exc.reason if isinstance(exc, TransportSourceError)
                                  else "http" if isinstance(exc, HTTPSourceError)
                                  else "integrity_or_bound"),
            status=getattr(exc, "status", None),
            no_retry=True,
            scope_accounting=_finish_scope(scope),
            reservation_totals=dict(calls=scope["total_reserved_calls"], nominal_cu=scope["nominal_cu"]),
            trust_limit=scope["trust_limit"],
        )
        publish(root / "STOP", failure)

    def call(method, params, *, entry=None, purpose=None):
        attempts = attempts_now()
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
        reservation = dict(method=method, cu=COSTS[method], started_ns=time.time_ns(),
                           cohort_index=entry["index"] if entry else None, purpose=purpose)
        publish(p / "reservation.json", reservation)
        scope["total_reserved_calls"] += 1
        scope["nominal_cu"] += COSTS[method]
        if method == "getSignaturesForAddress":
            entry["enumeration_reserved_calls"] += 1
            entry["status"] = "partial"
            entry["reason"] = "interrupted_or_incomplete"
        if method == "getTransaction":
            entry["hydration_reserved"] = True
            entry["hydration_status"] = "reserved_response_unknown"
        try:
            cap = client.fetch(method, params)
            cap.save(p / "capture")
            publish(p / "availability.json", dict(available_ns=time.time_ns()))
            seal(p)
            return cap.result(), p
        except Exception as exc:
            stop(exc)
            raise RuntimeError(
                "http" if isinstance(exc, HTTPSourceError)
                else "transport_" + exc.reason if isinstance(exc, TransportSourceError)
                else type(exc).__name__
            ) from None

    try:
        scope["inventory_status"] = "reserved_response_unknown"
        result, path = call(
            "getMultipleAccounts",
            [binding["universe"], dict(commitment="finalized", encoding="base64", dataSlice=dict(offset=0, length=0))],
            purpose="inventory",
        )
        if not isinstance(result, dict) or not isinstance(result.get("context"), dict) or type(result["context"].get("slot")) is not int:
            raise ValueError("inventory_schema")
        context = result["context"]["slot"]
        values = result.get("value")
        if not isinstance(values, list) or len(values) != len(binding["universe"]):
            raise ValueError("account_membership")
        staged_inventory = []
        for pool, account in zip(binding["universe"], values, strict=True):
            if account is not None and (not isinstance(account, dict) or not isinstance(account.get("owner"), str)):
                raise ValueError("inventory_schema")
            staged_inventory.append(dict(pool=pool, owner=account["owner"] if account else None,
                                         context_slot=context, checkpoint=False))
        inventory = staged_inventory
        scope["inventory_status"] = "validated"

        for entry in scope["entries"]:
            pool = entry["pool"]
            rows, refs, cursor = [], [], None
            terminal = None
            for page_no in range(2):
                config = dict(commitment="finalized", limit=20, minContextSlot=context)
                if cursor:
                    config["before"] = cursor
                page, path = call("getSignaturesForAddress", [pool, config],
                                  entry=entry, purpose="enumeration")
                staged = _validate_page(page, rows, cursor)
                # Commit only after every row and cross-page invariant has passed.
                refs.append(dict(path=str(path.resolve()), manifest_sha=sha(path / "manifest.json"),
                                 returned=len(staged)))
                entry["validated_responses"] += 1
                entry["page_returned_counts"].append(len(staged))
                entry["raw_count"] += len(staged)
                known = {r["signature"] for r in rows}
                rows.extend(r for r in staged if r["signature"] not in known)
                entry["unique_signatures"] = [r["signature"] for r in rows]
                entry["unique_count"] = len(rows)
                if not staged:
                    terminal = "observed_empty"
                    break
                cursor = staged[-1]["signature"]
            else:
                terminal = "page_bound"
            pages[pool] = dict(rows=rows, captures=refs,
                               coverage="bounded_account_references_only", history_complete=False)
            entry["reason"] = terminal
            eligible = next((r for r in rows if r["err"] is None), None)
            if eligible is None:
                entry["status"] = "completed"
                entry["hydration_status"] = "no_eligible_reference"
                continue
            entry["selected_signature"] = eligible["signature"]
            entry["hydration_status"] = "selected_not_reserved"
            tx_result, tx_path = call(
                "getTransaction",
                [eligible["signature"], dict(commitment="finalized", encoding="jsonParsed",
                                               maxSupportedTransactionVersion=0)],
                entry=entry, purpose="hydration",
            )
            ev = transaction_evidence(tx_path)
            if ev["status"] in ("raw_reference", "failed_transaction"):
                keys = tx_result["transaction"]["message"]["accountKeys"]
                keys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
                if (pool not in keys or tx_result["slot"] != eligible["slot"]
                        or tx_result["meta"]["err"] != eligible["err"]):
                    raise ValueError("hydration_reference_mismatch")
            entry["hydration_status"] = ev["status"]
            entry["hydration_completed"] = True
            entry["status"] = "completed"
            txs.append(dict(pool=pool, cohort_index=entry["index"], evidence=ev))

        with Ledger(root / "events", dict(scope="B05_raw_evidence_only",
                                          binding_sha=sha(root / "binding.json"))) as ledger:
            for t in txs:
                ingest(ledger, t["evidence"])
            event_view = view(ledger, time.time_ns())
        scope = _finish_scope(scope)
        result = dict(
            inventory=inventory, pages=pages, transactions=txs, event_view=event_view,
            attempts=len(attempts_now()), nominal_cu=scope["nominal_cu"],
            elapsed_seconds=time.monotonic() - begin, decoder_qualified=False,
            training_eligible=False, scope_accounting=scope,
        )
        publish(root / "result.json", result)
        seal(root)
        return result
    except Exception as exc:
        stop(exc)
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
