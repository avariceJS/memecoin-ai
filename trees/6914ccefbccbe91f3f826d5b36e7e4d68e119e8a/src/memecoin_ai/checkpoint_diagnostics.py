"""Read-only diagnosis separates bank agreement from economic decoder support."""

from __future__ import annotations

import json
import struct
from typing import Any

from memecoin_ai.domain import ValidationError, digest, integer
from memecoin_ai.raydium import PROGRAM, account_bytes, config_collection_terms, decode_pool
from memecoin_ai.sources import Capture
from memecoin_ai.tokens import CLOCK, SYSVAR_OWNER, decode_supported_checkpoint


def diagnose_checkpoint(
    cap: Capture, pool_id: str, *, config_layout_version: int = 1
) -> dict[str, Any]:
    if type(config_layout_version) is not int or config_layout_version not in (1, 2):
        raise ValidationError("unsupported config layout version")
    request = json.loads(cap.request_json)
    addresses, options = request["params"]
    result = cap.result()
    if (
        cap.method != "getMultipleAccounts"
        or request["method"] != cap.method
        or options.get("commitment") != "finalized"
        or options.get("encoding") != "base64"
        or "dataSlice" in options
        or not isinstance(result, dict)
    ):
        raise ValidationError("diagnosis requires full finalized getMultipleAccounts")
    values = result["value"]
    if len(addresses) != 7 or len(values) != 7 or addresses[-1] != CLOCK:
        raise ValidationError("diagnosis requires seven linked accounts and Clock")
    slot = result["context"]["slot"]
    integer(slot, "context slot")
    if slot < options.get("minContextSlot", 0):
        raise ValidationError("checkpoint context regressed")
    pool = decode_pool(values[0])
    if addresses != [*pool.addresses(pool_id), CLOCK]:
        raise ValidationError("checkpoint linkage mismatch")
    clock = account_bytes(values[6], SYSVAR_OWNER, 40)
    clock_slot, _, epoch, _, timestamp = struct.unpack("<QqQQq", clock)
    config = account_bytes(values[1], PROGRAM, 236)
    words = [
        dict(offset=i, value=struct.unpack_from("<Q", config, i)[0])
        for i in range(116 if config_layout_version == 1 else 124, 236, 8)
        if any(config[i : i + 8])
    ]
    report: dict[str, Any] = dict(
        kind="checkpoint_diagnostic_only",
        source_alias=cap.source_alias,
        response_sha256=digest(cap.response),
        request_started_ns=cap.request_started_ns,
        received_ns=cap.received_ns,
        pool_id=pool_id,
        config_address=pool.config,
        context_slot=slot,
        clock_slot=clock_slot,
        clock_epoch=epoch,
        clock_timestamp_seconds=timestamp,
        bank_clock_agreement=slot == clock_slot and timestamp >= 0,
        reserved_config_words=words,
        config_data_sha256=digest(config),
        economic_decoder_supported=False,
        historical_available_ns=None,
        source_revision_matches_binary=False,
    )
    if config_layout_version == 2:
        report["config_layout_version"] = 2
    if not report["bank_clock_agreement"]:
        report["reason"] = "clock_context_mismatch"
    elif words:
        # Expose uninterpreted raw words, never infer fees from magnitude or offsets.
        report["reason"] = "unsupported_config_reserved_bytes"
    else:
        try:
            decode_supported_checkpoint(cap, pool_id, config_layout_version=config_layout_version)
        except ValidationError:
            report["reason"] = "other_protocol_validation"
        else:
            report.update(reason=None, economic_decoder_supported=True)
            if config_layout_version == 2:
                report["config_collection_terms"] = config_collection_terms(values[1])
    return report
