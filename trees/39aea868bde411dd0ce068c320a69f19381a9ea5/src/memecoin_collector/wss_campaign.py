"""Finite, single-writer WSS campaign journal and authorization binding."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from memecoin_collector.wss_reference import (
    DISK_FLOOR_BYTES,
    MAX_CONTROL_RESPONSE_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_SESSION_NS,
    MAX_BILLABLE_MESSAGES,
    MAX_BILLABLE_UNCOMPRESSED_BYTES,
    MAX_TRANSPORT_CONTROL_FRAMES,
    MAX_TRANSPORT_CONTROL_WIRE_BYTES,
    RAW_LIMIT_BYTES,
    RECONNECTABLE_REASONS,
    STORED_LIMIT_BYTES,
    WssReferenceSession,
    _canonical,
    _safe_now,
    _write_new,
    default_binding,
)

MAX_SESSIONS = 2
MAX_AUTHORIZATION_NS = 7 * 24 * 60 * 60 * 1_000_000_000


def default_authorization(
    *,
    c4_stop_sha256: str,
    account_budget_evidence_sha256: str,
    authorized_at_ns: int,
    expires_at_ns: int,
) -> dict[str, Any]:
    """Build the credential-free, fixed future campaign authorization binding."""
    transport = Path(__file__).with_name("wss_transport.py")
    reference = Path(__file__).with_name("wss_reference.py")
    client = Path(__file__).with_name("wss_client.py")
    tls = Path(__file__).with_name("wss_tls.py")
    private_config = Path(__file__).with_name("private_config.py")
    socket_adapter = Path(__file__).with_name("wss_socket.py")
    resolver = Path(__file__).resolve().parents[2] / "tools" / "brain_senses_wss_resolver.py"
    return {
        "schema_version": 2,
        "provider": "helius-standard-wss",
        "program_id": default_binding()["program_id"],
        "method": "logsSubscribe",
        "commitment": "finalized",
        "filter": {"mentions": [default_binding()["program_id"]]},
        "c4_stop_sha256": c4_stop_sha256,
        "account_budget_evidence_sha256": account_budget_evidence_sha256,
        "source_code_sha256": {
            "wss_campaign.py": _sha(Path(__file__).read_bytes()),
            "wss_reference.py": _sha(reference.read_bytes()),
            "wss_transport.py": _sha(transport.read_bytes()),
            "wss_client.py": _sha(client.read_bytes()),
            "wss_tls.py": _sha(tls.read_bytes()),
            "private_config.py": _sha(private_config.read_bytes()),
            "wss_socket.py": _sha(socket_adapter.read_bytes()),
            "brain_senses_wss_resolver.py": _sha(resolver.read_bytes()),
        },
        "authorized_at_ns": authorized_at_ns,
        "expires_at_ns": expires_at_ns,
        "max_sessions": MAX_SESSIONS,
        "max_session_ns": MAX_SESSION_NS,
        "raw_limit_bytes": RAW_LIMIT_BYTES,
        "stored_limit_bytes": STORED_LIMIT_BYTES,
        "max_message_bytes": MAX_MESSAGE_BYTES,
        "max_billable_messages": MAX_BILLABLE_MESSAGES,
        "max_billable_uncompressed_bytes": MAX_BILLABLE_UNCOMPRESSED_BYTES,
        "max_transport_control_frames": MAX_TRANSPORT_CONTROL_FRAMES,
        "max_transport_control_wire_bytes": MAX_TRANSPORT_CONTROL_WIRE_BYTES,
        "minimum_free_disk_bytes": DISK_FLOOR_BYTES,
        "credential_transport": "private_env_in_process; never_argv_or_log",
        "resume_after_process_interruption": False,
        "backfill_or_http_retrieval": False,
    }


def _validate_authorization(row: dict[str, Any]) -> None:
    if not isinstance(row, dict):
        raise ValueError("wss_campaign_authorization_invalid")
    required = default_authorization(
        c4_stop_sha256=row.get("c4_stop_sha256", ""),
        account_budget_evidence_sha256=row.get("account_budget_evidence_sha256", ""),
        authorized_at_ns=row.get("authorized_at_ns", -1),
        expires_at_ns=row.get("expires_at_ns", -1),
    )
    if row != required:
        raise ValueError("wss_campaign_authorization_binding_mismatch")
    for name in ("c4_stop_sha256", "account_budget_evidence_sha256"):
        value = row[name]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ValueError("wss_campaign_evidence_hash_invalid")
    start = _safe_now(row["authorized_at_ns"])
    end = _safe_now(row["expires_at_ns"])
    if end <= start or end - start > MAX_AUTHORIZATION_NS:
        raise ValueError("wss_campaign_authorization_window_invalid")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class WssReferenceCampaign:
    """One-process owner for at most two strictly sequential observation windows."""

    def __init__(self, root: Path, authorization: dict[str, Any], *, opened_ns: int) -> None:
        _validate_authorization(authorization)
        opened_ns = _safe_now(opened_ns)
        if not authorization["authorized_at_ns"] <= opened_ns < authorization["expires_at_ns"]:
            raise ValueError("wss_campaign_open_outside_authorization")
        if root.exists():
            raise ValueError("wss_campaign_root_exists")
        root.mkdir(parents=True, mode=0o700)
        (root / "sessions").mkdir(mode=0o700)
        self.root = root
        self.authorization = authorization
        self.authorization_sha256 = _sha(_canonical(authorization))
        _write_new(root / "authorization.json", _canonical(authorization))
        lock_path = root / ".writer.lock"
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.lock_fd = fd
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            os.close(fd)
            raise ValueError("wss_campaign_writer_lock_failed") from None
        self.sessions: list[WssReferenceSession] = []
        self.active: WssReferenceSession | None = None
        self.terminal = False
        self._closed = False
        self._write_state(
            "campaign_open.json",
            {
                "status": "open",
                "opened_ns": opened_ns,
                "authorization_sha256": self.authorization_sha256,
                "session_count": 0,
            },
        )

    def _write_state(self, name: str, row: dict[str, Any]) -> dict[str, Any]:
        sealed = dict(row, sha256=_sha(_canonical(row)))
        _write_new(self.root / name, _canonical(sealed))
        return sealed

    @staticmethod
    def _verify_seal(row: dict[str, Any], code: str) -> dict[str, Any]:
        unsigned = {key: value for key, value in row.items() if key != "sha256"}
        if row.get("sha256") != _sha(_canonical(unsigned)):
            raise ValueError(code)
        return unsigned

    def _stored_bytes(self) -> int:
        total = 0
        for path in self.root.rglob("*"):
            if path.is_symlink():
                raise ValueError("wss_campaign_symlink_unsupported")
            if path.is_file():
                total += path.stat().st_size
        return total

    def _assert_writer_open(self) -> None:
        if self._closed or getattr(self, "lock_fd", None) is None:
            raise ValueError("wss_campaign_writer_released")

    def start_session(
        self,
        *,
        at_ns: int,
        free_disk_bytes: int,
        opened_monotonic_ns: int | None = None,
    ) -> WssReferenceSession:
        self._assert_writer_open()
        at_ns = _safe_now(at_ns)
        if self.terminal:
            raise ValueError("wss_campaign_terminal")
        if (
            not self.authorization["authorized_at_ns"]
            <= at_ns
            < self.authorization["expires_at_ns"]
        ):
            self.stop("authorization_expired_or_not_yet_active", at_ns)
            raise ValueError("wss_campaign_outside_authorization")
        if self.active is not None:
            raise ValueError("wss_campaign_session_already_active")
        if len(self.sessions) >= MAX_SESSIONS:
            self.stop("session_count_cap", at_ns)
            raise ValueError("wss_campaign_session_count_cap")
        if type(free_disk_bytes) is not int or free_disk_bytes < DISK_FLOOR_BYTES:
            self.stop("disk_floor", at_ns)
            raise ValueError("wss_campaign_disk_floor")
        if self._stored_bytes() + MAX_MESSAGE_BYTES + 16_384 > STORED_LIMIT_BYTES:
            self.stop("aggregate_stored_cap", at_ns)
            raise ValueError("wss_campaign_stored_cap")
        if sum(item.raw_bytes for item in self.sessions) + MAX_MESSAGE_BYTES > RAW_LIMIT_BYTES:
            self.stop("aggregate_raw_cap", at_ns)
            raise ValueError("wss_campaign_raw_cap")
        if (
            sum(item.billable_message_count for item in self.sessions) + 1
            > self.authorization["max_billable_messages"]
            or sum(item.billable_uncompressed_bytes for item in self.sessions) + MAX_MESSAGE_BYTES
            > self.authorization["max_billable_uncompressed_bytes"]
        ):
            self.stop("billable_message_cap", at_ns)
            raise ValueError("wss_campaign_billable_message_cap")
        gap = None
        if self.sessions:
            previous_stop = self._last_stop(self.sessions[-1])
            if previous_stop["reason"] not in RECONNECTABLE_REASONS:
                self.stop("previous_session_stop_not_reconnectable", at_ns)
                raise ValueError("wss_campaign_reconnect_not_authorized")
            if at_ns < previous_stop["at_ns"]:
                self.stop("reconnect_time_regression", at_ns)
                raise ValueError("wss_campaign_reconnect_time_regression")
            gap = {"start_ns": previous_stop["at_ns"], "end_ns": at_ns, "explicit": True}
        index = len(self.sessions) + 1
        session_root = self.root / "sessions" / f"{index:02d}"
        if opened_monotonic_ns is None:
            opened_monotonic_ns = time.monotonic_ns()
        session = WssReferenceSession(
            session_root,
            default_binding(),
            opened_ns=at_ns,
            opened_monotonic_ns=opened_monotonic_ns,
            writer_guard=lambda: not self._closed and self.lock_fd is not None,
        )
        start = {
            "session_index": index,
            "opened_ns": at_ns,
            "opened_monotonic_ns": opened_monotonic_ns,
            "authorization_sha256": self.authorization_sha256,
            "session_binding_sha256": _sha(_canonical(session.binding)),
            "preceding_gap": gap,
        }
        self._write_state(f"sessions/{index:02d}/campaign-start.json", start)
        self.sessions.append(session)
        self.active = session
        return session

    def prepare_receive(self, *, at_ns: int, free_disk_bytes: int) -> bool:
        self._assert_writer_open()
        if self.terminal or self.active is None:
            raise ValueError("wss_campaign_no_active_session")
        at_ns = _safe_now(at_ns)
        if not (
            self.authorization["authorized_at_ns"] <= at_ns < self.authorization["expires_at_ns"]
        ):
            self.stop("authorization_expired", at_ns)
            return False
        if sum(item.raw_bytes for item in self.sessions) + MAX_MESSAGE_BYTES > RAW_LIMIT_BYTES:
            self.stop("aggregate_raw_cap", at_ns)
            return False
        if (
            sum(item.billable_message_count for item in self.sessions) + 1
            > self.authorization["max_billable_messages"]
            or sum(item.billable_uncompressed_bytes for item in self.sessions) + MAX_MESSAGE_BYTES
            > self.authorization["max_billable_uncompressed_bytes"]
        ):
            self.stop("billable_message_cap", at_ns)
            return False
        if self._stored_bytes() + MAX_MESSAGE_BYTES + 16_384 > STORED_LIMIT_BYTES:
            self.stop("aggregate_stored_cap", at_ns)
            return False
        accepted = self.active.prepare_receive(at_ns=at_ns, free_disk_bytes=free_disk_bytes)
        if not accepted:
            self.stop("session_preflight_stop", at_ns)
        return accepted

    def prepare_control_receive(self, *, at_ns: int, free_disk_bytes: int) -> bool:
        """Reserve aggregate disk/raw/stored capacity for the subscription response."""
        self._assert_writer_open()
        if self.terminal or self.active is None:
            raise ValueError("wss_campaign_no_active_session")
        at_ns = _safe_now(at_ns)
        if not (
            self.authorization["authorized_at_ns"] <= at_ns < self.authorization["expires_at_ns"]
        ):
            self.stop("authorization_expired", at_ns)
            return False
        if (
            sum(item.raw_bytes for item in self.sessions) + MAX_CONTROL_RESPONSE_BYTES
            > RAW_LIMIT_BYTES
        ):
            self.stop("aggregate_raw_cap", at_ns)
            return False
        if (
            sum(item.billable_message_count for item in self.sessions) + 1
            > self.authorization["max_billable_messages"]
            or sum(item.billable_uncompressed_bytes for item in self.sessions) + MAX_CONTROL_RESPONSE_BYTES
            > self.authorization["max_billable_uncompressed_bytes"]
        ):
            self.stop("billable_message_cap", at_ns)
            return False
        if self._stored_bytes() + MAX_CONTROL_RESPONSE_BYTES + 16_384 > STORED_LIMIT_BYTES:
            self.stop("aggregate_stored_cap", at_ns)
            return False
        accepted = self.active.prepare_control_receive(at_ns=at_ns, free_disk_bytes=free_disk_bytes)
        if not accepted:
            self.stop("session_preflight_stop", at_ns)
        return accepted

    def record_subscribe_response(
        self, raw: bytes, *, received_ns: int, clock_ns: Callable[[], int]
    ) -> dict[str, Any]:
        self._assert_writer_open()
        if self.terminal or self.active is None:
            raise ValueError("wss_campaign_no_active_session")
        return self.active.record_subscribe_response(
            raw, received_ns=received_ns, clock_ns=clock_ns
        )

    def record_message(self, raw: bytes, *, received_ns: int, clock_ns) -> dict[str, Any]:
        self._assert_writer_open()
        if self.terminal or self.active is None:
            raise ValueError("wss_campaign_no_active_session")
        return self.active.record_message(raw, received_ns=received_ns, clock_ns=clock_ns)

    def receive_json_message(
        self,
        reader,
        *,
        at_ns: int,
        free_disk_bytes: int,
        clock_ns: Callable[[], int],
        send_client_frame: Callable[[bytes], None] | None = None,
    ) -> dict[str, Any] | None:
        """Preflight, read bounded synthetic/server frames, and persist one JSON-RPC message."""
        self._assert_writer_open()
        from memecoin_collector.wss_transport import (
            FrameProtocolError,
            FrameTooLarge,
            ServerFrame,
            WebSocketMessageAssembler,
            encode_client_frame,
            read_server_frame_header,
        )

        if not self.prepare_receive(at_ns=at_ns, free_disk_bytes=free_disk_bytes):
            return None
        assembler = WebSocketMessageAssembler()
        while True:
            observed_ns = _safe_now(clock_ns())
            if self.active is None:
                raise ValueError("wss_campaign_no_active_session")
            if observed_ns - self.active.opened_ns >= MAX_SESSION_NS:
                self.stop_session("session_duration_cap", observed_ns)
                return None
            try:
                header = read_server_frame_header(
                    reader, max_payload_bytes=assembler.remaining_message_bytes
                )
            except FrameTooLarge as exc:
                self.active.reject_transport_header(
                    raw_header=exc.header.raw,
                    declared_payload_bytes=exc.header.payload_length,
                    received_ns=observed_ns,
                )
                self.stop("websocket_payload_cap", observed_ns)
                return None
            except (FrameProtocolError, OSError, EOFError) as exc:
                reason = (
                    "notification_io"
                    if isinstance(exc, (OSError, EOFError))
                    else "notification_invalid"
                )
                self.active.record_transport_failure(
                    phase="notification",
                    reason=reason,
                    raw_evidence=b"",
                    progress_bytes=getattr(reader, "progress_bytes", 0),
                    at_ns=max(observed_ns, self.active.last_event_ns),
                )
                self.stop(
                    "transport_frame_header_failure",
                    max(observed_ns, self.active.last_event_ns),
                )
                raise
            if (
                header.opcode in {0x8, 0x9, 0xA}
                and (
                    sum(item.transport_control_frames for item in self.sessions)
                    >= self.authorization["max_transport_control_frames"]
                    or sum(item.transport_control_bytes for item in self.sessions)
                    + len(header.raw) + header.payload_length + 131
                    > self.authorization["max_transport_control_wire_bytes"]
                )
            ):
                self.active.reject_transport_header(
                    raw_header=header.raw,
                    declared_payload_bytes=header.payload_length,
                    received_ns=observed_ns,
                    reason="transport_control_frame_cap",
                )
                self.stop("transport_control_frame_cap", observed_ns)
                return None
            try:
                payload = reader.read_exactly(header.payload_length)
            except (OSError, EOFError):
                self.active.record_transport_failure(
                    phase="notification",
                    reason="notification_io",
                    raw_evidence=b"",
                    progress_bytes=getattr(reader, "progress_bytes", 0),
                    at_ns=max(observed_ns, self.active.last_event_ns),
                )
                self.stop(
                    "transport_frame_payload_failure",
                    max(observed_ns, self.active.last_event_ns),
                )
                raise
            if len(payload) != header.payload_length:
                self.active.record_transport_failure(
                    phase="notification",
                    reason="notification_io",
                    raw_evidence=b"",
                    progress_bytes=getattr(reader, "progress_bytes", 0),
                    at_ns=max(observed_ns, self.active.last_event_ns),
                )
                self.stop(
                    "transport_frame_payload_truncated",
                    max(observed_ns, self.active.last_event_ns),
                )
                raise ValueError("wss_frame_payload_truncated")
            try:
                message = assembler.accept(ServerFrame(header, payload))
            except (FrameProtocolError, FrameTooLarge):
                self.active.record_transport_failure(
                    phase="notification",
                    reason="notification_invalid",
                    raw_evidence=b"",
                    progress_bytes=getattr(reader, "progress_bytes", 0),
                    at_ns=max(observed_ns, self.active.last_event_ns),
                )
                self.stop(
                    "transport_message_invalid",
                    max(observed_ns, self.active.last_event_ns),
                )
                raise
            control = message if message is not None and message[0] in {"ping", "pong", "close"} else None
            if message is None:
                continue
            kind, body = message
            if kind == "ping":
                if send_client_frame is None:
                    self.active.record_transport_control(
                        control_kind=kind,
                        inbound_wire_bytes=len(header.raw) + len(payload),
                        outbound_wire_bytes=None,
                        at_ns=observed_ns,
                    )
                    self.stop("transport_ping_without_pong_writer", observed_ns)
                    raise ValueError("wss_transport_ping_writer_missing")
                try:
                    pong = encode_client_frame(0xA, body)
                    self.active.record_transport_control(
                        control_kind=kind,
                        inbound_wire_bytes=len(header.raw) + len(payload),
                        outbound_wire_bytes=len(pong),
                        at_ns=observed_ns,
                    )
                    send_client_frame(pong)
                except BaseException:
                    failure_ns = observed_ns
                    if self.active is not None:
                        failure_ns = max(observed_ns, self.active.last_event_ns)
                        self.active.record_transport_failure(
                            phase="notification",
                            reason="notification_io",
                            raw_evidence=b"",
                            progress_bytes=getattr(reader, "progress_bytes", 0),
                            at_ns=failure_ns,
                        )
                    self.stop("transport_pong_write_failure", failure_ns)
                    raise
                continue
            if kind == "pong":
                self.active.record_transport_control(
                    control_kind=kind,
                    inbound_wire_bytes=len(header.raw) + len(payload),
                    outbound_wire_bytes=0,
                    at_ns=observed_ns,
                )
                continue
            if kind == "close":
                self.active.record_transport_control(
                    control_kind=kind,
                    inbound_wire_bytes=len(header.raw) + len(payload),
                    outbound_wire_bytes=0,
                    at_ns=observed_ns,
                )
                self.stop_session("connection_disconnected", observed_ns)
                return None
            received_ns = _safe_now(clock_ns())
            return self.record_message(body, received_ns=received_ns, clock_ns=clock_ns)

    def stop_session(self, reason: str, at_ns: int) -> dict[str, Any]:
        self._assert_writer_open()
        if self.terminal or self.active is None:
            raise ValueError("wss_campaign_no_active_session")
        at_ns = _safe_now(at_ns)
        if reason == "session_duration_cap" and at_ns - self.active.opened_ns < MAX_SESSION_NS:
            raise ValueError("wss_campaign_duration_cap_not_reached")
        row = self.active.stop(reason, at_ns)
        self._write_state(
            f"sessions/{len(self.sessions):02d}/campaign-session-stop.json",
            {
                "session_index": len(self.sessions),
                "session_stop_sha256": _sha(_canonical(row)),
                "at_ns": at_ns,
                "reason": reason,
                "authorization_sha256": self.authorization_sha256,
            },
        )
        self.active = None
        if reason not in RECONNECTABLE_REASONS or len(self.sessions) >= MAX_SESSIONS:
            self.stop("session_window_terminal", at_ns)
        return row

    def stop(self, reason: str, at_ns: int) -> dict[str, Any]:
        self._assert_writer_open()
        if self.terminal:
            raise ValueError("wss_campaign_terminal")
        at_ns = _safe_now(at_ns)
        if self.active is not None:
            if not self.active.terminal:
                self.active.stop(reason, at_ns)
            self.active = None
        row = {
            "status": "stopped",
            "reason": reason,
            "stopped_ns": at_ns,
            "authorization_sha256": self.authorization_sha256,
            "session_count": len(self.sessions),
            "raw_message_bytes": sum(item.raw_bytes for item in self.sessions),
            "billable_message_count": sum(item.billable_message_count for item in self.sessions),
            "billable_uncompressed_bytes": sum(item.billable_uncompressed_bytes for item in self.sessions),
            "transport_control_frames": sum(item.transport_control_frames for item in self.sessions),
            "transport_control_wire_bytes": sum(item.transport_control_bytes for item in self.sessions),
            "stored_bytes_before_stop_record": self._stored_bytes(),
            "no_resume": True,
            "no_backfill_or_http_retrieval": True,
        }
        row = self._write_state("CAMPAIGN_STOP.json", row)
        self.terminal = True
        return row

    @staticmethod
    def _last_stop(session: WssReferenceSession) -> dict[str, Any]:
        path = sorted((session.root / "journal").glob("*.json"))[-1]
        row = json.loads(path.read_text())
        if row.get("kind") != "session_stop":
            raise ValueError("wss_campaign_previous_session_unsealed")
        return row

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if getattr(self, "lock_fd", None) is not None:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            self.lock_fd = None

    def __enter__(self) -> WssReferenceCampaign:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @classmethod
    def recover_interrupted(cls, root: Path, *, at_ns: int) -> dict[str, Any]:
        """Validate sessions and seal the campaign; interrupted runs never resume."""
        lock_path = root / ".writer.lock"
        if not lock_path.is_file() or lock_path.is_symlink():
            raise ValueError("wss_campaign_writer_lock_missing")
        if stat.S_IMODE(lock_path.stat().st_mode) != 0o600:
            raise ValueError("wss_campaign_writer_lock_mode_invalid")
        fd = os.open(lock_path, os.O_RDONLY)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("wss_campaign_writer_active") from exc
            return cls._recover_interrupted_locked(root, at_ns=at_ns)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @classmethod
    def _recover_interrupted_locked(cls, root: Path, *, at_ns: int) -> dict[str, Any]:
        at_ns = _safe_now(at_ns)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("wss_campaign_root_invalid")
        if stat.S_IMODE(root.stat().st_mode) != 0o700:
            raise ValueError("wss_campaign_root_mode_invalid")
        expected = {"authorization.json", "campaign_open.json", ".writer.lock", "sessions"}
        names = {path.name for path in root.iterdir()}
        if not names.issubset(expected | {"CAMPAIGN_STOP.json"}) or not expected.issubset(names):
            raise ValueError("wss_campaign_tree_invalid")
        for name in names - {"sessions"}:
            path = root / name
            if (
                path.is_symlink()
                or not path.is_file()
                or stat.S_IMODE(path.stat().st_mode) != 0o600
            ):
                raise ValueError("wss_campaign_file_mode_invalid")
        authorization = json.loads((root / "authorization.json").read_text())
        _validate_authorization(authorization)
        auth_sha = _sha(_canonical(authorization))
        opened = cls._verify_seal(
            json.loads((root / "campaign_open.json").read_text()),
            "wss_campaign_open_hash_mismatch",
        )
        if opened.get("authorization_sha256") != auth_sha:
            raise ValueError("wss_campaign_authorization_hash_mismatch")
        sessions_dir = root / "sessions"
        if sessions_dir.is_symlink() or not sessions_dir.is_dir():
            raise ValueError("wss_campaign_sessions_tree_invalid")
        if stat.S_IMODE(sessions_dir.stat().st_mode) != 0o700:
            raise ValueError("wss_campaign_sessions_mode_invalid")
        paths = sorted(sessions_dir.iterdir())
        if len(paths) > MAX_SESSIONS or any(
            path.is_symlink() or not path.is_dir() or path.name != f"{i:02d}"
            for i, path in enumerate(paths, 1)
        ):
            raise ValueError("wss_campaign_session_set_invalid")
        if any(stat.S_IMODE(path.stat().st_mode) != 0o700 for path in paths):
            raise ValueError("wss_campaign_session_mode_invalid")
        session_stops = []
        for index, session_root in enumerate(paths, 1):
            start_path = session_root / "campaign-start.json"
            if not start_path.is_file() or start_path.is_symlink():
                raise ValueError("wss_campaign_session_start_missing")
            start = cls._verify_seal(
                json.loads(start_path.read_text()), "wss_campaign_session_start_hash_mismatch"
            )
            session = WssReferenceSession.recover(session_root, at_ns=at_ns)
            if (
                start.get("session_index") != index
                or start.get("authorization_sha256") != auth_sha
                or start.get("session_binding_sha256") != _sha(_canonical(default_binding()))
                or start.get("opened_monotonic_ns") != session.opened_monotonic_ns
            ):
                raise ValueError("wss_campaign_session_binding_mismatch")
            stop = WssReferenceCampaign._last_stop(session)
            session_stop_path = session_root / "campaign-session-stop.json"
            if session_stop_path.exists():
                if stat.S_IMODE(session_stop_path.stat().st_mode) != 0o600:
                    raise ValueError("wss_campaign_session_stop_mode_invalid")
                sidecar = cls._verify_seal(
                    json.loads(session_stop_path.read_text()),
                    "wss_campaign_session_stop_hash_mismatch",
                )
                if (
                    sidecar.get("session_stop_sha256") != _sha(_canonical(stop))
                    or sidecar.get("at_ns") != stop.get("at_ns")
                    or sidecar.get("reason") != stop.get("reason")
                    or sidecar.get("authorization_sha256") != auth_sha
                ):
                    raise ValueError("wss_campaign_session_stop_binding_mismatch")
            session_stops.append({"session_index": index, "stop": stop})
        stop_path = root / "CAMPAIGN_STOP.json"
        if stop_path.exists():
            existing = cls._verify_seal(
                json.loads(stop_path.read_text()),
                "wss_campaign_stop_hash_mismatch",
            )
            if existing.get("authorization_sha256") != auth_sha:
                raise ValueError("wss_campaign_stop_binding_mismatch")
            raw_total = sum(row["stop"].get("raw_bytes", 0) for row in session_stops)
            billing_totals = {
                "billable_message_count": sum(row["stop"].get("billable_message_count", 0) for row in session_stops),
                "billable_uncompressed_bytes": sum(row["stop"].get("billable_uncompressed_bytes", 0) for row in session_stops),
                "transport_control_frames": sum(row["stop"].get("transport_control_frames", 0) for row in session_stops),
                "transport_control_wire_bytes": sum(row["stop"].get("transport_control_wire_bytes", 0) for row in session_stops),
            }
            stored_before_stop = (
                sum(
                    path.stat().st_size
                    for path in root.rglob("*")
                    if path.is_file() and not path.is_symlink()
                )
                - stop_path.stat().st_size
            )
            if (
                existing.get("session_count") != len(paths)
                or existing.get("raw_message_bytes") != raw_total
                or existing.get("stored_bytes_before_stop_record") != stored_before_stop
                or any(existing.get(key) != value for key, value in billing_totals.items())
            ):
                raise ValueError("wss_campaign_stop_accounting_mismatch")
            return existing
        raw_total = sum(row["stop"].get("raw_bytes", 0) for row in session_stops)
        billing_totals = {
            "billable_message_count": sum(row["stop"].get("billable_message_count", 0) for row in session_stops),
            "billable_uncompressed_bytes": sum(row["stop"].get("billable_uncompressed_bytes", 0) for row in session_stops),
            "transport_control_frames": sum(row["stop"].get("transport_control_frames", 0) for row in session_stops),
            "transport_control_wire_bytes": sum(row["stop"].get("transport_control_wire_bytes", 0) for row in session_stops),
        }
        if (
            billing_totals["billable_message_count"] > authorization["max_billable_messages"]
            or billing_totals["billable_uncompressed_bytes"] > authorization["max_billable_uncompressed_bytes"]
            or billing_totals["transport_control_frames"] > authorization["max_transport_control_frames"]
            or billing_totals["transport_control_wire_bytes"] > authorization["max_transport_control_wire_bytes"]
        ):
            raise ValueError("wss_campaign_recovered_billable_cap_exceeded")
        stored_total = sum(
            path.stat().st_size
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        if raw_total > RAW_LIMIT_BYTES or stored_total > STORED_LIMIT_BYTES:
            raise ValueError("wss_campaign_recovered_cap_exceeded")
        row = {
            "status": "stopped",
            "reason": "process_interrupted_no_resume",
            "stopped_ns": at_ns,
            "authorization_sha256": auth_sha,
            "session_count": len(paths),
            "raw_message_bytes": raw_total,
            **billing_totals,
            "stored_bytes_before_stop_record": stored_total,
            "session_stops": session_stops,
            "no_resume": True,
            "no_backfill_or_http_retrieval": True,
        }
        sealed = dict(row, sha256=_sha(_canonical(row)))
        _write_new(root / "CAMPAIGN_STOP.json", _canonical(sealed))
        return sealed
