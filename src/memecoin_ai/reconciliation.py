"""Offline endpoint and within-interval vault continuity checks; no trading simulation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from memecoin_ai.collection import PilotStore
from memecoin_ai.domain import ValidationError, canonical, digest, integer
from memecoin_ai.raydium import TOKEN, decode_checkpoint


def vault_transition(tx: dict[str, Any], vault: str, mint: str) -> tuple[int, int] | None:
    keys = tx["transaction"]["message"]["accountKeys"]
    addresses = [k["pubkey"] for k in keys]
    if len(set(addresses)) != len(addresses):
        raise ValidationError("ambiguous account keys")
    if vault not in addresses:
        return None
    index = addresses.index(vault)
    meta = tx["meta"]
    sides = []
    for name in ("preTokenBalances", "postTokenBalances"):
        values = [b for b in meta[name] if b["accountIndex"] == index]
        if len(values) != 1 or values[0]["mint"] != mint or values[0].get("programId") != TOKEN:
            raise ValidationError(
                "missing/ambiguous/unsupported vault metadata; closure not assumed zero"
            )
        amount = values[0]["uiTokenAmount"]["amount"]
        if not isinstance(amount, str) or not amount.isascii() or not amount.isdecimal():
            raise ValidationError("invalid atomic amount")
        value = int(amount)
        if value >= 2**64:
            raise ValidationError("atomic amount overflow")
        sides.append(value)
    if meta["err"] is not None and sides[0] != sides[1]:
        raise ValidationError("failed transaction changed token balance")
    return sides[0], sides[1]


def _scan_evidence(store: PilotStore, scan: Any, start: int, end: int) -> set[str]:
    before = None
    last_slot = None
    boundary = False
    discovered: dict[str, int] = {}
    for page in store.db.execute(
        "SELECT capture_id FROM pages WHERE scan_id=? ORDER BY capture_id", (scan["id"],)
    ):
        if boundary:
            raise ValidationError("pages after scan boundary")
        cap = store.capture(page[0])
        request = json.loads(cap.request_json)
        opts = request["params"][1]
        if (
            cap.method != "getSignaturesForAddress"
            or request["params"][0] != scan["address"]
            or opts.get("before") != before
            or opts.get("commitment") != "finalized"
            or type(opts.get("minContextSlot")) is not int
            or opts["minContextSlot"] < end
            or type(opts.get("limit")) is not int
            or not 1 <= opts["limit"] <= 100
        ):
            raise ValidationError("scan provenance mismatch")
        items = cap.result()
        if not isinstance(items, list) or not items or len(items) > opts["limit"]:
            raise ValidationError("invalid signature page")
        page_seen = set()
        for item in items:
            sig = item["signature"]
            slot = item["slot"]
            integer(slot, "slot")
            if (
                sig in page_seen
                or sig in discovered
                or item.get("confirmationStatus") != "finalized"
                or (last_slot is not None and slot > last_slot)
            ):
                raise ValidationError("invalid page order/duplicate")
            page_seen.add(sig)
            last_slot = slot
            if slot <= start:
                boundary = True
            if start < slot <= end:
                discovered[sig] = slot
        if items[-1]["signature"] == before:
            raise ValidationError("stalled page cursor")
        before = items[-1]["signature"]
    cached = {
        r["signature"]: r["slot"]
        for r in store.db.execute("SELECT * FROM signatures WHERE scan_id=?", (scan["id"],))
    }
    if (
        cached != discovered
        or bool(scan["boundary"]) != boundary
        or scan["before_sig"] != before
        or scan["last_slot"] != last_slot
    ):
        raise ValidationError("scan cache disagrees with raw pages")
    return set(discovered)


def audit_pilot(store: PilotStore) -> dict[str, Any]:
    if store.db.in_transaction:
        raise ValidationError("audit requires its own consistent read transaction")
    store.db.execute("BEGIN")
    try:
        return _audit_pilot(store)
    finally:
        store.db.rollback()


def _audit_pilot(store: PilotStore) -> dict[str, Any]:
    db = store.db
    if (
        db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
        or db.execute("PRAGMA foreign_key_check").fetchall()
    ):
        raise ValidationError("journal integrity failure")
    evidence = []
    for row in db.execute("SELECT id,body,sha FROM captures ORDER BY id"):
        cap = store.capture(row["id"])
        evidence.append(
            dict(
                id=row["id"],
                response_sha256=row["sha"],
                receipt_sha256=digest(row["body"].encode()),
            )
        )
        if (
            cap.source_alias != store.spec()["source_alias"]
            or cap.received_ns < cap.request_started_ns
        ):
            raise ValidationError("capture source/clock mismatch")
    checkpoints = {}
    for row in db.execute("SELECT * FROM checkpoints ORDER BY id"):
        decoded = decode_checkpoint(store.capture(row["capture_id"]), row["pool"])
        if canonical(decoded) != row["body"] or decoded["context_slot"] != row["slot"]:
            raise ValidationError("checkpoint cache disagrees with raw")
        checkpoints[row["id"]] = decoded
    reports = []
    pools_without_interval = []
    for pool in store.spec()["pools"]:
        ids = [
            r[0]
            for r in db.execute("SELECT id FROM checkpoints WHERE pool=? ORDER BY slot", (pool,))
        ]
        if len(ids) < 2:
            pools_without_interval.append(pool)
        for a, b in zip(ids, ids[1:], strict=False):
            left, right = checkpoints[a], checkpoints[b]
            start, end = left["context_slot"], right["context_slot"]
            scans = db.execute(
                "SELECT * FROM scans WHERE start_id=? AND end_id=?", (a, b)
            ).fetchall()
            if {s["address"] for s in scans} != {pool, *left["pool"]["vaults"]} or len(scans) != 3:
                raise ValidationError("incomplete interval address registry")
            signatures = set()
            for scan in scans:
                signatures.update(_scan_evidence(store, scan, start, end))
            blockers = []
            if not all(s["boundary"] for s in scans):
                blockers.append("signature pagination has not reached lower boundary")
            ordered = []
            for signature in sorted(signatures):
                row = db.execute(
                    "SELECT * FROM transactions WHERE signature=?", (signature,)
                ).fetchone()
                if row is None:
                    blockers.append("transaction missing:" + signature)
                    continue
                cap = store.capture(row["capture_id"])
                tx = cap.result()
                req = json.loads(cap.request_json)
                if (
                    cap.method != "getTransaction"
                    or req["params"][0] != signature
                    or req["params"][1].get("commitment") != "finalized"
                    or not isinstance(tx, dict)
                    or tx["slot"] != row["slot"]
                    or tx["transaction"]["signatures"][0] != signature
                    or not start < tx["slot"] <= end
                ):
                    raise ValidationError("transaction cache identity mismatch")
                addresses = {key["pubkey"] for key in tx["transaction"]["message"]["accountKeys"]}
                claimed_addresses = {
                    scan["address"]
                    for scan in scans
                    if db.execute(
                        "SELECT 1 FROM signatures WHERE scan_id=? AND signature=?",
                        (scan["id"], signature),
                    ).fetchone()
                }
                if not claimed_addresses <= addresses:
                    raise ValidationError("signature index references absent account")
                version = tx.get("version")
                if version != "legacy" and (type(version) is not int or version not in (0, 1)):
                    raise ValidationError("unknown transaction version")
                block_row = db.execute(
                    "SELECT capture_id FROM blocks WHERE slot=?", (row["slot"],)
                ).fetchone()
                if block_row is None:
                    blockers.append("block order missing:" + str(row["slot"]))
                    continue
                block_cap = store.capture(block_row[0])
                block = block_cap.result()
                block_req = json.loads(block_cap.request_json)
                if (
                    block_cap.method != "getBlock"
                    or block_req["params"][0] != tx["slot"]
                    or block_req["params"][1].get("commitment") != "finalized"
                    or not isinstance(block, dict)
                    or signature not in block["signatures"]
                    or len(set(block["signatures"])) != len(block["signatures"])
                ):
                    raise ValidationError("block/transaction membership mismatch")
                ordered.append((tx["slot"], block["signatures"].index(signature), signature, tx))
            balances = list(left["vault_balances"])
            steps = []
            for slot, index, signature, tx in sorted(ordered, key=lambda x: (x[0], x[1])):
                changes = []
                for i, (vault, mint) in enumerate(
                    zip(left["pool"]["vaults"], left["pool"]["mints"], strict=True)
                ):
                    try:
                        transition = vault_transition(tx, vault, mint)
                        if transition is None:
                            continue
                        before, after = transition
                        if balances[i] != before:
                            blockers.append(f"vault continuity mismatch:{signature}:{vault}")
                        # Never reset to post balance: that would hide a missing step.
                        balances[i] += after - before
                        changes.append(dict(vault=vault, before=before, after=after))
                    except (ValidationError, KeyError, TypeError) as exc:
                        blockers.append(
                            "unsupported transition:" + signature + ":" + type(exc).__name__
                        )
                steps.append(
                    dict(signature=signature, slot=slot, transaction_index=index, changes=changes)
                )
            residuals = [balances[i] - right["vault_balances"][i] for i in range(2)]
            if any(residuals):
                blockers.append("terminal vault residual is nonzero")
            reports.append(
                dict(
                    pool=pool,
                    start_slot=start,
                    end_slot=end,
                    start_checkpoint=a,
                    end_checkpoint=b,
                    scans_complete=all(s["boundary"] for s in scans),
                    signatures=len(signatures),
                    transactions_replayed=len(ordered),
                    reconstructed_vault_balances=balances,
                    observed_vault_balances=right["vault_balances"],
                    residuals=residuals,
                    consistency_passed=not blockers,
                    nonempty=bool(steps),
                    blockers=blockers,
                    steps=steps,
                    receipt_gap_ns=right["raw_received_ns"] - left["raw_received_ns"],
                    fee_counter_delta=[
                        right["pool"]["excluded_fees"][i] - left["pool"]["excluded_fees"][i]
                        for i in range(2)
                    ],
                    full_pool_state_reconstructed=False,
                )
            )
    return dict(
        schema_version=1,
        spec=store.spec(),
        evidence=evidence,
        checkpoints=len(checkpoints),
        intervals=reports,
        issues=[dict(row) for row in db.execute("SELECT * FROM issues ORDER BY id")],
        pools_without_interval=pools_without_interval,
        all_intervals_consistent=bool(reports)
        and not pools_without_interval
        and all(r["consistency_passed"] for r in reports),
        market_dataset_qualified=False,
        independent_provider_verified=False,
        coverage_scope="address index traversed to boundary; completeness conditional on provider",
        historical_availability="original receipts only; recovery does not backdate availability",
    )


def write_audit(store: PilotStore, output: Path) -> dict[str, Any]:
    result = audit_pilot(store)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(canonical(result) + "\n")
    return result


def export_pilot(store: PilotStore, output: Path) -> dict[str, Any]:
    """SQLite backup freezes a coherent point while the WAL writer may still progress."""
    import sqlite3

    output.mkdir(parents=True, exist_ok=False)
    destination = sqlite3.connect(output / "journal.sqlite")
    try:
        store.db.backup(destination)
    finally:
        destination.close()
    snapshot = PilotStore(output / "journal.sqlite", read_only=True)
    try:
        report = write_audit(snapshot, output / "report.json")
    finally:
        snapshot.close()
    modules = ("collection.py", "reconciliation.py", "raydium.py", "sources.py", "domain.py")
    source = {name: (Path(__file__).parent / name).read_text() for name in modules}
    (output / "decoder-source.json").write_text(canonical(source) + "\n")
    manifest = dict(
        schema_version=1,
        files={
            name: digest((output / name).read_bytes())
            for name in ("journal.sqlite", "report.json", "decoder-source.json")
        },
        semantics="audit source snapshot; not retroactive collector-version evidence",
        market_dataset_qualified=False,
    )
    (output / "manifest.json").write_text(canonical(manifest) + "\n")
    return dict(
        intervals=len(report["intervals"]),
        consistent=report["all_intervals_consistent"],
        manifest=manifest,
    )


def verify_pilot(output: Path) -> dict[str, Any]:
    manifest = json.loads((output / "manifest.json").read_text())
    if (
        manifest.get("schema_version") != 1
        or set(manifest["files"]) != {"journal.sqlite", "report.json", "decoder-source.json"}
        or manifest.get("market_dataset_qualified") is not False
    ):
        raise ValidationError("invalid pilot manifest")
    for name, sha in manifest["files"].items():
        if digest((output / name).read_bytes()) != sha:
            raise ValidationError("pilot artifact checksum mismatch")
    store = PilotStore(output / "journal.sqlite", read_only=True)
    try:
        actual = audit_pilot(store)
    finally:
        store.close()
    expected = json.loads((output / "report.json").read_text())
    if canonical(actual) != canonical(expected):
        raise ValidationError("pilot audit reproduction mismatch")
    return dict(
        intervals=len(actual["intervals"]),
        consistent=actual["all_intervals_consistent"],
        nonempty_intervals=sum(i["nonempty"] for i in actual["intervals"]),
        market_dataset_qualified=False,
    )
