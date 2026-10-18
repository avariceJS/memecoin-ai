"""Offline structural PumpSwap instruction references from reconciled finalized block bytes.

This module records recognized buy/buy_exact_quote_in/sell instruction references found in
captured finalized transaction records. Transaction status labels the containing transaction;
it does not claim an instruction executed or decode economic arguments, prices, support, or zero.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from memecoin_ai.sources import Capture
from memecoin_experience import observations
from memecoin_experience.block_reconciliation import (
    CAPABILITY as RECONCILIATION_CAPABILITY,
)
from memecoin_experience.block_reconciliation import (
    load_block_reconciliation,
)
from memecoin_experience.storage import (
    PIN,
    atomic_json,
    canonical,
    digest,
    lock,
    safe_path,
    sync_dir,
)
from memecoin_population.catalog import entity as checked_entity

from . import decoder
from .schema import SCHEMA

VERSION = "pumpswap-instruction-reference-audit-v2"
PROCESS_VERSION = "pumpswap-recognized-instruction-reference-v2"
CAPABILITY = "observed_values"
MAX_RANGE_SLOTS = 256
MAX_TRANSACTIONS_PER_SLOT = 200_000
MAX_INPUT_BUNDLE_BYTES = 220_000_000
MAX_TRANSACTION_SIGNATURES = 32
MAX_INSTRUCTION_DATA_CHARS = 16_384
DOMAIN = dict(
    version="typed-coordinate-domain-v1",
    name="solana_finalized_slot",
    network="solana-mainnet",
    kind="finalized_slot",
    unit="slot",
)
DOMAIN_PIN = digest(DOMAIN)
PROGRAM_ENTITY = checked_entity(f"solana/program/{SCHEMA['address']}")
_OBSERVATION_STREAMS = {
    f"pumpswap_instruction_reference/{kind}/{status}": dict(
        process=PROCESS_VERSION,
        features=[dict(name="instruction_reference", unit="indicator", modality="instruction")],
        coordinate_domain=DOMAIN,
        coordinate_domain_pin=DOMAIN_PIN,
        identity_rule=dict(
            version="observation-identity-rule-v1",
            name="sha256_primary_signature_coordinate_position_instruction_bytes_v1",
        ),
        position_rule=dict(
            version="observation-position-rule-v1",
            name="transaction_index_outer_index_optional_inner_index_v1",
        ),
        event_order_rule=dict(
            version="observation-event-order-rule-v1",
            name="coordinate_lexicographic_position_then_id_v1",
        ),
    )
    for kind in ("buy", "buy_exact_quote_in", "sell")
    for status in (
        "transaction_succeeded",
        "transaction_failed",
        "transaction_status_unknown",
    )
}
OBSERVATION_SCHEMA = dict(
    version="market-observations-v2",
    role="train",
    availability="point_in_time",
    streams=_OBSERVATION_STREAMS,
)
OBSERVATION_SCHEMA_PIN = digest(OBSERVATION_SCHEMA)
IDL_PIN = "b7d8c57a4d9c4dd0109a9ab893052352333252d4eedced10ac091d4d66cab89b"
_RECEIPTS = "pumpswap-reference-audits"


def _file_pin(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _code_pin() -> str:
    return _file_pin(Path(__file__))


def _schema_pin() -> str:
    return hashlib.sha256(
        Path(__import__("memecoin_pumpswap.schema", fromlist=["__file__"]).__file__).read_bytes()
    ).hexdigest()


def _decoder_pin() -> str:
    return _file_pin(Path(decoder.__file__))


def process_pin() -> str:
    instructions = {}
    for name in ("buy", "buy_exact_quote_in", "sell"):
        definition = SCHEMA["instructions"].get(name)
        if not isinstance(definition, dict) or definition["accounts"][0] != "pool":
            raise ValueError("pumpswap_reference_idl_role")
        instructions[name] = dict(
            discriminator=definition["discriminator"],
            first_account_role="pool",
        )
    if SCHEMA["provenance"]["sha256"] != IDL_PIN:
        raise ValueError("pumpswap_reference_idl_pin")
    return digest(
        dict(
            version=PROCESS_VERSION,
            reconciliation_capability=RECONCILIATION_CAPABILITY,
            program=SCHEMA["address"],
            idl_sha256=IDL_PIN,
            instructions=instructions,
            comparison="exact_ordered_signatures_outer_inner_projection_and_tx_status_v1",
            coordinate=dict(domain=DOMAIN_PIN, position="transaction_and_instruction_indices_v1"),
            observation_schema=OBSERVATION_SCHEMA_PIN,
        )
    )


def _relative_child(root: Path, child: str) -> Path:
    if (
        not isinstance(child, str)
        or not child
        or child.startswith("/")
        or "\\" in child
        or any(p in {"", ".", ".."} for p in child.split("/"))
    ):
        raise ValueError("pumpswap_reference_input_path")
    base = safe_path(root.resolve())
    path = safe_path(base / child)
    if not path.resolve().is_relative_to(base):
        raise ValueError("pumpswap_reference_input_path")
    return path


def _copy_input_bundle(source: Path, target: Path):
    source = safe_path(source.resolve())
    target = safe_path(target)
    total = 0
    for item in source.rglob("*"):
        if item.is_symlink():
            raise ValueError("pumpswap_reference_input_symlink")
        if item.is_file():
            total += item.stat().st_size
            if total > MAX_INPUT_BUNDLE_BYTES:
                raise ValueError("pumpswap_reference_input_size")
    if target.exists():
        raise ValueError("pumpswap_reference_input_exists")
    shutil.copytree(source, target)
    for item in target.rglob("*"):
        if item.is_symlink():
            raise ValueError("pumpswap_reference_input_copy_symlink")
        if item.is_file():
            with item.open("rb") as stream:
                os.fsync(stream.fileno())
    for directory in sorted(
        (p for p in target.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
    ):
        sync_dir(directory)
    sync_dir(target)
    sync_dir(target.parent)


def _tx_signature_array(tx):
    try:
        signatures = tx["transaction"]["signatures"]
    except (KeyError, TypeError):
        raise ValueError("pumpswap_reference_transaction_signature_shape") from None
    if (
        not isinstance(signatures, list)
        or not signatures
        or len(signatures) > MAX_TRANSACTION_SIGNATURES
        or any(not isinstance(s, str) or not s for s in signatures)
    ):
        raise ValueError("pumpswap_reference_transaction_signature_shape")
    return signatures


def _projection(tx):
    transaction = tx.get("transaction") if isinstance(tx, dict) else None
    message = transaction.get("message") if isinstance(transaction, dict) else None
    meta = tx.get("meta") if isinstance(tx, dict) else None
    if not isinstance(message, dict) or not isinstance(meta, dict):
        return None, None, "unsupported_trace"
    outer = message.get("instructions", "__missing_outer_instructions__")
    inner = meta.get("innerInstructions", "__missing_inner_instructions__")
    projection = dict(outer=outer, inner=inner)
    if not isinstance(outer, list) or not isinstance(inner, list):
        return projection, meta.get("err"), "unsupported_trace"
    if any(not isinstance(ix, dict) for ix in outer):
        return projection, meta.get("err"), "unsupported_trace"
    seen = set()
    for group in inner:
        if (
            not isinstance(group, dict)
            or type(group.get("index")) is not int
            or not 0 <= group["index"] < len(outer)
            or group["index"] in seen
            or not isinstance(group.get("instructions"), list)
            or any(not isinstance(ix, dict) for ix in group["instructions"])
        ):
            return projection, meta.get("err"), "unsupported_trace"
        seen.add(group["index"])
    if "err" not in meta:
        return projection, None, "unknown_transaction_status"
    return projection, meta["err"], "supported"


def _instructions(tx_index, projection):
    for outer_index, ix in enumerate(projection["outer"]):
        yield ix, [tx_index, outer_index], outer_index
    for group in projection["inner"]:
        outer_index = group["index"]
        for inner_index, ix in enumerate(group["instructions"]):
            yield ix, [tx_index, outer_index, inner_index], outer_index


def _matches_across_providers(left_txs, right_txs, stats):
    if len(left_txs) > MAX_TRANSACTIONS_PER_SLOT or len(right_txs) > MAX_TRANSACTIONS_PER_SLOT:
        raise ValueError("pumpswap_reference_transaction_bound")
    left, right = {}, {}
    left_order, right_order = [], []
    for target, ordered, txs in ((left, left_order, left_txs), (right, right_order, right_txs)):
        for tx in txs:
            signatures = _tx_signature_array(tx)
            primary = signatures[0]
            if primary in target:
                raise ValueError("pumpswap_reference_duplicate_transaction_signature")
            target[primary] = (signatures, tx)
            ordered.append(primary)
    if left_order != right_order or set(left) != set(right):
        raise ValueError("pumpswap_reference_transaction_identity_mismatch")
    matched = []
    for primary in left_order:
        lsigs, ltx = left[primary]
        rsigs, rtx = right[primary]
        if lsigs != rsigs:
            raise ValueError("pumpswap_reference_transaction_signatures_mismatch")
        lproj, lstatus, lstate = _projection(ltx)
        rproj, rstatus, rstate = _projection(rtx)
        if lstatus != rstatus:
            raise ValueError("pumpswap_reference_transaction_status_mismatch")
        if lstate != rstate:
            raise ValueError("pumpswap_reference_trace_status_mismatch")
        if canonical(lproj) != canonical(rproj):
            raise ValueError("pumpswap_reference_instruction_projection_mismatch")
        stats["transactions_compared"] += 1
        meta = ltx.get("meta")
        if (
            lstate == "unknown_transaction_status"
            or not isinstance(meta, dict)
            or "err" not in meta
        ):
            stats["transactions_status_unknown"] += 1
        elif lstatus is None:
            stats["transactions_succeeded"] += 1
        else:
            stats["transactions_failed"] += 1
        if lstate == "unsupported_trace":
            stats["unsupported_traces"] += 1
            matched.append((primary, ltx, None, lstatus, lstate))
        elif lstate == "unknown_transaction_status":
            matched.append((primary, ltx, lproj, lstatus, lstate))
        else:
            stats["instruction_projections_matched"] += 1
            matched.append((primary, ltx, lproj, lstatus, lstate))
    return matched


def _single_provider_transactions(transactions, stats):
    """Retain one sealed provider's positive records without inferring completeness."""
    if not isinstance(transactions, list) or len(transactions) > MAX_TRANSACTIONS_PER_SLOT:
        raise ValueError("pumpswap_reference_transaction_bound")
    unique = {}
    order = []
    for tx in transactions:
        signatures = _tx_signature_array(tx)
        primary = signatures[0]
        prior = unique.get(primary)
        if prior is not None:
            if canonical(prior) != canonical(tx):
                raise ValueError("pumpswap_reference_conflicting_duplicate_signature")
            stats["duplicate_transactions_suppressed"] += 1
            continue
        unique[primary] = tx
        order.append(primary)
    output = []
    for primary in order:
        tx = unique[primary]
        signatures = _tx_signature_array(tx)
        projection, status, state = _projection(tx)
        if state == "unknown_transaction_status":
            stats["transactions_status_unknown"] += 1
        elif state == "supported" and status is None:
            stats["transactions_succeeded"] += 1
        elif state == "supported":
            stats["transactions_failed"] += 1
        else:
            stats["unsupported_traces"] += 1
        output.append((primary, tx, projection, status, state))
    return output


def _ensure_spool(path):
    db = sqlite3.connect(path)
    db.execute("pragma cache_size=-4096")
    db.execute("pragma temp_store=FILE")
    db.execute(
        "create table events(entity text,stream text,coordinate integer,"
        "p0 integer,p1 integer,p2 integer,"
        "position text,id text,payload text,"
        "primary key(entity,stream,coordinate,p0,p1,p2,id)) without rowid"
    )
    return db


def _pool_guard(pool):
    if not isinstance(pool, str) or not pool:
        return "unresolved_role_map"
    try:
        checked_entity(f"solana/pool/{pool}")
    except ValueError as exc:
        if str(exc) == "population_protected_entity":
            return "protected_entity"
        return "unresolved_role_map"
    return "admitted"


def _iter_slot_transactions(db, transactions, slot, available_ns, stats, evidence):
    if not isinstance(transactions, list):
        raise ValueError("pumpswap_reference_block_transactions")
    stats["transactions_seen"] += len(transactions)
    # Caller supplies matched transactions after exact signature/status/trace comparison.
    for tx_index, (signature, _tx, projection, status, trace_state) in enumerate(transactions):
        if trace_state not in {"supported", "unknown_transaction_status"}:
            continue
        for ix, position, _outer_index in _instructions(tx_index, projection):
            if ix.get("programId") != SCHEMA["address"]:
                continue
            stats["pump_program_instruction_count"] += 1
            encoded = ix.get("data")
            if not isinstance(encoded, str) or len(encoded) > MAX_INSTRUCTION_DATA_CHARS:
                stats["malformed_instruction_data"] += 1
                continue
            try:
                wire = decoder.decode58(encoded)
            except ValueError:
                stats["malformed_instruction_data"] += 1
                continue
            if len(wire) < 8:
                stats["malformed_instruction_data"] += 1
                continue
            if wire[:8] == decoder.EVENT_TAG:
                if wire[8:16] in {bytes(v) for v in SCHEMA["events"].values()}:
                    stats["known_pumpswap_events"] += 1
                else:
                    stats["unknown_pumpswap_events"] += 1
                continue
            kind = next(
                (
                    name
                    for name in ("buy", "buy_exact_quote_in", "sell")
                    if wire[:8] == bytes(SCHEMA["instructions"][name]["discriminator"])
                ),
                None,
            )
            if kind is None:
                stats["unknown_pumpswap_instructions"] += 1
                continue
            stats["recognized_instruction_candidates"] += 1
            accounts = ix.get("accounts")
            if not isinstance(accounts, list) or not accounts or not isinstance(accounts[0], str):
                stats["unresolved_role_maps"] += 1
                continue
            # Protected identities are rejected before admitting the pool or producing any fact.
            guard_state = _pool_guard(accounts[0])
            if guard_state == "protected_entity":
                stats["protected_entity_rejections"] += 1
                continue
            if guard_state != "admitted":
                stats["unresolved_role_maps"] += 1
                continue
            stats["admissible_pool_role_references"] += 1
            if trace_state == "unknown_transaction_status":
                outcome = "transaction_status_unknown"
            elif status is None:
                outcome = "transaction_succeeded"
            else:
                outcome = "transaction_failed"
            stats[f"instruction_references_{outcome}"] += 1
            stream = f"pumpswap_instruction_reference/{kind}/{outcome}"
            event_id = hashlib.sha256(
                canonical(
                    dict(
                        signature=signature,
                        slot=slot,
                        position=position,
                        instruction_sha256=hashlib.sha256(wire).hexdigest(),
                    )
                )
            ).hexdigest()
            row = dict(
                kind="event",
                entity=PROGRAM_ENTITY,
                stream=stream,
                id=event_id,
                coordinate=slot,
                position=position,
                coordinate_domain_pin=DOMAIN_PIN,
                available_ns=available_ns,
                evidence=evidence,
                values=[1.0],
            )
            p0, p1 = position[0], position[1]
            p2 = position[2] if len(position) > 2 else -1
            db.execute(
                "insert into events values(?,?,?,?,?,?,?,?,?)",
                (
                    PROGRAM_ENTITY,
                    stream,
                    slot,
                    p0,
                    p1,
                    p2,
                    canonical(position).decode(),
                    event_id,
                    canonical(row).decode(),
                ),
            )


def _input_evidence(reconciliation_store: Path, reconciliation_pin: str):
    receipt = load_block_reconciliation(reconciliation_store, reconciliation_pin)
    if receipt["capability"] != RECONCILIATION_CAPABILITY:
        raise ValueError("pumpswap_reference_reconciliation_capability")
    if receipt["trust_model"] != "provider_agreement_not_cryptographic_or_market_exhaustiveness":
        raise ValueError("pumpswap_reference_reconciliation_trust")
    if receipt["interval"]["end_slot"] - receipt["interval"]["start_slot"] > MAX_RANGE_SLOTS:
        raise ValueError("pumpswap_reference_range_bound")
    return receipt


def _recompute(reconciliation_store: Path, reconciliation_pin: str, scratch_db: Path):
    receipt = _input_evidence(reconciliation_store, reconciliation_pin)
    providers = receipt["providers"]
    provider_roots = [
        _relative_child(reconciliation_store, provider["root"]) for provider in providers
    ]
    provider_by_alias = {provider["alias"]: provider for provider in providers}
    reconciliation_pages = [
        json.loads(
            (
                reconciliation_store
                / "qualification-block-reconciliation-pages"
                / f"{ref['pin']}.json"
            ).read_text()
        )
        for ref in receipt["pages"]
    ]
    support_rows = {row["slot"]: row for page in reconciliation_pages for row in page}
    stats = Counter()
    stats["reconciliation_pin"] = reconciliation_pin
    stats["transaction_signature_group_mismatches"] = 0
    db = _ensure_spool(scratch_db)
    try:
        slot_accounting = {}
        for slot in range(receipt["interval"]["start_slot"], receipt["interval"]["end_slot"]):
            before = stats.copy()
            support = support_rows[slot]
            loaded = {}
            for alias, provider in provider_by_alias.items():
                if support["status"] == "partial" and support["positive_provider"] != alias:
                    continue
                root = provider_roots[providers.index(provider)]
                capture_path = root / f"slots/{slot}"
                if not capture_path.is_dir():
                    continue
                capture = Capture.load(capture_path)
                response = capture.result()
                if not isinstance(response, dict) or not isinstance(
                    response.get("transactions"), list
                ):
                    continue
                loaded[alias] = (capture, response)
            if support["status"] == "complete":
                if set(loaded) != set(provider_by_alias):
                    raise ValueError("pumpswap_reference_complete_provider_capture_missing")
                left_capture, left_block = loaded["alchemy"]
                right_capture, right_block = loaded["solana-public"]
                matched = _matches_across_providers(
                    left_block["transactions"], right_block["transactions"], stats
                )
                available_ns = max(left_capture.received_ns, right_capture.received_ns)
                evidence = [reconciliation_pin]
                trust = "two_provider_instruction_projection_agreement"
                stats["provider_agreement_slots"] += 1
            elif support["status"] == "partial":
                alias = support["positive_provider"]
                if alias not in loaded:
                    raise ValueError("pumpswap_reference_partial_positive_capture_missing")
                capture, block = loaded[alias]
                matched = _single_provider_transactions(block["transactions"], stats)
                available_ns = capture.received_ns
                evidence = [reconciliation_pin, provider_by_alias[alias]["source_pin"]]
                trust = "provider_reported_finalized_positive_fact_v1"
                stats["single_provider_positive_slots"] += 1
                stats[f"positive_source_{alias.replace('-', '_')}"] += 1
            else:
                matched = []
                available_ns = support.get("available_ns")
                evidence = [reconciliation_pin]
                trust = None
            stats["slots_replayed"] += 1
            if available_ns is not None:
                _iter_slot_transactions(db, matched, slot, available_ns, stats, evidence)
            slot_accounting[str(slot)] = {
                "slot": slot,
                "available_ns": available_ns,
                "status": support["status"],
                "reason": support["reason"],
                "transport_status": support["transport_status"],
                "providers": support["providers"],
                "trust_policy": trust,
                "transactions": len(matched),
                "transactions_compared": stats["transactions_compared"]
                - before["transactions_compared"],
                "transactions_succeeded": stats["transactions_succeeded"]
                - before["transactions_succeeded"],
                "transactions_failed": stats["transactions_failed"] - before["transactions_failed"],
                "transactions_status_unknown": stats["transactions_status_unknown"]
                - before["transactions_status_unknown"],
                "unsupported_traces": stats["unsupported_traces"] - before["unsupported_traces"],
                "recognized_instruction_candidates": stats["recognized_instruction_candidates"]
                - before["recognized_instruction_candidates"],
                "unknown_pumpswap_instructions": stats["unknown_pumpswap_instructions"]
                - before["unknown_pumpswap_instructions"],
                "malformed_instruction_data": stats["malformed_instruction_data"]
                - before["malformed_instruction_data"],
                "unresolved_role_maps": stats["unresolved_role_maps"]
                - before["unresolved_role_maps"],
                "protected_entity_rejections": stats["protected_entity_rejections"]
                - before["protected_entity_rejections"],
                "known_pumpswap_events": stats["known_pumpswap_events"]
                - before["known_pumpswap_events"],
                "unknown_pumpswap_events": stats["unknown_pumpswap_events"]
                - before["unknown_pumpswap_events"],
                "reference_succeeded": stats["instruction_references_transaction_succeeded"]
                - before["instruction_references_transaction_succeeded"],
                "reference_failed": stats["instruction_references_transaction_failed"]
                - before["instruction_references_transaction_failed"],
                "reference_status_unknown": stats[
                    "instruction_references_transaction_status_unknown"
                ]
                - before["instruction_references_transaction_status_unknown"],
            }
        stats["slot_accounting"] = slot_accounting
        db.commit()
        db.execute("create index event_order on events(entity,stream,coordinate,p0,p1,p2,id)")
        db.commit()
        stats["positive_reference_facts"] = db.execute("select count(*) from events").fetchone()[0]
        for coordinate, count in db.execute(
            "select coordinate,count(*) from events group by coordinate"
        ):
            slot_accounting[str(coordinate)]["positive_reference_facts"] = count
        for slot in range(receipt["interval"]["start_slot"], receipt["interval"]["end_slot"]):
            slot_accounting[str(slot)].setdefault("positive_reference_facts", 0)
        stats["transaction_outcomes"] = {
            "succeeded": stats["transactions_succeeded"],
            "failed": stats["transactions_failed"],
            "unknown": stats["transactions_status_unknown"],
        }
        stats["instruction_reference_outcomes"] = {
            "transaction_succeeded": stats["instruction_references_transaction_succeeded"],
            "transaction_failed": stats["instruction_references_transaction_failed"],
            "transaction_status_unknown": stats[
                "instruction_references_transaction_status_unknown"
            ],
        }
        stats["instruction_denominators"] = {
            "pump_program_instruction_count": stats["pump_program_instruction_count"],
            "recognized_instruction_candidates": stats["recognized_instruction_candidates"],
            "unknown_instructions": stats["unknown_pumpswap_instructions"],
            "known_events": stats["known_pumpswap_events"],
            "unknown_events": stats["unknown_pumpswap_events"],
            "malformed_instruction_data": stats["malformed_instruction_data"],
            "unresolved_role_maps": stats["unresolved_role_maps"],
            "protected_entity_rejections": stats["protected_entity_rejections"],
        }
        latest_available = max(
            row["available_ns"]
            for row in slot_accounting.values()
            if row["available_ns"] is not None
        )
        return receipt, stats, latest_available, db
    except BaseException:
        db.close()
        raise


def _rows(db):
    for (payload,) in db.execute(
        "select payload from events order by entity,stream,coordinate,p0,p1,p2,id"
    ):
        yield json.loads(payload)


def _aggregate_receipt(reconciliation_pin, source_pins, observation_pin, schema_pin, stats):
    return dict(
        version=VERSION,
        capability=CAPABILITY,
        scope="recognized_pumpswap_instruction_references_only",
        subject_entity=PROGRAM_ENTITY,
        trust_model=(
            "two_provider_instruction_projection_agreement_or_"
            "provider_reported_finalized_positive_fact_v1; provider_asserted_capture"
        ),
        reconciliation_pin=reconciliation_pin,
        provider_source_pins=source_pins,
        process_version=PROCESS_VERSION,
        process_pin=process_pin(),
        official_idl_sha256=IDL_PIN,
        pumpswap_schema_pin=_schema_pin(),
        decoder_code_pin=_decoder_pin(),
        producer_code_pin=_code_pin(),
        observation_schema_pin=schema_pin,
        coordinate_domain_pin=DOMAIN_PIN,
        observation_source_pin=observation_pin,
        counts={
            k: v
            for k, v in stats.items()
            if k not in {"reconciliation_pin", "transaction_signature_group_mismatches"}
        },
        time_contract=dict(
            coordinate="native_solana_slot_plus_instruction_position",
            availability=(
                "bilateral_max_getBlock_receipt_received_ns_or_"
                "selected_provider_getBlock_receipt_received_ns_for_positive_bound"
            ),
            block_time_used=False,
            utc_window_claims=False,
        ),
        data_scope=dict(
            includes_pool_identity=False,
            pool_scoped_claims=False,
            includes_transaction_signatures=False,
            includes_raw_payloads=False,
            support_claims=False,
            market_coverage_claims=False,
            observed_zero_claims=False,
        ),
        training_eligibility=dict(
            fit_allowed=False,
            outcome="not_eligible",
            reason="two_adjacent_slots_cannot_form_independent_train_validation_test_partitions",
        ),
    )


def publish_reference_audit(reconciliation_store, reconciliation_pin, output_store):
    """Produce private coordinate facts plus an aggregate-only pinned receipt; offline only."""
    source_store = safe_path(reconciliation_store)
    _input_evidence(source_store, reconciliation_pin)
    output_store = safe_path(output_store)
    if not output_store.is_relative_to(safe_path(Path.cwd() / "work")):
        raise ValueError("pumpswap_reference_output_must_be_work")
    output_store.parent.mkdir(parents=True, exist_ok=True)
    with lock(output_store.parent / ("." + output_store.name + ".lock")):
        if output_store.exists():
            raise ValueError("pumpswap_reference_output_exists")
        stage = Path(tempfile.mkdtemp(prefix=".pumpswap-reference-", dir=output_store.parent))
        os.chmod(stage, 0o700)
        try:
            private_input = stage / "reconciliation"
            _copy_input_bundle(source_store, private_input)
            scratch_dir = stage / "scratch"
            scratch_dir.mkdir(mode=0o700)
            with tempfile.TemporaryDirectory(prefix="pumpswap-ref-", dir=scratch_dir) as tmp:
                receipt, stats, cutoff, db = _recompute(
                    private_input, reconciliation_pin, Path(tmp) / "facts.sqlite"
                )
                try:
                    observation_pin = None
                    if stats["positive_reference_facts"]:
                        observations_store = stage / "observations"
                        observation_pin = observations.publish(
                            observations_store,
                            _rows(db),
                            schema=OBSERVATION_SCHEMA,
                            cutoff=cutoff,
                            sources=[
                                dict(
                                    kind="cross_provider_block_reconciliation",
                                    pin=reconciliation_pin,
                                )
                            ],
                            chunk=512,
                        )
                    source_pins = [p["source_pin"] for p in receipt["providers"]]
                    aggregate = _aggregate_receipt(
                        reconciliation_pin,
                        source_pins,
                        observation_pin,
                        OBSERVATION_SCHEMA_PIN,
                        stats,
                    )
                    audit_pin = digest(aggregate)
                    reports = stage / _RECEIPTS
                    reports.mkdir()
                    atomic_json(reports / f"{audit_pin}.json", aggregate)
                    sync_dir(reports)
                finally:
                    db.close()
            shutil.rmtree(scratch_dir)
            sync_dir(stage)
            os.rename(stage, output_store)
            sync_dir(output_store.parent)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return audit_pin


def load_reference_audit(output_store, audit_pin):
    """Replay the embedded reconciliation and normalized facts without publishing anything."""
    if not isinstance(audit_pin, str) or not PIN.fullmatch(audit_pin):
        raise ValueError("pumpswap_reference_audit_pin")
    output_store = safe_path(output_store)
    path = safe_path(output_store / _RECEIPTS / f"{audit_pin}.json")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError("pumpswap_reference_audit_receipt_file")
    aggregate = json.loads(path.read_text())
    if digest(aggregate) != audit_pin:
        raise ValueError("pumpswap_reference_audit_checksum")
    if (
        aggregate.get("version") != VERSION
        or aggregate.get("capability") != CAPABILITY
        or aggregate.get("producer_code_pin") != _code_pin()
        or aggregate.get("pumpswap_schema_pin") != _schema_pin()
        or aggregate.get("decoder_code_pin") != _decoder_pin()
        or aggregate.get("official_idl_sha256") != IDL_PIN
        or aggregate.get("process_pin") != process_pin()
        or aggregate.get("observation_schema_pin") != OBSERVATION_SCHEMA_PIN
        or aggregate.get("coordinate_domain_pin") != DOMAIN_PIN
    ):
        raise ValueError("pumpswap_reference_audit_contract")
    private_input = safe_path(output_store / "reconciliation")
    with tempfile.TemporaryDirectory(prefix="pumpswap-ref-replay-") as tmp:
        receipt, stats, cutoff, db = _recompute(
            private_input, aggregate["reconciliation_pin"], Path(tmp) / "facts.sqlite"
        )
        try:
            if aggregate["observation_source_pin"] is None:
                if stats["positive_reference_facts"] or (output_store / "observations").exists():
                    raise ValueError("pumpswap_reference_observation_missing")
                count = 0
            else:
                source = observations.Source(
                    output_store / "observations",
                    aggregate["observation_source_pin"],
                    role="train",
                )
                if source.schema != OBSERVATION_SCHEMA:
                    raise ValueError("pumpswap_reference_observation_schema")
                actual_rows = source.rows()
                expected_rows = _rows(db)
                count = 0
                for actual, expected in zip(actual_rows, expected_rows, strict=True):
                    if actual != expected:
                        raise ValueError("pumpswap_reference_observation_replay")
                    count += 1
                if source.manifest["binding"]["cutoff_ns"] != cutoff:
                    raise ValueError("pumpswap_reference_observation_count")
            if count != stats["positive_reference_facts"]:
                raise ValueError("pumpswap_reference_observation_count")
            expected_aggregate = _aggregate_receipt(
                aggregate["reconciliation_pin"],
                [p["source_pin"] for p in receipt["providers"]],
                aggregate["observation_source_pin"],
                OBSERVATION_SCHEMA_PIN,
                stats,
            )
            if expected_aggregate != aggregate:
                raise ValueError("pumpswap_reference_aggregate_replay")
        finally:
            db.close()
    return aggregate
