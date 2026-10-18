"""One no-retry Free-endpoint capture for a fixed, one-slot finalized interval."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from memecoin_ai.domain import digest as bytes_digest

from .alchemy_capture import NoRedirect, _read_endpoint_from_local_env
from .block_inventory import BLOCK_OPTIONS
from .storage import atomic_json, canonical, safe_path, sync_dir

START_SLOT = 454_538_107
END_SLOT = 454_538_108
MAX_REQUESTS = 4
MAX_RESPONSE_BYTES = 12_000_000
MAX_BUNDLE_BYTES = 64_000_000
TIMEOUT_SECONDS = 60
ENDPOINT_ENV = "MEMECOIN_ALCHEMY_FREE_HTTP_URL"
SOURCE_ALIAS = "alchemy-free-finalized-block-range-v1"
ACCOUNT_ALIAS = "alchemy-free-local"
ENDPOINT_ORIGIN = "https://solana-mainnet.g.alchemy.com"
_SAFE_HEADERS = {"content-type", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining"}
MAX_PLAN_SLOTS = 256
MAX_PLAN_REQUESTS = MAX_PLAN_SLOTS + 3
MAX_REQUEST_BODY_BYTES = 16_384
MAX_PLAN_EXPIRY_NS = 4_102_444_800_000_000_000


@dataclass(frozen=True)
class CapturePlan:
    provider_alias: str
    endpoint_origin: str
    endpoint_env: str
    account_alias: str
    source_alias: str
    start_slot: int
    end_slot: int
    left_witness_slot: int
    right_witness_slot: int
    request_budget: int
    max_request_bytes: int
    max_response_bytes: int
    max_bundle_bytes: int
    timeout_seconds: int
    expires_at_ns: int

    def checked(self):
        if (
            type(self.start_slot) is not int
            or type(self.end_slot) is not int
            or not isinstance(self.endpoint_origin, str)
        ):
            raise ValueError("block_range_plan_bounds")
        planned_requests = self.end_slot - self.start_slot + 3
        origin = urlsplit(self.endpoint_origin)
        if (
            not all(
                isinstance(value, str) and re.fullmatch(r"[a-z0-9_.-]{1,64}", value)
                for value in (self.provider_alias, self.account_alias, self.source_alias)
            )
            or not isinstance(self.endpoint_env, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,95}", self.endpoint_env)
            or origin.scheme != "https"
            or not origin.hostname
            or origin.path
            or origin.query
            or origin.fragment
            or self.start_slot < 1
            or self.end_slot <= self.start_slot
            or self.end_slot - self.start_slot > MAX_PLAN_SLOTS
            or type(self.left_witness_slot) is not int
            or not self.start_slot - MAX_PLAN_SLOTS <= self.left_witness_slot < self.start_slot
            or type(self.right_witness_slot) is not int
            or not self.end_slot <= self.right_witness_slot < self.end_slot + MAX_PLAN_SLOTS
            or type(self.request_budget) is not int
            or not 1 <= self.request_budget <= min(planned_requests, MAX_PLAN_REQUESTS)
            or type(self.max_request_bytes) is not int
            or not 128 <= self.max_request_bytes <= MAX_REQUEST_BODY_BYTES
            or type(self.max_response_bytes) is not int
            or not 1 <= self.max_response_bytes <= MAX_RESPONSE_BYTES
            or type(self.max_bundle_bytes) is not int
            or not (
                self.request_budget * self.max_response_bytes
                <= self.max_bundle_bytes
                <= MAX_BUNDLE_BYTES
            )
            or type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 120
            or type(self.expires_at_ns) is not int
            or not 0 < self.expires_at_ns <= MAX_PLAN_EXPIRY_NS
        ):
            raise ValueError("block_range_plan_bounds")
        return self


FROZEN_ALCHEMY_FREE_PLAN = CapturePlan(
    provider_alias="alchemy",
    endpoint_origin=ENDPOINT_ORIGIN,
    endpoint_env=ENDPOINT_ENV,
    account_alias=ACCOUNT_ALIAS,
    source_alias=SOURCE_ALIAS,
    start_slot=START_SLOT,
    end_slot=END_SLOT,
    left_witness_slot=START_SLOT - 1,
    right_witness_slot=END_SLOT,
    request_budget=MAX_REQUESTS,
    max_request_bytes=MAX_REQUEST_BODY_BYTES,
    max_response_bytes=MAX_RESPONSE_BYTES,
    max_bundle_bytes=MAX_BUNDLE_BYTES,
    timeout_seconds=TIMEOUT_SECONDS,
    expires_at_ns=MAX_PLAN_EXPIRY_NS,
).checked()


def _durable_write(path: Path, body: bytes):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)


def _store_capture(
    root: Path,
    relative: str,
    method: str,
    params,
    body: bytes,
    started: int,
    received: int,
    provenance: dict,
    source_alias: str,
):
    path = safe_path(root / relative)
    path.mkdir(parents=True, exist_ok=False)
    os.chmod(path, 0o700)
    request_json = canonical(dict(jsonrpc="2.0", id=1, method=method, params=params)).decode()
    receipt = dict(
        schema_version=1,
        source_alias=source_alias,
        method=method,
        request_json=request_json,
        request_started_ns=started,
        received_ns=received,
        response_sha256=bytes_digest(body),
        semantics="HTTP receipt now; not historical market availability",
        http_provenance=provenance,
    )
    _durable_write(path / "response.json", body)
    atomic_json(path / "receipt.json", receipt)
    sync_dir(path)


def _request_rows(plan: CapturePlan):
    rows = [
        (
            "enumeration",
            None,
            "getBlocks",
            [plan.start_slot, plan.end_slot - 1, {"commitment": "finalized"}],
            "enumeration",
        ),
        (
            "boundary",
            plan.left_witness_slot,
            "getBlock",
            [plan.left_witness_slot, BLOCK_OPTIONS],
            "boundary-left",
        ),
    ]
    rows.extend(
        (
            "slot",
            slot,
            "getBlock",
            [slot, BLOCK_OPTIONS],
            "slot" if plan.end_slot - plan.start_slot == 1 else f"slots/{slot}",
        )
        for slot in range(plan.start_slot, plan.end_slot)
    )
    rows.append(
        (
            "boundary",
            plan.right_witness_slot,
            "getBlock",
            [plan.right_witness_slot, BLOCK_OPTIONS],
            "boundary-right",
        )
    )
    return rows


def _attempt_ledger(
    root: Path,
    plan: CapturePlan,
    rows: list[dict],
    planned_requests: list[dict],
):
    value = dict(
        version="solana-bounded-request-attempt-ledger-v1",
        plan=_plan_value(plan),
        plan_pin=_plan_pin(plan),
        interval=[plan.start_slot, plan.end_slot],
        request_budget=plan.request_budget,
        planned_requests=planned_requests,
        attempts=rows,
    )
    atomic_json(root / "attempt-ledger.json", value, replace=True)
    sync_dir(root)
    return value


def _plan_value(plan: CapturePlan):
    return {key: getattr(plan, key) for key in CapturePlan.__dataclass_fields__}


def _plan_pin(plan: CapturePlan):
    return hashlib.sha256(canonical(_plan_value(plan))).hexdigest()


def _endpoint_matches(endpoint: str, plan: CapturePlan):
    parsed = urlsplit(endpoint)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return (
        parsed.scheme == "https"
        and origin == plan.endpoint_origin
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.path.startswith("/")
        and len(parsed.path) <= 512
        and not parsed.query
        and not parsed.fragment
    )


def capture_once(
    output: Path | str,
    endpoint: str | None = None,
    *,
    plan: CapturePlan = FROZEN_ALCHEMY_FREE_PLAN,
) -> dict:
    """Execute one validated fixed plan; never retries, searches witnesses or expands range."""
    plan.checked()
    output = Path(output)
    if output.is_absolute() or not output.parts or output.parts[0] != "work":
        raise ValueError("block_range_output_must_be_ignored_work_path")
    if endpoint is None:
        endpoint = _read_endpoint_from_local_env(plan.endpoint_env)
    if not _endpoint_matches(endpoint, plan):
        raise ValueError("block_range_endpoint_origin_mismatch")
    root = safe_path(output)
    root.mkdir(parents=True, exist_ok=False)
    os.chmod(root, 0o700)
    opener = urllib.request.build_opener(NoRedirect)
    captures = []
    outcomes = []
    attempt_rows = []
    previous_received = None
    total_bytes = 0
    request_rows = _request_rows(plan)
    planned_requests = []
    for purpose, slot, method, params, relative in request_rows:
        request_body = canonical(dict(jsonrpc="2.0", id=1, method=method, params=params))
        if len(request_body) > plan.max_request_bytes:
            raise ValueError("block_range_request_byte_bound")
        planned_requests.append(
            dict(
                purpose=purpose,
                slot=slot,
                method=method,
                path=relative,
                request_json=request_body.decode(),
                request_sha256=hashlib.sha256(request_body).hexdigest(),
                status="planned",
            )
        )
    _attempt_ledger(root, plan, attempt_rows, planned_requests)
    stop_reason = None
    attempted_slots = set()
    for index, (purpose, slot, method, params, relative) in enumerate(request_rows):
        if len(attempt_rows) >= plan.request_budget:
            stop_reason = "request_budget_stop"
            break
        if time.time_ns() >= plan.expires_at_ns:
            stop_reason = "plan_expired"
            break
        planned = planned_requests[index]
        request_body = planned["request_json"].encode()
        request_sha256 = planned["request_sha256"]
        request = urllib.request.Request(
            endpoint,
            data=request_body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        started = time.time_ns()
        planned.update(status="reserved", request_started_ns=started, attempt_number=1)
        attempt_row = dict(
            attempt_number=1,
            purpose=purpose,
            slot=slot,
            method=method,
            request_json=request_body.decode(),
            request_sha256=request_sha256,
            request_started_ns=started,
            status="reserved",
            retry_count=0,
        )
        attempt_rows.append(attempt_row)
        _attempt_ledger(root, plan, attempt_rows, planned_requests)
        status = None
        headers = {}
        transport_error = None
        try:
            response = opener.open(request, timeout=plan.timeout_seconds)
            status = response.status
            headers = {
                key.lower(): value[:128]
                for key, value in response.headers.items()
                if key.lower() in _SAFE_HEADERS
            }
            body = response.read(plan.max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = exc.code
            headers = {
                key.lower(): value[:128]
                for key, value in exc.headers.items()
                if key.lower() in _SAFE_HEADERS
            }
            body = exc.read(plan.max_response_bytes + 1)
            exc.close()
        except Exception as exc:
            # Exception messages may contain the credential-bearing endpoint; retain only type.
            transport_error = type(exc).__name__[:64]
            body = b""
        received = time.time_ns()
        if previous_received is not None and started < previous_received:
            raise ValueError("block_range_clock_regression")
        previous_received = received
        truncated = len(body) > plan.max_response_bytes
        if truncated:
            body = body[: plan.max_response_bytes]
        total_bytes += len(body)
        if total_bytes > plan.max_bundle_bytes:
            raise ValueError("block_range_bundle_byte_limit")
        provenance = dict(
            version="solana-http-provenance-v1",
            endpoint_origin=plan.endpoint_origin,
            account_alias=plan.account_alias,
            http_status=status,
            response_headers=headers,
            retry_count=0,
            attempt_number=1,
            transport_error_class=transport_error,
            response_truncated=truncated,
        )
        _store_capture(
            root,
            relative,
            method,
            params,
            body,
            started,
            received,
            provenance,
            plan.source_alias,
        )
        if purpose == "slot":
            attempted_slots.add(slot)
        attempt_row.update(
            status="transport_error" if transport_error is not None else "response_received",
            received_ns=received,
            http_status=status,
            transport_error_class=transport_error,
            response_bytes=len(body),
            response_sha256=hashlib.sha256(body).hexdigest(),
            response_truncated=truncated,
        )
        planned.update(
            status="transport_error" if transport_error is not None else "response_received",
            received_ns=received,
            http_status=status,
            transport_error_class=transport_error,
            response_bytes=len(body),
            response_sha256=hashlib.sha256(body).hexdigest(),
            response_truncated=truncated,
        )
        _attempt_ledger(root, plan, attempt_rows, planned_requests)
        row = dict(purpose=purpose, path=relative)
        if purpose == "slot":
            row["slot"] = slot
        elif purpose == "boundary":
            row.update(slot=slot, side="left" if relative == "boundary-left" else "right")
        captures.append(row)
        outcomes.append(
            dict(
                purpose=purpose,
                slot=slot,
                method=method,
                http_status=status,
                transport_error_class=transport_error,
                response_truncated=truncated,
                response_bytes=len(body),
                response_sha256=hashlib.sha256(body).hexdigest(),
                request_started_ns=started,
                received_ns=received,
                retries=0,
            )
        )
    stopped_slots = []
    if stop_reason is not None:
        stop_status = (
            "stopped_budget" if stop_reason == "request_budget_stop" else "stopped_expired"
        )
        for row in planned_requests:
            if row["status"] == "planned":
                row["status"] = stop_status
        stopped_slots = [
            slot
            for purpose, slot, _, _, _ in request_rows
            if purpose == "slot" and slot not in attempted_slots
        ]
        _attempt_ledger(root, plan, attempt_rows, planned_requests)
    if captures:
        manifest = dict(
            version="solana-block-range-fixture-v1",
            start_slot=plan.start_slot,
            end_slot=plan.end_slot,
            interval_convention="half-open-start-inclusive-end-exclusive-v1",
            captures=captures,
            stopped_slots=stopped_slots,
        )
        _durable_write(root / "fixture.json", canonical(manifest))
    receipt = dict(
        version="solana-bounded-block-range-capture-v1",
        provider_alias=plan.provider_alias,
        account_alias=plan.account_alias,
        endpoint_origin=plan.endpoint_origin,
        endpoint_env=plan.endpoint_env,
        provider_plan_independently_verified=False,
        free_endpoint_designation=plan.endpoint_env if plan.provider_alias == "alchemy" else None,
        free_plan_independently_verified=False if plan.provider_alias == "alchemy" else None,
        plan=_plan_value(plan),
        plan_pin=_plan_pin(plan),
        interval=[plan.start_slot, plan.end_slot],
        request_budget=plan.request_budget,
        planned_requests=len(planned_requests),
        attempts=len(outcomes),
        retries=0,
        attempt_ledger_sha256=hashlib.sha256(
            (root / "attempt-ledger.json").read_bytes()
        ).hexdigest(),
        timeout_seconds=plan.timeout_seconds,
        max_request_bytes=plan.max_request_bytes,
        max_response_bytes=plan.max_response_bytes,
        max_bundle_bytes=plan.max_bundle_bytes,
        outcomes=outcomes,
        stopped_requests=[
            {key: row[key] for key in ("purpose", "slot", "method", "path", "status")}
            for row in planned_requests
            if row["status"].startswith("stopped_")
        ],
        stopped_slots=stopped_slots,
        stop_reason=stop_reason,
        fixture_written=bool(captures),
        raw_payloads_in_work=True,
    )
    atomic_json(root / "run-receipt.json", receipt)
    sync_dir(root)
    return receipt


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="private ignored work/ destination")
    args = parser.parse_args()
    result = capture_once(args.output)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
