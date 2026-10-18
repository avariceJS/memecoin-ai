import hashlib
import json
from pathlib import Path

import pytest

from memecoin_experience import observations, qualification, signature_capture

NS = 10**9
SIGNATURES = ["1" * 63 + value for value in "2345"]


def _capture(root, *, missing_block_time=False, all_block_times_missing=False):
    pages = [
        [
            dict(
                signature=SIGNATURES[0],
                slot=30,
                blockTime=None if all_block_times_missing else 15,
                confirmationStatus="finalized",
                err=None,
            ),
            dict(
                signature=SIGNATURES[1],
                slot=20,
                blockTime=None if all_block_times_missing else 14,
                confirmationStatus="finalized",
                err=None,
            ),
        ],
        [
            dict(
                signature=SIGNATURES[2],
                slot=20,
                blockTime=None if all_block_times_missing else 14,
                confirmationStatus="finalized",
                err={"InstructionError": [1, "Custom"]},
            ),
            dict(
                signature=SIGNATURES[3],
                slot=10,
                blockTime=None if missing_block_time or all_block_times_missing else 13,
                confirmationStatus="finalized",
                err=None,
            ),
        ],
    ]
    (root / "pages").mkdir(parents=True)
    page_rows = []
    for i, rows in enumerate(pages):
        raw = json.dumps(rows, separators=(",", ":")).encode()
        path = root / "pages" / f"page-{i:04d}.json"
        path.write_bytes(raw)
        page_rows.append(
            dict(
                request_started_ns=(19 + i) * NS,
                received_ns=(20 + i) * NS,
                request_limit=2,
                used_before_cursor=i > 0,
                before_signature_sha256=(
                    hashlib.sha256(pages[i - 1][-1]["signature"].encode()).hexdigest()
                    if i > 0
                    else None
                ),
                returned=len(rows),
                overlap=0,
                newest_slot=rows[0]["slot"],
                oldest_slot=rows[-1]["slot"],
                finalized=True,
                monotonic=True,
                local_seal="created",
                retried=False,
                path=f"pages/page-{i:04d}.json",
                response_sha256=hashlib.sha256(raw).hexdigest(),
                response_bytes=len(raw),
            )
        )
    receipt = dict(
        version="alchemy-mcp-signature-cursor-probe-v1",
        address="11111111111111111111111111111111",
        network="solana-mainnet",
        method="getSignaturesForAddress",
        commitment="finalized",
        page_limit=2,
        attempts=2,
        retries=0,
        pages=page_rows,
        unique_signatures=4,
        aborted=False,
        raw_surface="test fixture",
    )
    receipt_path = root / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True))
    return receipt_path


def test_signature_capture_stays_unknown_without_invented_membership(tmp_path):
    receipt = _capture(tmp_path / "capture")
    imported = signature_capture.publish(receipt, tmp_path / "observations")
    source = observations.Source(
        tmp_path / "observations", imported["observation_pin"], role="train"
    )
    rows = list(source.rows())
    assert imported["accounting"] == {
        "captured_signature_rows": 4,
        "event_time_resolved": 4,
        "unsupported_missing_block_time": 0,
        "support_status": "unknown",
        "coverage_complete": False,
        "absence_inference_permitted": False,
        "cursor_chain_verified": True,
    }
    assert sum(row["kind"] == "event" for row in rows) == 4
    assert {row["entity"] for row in rows} == {"solana/address/11111111111111111111111111111111"}
    assert not any(row["kind"] in {"enrollment", "support", "lifecycle"} for row in rows)
    assert imported["accounting"]["support_status"] == "unknown"
    assert imported["accounting"]["cursor_chain_verified"] is True
    assert source.schema["streams"]["solana/address-signatures"]["features"][1]["name"] == (
        "transaction_rpc_succeeded"
    )
    import_path = tmp_path / "observations" / "capture-imports" / f"{imported['import_pin']}.json"
    import_record = json.loads(import_path.read_text())
    assert import_record["accounting"]["unsupported_missing_block_time"] == 0


def test_signature_capture_accounts_for_missing_time_as_unsupported(tmp_path):
    receipt = _capture(tmp_path / "capture", missing_block_time=True)
    imported = signature_capture.publish(receipt, tmp_path / "observations")
    source = observations.Source(
        tmp_path / "observations", imported["observation_pin"], role="train"
    )
    assert imported["accounting"]["captured_signature_rows"] == 4
    assert imported["accounting"]["event_time_resolved"] == 3
    assert imported["accounting"]["unsupported_missing_block_time"] == 1
    assert sum(row["kind"] == "event" for row in source.rows()) == 3
    import_path = tmp_path / "observations" / "capture-imports" / f"{imported['import_pin']}.json"
    import_record = json.loads(import_path.read_text())
    assert import_record["accounting"]["unsupported_missing_block_time"] == 1


def test_signature_capture_seals_all_unresolved_time_rows_without_fake_events(tmp_path):
    receipt = _capture(tmp_path / "capture", all_block_times_missing=True)
    imported = signature_capture.publish(receipt, tmp_path / "observations")
    assert imported["observation_pin"] is None
    assert imported["accounting"]["event_time_resolved"] == 0
    assert imported["accounting"]["unsupported_missing_block_time"] == 4
    import_path = tmp_path / "observations" / "capture-imports" / f"{imported['import_pin']}.json"
    import_record = json.loads(import_path.read_text())
    assert import_record["accounting"] == imported["accounting"]


def test_signature_capture_rejects_page_mutation(tmp_path):
    receipt = _capture(tmp_path / "capture")
    page = tmp_path / "capture" / "pages" / "page-0001.json"
    page.write_text(page.read_text().replace(SIGNATURES[3], SIGNATURES[0]))
    with pytest.raises(ValueError, match="signature_capture_page_checksum"):
        signature_capture.publish(receipt, tmp_path / "observations")


def test_signature_capture_rejects_unlinked_cursor(tmp_path):
    receipt = _capture(tmp_path / "capture")
    value = json.loads(receipt.read_text())
    value["pages"][1]["before_signature_sha256"] = "0" * 64
    receipt.write_text(json.dumps(value, sort_keys=True))
    with pytest.raises(ValueError, match="signature_capture_page_receipt"):
        signature_capture.publish(receipt, tmp_path / "observations")


def test_signature_capture_marks_unsealed_cursor_lineage_unknown(tmp_path):
    receipt = _capture(tmp_path / "capture")
    value = json.loads(receipt.read_text())
    del value["pages"][1]["before_signature_sha256"]
    receipt.write_text(json.dumps(value, sort_keys=True))
    result = signature_capture.publish(receipt, tmp_path / "observations")
    assert result["accounting"]["cursor_chain_verified"] is False


def test_signature_capture_rejects_overlapping_page_clock(tmp_path):
    receipt = _capture(tmp_path / "capture")
    value = json.loads(receipt.read_text())
    value["pages"][1]["request_started_ns"] = 19 * NS
    receipt.write_text(json.dumps(value, sort_keys=True))
    with pytest.raises(ValueError, match="signature_capture_page_receipt"):
        signature_capture.publish(receipt, tmp_path / "observations")


def test_signature_capture_does_not_assume_cross_page_block_time_monotonicity(tmp_path):
    receipt = _capture(tmp_path / "capture")
    page = tmp_path / "capture" / "pages" / "page-0001.json"
    rows = json.loads(page.read_text())
    rows[0]["blockTime"] = 18
    raw = json.dumps(rows, separators=(",", ":")).encode()
    page.write_bytes(raw)
    value = json.loads(receipt.read_text())
    value["pages"][1]["response_bytes"] = len(raw)
    value["pages"][1]["response_sha256"] = hashlib.sha256(raw).hexdigest()
    receipt.write_text(json.dumps(value, sort_keys=True))
    result = signature_capture.publish(receipt, tmp_path / "observations")
    assert result["accounting"]["event_time_resolved"] == 4


@pytest.mark.parametrize(
    ("field", "value"),
    [("aborted", 0), ("unique_signatures", True), ("attempts", 3), ("retries", 1)],
)
def test_signature_capture_rejects_bad_receipt_accounting(tmp_path, field, value):
    receipt = _capture(tmp_path / "capture")
    payload = json.loads(receipt.read_text())
    payload[field] = value
    receipt.write_text(json.dumps(payload, sort_keys=True))
    with pytest.raises(ValueError, match="signature_capture_receipt"):
        signature_capture.publish(receipt, tmp_path / "observations")


def test_verified_values_receipt_keeps_positive_facts_without_support_or_enrollment(tmp_path):
    receipt = _capture(tmp_path / "capture", missing_block_time=True)
    store = tmp_path / "observations"
    result = signature_capture.publish_verified_values(receipt, store, chunk=2)
    assert result["accounting"]["captured_signature_rows"] == 4
    assert result["accounting"]["event_time_resolved"] == 3
    assert result["accounting"]["unsupported_missing_block_time"] == 1
    assert result["qualification_receipt_pin"]
    assert result["verified_observation_pin"]

    source = observations.Source(store, result["verified_observation_pin"], role="train")
    assert source.manifest["binding"]["chunk"] == 2
    rows = list(source.rows())
    assert len(rows) == 3
    assert all(row["kind"] == "event" for row in rows)
    assert [row["event_ns"] for row in rows] == sorted(row["event_ns"] for row in rows)
    assert all(result["qualification_receipt_pin"] in row["evidence"] for row in rows)
    assert not any(row["kind"] in {"enrollment", "support", "lifecycle"} for row in rows)

    before = {
        path.relative_to(store).as_posix(): path.read_bytes()
        for path in store.rglob("*")
        if path.is_file()
    }
    value = qualification.load_observed_values_receipt(
        store,
        result["qualification_receipt_pin"],
        source_store=store,
        capture_root=receipt.parent,
    )
    assert value["capabilities"] == ["observed_values"]
    assert value["accounting"] == {"captured": 4, "observed": 3, "unresolved": 1}
    assert value["accounting_unit"] == "provider_signature_rows"
    after = {
        path.relative_to(store).as_posix(): path.read_bytes()
        for path in store.rglob("*")
        if path.is_file()
    }
    assert after == before
    # This event-only source has no membership and cannot establish an absent value or zero.
    with pytest.raises(ValueError, match="observation_outside_enrollment"):
        source.index(tmp_path / "index.sqlite")


def test_verified_values_rechecks_raw_capture_and_body_mutations(tmp_path):
    receipt = _capture(tmp_path / "capture")
    store = tmp_path / "observations"
    result = signature_capture.publish_verified_values(receipt, store)
    page = receipt.parent / "pages" / "page-0001.json"
    page.write_text(page.read_text().replace(SIGNATURES[3], SIGNATURES[0]))
    with pytest.raises(ValueError, match="signature_capture_page_checksum"):
        qualification.load_observed_values_receipt(
            store,
            result["qualification_receipt_pin"],
            source_store=store,
            capture_root=receipt.parent,
        )


def test_verified_values_rejects_mutated_normalized_source_body(tmp_path):
    from memecoin_experience.storage import object_path

    receipt = _capture(tmp_path / "capture")
    store = tmp_path / "observations"
    result = signature_capture.publish_verified_values(receipt, store)
    raw_source = observations.Source(store, result["observation_pin"], role="train")
    facts = object_path(store, raw_source.parts[0]["pin"]) / "facts.jsonl"
    facts.write_bytes(facts.read_bytes().replace(SIGNATURES[0].encode(), SIGNATURES[1].encode()))
    with pytest.raises(ValueError, match="experience_checksum"):
        qualification.load_observed_values_receipt(
            store,
            result["qualification_receipt_pin"],
            source_store=store,
            capture_root=receipt.parent,
        )


def test_verified_values_persists_unresolved_time_denominator_without_receipt(tmp_path):
    receipt = _capture(tmp_path / "capture", all_block_times_missing=True)
    store = tmp_path / "observations"
    result = signature_capture.publish_verified_values(receipt, store)
    assert result["verified_observation_pin"] is None
    assert result["qualification_receipt_pin"] is None
    assert result["accounting"]["captured_signature_rows"] == 4
    assert result["accounting"]["event_time_resolved"] == 0
    assert result["accounting"]["unsupported_missing_block_time"] == 4
    import_record = json.loads(
        (store / "capture-imports" / f"{result['import_pin']}.json").read_text()
    )
    assert import_record["accounting"]["coverage_complete"] is False
    assert import_record["accounting"]["absence_inference_permitted"] is False


def test_verified_values_rejects_wrong_role_and_protected_identity_before_pages(tmp_path):
    receipt = _capture(tmp_path / "capture")
    store = tmp_path / "observations"
    result = signature_capture.publish_verified_values(receipt, store)
    receipt_file = (
        store / "qualification-observed-values-v2" / f"{result['qualification_receipt_pin']}.json"
    )
    value = json.loads(receipt_file.read_text())
    value["role"] = "research_evaluation"
    # A forged role-bound pin must fail on the source role before the verifier opens capture pages.
    from memecoin_experience.storage import atomic_json, digest

    forged_pin = digest(value)
    atomic_json(store / "qualification-observed-values-v2" / f"{forged_pin}.json", value)
    (receipt.parent / "pages" / "page-0000.json").unlink()
    with pytest.raises(ValueError, match="observation_reserved_role"):
        qualification.load_observed_values_receipt(
            store, forged_pin, source_store=store, capture_root=receipt.parent
        )

    # The reserved address namespace is refused before page payloads are read.
    protected = json.loads(receipt.read_text())
    protected["address"] = "11111111111111111111111111111111"  # replaced below with protected id
    from memecoin_brain.source import EXCLUDED

    excluded = next(iter(EXCLUDED))
    protected["address"] = excluded.rsplit("/", 1)[-1]
    receipt.write_text(json.dumps(protected, sort_keys=True))
    with pytest.raises(ValueError, match="population_protected_entity"):
        signature_capture.publish_verified_values(receipt, tmp_path / "other-store")


def test_verified_values_handles_one_full_capture_page(tmp_path):
    root = tmp_path / "capture"
    (root / "pages").mkdir(parents=True)

    def encode_base58(number):
        encoded = ""
        while number:
            number, remainder = divmod(number, 58)
            encoded = BASE58_LOCAL[remainder] + encoded
        return encoded

    signatures = [encode_base58((1 << 504) + i) for i in range(1000)]
    rows = [
        dict(
            signature=signature,
            slot=1000 - i,
            blockTime=1000 - i,
            confirmationStatus="finalized",
            err=None,
        )
        for i, signature in enumerate(signatures)
    ]
    raw = json.dumps(rows, separators=(",", ":")).encode()
    (root / "pages" / "page-0000.json").write_bytes(raw)
    payload = dict(
        version="alchemy-mcp-signature-cursor-probe-v1",
        address="11111111111111111111111111111111",
        network="solana-mainnet",
        method="getSignaturesForAddress",
        commitment="finalized",
        page_limit=1000,
        attempts=1,
        retries=0,
        pages=[
            dict(
                request_started_ns=2000 * NS,
                received_ns=2001 * NS,
                request_limit=1000,
                used_before_cursor=False,
                before_signature_sha256=None,
                returned=1000,
                overlap=0,
                newest_slot=1000,
                oldest_slot=1,
                finalized=True,
                monotonic=True,
                local_seal="created",
                retried=False,
                path="pages/page-0000.json",
                response_sha256=hashlib.sha256(raw).hexdigest(),
                response_bytes=len(raw),
            )
        ],
        unique_signatures=1000,
        aborted=False,
    )
    receipt = root / "receipt.json"
    receipt.write_text(json.dumps(payload, sort_keys=True))
    result = signature_capture.publish_verified_values(receipt, tmp_path / "scale-store")
    source = observations.Source(
        tmp_path / "scale-store", result["verified_observation_pin"], role="train"
    )
    assert sum(1 for _ in source.rows()) == 1000
    assert result["accounting"]["event_time_resolved"] == 1000


def test_verified_values_replication_copies_and_revalidates_dependency_bundle(tmp_path):
    receipt = _capture(tmp_path / "capture", missing_block_time=True)
    source_store = tmp_path / "source-store"
    published = signature_capture.publish_verified_values(receipt, source_store, chunk=2)
    target_store = tmp_path / "replica-store"
    replicated = signature_capture.replicate_verified_values(
        source_store,
        target_store,
        published["qualification_receipt_pin"],
        published["verified_observation_pin"],
        capture_root=receipt.parent,
    )
    assert replicated["observed"] == 3
    target_receipt = qualification.load_observed_values_receipt(
        target_store,
        replicated["qualification_receipt_pin"],
        source_store=target_store,
        capture_root=replicated["capture_root"],
    )
    assert target_receipt["accounting"] == {"captured": 4, "observed": 3, "unresolved": 1}
    target_source = observations.Source(
        target_store, replicated["verified_observation_pin"], role="train"
    )
    assert sum(1 for _ in target_source.rows()) == 3
    assert (target_store / "capture-imports" / f"{published['import_pin']}.json").is_file()
    assert (
        target_store
        / "qualification-payload-schemas"
        / f"{target_receipt['process_contract']['payload_schema_pin']}.json"
    ).is_file()

    # The replica is self-contained: replay must continue after the origin is gone.
    import shutil

    shutil.rmtree(source_store)
    shutil.rmtree(receipt.parent)
    replayed = qualification.load_observed_values_receipt(
        target_store,
        replicated["qualification_receipt_pin"],
        source_store=target_store,
        capture_root=replicated["capture_root"],
    )
    assert replayed["source_pin"] == published["observation_pin"]


def test_verified_values_replica_rejects_missing_capture_dependency(tmp_path):
    receipt = _capture(tmp_path / "capture")
    source_store = tmp_path / "source-store"
    published = signature_capture.publish_verified_values(receipt, source_store)
    target_store = tmp_path / "replica-store"
    replicated = signature_capture.replicate_verified_values(
        source_store,
        target_store,
        published["qualification_receipt_pin"],
        published["verified_observation_pin"],
        capture_root=receipt.parent,
    )
    (tmp_path / "capture" / "pages" / "page-0000.json").unlink()
    bundled_page = Path(replicated["capture_root"]) / "pages" / "page-0000.json"
    bundled_page.unlink()
    with pytest.raises((FileNotFoundError, ValueError)):
        qualification.load_observed_values_receipt(
            target_store,
            replicated["qualification_receipt_pin"],
            source_store=target_store,
            capture_root=replicated["capture_root"],
        )


BASE58_LOCAL = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
