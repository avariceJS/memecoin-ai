"""One-shot, bounded DNS and TCP establishment for the offline WSS pipeline.

The resolver is a fixed local Python file. Only its public hostname argument is
passed to the child; endpoint paths, query strings, and credentials stay in the
owning process and never enter child argv or environment.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import os
import select
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from memecoin_collector.wss_reference import DISK_FLOOR_BYTES, MAX_SESSION_NS

DNS_TIMEOUT_NS = 5_000_000_000
CONNECT_TIMEOUT_NS = 5_000_000_000
MAX_RESOLVER_OUTPUT = 16 * 1024
MAX_DNS_ANSWERS = 64
PORT = 443
ALLOWED_HOSTS = {"mainnet.helius-rpc.com"}
RESOLVER = Path(__file__).resolve().parents[2] / "tools" / "brain_senses_wss_resolver.py"
SAFE_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC"}
_IN_PROGRESS = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR}


class WssSocketError(OSError):
    """Sanitized local connection result."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Address:
    family: int
    ip: str
    scope_id: int = 0


@dataclass(frozen=True)
class ResolverResult:
    addresses: tuple[Address, ...]
    stdout_bytes: int
    answer_count: int


def _validate_host(host: str) -> str:
    if (
        not isinstance(host, str)
        or not host
        or len(host) > 253
        or not host.isascii()
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-."
            for c in host
        )
        or host.startswith(".")
        or host.endswith(".")
        or ".." in host
        or host.lower() not in ALLOWED_HOSTS
    ):
        raise WssSocketError("dns_hostname_invalid")
    return host.lower()


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    """Bound child cleanup; inability to confirm reaping is terminal."""
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1.0)
    except BaseException as first_error:
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=1.0)
        except BaseException:
            if not isinstance(first_error, Exception):
                raise first_error from None
            raise WssSocketError("dns_resolver_cleanup_failed") from None
        if not isinstance(first_error, Exception):
            raise first_error from None


def _close_resolver_stdout(process: subprocess.Popen[bytes]) -> None:
    if process.stdout is not None:
        try:
            process.stdout.close()
        except OSError:
            pass


def _parse_resolver_output(raw: bytes) -> ResolverResult:
    if not raw or len(raw) > MAX_RESOLVER_OUTPUT:
        raise WssSocketError("dns_resolver_output_invalid")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise WssSocketError("dns_resolver_output_invalid") from None
    if not isinstance(value, dict) or set(value) != {"status", "addresses"}:
        raise WssSocketError("dns_resolver_output_invalid")
    if value["status"] != "ok" or not isinstance(value["addresses"], list):
        raise WssSocketError("dns_resolver_failed")
    if len(value["addresses"]) > MAX_DNS_ANSWERS:
        raise WssSocketError("dns_resolver_answer_cap")
    addresses: list[Address] = []
    for row in value["addresses"]:
        if not isinstance(row, dict) or set(row) != {"family", "ip", "scope_id"}:
            raise WssSocketError("dns_resolver_output_invalid")
        family, ip, scope_id = row["family"], row["ip"], row["scope_id"]
        if type(family) is not int:
            raise WssSocketError("dns_resolver_output_invalid")
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        if (
            not isinstance(ip, str)
            or len(ip) > 64
            or "%" in ip
            or type(scope_id) is not int
            or not 0 <= scope_id <= 2**32 - 1
        ):
            raise WssSocketError("dns_resolver_output_invalid")
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            continue
        expected_version = 4 if family == socket.AF_INET else 6
        if (
            parsed.version == expected_version
            and parsed.is_global
            and not parsed.is_multicast
            and not parsed.is_reserved
            and not (parsed.version == 6 and parsed.is_site_local)
            and scope_id == 0
        ):
            addresses.append(Address(family=family, ip=parsed.compressed, scope_id=scope_id))
    return ResolverResult(tuple(addresses), len(raw), len(value["addresses"]))


def resolve_once(
    host: str,
    *,
    timeout_ns: int = DNS_TIMEOUT_NS,
    resolver_script: Path = RESOLVER,
    executable: str = sys.executable,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> ResolverResult:
    """Resolve once in a scrubbed fixed-code child with capped stdout and hard timeout."""
    host = _validate_host(host)
    if type(timeout_ns) is not int or not 0 < timeout_ns <= DNS_TIMEOUT_NS:
        raise WssSocketError("dns_timeout_invalid")
    process = None
    output = bytearray()
    started_mono = monotonic_ns()
    if type(started_mono) is not int or started_mono < 0:
        raise WssSocketError("dns_monotonic_clock_invalid")
    last_mono = started_mono
    deadline = started_mono + timeout_ns

    def remaining_ns() -> int:
        nonlocal last_mono
        now = monotonic_ns()
        if type(now) is not int or now < last_mono:
            raise WssSocketError("dns_monotonic_clock_invalid")
        last_mono = now
        return deadline - now

    try:
        process = popen(
            [executable, "-I", str(resolver_script), host],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(SAFE_ENV),
            close_fds=True,
            shell=False,
            bufsize=0,
        )
        if process.stdout is None:
            raise WssSocketError("dns_resolver_io_failed")
        fd = process.stdout.fileno()
        os.set_blocking(fd, False)
        while True:
            remaining = remaining_ns()
            if remaining <= 0:
                raise WssSocketError("dns_resolution_timeout")
            readable, _, _ = select.select([fd], [], [], remaining / 1_000_000_000)
            if not readable:
                continue
            chunk = os.read(fd, min(4096, MAX_RESOLVER_OUTPUT + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > MAX_RESOLVER_OUTPUT:
                raise WssSocketError("dns_resolver_output_cap")
        remaining = remaining_ns()
        if remaining <= 0:
            raise WssSocketError("dns_resolution_timeout")
        try:
            code = process.wait(timeout=remaining / 1_000_000_000)
        except subprocess.TimeoutExpired:
            raise WssSocketError("dns_resolution_timeout") from None
        if code != 0:
            raise WssSocketError("dns_resolver_failed")
        return _parse_resolver_output(bytes(output))
    except WssSocketError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        raise WssSocketError("dns_resolver_io_failed") from None
    finally:
        if process is not None:
            try:
                _kill_and_reap(process)
            finally:
                _close_resolver_stdout(process)


def _sockaddr(address: Address) -> tuple:
    if address.family == socket.AF_INET:
        return address.ip, PORT
    return address.ip, PORT, 0, address.scope_id


def _valid_public_address(address: object) -> bool:
    if (
        not isinstance(address, Address)
        or not isinstance(address.family, int)
        or isinstance(address.family, bool)
        or address.family not in {socket.AF_INET, socket.AF_INET6}
        or not isinstance(address.ip, str)
        or type(address.scope_id) is not int
        or address.scope_id != 0
        or "%" in address.ip
    ):
        return False
    try:
        parsed = ipaddress.ip_address(address.ip)
    except ValueError:
        return False
    return (
        parsed.version == (4 if address.family == socket.AF_INET else 6)
        and parsed.is_global
        and not parsed.is_multicast
        and not parsed.is_reserved
        and not (parsed.version == 6 and parsed.is_site_local)
        and parsed.compressed == address.ip
    )


def connect_once(
    address: Address,
    *,
    timeout_ns: int = CONNECT_TIMEOUT_NS,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    socket_factory: Callable[..., socket.socket] = socket.socket,
) -> socket.socket:
    """Make one nonblocking connect to the first selected address, without fallback."""
    if (
        not isinstance(address, Address)
        or address.family not in {socket.AF_INET, socket.AF_INET6}
        or type(timeout_ns) is not int
        or not 0 < timeout_ns <= CONNECT_TIMEOUT_NS
    ):
        raise WssSocketError("tcp_connect_input_invalid")
    started_mono = monotonic_ns()
    if type(started_mono) is not int or started_mono < 0:
        raise WssSocketError("tcp_monotonic_clock_invalid")
    last_mono = started_mono
    deadline = started_mono + timeout_ns

    def ensure_time() -> None:
        nonlocal last_mono
        now = monotonic_ns()
        if type(now) is not int or now < last_mono:
            raise WssSocketError("tcp_monotonic_clock_invalid")
        last_mono = now
        if deadline - now <= 0:
            raise WssSocketError("tcp_connect_timeout")

    try:
        if not _valid_public_address(address):
            raise ValueError
        sock = socket_factory(address.family, socket.SOCK_STREAM)
        ensure_time()
        sock.setblocking(False)
        result = sock.connect_ex(_sockaddr(address))
        ensure_time()
        if result not in {0, errno.EISCONN}:
            if result not in _IN_PROGRESS:
                raise WssSocketError("tcp_connect_failed")
            while True:
                ensure_time()
                remaining_ns = deadline - last_mono
                _, writable, exceptional = select.select(
                    [], [sock], [sock], remaining_ns / 1_000_000_000
                )
                if not writable and not exceptional:
                    continue
                error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error:
                    raise WssSocketError("tcp_connect_failed")
                break
        ensure_time()
        return sock
    except BaseException as exc:
        if "sock" in locals():
            try:
                sock.close()
            except BaseException:
                pass
        if isinstance(exc, WssSocketError):
            raise
        if not isinstance(exc, Exception):
            raise
        raise WssSocketError("tcp_connect_failed") from None


def establish_one(
    campaign,
    *,
    host: str,
    clock_ns: Callable[[], int] = time.time_ns,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    resolver: Callable[..., ResolverResult] = resolve_once,
    connector: Callable[..., socket.socket] = connect_once,
    free_disk_bytes: Callable[[], int],
) -> socket.socket:
    """Resolve once, connect once, journal outcomes, and seal failures as explicit gaps."""
    session = campaign.active
    if campaign.terminal or session is None:
        raise WssSocketError("campaign_session_missing")
    host = _validate_host(host)

    def seal_ambiguous() -> None:
        if campaign.active is session and not campaign.terminal:
            try:
                now = clock_ns()
            except BaseException:
                now = session.last_event_ns
            at_ns = max(now, session.last_event_ns) if type(now) is int else session.last_event_ns
            try:
                campaign.stop("client_failure", at_ns)
            except BaseException:
                pass

    def event_time() -> int:
        if campaign.active is not session or campaign.terminal or session.terminal:
            raise WssSocketError("campaign_session_stale")
        campaign._assert_writer_open()
        now = clock_ns()
        if type(now) is not int or now < session.last_event_ns:
            campaign.stop("session_clock_regression", session.last_event_ns)
            raise WssSocketError("session_clock_regression")
        return now

    def elapsed_ns(started: int) -> tuple[int, int]:
        finished = monotonic_ns()
        if type(finished) is not int or finished < started:
            campaign.stop("client_deadline", session.last_event_ns)
            raise WssSocketError("monotonic_clock_regression")
        return finished, finished - started

    def preflight(*, stage: str) -> tuple[int, int, int]:
        if campaign.active is not session or campaign.terminal or session.terminal:
            raise WssSocketError("campaign_session_stale")
        campaign._assert_writer_open()
        now_wall = clock_ns()
        now_mono = monotonic_ns()
        if type(now_wall) is not int or type(now_mono) is not int or now_mono < 0:
            campaign.stop("client_deadline", session.last_event_ns)
            raise WssSocketError("session_deadline_invalid")
        if now_mono < session.opened_monotonic_ns:
            campaign.stop("client_deadline", max(now_wall, session.last_event_ns))
            raise WssSocketError("session_deadline_invalid")
        if now_wall < session.last_event_ns:
            campaign.stop("session_clock_regression", session.last_event_ns)
            raise WssSocketError("session_clock_regression")
        if stage == "dns":
            attempt_state_invalid = (
                session.dns_recorded or session.connect_recorded or session.connected
            )
        elif stage == "connect":
            attempt_state_invalid = (
                not session.dns_recorded
                or session.dns_selected_family is None
                or session.dns_selected_address is None
                or session.connect_recorded
                or session.connected
            )
        elif stage == "connected":
            attempt_state_invalid = (
                not session.dns_recorded
                or session.connect_outcome != "connected"
                or not session.connect_recorded
                or session.connected
            )
        else:
            attempt_state_invalid = True
        if attempt_state_invalid:
            campaign.stop("client_failure", max(now_wall, session.last_event_ns))
            raise WssSocketError("connection_attempt_state_invalid")
        if (
            not campaign.authorization["authorized_at_ns"]
            <= now_wall
            < campaign.authorization["expires_at_ns"]
        ):
            campaign.stop("authorization_expired", max(now_wall, session.last_event_ns))
            raise WssSocketError("authorization_expired")
        wall_elapsed = now_wall - session.opened_ns
        if wall_elapsed >= MAX_SESSION_NS:
            campaign.stop("session_duration_cap", now_wall)
            raise WssSocketError("session_duration_cap")
        session_remaining = min(
            MAX_SESSION_NS - (now_mono - session.opened_monotonic_ns),
            MAX_SESSION_NS - wall_elapsed,
        )
        authorization_remaining = campaign.authorization["expires_at_ns"] - now_wall
        if session_remaining <= 0:
            campaign.stop("session_duration_cap", now_wall)
            raise WssSocketError("session_duration_cap")
        try:
            free = free_disk_bytes()
        except Exception:
            campaign.stop("client_failure", now_wall)
            raise WssSocketError("disk_measurement_failed") from None
        if type(free) is not int or free < DISK_FLOOR_BYTES:
            campaign.stop("disk_floor", now_wall)
            raise WssSocketError("disk_floor")
        timeout = min(DNS_TIMEOUT_NS, session_remaining, authorization_remaining)
        if timeout <= 0:
            campaign.stop("authorization_expired", now_wall)
            raise WssSocketError("authorization_expired")
        return now_wall, now_mono, timeout

    start_wall, start_mono, dns_timeout = preflight(stage="dns")
    try:
        result = resolver(host, timeout_ns=dns_timeout, monotonic_ns=monotonic_ns)
    except WssSocketError as exc:
        try:
            finished_wall = event_time()
            _, duration = elapsed_ns(start_mono)
            timeout = exc.reason == "dns_resolution_timeout"
            session.record_dns_result(
                started_ns=start_wall,
                finished_ns=finished_wall,
                duration_ns=duration,
                address_count=0,
                selected_family=None,
                selected_address=None,
                outcome="resolver_timeout" if timeout else "resolver_failed",
            )
        except BaseException as bookkeeping_error:
            seal_ambiguous()
            if not isinstance(bookkeeping_error, Exception):
                raise bookkeeping_error from None
            raise WssSocketError("dns_result_journal_failed") from None
        if exc.reason in {"dns_resolver_cleanup_failed", "dns_monotonic_clock_invalid"}:
            campaign.stop("client_failure", finished_wall)
        else:
            campaign.stop_session(
                "dns_resolution_timeout" if timeout else "dns_resolution_failed", finished_wall
            )
        raise WssSocketError(exc.reason) from None
    except BaseException as exc:
        try:
            finished_wall = event_time()
            _, duration = elapsed_ns(start_mono)
            session.record_dns_result(
                started_ns=start_wall,
                finished_ns=finished_wall,
                duration_ns=duration,
                address_count=0,
                selected_family=None,
                selected_address=None,
                outcome="resolver_failed",
            )
        except BaseException as bookkeeping_error:
            seal_ambiguous()
            if not isinstance(bookkeeping_error, Exception):
                raise bookkeeping_error from None
            raise WssSocketError("dns_result_journal_failed") from None
        else:
            campaign.stop("client_failure", finished_wall)
        if not isinstance(exc, Exception):
            raise
        raise WssSocketError("dns_resolver_io_failed") from None
    try:
        finished_mono, dns_elapsed = elapsed_ns(start_mono)
        if dns_elapsed >= dns_timeout:
            session.record_dns_result(
                started_ns=start_wall,
                finished_ns=event_time(),
                duration_ns=dns_elapsed,
                address_count=0,
                selected_family=None,
                selected_address=None,
                outcome="resolver_timeout",
            )
            campaign.stop_session("dns_resolution_timeout", event_time())
            raise WssSocketError("dns_resolution_timeout")
        finished_wall = event_time()
        if (
            not isinstance(result, ResolverResult)
            or type(result.addresses) is not tuple
            or type(result.answer_count) is not int
            or not 0 <= result.answer_count <= MAX_DNS_ANSWERS
            or type(result.stdout_bytes) is not int
            or not 0 < result.stdout_bytes <= MAX_RESOLVER_OUTPUT
            or len(result.addresses) > result.answer_count
            or any(not _valid_public_address(address) for address in result.addresses)
        ):
            session.record_dns_result(
                started_ns=start_wall,
                finished_ns=finished_wall,
                duration_ns=dns_elapsed,
                address_count=0,
                selected_family=None,
                selected_address=None,
                outcome="resolver_failed",
            )
            campaign.stop_session("dns_resolution_failed", finished_wall)
            raise WssSocketError("dns_resolver_result_invalid")
        selected = result.addresses[0] if result.addresses else None
        session.record_dns_result(
            started_ns=start_wall,
            finished_ns=finished_wall,
            duration_ns=dns_elapsed,
            address_count=result.answer_count,
            selected_family=None if selected is None else selected.family,
            selected_address=None if selected is None else selected.ip,
            outcome="resolved" if selected is not None else "no_usable_address",
        )
        if selected is None:
            campaign.stop_session("dns_resolution_failed", finished_wall)
            raise WssSocketError("dns_no_usable_address")
    except BaseException as exc:
        seal_ambiguous()
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, WssSocketError):
            raise
        raise WssSocketError("dns_result_journal_failed") from None

    start_wall, start_mono, connect_timeout = preflight(stage="connect")
    try:
        sock = connector(selected, timeout_ns=connect_timeout, monotonic_ns=monotonic_ns)
    except WssSocketError as exc:
        try:
            finished_wall = event_time()
            _, duration = elapsed_ns(start_mono)
            timeout = exc.reason == "tcp_connect_timeout"
            session.record_connect_result(
                started_ns=start_wall,
                finished_ns=finished_wall,
                duration_ns=duration,
                selected_family=selected.family,
                selected_address=selected.ip,
                outcome="connect_timeout" if timeout else "connect_failed",
            )
            if exc.reason == "tcp_monotonic_clock_invalid":
                campaign.stop("client_failure", finished_wall)
            else:
                campaign.stop_session(
                    "tcp_connect_timeout" if timeout else "tcp_connect_failed", finished_wall
                )
        except BaseException as bookkeeping_error:
            seal_ambiguous()
            if not isinstance(bookkeeping_error, Exception):
                raise bookkeeping_error from None
            if isinstance(bookkeeping_error, WssSocketError):
                raise
            raise WssSocketError("tcp_result_journal_failed") from None
        raise WssSocketError(exc.reason) from None
    except BaseException as exc:
        seal_ambiguous()
        if not isinstance(exc, Exception):
            raise
        raise WssSocketError("tcp_connect_failed") from None
    try:
        finished_mono, connect_elapsed = elapsed_ns(start_mono)
        if connect_elapsed >= connect_timeout:
            try:
                session.record_connect_result(
                    started_ns=start_wall,
                    finished_ns=event_time(),
                    duration_ns=connect_elapsed,
                    selected_family=selected.family,
                    selected_address=selected.ip,
                    outcome="connect_timeout",
                )
            except BaseException:
                seal_ambiguous()
                raise WssSocketError("tcp_result_journal_failed") from None
            campaign.stop_session("tcp_connect_timeout", event_time())
            raise WssSocketError("tcp_connect_timeout")
        finished_wall = event_time()
        try:
            session.record_connect_result(
                started_ns=start_wall,
                finished_ns=finished_wall,
                duration_ns=connect_elapsed,
                selected_family=selected.family,
                selected_address=selected.ip,
                outcome="connected",
            )
        except BaseException:
            if campaign.active is session and not campaign.terminal:
                campaign.stop("client_failure", session.last_event_ns)
            raise WssSocketError("tcp_connect_journal_failed") from None
        preflight(stage="connected")
        return sock
    except BaseException as exc:
        try:
            sock.close()
        except BaseException:
            pass
        seal_ambiguous()
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, WssSocketError):
            raise
        raise WssSocketError("tcp_post_connect_failed") from None
