"""Versioned Token-2022 economic allowlist. Inventory alone never grants admission."""

from __future__ import annotations

import json
import struct
from dataclasses import asdict, dataclass
from typing import Any

from memecoin_ai.cpmm import EconomicState, Fees, SwapQuote, ceil_div, u64
from memecoin_ai.domain import ValidationError, digest, integer
from memecoin_ai.raydium import (
    SOURCE_REVISION,
    TOKEN,
    TOKEN_2022,
    TOKEN_SOURCE_REVISION,
    WSOL,
    account_bytes,
    b58encode,
    decode_checkpoint,
    decode_config,
    decode_pool,
    token_inventory,
)
from memecoin_ai.sources import Capture

CLOCK = "SysvarC1ock11111111111111111111111111111111"
SYSVAR_OWNER = "Sysvar1111111111111111111111111111111111111"


@dataclass(frozen=True)
class TransferFee:
    epoch: int = 0
    maximum: int = 0
    bps: int = 0

    def __post_init__(self) -> None:
        u64(self.epoch)
        u64(self.maximum)
        if type(self.bps) is not int or not 0 <= self.bps <= 10000:
            raise ValidationError("invalid transfer fee basis points")

    def fee(self, gross: int) -> int:
        return min(ceil_div(u64(gross) * self.bps, 10000), self.maximum)

    def gross(self, net: int) -> int:
        u64(net)
        if not net or not self.bps:
            return net
        result = (
            net + self.maximum
            if self.bps == 10000
            else min(ceil_div(net * 10000, 10000 - self.bps), net + self.maximum)
        )
        u64(result)
        if result - self.fee(result) != net:
            raise ValidationError("transfer inverse mismatch")
        return result


def extension_data(account: dict[str, Any], *, mint: bool) -> tuple[bytes, dict[int, bytes]]:
    inventory = token_inventory(account, mint=mint)
    raw = account_bytes(account, inventory["owner"], inventory["size"])
    extensions = {}
    offset = 166
    for extension in inventory["extensions"]:
        length = extension["length"]
        extensions[extension["type_id"]] = raw[offset + 4 : offset + 4 + length]
        offset += 4 + length
    return raw, extensions


def _metadata(raw: bytes) -> None:
    if len(raw) < 80:
        raise ValidationError("truncated token metadata")
    offset = 64

    def string() -> None:
        nonlocal offset
        if offset + 4 > len(raw):
            raise ValidationError("truncated metadata string")
        size = struct.unpack_from("<I", raw, offset)[0]
        offset += 4
        if offset + size > len(raw):
            raise ValidationError("truncated metadata content")
        raw[offset : offset + size].decode("utf-8")
        offset += size

    for _ in range(3):
        string()
    if offset + 4 > len(raw):
        raise ValidationError("missing metadata map")
    count = struct.unpack_from("<I", raw, offset)[0]
    offset += 4
    if count > (len(raw) - offset) // 8:
        raise ValidationError("invalid metadata map size")
    for _ in range(count):
        string()
        string()
    if offset != len(raw):
        raise ValidationError("metadata trailing bytes")


def mint_terms(account: dict[str, Any], decimals: int, epoch: int) -> dict[str, Any]:
    u64(epoch)
    raw, ext = extension_data(account, mint=True)
    if raw[44] != decimals or raw[45] != 1:
        raise ValidationError("mint decimals/initialization mismatch")
    flags = [struct.unpack_from("<I", raw, offset)[0] for offset in (0, 46)]
    if any(flag not in (0, 1) for flag in flags):
        raise ValidationError("invalid mint authority option")
    if set(ext) - {1, 18, 19}:
        raise ValidationError("unsupported mint extensions:" + str(sorted(set(ext) - {1, 18, 19})))
    if 18 in ext and len(ext[18]) != 64:
        raise ValidationError("invalid metadata pointer")
    if 19 in ext:
        _metadata(ext[19])
    older = newer = TransferFee()
    if 1 in ext:
        if len(ext[1]) != 108:
            raise ValidationError("invalid transfer fee config length")
        older = TransferFee(*struct.unpack_from("<QQH", ext[1], 72))
        newer = TransferFee(*struct.unpack_from("<QQH", ext[1], 90))
    active = newer if epoch >= newer.epoch else older
    # Mint-level harvested withheld tokens do not change transfer rates or vault liquidity.
    economic = raw[:36] + raw[44:82] + (ext[1][:64] + ext[1][72:] if 1 in ext else b"")
    return dict(
        owner=account["owner"],
        flags=dict(mint_authority=bool(flags[0]), freeze_authority=bool(flags[1])),
        extensions=sorted(ext),
        transfer_fee=asdict(active),
        older=asdict(older),
        newer=asdict(newer),
        economic_hash=digest(economic),
    )


def vault_terms(account: dict[str, Any], mint: str) -> dict[str, Any]:
    raw, ext = extension_data(account, mint=False)
    if b58encode(raw[:32]) != mint or raw[108] != 1:
        raise ValidationError("vault mint/frozen/initialization mismatch")
    if any(struct.unpack_from("<I", raw, pos)[0] for pos in (72, 129)):
        raise ValidationError("delegated/closable vault unsupported")
    if (
        struct.unpack_from("<I", raw, 109)[0] not in (0, 1)
        or struct.unpack_from("<Q", raw, 121)[0] != 0
    ):
        raise ValidationError("invalid native option or stale delegated amount")
    if set(ext) - {2, 7}:
        raise ValidationError("unsupported vault extensions:" + str(sorted(set(ext) - {2, 7})))
    if (2 in ext and len(ext[2]) != 8) or (7 in ext and ext[7]):
        raise ValidationError("invalid vault extension size")
    return dict(
        amount=struct.unpack_from("<Q", raw, 64)[0],
        authority=b58encode(raw[32:64]),
        withheld=struct.unpack("<Q", ext[2])[0] if 2 in ext else 0,
        extensions=sorted(ext),
    )


def decode_supported_checkpoint(capture: Capture, pool_id: str) -> dict[str, Any]:
    request = json.loads(capture.request_json)
    addresses, options = request["params"]
    if len(addresses) == 6:
        return decode_checkpoint(capture, pool_id)
    result = capture.result()
    if (
        capture.method != "getMultipleAccounts"
        or request["method"] != capture.method
        or options.get("commitment") != "finalized"
        or options.get("encoding") != "base64"
        or "dataSlice" in options
        or not isinstance(result, dict)
    ):
        raise ValidationError("invalid extended checkpoint request")
    slot = result["context"]["slot"]
    integer(slot, "context slot")
    if slot < options.get("minContextSlot", 0):
        raise ValidationError("checkpoint context regressed")
    values = result["value"]
    if not isinstance(values, list) or len(values) != 7:
        raise ValidationError("extended checkpoint requires seven accounts including Clock")
    pool = decode_pool(values[0])
    if addresses != [*pool.addresses(pool_id), CLOCK]:
        raise ValidationError("extended checkpoint linkage mismatch")
    clock = account_bytes(values[6], SYSVAR_OWNER, 40)
    clock_slot, _, epoch, _, timestamp = struct.unpack("<QqQQq", clock)
    if clock_slot != slot or timestamp < 0:
        raise ValidationError("Clock does not match checkpoint bank")
    mints = [mint_terms(values[4 + i], pool.decimals[i], epoch) for i in range(2)]
    vaults = [vault_terms(values[2 + i], pool.mints[i]) for i in range(2)]
    for i in range(2):
        if (
            values[2 + i]["owner"] != pool.token_programs[i]
            or values[4 + i]["owner"] != pool.token_programs[i]
            or pool.token_programs[i] not in (TOKEN, TOKEN_2022)
            or (1 in mints[i]["extensions"]) != (2 in vaults[i]["extensions"])
        ):
            raise ValidationError("mint/vault token program or fee extension mismatch")
    if vaults[0]["authority"] != vaults[1]["authority"]:
        raise ValidationError("vault authorities disagree")
    reserves = [vaults[i]["amount"] - pool.excluded_fees[i] for i in range(2)]
    if min(reserves) < 0:
        raise ValidationError("fees exceed spendable vault balance")
    return dict(
        schema_version=2,
        pool_id=pool_id,
        context_slot=slot,
        raw_received_ns=capture.received_ns,
        historical_event_time=None,
        availability_semantics="raw checkpoint receipt; not historical or feature availability",
        source_revision=SOURCE_REVISION,
        token_source_revision=TOKEN_SOURCE_REVISION,
        source_matches_deployed_binary=False,
        pool=asdict(pool),
        fees=decode_config(values[1]),
        vault_balances=[v["amount"] for v in vaults],
        effective_reserves=reserves,
        vault_withheld=[v["withheld"] for v in vaults],
        mint_terms=mints,
        mint_flags=[m["flags"] for m in mints],
        clock_epoch=epoch,
        clock_timestamp=timestamp,
        sol_pair=WSOL in pool.mints,
        execution_qualified=False,
    )


def transfer_swap(
    state: EconomicState,
    fees: Fees,
    input_index: int,
    amount: int,
    limit: int,
    *,
    exact_input: bool,
    creator_fee_on: int,
    creator_enabled: bool,
    token_fees: tuple[TransferFee, TransferFee],
) -> tuple[SwapQuote, int, int, int, int]:
    from memecoin_ai.cpmm import U64, swap

    u64(amount)
    u64(limit)
    if type(exact_input) is not bool or type(input_index) is not int or input_index not in (0, 1):
        raise ValidationError("invalid swap direction")
    incoming, outgoing = token_fees[input_index], token_fees[1 - input_index]
    if exact_input:
        gross_in = amount
        input_fee = incoming.fee(gross_in)
        q = swap(
            state,
            fees,
            input_index,
            gross_in - input_fee,
            0,
            exact_input=True,
            creator_fee_on=creator_fee_on,
            creator_enabled=creator_enabled,
        )
        output_fee = outgoing.fee(q.output_amount)
        net_out = q.output_amount - output_fee
        if not net_out or net_out < limit:
            raise ValidationError("minimum net output not met")
    else:
        gross_out = outgoing.gross(amount)
        q = swap(
            state,
            fees,
            input_index,
            gross_out,
            U64,
            exact_input=False,
            creator_fee_on=creator_fee_on,
            creator_enabled=creator_enabled,
        )
        gross_in = incoming.gross(q.input_amount)
        input_fee = gross_in - q.input_amount
        output_fee = gross_out - amount
        net_out = amount
        if gross_in > limit:
            raise ValidationError("maximum gross input exceeded")
    return q, gross_in, net_out, input_fee, output_fee
