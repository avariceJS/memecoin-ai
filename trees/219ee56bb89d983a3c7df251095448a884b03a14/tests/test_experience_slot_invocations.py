import json
import shutil

import pytest

from memecoin_ai.sources import Capture
from memecoin_brain.source import EXCLUDED
from memecoin_experience import block_reconciliation
from memecoin_experience.storage import canonical
from memecoin_pumpswap import decoder
from memecoin_pumpswap import slot_invocations as audit
from memecoin_pumpswap.schema import SCHEMA


def _ix(name="buy", pool="synthetic-pool", discriminator=None):
    if discriminator is None:
        discriminator = SCHEMA["instructions"][name]["discriminator"]
    return {
        "programId": SCHEMA["address"],
        "accounts": [pool],
        "data": decoder.encode58(bytes(discriminator) + b"\x00"),
    }


def _tx(*, ix=None, err=None, signature="synthetic-signature", block_time=1):
    return {
        "blockTime": block_time,
        "transaction": {
            "signatures": [signature],
            "message": {"instructions": [] if ix is None else [ix]},
        },
        "meta": {"err": err, "innerInstructions": []},
    }


def _inputs(
    tmp_path,
    monkeypatch,
    *,
    left_tx=None,
    right_tx=None,
    support_status="complete",
    positive_provider=None,
    peer_status=None,
):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "work" / "input"
    providers = [
        {"alias": "alchemy", "root": "inputs/left", "source_pin": "a" * 64},
        {"alias": "solana-public", "root": "inputs/right", "source_pin": "b" * 64},
    ]
    free_status = "complete" if support_status == "complete" else "complete"
    public_status = "complete" if support_status == "complete" else (peer_status or "failed")
    available = 280 if support_status == "complete" else 250
    providers_detail = {
        "alchemy": dict(
            status=free_status,
            reason="captured_full_block",
            transport_status="captured_result",
            disposition="block_present",
            enumeration_relation="listed",
            lineage_status="not_required",
            available_ns=250,
        ),
        "solana_public": dict(
            status=public_status,
            reason="synthetic_peer_failure"
            if public_status != "complete"
            else "captured_full_block",
            transport_status="provider_error" if public_status != "complete" else "captured_result",
            disposition="block_presence_unresolved"
            if public_status != "complete"
            else "block_present",
            enumeration_relation="listed",
            lineage_status="not_required",
            available_ns=280 if public_status == "complete" else None,
        ),
    }
    page_pin = "f" * 64
    support = dict(
        slot=7,
        status=support_status,
        available_ns=available,
        disposition="block_present",
        reason="single_provider_positive_peer_failure_or_stop"
        if support_status == "partial"
        else "bilateral_full_block_match",
        enumeration_relation="listed",
        enumeration_status="complete",
        transport_status="provider_error" if support_status == "partial" else "captured_result",
        lineage_status="not_required",
        proof_available_ns=None,
        proof_evidence=[],
        evidence=["c" * 64],
        providers=providers_detail,
        positive_provider=positive_provider,
    )
    support6 = dict(
        slot=6,
        status="complete",
        available_ns=180,
        disposition="block_present",
        reason="bilateral_full_block_match",
        enumeration_relation="listed",
        enumeration_status="complete",
        transport_status="captured_result",
        lineage_status="not_required",
        proof_available_ns=None,
        proof_evidence=[],
        evidence=["c" * 64],
        providers={
            "alchemy": dict(providers_detail["alchemy"], available_ns=150),
            "solana_public": dict(
                status="complete",
                reason="captured_full_block",
                transport_status="captured_result",
                disposition="block_present",
                enumeration_relation="listed",
                lineage_status="not_required",
                available_ns=180,
            ),
        },
        positive_provider=None,
    )
    (root / "qualification-block-reconciliation-pages").mkdir(parents=True)
    (root / "qualification-block-reconciliation-pages" / f"{page_pin}.json").write_bytes(
        canonical([support6, support])
    )
    receipt = {
        "capability": "cross_provider_finalized_range_reconciliation_v1",
        "trust_model": "provider_agreement_not_cryptographic_or_market_exhaustiveness",
        "interval": {"start_slot": 6, "end_slot": 8},
        "providers": providers,
        "pages": [{"pin": page_pin, "count": 2}],
    }
    for name, tx in (("left", left_tx), ("right", right_tx)):
        for slot, slot_tx in ((6, None), (7, tx)):
            path = root / "inputs" / name / "slots" / str(slot)
            path.parent.mkdir(parents=True, exist_ok=True)
            body = canonical(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "blockhash": f"block-{slot}",
                        "previousBlockhash": f"block-{slot - 1}",
                        "parentSlot": slot - 1,
                        "blockTime": 123456789,
                        "transactions": []
                        if slot_tx is None
                        else slot_tx
                        if isinstance(slot_tx, list)
                        else [slot_tx],
                    },
                }
            )
            base = 150 if slot == 6 else 250
            capture = Capture(
                f"synthetic-{name}",
                "getBlock",
                canonical(
                    {"jsonrpc": "2.0", "id": 1, "method": "getBlock", "params": [slot]}
                ).decode(),
                base - 10 + (name == "right") * 10,
                base + (name == "right") * 30,
                body,
            )
            capture.save(path)
    monkeypatch.setattr(audit, "load_block_reconciliation", lambda *_: receipt)
    monkeypatch.setattr(block_reconciliation, "load_block_reconciliation", lambda *_: receipt)
    return root, receipt


def _publish(tmp_path, monkeypatch, **kwargs):
    inputs, _ = _inputs(tmp_path, monkeypatch, **kwargs)
    output = tmp_path / "work" / "audit"
    pin = audit.publish_reference_audit(inputs, "c" * 64, output)
    return inputs, output, pin


@pytest.mark.parametrize("peer_status", ["failed", "stopped"])
def test_single_provider_positive_is_a_replayable_lower_bound_only(
    tmp_path, monkeypatch, peer_status
):
    tx = _tx(ix=_ix(), signature=f"positive-{peer_status}")
    inputs, output, pin = _publish(
        tmp_path,
        monkeypatch,
        left_tx=tx,
        support_status="partial",
        positive_provider="alchemy",
        peer_status=peer_status,
    )
    loaded = audit.load_reference_audit(output, pin)
    assert loaded["counts"]["positive_reference_facts"] == 1
    assert loaded["counts"]["single_provider_positive_slots"] == 1
    rows = list(
        audit.observations.Source(
            output / "observations", loaded["observation_source_pin"], role="train"
        ).rows()
    )
    assert rows[0]["available_ns"] == 250
    assert rows[0]["evidence"] == ["c" * 64, "a" * 64]
    from memecoin_pumpswap import slot_targets

    target_receipt = slot_targets._build_from_audit(output, pin)
    assert target_receipt["samples"][0]["origin_coordinate"] == 6
    assert target_receipt["samples"][0]["target_status"] == "lower_bound"
    assert target_receipt["samples"][0]["lower_bound"] == 1
    assert target_receipt["samples"][0]["target_value"] is None
    assert (
        target_receipt["samples"][0]["target_reasons"][0]["providers"]["solana_public"]["status"]
        == peer_status
    )
    assert inputs.exists()


@pytest.mark.parametrize("peer_status", ["failed", "stopped"])
def test_single_provider_duplicate_transaction_is_suppressed(tmp_path, monkeypatch, peer_status):
    tx = _tx(ix=_ix(), signature=f"duplicate-{peer_status}")
    _, output, pin = _publish(
        tmp_path,
        monkeypatch,
        left_tx=[tx, tx],
        support_status="partial",
        positive_provider="alchemy",
        peer_status=peer_status,
    )
    loaded = audit.load_reference_audit(output, pin)
    assert loaded["counts"]["duplicate_transactions_suppressed"] == 1
    assert loaded["counts"]["positive_reference_facts"] == 1


def test_conflicted_slot_is_quarantined_from_positive_facts(tmp_path, monkeypatch):
    _, output, pin = _publish(
        tmp_path,
        monkeypatch,
        left_tx=_tx(ix=_ix(), signature="must-not-pass"),
        support_status="conflict",
    )
    loaded = audit.load_reference_audit(output, pin)
    assert loaded["counts"]["positive_reference_facts"] == 0
    assert loaded["counts"].get("single_provider_positive_slots", 0) == 0
    from memecoin_pumpswap import slot_targets

    target_receipt = slot_targets._build_from_audit(output, pin)
    assert target_receipt["samples"][0]["target_status"] == "masked"
    assert target_receipt["samples"][0]["target_value"] is None
    assert target_receipt["samples"][0]["lower_bound"] is None


def test_replay_preserves_native_coordinate_availability_and_relocates(tmp_path, monkeypatch):
    tx = _tx(ix=_ix(), block_time=1)
    inputs, output, pin = _publish(tmp_path, monkeypatch, left_tx=tx, right_tx=tx)
    loaded = audit.load_reference_audit(output, pin)
    source = audit.observations.Source(
        output / "observations", loaded["observation_source_pin"], role="train"
    )
    rows = list(source.rows())
    assert len(rows) == 1
    assert rows[0]["coordinate"] == 7
    assert rows[0]["position"] == [0, 0]
    assert rows[0]["entity"] == audit.PROGRAM_ENTITY
    assert rows[0]["entity"] != "solana/pool/synthetic-pool"
    assert rows[0]["available_ns"] == 280
    assert "event_ns" not in rows[0]
    assert rows[0]["available_ns"] != 1
    shutil.rmtree(inputs)
    relocated = tmp_path / "relocated" / "audit"
    relocated.parent.mkdir()
    shutil.move(output, relocated)
    assert audit.load_reference_audit(relocated, pin)["counts"]["positive_reference_facts"] == 1


def test_provider_instruction_projection_mismatch_fails_closed(tmp_path, monkeypatch):
    left = _tx(ix=_ix("buy"))
    right = _tx(ix=_ix("sell"))
    inputs, _ = _inputs(tmp_path, monkeypatch, left_tx=left, right_tx=right)
    with pytest.raises(ValueError, match="instruction_projection_mismatch"):
        audit.publish_reference_audit(inputs, "c" * 64, tmp_path / "work" / "audit")


def test_provider_transaction_status_mismatch_fails_closed(tmp_path, monkeypatch):
    left = _tx(ix=_ix(), err=None)
    right = _tx(ix=_ix(), err={"InstructionError": [0, "Custom"]})
    inputs, _ = _inputs(tmp_path, monkeypatch, left_tx=left, right_tx=right)
    with pytest.raises(ValueError, match="transaction_status_mismatch"):
        audit.publish_reference_audit(inputs, "c" * 64, tmp_path / "work" / "audit")


def test_unsupported_instruction_trace_mismatch_fails_closed(tmp_path, monkeypatch):
    left = _tx()
    right = _tx()
    left["transaction"]["message"]["instructions"] = ["malformed"]
    right["transaction"]["message"]["instructions"] = ["different"]
    inputs, _ = _inputs(tmp_path, monkeypatch, left_tx=left, right_tx=right)
    with pytest.raises(ValueError, match="instruction_projection_mismatch"):
        audit.publish_reference_audit(inputs, "c" * 64, tmp_path / "work" / "audit")


def test_unknown_discriminator_stays_in_denominator_without_positive_fact(tmp_path, monkeypatch):
    unknown = _tx(ix=_ix(discriminator=[1, 2, 3, 4, 5, 6, 7, 8]))
    _, output, pin = _publish(tmp_path, monkeypatch, left_tx=unknown, right_tx=unknown)
    loaded = audit.load_reference_audit(output, pin)
    assert loaded["counts"]["unknown_pumpswap_instructions"] == 1
    assert loaded["counts"]["positive_reference_facts"] == 0


def test_failed_transaction_reference_is_classified_without_execution_claim(tmp_path, monkeypatch):
    failed = _tx(ix=_ix(), err={"InstructionError": [999, "Custom"]})
    _, output, pin = _publish(tmp_path, monkeypatch, left_tx=failed, right_tx=failed)
    loaded = audit.load_reference_audit(output, pin)
    assert loaded["counts"]["instruction_reference_outcomes"] == {
        "transaction_succeeded": 0,
        "transaction_failed": 1,
        "transaction_status_unknown": 0,
    }


def test_unknown_transaction_status_retains_structural_reference(tmp_path, monkeypatch):
    unknown = _tx(ix=_ix())
    del unknown["meta"]["err"]
    _, output, pin = _publish(tmp_path, monkeypatch, left_tx=unknown, right_tx=unknown)
    loaded = audit.load_reference_audit(output, pin)
    assert loaded["counts"]["instruction_reference_outcomes"] == {
        "transaction_succeeded": 0,
        "transaction_failed": 0,
        "transaction_status_unknown": 1,
    }
    assert loaded["counts"]["positive_reference_facts"] == 1


def test_protected_pool_is_rejected_before_fact_admission(tmp_path, monkeypatch):
    protected_pool = next(iter(EXCLUDED))
    protected = _tx(ix=_ix(pool=protected_pool))
    _, output, pin = _publish(tmp_path, monkeypatch, left_tx=protected, right_tx=protected)
    loaded = audit.load_reference_audit(output, pin)
    assert loaded["counts"]["protected_entity_rejections"] == 1
    assert loaded["counts"]["positive_reference_facts"] == 0


def test_receipt_has_aggregate_only_scope_and_no_pool_identity(tmp_path, monkeypatch):
    tx = _tx(ix=_ix(pool="pool-secret-test"))
    _, output, pin = _publish(tmp_path, monkeypatch, left_tx=tx, right_tx=tx)
    text = (output / audit._RECEIPTS / f"{pin}.json").read_text()
    assert "pool-secret-test" not in text
    assert "synthetic-signature" not in text
    assert "https://" not in text
    loaded = json.loads(text)
    assert loaded["training_eligibility"]["fit_allowed"] is False
