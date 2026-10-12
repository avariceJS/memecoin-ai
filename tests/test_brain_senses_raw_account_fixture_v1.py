from __future__ import annotations

import base64
import copy
import hashlib
import struct
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from tools import brain_senses_raw_account_fixture_schema_v1 as schema
from tools import brain_senses_raw_account_fixture_v1 as fixture


def _b58(raw: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, digit = divmod(number, 58)
        encoded = alphabet[digit] + encoded
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + encoded


def _account(
    account_id: str,
    role: str,
    address_bytes: bytes,
    owner: str,
    raw: bytes,
    *,
    lamports: int = 1_000_000,
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "role": role,
        "address": _b58(address_bytes),
        "owner": owner,
        "lamports": lamports,
        "executable": False,
        "data_b64": base64.b64encode(raw).decode("ascii"),
        "data_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _mint_raw(
    *, fee: tuple[int, int, int, int, int, int] | None = None, hook: bool = False
) -> bytes:
    raw = bytearray(166)
    raw[44] = 6
    raw[45] = 1
    raw[165] = 1
    extensions: list[tuple[int, bytes]] = []
    if fee is not None:
        older_epoch, older_cap, older_bps, newer_epoch, newer_cap, newer_bps = fee
        fee_data = (
            bytes(64)
            + bytes(8)
            + struct.pack(
                "<QQHQQH", older_epoch, older_cap, older_bps, newer_epoch, newer_cap, newer_bps
            )
        )
        assert len(fee_data) == 108
        extensions.append((1, fee_data))
    if hook:
        extensions.append((14, bytes(64)))
    for kind, body in extensions:
        raw += struct.pack("<HH", kind, len(body)) + body
    return bytes(raw)


def _token_account_raw(
    mint: str,
    authority: str,
    *,
    amount: int = 0,
    native_reserve: int | None = None,
    withheld: int | None = None,
) -> bytes:
    raw = bytearray(165)
    raw[:32] = fixture._b58decode(mint)
    raw[32:64] = fixture._b58decode(authority)
    struct.pack_into("<Q", raw, 64, amount)
    raw[108] = 1
    if native_reserve is not None:
        struct.pack_into("<I", raw, 109, 1)
        struct.pack_into("<Q", raw, 113, native_reserve)
    if withheld is not None:
        raw.extend(b"\x02")
        raw.extend(struct.pack("<HHQ", 2, 8, withheld))
    return bytes(raw)


def _base_vector(
    case_id: str,
    domain: str,
    *,
    accounts: list[dict[str, Any]],
    steps: list[dict[str, Any]] | None = None,
    epoch: int = 5,
    clock_id: str | None = None,
    rent_id: str | None = None,
    focus_id: str | None = None,
    transaction_status: str = "succeeded",
    failure_step_ordinal: int | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "domain": domain,
        "frame": {
            "slot": 500,
            "blockhash": _b58(hashlib.sha256(case_id.encode()).digest()),
            "block_time_seconds": 100,
            "capture_id": fixture.sha256(
                fixture.canonical(
                    {"slot": 500, "blockhash": _b58(hashlib.sha256(case_id.encode()).digest())}
                )
            ),
            "receipt_ns": 102_000_000_000,
            "durable_available_ns": 103_000_000_000,
        },
        "context_epoch": epoch,
        "clock_account_id": clock_id,
        "rent_account_id": rent_id,
        "focus_account_id": focus_id,
        "transaction_status": transaction_status,
        "failure_step_ordinal": failure_step_ordinal,
        "decision_ns": None,
        "accounts": accounts,
        "steps": steps or [],
    }


def _step(
    ordinal: int, kind: str, program_id: str, accounts: list[str], data: bytes, *, stack: int = 1
) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "kind": kind,
        "program_id": program_id,
        "account_ids": accounts,
        "stack_height": stack,
        "data_b64": base64.b64encode(data).decode("ascii"),
        "data_sha256": hashlib.sha256(data).hexdigest(),
        "status": "attempted",
    }


def _wsol_vector() -> dict[str, Any]:
    wallet = _b58(bytes([7]) * 32)
    wsol_account = "wsol"
    address = bytes([8]) * 32
    token = _account(
        wsol_account,
        "token_account",
        address,
        schema.TOKEN_PROGRAM_ID,
        _token_account_raw(schema.WSOL_MINT_ID, wallet, amount=51_000, native_reserve=890_000),
        lamports=941_000,
    )
    clock_raw = struct.pack("<QqQQq", 500, 100, 7, 7, 200)
    rent_raw = struct.pack("<QdB", 3480, 2.0, 50)
    clock = _account(
        "clock",
        "clock",
        fixture._b58decode(schema.CLOCK_SYSVAR_ID),
        schema.SYSVAR_OWNER_ID,
        clock_raw,
    )
    rent = _account(
        "rent", "rent", fixture._b58decode(schema.RENT_SYSVAR_ID), schema.SYSVAR_OWNER_ID, rent_raw
    )
    wsol_mint = _account(
        "wsol-mint",
        "mint",
        fixture._b58decode(schema.WSOL_MINT_ID),
        schema.TOKEN_PROGRAM_ID,
        _mint_raw()[:82],
    )
    system_create = struct.pack("<IQQ", 0, 1_000_000, 165) + fixture._b58decode(
        schema.TOKEN_PROGRAM_ID
    )
    steps = [
        _step(0, "system_create_account", schema.SYSTEM_PROGRAM_ID, [wsol_account], system_create),
        _step(
            1,
            "system_transfer",
            schema.SYSTEM_PROGRAM_ID,
            [wsol_account],
            struct.pack("<IQ", 2, 1_000_000),
        ),
        _step(
            2,
            "token_initialize_account3",
            schema.TOKEN_PROGRAM_ID,
            [wsol_account],
            b"\x12" + fixture._b58decode(wallet),
        ),
        _step(3, "token_sync_native", schema.TOKEN_PROGRAM_ID, [wsol_account], b"\x11"),
        _step(4, "token_close_account", schema.TOKEN_PROGRAM_ID, [wsol_account], b"\x09"),
    ]
    return _base_vector(
        "wsol-synthetic-order",
        "wsol_native_lifecycle",
        accounts=[token, clock, rent, wsol_mint],
        steps=steps,
        epoch=7,
        clock_id="clock",
        rent_id="rent",
        focus_id=wsol_account,
    )


def _valid_vectors() -> list[dict[str, Any]]:
    mint_address = bytes([1]) * 32
    token_address = bytes([2]) * 32
    authority = _b58(bytes([3]) * 32)
    fee_mint = _account(
        "fee-mint",
        "mint",
        mint_address,
        schema.TOKEN_2022_PROGRAM_ID,
        _mint_raw(fee=(1, 25, 100, 9, 50, 250)),
    )
    fee_token = _account(
        "fee-token",
        "token_account",
        token_address,
        schema.TOKEN_2022_PROGRAM_ID,
        _token_account_raw(_b58(mint_address), authority, amount=123, withheld=13),
    )
    no_hook = _account(
        "control-mint",
        "mint",
        bytes([4]) * 32,
        schema.TOKEN_2022_PROGRAM_ID,
        bytes(bytearray(_mint_raw())[:82]),
    )
    hook = _account(
        "hook-mint", "mint", bytes([5]) * 32, schema.TOKEN_2022_PROGRAM_ID, _mint_raw(hook=True)
    )
    cpi_account = _account(
        "cpi-mint",
        "mint",
        bytes([6]) * 32,
        schema.TOKEN_2022_PROGRAM_ID,
        bytes(bytearray(_mint_raw())[:82]),
    )
    cpi_step = _step(0, "cpi_attempt", schema.TOKEN_2022_PROGRAM_ID, ["cpi-mint"], b"\x00", stack=2)
    return [
        _base_vector(
            "fee-config-older-newer",
            "token2022_fee_config",
            accounts=[fee_mint, fee_token],
        ),
        _base_vector(
            "hook-no-hook-control",
            "transfer_hook_control",
            accounts=[no_hook],
            focus_id="control-mint",
        ),
        _base_vector(
            "hook-extension-unsupported",
            "transfer_hook_unsupported",
            accounts=[hook],
            focus_id="hook-mint",
        ),
        _wsol_vector(),
        _base_vector(
            "cpi-success-attempted-only",
            "cpi_attempt_only",
            accounts=[cpi_account],
            steps=[cpi_step],
        ),
        _base_vector(
            "cpi-failed-attempted-only",
            "cpi_attempt_only",
            accounts=[cpi_account],
            steps=[cpi_step],
            transaction_status="failed",
            failure_step_ordinal=0,
        ),
    ]


def _bundle(vectors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return fixture.build_bundle("synthetic-raw-account-fixture-set", vectors or _valid_vectors())


def test_synthetic_bundle_replays_structural_bytes_and_keeps_admission_false() -> None:
    raw = fixture.encode_bundle(_bundle())
    result = fixture.verify_bundle_bytes(raw, expected_bundle_sha256=fixture.sha256(raw))
    assert result["fixture_vectors"] == "synthetic_only"
    assert result["runtime_law_qualified"] is False
    assert result["economic_admission"] is False
    by_id = {item["case_id"]: item for item in result["vectors"]}
    fee_config = by_id["fee-config-older-newer"]["accounts"]["fee-mint"]["transfer_fee_config"]
    assert fee_config["older"] == {
        "epoch": 1,
        "maximum_fee_atomic_units": 25,
        "basis_points": 100,
    }
    assert fee_config["newer"] == {
        "epoch": 9,
        "maximum_fee_atomic_units": 50,
        "basis_points": 250,
    }
    assert (
        by_id["fee-config-older-newer"]["accounts"]["fee-token"][
            "withheld_transfer_fee_atomic_units"
        ]
        == 13
    )
    assert by_id["hook-extension-unsupported"]["accounts"]["hook-mint"]["transfer_hook_status"] == (
        "unsupported_without_runtime_evidence"
    )
    wsol = by_id["wsol-synthetic-order"]
    assert [step["kind"] for step in wsol["steps"]] == [
        "system_create_account",
        "system_transfer",
        "token_initialize_account3",
        "token_sync_native",
        "token_close_account",
    ]
    assert all(step["status"] == "attempted_only" for step in wsol["steps"])
    assert wsol["accounts"]["wsol"]["native_reserve_lamports"] == 890_000
    assert wsol["accounts"]["wsol"]["native_reserve_dimension"] == "lamports_or_null"
    assert (
        by_id["cpi-failed-attempted-only"]["replay_status"] == "attempted_only_transaction_failed"
    )


def test_external_bundle_hash_pins_rent_epoch_and_frame_bytes() -> None:
    original = fixture.encode_bundle(_bundle())
    expected = fixture.sha256(original)
    mutated = copy.deepcopy(_bundle())
    rent = mutated["vectors"][3]["accounts"][2]
    changed = struct.pack("<QdB", 3481, 2.0, 50)
    rent["data_b64"] = base64.b64encode(changed).decode("ascii")
    rent["data_sha256"] = hashlib.sha256(changed).hexdigest()
    changed_bundle = fixture.canonical(mutated)
    with pytest.raises(fixture.FixtureRejected, match="fixture_hash_mismatch"):
        fixture.verify_bundle_bytes(changed_bundle, expected_bundle_sha256=expected)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda bundle: bundle.update(runtime_law_qualified=True),
            "qualification_flags_must_be_false",
        ),
        (
            lambda bundle: bundle.update(economic_admission=True),
            "qualification_flags_must_be_false",
        ),
        (
            lambda bundle: bundle.update(source_pins={"forged": True}),
            "upstream_source_pin_mismatch",
        ),
        (
            lambda bundle: bundle.update(validator_sources={"changed.py": "0" * 64}),
            "validator_source_pin_mismatch",
        ),
    ],
)
def test_bundle_authority_and_source_mutations_fail_closed(mutate: Any, reason: str) -> None:
    bundle = copy.deepcopy(_bundle())
    mutate(bundle)
    with pytest.raises(fixture.FixtureRejected, match=reason):
        fixture.verify_bundle(bundle)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda vector: vector["accounts"][0].update(data_sha256="0" * 64),
            "account_digest_mismatch",
        ),
        (
            lambda vector: vector["accounts"][0].update(owner="11111111111111111111111111111111"),
            "token_account_owner",
        ),
        (lambda vector: vector["accounts"][1].update(data_b64="%%%"), "account_base64"),
    ],
)
def test_raw_account_owner_bytes_and_digest_mutations_fail(mutate: Any, reason: str) -> None:
    vector = copy.deepcopy(_valid_vectors()[0])
    mutate(vector)
    with pytest.raises(fixture.FixtureRejected, match=reason):
        fixture.verify_bundle(_bundle([vector]))


def test_hook_extension_and_no_hook_control_are_distinct() -> None:
    result = fixture.verify_bundle(_bundle())
    controls = {item["case_id"]: item for item in result["vectors"]}
    assert controls["hook-no-hook-control"]["accounts"]["control-mint"]["transfer_hook_status"] == (
        "no_hook_extension_observed"
    )
    assert controls["hook-extension-unsupported"]["accounts"]["hook-mint"][
        "transfer_hook_status"
    ] == ("unsupported_without_runtime_evidence")


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("duplicate_address", "duplicate_account_address"),
        ("frame_slot", "frame_capture_id"),
        ("frame_time", "frame_availability_before_receipt"),
        ("missing_mint", "token_account_mint_missing"),
        ("fee_bps", "transfer_fee_basis_points"),
        ("clock_sysvar", "clock_account_shape"),
        ("rent_sysvar", "rent_account_shape"),
    ],
)
def test_frame_account_set_and_fee_byte_mutations_fail(mutation: str, reason: str) -> None:
    vectors = copy.deepcopy(_valid_vectors())
    if mutation == "duplicate_address":
        vectors[0]["accounts"][1]["address"] = vectors[0]["accounts"][0]["address"]
    elif mutation == "frame_slot":
        vectors[0]["frame"]["slot"] += 1
    elif mutation == "frame_time":
        vectors[0]["frame"]["durable_available_ns"] = vectors[0]["frame"]["receipt_ns"] - 1
    elif mutation == "clock_sysvar":
        vectors[3]["accounts"][1]["address"] = _b58(bytes([77]) * 32)
    elif mutation == "rent_sysvar":
        vectors[3]["accounts"][2]["address"] = _b58(bytes([78]) * 32)
    elif mutation == "missing_mint":
        vectors[0]["accounts"] = vectors[0]["accounts"][1:]
    else:
        raw = bytearray(base64.b64decode(vectors[0]["accounts"][0]["data_b64"]))
        struct.pack_into("<H", raw, 166 + 4 + 88, 10_001)
        vectors[0]["accounts"][0]["data_b64"] = base64.b64encode(raw).decode("ascii")
        vectors[0]["accounts"][0]["data_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(fixture.FixtureRejected, match=reason):
        fixture.verify_bundle(_bundle(vectors))


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("clock_epoch", "clock_epoch_mismatch"),
        ("rent_hash", "account_digest_mismatch"),
        ("wsol_mint", "wsol_mint_mismatch"),
        ("wsol_order", "wsol_lifecycle_order"),
        ("attempt_result", "step_status_must_be_attempted_only"),
    ],
)
def test_native_context_and_cpi_status_mutations_fail(mutation: str, reason: str) -> None:
    vectors = copy.deepcopy(_valid_vectors())
    wsol = vectors[3]
    if mutation == "clock_epoch":
        wsol["context_epoch"] += 1
    elif mutation == "rent_hash":
        wsol["accounts"][2]["data_b64"] = base64.b64encode(bytes(17)).decode("ascii")
    elif mutation == "wsol_mint":
        new_mint = bytes([99]) * 32
        token_raw = bytearray(base64.b64decode(wsol["accounts"][0]["data_b64"]))
        token_raw[:32] = new_mint
        wsol["accounts"][0]["data_b64"] = base64.b64encode(token_raw).decode("ascii")
        wsol["accounts"][0]["data_sha256"] = hashlib.sha256(token_raw).hexdigest()
        wsol["accounts"][3]["address"] = _b58(new_mint)
    elif mutation == "wsol_order":
        wsol["steps"][1], wsol["steps"][2] = wsol["steps"][2], wsol["steps"][1]
        wsol["steps"][1]["ordinal"], wsol["steps"][2]["ordinal"] = 1, 2
    else:
        vectors[5]["steps"][0]["status"] = "executed"
    with pytest.raises(fixture.FixtureRejected, match=reason):
        fixture.verify_bundle(_bundle(vectors))


@given(
    older_epoch=st.integers(min_value=0, max_value=2**64 - 1),
    older_cap=st.integers(min_value=0, max_value=2**64 - 1),
    older_bps=st.integers(min_value=0, max_value=10_000),
    newer_epoch=st.integers(min_value=0, max_value=2**64 - 1),
    newer_cap=st.integers(min_value=0, max_value=2**64 - 1),
    newer_bps=st.integers(min_value=0, max_value=10_000),
)
@settings(max_examples=80, deadline=None)
@example(older_epoch=0, older_cap=0, older_bps=0, newer_epoch=0, newer_cap=0, newer_bps=0)
@example(
    older_epoch=1,
    older_cap=1,
    older_bps=1,
    newer_epoch=2**64 - 1,
    newer_cap=2**64 - 1,
    newer_bps=10_000,
)
@example(
    older_epoch=2**64 - 1,
    older_cap=2**64 - 1,
    older_bps=9_999,
    newer_epoch=2**64 - 1,
    newer_cap=0,
    newer_bps=10_000,
)
def test_fee_config_raw_byte_parse_preserves_dimensions_and_epoch_fields(
    older_epoch: int,
    older_cap: int,
    older_bps: int,
    newer_epoch: int,
    newer_cap: int,
    newer_bps: int,
) -> None:
    vector = _valid_vectors()[0]
    raw = _mint_raw(fee=(older_epoch, older_cap, older_bps, newer_epoch, newer_cap, newer_bps))
    vector["accounts"][0]["data_b64"] = base64.b64encode(raw).decode("ascii")
    vector["accounts"][0]["data_sha256"] = hashlib.sha256(raw).hexdigest()
    result = fixture.verify_bundle(_bundle([vector]))["vectors"][0]
    parsed = result["accounts"]["fee-mint"]["transfer_fee_config"]
    assert parsed["older"] == {
        "epoch": older_epoch,
        "maximum_fee_atomic_units": older_cap,
        "basis_points": older_bps,
    }
    assert parsed["newer"] == {
        "epoch": newer_epoch,
        "maximum_fee_atomic_units": newer_cap,
        "basis_points": newer_bps,
    }


@given(raw=st.binary(max_size=600))
@settings(max_examples=250, deadline=None)
def test_tlv_parser_arbitrary_bytes_never_escape_typed_rejection(raw: bytes) -> None:
    try:
        fixture._tlv_extensions(raw, role="mint")
    except fixture.FixtureRejected:
        return
    assert len(raw) == 82 or (len(raw) >= 166 and raw[165] == 1)


@given(raw=st.binary(max_size=900))
@settings(max_examples=250, deadline=None)
def test_canonical_byte_parser_only_returns_typed_fixture_errors(raw: bytes) -> None:
    try:
        fixture.verify_bundle_bytes(raw)
    except fixture.FixtureRejected:
        return
    assert raw.endswith(b"\n")


@given(
    value=st.recursive(
        st.none() | st.booleans() | st.integers() | st.text(max_size=12),
        lambda child: (
            st.lists(child, max_size=5) | st.dictionaries(st.text(max_size=8), child, max_size=5)
        ),
        max_leaves=20,
    )
)
@settings(max_examples=150, deadline=None)
def test_arbitrary_json_values_reject_with_typed_error_only(value: Any) -> None:
    try:
        fixture.verify_bundle(value)
    except fixture.FixtureRejected:
        return
    assert isinstance(value, dict)
