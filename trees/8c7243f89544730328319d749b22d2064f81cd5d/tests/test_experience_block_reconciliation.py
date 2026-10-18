import hashlib
import json
import shutil
from pathlib import Path

import pytest

from memecoin_ai.sources import Capture
from memecoin_experience import block_inventory
from memecoin_experience.block_reconciliation import (
    CAPABILITY,
    TRUST_MODEL,
    load_block_reconciliation,
    publish_block_reconciliation,
)
from memecoin_experience.storage import canonical
from memecoin_pumpswap import decoder, slot_invocations, slot_targets
from memecoin_pumpswap.schema import SCHEMA

START, END = 109, 111
OPTIONS = block_inventory.BLOCK_OPTIONS


def _make_root(root: Path, provider: str, *, mutation=None):
    free = provider == "free"
    provider_alias = "alchemy" if free else "solana-public"
    account = "alchemy-free-local" if free else "public-rpc"
    origin = (
        "https://solana-mainnet.g.alchemy.com" if free else "https://api.mainnet-beta.solana.com"
    )
    source_alias = (
        "alchemy-free-finalized-block-range-v1"
        if free
        else "solana-public-finalized-block-range-v1"
    )
    endpoint_env = "MEMECOIN_ALCHEMY_FREE_HTTP_URL" if free else "SOLANA_PUBLIC_RPC_HTTP_URL"
    plan = dict(
        provider_alias=provider_alias,
        endpoint_origin=origin,
        endpoint_env=endpoint_env,
        account_alias=account,
        source_alias=source_alias,
        start_slot=START,
        end_slot=END,
        left_witness_slot=108,
        right_witness_slot=111,
        request_budget=5,
        max_request_bytes=16384,
        max_response_bytes=12_000_000,
        max_bundle_bytes=64_000_000,
        timeout_seconds=60,
        expires_at_ns=2_000_000_000,
    )
    schedule = [
        (
            "enumeration",
            None,
            "getBlocks",
            [START, END - 1, {"commitment": "finalized"}],
            "enumeration",
        ),
        ("boundary", 108, "getBlock", [108, OPTIONS], "boundary-left"),
        ("slot", 109, "getBlock", [109, OPTIONS], "slots/109"),
        ("slot", 110, "getBlock", [110, OPTIONS], "slots/110"),
        ("boundary", 111, "getBlock", [111, OPTIONS], "boundary-right"),
    ]
    hashes = {slot: f"block-{slot}" for slot in range(108, 112)}
    captures, ledger_attempts, planned, outcomes = [], [], [], []
    root.mkdir(parents=True)
    for ix, (purpose, slot, method, params, rel) in enumerate(schedule):
        if method == "getBlocks":
            result = [START, END - 1]
            if mutation == (provider, "enumeration"):
                result = [START]
            if mutation in {("both", "skip"), (provider, "skip")}:
                result = [START]
            if mutation in {("both", "skip109"), (provider, "skip109")}:
                result = [START + 1]
        else:
            block_slot = slot
            prev = hashes.get(block_slot - 1, f"block-{block_slot - 1}")
            parent_slot = block_slot - 1
            if mutation in {("both", "skip"), (provider, "skip")} and block_slot == 111:
                prev = hashes[109]
                parent_slot = 109
            if mutation in {("both", "skip109"), (provider, "skip109")} and block_slot == 110:
                prev = hashes[108]
                parent_slot = 108
            if mutation == (provider, "parent") and block_slot == 110:
                prev = "broken-parent"
            transaction = {
                "transaction": {"signatures": [f"sig-{block_slot}-a", f"sig-{block_slot}-b"]}
            }
            if (
                provider == "free"
                and block_slot == 110
                and mutation
                in {
                    ("public", "http"),
                    ("public", "rpc"),
                    ("public", "transport"),
                    ("public", "stop"),
                }
            ):
                pump_ix = {
                    "programId": SCHEMA["address"],
                    "accounts": ["synthetic-pool"],
                    "data": decoder.encode58(
                        bytes(SCHEMA["instructions"]["buy"]["discriminator"]) + b"\x00"
                    ),
                }
                transaction = {
                    "transaction": {
                        "signatures": [f"sig-{block_slot}-a"],
                        "message": {"instructions": [pump_ix]},
                    },
                    "meta": {"err": None, "innerInstructions": []},
                }
            result = dict(
                blockhash=hashes[block_slot],
                previousBlockhash=prev,
                parentSlot=parent_slot,
                transactions=[transaction],
            )
            if mutation == (provider, "block") and block_slot == 109:
                result["blockhash"] = "different-block"
            if mutation in {(provider, "nullslot"), ("both", "nullslot")} and block_slot == 109:
                result = None
            if mutation in {("both", "skip"), (provider, "skip")} and block_slot == 110:
                result = None
            if mutation in {("both", "skip109"), (provider, "skip109")} and block_slot == 109:
                result = None
            if mutation == (provider, "truncated") and block_slot == 109:
                # The response remains byte-valid, but its sealed HTTP provenance claims truncation.
                pass
        envelope = {"jsonrpc": "2.0", "id": 1, "result": result}
        failure_kind = mutation[1] if mutation and mutation[0] == provider else None
        if purpose == "slot" and slot == 110 and failure_kind == "rpc":
            envelope = {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": "synthetic rpc failure"},
            }
        if mutation == (provider, "witness") and purpose == "boundary":
            envelope = {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": "synthetic unavailable"},
            }
        body = canonical(envelope)
        http_status = 503 if purpose == "slot" and slot == 110 and failure_kind == "http" else 200
        transport_error = (
            "TimeoutError"
            if purpose == "slot" and slot == 110 and failure_kind == "transport"
            else None
        )
        attempt_status = "transport_error" if transport_error else "response_received"
        response_truncated = mutation == (provider, "truncated") and slot == 109
        request_json = canonical(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).decode()
        started, received = 1000 + ix * 10, 1005 + ix * 10
        directory = root / rel
        cap = Capture(source_alias, method, request_json, started, received, body)
        cap.save(directory)
        receipt_path = directory / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["http_provenance"] = dict(
            version="solana-http-provenance-v1",
            endpoint_origin=origin,
            account_alias=account,
            http_status=http_status,
            response_headers={"content-type": "application/json"},
            retry_count=0,
            attempt_number=1,
            transport_error_class=transport_error,
            response_truncated=response_truncated,
        )
        receipt_path.write_bytes(canonical(receipt))
        cap_row = {"purpose": purpose, "path": rel}
        if purpose == "slot":
            cap_row["slot"] = slot
        elif purpose == "boundary":
            cap_row.update(slot=slot, side="left" if rel == "boundary-left" else "right")
        captures.append(cap_row)
        sha = hashlib.sha256(body).hexdigest()
        planned_row = dict(
            purpose=purpose,
            slot=slot,
            method=method,
            path=rel,
            request_json=request_json,
            request_sha256=hashlib.sha256(request_json.encode()).hexdigest(),
            status=attempt_status,
            attempt_number=1,
            request_started_ns=started,
            received_ns=received,
            http_status=http_status,
            transport_error_class=transport_error,
            response_bytes=len(body),
            response_sha256=sha,
            response_truncated=response_truncated,
        )
        planned.append(planned_row)
        ledger_attempts.append(
            {k: v for k, v in planned_row.items() if k != "path"} | {"retry_count": 0}
        )
        outcomes.append(
            dict(
                purpose=purpose,
                slot=slot,
                method=method,
                http_status=http_status,
                transport_error_class=transport_error,
                response_truncated=response_truncated,
                response_bytes=len(body),
                response_sha256=sha,
                request_started_ns=started,
                received_ns=received,
                retries=0,
            )
        )
    manifest = dict(
        version="solana-block-range-fixture-v1",
        start_slot=START,
        end_slot=END,
        interval_convention="half-open-start-inclusive-end-exclusive-v1",
        captures=captures,
        stopped_slots=[],
    )
    (root / "fixture.json").write_bytes(canonical(manifest))
    plan_pin = hashlib.sha256(canonical(plan)).hexdigest()
    ledger = dict(
        version="solana-bounded-request-attempt-ledger-v1",
        plan=plan,
        plan_pin=plan_pin,
        interval=[START, END],
        request_budget=5,
        planned_requests=planned,
        attempts=ledger_attempts,
    )
    ledger_path = root / "attempt-ledger.json"
    ledger_path.write_bytes(canonical(ledger))
    run = dict(
        version="solana-bounded-block-range-capture-v1",
        provider_alias=provider_alias,
        account_alias=account,
        endpoint_origin=origin,
        endpoint_env=endpoint_env,
        fixture_written=True,
        raw_payloads_in_work=True,
        plan=plan,
        plan_pin=plan_pin,
        interval=[START, END],
        request_budget=5,
        planned_requests=5,
        attempts=5,
        retries=0,
        stop_reason=None,
        stopped_requests=[],
        stopped_slots=[],
        timeout_seconds=60,
        max_request_bytes=16384,
        max_response_bytes=12_000_000,
        max_bundle_bytes=64_000_000,
        outcomes=outcomes,
        attempt_ledger_sha256=hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
    )
    if free:
        run.update(
            free_endpoint_designation=endpoint_env,
            free_plan_independently_verified=False,
            provider_plan_independently_verified=False,
        )
    else:
        run.update(
            free_endpoint_designation=None,
            free_plan_independently_verified=None,
            provider_plan_independently_verified=False,
        )
    (root / "run-receipt.json").write_bytes(canonical(run))


def _stop_root_before_slot(root: Path, slot: int):
    manifest_path = root / "fixture.json"
    ledger_path = root / "attempt-ledger.json"
    run_path = root / "run-receipt.json"
    manifest = json.loads(manifest_path.read_text())
    ledger = json.loads(ledger_path.read_text())
    run = json.loads(run_path.read_text())
    planned = ledger["planned_requests"]
    stop_at = next(
        i for i, row in enumerate(planned) if row["purpose"] == "slot" and row["slot"] == slot
    )
    for capture in manifest["captures"][stop_at:]:
        shutil.rmtree(root / capture["path"])
    manifest["captures"] = manifest["captures"][:stop_at]
    stopped = []
    stopped_slots = []
    for row in planned[stop_at:]:
        row["status"] = "stopped_expired"
        stopped.append(
            {key: row[key] for key in ("purpose", "slot", "method", "path")}
            | {"status": "stopped_expired"}
        )
        if row["purpose"] == "slot":
            stopped_slots.append(row["slot"])
    manifest["stopped_slots"] = stopped_slots
    ledger["attempts"] = ledger["attempts"][:stop_at]
    run["attempts"] = stop_at
    run["outcomes"] = run["outcomes"][:stop_at]
    run["stop_reason"] = "plan_expired"
    run["stopped_requests"] = stopped
    run["stopped_slots"] = stopped_slots
    manifest_path.write_bytes(canonical(manifest))
    ledger_path.write_bytes(canonical(ledger))
    run["attempt_ledger_sha256"] = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    run_path.write_bytes(canonical(run))


def _fixture(tmp_path, mutation=None):
    free = tmp_path / "work" / "free"
    public = tmp_path / "work" / "public"
    free.parent.mkdir()
    _make_root(free, "free", mutation=mutation)
    _make_root(public, "public", mutation=mutation)
    if mutation == ("public", "stop"):
        _stop_root_before_slot(public, 110)
    inventory_store = tmp_path / "work" / "inventory"
    inventory_pin = block_inventory.publish_finalized_block_inventory(inventory_store, free)
    return free, public, inventory_store, inventory_pin


def test_two_provider_reconciliation_publishes_and_replays(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    free, public, inventory, inventory_pin = _fixture(tmp_path)
    store = tmp_path / "work" / "reconciliation"
    pin = publish_block_reconciliation(store, free, public, inventory, inventory_pin)
    receipt = load_block_reconciliation(store, pin)
    assert receipt["capability"] == CAPABILITY
    assert receipt["trust_model"] == TRUST_MODEL
    assert receipt["market_support_claims"] is False
    assert receipt["accounting"] == {
        "unit": "in_range_slots",
        "expected": 2,
        "reconciled": 2,
        "unresolved": 0,
    }
    assert len(receipt["pages"]) == 1


def test_reconciliation_accepts_only_bilaterally_witnessed_null_skip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    free, public, inventory, inventory_pin = _fixture(tmp_path, ("both", "skip"))
    store = tmp_path / "work" / "reconciliation"
    pin = publish_block_reconciliation(store, free, public, inventory, inventory_pin)
    receipt = load_block_reconciliation(store, pin)
    rows = [
        row
        for ref in receipt["pages"]
        for row in json.loads(
            (store / "qualification-block-reconciliation-pages" / f"{ref['pin']}.json").read_text()
        )
    ]
    assert [row["status"] for row in rows] == ["complete", "skipped"]
    assert rows[1]["transport_status"] == "captured_null_result"
    assert rows[1]["disposition"] == "block_absent_confirmed"
    assert rows[1]["enumeration_relation"] == "unlisted"
    assert rows[1]["enumeration_status"] == "complete"
    assert rows[1]["lineage_status"] == "linked"
    assert rows[1]["proof_available_ns"] > rows[1]["available_ns"]
    assert receipt["accounting"] == {
        "unit": "in_range_slots",
        "expected": 2,
        "reconciled": 2,
        "unresolved": 0,
    }


def test_skip_proof_availability_uses_required_branch_dependencies_not_later_witness(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    free, public, inventory, inventory_pin = _fixture(tmp_path, ("both", "skip109"))
    store = tmp_path / "work" / "reconciliation"
    pin = publish_block_reconciliation(store, free, public, inventory, inventory_pin)
    receipt = load_block_reconciliation(store, pin)
    rows = [
        row
        for ref in receipt["pages"]
        for row in json.loads(
            (store / "qualification-block-reconciliation-pages" / f"{ref['pin']}.json").read_text()
        )
    ]
    absent = rows[0]
    assert absent["status"] == "skipped"
    assert absent["enumeration_relation"] == "unlisted"
    assert absent["enumeration_status"] == "complete"
    assert absent["lineage_status"] == "linked"
    assert len(absent["proof_evidence"]) == 8
    # Each provider's four dependencies are enumeration, left witness, slot attempt,
    # and the captured child that closes the gap. The later right witness is not required.
    unrelated_later_witness_ns = Capture.load(free / "boundary-right").received_ns
    assert absent["proof_available_ns"] == 1035
    assert absent["proof_available_ns"] < unrelated_later_witness_ns


def test_reconciliation_preserves_unwitnessed_null_as_unknown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    free, public, inventory, inventory_pin = _fixture(tmp_path, ("both", "nullslot"))
    store = tmp_path / "work" / "reconciliation"
    pin = publish_block_reconciliation(store, free, public, inventory, inventory_pin)
    receipt = load_block_reconciliation(store, pin)
    rows = [
        row
        for ref in receipt["pages"]
        for row in json.loads(
            (store / "qualification-block-reconciliation-pages" / f"{ref['pin']}.json").read_text()
        )
    ]
    assert [row["status"] for row in rows] == ["unknown", "complete"]
    assert rows[0]["transport_status"] == "captured_null_result"
    assert rows[0]["disposition"] == "block_presence_unresolved"


@pytest.mark.parametrize(
    "mutation,slot,status",
    [
        (("public", "block"), 109, "conflict"),
        (("public", "enumeration"), 109, "complete"),
        (("public", "parent"), 110, "conflict"),
        (("free", "truncated"), 109, "partial"),
        (("public", "witness"), 109, "complete"),
        (("public", "nullslot"), 109, "conflict"),
    ],
)
def test_reconciliation_preserves_provider_or_capture_disagreement(
    tmp_path, monkeypatch, mutation, slot, status
):
    monkeypatch.chdir(tmp_path)
    free, public, inventory, inventory_pin = _fixture(tmp_path, mutation)
    store = tmp_path / "work" / "reconciliation"
    pin = publish_block_reconciliation(store, free, public, inventory, inventory_pin)
    receipt = load_block_reconciliation(store, pin)
    rows = [
        row
        for ref in receipt["pages"]
        for row in json.loads(
            (store / "qualification-block-reconciliation-pages" / f"{ref['pin']}.json").read_text()
        )
    ]
    assert next(row for row in rows if row["slot"] == slot)["status"] == status
    if mutation == ("public", "enumeration"):
        assert all(row["enumeration_status"] == "mismatch" for row in rows)


@pytest.mark.parametrize("failure", ["http", "rpc", "transport", "stop"])
def test_reconciliation_retains_failed_and_stopped_provider_status(tmp_path, monkeypatch, failure):
    monkeypatch.chdir(tmp_path)
    free, public, inventory, inventory_pin = _fixture(tmp_path, ("public", failure))
    store = tmp_path / "work" / "reconciliation"
    pin = publish_block_reconciliation(store, free, public, inventory, inventory_pin)
    receipt = load_block_reconciliation(store, pin)
    rows = [
        row
        for ref in receipt["pages"]
        for row in json.loads(
            (store / "qualification-block-reconciliation-pages" / f"{ref['pin']}.json").read_text()
        )
    ]
    row = next(item for item in rows if item["slot"] == 110)
    assert row["status"] == "partial"
    assert row["positive_provider"] == "alchemy"
    assert row["providers"]["solana_public"]["status"] == (
        "stopped" if failure == "stop" else "failed"
    )
    audit_store = tmp_path / "work" / "invocation-audit"
    audit_pin = slot_invocations.publish_reference_audit(store, pin, audit_store)
    aggregate = slot_invocations.load_reference_audit(audit_store, audit_pin)
    assert aggregate["counts"]["positive_reference_facts"] == 1
    assert aggregate["counts"]["single_provider_positive_slots"] == 1
    target_receipt = slot_targets._build_from_audit(audit_store, audit_pin)
    target = next(
        sample for sample in target_receipt["samples"] if sample["origin_coordinate"] == 109
    )
    assert target["target_status"] == "lower_bound"
    assert target["target_value"] is None
    assert target["lower_bound"] == 1
    assert target["target_reasons"][0]["providers"]["solana_public"]["status"] == (
        "stopped" if failure == "stop" else "failed"
    )


def test_reconciliation_rejects_request_and_receipt_tampering(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    free, public, inventory, inventory_pin = _fixture(tmp_path)
    ledger_path = public / "attempt-ledger.json"
    ledger = json.loads(ledger_path.read_text())
    ledger["attempts"][0]["request_json"] = "{}"
    ledger_path.write_bytes(canonical(ledger))
    with pytest.raises(ValueError):
        publish_block_reconciliation(
            tmp_path / "work" / "reconciliation", free, public, inventory, inventory_pin
        )


def test_receipt_mutation_is_detected_on_replay(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    free, public, inventory, inventory_pin = _fixture(tmp_path)
    store = tmp_path / "work" / "reconciliation"
    pin = publish_block_reconciliation(store, free, public, inventory, inventory_pin)
    reconciliation_receipt = json.loads(
        (store / "qualification-block-reconciliations" / f"{pin}.json").read_text()
    )
    public_ref = reconciliation_receipt["providers"][1]["root"]
    receipt_path = store / public_ref / "slots" / "109" / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["http_provenance"]["http_status"] = 503
    receipt_path.write_bytes(canonical(receipt))
    with pytest.raises(ValueError):
        load_block_reconciliation(store, pin)


def test_receipt_is_self_contained_after_origin_removal_and_relocation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    free, public, inventory, inventory_pin = _fixture(tmp_path)
    store = tmp_path / "work" / "reconciliation"
    pin = publish_block_reconciliation(store, free, public, inventory, inventory_pin)
    shutil.rmtree(free)
    shutil.rmtree(public)
    shutil.rmtree(inventory)
    relocated = tmp_path / "relocated" / "reconciliation"
    relocated.parent.mkdir()
    shutil.move(store, relocated)
    receipt = load_block_reconciliation(relocated, pin)
    assert receipt["accounting"]["reconciled"] == 2


def test_receipt_contains_no_endpoints_paths_or_raw_transaction_facts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    free, public, inventory, inventory_pin = _fixture(tmp_path)
    store = tmp_path / "work" / "reconciliation"
    pin = publish_block_reconciliation(store, free, public, inventory, inventory_pin)
    text = (store / "qualification-block-reconciliations" / f"{pin}.json").read_text()
    assert "request_json" not in text
    assert "transactions" not in text
    assert "signature-" not in text
    assert "https://" not in text
    assert "@" not in text
    assert "?" not in text
