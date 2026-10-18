from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from memecoin_ai.domain import ValidationError, canonical, digest, integer
from memecoin_ai.raydium import (
    PROGRAM,
    SOURCE_REVISION,
    TOKEN,
    checkpoint_inventory,
    decode_checkpoint,
    decode_pool,
    public_key,
    swap_candidates,
)
from memecoin_ai.sources import Capture, ReadOnlyRPC, SourceError


def reconcile_vault_transfers(transaction: Any, vaults: list[str]) -> dict[str, Any]:
    """Compare two RPC representations, not two independent data sources or swap math."""
    if transaction["meta"].get("err") is not None:
        raise ValidationError("cannot reconcile failed transaction as executed transfers")
    if len(vaults) != 2 or len(set(vaults)) != 2:
        raise ValidationError("requires two distinct vaults")
    keys = transaction["transaction"]["message"]["accountKeys"]
    if any(not isinstance(k, dict) for k in keys):
        raise ValidationError("requires jsonParsed account keys including loaded addresses")
    addresses = [k["pubkey"] for k in keys]
    if len(set(addresses)) != len(addresses):
        raise ValidationError("duplicate transaction account keys")
    meta = transaction["meta"]
    instructions = list(transaction["transaction"]["message"]["instructions"])
    for inner in meta.get("innerInstructions") or []:
        instructions.extend(inner["instructions"])
    changes = {}
    for vault in vaults:
        if vault not in addresses:
            raise ValidationError("vault not referenced by transaction")
        index = addresses.index(vault)
        sides = [
            [b for b in meta[name] if b["accountIndex"] == index]
            for name in ("preTokenBalances", "postTokenBalances")
        ]
        if any(len(side) != 1 for side in sides):
            raise ValidationError("missing/ambiguous vault balance metadata")
        before, after = sides[0][0], sides[1][0]
        if (
            before.get("programId") != TOKEN
            or after.get("programId") != TOKEN
            or before["mint"] != after["mint"]
        ):
            raise ValidationError("unsupported vault token metadata")
        before_amount = int(before["uiTokenAmount"]["amount"])
        after_amount = int(after["uiTokenAmount"]["amount"])
        integer(before_amount, "pre balance")
        integer(after_amount, "post balance")
        observed = after_amount - before_amount
        transfers = 0
        count = 0
        for instruction in instructions:
            if instruction.get("programId") != TOKEN:
                continue
            parsed = instruction.get("parsed")
            if parsed is None:
                # An opaque token instruction could change accounting; do not assume it is harmless.
                raise ValidationError("unparsed token instruction")
            info = parsed["info"]
            kind = parsed["type"]
            if kind not in ("transfer", "transferChecked"):
                if vault in info.values():
                    raise ValidationError("non-transfer operation touches vault")
                continue
            if vault not in (info["source"], info["destination"]):
                continue
            amount = int(info["amount"] if kind == "transfer" else info["tokenAmount"]["amount"])
            integer(amount, "transfer amount")
            transfers += amount * (int(info["destination"] == vault) - int(info["source"] == vault))
            count += 1
        if not count:
            raise ValidationError("no parsed transfer evidence for vault")
        changes[vault] = dict(
            balance_delta=observed, transfer_delta=transfers, matches=observed == transfers
        )
    return dict(
        slot=transaction["slot"],
        vaults=changes,
        all_match=all(v["matches"] for v in changes.values()),
        scope="transaction vault flows only; no historical fee state or swap execution calibration",
    )


def decoder_fingerprint() -> dict[str, str]:
    root = Path(__file__).parent
    return {
        name: digest((root / name).read_bytes())
        for name in ("qualification.py", "raydium.py", "sources.py", "domain.py")
    }


def qualify_cpmm(
    client: ReadOnlyRPC,
    output: Path,
    *,
    limit: int = 12,
    discovery_address: str = PROGRAM,
    request_interval_s: float = 1.0,
) -> dict[str, Any]:
    integer(limit, "limit", 1)
    if limit > 30:
        raise ValidationError("qualification is bounded to 30 signatures")
    public_key(discovery_address)
    if not 0 <= request_interval_s <= 10:
        raise ValidationError("request interval must be in [0,10]")
    output.mkdir(parents=True, exist_ok=False)
    spec = dict(
        schema_version=1,
        implementation=decoder_fingerprint(),
        source_alias=client.source_alias,
        program=PROGRAM,
        source_revision=SOURCE_REVISION,
        max_supported_transaction_version=1,
        signature_limit=limit,
        pool_budget=10,
        discovery_address=discovery_address,
        request_interval_s=request_interval_s,
        selection="latest finalized address signatures at request time, no outcome ranking",
        purpose="adapter qualification; activity-selected sample is not a training universe",
    )
    (output / "spec.json").write_text(canonical(spec) + "\n")
    failures: list[dict[str, str]] = []
    captures: dict[str, dict[str, str]] = {}

    def fetch(name: str, method: str, params: list[object]) -> Capture:
        if captures:
            time.sleep(request_interval_s)
        capture = client.fetch(method, params)
        capture.save(output / name)
        captures[name] = dict(
            response=digest(capture.response),
            receipt=digest((output / name / "receipt.json").read_bytes()),
        )
        return capture

    transactions: dict[str, Any] = {}
    pool_transactions: dict[str, list[str]] = {}
    try:
        signatures = fetch(
            "discovery",
            "getSignaturesForAddress",
            [discovery_address, {"commitment": "finalized", "limit": limit}],
        ).result()
        if not isinstance(signatures, list) or len(signatures) > limit:
            raise ValidationError("invalid signature list")
        seen_signatures: set[str] = set()
        for i, item in enumerate(signatures):
            name = f"transaction-{i:03d}"
            try:
                if (
                    item.get("confirmationStatus") != "finalized"
                    or item["signature"] in seen_signatures
                ):
                    raise ValidationError("non-finalized or duplicate discovery signature")
                seen_signatures.add(item["signature"])
                tx = fetch(
                    name,
                    "getTransaction",
                    [
                        item["signature"],
                        {
                            "commitment": "finalized",
                            "encoding": "jsonParsed",
                            "maxSupportedTransactionVersion": 1,
                        },
                    ],
                ).result()
                if (
                    not isinstance(tx, dict)
                    or tx["slot"] != item["slot"]
                    or tx["transaction"]["signatures"][0] != item["signature"]
                ):
                    raise ValidationError("missing transaction or discovery identity mismatch")
                transactions[name] = tx
                for pool_id in swap_candidates(tx):
                    pool_transactions.setdefault(pool_id, []).append(name)
            except (SourceError, ValidationError, KeyError, TypeError, ValueError) as exc:
                failures.append(dict(stage=name, reason=type(exc).__name__))
    except (SourceError, ValidationError, KeyError, TypeError, ValueError) as exc:
        failures.append(dict(stage="discovery", reason=type(exc).__name__))

    inventories = []
    checkpoints = []
    reconciliations = []
    for i, (pool_id, names) in enumerate(pool_transactions.items()):
        stage = f"pool-{i:03d}"
        if i >= 10:
            failures.append(
                dict(stage=stage, pool_id=pool_id, reason="pool request budget exceeded")
            )
            continue
        try:
            first = fetch(
                stage + "-lookup",
                "getAccountInfo",
                [pool_id, {"commitment": "finalized", "encoding": "base64"}],
            ).result()
            if not isinstance(first, dict):
                raise ValidationError("missing pool lookup")
            pool = decode_pool(first["value"])
            capture = fetch(
                stage + "-checkpoint",
                "getMultipleAccounts",
                [
                    pool.addresses(pool_id),
                    {
                        "commitment": "finalized",
                        "encoding": "base64",
                        "minContextSlot": first["context"]["slot"],
                    },
                ],
            )
            inventory = checkpoint_inventory(capture, pool_id)
            inventory["capture"] = stage + "-checkpoint"
            inventories.append(inventory)
            checkpoint = decode_checkpoint(capture, pool_id)
            checkpoint["capture"] = stage + "-checkpoint"
            checkpoints.append(checkpoint)
            for name in names:
                try:
                    result = reconcile_vault_transfers(transactions[name], list(pool.vaults))
                    reconciliations.append(dict(transaction=name, pool_id=pool_id, **result))
                    if not result["all_match"]:
                        failures.append(
                            dict(stage=f"{stage}/{name}/reconcile", reason="vault flow mismatch")
                        )
                except (ValidationError, KeyError, TypeError, ValueError) as exc:
                    failures.append(
                        dict(stage=f"{stage}/{name}/reconcile", reason=type(exc).__name__)
                    )
        except (SourceError, ValidationError, KeyError, TypeError, ValueError) as exc:
            # Decoder messages are controlled locally; transport/provider text may contain secrets.
            reason = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
            failures.append(dict(stage=stage, pool_id=pool_id, reason=reason))
    report = dict(
        spec=spec,
        captures=captures,
        transactions_received=len(transactions),
        pools_discovered=len(pool_transactions),
        checkpoints=checkpoints,
        inventories=inventories,
        reconciliations=reconciliations,
        failures=failures,
        market_dataset_qualified=False,
        historical_arrival_times_available=False,
        execution_calibrated=False,
        birth_death_coverage_verified=False,
        independent_provider_verified=False,
    )
    (output / "report.json").write_text(canonical(report) + "\n")
    return report


def verify_qualification(path: Path) -> dict[str, int | bool]:
    report = json.loads((path / "report.json").read_text())
    if report["spec"] != json.loads((path / "spec.json").read_text()):
        raise ValidationError("qualification spec mismatch")
    for name in report["captures"]:
        if not re.fullmatch(r"discovery|transaction-\d{3}|pool-\d{3}-(lookup|checkpoint)", name):
            raise ValidationError("invalid capture path")
    for flag in (
        "market_dataset_qualified",
        "historical_arrival_times_available",
        "execution_calibrated",
        "birth_death_coverage_verified",
        "independent_provider_verified",
    ):
        if report[flag] is not False:
            raise ValidationError("unsupported qualification claim")
    captures = {name: Capture.load(path / name) for name in report["captures"]}
    for name, capture in captures.items():
        if (
            digest(capture.response) != report["captures"][name]["response"]
            or digest((path / name / "receipt.json").read_bytes())
            != report["captures"][name]["receipt"]
        ):
            raise ValidationError("qualification raw hash mismatch")
    for inventory in report.get("inventories", []):
        decoded = checkpoint_inventory(captures[inventory["capture"]], inventory["pool_id"])
        decoded["capture"] = inventory["capture"]
        if canonical(decoded) != canonical(inventory):
            raise ValidationError("inventory reproduction mismatch")
    for checkpoint in report["checkpoints"]:
        decoded = decode_checkpoint(captures[checkpoint["capture"]], checkpoint["pool_id"])
        decoded["capture"] = checkpoint["capture"]
        if canonical(decoded) != canonical(checkpoint):
            raise ValidationError("checkpoint reproduction mismatch")
    checkpoints_by_pool = {c["pool_id"]: c for c in report["checkpoints"]}
    for reconciliation in report["reconciliations"]:
        checkpoint = checkpoints_by_pool.get(reconciliation["pool_id"])
        if checkpoint is None or set(reconciliation["vaults"]) != set(checkpoint["pool"]["vaults"]):
            raise ValidationError("reconciliation/checkpoint linkage mismatch")
        if reconciliation["pool_id"] not in swap_candidates(
            captures[reconciliation["transaction"]].result()
        ):
            raise ValidationError("reconciliation transaction does not invoke pool")
        decoded = reconcile_vault_transfers(
            captures[reconciliation["transaction"]].result(), list(reconciliation["vaults"])
        )
        decoded.update(transaction=reconciliation["transaction"], pool_id=reconciliation["pool_id"])
        if canonical(decoded) != canonical(reconciliation):
            raise ValidationError("reconciliation reproduction mismatch")
    return dict(
        captures_verified=len(captures),
        decoder_fingerprint_recorded="implementation" in report["spec"],
        decoder_matches_recorded=report["spec"].get("implementation") == decoder_fingerprint(),
        checkpoints_reproduced=len(report["checkpoints"]),
        inventories_reproduced=len(report.get("inventories", [])),
        reconciliations_reproduced=len(report["reconciliations"]),
        market_dataset_qualified=False,
    )
