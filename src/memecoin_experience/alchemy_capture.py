"""Bounded raw HTTP capture for finalized Solana address-signature pages."""

import argparse
import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from memecoin_experience.storage import canonical

MAX_RESPONSE_BYTES = 4_000_000
MAX_PAGES = 10
HEADERS = {"content-type", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _atomic_bytes(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".capture-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
    finally:
        Path(name).unlink(missing_ok=True)


def _endpoint_ok(endpoint):
    parsed = urlparse(endpoint)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "solana-mainnet.g.alchemy.com"
        and parsed.path.startswith("/v2/")
        and len(parsed.path.split("/")) == 3
        and parsed.username is None
        and parsed.password is None
        and parsed.query == ""
        and parsed.fragment == ""
    )


def _read_endpoint_from_local_env(name="MEMECOIN_ALCHEMY_FREE_HTTP_URL"):
    if os.environ.get(name):
        return os.environ[name]
    path = Path(".env.local")
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            return value
    raise ValueError("capture_endpoint_unavailable")


def capture_signatures(endpoint, output, address, *, limit=10, max_pages=2):
    """Make sequential no-retry RPC calls; save wire bytes before parsing any response."""
    if not _endpoint_ok(endpoint):
        raise ValueError("capture_endpoint_network")
    if (
        not isinstance(address, str)
        or not address
        or type(limit) is not int
        or not 1 <= limit <= 1000
        or type(max_pages) is not int
        or not 1 <= max_pages <= MAX_PAGES
    ):
        raise ValueError("capture_bounds")
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    pages = []
    requests = []
    seen = set()
    before = None
    terminal_reason = "page_budget"
    error = None
    attempts = 0
    opener = urllib.request.build_opener(NoRedirect)
    for index in range(max_pages):
        request_id = f"brain-senses-{index:04d}"
        params = {"limit": limit, "commitment": "finalized"}
        if before is not None:
            params["before"] = before
        payload = canonical(
            dict(
                jsonrpc="2.0",
                id=request_id,
                method="getSignaturesForAddress",
                params=[address, params],
            )
        )
        request_sha256 = hashlib.sha256(payload).hexdigest()
        address_sha256 = hashlib.sha256(address.encode()).hexdigest()
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        started_ns = time.time_ns()
        attempts += 1
        status = None
        headers = {}
        try:
            response = opener.open(request, timeout=20)
            status = response.status
            headers = {k.lower(): v for k, v in response.headers.items() if k.lower() in HEADERS}
            body = response.read(MAX_RESPONSE_BYTES + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = exc.code
            headers = {k.lower(): v for k, v in exc.headers.items() if k.lower() in HEADERS}
            body = exc.read(MAX_RESPONSE_BYTES + 1)
            exc.close()
        except Exception as exc:  # The error type is safe; its message can include the secret URL.
            terminal_reason = "transport_error"
            error = type(exc).__name__
            requests.append(
                dict(
                    request_id=request_id,
                    method="getSignaturesForAddress",
                    commitment="finalized",
                    limit=limit,
                    address_sha256=address_sha256,
                    request_sha256=request_sha256,
                    before_signature_sha256=(
                        hashlib.sha256(before.encode()).hexdigest() if before is not None else None
                    ),
                    request_started_ns=started_ns,
                    received_ns=time.time_ns(),
                    error_class=error,
                    result_status="transport_error",
                )
            )
            break
        received_ns = time.time_ns()
        wire_complete = len(body) <= MAX_RESPONSE_BYTES
        if not wire_complete:
            body = body[:MAX_RESPONSE_BYTES]
            terminal_reason = "response_too_large"
            error = "response_size_limit"
        wire_path = f"wire/response-{index:04d}.bin"
        _atomic_bytes(root / wire_path, body)
        wire_sha = hashlib.sha256(body).hexdigest()
        request_record = dict(
            request_id=request_id,
            method="getSignaturesForAddress",
            commitment="finalized",
            limit=limit,
            address_sha256=address_sha256,
            request_sha256=request_sha256,
            before_signature_sha256=(
                hashlib.sha256(before.encode()).hexdigest() if before is not None else None
            ),
            request_started_ns=started_ns,
            received_ns=received_ns,
            http_status=status,
            response_headers=headers,
            wire_path=wire_path,
            wire_sha256=wire_sha,
            wire_bytes=len(body),
            wire_complete=wire_complete,
        )
        requests.append(request_record)
        if status != 200 or terminal_reason == "response_too_large":
            request_record["result_status"] = "http_error"
            terminal_reason = "http_error" if status != 200 else terminal_reason
            error = f"http_{status}" if status != 200 else error
            break
        try:
            envelope = json.loads(body)
            if (
                not isinstance(envelope, dict)
                or envelope.get("jsonrpc") != "2.0"
                or envelope.get("id") != request_id
                or "error" in envelope
                or not isinstance(envelope.get("result"), list)
            ):
                raise ValueError("rpc_envelope")
            rows = envelope["result"]
            if len(rows) > limit:
                raise ValueError("rpc_result_limit")
            if not rows:
                request_record["result_status"] = "empty"
                terminal_reason = "empty_page"
                break
            signatures = [row.get("signature") if isinstance(row, dict) else None for row in rows]
            if any(not isinstance(sig, str) or not sig for sig in signatures):
                raise ValueError("rpc_signature_shape")
            if len(set(signatures)) != len(signatures) or any(sig in seen for sig in signatures):
                raise ValueError("rpc_cursor_overlap")
            slots = [row.get("slot") for row in rows]
            monotonic = all(type(slot) is int for slot in slots) and all(
                a >= b for a, b in zip(slots, slots[1:], strict=False)
            )
            finalized = all(row.get("confirmationStatus") == "finalized" for row in rows)
            if not monotonic or not finalized:
                raise ValueError("rpc_result_qualification")
            page_bytes = canonical(rows)
            page_path = f"pages/page-{index:04d}.json"
            _atomic_bytes(root / page_path, page_bytes)
            pages.append(
                dict(
                    request_id=request_id,
                    response_id=envelope["id"],
                    http_status=status,
                    response_headers=headers,
                    request_started_ns=started_ns,
                    received_ns=received_ns,
                    request_limit=limit,
                    used_before_cursor=before is not None,
                    before_signature_sha256=(
                        hashlib.sha256(before.encode()).hexdigest() if before is not None else None
                    ),
                    returned=len(rows),
                    overlap=0,
                    oldest_slot=min(slots),
                    newest_slot=max(slots),
                    finalized=finalized,
                    monotonic=monotonic,
                    retried=False,
                    local_seal="created",
                    path=page_path,
                    response_sha256=hashlib.sha256(page_bytes).hexdigest(),
                    response_bytes=len(page_bytes),
                    wire_path=wire_path,
                    wire_sha256=wire_sha,
                    wire_bytes=len(body),
                    wire_complete=wire_complete,
                )
            )
            request_record["result_status"] = "page"
            seen.update(signatures)
            before = signatures[-1]
            if len(rows) < limit:
                terminal_reason = "short_page"
                break
        except Exception as exc:
            request_record["result_status"] = "decode_error"
            terminal_reason = "rpc_decode_or_cursor_error"
            error = type(exc).__name__
            break
    receipt = dict(
        version="solana-address-signature-capture-v1",
        source_account="free_solana",
        endpoint_not_recorded=True,
        network="solana-mainnet",
        method="getSignaturesForAddress",
        address=address,
        commitment="finalized",
        page_limit=limit,
        attempts=attempts,
        retries=0,
        requests=requests,
        pages=pages,
        unique_signatures=len(seen),
        aborted=error is not None,
        error_class=error,
        terminal_reason=terminal_reason,
        support_status="unknown",
        coverage_complete=False,
        absence_inference_permitted=False,
        raw_surface=(
            "HTTP response body sealed before JSON decoding; oversized bodies are prefix-truncated"
        ),
    )
    _atomic_bytes(root / "receipt.json", canonical(receipt) + b"\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="private ignored work/ destination")
    parser.add_argument("--address", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-pages", type=int, default=2)
    args = parser.parse_args()
    output = Path(args.output)
    if output.is_absolute() or not output.parts or output.parts[0] != "work":
        raise SystemExit("output must be a relative work/ path")
    endpoint = _read_endpoint_from_local_env()
    receipt = capture_signatures(
        endpoint, output, args.address, limit=args.limit, max_pages=args.max_pages
    )
    print(
        json.dumps(
            dict(
                attempts=receipt["attempts"],
                pages=len(receipt["pages"]),
                signatures=receipt["unique_signatures"],
                terminal_reason=receipt["terminal_reason"],
                http_statuses=[page["http_status"] for page in receipt["pages"]],
                retries=receipt["retries"],
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
