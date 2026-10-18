"""Read-side provider evidence. Agreement and response time never imply trade inclusion."""

from __future__ import annotations

import json
import math
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from memecoin_ai.domain import ValidationError, canonical, digest, integer, strict_json
from memecoin_ai.raydium import account_bytes
from memecoin_ai.sources import (
    Capture,
    HTTPSourceError,
    ReadOnlyRPC,
    SourceError,
    TransportSourceError,
)
from memecoin_ai.tokens import CLOCK, SYSVAR_OWNER


def compare_captures(
    left: Capture, right: Capture, *, comparison_schema: int = 3
) -> dict[str, Any]:
    if type(comparison_schema) is not int or comparison_schema not in (1, 2, 3):
        raise ValidationError("unsupported provider comparison schema")
    a, b = json.loads(left.request_json), json.loads(right.request_json)
    if left.source_alias == right.source_alias:
        raise ValidationError("cross-provider comparison requires distinct source aliases")
    if (a["method"], a["params"]) != (b["method"], b["params"]):
        raise ValidationError("comparison requests differ")
    method = a["method"]
    allowed = {"getBlocks", "getBlock", "getTransaction", "getMultipleAccounts", "getAccountInfo"}
    if method not in allowed or a["params"][-1].get("commitment") != "finalized":
        raise ValidationError("comparison requires supported finalized requests")
    left_result, right_result = left.result(), right.result()
    result: dict[str, Any] = dict(
        method=method,
        left_source=left.source_alias,
        right_source=right.source_alias,
        left_sha256=digest(left.response),
        right_sha256=digest(right.response),
        independent_infrastructure_verified=False,
        market_dataset_qualified=False,
    )
    if comparison_schema >= 2:
        result["comparison_schema"] = comparison_schema
    truncated: dict[str, list[int]] = {}
    if left_result is None or right_result is None:
        return dict(result, status="unavailable", reason="null result is not evidence of agreement")
    if method in ("getMultipleAccounts", "getAccountInfo"):
        if not isinstance(left_result, dict) or not isinstance(right_result, dict):
            raise ValidationError("invalid account response")
        if left_result["context"]["slot"] != right_result["context"]["slot"]:
            return dict(
                result,
                status="incomparable",
                reason="different banks; minContextSlot is only a lower bound",
            )
        left_result, right_result = left_result["value"], right_result["value"]
        if (
            left_result is None
            or right_result is None
            or (isinstance(left_result, list) and (None in left_result or None in right_result))
        ):
            return dict(result, status="unavailable", reason="missing account")
    elif method == "getBlock":
        fields = ["blockhash", "previousBlockhash", "parentSlot", "blockTime"]
        detail = a["params"][1].get("transactionDetails")
        fields += (
            ["signatures"]
            if detail == "signatures"
            else ["transactions"]
            if detail == "full"
            else []
        )
        if (
            len(fields) != 5
            or not isinstance(left_result, dict)
            or not isinstance(right_result, dict)
        ):
            raise ValidationError("unsupported block comparison")
        if comparison_schema == 1:
            left_result, right_result = (
                {k: left_result[k] for k in fields},
                {k: right_result[k] for k in fields},
            )
        else:
            for block in (left_result, right_result):
                if any(k not in block for k in fields):
                    raise ValidationError("required block fields missing")
                if detail == "full" and not _has_transaction_metadata(block["transactions"]):
                    return dict(
                        result, status="unavailable", reason="incomplete transaction metadata"
                    )
            if comparison_schema >= 3 and detail == "full":
                truncated = {
                    side: _truncated_transactions(body["transactions"])
                    for side, body in (("left", left_result), ("right", right_result))
                }
    elif method == "getTransaction":
        if not isinstance(left_result, dict) or not isinstance(right_result, dict):
            raise ValidationError("invalid transaction comparison")
        fields = ["slot", "blockTime", "version", "transaction", "meta"]
        if comparison_schema >= 2 and any(
            k not in body for body in (left_result, right_result) for k in fields
        ):
            raise ValidationError("required transaction fields missing")
        if comparison_schema >= 3:
            if not _has_transaction_metadata([left_result, right_result]):
                return dict(result, status="unavailable", reason="incomplete transaction metadata")
            truncated = {
                side: _truncated_transactions([body])
                for side, body in (("left", left_result), ("right", right_result))
            }
        if comparison_schema == 1:
            left_result, right_result = (
                {k: left_result[k] for k in fields},
                {k: right_result[k] for k in fields},
            )
        if (
            left_result["transaction"]["signatures"][0] != a["params"][0]
            or right_result["transaction"]["signatures"][0] != a["params"][0]
        ):
            raise ValidationError("transaction identity mismatch")
    elif method == "getBlocks":
        start, end = a["params"][:2]
        for slots in (left_result, right_result):
            if not isinstance(slots, list) or not slots:
                return dict(result, status="unavailable", reason="empty block enumeration")
            if any(type(s) is not int or not start <= s <= end for s in slots) or slots != sorted(
                set(slots)
            ):
                raise ValidationError("invalid block enumeration")
    same = canonical(left_result) == canonical(right_result)
    status = "match" if same else "mismatch"
    if comparison_schema >= 3:
        if truncated:
            result["truncated_log_transaction_indices"] = truncated
            result["log_truncation_observed"] = any(truncated.values())
            if same and any(truncated.values()):
                status = "unavailable"
                result["reason"] = "identical truncated logs are not complete trace evidence"
        if not same:
            result["differences"] = json_difference_summary(left_result, right_result)
    return dict(
        result,
        status=status,
        semantic_left_sha256=digest(canonical(left_result).encode()),
        semantic_right_sha256=digest(canonical(right_result).encode()),
    )


def json_difference_summary(left: object, right: object, *, limit: int = 16) -> dict[str, Any]:
    """Count all differing sites; emit bounded JSON-pointer paths, never large values.

    An array length change is one site, plus differences in overlapping elements.
    This is diagnostic only: no response fields are dropped from the comparison.
    """
    integer(limit, "difference sample limit", 1)
    if limit > 128:
        raise ValidationError("difference sample limit exceeds 128")
    samples: list[dict[str, Any]] = []
    count = 0

    def record(path: str, kind: str, **details: object) -> None:
        nonlocal count
        count += 1
        if len(samples) < limit:
            samples.append(dict(path=path, kind=kind, **details))

    def visit(a: object, b: object, path: str) -> None:
        if type(a) is not type(b):
            record(path, "type", left_type=type(a).__name__, right_type=type(b).__name__)
        elif isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(a.keys() | b.keys()):
                child = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in a or key not in b:
                    record(child, "missing_field", left_present=key in a, right_present=key in b)
                else:
                    visit(a[key], b[key], child)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                record(path, "array_length", left_length=len(a), right_length=len(b))
            for i, (x, y) in enumerate(zip(a, b, strict=False)):
                visit(x, y, path + "/" + str(i))
        elif a != b:
            record(path, "value")

    visit(left, right, "")
    return dict(total_sites=count, samples=samples, samples_complete=count <= limit)


def diagnose_block_account_scope(
    left: Capture, right: Capture, addresses: list[str]
) -> dict[str, Any]:
    """Localize differences using an externally frozen account cohort, never qualify it.

    Writable references are potential mutations, including failed transactions. Read-only
    references remain in the report. This does not bypass replay's complete-log gate or
    establish that the supplied cohort covers every economic dependency.
    """
    if (
        not addresses
        or any(not isinstance(a, str) or not a for a in addresses)
        or len(set(addresses)) != len(addresses)
    ):
        raise ValidationError("account scope requires nonempty unique addresses")
    request = json.loads(left.request_json)
    if (
        request["method"] != "getBlock"
        or request["params"][1].get("transactionDetails") != "full"
        or request["params"][1].get("encoding") != "jsonParsed"
    ):
        raise ValidationError("account scope requires full jsonParsed blocks")
    strict = compare_captures(left, right)
    a, b = left.result(), right.result()
    if not isinstance(a, dict) or not isinstance(b, dict):
        raise ValidationError("account scope requires available blocks")
    if not all(_has_transaction_metadata(x.get("transactions")) for x in (a, b)):
        raise ValidationError("account scope requires complete metadata structure")
    if len(a["transactions"]) != len(b["transactions"]):
        raise ValidationError("account scope transaction counts differ")
    groups: dict[str, Any] = {
        name: dict(
            count=0, exact_matches=0, mismatched_indices=[], truncated_left=[], truncated_right=[]
        )
        for name in ("writable_reference", "readonly_reference", "no_reference")
    }
    mismatches = []
    watched = set(addresses)
    for index, (x, y) in enumerate(zip(a["transactions"], b["transactions"], strict=True)):
        tx, other = x["transaction"], y["transaction"]
        keys = tx["message"].get("accountKeys")
        if tx["signatures"] != other["signatures"]:
            raise ValidationError("account scope transaction order differs")
        if (
            not isinstance(keys, list)
            or not keys
            or any(
                not isinstance(k, dict)
                or not isinstance(k.get("pubkey"), str)
                or type(k.get("writable")) is not bool
                for k in keys
            )
        ):
            raise ValidationError("account scope requires resolved writable flags")
        if len({k["pubkey"] for k in keys}) != len(keys):
            raise ValidationError("account scope contains duplicate resolved keys")
        if canonical(keys) != canonical(other["message"].get("accountKeys")):
            raise ValidationError("account scope resolved keys differ")
        touched = [k for k in keys if k["pubkey"] in watched]
        group = (
            "writable_reference"
            if any(k["writable"] for k in touched)
            else "readonly_reference"
            if touched
            else "no_reference"
        )
        row = groups[group]
        row["count"] += 1
        same = canonical(x) == canonical(y)
        row["exact_matches"] += int(same)
        for side, transaction in (("left", x), ("right", y)):
            if "Log truncated" in transaction["meta"]["logMessages"]:
                row["truncated_" + side].append(index)
        if not same:
            row["mismatched_indices"].append(index)
            mismatches.append(dict(index=index, group=group, referenced_accounts=touched))
    return dict(
        schema_version=1,
        kind="block_account_scope_diagnostic",
        addresses=addresses,
        strict_comparison=strict,
        groups=groups,
        mismatches=mismatches,
        scope_completeness_verified=False,
        market_dataset_qualified=False,
        execution_qualified=False,
    )


def _truncated_transactions(transactions: list[Any]) -> list[int]:
    return [i for i, tx in enumerate(transactions) if "Log truncated" in tx["meta"]["logMessages"]]


def _has_transaction_metadata(transactions: object) -> bool:
    if not isinstance(transactions, list):
        return False
    arrays = (
        "preBalances",
        "postBalances",
        "preTokenBalances",
        "postTokenBalances",
        "innerInstructions",
        "logMessages",
    )
    for tx in transactions:
        if not isinstance(tx, dict) or not isinstance(tx.get("meta"), dict):
            return False
        meta = tx["meta"]
        if "err" not in meta or type(meta.get("fee")) is not int:
            return False
        if any(not isinstance(meta.get(k), list) for k in arrays):
            return False
        transaction = tx.get("transaction")
        if (
            "version" not in tx
            or not isinstance(transaction, dict)
            or not isinstance(transaction.get("signatures"), list)
            or not transaction["signatures"]
            or not isinstance(transaction.get("message"), dict)
        ):
            return False
    return True


def _seal(output: Path, report: dict[str, Any]) -> None:
    (output / "report.json").write_text(canonical(report) + "\n")
    modules = ("provider_audit.py", "sources.py", "domain.py", "raydium.py", "tokens.py", "cpmm.py")
    (output / "source.json").write_text(
        canonical({n: Path(__file__).with_name(n).read_text() for n in modules}) + "\n"
    )
    files = {
        str(p.relative_to(output)): digest(p.read_bytes())
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    (output / "manifest.json").write_text(canonical(dict(schema_version=1, files=files)) + "\n")


def _clock(cap: Capture) -> tuple[int, int]:
    request = json.loads(cap.request_json)
    result = cap.result()
    if (
        cap.method != "getAccountInfo"
        or request["params"][0] != CLOCK
        or not isinstance(result, dict)
        or request["params"][1].get("encoding") != "base64"
    ):
        raise ValidationError("invalid Clock observation")
    raw = account_bytes(result["value"], SYSVAR_OWNER, 40)
    slot, _, _, _, timestamp = struct.unpack("<QqQQq", raw)
    if result["context"]["slot"] != slot or timestamp < 0:
        raise ValidationError("Clock/bank mismatch")
    return slot, timestamp


def _distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    if not values:
        return dict(n=0, p50=None, p95=None, maximum=None)
    return dict(
        n=len(values),
        p50=ordered[math.ceil(0.5 * len(values)) - 1],
        p95=ordered[math.ceil(0.95 * len(values)) - 1],
        maximum=ordered[-1],
    )


def latency_report(output: Path) -> dict[str, Any]:
    spec = strict_json((output / "spec.json").read_text())
    attempts = strict_json((output / "attempts.json").read_text())
    groups: dict[str, list[dict[str, Any]]] = {"confirmed": [], "finalized": []}
    if not attempts or len(attempts) > spec["rounds"] * 2 or len(attempts) % 2:
        raise ValidationError("invalid attempt count")
    for index, attempt in enumerate(attempts):
        if (attempt["round"], attempt["commitment"]) != (
            index // 2,
            ("confirmed", "finalized")[index % 2],
        ):
            raise ValidationError("attempt ordering/identity mismatch")
        integer(attempt["elapsed_ns"], "monotonic duration")
        if "capture" not in attempt:
            continue
        cap = Capture.load(output / attempt["capture"])
        if cap.source_alias != spec["source_alias"]:
            raise ValidationError("source alias mismatch")
        commitment = json.loads(cap.request_json)["params"][1]["commitment"]
        if commitment != attempt["commitment"] or commitment not in groups:
            raise ValidationError("commitment mismatch")
        if attempt.get("error"):
            continue
        slot, stamp = _clock(cap)
        groups[commitment].append(
            dict(
                slot=slot,
                received_ns=cap.received_ns,
                response_ms=attempt["elapsed_ns"] / 1e6,
                clock_age_seconds=cap.received_ns / 1e9 - stamp,
            )
        )
    target = groups["confirmed"][0] if groups["confirmed"] else None
    final = next((x for x in groups["finalized"] if target and x["slot"] >= target["slot"]), None)
    visibility = None
    if target and final:
        delta = final["received_ns"] - target["received_ns"]
        visibility = delta / 1e9 if delta > 0 else None
    return dict(
        schema_version=1,
        source_alias=spec["source_alias"],
        attempted=len(attempts),
        errors=sum(bool(a.get("error")) for a in attempts),
        planned=spec["rounds"] * 2,
        groups={
            k: dict(
                response_ms=_distribution([x["response_ms"] for x in v]),
                receipt_minus_chain_clock_seconds=_distribution(
                    [x["clock_age_seconds"] for x in v]
                ),
            )
            for k, v in groups.items()
        },
        first_confirmed_slot=target["slot"] if target else None,
        finalized_seen=final is not None,
        observed_visibility_delay_seconds=visibility,
        semantics=(
            "Monotonic HTTP response time includes TLS/RPC/download. "
            "Clock age uses unsynchronized wall clocks and integer chain timestamps. "
            "Visibility delay is polling-dependent, not actual consensus finality."
        ),
        execution_latency_calibrated=False,
        market_dataset_qualified=False,
    )


def measure_latency(
    client: ReadOnlyRPC, output: Path, *, rounds: int = 12, interval: float = 3
) -> dict[str, Any]:
    integer(rounds, "rounds", 1)
    if rounds > 30 or not 1 <= interval <= 60:
        raise ValidationError("latency sample exceeds bounded limits")
    output.mkdir(parents=True, exist_ok=False)
    (output / "spec.json").write_text(
        canonical(
            dict(
                schema_version=1,
                source_alias=client.source_alias,
                rounds=rounds,
                interval_seconds=interval,
            )
        )
        + "\n"
    )
    attempts = []

    def observe(pair: tuple[int, str]) -> dict[str, Any]:
        index, commitment = pair
        start = time.monotonic_ns()
        attempt: dict[str, Any] = dict(round=index, commitment=commitment)
        try:
            cap = client.fetch(
                "getAccountInfo", [CLOCK, dict(encoding="base64", commitment=commitment)]
            )
            attempt["elapsed_ns"] = time.monotonic_ns() - start
            name = f"captures/{index:03d}-{commitment}"
            cap.save(output / name)
            attempt["capture"] = name
            _clock(cap)
        except (SourceError, ValidationError) as exc:
            attempt.setdefault("elapsed_ns", time.monotonic_ns() - start)
            attempt["error"] = type(exc).__name__
            if isinstance(exc, HTTPSourceError):
                attempt["http_status"] = exc.status
            if isinstance(exc, TransportSourceError):
                attempt["transport_reason"] = exc.reason
        return attempt

    with ThreadPoolExecutor(max_workers=2) as executor:
        for index in range(rounds):
            if index:
                time.sleep(interval)
            rows = list(executor.map(observe, [(index, "confirmed"), (index, "finalized")]))
            attempts.extend(rows)
            (output / "attempts.json").write_text(canonical(attempts) + "\n")
            if any(r.get("http_status") in (401, 403, 429) for r in rows):
                break  # No blind retry of denied or throttled access.
    report = latency_report(output)
    _seal(output, report)
    return report


def crosscheck(
    captures: list[Capture], client: ReadOnlyRPC, output: Path, *, interval: float = 3
) -> dict[str, Any]:
    if not captures or len(captures) > 16 or not 1 <= interval <= 60:
        raise ValidationError("crosscheck exceeds bounded limits")
    if any(c.source_alias == client.source_alias for c in captures):
        raise ValidationError("secondary alias must differ from reference")
    output.mkdir(parents=True, exist_ok=False)
    references = [f"{i:03d}-reference" for i in range(len(captures))]
    for left, name in zip(captures, references, strict=True):
        left.save(output / name)
    (output / "spec.json").write_text(
        canonical(
            dict(
                schema_version=1,
                kind="crosscheck",
                comparison_schema=3,
                timeout_seconds=client.timeout,
                max_response_bytes=client.max_bytes,
                interval_seconds=interval,
                secondary_alias=client.source_alias,
                references=references,
            )
        )
        + "\n"
    )
    rows = []
    for index, left in enumerate(captures):
        if index:
            time.sleep(interval)
        req = json.loads(left.request_json)
        row: dict[str, Any] = dict(reference=f"{index:03d}-reference")
        try:
            right = client.fetch(req["method"], req["params"])
            right.save(output / f"{index:03d}-secondary")
            row["secondary"] = f"{index:03d}-secondary"
            row["comparison"] = compare_captures(left, right)
        except (SourceError, ValidationError) as exc:
            row["error"] = type(exc).__name__
            if isinstance(exc, HTTPSourceError):
                row["http_status"] = exc.status
            if isinstance(exc, TransportSourceError):
                row["transport_reason"] = exc.reason
        rows.append(row)
        (output / "attempts.json").write_text(canonical(rows) + "\n")
        if row.get("error") or row.get("comparison", {}).get("status") != "match":
            break  # Preserve the remaining plan; investigate the first non-match/error.
    report = dict(
        schema_version=1,
        planned=len(captures),
        attempted=len(rows),
        rows=rows,
        all_requested_observations_match=len(rows) == len(captures)
        and all(r.get("comparison", {}).get("status") == "match" for r in rows),
        independent_infrastructure_verified=False,
        market_dataset_qualified=False,
    )
    _seal(output, report)
    return report


def verify_provider_bundle(output: Path) -> dict[str, Any]:
    manifest = strict_json((output / "manifest.json").read_text())
    actual = {
        str(p.relative_to(output))
        for p in output.rglob("*")
        if p.is_file() and p != output / "manifest.json"
    }
    if manifest.get("schema_version") != 1 or set(manifest["files"]) != actual:
        raise ValidationError("provider bundle file registry mismatch")
    for name, sha in manifest["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or digest((output / name).read_bytes()) != sha
        ):
            raise ValidationError("provider bundle checksum/path mismatch")
    report: dict[str, Any] = strict_json((output / "report.json").read_text())
    if report.get("kind") == "chunked_range":
        if report != chunked_range_report(output):
            raise ValidationError("chunked range report does not reproduce")
    elif "groups" in report:
        if report != latency_report(output):
            raise ValidationError("latency report does not reproduce")
    else:
        spec = strict_json((output / "spec.json").read_text())
        rows = strict_json((output / "attempts.json").read_text())
        if (
            not rows
            or len(rows) > len(spec["references"])
            or report["planned"] != len(spec["references"])
            or report["attempted"] != len(rows)
        ):
            raise ValidationError("comparison plan/count mismatch")
        if [r["reference"] for r in rows] != spec["references"][: len(rows)]:
            raise ValidationError("comparison reference ordering mismatch")
        if rows != report["rows"]:
            raise ValidationError("comparison attempt registry mismatch")
        for row in rows:
            if "comparison" in row:
                right = Capture.load(output / row["secondary"])
                if right.source_alias != spec["secondary_alias"]:
                    raise ValidationError("secondary alias disagrees with plan")
                if row["comparison"] != compare_captures(
                    Capture.load(output / row["reference"]),
                    right,
                    comparison_schema=spec.get("comparison_schema", 1),
                ):
                    raise ValidationError("comparison report does not reproduce")
        matched = len(rows) == report["planned"] and all(
            r.get("comparison", {}).get("status") == "match" for r in rows
        )
        if report["all_requested_observations_match"] != matched:
            raise ValidationError("comparison success claim disagrees with evidence")
    return report


def slot_chunks(start: int, end: int, max_slots: int) -> list[tuple[int, int]]:
    integer(start, "start slot")
    integer(end, "end slot", start)
    integer(max_slots, "chunk size", 1)
    if end - start + 1 > 256 or max_slots > 256:
        raise ValidationError("range exceeds bounded slot budget")
    return [(s, min(s + max_slots - 1, end)) for s in range(start, end + 1, max_slots)]


def _enumeration(cap: Capture, start: int, end: int) -> list[int]:
    slots = cap.result()
    if not isinstance(slots, list) or any(
        type(s) is not int or not start <= s <= end for s in slots
    ):
        raise ValidationError("invalid block enumeration")
    if slots != sorted(set(slots)):
        raise ValidationError("block enumeration is not sorted and unique")
    return slots


def _range_request(cap: Capture) -> tuple[int, int, dict[str, Any]]:
    req = strict_json(cap.request_json)
    params = req["params"]
    if (
        cap.method != "getBlocks"
        or req["method"] != "getBlocks"
        or len(params) != 3
        or not isinstance(params[2], dict)
        or params[2].get("commitment") != "finalized"
    ):
        raise ValidationError("range comparison requires explicit finalized getBlocks bounds")
    start, end, options = params
    slot_chunks(start, end, 1)
    return start, end, options


def chunked_range_report(output: Path) -> dict[str, Any]:
    spec = strict_json((output / "spec.json").read_text())
    reference = Capture.load(output / "reference")
    start, end, options = _range_request(reference)
    expected = _enumeration(reference, start, end)
    plan = slot_chunks(start, end, spec["max_slots"])
    if spec["chunks"] != [list(p) for p in plan]:
        raise ValidationError("chunk plan does not partition the original range")
    if reference.source_alias == spec["secondary_alias"]:
        raise ValidationError("secondary alias must differ from reference")
    rows = strict_json((output / "attempts.json").read_text())
    if not 1 <= len(rows) <= len(plan):
        raise ValidationError("invalid chunk attempt count")
    observed = []
    for index, row in enumerate(rows):
        if row["chunk"] != index:
            raise ValidationError("chunk attempt ordering mismatch")
        if row.get("error"):
            if index != len(rows) - 1:
                raise ValidationError("requests continued after chunk failure")
            continue
        cap = Capture.load(output / f"chunk-{index:03d}")
        req = strict_json(cap.request_json)
        lo, hi = plan[index]
        if (
            cap.source_alias != spec["secondary_alias"]
            or cap.method != "getBlocks"
            or req["method"] != "getBlocks"
            or req["params"] != [lo, hi, options]
        ):
            raise ValidationError("chunk request differs from plan")
        observed.extend(_enumeration(cap, lo, hi))
    complete = len(rows) == len(plan) and not any(r.get("error") for r in rows)
    return dict(
        schema_version=1,
        kind="chunked_range",
        start_slot=start,
        end_slot=end,
        planned=len(plan),
        attempted=len(rows),
        rows=rows,
        reference_blocks=len(expected),
        observed_blocks=len(observed),
        coverage_complete=complete,
        enumeration_match=complete and bool(expected) and observed == expected,
        missing_slots=sorted(set(expected) - set(observed)) if complete else None,
        extra_slots=sorted(set(observed) - set(expected)) if complete else None,
        independent_infrastructure_verified=False,
        market_dataset_qualified=False,
    )


def crosscheck_chunked_range(
    reference: Capture,
    client: ReadOnlyRPC,
    output: Path,
    *,
    max_slots: int = 5,
    interval: float = 3,
) -> dict[str, Any]:
    start, end, options = _range_request(reference)
    _enumeration(reference, start, end)
    plan = slot_chunks(start, end, max_slots)
    if not 1 <= interval <= 60 or client.source_alias == reference.source_alias:
        raise ValidationError("invalid interval or secondary alias")
    output.mkdir(parents=True, exist_ok=False)
    reference.save(output / "reference")
    (output / "spec.json").write_text(
        canonical(
            dict(
                schema_version=1,
                kind="chunked_range",
                timeout_seconds=client.timeout,
                max_response_bytes=client.max_bytes,
                interval_seconds=interval,
                max_slots=max_slots,
                chunks=plan,
                secondary_alias=client.source_alias,
            )
        )
        + "\n"
    )
    rows = []
    for index, (lo, hi) in enumerate(plan):
        if index:
            time.sleep(interval)
        row: dict[str, Any] = dict(chunk=index)
        try:
            cap = client.fetch("getBlocks", [lo, hi, options])
            cap.save(output / f"chunk-{index:03d}")
            _enumeration(cap, lo, hi)
        except (SourceError, ValidationError) as exc:
            row["error"] = type(exc).__name__
            if isinstance(exc, HTTPSourceError):
                row["http_status"] = exc.status
            if isinstance(exc, TransportSourceError):
                row["transport_reason"] = exc.reason
        rows.append(row)
        (output / "attempts.json").write_text(canonical(rows) + "\n")
        if row.get("error"):
            break
    report = chunked_range_report(output)
    _seal(output, report)
    return report


def audit_provider_range(enumeration: Path, blocks: Path) -> dict[str, Any]:
    enum = verify_provider_bundle(enumeration)
    comparisons = verify_provider_bundle(blocks)
    if enum.get("kind") != "chunked_range" or not enum.get("enumeration_match"):
        raise ValidationError("range enumeration has not independently matched")
    if not comparisons.get("all_requested_observations_match"):
        raise ValidationError("block comparisons have not all matched")
    ref = Capture.load(enumeration / "reference")
    expected = _enumeration(ref, enum["start_slot"], enum["end_slot"])
    secondary = strict_json((enumeration / "spec.json").read_text())["secondary_alias"]
    slots = []
    count = 0
    previous = None
    for row in comparisons["rows"]:
        left = Capture.load(blocks / row["reference"])
        right = Capture.load(blocks / row["secondary"])
        req = strict_json(left.request_json)
        if (
            left.source_alias != ref.source_alias
            or right.source_alias != secondary
            or left.method != "getBlock"
            or req["params"][1].get("transactionDetails") != "full"
        ):
            raise ValidationError("wrong source pair or incomplete block metadata")
        slot = req["params"][0]
        body = left.result()
        if not isinstance(body, dict) or not isinstance(body.get("transactions"), list):
            raise ValidationError("invalid full block")
        if body["parentSlot"] >= slot:
            raise ValidationError("invalid block parent")
        if previous is not None and (
            body["parentSlot"] != slots[-1] or body["previousBlockhash"] != previous["blockhash"]
        ):
            raise ValidationError("broken block ancestry within range")
        slots.append(slot)
        count += len(body["transactions"])
        previous = body
    if slots != expected:
        raise ValidationError("block comparisons do not cover the enumerated range exactly")
    return dict(
        schema_version=1,
        range=[enum["start_slot"], enum["end_slot"]],
        blocks=len(slots),
        transactions=count,
        bounded_range_agreement=True,
        enumeration_manifest_sha256=digest((enumeration / "manifest.json").read_bytes()),
        blocks_manifest_sha256=digest((blocks / "manifest.json").read_bytes()),
        independent_infrastructure_verified=False,
        market_dataset_qualified=False,
        execution_qualified=False,
    )
