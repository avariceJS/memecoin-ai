"""Offline reconstruction from externally pinned raw evidence; no RPC or training."""

import json
from pathlib import Path

from memecoin_ai.sources import Capture
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import Ledger, publish

from .events import capacity, transaction_evidence, view
from .probe import COSTS


def reproduce(root: Path, expected_manifest_sha: str) -> dict:
    if sha(root / "manifest.json") != expected_manifest_sha:
        raise ValueError("external_pin_mismatch")
    verify(root)
    binding, original = read(root / "binding.json"), read(root / "result.json")
    pages, txs, inventory, charges, receipts = {}, [], [], [], []
    reservations, workflows = [], []
    context = None
    for path in sorted((root / "attempts").iterdir()):
        verify(path)
        cap = Capture.load(path / "capture")
        request, value = json.loads(cap.request_json), cap.result()
        reservation = read(path / "reservation.json")
        reservations.append(reservation)
        workflows.append((reservation, cap.method, request, value))
        available = read(path / "availability.json")["available_ns"]
        if not reservation["started_ns"] <= cap.request_started_ns <= cap.received_ns <= available:
            raise ValueError("capture_clock_integrity")
        if reservation["method"] != cap.method or reservation["cu"] != COSTS[cap.method]:
            raise ValueError("reservation_integrity")
        if request["params"][1]["commitment"] != "finalized":
            raise ValueError("commitment_changed")
        charges.append(reservation["cu"])
        receipts.append(cap.received_ns)
        if cap.method == "getMultipleAccounts":
            if inventory or request["params"][0] != binding["universe"]:
                raise ValueError("inventory_scope")
            context = value["context"]["slot"]
            inventory = [
                dict(
                    pool=p, owner=v["owner"] if v else None, context_slot=context, checkpoint=False
                )
                for p, v in zip(binding["universe"], value["value"], strict=True)
            ]
        elif cap.method == "getSignaturesForAddress":
            pool, cfg = request["params"]
            if (
                pool not in binding["cohort"]
                or cfg["minContextSlot"] != context
                or cfg["limit"] != 20
            ):
                raise ValueError("page_scope")
            entry = pages.setdefault(
                pool,
                dict(
                    rows=[],
                    captures=[],
                    coverage="bounded_account_references_only",
                    history_complete=False,
                ),
            )
            expected_cursor = entry["rows"][-1]["signature"] if entry["rows"] else None
            if cfg.get("before") != expected_cursor or len(entry["captures"]) >= 2:
                raise ValueError("page_cursor")
            for row in value:
                prior = next((r for r in entry["rows"] if r["signature"] == row["signature"]), None)
                if prior is not None and prior != row:
                    raise ValueError("signature_revision")
                if prior is None:
                    entry["rows"].append(row)
            entry["captures"].append(
                dict(
                    path=str(path.resolve()),
                    manifest_sha=sha(path / "manifest.json"),
                    returned=len(value),
                )
            )
        else:
            signature = request["params"][0]
            candidates = [
                (p, next((r for r in v["rows"] if r["err"] is None), None))
                for p, v in pages.items()
            ]
            matches = [(p, r) for p, r in candidates if r and r["signature"] == signature]
            # A signature can touch multiple sampled pools; associate with the most recent page.
            if not matches:
                raise ValueError("hydration_without_signature")
            pool, row = matches[-1]
            ev = transaction_evidence(path)
            if ev["status"] in ("raw_reference", "failed_transaction"):
                keys = value["transaction"]["message"]["accountKeys"]
                keys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
                if (
                    pool not in keys
                    or value["slot"] != row["slot"]
                    or value["meta"]["err"] != row["err"]
                ):
                    raise ValueError("hydration_reference_mismatch")
            txs.append(dict(pool=pool, cohort_index=reservation.get("cohort_index"), evidence=ev))

    # A present accounting object selects strict v1; malformed/present-null never falls back.
    has_v1 = "scope_accounting" in original
    derived_scope = None
    if has_v1:
        if binding.get("cohort") is None or len(set(binding["cohort"])) != len(binding["cohort"]):
            raise ValueError("planned_scope_invalid")
        cohort = binding["cohort"]
        if not workflows or workflows[0][1] != "getMultipleAccounts":
            raise ValueError("workflow_inventory_first")
        if sum(method == "getMultipleAccounts" for _, method, _, _ in workflows) != 1:
            raise ValueError("workflow_inventory_count")
        if workflows[0][0].get("purpose") != "inventory":
            raise ValueError("workflow_inventory_purpose")
        entries = []
        pos = 1
        reconstructed_txs = {t["cohort_index"]: t for t in txs}
        for index, pool in enumerate(cohort):
            enum = []
            while pos < len(workflows) and workflows[pos][1] == "getSignaturesForAddress":
                reservation, method, request, value = workflows[pos]
                if reservation.get("purpose") != "enumeration" or reservation.get("cohort_index") != index:
                    raise ValueError("workflow_entry_order")
                if request["params"][0] != pool:
                    raise ValueError("workflow_entry_scope")
                enum.append((reservation, request, value))
                pos += 1
                if not value:
                    break
                if len(enum) == 2:
                    break
            if not enum or len(enum) > 2:
                raise ValueError("workflow_missing_enumeration")
            if enum[-1][2] and len(enum) != 2:
                raise ValueError("workflow_nonterminal_enumeration")
            rows, raw_counts, seen = [], [], {}
            cursor = None
            for _, request, value in enum:
                cfg = request["params"][1]
                if cfg.get("before") != cursor:
                    raise ValueError("page_cursor")
                raw_counts.append(len(value))
                for row in value:
                    old = seen.get(row["signature"])
                    if old is not None and old != row:
                        raise ValueError("signature_revision")
                    if old is None:
                        seen[row["signature"]] = row
                        rows.append(row)
                if value:
                    cursor = value[-1]["signature"]
            eligible = next((r for r in rows if r["err"] is None), None)
            tx = reconstructed_txs.get(index)
            if eligible is None:
                hydration_status = "no_eligible_reference"
                hydration_completed = False
                if tx is not None:
                    raise ValueError("workflow_unselected_hydration")
            else:
                if pos >= len(workflows) or workflows[pos][1] != "getTransaction":
                    raise ValueError("workflow_missing_hydration")
                reservation, method, request, value = workflows[pos]
                if (reservation.get("purpose") != "hydration"
                        or reservation.get("cohort_index") != index
                        or request["params"][0] != eligible["signature"]):
                    raise ValueError("workflow_hydration_selection")
                if tx is None or tx.get("cohort_index") != index:
                    raise ValueError("workflow_hydration_evidence")
                hydration_status = tx["evidence"]["status"]
                hydration_completed = True
                pos += 1
            if tx is not None and tx.get("cohort_index") != index:
                raise ValueError("workflow_hydration_index")
            reason = "observed_empty" if not enum[-1][2] else "page_bound"
            entries.append(dict(
                index=index, pool=pool,
                status="completed", reason=reason,
                enumeration_reserved_calls=len(enum),
                hydration_reserved=eligible is not None,
                validated_responses=len(enum), page_returned_counts=raw_counts,
                raw_count=sum(raw_counts), unique_signatures=list(seen),
                unique_count=len(seen),
                selected_signature=eligible["signature"] if eligible else None,
                hydration_status=hydration_status,
                hydration_completed=hydration_completed,
                further_enumeration="unknown_after_bounded_work",
            ))
        if pos != len(workflows):
            raise ValueError("workflow_extra_or_misplaced_call")
        total_calls = len(workflows)
        total_cu = sum(charges)
        attempted_entries = sum(bool(e["enumeration_reserved_calls"]) for e in entries)
        derived_scope = dict(
            schema_version=1,
            denominator="ordered_binding_cohort_index_and_pool",
            coverage="bounded_account_references_only",
            history_complete=False,
            global_completeness="unknown",
            planned=len(cohort), attempted=attempted_entries,
            completed=len(entries), partial=0, unattempted=0,
            entries=entries,
            inventory_status="validated",
            total_reserved_calls=total_calls, nominal_cu=total_cu,
            trust_limit="Only fully validated returned pages count; provider scope is bounded.",
        )
        if not isinstance(original["scope_accounting"], dict):
            raise ValueError("scope_accounting_schema")
        if type(original["scope_accounting"].get("schema_version")) is not int:
            raise ValueError("scope_accounting_schema")
        if original["scope_accounting"].get("schema_version") != 1:
            raise ValueError("scope_accounting_version")
        def same_typed(left, right):
            if type(left) is not type(right):
                return False
            if isinstance(left, dict):
                return left.keys() == right.keys() and all(same_typed(left[k], right[k]) for k in right)
            if isinstance(left, list):
                return len(left) == len(right) and all(same_typed(a, b) for a, b in zip(left, right, strict=True))
            return left == right
        if not same_typed(original["scope_accounting"], derived_scope):
            raise ValueError("scope_accounting_mismatch")


    for key, actual in (("inventory", inventory), ("pages", pages), ("transactions", txs)):
        if original[key] != actual:
            raise ValueError("reproduction_mismatch_" + key)
    if len(charges) != original["attempts"] or sum(charges) != original["nominal_cu"]:
        raise ValueError("charge_mismatch")
    if len(charges) > 13 or sum(charges) > 500:
        raise ValueError("budget_integrity")
    with Ledger(root / "events", read(root / "events/binding.json")) as ledger:
        ledger.audit(expected_head=original["event_view"]["ledger_head"])
        if [r["payload"]["evidence"] for r in ledger.events] != [t["evidence"] for t in txs]:
            raise ValueError("ledger_evidence_mismatch")
        if view(ledger, original["event_view"]["as_of_ns"]) != original["event_view"]:
            raise ValueError("view_mismatch")
    verify(root)
    return dict(
        exact=True,
        accounting_mode="v1_reproduced" if has_v1 else "legacy_accounting_unavailable",
        scope_accounting=derived_scope,
        manifest_sha=expected_manifest_sha,
        attempts=len(charges),
        nominal_cu=sum(charges),
        first_receipt_ns=min(receipts),
        last_receipt_ns=max(receipts),
        hydrated=len(txs),
        pool_reference_and_slot_checked=True,
        network_requests=0,
        fits=0,
        decoder_qualified=False,
        history_complete=False,
        capacity=[capacity(n) for n in (12, 100, 300)],
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("manifest_sha")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    publish(args.output, reproduce(args.root, args.manifest_sha))
