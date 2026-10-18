"""Phase observers preserve return values, original errors, and the TLS call path."""

import runpy
import socket
import ssl
from pathlib import Path

import pytest

m = runpy.run_path(str(Path(__file__).parents[1] / "tools/cold_connection.py"))


def test_observer_forwards_arguments_and_result():
    rows = []
    token = object()

    def call(a, *, b):
        assert a is token and b == 7
        return token

    assert m["observed"]("tcp", call, rows.append)(token, b=7) is token
    assert [r["event"] for r in rows] == ["start", "end"]
    assert rows[-1]["elapsed_ns"] >= 0


def test_error_identity_and_secret_redaction():
    rows = []
    error = TimeoutError("credential-should-never-appear")

    def fail():
        raise error

    with pytest.raises(TimeoutError) as caught:
        m["observed"]("tls", fail, rows.append)()
    assert caught.value is error
    assert rows[-1]["phase"] == "tls" and rows[-1]["error_type"] == "TimeoutError"
    assert "credential" not in str(rows)


def test_instrumentation_restores_functions_even_on_error():
    originals = (socket.getaddrinfo, socket.socket.connect, ssl.SSLContext.wrap_socket)
    with pytest.raises(RuntimeError), m["phase_observer"](lambda r: None):
        assert ssl.SSLContext.wrap_socket is not originals[2]
        raise RuntimeError()
    assert originals == (socket.getaddrinfo, socket.socket.connect, ssl.SSLContext.wrap_socket)
