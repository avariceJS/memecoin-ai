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
from memecoin_ai.sources import Capture, HTTPSourceError, ReadOnlyRPC, SourceError
from memecoin_ai.tokens import CLOCK, SYSVAR_OWNER


def compare_captures(left: Capture, right: Capture) -> dict[str, Any]:
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
        left_result, right_result = (
            {k: left_result[k] for k in fields},
            {k: right_result[k] for k in fields},
        )
    elif method == "getTransaction":
        if not isinstance(left_result, dict) or not isinstance(right_result, dict):
            raise ValidationError("invalid transaction comparison")
        fields = ["slot", "blockTime", "version", "transaction", "meta"]
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
    return dict(
        result,
        status="match" if canonical(left_result) == canonical(right_result) else "mismatch",
        semantic_left_sha256=digest(canonical(left_result).encode()),
        semantic_right_sha256=digest(canonical(right_result).encode()),
    )


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
        rows.append(row)
        (output / "attempts.json").write_text(canonical(rows) + "\n")
        if row.get("error"):
            break  # Preserve the remaining plan; investigate the first failed request.
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
    if "groups" in report:
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
                    Capture.load(output / row["reference"]), right
                ):
                    raise ValidationError("comparison report does not reproduce")
        matched = len(rows) == report["planned"] and all(
            r.get("comparison", {}).get("status") == "match" for r in rows
        )
        if report["all_requested_observations_match"] != matched:
            raise ValidationError("comparison success claim disagrees with evidence")
    return report
