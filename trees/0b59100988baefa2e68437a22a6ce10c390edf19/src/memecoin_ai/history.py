"""Ordered economic parameters; never infer an intermediate value from the right endpoint."""

from __future__ import annotations

import base64
import copy
import json
import struct
from dataclasses import dataclass
from typing import Any

from memecoin_ai.cpmm import U64, Fees, u64
from memecoin_ai.domain import ValidationError, canonical
from memecoin_ai.raydium import (
    PROGRAM,
    TOKEN,
    TOKEN_2022,
    account_bytes,
    b58decode,
    decode_config,
    discriminator,
)
from memecoin_ai.sources import Capture
from memecoin_ai.tokens import TransferFee, decode_supported_checkpoint, extension_data, mint_terms


@dataclass(frozen=True)
class EpochSchedule:
    slots_per_epoch: int
    first_normal_epoch: int
    first_normal_slot: int

    def __post_init__(self) -> None:
        for value in (self.slots_per_epoch, self.first_normal_epoch, self.first_normal_slot):
            u64(value)
        if not self.slots_per_epoch:
            raise ValidationError("zero slots per epoch")

    @classmethod
    def from_capture(cls, capture: Capture) -> EpochSchedule:
        request = json.loads(capture.request_json)
        result = capture.result()
        if (
            capture.method != "getEpochSchedule"
            or request["params"] != []
            or not isinstance(result, dict)
            or type(result.get("warmup")) is not bool
        ):
            raise ValidationError("invalid epoch schedule capture")
        values = [result[k] for k in ("slotsPerEpoch", "firstNormalEpoch", "firstNormalSlot")]
        for value in values:
            u64(value)
        if not values[0] or (not result["warmup"] and (values[1] or values[2])):
            raise ValidationError("invalid normal epoch schedule")
        return cls(*values)

    def epoch(self, slot: int) -> int:
        u64(slot)
        if slot < self.first_normal_slot:
            raise ValidationError("warmup epoch reconstruction unsupported")
        return u64(
            self.first_normal_epoch + (slot - self.first_normal_slot) // self.slots_per_epoch
        )


def _replace_data(account: dict[str, Any], raw: bytes | bytearray) -> None:
    account["data"] = [base64.b64encode(raw).decode(), "base64"]


class ParameterHistory:
    def __init__(self, capture: Capture, schedule: EpochSchedule) -> None:
        decode_supported_checkpoint(capture, json.loads(capture.request_json)["params"][0][0])
        result = capture.result()
        if not isinstance(result, dict):
            raise ValidationError("invalid parameter anchor")
        self.accounts = copy.deepcopy(result["value"])
        self.addresses = json.loads(capture.request_json)["params"][0]
        self.schedule = schedule
        self.slot = result["context"]["slot"]
        self.epoch = schedule.epoch(self.slot)
        if len(self.accounts) != 7:
            raise ValidationError("parameter history requires a Clock checkpoint")
        clock = base64.b64decode(self.accounts[6]["data"][0], validate=True)
        if struct.unpack_from("<Q", clock, 16)[0] != self.epoch:
            raise ValidationError("epoch schedule disagrees with checkpoint Clock")

    def advance(self, slot: int) -> None:
        if slot < self.slot:
            raise ValidationError("parameter history slot regressed")
        self.epoch = self.schedule.epoch(slot)
        self.slot = slot

    @property
    def fees(self) -> Fees:
        return Fees(**decode_config(self.accounts[1]))

    def terms(self) -> list[dict[str, Any]]:
        return [
            mint_terms(a, base64.b64decode(a["data"][0])[44], self.epoch)
            for a in self.accounts[4:6]
        ]

    def token_fees(self) -> tuple[TransferFee, TransferFee]:
        terms = self.terms()
        return TransferFee(**terms[0]["transfer_fee"]), TransferFee(**terms[1]["transfer_fee"])

    def assert_endpoint(self, capture: Capture) -> None:
        other = ParameterHistory(capture, self.schedule)
        if other.addresses != self.addresses:
            raise ValidationError("parameter endpoint identity changed")
        self.advance(other.slot)
        if account_bytes(self.accounts[1], PROGRAM, 236) != account_bytes(
            other.accounts[1], PROGRAM, 236
        ):
            raise ValidationError("terminal config mismatch")
        if [t["economic_hash"] for t in self.terms()] != [
            t["economic_hash"] for t in other.terms()
        ]:
            raise ValidationError("terminal mint economic terms mismatch")

    def apply(self, ix: dict[str, Any], *, has_children: bool) -> dict[str, Any] | None:
        program = ix["programId"]
        if program == PROGRAM and self.addresses[1] in ix.get("accounts", []):
            raw = b58decode(ix["data"])
            if raw[:8] != discriminator("global:update_amm_config"):
                # Other known pool operations only read this shared config.
                if raw[:8] in {
                    discriminator("global:" + k)
                    for k in (
                        "swap_base_input",
                        "swap_base_output",
                        "initialize",
                        "collect_protocol_fee",
                        "collect_fund_fee",
                        "collect_creator_fee",
                    )
                }:
                    return None
                raise ValidationError("unsupported config operation")
            if has_children or len(raw) != 17 or ix["accounts"][1] != self.addresses[1]:
                raise ValidationError("invalid config update layout")
            param, value = struct.unpack_from("<BQ", raw, 8)
            updated = bytearray(account_bytes(self.accounts[1], PROGRAM, 236))
            if param in (0, 1, 2, 5, 7):
                struct.pack_into("<Q", updated, {0: 12, 1: 20, 2: 28, 5: 36, 7: 108}[param], value)
            elif param in (3, 4):
                owner = b58decode(ix["accounts"][2])
                if len(owner) != 32 or not any(owner):
                    raise ValidationError("invalid config owner")
                start = 44 if param == 3 else 76
                updated[start : start + 32] = owner
            elif param == 6:
                updated[9] = int(value != 0)
            else:
                raise ValidationError("unsupported config parameter")
            candidate = copy.deepcopy(self.accounts[1])
            _replace_data(candidate, updated)
            Fees(**decode_config(candidate))
            self.accounts[1] = candidate
            return dict(kind="update_amm_config", parameter=param, value=value, quote_checked=False)
        if program not in (TOKEN, TOKEN_2022):
            return None
        parsed = ix.get("parsed", {})
        info = parsed.get("info", {})
        mint = info.get("mint")
        if mint not in self.addresses[4:6]:
            if any(m in canonical(ix) for m in self.addresses[4:6]):
                raise ValidationError("unparsed/unattributed mint operation")
            return None
        kind = parsed.get("type")
        # These change token accounts / mint withheld only, not supply or fee schedules.
        if kind in (
            "getAccountDataSize",
            "initializeAccount",
            "initializeAccount2",
            "initializeAccount3",
            "transferChecked",
            "transferCheckedWithFee",
            "harvestWithheldTokensToMint",
            "withdrawWithheldTokensFromMint",
            "withdrawWithheldTokensFromAccounts",
        ):
            return None
        index = self.addresses.index(mint)
        account = copy.deepcopy(self.accounts[index])
        raw, extensions = extension_data(account, mint=True)
        updated = bytearray(raw)
        if has_children or account["owner"] != program:
            raise ValidationError("invalid mint update invocation")
        if kind == "setTransferFee" and program == TOKEN_2022 and 1 in extensions:
            bps = info["transferFeeBasisPoints"]
            maximum = _amount(info["maximumFee"])
            newer = TransferFee(min(U64, self.epoch + 2), maximum, bps)
            offset = 166
            while struct.unpack_from("<H", raw, offset)[0] != 1:
                offset += 4 + struct.unpack_from("<H", raw, offset + 2)[0]
            offset += 4
            old_newer = TransferFee(*struct.unpack_from("<QQH", raw, offset + 90))
            if old_newer.epoch <= self.epoch:
                updated[offset + 72 : offset + 90] = raw[offset + 90 : offset + 108]
            struct.pack_into("<QQH", updated, offset + 90, newer.epoch, newer.maximum, newer.bps)
        elif kind in ("mintTo", "mintToChecked", "burn", "burnChecked"):
            amount = _amount(
                info["tokenAmount"]["amount"] if kind.endswith("Checked") else info["amount"]
            )
            supply = struct.unpack_from("<Q", raw, 36)[0]
            struct.pack_into(
                "<Q", updated, 36, u64(supply + (amount if kind.startswith("mint") else -amount))
            )
        else:
            raise ValidationError("unsupported mint parameter mutation:" + str(kind))
        _replace_data(account, updated)
        mint_terms(account, raw[44], self.epoch)
        self.accounts[index] = account
        return dict(kind=kind, mint=mint, epoch=self.epoch, quote_checked=False)


def _amount(value: object) -> int:
    if type(value) is int:
        return u64(value)
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return u64(int(value))
    raise ValidationError("invalid raw token amount")
