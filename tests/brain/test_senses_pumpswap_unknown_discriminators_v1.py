from __future__ import annotations

import copy
import hashlib
import itertools
import json

import pytest

from tools import brain_senses_pumpswap_unknown_discriminators_v1 as inventory


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _class_row(
    discriminator: str,
    *,
    count: int,
    wire_length: int,
    status: str,
    first_seen: str = inventory.INCLUDED_BUCKETS[0],
    also_seen: list[str] | None = None,
) -> dict[str, object]:
    fingerprint = {
        "instruction_kind": "unknown_discriminator",
        "discriminator_hex": discriminator,
        "fixed_account_count": None,
        "wire_data_length": wire_length,
        "transaction_status": status,
    }
    return {
        "class_sha256": _sha(_canonical(fingerprint)),
        "count": count,
        "first_seen_in": first_seen,
        "also_seen_in": also_seen or [],
        "fingerprint": fingerprint,
    }


def _valid_source() -> tuple[bytes, dict[str, object]]:
    path = inventory.ROOT / inventory.SOURCE_RECEIPT
    data = path.read_bytes()
    return data, json.loads(data)


def test_source_receipt_and_program_idl_are_pinned() -> None:
    data, receipt = _valid_source()
    inventory._validate_source_receipt(data, receipt)


def test_source_receipt_byte_mutation_is_rejected() -> None:
    data, receipt = _valid_source()
    with pytest.raises(inventory.InventoryError, match="source_receipt_hash_mismatch"):
        inventory._validate_source_receipt(data + b" ", receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pump_amm_idl_commit", "0" * 40),
        ("pump_amm_idl_sha256", "0" * 64),
        ("pump_amm_program_id", "wrong-program"),
    ],
)
def test_wrong_idl_or_program_pin_is_rejected(field: str, value: str) -> None:
    data, receipt = _valid_source()
    mutated = copy.deepcopy(receipt)
    mutated["source_pins"][field] = value
    with pytest.raises(inventory.InventoryError, match="source_idl_or_program_pin_mismatch"):
        inventory._validate_source_receipt(data, mutated)


def test_tail2_inclusion_or_missing_exclusion_is_rejected() -> None:
    data, receipt = _valid_source()
    included = copy.deepcopy(receipt)
    included["frozen_manifest"]["included_buckets"].append(inventory.EXCLUDED_TAIL2)
    with pytest.raises(inventory.InventoryError, match="included_frame_set_mismatch"):
        inventory._validate_source_receipt(data, included)
    missing_exclusion = copy.deepcopy(receipt)
    missing_exclusion["frozen_manifest"]["excluded_buckets"] = []
    with pytest.raises(inventory.InventoryError, match="tail2_exclusion_invalid"):
        inventory._validate_source_receipt(data, missing_exclusion)


def test_compressed_and_decoded_artifact_mutations_are_rejected() -> None:
    compressed = b"compressed"
    decoded = b"decoded"
    artifact = {
        "compressed_bytes": len(compressed),
        "compressed_sha256": _sha(compressed),
        "uncompressed_bytes": len(decoded),
        "uncompressed_sha256": _sha(decoded),
    }
    inventory._validate_artifact_binding(artifact, compressed, decoded)
    with pytest.raises(inventory.InventoryError, match="compressed_detail_binding_mismatch"):
        inventory._validate_artifact_binding(artifact, compressed + b"x", decoded)
    with pytest.raises(inventory.InventoryError, match="uncompressed_detail_binding_mismatch"):
        inventory._validate_artifact_binding(artifact, compressed, decoded + b"x")


def test_class_aggregation_is_permutation_invariant_and_partitions_each_group() -> None:
    rows = [
        _class_row("e445a52e51cb9a1d", count=2, wire_length=24, status="success"),
        _class_row(
            "e445a52e51cb9a1d",
            count=1,
            wire_length=32,
            status="failed",
            first_seen=inventory.INCLUDED_BUCKETS[1],
        ),
        _class_row("c2ab1c46684d5b2f", count=3, wire_length=8, status="success"),
    ]
    expected = None
    for permutation in itertools.permutations(rows):
        grouped, kind_counts = inventory._validate_class_inventory(
            list(permutation),
            structural_refs=6,
            expected_classes=3,
            expected_class_count=3,
            expected_instruction_refs=6,
        )
        result = inventory._summarize_discriminators(grouped)
        assert kind_counts["unknown_discriminator"] == 6
        if expected is None:
            expected = result
        assert result == expected
    assert expected is not None
    assert sum(row["instruction_references"] for row in expected) == 6
    for row in expected:
        assert sum(row["observed_data_length_counts"].values()) == row["instruction_references"]
        assert (
            sum(row["transaction_status_reference_counts"].values())
            == row["instruction_references"]
        )


def test_duplicate_class_and_missing_or_extra_class_rows_fail_closed() -> None:
    rows = [
        _class_row("e445a52e51cb9a1d", count=1, wire_length=24, status="success"),
        _class_row("c2ab1c46684d5b2f", count=1, wire_length=8, status="failed"),
    ]
    with pytest.raises(inventory.InventoryError, match="class_row_invalid_or_duplicate"):
        inventory._validate_class_inventory(
            [rows[0], rows[0]],
            structural_refs=2,
            expected_classes=2,
            expected_class_count=2,
            expected_instruction_refs=2,
        )
    for changed in (rows[:1], rows + [rows[0]]):
        with pytest.raises(inventory.InventoryError, match="class_inventory_count_mismatch"):
            inventory._validate_class_inventory(
                changed,
                structural_refs=2,
                expected_classes=len(changed),
                expected_class_count=2,
                expected_instruction_refs=2,
            )


def test_missing_or_extra_reference_counts_fail_closed() -> None:
    rows = [
        _class_row("e445a52e51cb9a1d", count=2, wire_length=24, status="success"),
        _class_row("c2ab1c46684d5b2f", count=1, wire_length=8, status="failed"),
    ]
    for changed_total in (2, 4):
        with pytest.raises(inventory.InventoryError, match="instruction_reference_total_mismatch"):
            inventory._validate_class_inventory(
                rows,
                structural_refs=changed_total,
                expected_classes=2,
                expected_class_count=2,
                expected_instruction_refs=3,
            )
    with pytest.raises(inventory.InventoryError, match="unique_class_count_mismatch"):
        inventory._validate_class_inventory(
            rows,
            structural_refs=3,
            expected_classes=3,
            expected_class_count=2,
            expected_instruction_refs=3,
        )


def test_invalid_discriminator_class_is_rejected() -> None:
    row = _class_row("e445a52e51cb9a1d", count=1, wire_length=24, status="success")
    row["fingerprint"]["discriminator_hex"] = "xyz"
    row["class_sha256"] = _sha(_canonical(row["fingerprint"]))
    with pytest.raises(inventory.InventoryError, match="unknown_discriminator_class_invalid"):
        inventory._validate_class_inventory(
            [row],
            structural_refs=1,
            expected_classes=1,
            expected_class_count=1,
            expected_instruction_refs=1,
        )
