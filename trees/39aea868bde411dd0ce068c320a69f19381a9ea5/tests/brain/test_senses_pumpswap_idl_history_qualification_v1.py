from __future__ import annotations

import random

import pytest

from tools import brain_senses_pumpswap_idl_history_qualification_v1 as qualification


@pytest.mark.parametrize(
    "raw",
    [b"", b"\0", b"\0\0\x01", bytes(range(32)), bytes([255]) * 32],
)
def test_base58_round_trip_fixed_boundaries(raw: bytes) -> None:
    encoded = qualification._b58encode(raw)
    assert qualification._b58decode(encoded) == raw


def test_base58_round_trip_deterministic_32_byte_domain_sample() -> None:
    rng = random.Random(913)
    for _ in range(512):
        raw = bytes(rng.randrange(256) for _ in range(32))
        assert qualification._b58decode(qualification._b58encode(raw)) == raw


def test_event_authority_pda_matches_seed_program_and_anchor_expected_key() -> None:
    assert (
        qualification._find_program_address(b"__event_authority", qualification.PROGRAM_ID)
        == qualification.EXPECTED_EVENT_AUTHORITY
    )


def test_historical_idl_capture_manifest_reconciles_all_source_snapshots() -> None:
    versions, manifest = qualification._load_idl_history()
    assert manifest["history_entry_count"] == 18
    assert len(versions) == len(manifest["versions"]) == 18
    for version, row in zip(versions, manifest["versions"], strict=True):
        assert version["capture"]["commit"] == row["commit"]
        assert version["capture"]["sha256"] == row["sha256"]
        assert row["url"].endswith(f"/{row['commit']}/idl/pump_amm.json")
    assert not qualification._ed25519_compressed_point_is_valid(
        qualification._b58decode(qualification.EXPECTED_EVENT_AUTHORITY)
    )


def test_anchor_event_tag_is_not_an_instruction_identity() -> None:
    assert qualification.ANCHOR_EVENT_TAG_LE.hex() == "e445a52e51cb9a1d"
    assert len(qualification.ANCHOR_EVENT_TAG_LE) == 8


def test_pda_derivation_rejects_invalid_program_key_width() -> None:
    with pytest.raises(qualification.QualificationError, match="program_id_width_invalid"):
        qualification._find_program_address(b"__event_authority", "1")


def test_trace_parent_uses_nearest_active_sibling_not_any_prior_program() -> None:
    stack: list[tuple[int, str | None]] = []
    pump = qualification.PROGRAM_ID
    assert qualification._trace_parent(stack, 2, pump, pump) == pump
    assert (
        qualification._trace_parent(stack, 2, "OtherProgram1111111111111111111111111111", pump)
        == pump
    )
    parent = qualification._trace_parent(stack, 3, pump, "OuterProgram1111111111111111111111111111")
    assert parent == "OtherProgram1111111111111111111111111111"
    assert parent != pump


@pytest.mark.parametrize(
    ("stack", "height"),
    [([], 3), ([(2, "program")], 4), ([], None), ([], 1)],
)
def test_trace_parent_fails_closed_on_missing_or_discontinuous_depth(
    stack: list[tuple[int, str | None]], height: int | None
) -> None:
    with pytest.raises(qualification.QualificationError):
        qualification._trace_parent(stack, height, "child", "outer")
