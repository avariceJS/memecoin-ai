from __future__ import annotations

import os
import shutil
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

import pytest

from memecoin_collector import private_config, wss_tls
from memecoin_collector.wss_tls import (
    TLS_HANDSHAKE_FAILURE_REASON,
    TLS_HANDSHAKE_TIMEOUT_REASON,
    TLS_KEYLOG_DISABLED_REASON,
    TLSVerification,
    VerifiedTLSStream,
    WssTLSFailure,
    load_private_endpoint,
)


def test_private_endpoint_loader_is_fd_checked_and_redacted(tmp_path, monkeypatch):
    config = tmp_path / ".env.local"
    config.write_text(
        'OTHER=x\nMEMECOIN_HELIUS_RPC_URL="https://mainnet.helius-rpc.com/?api-key=test-secret"\n'
    )
    config.chmod(0o600)
    monkeypatch.setattr(wss_tls, "PRIVATE_CONFIG_PATH", config)

    endpoint = load_private_endpoint()

    assert endpoint.host == "mainnet.helius-rpc.com"
    assert endpoint.authority == "mainnet.helius-rpc.com"
    assert endpoint.request_target == "/?api-key=test-secret"
    assert "test-secret" not in repr(endpoint)
    assert "test-secret" not in str(endpoint)


def test_tls_verification_flags_cannot_be_forged_without_internal_capability():
    with pytest.raises(ValueError, match="tls_verification_not_handshake_bound"):
        TLSVerification("helius.test", True, True, object())


@pytest.mark.parametrize(
    "value",
    [
        "http://mainnet.helius-rpc.com/?api-key=fake",
        "https://user@mainnet.helius-rpc.com/?api-key=fake",
        "https://mainnet.helius-rpc.com:444/?api-key=fake",
        "https://evil.example/?api-key=fake",
        "https://mainnet.helius-rpc.com/?api-key=fake#fragment",
    ],
)
def test_private_endpoint_loader_rejects_unsafe_endpoint_shapes(tmp_path, monkeypatch, value):
    config = tmp_path / ".env.local"
    config.write_text(f"MEMECOIN_HELIUS_RPC_URL={value}\n")
    config.chmod(0o600)
    monkeypatch.setattr(wss_tls, "PRIVATE_CONFIG_PATH", config)
    with pytest.raises(ValueError, match="private_endpoint_url_invalid"):
        load_private_endpoint()


def test_private_endpoint_loader_rejects_non_private_file(tmp_path, monkeypatch):
    config = tmp_path / ".env.local"
    config.write_text("MEMECOIN_HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=fake\n")
    config.chmod(0o644)
    monkeypatch.setattr(wss_tls, "PRIVATE_CONFIG_PATH", config)
    with pytest.raises(ValueError, match="private_endpoint_config_permissions_invalid"):
        load_private_endpoint()


def test_private_endpoint_loader_rejects_fifo_without_blocking(tmp_path, monkeypatch):
    config = tmp_path / ".env.local"
    os.mkfifo(config)
    monkeypatch.setattr(wss_tls, "PRIVATE_CONFIG_PATH", config)

    with pytest.raises(ValueError, match="private_endpoint_config_permissions_invalid"):
        load_private_endpoint()


def test_private_endpoint_loader_rejects_hardlink_and_oversized_config(tmp_path, monkeypatch):
    config = tmp_path / ".env.local"
    config.write_text("MEMECOIN_HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?key=x\n")
    config.chmod(0o600)
    alias = tmp_path / "alias"
    os.link(config, alias)
    monkeypatch.setattr(wss_tls, "PRIVATE_CONFIG_PATH", config)
    with pytest.raises(ValueError, match="private_endpoint_config_permissions_invalid"):
        load_private_endpoint()

    alias.unlink()
    config.write_bytes(b"x" * (wss_tls.MAX_CONFIG_BYTES + 1))
    config.chmod(0o600)
    with pytest.raises(ValueError, match="private_endpoint_config_too_large"):
        load_private_endpoint()


def test_private_endpoint_loader_rejects_symlink(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_text("MEMECOIN_HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?key=x\n")
    target.chmod(0o600)
    config = tmp_path / ".env.local"
    config.symlink_to(target)
    monkeypatch.setattr(wss_tls, "PRIVATE_CONFIG_PATH", config)
    with pytest.raises(ValueError, match="private_endpoint_config_unavailable"):
        load_private_endpoint()


def test_private_endpoint_loader_rejects_file_changed_during_read(tmp_path, monkeypatch):
    config = tmp_path / ".env.local"
    config.write_text("MEMECOIN_HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=fake\n")
    config.chmod(0o600)
    monkeypatch.setattr(wss_tls, "PRIVATE_CONFIG_PATH", config)
    with pytest.raises(ValueError, match="private_endpoint_config_changed_during_read"):
        original_read = wss_tls.read_private_config

        def change_during_read(path, *, max_bytes):
            original_os_read = wss_tls.os.read
            appended = False

            def append_before_read(fd, size):
                nonlocal appended
                if not appended:
                    appended = True
                    with config.open("ab") as stream:
                        stream.write(b"# changed\n")
                return original_os_read(fd, size)

            monkeypatch.setattr(wss_tls.os, "read", append_before_read)
            return original_read(path, max_bytes=max_bytes)

        monkeypatch.setattr(wss_tls, "read_private_config", change_during_read)
        load_private_endpoint()


@pytest.mark.parametrize("failing_operation", ["fstat", "read", "close"])
def test_private_file_io_errors_are_sanitized(tmp_path, monkeypatch, failing_operation):
    config = tmp_path / "private.env"
    config.write_text("private fixture bytes")
    config.chmod(0o600)
    original = getattr(private_config.os, failing_operation)

    def fail_with_secret(*args, **kwargs):
        if failing_operation == "close":
            original(*args, **kwargs)
        raise OSError("fixture-secret-must-not-escape")

    monkeypatch.setattr(private_config.os, failing_operation, fail_with_secret)
    reason = (
        "private_config_close_failed"
        if failing_operation == "close"
        else "private_config_io_failed"
    )
    with pytest.raises(private_config.PrivateConfigError, match=reason) as caught:
        private_config.read_private_config(config)
    assert "fixture-secret" not in str(caught.value)
    assert "fixture-secret" not in repr(caught.value)


@pytest.fixture
def local_tls_certificate(tmp_path):
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl executable required for local TLS engine fixture")
    cert = tmp_path / "server-cert.pem"
    key = tmp_path / "server-key.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=helius.test",
            "-addext",
            "subjectAltName=DNS:helius.test",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
        ],
        check=True,
        capture_output=True,
    )
    key.chmod(0o600)
    return cert, key


def _server_context(cert: Path, key: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert, keyfile=key)
    return context


class _ScriptedSocket:
    def __init__(self, *, recv_steps=(), send_steps=()):
        self.recv_steps = list(recv_steps)
        self.send_steps = list(send_steps)
        self.sent = bytearray()
        self.closed = False

    def recv(self, size):
        step = self.recv_steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def send(self, data):
        step = self.send_steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        count = min(step, len(data))
        self.sent.extend(data[:count])
        return count

    def close(self):
        self.closed = True


def _scripted_stream(sock):
    return VerifiedTLSStream(
        sock,
        expected_hostname="helius.test",
        token=wss_tls._VERIFIED_STREAM_TOKEN,
    )


@pytest.mark.parametrize(
    "direction, first, second, want_read",
    [
        ("read", ssl.SSLWantReadError(), b"x", True),
        ("read", ssl.SSLWantWriteError(), b"x", False),
        ("write", ssl.SSLWantReadError(), 1, True),
        ("write", ssl.SSLWantWriteError(), 1, False),
    ],
)
def test_tls_want_read_and_want_write_are_supported_in_both_directions(
    monkeypatch, direction, first, second, want_read
):
    fake = _ScriptedSocket(
        recv_steps=[first, second] if direction == "read" else (),
        send_steps=[first, second] if direction == "write" else (),
    )
    selected = []

    def ready(readers, writers, errors, timeout):
        selected.append((bool(readers), bool(writers), timeout))
        return (readers if want_read else [], writers if not want_read else [], [])

    monkeypatch.setattr(wss_tls, "select", ready)
    stream = _scripted_stream(fake)
    deadline = time.monotonic_ns() + 5_000_000_000
    if direction == "read":
        assert stream.read_exactly(1, deadline_monotonic_ns=deadline) == b"x"
    else:
        stream.write_all(b"x", deadline_monotonic_ns=deadline)
        assert fake.sent == b"x"
    assert selected and selected[0][0] is want_read
    stream.close()


def test_tls_partial_write_failure_reports_outbound_progress_only(monkeypatch):
    fake = _ScriptedSocket(send_steps=[2, OSError("write broke")])
    monkeypatch.setattr(wss_tls, "select", lambda *args: ([], [], []))
    stream = _scripted_stream(fake)
    with pytest.raises(WssTLSFailure) as caught:
        stream.write_all(b"abcd", deadline_monotonic_ns=time.monotonic_ns() + 5_000_000_000)
    assert str(caught.value) == "tls_write_failed"
    assert caught.value.bytes_completed == 2
    stream.close()


def test_verified_tls_stream_uses_verified_hostname_and_exact_deadline_io(
    local_tls_certificate, monkeypatch
):
    cert, key = local_tls_certificate
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    server_context = _server_context(cert, key)
    client_sock, server_sock = socket.socketpair()
    server_result = []

    def serve():
        with server_context.wrap_socket(server_sock, server_side=True) as tls:
            received = tls.recv(5)
            server_result.append(received)
            tls.sendall(b"world")

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    deadline = time.monotonic_ns() + 5_000_000_000
    stream = VerifiedTLSStream.handshake(
        client_sock,
        expected_hostname="helius.test",
        deadline_monotonic_ns=deadline,
    )
    assert stream.tls_verification.peer_hostname == "helius.test"
    assert stream.tls_verification.certificate_verified is True
    assert stream.tls_verification.hostname_verified is True
    stream.write_all(b"hello", deadline_monotonic_ns=deadline)
    assert stream.read_exactly(5, deadline_monotonic_ns=deadline) == b"world"
    stream.close()
    stream.close()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert server_result == [b"hello"]


def test_tls_hostname_failure_closes_socket_and_hides_backend_error(
    local_tls_certificate, monkeypatch
):
    cert, key = local_tls_certificate
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    server_context = _server_context(cert, key)
    client_sock, server_sock = socket.socketpair()

    def serve():
        try:
            with server_context.wrap_socket(server_sock, server_side=True):
                pass
        except OSError:
            pass

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    with pytest.raises(WssTLSFailure) as caught:
        VerifiedTLSStream.handshake(
            client_sock,
            expected_hostname="wrong.helius.test",
            deadline_monotonic_ns=time.monotonic_ns() + 5_000_000_000,
        )
    assert str(caught.value) == TLS_HANDSHAKE_FAILURE_REASON
    assert client_sock.fileno() == -1
    worker.join(timeout=5)


def test_tls_untrusted_certificate_closes_socket_and_hides_backend_error(
    local_tls_certificate, monkeypatch
):
    cert, key = local_tls_certificate
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    server_context = _server_context(cert, key)
    client_sock, server_sock = socket.socketpair()

    def serve():
        try:
            with server_context.wrap_socket(server_sock, server_side=True):
                pass
        except OSError:
            pass

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    with pytest.raises(WssTLSFailure) as caught:
        VerifiedTLSStream.handshake(
            client_sock,
            expected_hostname="helius.test",
            deadline_monotonic_ns=time.monotonic_ns() + 5_000_000_000,
        )
    assert str(caught.value) == TLS_HANDSHAKE_FAILURE_REASON
    assert client_sock.fileno() == -1
    worker.join(timeout=5)


def test_tls_partial_read_reports_exact_progress_without_returning_incomplete_data(
    local_tls_certificate, monkeypatch
):
    cert, key = local_tls_certificate
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    server_context = _server_context(cert, key)
    client_sock, server_sock = socket.socketpair()

    def serve():
        with server_context.wrap_socket(server_sock, server_side=True) as tls:
            tls.sendall(b"part")

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    deadline = time.monotonic_ns() + 5_000_000_000
    stream = VerifiedTLSStream.handshake(
        client_sock,
        expected_hostname="helius.test",
        deadline_monotonic_ns=deadline,
    )
    with pytest.raises(WssTLSFailure) as caught:
        stream.read_exactly(8, deadline_monotonic_ns=deadline)
    assert caught.value.bytes_completed == 4
    assert str(caught.value) == "tls_read_failed"
    stream.close()
    worker.join(timeout=5)


def test_tls_readiness_value_error_keeps_read_phase_and_partial_progress(
    local_tls_certificate, monkeypatch
):
    cert, key = local_tls_certificate
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    server_context = _server_context(cert, key)
    client_sock, server_sock = socket.socketpair()
    sent = threading.Event()

    def serve():
        with server_context.wrap_socket(server_sock, server_side=True) as tls:
            tls.sendall(b"x")
            sent.set()
            time.sleep(0.1)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    deadline = time.monotonic_ns() + 5_000_000_000
    stream = VerifiedTLSStream.handshake(
        client_sock,
        expected_hostname="helius.test",
        deadline_monotonic_ns=deadline,
    )
    assert sent.wait(timeout=2)

    def broken_select(*args):
        raise ValueError("closed or invalid descriptor")

    monkeypatch.setattr(wss_tls, "select", broken_select)
    with pytest.raises(WssTLSFailure) as caught:
        stream.read_exactly(2, deadline_monotonic_ns=deadline)

    assert str(caught.value) == "tls_read_failed"
    assert caught.value.bytes_completed == 1
    stream.close()
    worker.join(timeout=5)


def test_tls_keylog_environment_is_rejected_before_handshake():
    client_sock, server_sock = socket.socketpair()
    os.environ["SSLKEYLOGFILE"] = "/tmp/must-not-be-opened"
    try:
        with pytest.raises(WssTLSFailure) as caught:
            VerifiedTLSStream.handshake(
                client_sock,
                expected_hostname="helius.test",
                deadline_monotonic_ns=time.monotonic_ns() + 1_000_000_000,
            )
        assert str(caught.value) == TLS_KEYLOG_DISABLED_REASON
        assert client_sock.fileno() == -1
    finally:
        os.environ.pop("SSLKEYLOGFILE", None)
        server_sock.close()


def test_tls_handshake_timeout_closes_socket_without_network_io():
    client_sock, server_sock = socket.socketpair()
    with pytest.raises(WssTLSFailure) as caught:
        VerifiedTLSStream.handshake(
            client_sock,
            expected_hostname="helius.test",
            deadline_monotonic_ns=time.monotonic_ns() + 20_000_000,
        )
    assert str(caught.value) == TLS_HANDSHAKE_TIMEOUT_REASON
    assert client_sock.fileno() == -1
    server_sock.close()
