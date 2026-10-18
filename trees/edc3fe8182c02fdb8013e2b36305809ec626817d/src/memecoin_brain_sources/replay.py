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
    context = None
    for path in sorted((root / "attempts").iterdir()):
        verify(path)
        cap = Capture.load(path / "capture")
        request, value = json.loads(cap.request_json), cap.result()
        reservation = read(path / "reservation.json")
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
            txs.append(dict(pool=pool, evidence=ev))
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
