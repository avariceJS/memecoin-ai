"""Read-only CPMM account decoding. This is not an executable quote adapter."""

from __future__ import annotations

import base64
import hashlib
import struct
from dataclasses import asdict, dataclass
from typing import Any

from memecoin_ai.domain import ValidationError, integer
from memecoin_ai.sources import Capture

PROGRAM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
WSOL = "So11111111111111111111111111111111111111112"
SOURCE_REVISION = "59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92"
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    result = ""
    while n:
        n, digit = divmod(n, 58)
        result = ALPHABET[digit] + result
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + result


def b58decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValidationError("invalid base58 input")
    n = 0
    for char in value:
        if char not in ALPHABET:
            raise ValidationError("invalid base58 character")
        n = n * 58 + ALPHABET.index(char)
    return b"\0" * (len(value) - len(value.lstrip("1"))) + n.to_bytes(
        (n.bit_length() + 7) // 8, "big"
    )


def public_key(value: str) -> str:
    if len(b58decode(value)) != 32:
        raise ValidationError("public key must encode 32 bytes")
    return value


def discriminator(name: str) -> bytes:
    return hashlib.sha256(name.encode()).digest()[:8]


def account_bytes(account: Any, owner: str, size: int) -> bytes:
    if not isinstance(account, dict) or account.get("owner") != owner:
        raise ValidationError("missing account or unsupported owner")
    if account.get("executable") is not False:
        raise ValidationError("unexpected executable account")
    data = account.get("data")
    if not isinstance(data, list) or len(data) != 2 or data[1] != "base64":
        raise ValidationError("expected full base64 account")
    try:
        raw = base64.b64decode(data[0], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValidationError("invalid base64 account") from exc
    if len(raw) != size:
        raise ValidationError("unsupported account size/extensions")
    return raw


@dataclass(frozen=True)
class PoolState:
    config: str
    vaults: tuple[str, str]
    mints: tuple[str, str]
    token_programs: tuple[str, str]
    decimals: tuple[int, int]
    status: int
    open_time: int
    excluded_fees: tuple[int, int]
    creator_fee_on: int
    enable_creator_fee: bool

    def addresses(self, pool_id: str) -> list[str]:
        return [public_key(pool_id), self.config, *self.vaults, *self.mints]


def decode_pool(account: Any) -> PoolState:
    raw = account_bytes(account, PROGRAM, 637)
    if raw[:8] != discriminator("account:PoolState"):
        raise ValidationError("PoolState discriminator mismatch")
    keys = [b58encode(raw[8 + i * 32 : 40 + i * 32]) for i in range(10)]
    # Packed layout; fee extensions reuse padding. Unknown bytes fail closed.
    if raw[389] > 2 or raw[390] not in (0, 1) or any(raw[391:397]) or any(raw[413:]):
        raise ValidationError("unsupported pool fee model/padding")
    if raw[329] & ~7:
        raise ValidationError("unsupported pool status bits")
    amounts = struct.unpack_from("<7Q", raw, 333)
    creators = struct.unpack_from("<2Q", raw, 397)
    return PoolState(
        keys[0],
        (keys[2], keys[3]),
        (keys[5], keys[6]),
        (keys[7], keys[8]),
        (raw[331], raw[332]),
        raw[329],
        amounts[5],
        (amounts[1] + amounts[3] + creators[0], amounts[2] + amounts[4] + creators[1]),
        raw[389],
        bool(raw[390]),
    )


def decode_config(account: Any) -> dict[str, int]:
    raw = account_bytes(account, PROGRAM, 236)
    if raw[:8] != discriminator("account:AmmConfig") or raw[9] not in (0, 1):
        raise ValidationError("invalid AmmConfig header")
    if any(raw[116:]):
        raise ValidationError("unsupported AmmConfig padding")
    trade, protocol, fund = struct.unpack_from("<3Q", raw, 12)
    creator = struct.unpack_from("<Q", raw, 108)[0]
    if trade + creator >= 1_000_000 or protocol + fund > 1_000_000:
        raise ValidationError("invalid fee rates")
    return dict(
        trade_ppm=trade, protocol_share_ppm=protocol, fund_share_ppm=fund, creator_ppm=creator
    )


def decode_vault(account: Any, mint: str) -> tuple[int, str]:
    raw = account_bytes(account, TOKEN, 165)
    if b58encode(raw[:32]) != mint or raw[108] != 1:
        raise ValidationError("vault mint mismatch or vault not initialized/unfrozen")
    # Delegation/close authority would need separate execution semantics.
    if struct.unpack_from("<I", raw, 72)[0] != 0 or struct.unpack_from("<I", raw, 129)[0] != 0:
        raise ValidationError("unsupported vault authority options")
    return struct.unpack_from("<Q", raw, 64)[0], b58encode(raw[32:64])


def decode_mint(account: Any, decimals: int) -> dict[str, bool]:
    raw = account_bytes(account, TOKEN, 82)
    mint_option = struct.unpack_from("<I", raw, 0)[0]
    freeze_option = struct.unpack_from("<I", raw, 46)[0]
    if (
        raw[44] != decimals
        or raw[45] != 1
        or mint_option not in (0, 1)
        or freeze_option not in (0, 1)
    ):
        raise ValidationError("invalid mint state")
    return dict(
        mint_authority_present=bool(mint_option), freeze_authority_present=bool(freeze_option)
    )


def decode_checkpoint(capture: Capture, pool_id: str) -> dict[str, Any]:
    import json

    request = json.loads(capture.request_json)
    if capture.method != "getMultipleAccounts" or request["method"] != capture.method:
        raise ValidationError("checkpoint requires one getMultipleAccounts response")
    addresses, options = request["params"]
    if (
        options.get("commitment") != "finalized"
        or options.get("encoding") != "base64"
        or "dataSlice" in options
    ):
        raise ValidationError("checkpoint requires full finalized accounts")
    result = capture.result()
    if not isinstance(result, dict):
        raise ValidationError("missing checkpoint")
    slot = result["context"]["slot"]
    integer(slot, "context_slot")
    values = result["value"]
    if not isinstance(values, list) or len(values) != 6:
        raise ValidationError("checkpoint requires pool/config/two vaults/two mints")
    pool = decode_pool(values[0])
    if addresses != pool.addresses(pool_id):
        raise ValidationError("checkpoint account linkage mismatch")
    if slot < options.get("minContextSlot", 0):
        raise ValidationError("checkpoint context regressed")
    if pool.token_programs != (TOKEN, TOKEN):
        raise ValidationError("Token-2022 is outside the current decoder domain")
    fees = decode_config(values[1])
    vaults = [decode_vault(values[2 + i], pool.mints[i]) for i in range(2)]
    if vaults[0][1] != vaults[1][1]:
        raise ValidationError("vault authorities disagree")
    mints = [decode_mint(values[4 + i], pool.decimals[i]) for i in range(2)]
    reserves = [vaults[i][0] - pool.excluded_fees[i] for i in range(2)]
    if min(reserves) < 0:
        raise ValidationError("excluded fees exceed vault balance")
    return dict(
        schema_version=1,
        pool_id=pool_id,
        context_slot=slot,
        raw_received_ns=capture.received_ns,
        historical_event_time=None,
        availability_semantics="raw checkpoint receipt; not historical or feature availability",
        source_revision=SOURCE_REVISION,
        source_matches_deployed_binary=False,
        pool=asdict(pool),
        fees=fees,
        vault_balances=[v[0] for v in vaults],
        effective_reserves=reserves,
        mint_flags=mints,
        sol_pair=WSOL in pool.mints,
        execution_qualified=False,
    )


def swap_candidates(transaction: Any) -> list[str]:
    """Discovery evidence only. It does not establish universe completeness."""
    if not isinstance(transaction, dict) or transaction.get("meta") is None:
        raise ValidationError("transaction metadata unavailable")
    version = transaction.get("version")
    if version != "legacy" and (type(version) is not int or version not in (0, 1)):
        raise ValidationError("unsupported transaction version")
    if transaction["meta"].get("err") is not None:
        return []
    instructions = list(transaction["transaction"]["message"]["instructions"])
    for inner in transaction["meta"].get("innerInstructions") or []:
        instructions.extend(inner["instructions"])
    pools = []
    accepted = {discriminator("global:swap_base_input"), discriminator("global:swap_base_output")}
    for instruction in instructions:
        if instruction.get("programId") != PROGRAM:
            continue
        raw = b58decode(instruction.get("data", ""))
        if raw[:8] not in accepted:
            continue
        if len(raw) != 24 or len(instruction.get("accounts", [])) < 13:
            raise ValidationError("unsupported swap instruction layout")
        pools.append(public_key(instruction["accounts"][3]))
    return list(dict.fromkeys(pools))


TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_SOURCE_REVISION = "5bc7ea3b9568b9fdac2d08c921d73d40260249c7"


def token_inventory(account: Any, *, mint: bool) -> dict[str, Any]:
    """Inspect extension framing; unknown economic semantics remain unsupported."""
    if not isinstance(account, dict) or account.get("owner") not in (TOKEN, TOKEN_2022):
        raise ValidationError("unsupported token owner")
    owner = account["owner"]
    try:
        size = len(base64.b64decode(account["data"][0], validate=True))
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        raise ValidationError("invalid token account data") from exc
    raw = account_bytes(account, owner, size)
    base_size = 82 if mint else 165
    if size < base_size or size == 355:
        raise ValidationError("invalid token base size/multisig")
    extensions: list[dict[str, int]] = []
    if size != base_size:
        if owner != TOKEN_2022 or size < 166 or raw[165] != (1 if mint else 2):
            raise ValidationError("invalid token extension account type")
        if mint and any(raw[82:165]):
            raise ValidationError("invalid mint padding")
        position = 166
        seen = set()
        while position < len(raw):
            if not any(raw[position:]):
                break
            if len(raw) - position < 4:
                raise ValidationError("truncated extension header")
            kind, length = struct.unpack_from("<HH", raw, position)
            position += 4
            if kind == 0 or kind in seen or position + length > len(raw):
                raise ValidationError("invalid/duplicate/truncated extension")
            seen.add(kind)
            extensions.append(dict(type_id=kind, length=length))
            position += length
    result: dict[str, Any] = dict(
        owner=owner, size=size, extensions=extensions, transfer_semantics_verified=False
    )
    if mint:
        result.update(decimals=raw[44], initialized=raw[45] == 1)
    else:
        result.update(
            mint=b58encode(raw[:32]),
            state=raw[108],
            base_amount=struct.unpack_from("<Q", raw, 64)[0],
        )
    return result


def checkpoint_inventory(capture: Capture, pool_id: str) -> dict[str, Any]:
    import json

    request = json.loads(capture.request_json)
    if capture.method != "getMultipleAccounts" or request["method"] != capture.method:
        raise ValidationError("inventory requires getMultipleAccounts")
    result = capture.result()
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("value"), list)
        or len(result["value"]) != 6
    ):
        raise ValidationError("invalid inventory response")
    values = result["value"]
    pool = decode_pool(values[0])
    addresses, options = request["params"]
    if (
        addresses != pool.addresses(pool_id)
        or options.get("commitment") != "finalized"
        or options.get("encoding") != "base64"
        or "dataSlice" in options
    ):
        raise ValidationError("invalid inventory linkage/options")
    slot = result["context"]["slot"]
    integer(slot, "context slot")
    if slot < options.get("minContextSlot", 0):
        raise ValidationError("inventory context regressed")
    vaults = [token_inventory(v, mint=False) for v in values[2:4]]
    mints = [token_inventory(v, mint=True) for v in values[4:6]]
    for i in range(2):
        if (
            vaults[i]["mint"] != pool.mints[i]
            or vaults[i]["owner"] != pool.token_programs[i]
            or mints[i]["owner"] != pool.token_programs[i]
            or mints[i]["decimals"] != pool.decimals[i]
        ):
            raise ValidationError("token inventory linkage mismatch")
    return dict(
        pool_id=pool_id,
        context_slot=slot,
        raw_received_ns=capture.received_ns,
        token_source_revision=TOKEN_SOURCE_REVISION,
        pool=asdict(pool),
        fees=decode_config(values[1]),
        vault_inventory=vaults,
        mint_inventory=mints,
        execution_qualified=False,
        semantics="structural inventory only; extension payloads uninterpreted",
    )
