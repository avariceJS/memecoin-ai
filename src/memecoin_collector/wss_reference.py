"""Durable, fail-closed journal for bounded Solana logsSubscribe sessions.

This module does not open a socket or fetch transactions. A transport adapter may
pass complete UTF-8 JSON-RPC messages to it after the live-session gate is approved.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from memecoin_registry.contracts import digest

PUMP_BONDING_CURVE_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RAW_LIMIT_BYTES = 128 * 1024 * 1024
STORED_LIMIT_BYTES = 64 * 1024 * 1024
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_CONTROL_RESPONSE_BYTES = 64 * 1024
DISK_FLOOR_BYTES = 50 * 1024**3
MAX_SESSION_NS = 4 * 60 * 60 * 1_000_000_000
MAX_SESSIONS = 2
TRANSPORT_FAILURE_CODES = {
    "http_upgrade_io",
    "http_upgrade_rejected",
    "subscribe_response_io",
    "subscribe_response_invalid",
    "subscribe_response_cap",
    "connection_disconnected",
    "client_deadline",
    "authorization_expired",
    "session_clock_regression",
}


def _canonical(row: dict[str, Any]) -> bytes:
    return (json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _safe_now(now_ns: int) -> int:
    if type(now_ns) is not int or now_ns < 0:
        raise ValueError("invalid_wss_time")
    return now_ns


def parse_logs_message(raw: bytes) -> dict[str, Any]:
    """Parse only the standard logsSubscribe envelope, never economic semantics."""
    try:
        message = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("malformed_logs_message") from exc
    if not isinstance(message, dict):
        raise ValueError("logs_message_not_object")
    if message.get("jsonrpc") != "2.0":
        raise ValueError("logs_message_jsonrpc_version")
    if message.get("method") != "logsNotification":
        raise ValueError("logs_message_method")
    params = message.get("params")
    result = params.get("result") if isinstance(params, dict) else None
    subscription_id = params.get("subscription") if isinstance(params, dict) else None
    if type(subscription_id) is not int or subscription_id < 0:
        raise ValueError("logs_message_subscription_missing")
    context = result.get("context") if isinstance(result, dict) else None
    value = result.get("value") if isinstance(result, dict) else None
    if not isinstance(context, dict) or type(context.get("slot")) is not int or context["slot"] < 0:
        raise ValueError("logs_message_slot_missing")
    if not isinstance(value, dict):
        raise ValueError("logs_message_value_missing")
    signature = value.get("signature")
    if not isinstance(signature, str) or not signature:
        raise ValueError("logs_message_signature_missing")
    logs = value.get("logs")
    if logs is not None and (
        not isinstance(logs, list) or any(not isinstance(x, str) for x in logs)
    ):
        raise ValueError("logs_message_logs_shape")
    if "err" not in value:
        raise ValueError("logs_message_err_missing")
    return {
        "event_slot": context["slot"],
        "subscription_id": subscription_id,
        "signature": signature,
        "transaction_error": value["err"] is not None,
        "logs_present": logs is not None,
        "log_count": None if logs is None else len(logs),
    }


def parse_subscribe_response(raw: bytes) -> dict[str, Any]:
    """Project the bounded JSON-RPC response to the one subscribe request."""
    try:
        response = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return {"response_kind": "invalid", "request_id": None, "subscription_id": None}
    if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
        return {"response_kind": "invalid", "request_id": None, "subscription_id": None}
    request_id = response.get("id")
    if type(request_id) is not int or request_id != 1:
        return {"response_kind": "invalid", "request_id": None, "subscription_id": None}
    if type(response.get("result")) is int and response["result"] >= 0 and "error" not in response:
        return {
            "response_kind": "ack",
            "request_id": request_id,
            "subscription_id": response["result"],
        }
    if isinstance(response.get("error"), dict) and "result" not in response:
        return {"response_kind": "error", "request_id": request_id, "subscription_id": None}
    return {"response_kind": "invalid", "request_id": request_id, "subscription_id": None}


class WssReferenceSession:
    """Append-only one-connection journal; interruption seals a gap, never resumes."""

    def __init__(
        self,
        root: Path,
        binding: dict[str, Any],
        *,
        opened_ns: int,
        opened_monotonic_ns: int | None = None,
    ) -> None:
        opened_ns = _safe_now(opened_ns)
        if root.exists():
            raise ValueError("wss_session_root_exists")
        required = {
            "schema_version": 1,
            "provider": "helius-standard-wss",
            "program_id": PUMP_BONDING_CURVE_PROGRAM,
            "method": "logsSubscribe",
            "commitment": "finalized",
            "filter": {"mentions": [PUMP_BONDING_CURVE_PROGRAM]},
            "raw_limit_bytes": RAW_LIMIT_BYTES,
            "stored_limit_bytes": STORED_LIMIT_BYTES,
            "max_message_bytes": MAX_MESSAGE_BYTES,
            "max_duration_ns": MAX_SESSION_NS,
            "credential_transport": "private_env_in_process; never_argv_or_log",
        }
        if binding != required:
            raise ValueError("wss_session_binding_mismatch")
        self.root = root
        root.mkdir(parents=True, mode=0o700)
        (root / "raw").mkdir(mode=0o700)
        (root / "journal").mkdir(mode=0o700)
        _write_new(root / "binding.json", _canonical(binding))
        self.binding = binding
        self.seq = 0
        self.parent_sha256 = hashlib.sha256(_canonical(binding)).hexdigest()
        self.raw_bytes = 0
        self.stored_bytes = (root / "binding.json").stat().st_size
        self.seen_signatures: set[tuple[int, str]] = set()
        self.acknowledged = False
        self.subscription_id: int | None = None
        self.receive_prepared = False
        self.control_receive_prepared = False
        self.last_subscribe_response_sha256: str | None = None
        self.connected = False
        self.subscribe_sent = False
        self.terminal = False
        self.opened_ns = opened_ns
        if opened_monotonic_ns is None:
            opened_monotonic_ns = time.monotonic_ns()
        if type(opened_monotonic_ns) is not int or opened_monotonic_ns < 0:
            raise ValueError("invalid_wss_monotonic_open_time")
        self.opened_monotonic_ns = opened_monotonic_ns
        self.last_event_ns = opened_ns
        self._event(
            "session_open",
            opened_ns,
            {
                "event_time_basis": "slot_only",
                "opened_monotonic_ns": opened_monotonic_ns,
            },
        )

    def _event(self, kind: str, at_ns: int, fields: dict[str, Any]) -> dict[str, Any]:
        at_ns = _safe_now(at_ns)
        if self.terminal:
            raise ValueError("wss_session_terminal")
        if at_ns < self.last_event_ns:
            raise ValueError("wss_journal_clock_regression")
        self.seq += 1
        unsigned = {
            "sequence": self.seq,
            "kind": kind,
            "at_ns": at_ns,
            "parent_sha256": self.parent_sha256,
            **fields,
        }
        row = dict(unsigned, sha256=digest(unsigned))
        _write_new(self.root / "journal" / f"{self.seq:08d}.json", _canonical(row))
        self.parent_sha256 = row["sha256"]
        self.last_event_ns = at_ns
        self.stored_bytes += (self.root / "journal" / f"{self.seq:08d}.json").stat().st_size
        return row

    def connection_opened(self, at_ns: int) -> None:
        if self.connected or self.subscribe_sent:
            raise ValueError("wss_connection_state_invalid")
        self._event("connection_opened", at_ns, {})
        self.connected = True

    def record_upgrade_verified(self, at_ns: int, response_headers: bytes) -> None:
        """Record only the verified Upgrade response digest, never endpoint credentials."""
        if not self.connected or self.subscribe_sent or not isinstance(response_headers, bytes):
            raise ValueError("wss_upgrade_evidence_state_invalid")
        if not 1 <= len(response_headers) <= 16 * 1024:
            raise ValueError("wss_upgrade_evidence_size_invalid")
        self._event(
            "upgrade_verified",
            at_ns,
            {
                "response_bytes": len(response_headers),
                "response_sha256": hashlib.sha256(response_headers).hexdigest(),
                "tls_adapter_verified_peer": True,
                "endpoint_and_credentials_recorded": False,
            },
        )

    def request_subscribe(self, at_ns: int, *, request_id: int = 1) -> None:
        if not self.connected or self.subscribe_sent or request_id != 1:
            raise ValueError("wss_subscribe_request_invalid")
        self._event(
            "subscribe_sent",
            at_ns,
            {
                "request_id": request_id,
                "method": "logsSubscribe",
                "filter": {"mentions": [PUMP_BONDING_CURVE_PROGRAM]},
                "commitment": "finalized",
            },
        )
        self.subscribe_sent = True

    def acknowledge(
        self,
        subscription_id: int,
        at_ns: int,
        *,
        request_id: int = 1,
        response_sha256: str | None = None,
    ) -> None:
        if (
            not self.subscribe_sent
            or self.acknowledged
            or request_id != 1
            or type(subscription_id) is not int
            or subscription_id < 0
        ):
            raise ValueError("invalid_wss_subscription_ack")
        if at_ns < self.opened_ns or at_ns - self.opened_ns > MAX_SESSION_NS:
            raise ValueError("wss_ack_outside_session_window")
        if response_sha256 is not None and response_sha256 != self.last_subscribe_response_sha256:
            raise ValueError("wss_subscription_ack_evidence_mismatch")
        self._event(
            "subscription_ack",
            at_ns,
            {
                "request_id": request_id,
                "subscription_id": subscription_id,
                "response_sha256": response_sha256,
            },
        )
        self.acknowledged = True
        self.subscription_id = subscription_id

    def prepare_control_receive(self, *, at_ns: int, free_disk_bytes: int) -> bool:
        """Reserve bounded space for the pre-ack JSON-RPC response before reading it."""
        if self.terminal or not self.subscribe_sent or self.acknowledged:
            raise ValueError("wss_control_receive_state_invalid")
        at_ns = _safe_now(at_ns)
        if type(free_disk_bytes) is not int or free_disk_bytes < 0:
            raise ValueError("invalid_wss_disk_measurement")
        if at_ns - self.opened_ns >= MAX_SESSION_NS:
            self.stop("session_duration_cap", at_ns)
            return False
        if free_disk_bytes < DISK_FLOOR_BYTES:
            self.stop("disk_floor", at_ns)
            return False
        if (
            self.raw_bytes + MAX_CONTROL_RESPONSE_BYTES > RAW_LIMIT_BYTES
            or self.stored_bytes + MAX_CONTROL_RESPONSE_BYTES + 8192 > STORED_LIMIT_BYTES
        ):
            self.stop("pre_receive_byte_cap", at_ns)
            return False
        self.control_receive_prepared = True
        return True

    def record_subscribe_response(
        self,
        raw: bytes,
        *,
        received_ns: int,
        clock_ns: Callable[[], int],
    ) -> dict[str, Any]:
        """Persist the exact pre-ack response and its bounded derived classification."""
        received_ns = _safe_now(received_ns)
        if self.terminal or not self.control_receive_prepared or self.acknowledged:
            raise ValueError("wss_control_response_state_invalid")
        self.control_receive_prepared = False
        if not isinstance(raw, bytes) or len(raw) > MAX_CONTROL_RESPONSE_BYTES:
            raise ValueError("wss_control_response_size_invalid")
        projection = parse_subscribe_response(raw)
        if received_ns < self.opened_ns or received_ns - self.opened_ns >= MAX_SESSION_NS:
            self.stop("session_duration_cap", received_ns)
            raise ValueError("wss_control_response_outside_session_window")
        if (
            self.raw_bytes + len(raw) > RAW_LIMIT_BYTES
            or self.stored_bytes + len(raw) + 8192 > STORED_LIMIT_BYTES
        ):
            self._event(
                "message_rejected",
                received_ns,
                {
                    "reason": "control_byte_cap",
                    "raw_bytes": len(raw),
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                },
            )
            self.stop("byte_cap_gap", received_ns)
            raise ValueError("wss_control_response_byte_cap")
        seq = self.seq + 1
        raw_path = self.root / "raw" / f"{seq:08d}.bin"
        _write_new(raw_path, raw)
        available_ns = _safe_now(clock_ns())
        if available_ns < received_ns:
            raise ValueError("wss_availability_precedes_receipt")
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        self.raw_bytes += len(raw)
        self.stored_bytes += len(raw)
        row = self._event(
            "protocol_response",
            received_ns,
            {
                **projection,
                "received_ns": received_ns,
                "availability_ns": available_ns,
                "decision_ns": None,
                "raw_path": raw_path.relative_to(self.root).as_posix(),
                "raw_sha256": raw_sha256,
                "raw_bytes": len(raw),
            },
        )
        self.last_subscribe_response_sha256 = raw_sha256
        return row

    def record_transport_failure(
        self,
        *,
        phase: str,
        reason: str,
        raw_evidence: bytes,
        at_ns: int,
    ) -> dict[str, Any]:
        """Preserve bounded HTTP failure bytes without charging them as RPC messages."""
        at_ns = _safe_now(at_ns)
        if (
            self.terminal
            or phase not in {"http_upgrade", "subscribe_response"}
            or reason not in TRANSPORT_FAILURE_CODES
            or not isinstance(raw_evidence, bytes)
            or len(raw_evidence) > 16 * 1024
        ):
            raise ValueError("wss_transport_failure_evidence_invalid")
        raw_path = self.root / "raw" / f"{self.seq + 1:08d}.bin"
        raw_sha256 = hashlib.sha256(raw_evidence).hexdigest()
        _write_new(raw_path, raw_evidence)
        self.stored_bytes += len(raw_evidence)
        return self._event(
            "transport_failure",
            at_ns,
            {
                "phase": phase,
                "reason": reason,
                "raw_path": raw_path.relative_to(self.root).as_posix(),
                "raw_sha256": raw_sha256,
                "raw_bytes": len(raw_evidence),
            },
        )

    def prepare_receive(self, *, at_ns: int, free_disk_bytes: int) -> bool:
        """Reserve worst-case next-message headroom before the transport reads it."""
        if self.terminal:
            raise ValueError("wss_session_terminal")
        at_ns = _safe_now(at_ns)
        if type(free_disk_bytes) is not int or free_disk_bytes < 0:
            raise ValueError("invalid_wss_disk_measurement")
        if not self.acknowledged:
            raise ValueError("wss_notification_before_ack")
        if at_ns - self.opened_ns >= MAX_SESSION_NS:
            self.stop("session_duration_cap", at_ns)
            return False
        if free_disk_bytes < DISK_FLOOR_BYTES:
            self.stop("disk_floor", at_ns)
            return False
        if (
            self.raw_bytes + MAX_MESSAGE_BYTES > RAW_LIMIT_BYTES
            or self.stored_bytes + MAX_MESSAGE_BYTES + 8192 > STORED_LIMIT_BYTES
        ):
            self.stop("pre_receive_byte_cap", at_ns)
            return False
        self.receive_prepared = True
        return True

    def record_message(
        self,
        raw: bytes,
        *,
        received_ns: int,
        clock_ns: Callable[[], int],
    ) -> dict[str, Any]:
        received_ns = _safe_now(received_ns)
        if self.terminal:
            raise ValueError("wss_session_terminal")
        if not self.acknowledged:
            raise ValueError("wss_notification_before_ack")
        if not self.receive_prepared:
            raise ValueError("wss_receive_not_prepared")
        self.receive_prepared = False
        if received_ns < self.opened_ns or received_ns - self.opened_ns >= MAX_SESSION_NS:
            self.stop("session_duration_cap", received_ns)
            raise ValueError("wss_message_outside_session_window")
        if not isinstance(raw, bytes):
            raise ValueError("wss_message_must_be_bytes")
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        if len(raw) > MAX_MESSAGE_BYTES:
            self._event(
                "message_rejected",
                received_ns,
                {"reason": "message_cap", "raw_bytes": len(raw), "raw_sha256": raw_sha256},
            )
            self.stop("message_cap_gap", received_ns)
            raise ValueError("wss_message_cap")
        # Leave room for the immutable per-message row and terminal STOP receipt.
        if (
            self.raw_bytes + len(raw) > RAW_LIMIT_BYTES
            or self.stored_bytes + len(raw) + 8192 > STORED_LIMIT_BYTES
        ):
            self._event(
                "message_rejected",
                received_ns,
                {"reason": "byte_cap", "raw_bytes": len(raw), "raw_sha256": raw_sha256},
            )
            self.stop("byte_cap_gap", received_ns)
            raise ValueError("wss_byte_cap")

        next_seq = self.seq + 1
        raw_path = self.root / "raw" / f"{next_seq:08d}.bin"
        _write_new(raw_path, raw)
        # Availability is measured after the exact raw application message is durable.
        available_ns = _safe_now(clock_ns())
        if available_ns < received_ns:
            raise ValueError("wss_availability_precedes_receipt")
        self.raw_bytes += len(raw)
        self.stored_bytes += len(raw)
        try:
            facts = parse_logs_message(raw)
            if facts["subscription_id"] != self.subscription_id:
                raise ValueError("logs_message_subscription_mismatch")
            identity = (facts["event_slot"], facts["signature"])
            duplicate = identity in self.seen_signatures
            self.seen_signatures.add(identity)
            parse_state = "notification"
        except ValueError as exc:
            facts = {
                "event_slot": None,
                "subscription_id": None,
                "signature": None,
                "transaction_error": None,
            }
            duplicate = False
            parse_state = str(exc)
        return self._event(
            "message",
            received_ns,
            {
                "received_ns": received_ns,
                "availability_ns": available_ns,
                "decision_ns": None,
                "raw_path": raw_path.relative_to(self.root).as_posix(),
                "raw_sha256": raw_sha256,
                "raw_bytes": len(raw),
                "parse_state": parse_state,
                "duplicate_delivery": duplicate,
                **facts,
            },
        )

    def reject_transport_header(
        self, *, raw_header: bytes, declared_payload_bytes: int, received_ns: int
    ) -> dict[str, Any]:
        """Record only the bounded frame header when its payload is refused pre-read."""
        if self.terminal or not self.receive_prepared:
            raise ValueError("wss_transport_rejection_state_invalid")
        if (
            not isinstance(raw_header, bytes)
            or not 2 <= len(raw_header) <= 10
            or type(declared_payload_bytes) is not int
            or declared_payload_bytes < 0
        ):
            raise ValueError("wss_transport_rejection_invalid")
        received_ns = _safe_now(received_ns)
        self.receive_prepared = False
        self._event(
            "message_rejected",
            received_ns,
            {
                "reason": "websocket_frame_payload_cap",
                "frame_header_hex": raw_header.hex(),
                "declared_payload_bytes": declared_payload_bytes,
                "payload_read": False,
            },
        )
        return self.stop("websocket_frame_payload_cap", received_ns)

    def stop(
        self,
        reason: str,
        at_ns: int,
        *,
        unreferenced_raw_files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        messages = []
        control_responses = 0
        transport_failures = 0
        rejected = 0
        for path in (self.root / "journal").glob("*.json"):
            event = json.loads(path.read_text())
            if event.get("kind") == "message":
                messages.append(event)
            elif event.get("kind") == "protocol_response":
                control_responses += 1
            elif event.get("kind") == "transport_failure":
                transport_failures += 1
            elif event.get("kind") == "message_rejected":
                rejected += 1
        row = self._event(
            "session_stop",
            at_ns,
            {
                "reason": reason,
                "raw_bytes": self.raw_bytes,
                "stored_bytes_before_stop": self.stored_bytes,
                "notifications": len(messages),
                "control_responses": control_responses,
                "transport_failures": transport_failures,
                "rejected_messages_or_frames": rejected,
                "parse_failures": sum(m.get("parse_state") != "notification" for m in messages),
                "duplicate_deliveries": sum(bool(m.get("duplicate_delivery")) for m in messages),
                "failed_transactions": sum(m.get("transaction_error") is True for m in messages),
                "unique_slot_signatures": len(self.seen_signatures),
                "subscription_acknowledged": self.acknowledged,
                "unreferenced_raw_files": unreferenced_raw_files or [],
            },
        )
        self.terminal = True
        return row

    def recover_interrupted(self, at_ns: int) -> dict[str, Any]:
        """Seal an unclosed session as interrupted; this object cannot resume it."""
        if self.terminal:
            raise ValueError("wss_session_already_terminal")
        return self.stop("process_interrupted_no_resume", at_ns)

    @classmethod
    def recover(cls, root: Path, *, at_ns: int) -> WssReferenceSession:
        """Verify a durable prefix and seal it as interrupted; never reopen its stream."""
        at_ns = _safe_now(at_ns)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("wss_session_root_invalid")
        if stat.S_IMODE(root.stat().st_mode) != 0o700:
            raise ValueError("wss_session_root_mode_invalid")
        allowed = {
            "binding.json",
            "raw",
            "journal",
            "campaign-start.json",
            "campaign-session-stop.json",
        }
        if not {p.name for p in root.iterdir()}.issubset(allowed) or not {
            "binding.json",
            "raw",
            "journal",
        }.issubset({p.name for p in root.iterdir()}):
            raise ValueError("wss_session_tree_invalid")
        binding_path = root / "binding.json"
        journal_dir = root / "journal"
        raw_dir = root / "raw"
        for sidecar_name in ("campaign-start.json", "campaign-session-stop.json"):
            sidecar = root / sidecar_name
            if sidecar.exists() and (
                sidecar.is_symlink()
                or not sidecar.is_file()
                or stat.S_IMODE(sidecar.stat().st_mode) != 0o600
            ):
                raise ValueError("wss_session_sidecar_invalid")
        if (
            binding_path.is_symlink()
            or not binding_path.is_file()
            or stat.S_IMODE(binding_path.stat().st_mode) != 0o600
            or journal_dir.is_symlink()
            or not journal_dir.is_dir()
            or stat.S_IMODE(journal_dir.stat().st_mode) != 0o700
            or raw_dir.is_symlink()
            or not raw_dir.is_dir()
            or stat.S_IMODE(raw_dir.stat().st_mode) != 0o700
        ):
            raise ValueError("wss_session_tree_invalid")
        if any(
            p.is_symlink() or not p.is_file() or stat.S_IMODE(p.stat().st_mode) != 0o600
            for p in journal_dir.iterdir()
        ):
            raise ValueError("wss_journal_tree_invalid")
        binding = json.loads(binding_path.read_text())
        if binding != default_binding():
            raise ValueError("wss_session_binding_mismatch")
        rows = []
        parent = hashlib.sha256(_canonical(binding)).hexdigest()
        raw_names: set[str] = set()
        previous_at = -1
        ack_subscription_id: int | None = None
        last_subscribe_response_sha256: str | None = None
        last_subscribe_response_kind: str | None = None
        seen_identities: set[tuple[int, str]] = set()
        connected = False
        subscribe_sent = False
        journal_files = sorted(journal_dir.glob("*.json"))
        if len(journal_files) != len(list(journal_dir.iterdir())):
            raise ValueError("wss_journal_tree_invalid")
        for expected, path in enumerate(journal_files, start=1):
            row = json.loads(path.read_text())
            unsigned = {k: v for k, v in row.items() if k != "sha256"}
            if (
                path.name != f"{expected:08d}.json"
                or row.get("sequence") != expected
                or row.get("parent_sha256") != parent
                or row.get("sha256") != digest(unsigned)
                or type(row.get("at_ns")) is not int
                or row["at_ns"] < previous_at
            ):
                raise ValueError("wss_journal_chain_invalid")
            parent, previous_at = row["sha256"], row["at_ns"]
            if row.get("kind") not in {
                "session_open",
                "connection_opened",
                "upgrade_verified",
                "subscribe_sent",
                "subscription_ack",
                "protocol_response",
                "transport_failure",
                "message",
                "message_rejected",
                "session_stop",
            }:
                raise ValueError("wss_journal_event_unknown")
            if (expected == 1) != (row.get("kind") == "session_open"):
                raise ValueError("wss_session_open_order_invalid")
            if row.get("kind") == "session_stop" and expected != len(journal_files):
                raise ValueError("wss_event_after_terminal_stop")
            if row.get("kind") == "connection_opened":
                if connected or subscribe_sent:
                    raise ValueError("wss_connection_state_invalid")
                connected = True
            if row.get("kind") == "upgrade_verified":
                response_hash = row.get("response_sha256")
                if (
                    not connected
                    or subscribe_sent
                    or type(row.get("response_bytes")) is not int
                    or not 1 <= row["response_bytes"] <= 16 * 1024
                    or not isinstance(response_hash, str)
                    or len(response_hash) != 64
                    or row.get("tls_adapter_verified_peer") is not True
                    or row.get("endpoint_and_credentials_recorded") is not False
                ):
                    raise ValueError("wss_upgrade_evidence_invalid")
            if row.get("kind") == "subscribe_sent":
                if not connected or subscribe_sent or row.get("request_id") != 1:
                    raise ValueError("wss_subscribe_request_invalid")
                if (
                    row.get("method") != "logsSubscribe"
                    or row.get("filter") != {"mentions": [PUMP_BONDING_CURVE_PROGRAM]}
                    or row.get("commitment") != "finalized"
                ):
                    raise ValueError("wss_subscribe_binding_mismatch")
                subscribe_sent = True
            if row.get("kind") == "subscription_ack":
                if (
                    not subscribe_sent
                    or ack_subscription_id is not None
                    or row.get("request_id") != 1
                    or type(row.get("subscription_id")) is not int
                    or (
                        last_subscribe_response_sha256 is not None
                        and (
                            row.get("response_sha256") != last_subscribe_response_sha256
                            or last_subscribe_response_kind != "ack"
                        )
                    )
                    or (
                        row.get("response_sha256") is not None
                        and (
                            row.get("response_sha256") != last_subscribe_response_sha256
                            or last_subscribe_response_kind != "ack"
                        )
                    )
                ):
                    raise ValueError("wss_subscription_ack_invalid")
                ack_subscription_id = row["subscription_id"]
            if row.get("kind") == "protocol_response":
                if (
                    not subscribe_sent
                    or ack_subscription_id is not None
                    or last_subscribe_response_sha256
                ):
                    raise ValueError("wss_subscription_response_order_invalid")
                name = row.get("raw_path")
                if (
                    not isinstance(name, str)
                    or not name.startswith("raw/")
                    or ".." in Path(name).parts
                    or Path(name).name in raw_names
                ):
                    raise ValueError("wss_raw_reference_invalid")
                raw_names.add(Path(name).name)
                raw_path = root / name
                if not raw_path.is_file() or raw_path.is_symlink():
                    raise ValueError("wss_raw_capture_missing")
                raw = raw_path.read_bytes()
                raw_sha256 = hashlib.sha256(raw).hexdigest()
                if len(raw) != row.get("raw_bytes") or raw_sha256 != row.get("raw_sha256"):
                    raise ValueError("wss_raw_capture_hash_mismatch")
                if (
                    row.get("received_ns") != row.get("at_ns")
                    or type(row.get("availability_ns")) is not int
                    or row["availability_ns"] < row["received_ns"]
                    or row.get("decision_ns") is not None
                ):
                    raise ValueError("wss_message_time_contract_invalid")
                projection = parse_subscribe_response(raw)
                if any(row.get(key) != value for key, value in projection.items()):
                    raise ValueError("wss_subscription_response_projection_mismatch")
                last_subscribe_response_sha256 = raw_sha256
                last_subscribe_response_kind = projection["response_kind"]
            if row.get("kind") == "transport_failure":
                name = row.get("raw_path")
                if (
                    row.get("phase") not in {"http_upgrade", "subscribe_response"}
                    or row.get("reason") not in TRANSPORT_FAILURE_CODES
                    or not isinstance(name, str)
                    or not name.startswith("raw/")
                    or ".." in Path(name).parts
                    or Path(name).name in raw_names
                ):
                    raise ValueError("wss_transport_failure_projection_invalid")
                raw_names.add(Path(name).name)
                raw_path = root / name
                if not raw_path.is_file() or raw_path.is_symlink():
                    raise ValueError("wss_raw_capture_missing")
                raw = raw_path.read_bytes()
                if (
                    len(raw) != row.get("raw_bytes")
                    or len(raw) > 16 * 1024
                    or hashlib.sha256(raw).hexdigest() != row.get("raw_sha256")
                ):
                    raise ValueError("wss_raw_capture_hash_mismatch")
            if row.get("kind") == "message":
                if ack_subscription_id is None:
                    raise ValueError("wss_notification_before_ack")
                name = row.get("raw_path")
                if (
                    not isinstance(name, str)
                    or not name.startswith("raw/")
                    or ".." in Path(name).parts
                    or Path(name).name in raw_names
                ):
                    raise ValueError("wss_raw_reference_invalid")
                raw_names.add(Path(name).name)
                raw_path = root / name
                if not raw_path.is_file() or raw_path.is_symlink():
                    raise ValueError("wss_raw_capture_missing")
                raw = raw_path.read_bytes()
                if len(raw) != row.get("raw_bytes") or hashlib.sha256(raw).hexdigest() != row.get(
                    "raw_sha256"
                ):
                    raise ValueError("wss_raw_capture_hash_mismatch")
                if (
                    row.get("received_ns") != row.get("at_ns")
                    or type(row.get("availability_ns")) is not int
                    or row["availability_ns"] < row["received_ns"]
                    or row.get("decision_ns") is not None
                ):
                    raise ValueError("wss_message_time_contract_invalid")
                try:
                    facts = parse_logs_message(raw)
                    if facts["subscription_id"] != ack_subscription_id:
                        raise ValueError("logs_message_subscription_mismatch")
                    expected_state = "notification"
                except ValueError as exc:
                    facts = {
                        "event_slot": None,
                        "subscription_id": None,
                        "signature": None,
                        "transaction_error": None,
                    }
                    expected_state = str(exc)
                if any(row.get(key) != value for key, value in facts.items()):
                    raise ValueError("wss_message_projection_mismatch")
                if row.get("parse_state") != expected_state:
                    raise ValueError("wss_message_parse_state_mismatch")
                identity = (facts.get("event_slot"), facts.get("signature"))
                duplicate = identity in seen_identities if facts.get("signature") else False
                if row.get("duplicate_delivery") is not duplicate:
                    raise ValueError("wss_duplicate_projection_mismatch")
                if facts.get("signature"):
                    seen_identities.add(identity)
            rows.append(row)
        actual_raw = {p.name for p in raw_dir.iterdir() if p.is_file()}
        if any(
            p.is_symlink() or not p.is_file() or stat.S_IMODE(p.stat().st_mode) != 0o600
            for p in raw_dir.iterdir()
        ):
            raise ValueError("wss_raw_tree_invalid")
        orphan_names = sorted(actual_raw - raw_names)
        obj = cls.__new__(cls)
        obj.root = root
        obj.binding = binding
        obj.seq = len(rows)
        obj.parent_sha256 = parent
        obj.last_event_ns = previous_at
        obj.raw_bytes = sum(
            row["raw_bytes"] for row in rows if row.get("kind") in {"message", "protocol_response"}
        )
        obj.stored_bytes = sum(
            path.stat().st_size
            for path in root.rglob("*")
            if path.is_file()
            and not (
                path.parent == root
                and path.name in {"campaign-start.json", "campaign-session-stop.json"}
            )
        )
        if obj.raw_bytes > RAW_LIMIT_BYTES or obj.stored_bytes > STORED_LIMIT_BYTES:
            raise ValueError("wss_recovered_byte_cap_exceeded")
        obj.seen_signatures = {
            (row["event_slot"], row["signature"])
            for row in rows
            if row.get("kind") == "message"
            and row.get("event_slot") is not None
            and row.get("signature")
        }
        obj.acknowledged = any(row.get("kind") == "subscription_ack" for row in rows)
        obj.subscription_id = ack_subscription_id
        obj.receive_prepared = False
        obj.control_receive_prepared = False
        obj.last_subscribe_response_sha256 = last_subscribe_response_sha256
        obj.connected = connected
        obj.subscribe_sent = subscribe_sent
        obj.terminal = bool(rows and rows[-1].get("kind") == "session_stop")
        opened = next((row["at_ns"] for row in rows if row.get("kind") == "session_open"), None)
        opened_monotonic = next(
            (row.get("opened_monotonic_ns") for row in rows if row.get("kind") == "session_open"),
            None,
        )
        if opened is None:
            raise ValueError("wss_session_open_missing")
        obj.opened_ns = opened
        if type(opened_monotonic) is not int or opened_monotonic < 0:
            raise ValueError("wss_session_monotonic_open_time_invalid")
        obj.opened_monotonic_ns = opened_monotonic
        if obj.terminal:
            stop_row = rows[-1]
            stop_path = journal_files[-1]
            if (
                stop_row.get("raw_bytes") != obj.raw_bytes
                or stop_row.get("stored_bytes_before_stop")
                != obj.stored_bytes - stop_path.stat().st_size
                or stop_row.get("notifications")
                != sum(row.get("kind") == "message" for row in rows)
                or stop_row.get("control_responses")
                != sum(row.get("kind") == "protocol_response" for row in rows)
                or stop_row.get("transport_failures")
                != sum(row.get("kind") == "transport_failure" for row in rows)
            ):
                raise ValueError("wss_session_stop_accounting_mismatch")
            return obj
        if orphan_names:
            orphan_facts = []
            for name in orphan_names:
                data = (root / "raw" / name).read_bytes()
                orphan_facts.append(
                    {
                        "path": f"raw/{name}",
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
            obj.stop(
                "process_interrupted_after_raw_write_no_resume",
                at_ns,
                unreferenced_raw_files=orphan_facts,
            )
        else:
            obj.stop("process_interrupted_no_resume", at_ns)
        return obj


def default_binding() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "provider": "helius-standard-wss",
        "program_id": PUMP_BONDING_CURVE_PROGRAM,
        "method": "logsSubscribe",
        "commitment": "finalized",
        "filter": {"mentions": [PUMP_BONDING_CURVE_PROGRAM]},
        "raw_limit_bytes": RAW_LIMIT_BYTES,
        "stored_limit_bytes": STORED_LIMIT_BYTES,
        "max_message_bytes": MAX_MESSAGE_BYTES,
        "max_duration_ns": MAX_SESSION_NS,
        "credential_transport": "private_env_in_process; never_argv_or_log",
    }
