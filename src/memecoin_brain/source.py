"""Budgeted keyless public OHLCV acquisition with immutable receipts and revisions."""

from __future__ import annotations

import json
import math
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from memecoin_ml.data import read, sha, write
from memecoin_operational.contract import no_symlink
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish

Row = dict[str, Any]
NS = 10**9
BAR = 300
HOST = "https://api.geckoterminal.com/api/v2"
EXCLUDED = {
    "7zBzrNNbdQJo1ShWYtZniRJ3WyRF5YmYr4LVd8HKuXMr",
    "4GWh5saKgzoukhhj4Rh3p5FmPU6yPuAexV6FaH5JQYfm",
    "G7iLGa5Y8CERJaW2asC4bMV5SWjijQ1y2SWU5sBdZxr2",
}
BASE_EXCLUDED = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
ADDRESS = r"[1-9A-HJ-NP-Za-km-z]{32,44}"
_SOURCE_PATH = re.compile(
    rf"/networks/solana/(?:pools(?:/{ADDRESS}(?:/ohlcv/minute)?|/{ADDRESS}/info)?|tokens/{ADDRESS}/pools)"
)


class SourceFailure(ValueError):
    """Safe, controlled category only."""


def initialize(root: Path) -> Row:
    no_symlink(root)
    root.mkdir(parents=True, exist_ok=False)
    for name in ("requests", "cycles", "models", "shadow", "training"):
        (root / name).mkdir()
    now = time.time_ns()
    config = dict(
        schema=1,
        track="brain_development",
        created_ns=now,
        expires_ns=now + 72 * 3600 * NS,
        max_requests=12000,
        min_request_interval_seconds=7,
        timeout_seconds=15,
        max_response_bytes=2 * 1024**2,
        storage_limit_bytes=2 * 1024**3,
        free_floor_bytes=4 * 1024**3,
        interval_seconds=300,
        train_interval_seconds=21600,
        max_training_cycles=12,
        consecutive_failure_stop=2,
        paid_budget_usd=0,
        F01=False,
        trading=False,
        historical_online_availability_known=False,
    )
    write(root / "config.json", config)
    return config


def host_guard(root: Path) -> Row:
    no_symlink(root)
    if (root / "deployment.json").exists():
        from .runtime import verify_runtime

        verify_runtime(root)
    c = read(root / "config.json")
    if (root / "STOP").exists() or time.time_ns() >= c["expires_ns"]:
        raise SourceFailure("stop_or_expiry")
    if shutil.disk_usage(root).free < c["free_floor_bytes"]:
        raise SourceFailure("disk_headroom")
    size = 0
    for p in root.rglob("*"):
        if p.is_symlink():
            raise SourceFailure("symlink_artifact")
        if p.is_file():
            size += p.stat().st_size
    if size + c["max_response_bytes"] > c["storage_limit_bytes"]:
        raise SourceFailure("storage_budget")
    return c


def fetch(root: Path, path: str, params: Row, *, lane: str) -> Path:
    """Caller holds track lock. Every attempted GET reserves budget before network."""
    if lane not in ("discovery", "historical", "prospective") or not _SOURCE_PATH.fullmatch(path):
        raise SourceFailure("source_scope")
    c = host_guard(root)
    prior = sorted((root / "requests").iterdir())
    if [p.name for p in prior] != [f"{i:06d}" for i in range(len(prior))]:
        raise SourceFailure("request_sequence")
    debit = (
        read(root / "external-request-debit.json")["requests"]
        if (root / "external-request-debit.json").exists()
        else 0
    )
    if type(debit) is not int or debit < 0 or len(prior) + debit >= c["max_requests"]:
        raise SourceFailure("request_budget")
    for attempt in reversed(prior):
        if (attempt / "failure.json").exists():
            failure = read(attempt / "failure.json")
            if failure.get("http_status") in (401, 403):
                raise SourceFailure("public_access_denied_no_retry")
            if failure.get("http_status") == 429:
                until = (
                    failure["received_ns"] + max(900, failure.get("retry_after_seconds", 0)) * NS
                )
                if time.time_ns() < until:
                    raise SourceFailure("provider_cooldown")
            break
    if prior:
        previous = read(prior[-1] / "request.json")["started_ns"]
        interval = max(12, c["min_request_interval_seconds"])
        delay = previous / NS + interval - time.time()
        if delay > interval + 2:
            raise SourceFailure("clock_regression")
        if delay > 0:
            time.sleep(delay)
    host_guard(root)
    output = root / "requests" / f"{len(prior):06d}"
    output.mkdir()
    url = HOST + path + "?" + urllib.parse.urlencode(params)
    publish(output / "request.json", dict(url=url, started_ns=time.time_ns(), lane=lane))
    started = time.monotonic()
    try:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json;version=20230203",
                "User-Agent": "memecoin-ai-research/0.1",
            },
        )
        with urllib.request.urlopen(request, timeout=c["timeout_seconds"]) as response:
            payload = response.read(c["max_response_bytes"] + 1)
            status = response.status
        received = time.time_ns()
        (output / "response.bin").write_bytes(payload)
        if len(payload) > c["max_response_bytes"]:
            raise SourceFailure("response_budget")
        json.loads(payload)
        publish(
            output / "receipt.json",
            dict(
                status="completed",
                http_status=status,
                received_ns=received,
                available_ns=time.time_ns(),
                latency_seconds=time.monotonic() - started,
                response_sha256=sha(output / "response.bin"),
            ),
        )
    except Exception as exc:
        retry_after = 0
        if isinstance(exc, urllib.error.HTTPError):
            value = exc.headers.get("Retry-After", "0")
            if value.isdigit():
                retry_after = int(value)
            try:
                (output / "error-response.bin").write_bytes(exc.read(c["max_response_bytes"] + 1))
            except Exception:
                pass  # The HTTP status/error receipt survives even an unreadable error body.
        row = dict(
            status="failed",
            category=str(exc) if isinstance(exc, SourceFailure) else "public_http_failure",
            error_type=type(exc).__name__,
            http_status=exc.code if isinstance(exc, urllib.error.HTTPError) else None,
            received_ns=time.time_ns(),
            no_retry=True,
            retry_after_seconds=retry_after,
        )
        publish(output / "failure.json", row)
        seal(output)
        print(json.dumps(row), flush=True)
        raise SourceFailure(row["category"]) from None
    seal(output)
    return output


def select_universe(captures: list[Path], *, now_ns: int, maximum: int = 12) -> Row:
    buckets: list[list[Row]] = [[], [], []]
    seen = set()
    excluded: Counter[str] = Counter()
    from datetime import datetime

    for capture in captures:
        verify(capture)
        available = read(capture / "receipt.json")["available_ns"]
        if available > now_ns:
            raise ValueError("future_universe")
        for row in json.loads((capture / "response.bin").read_bytes())["data"]:
            a, rel = row["attributes"], row["relationships"]
            pool = a["address"]
            base = rel["base_token"]["data"]["id"].removeprefix("solana_")
            if pool in EXCLUDED or base in BASE_EXCLUDED or base in seen:
                excluded["protected_base_or_duplicate"] += 1
                continue
            if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", pool):
                continue
            if a.get("reserve_in_usd") is None or not a.get("pool_created_at"):
                excluded["missing_liquidity_or_creation"] += 1
                continue
            liquidity = float(a["reserve_in_usd"])
            if not math.isfinite(liquidity) or liquidity < 10000:
                excluded["liquidity_below_scope"] += 1
                continue
            created = int(
                datetime.fromisoformat(a["pool_created_at"].replace("Z", "+00:00")).timestamp() * NS
            )
            if created > now_ns:
                continue
            age = (now_ns - created) / NS / 86400
            bucket = 0 if age < 1 else 1 if age < 7 else 2
            buckets[bucket].append(
                dict(
                    pool=pool,
                    base_mint=base,
                    quote_mint=rel["quote_token"]["data"]["id"].removeprefix("solana_"),
                    dex=rel["dex"]["data"]["id"],
                    pool_created_ns=created,
                    discovered_ns=available,
                    liquidity_at_discovery_usd=liquidity,
                    age_bucket=bucket,
                    capture_sha=sha(capture / "manifest.json"),
                )
            )
            seen.add(base)
    chosen = []
    while any(buckets) and len(chosen) < maximum:
        for bucket in buckets:
            if bucket and len(chosen) < maximum:
                chosen.append(bucket.pop(0))
    if not chosen:
        raise SourceFailure("empty_universe")
    return dict(
        revision=0,
        admitted_ns=now_ns,
        pools=chosen,
        selection="current public ranking; round-robin ages <1d,1-7d,>=7d; liquidity>=10000",
        survivorship_bias=True,
        historical_universe_known=False,
        F01_pools_excluded=sorted(EXCLUDED),
        execution_protocol_support_expanded=False,
        exclusions=dict(excluded),
    )


def bars(capture: Path, pool: str) -> list[Row]:
    verify(capture)
    request, receipt = read(capture / "request.json"), read(capture / "receipt.json")
    raw = json.loads((capture / "response.bin").read_bytes())["data"]["attributes"]["ohlcv_list"]
    result, seen = [], set()
    for item in sorted(raw):
        if len(item) != 6 or type(item[0]) is not int or item[0] % BAR:
            raise ValueError("bar_schema_or_alignment")
        t, o, h, low, close, volume = item
        if t in seen:
            raise ValueError("duplicate_bar_in_response")
        seen.add(t)
        if (
            any(not math.isfinite(v) for v in item)
            or not 0 < low <= min(o, close) <= max(o, close) <= h
            or volume < 0
        ):
            raise ValueError("invalid_OHLCV")
        # Open timestamps are event time, not receipt time. Incomplete bars never enter data.
        if (t + BAR) * NS > receipt["received_ns"]:
            continue
        result.append(
            dict(
                pool=pool,
                open_s=t,
                ohlcv=item[1:],
                available_ns=receipt["available_ns"],
                lane=request["lane"],
                capture_sha=sha(capture / "manifest.json"),
            )
        )
    return result


def ohlcv(
    root: Path,
    pool: str,
    *,
    lane: str,
    before: int | None = None,
    limit: int = 1000,
    token: str = "base",
) -> Path:
    if token not in ("base", "quote") and not re.fullmatch(ADDRESS, token):
        raise SourceFailure("source_scope")
    params: Row = dict(
        aggregate=5, limit=limit, currency="usd", token=token, include_empty_intervals="false"
    )
    if before is not None:
        params["before_timestamp"] = before
    return fetch(root, f"/networks/solana/pools/{pool}/ohlcv/minute", params, lane=lane)
