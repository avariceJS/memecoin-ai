"""Offline economic state tapes and conditional quotes, never a market-ready ML dataset."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from memecoin_ai.collection import PilotStore, captured_transaction
from memecoin_ai.cpmm import PPM, EconomicState, Fees
from memecoin_ai.domain import ValidationError, canonical, digest, integer
from memecoin_ai.protocol import audit_protocol
from memecoin_ai.tokens import TransferFee, decode_supported_checkpoint, transfer_swap

MODULES = (
    "datasets.py",
    "protocol.py",
    "cpmm.py",
    "tokens.py",
    "history.py",
    "raydium.py",
    "reconciliation.py",
    "collection.py",
    "blockrange.py",
    "sources.py",
    "domain.py",
)
FILES = {
    "journal.sqlite",
    "spec.json",
    "states.jsonl",
    "quotes.jsonl",
    "report.json",
    "source.json",
}


def dataset_spec(sizes_ppm: list[int]) -> dict[str, Any]:
    if not sizes_ppm or len(sizes_ppm) > 8 or sizes_ppm != sorted(set(sizes_ppm)):
        raise ValidationError("quote grid requires 1..8 distinct increasing sizes")
    for size in sizes_ppm:
        integer(size, "input reserve ppm", 1)
        if size > 10_000:
            raise ValidationError("diagnostic quote grid capped at 1% of input reserves")
    return dict(
        schema_version=1,
        kind="cpmm_economic_state_tape",
        input_reserve_ppm=sizes_ppm,
        sizing="floor(effective input reserve * ppm / 1000000), gross wallet input",
        scope="fixed cohort; segments between audited finalized checkpoints",
        historical_availability="unknown; receipts are evidence acquisition only",
        execution_model="conditional_transfer_swap_v1; isolated immediate round trip",
        seed=None,
        temporal_split=None,
        market_dataset_qualified=False,
        training_ready=False,
        execution_qualified=False,
    )


def conditional_quotes(row: dict[str, Any], sizes_ppm: list[int]) -> list[dict[str, Any]]:
    """No history mutation: each hypothetical action starts from the same state."""
    dataset_spec(sizes_ppm)
    state = EconomicState(**row["state"])
    fees = Fees(**row["fees"])
    tokens = tuple(TransferFee(**f) for f in row["token_fees"])
    if len(tokens) != 2:
        raise ValidationError("quote requires two token fee schedules")
    terms = dict(
        creator_fee_on=row["pool"]["creator_fee_on"],
        creator_enabled=row["pool"]["enable_creator_fee"],
        token_fees=(tokens[0], tokens[1]),
    )
    results = []
    for direction in (0, 1):
        for size in sizes_ppm:
            amount = state.reserves[direction] * size // PPM
            result: dict[str, Any] = dict(
                state_id=row["state_id"],
                input_index=direction,
                input_reserve_ppm=size,
                gross_input=amount,
            )
            try:
                if row["pool"]["status"] & 4:
                    raise ValidationError("pool swaps disabled")
                timestamp = row["chain_timestamp_seconds"]
                if type(timestamp) is not int or timestamp < row["pool"]["open_time"]:
                    raise ValidationError("unknown or pre-open chain timestamp")
                quote, gross, net, incoming, outgoing = transfer_swap(
                    state, fees, direction, amount, 0, exact_input=True, **terms
                )
                # Reverse on the hypothetical post-trade state, not the unchanged historical pool.
                _, _, back, _, _ = transfer_swap(
                    quote.after, fees, 1 - direction, net, 0, exact_input=True, **terms
                )
                result.update(
                    status="quoted",
                    net_output=net,
                    input_transfer_fee=incoming,
                    output_transfer_fee=outgoing,
                    pool_quote={k: v for k, v in asdict(quote).items() if k != "after"},
                    immediate_roundtrip_output=back,
                    immediate_roundtrip_loss=gross - back,
                )
            except ValidationError as exc:
                result.update(status="unavailable", reason=str(exc))
            if result["status"] == "quoted" and result["immediate_roundtrip_loss"] < 0:
                raise ValidationError("positive isolated round trip violates quote invariant")
            results.append(result)
    return results


def reconstruct_dataset(store: PilotStore, spec: dict[str, Any]) -> dict[str, Any]:
    if spec != dataset_spec(spec["input_reserve_ppm"]):
        raise ValidationError("unsupported dataset specification")
    states: list[dict[str, Any]] = []
    audit = audit_protocol(store, state_rows=states)
    captures = {
        r[0]: store.capture(r[0]) for r in store.db.execute("SELECT id FROM captures ORDER BY id")
    }
    evidence_ready = max((c.received_ns for c in captures.values()), default=None)
    quotes = []
    for row in states:
        if row["phase"] == "transaction":
            cid = store.db.execute(
                "SELECT capture_id FROM transactions WHERE signature=?", (row["signature"],)
            ).fetchone()[0]
            tx = captured_transaction(captures[cid], row["signature"], row["slot"])
            timestamp = tx.get("blockTime")
            network_fee = tx["meta"].get("fee")
        else:
            checkpoint_id = (
                row["start_checkpoint"]
                if row["phase"] == "start_checkpoint"
                else row["end_checkpoint"]
            )
            cid = store.db.execute(
                "SELECT capture_id FROM checkpoints WHERE id=?", (checkpoint_id,)
            ).fetchone()[0]
            decoded = decode_supported_checkpoint(captures[cid], row["pool_id"])
            timestamp = decoded.get("clock_timestamp")
            network_fee = None
        row.update(
            state_id=digest(
                canonical(
                    {
                        k: row[k]
                        for k in (
                            "pool_id",
                            "start_checkpoint",
                            "end_checkpoint",
                            "phase",
                            "slot",
                            "transaction_index",
                            "signature",
                        )
                    }
                ).encode()
            ),
            source_capture_id=cid,
            source_response_sha256=digest(captures[cid].response),
            source_received_ns=captures[cid].received_ns,
            historical_available_ns=None,
            evidence_ready_ns=evidence_ready,
            chain_timestamp_seconds=timestamp,
            observed_network_fee_lamports=network_fee,
        )
        quotes.extend(conditional_quotes(row, spec["input_reserve_ppm"]))
    report = dict(
        schema_version=1,
        kind=spec["kind"],
        source_alias=store.spec()["source_alias"],
        cohort=store.spec()["pools"],
        protocol_audit=audit,
        counts=dict(
            segments=len(audit["intervals"]),
            accepted_segments=sum(i["consistency_passed"] for i in audit["intervals"]),
            quarantined_segments=sum(not i["consistency_passed"] for i in audit["intervals"]),
            states=len(states),
            transactions=sum(r["phase"] == "transaction" for r in states),
            quotes=len(quotes),
            quoted=sum(q["status"] == "quoted" for q in quotes),
            unavailable_quotes=sum(q["status"] != "quoted" for q in quotes),
        ),
        evidence_ready_ns=evidence_ready,
        market_dataset_qualified=False,
        execution_qualified=False,
        training_ready=False,
        blockers=[
            "historical feature availability unknown",
            "fixed cohort universe selection",
            "transaction admission/inclusion/delay/network costs uncalibrated",
            "hypothetical quotes are not observed fills or persistent market impact",
        ],
    )
    return dict(states=states, quotes=quotes, report=report)


def _source() -> dict[str, str]:
    return {name: Path(__file__).with_name(name).read_text() for name in MODULES}


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(canonical(row) + "\n" for row in rows)


def export_economic_dataset(
    store: PilotStore, output: Path, *, sizes_ppm: list[int]
) -> dict[str, Any]:
    spec = dataset_spec(sizes_ppm)
    if store.db.in_transaction:
        raise ValidationError("dataset export requires an idle journal connection")
    output.mkdir(parents=True, exist_ok=False)
    (output / "spec.json").write_text(canonical(spec) + "\n")
    (output / "source.json").write_text(canonical(_source()) + "\n")
    destination = sqlite3.connect(output / "journal.sqlite")
    try:
        store.db.backup(destination)
    finally:
        destination.close()
    snapshot = PilotStore(output / "journal.sqlite", read_only=True)
    try:
        result = reconstruct_dataset(snapshot, spec)
    finally:
        snapshot.close()
    for name in ("states", "quotes"):
        (output / (name + ".jsonl")).write_text(_jsonl(result[name]))
    (output / "report.json").write_text(canonical(result["report"]) + "\n")
    (output / "manifest.json").write_text(
        canonical(
            dict(
                schema_version=1,
                kind=spec["kind"],
                files={name: digest((output / name).read_bytes()) for name in sorted(FILES)},
            )
        )
        + "\n"
    )
    report: dict[str, Any] = result["report"]
    return report


def verify_economic_dataset(output: Path) -> dict[str, Any]:
    manifest = json.loads((output / "manifest.json").read_text())
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "cpmm_economic_state_tape"
        or set(manifest["files"]) != FILES
    ):
        raise ValidationError("invalid economic dataset manifest")
    for name, sha in manifest["files"].items():
        if digest((output / name).read_bytes()) != sha:
            raise ValidationError("economic dataset checksum mismatch")
    if json.loads((output / "source.json").read_text()) != _source():
        raise ValidationError("dataset transform version differs; use its recorded source revision")
    snapshot = PilotStore(output / "journal.sqlite", read_only=True)
    try:
        result = reconstruct_dataset(snapshot, json.loads((output / "spec.json").read_text()))
    finally:
        snapshot.close()
    for name in ("states", "quotes"):
        if (output / (name + ".jsonl")).read_text() != _jsonl(result[name]):
            raise ValidationError("economic dataset reproduction mismatch")
    if (output / "report.json").read_text() != canonical(result["report"]) + "\n":
        raise ValidationError("economic dataset report reproduction mismatch")
    report: dict[str, Any] = result["report"]
    return report
