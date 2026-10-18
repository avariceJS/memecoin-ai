from __future__ import annotations

import json
import socket
import subprocess

import pytest

from memecoin_collector.wss_campaign import WssReferenceCampaign, default_authorization
from memecoin_collector.wss_reference import DISK_FLOOR_BYTES
from memecoin_collector.wss_socket import (
    Address,
    ResolverResult,
    WssSocketError,
    _kill_and_reap,
    _parse_resolver_output,
    connect_once,
    establish_one,
    resolve_once,
)


def test_resolver_uses_bounded_fixed_child_and_parses_os_order(tmp_path):
    script = tmp_path / "resolver.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'status':'ok','addresses':["
        f"{{'family':{socket.AF_INET},'ip':'8.8.8.8','scope_id':0}},"
        f"{{'family':{socket.AF_INET6},'ip':'2001:4860:4860::8888','scope_id':0}}]}}), end='')\n"
    )
    result = resolve_once("mainnet.helius-rpc.com", resolver_script=script)
    assert result.addresses == (
        Address(socket.AF_INET, "8.8.8.8", 0),
        Address(socket.AF_INET6, "2001:4860:4860::8888", 0),
    )
    assert result.stdout_bytes < 16 * 1024


def test_resolver_child_timeout_is_killed_and_reaped(tmp_path):
    script = tmp_path / "resolver.py"
    script.write_text("import time\ntime.sleep(5)\n")
    children = []
    real_popen = subprocess.Popen

    def tracked_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    with pytest.raises(WssSocketError, match="dns_resolution_timeout"):
        resolve_once(
            "mainnet.helius-rpc.com",
            timeout_ns=100_000_000,
            resolver_script=script,
            popen=tracked_popen,
        )
    assert len(children) == 1
    assert children[0].returncode is not None


def test_resolver_reap_preserves_operator_cancellation():
    class Process:
        def __init__(self):
            self.waits = 0

        def poll(self):
            return 0

        def kill(self):
            raise AssertionError("already exited")

        def wait(self, *, timeout):
            self.waits += 1
            assert timeout == 1.0
            if self.waits == 1:
                raise KeyboardInterrupt
            return 0

    process = Process()
    with pytest.raises(KeyboardInterrupt):
        _kill_and_reap(process)
    assert process.waits == 2


def test_resolver_child_receives_only_public_hostname_and_scrubbed_environment(
    tmp_path, monkeypatch
):
    script = tmp_path / "resolver.py"
    script.write_text('print(\'{"status":"ok","addresses":[]}\', end=\'\')\n')
    secret = "https://mainnet.helius-rpc.com/?api-key=never-in-child"
    monkeypatch.setenv("MEMECOIN_HELIUS_RPC_URL", secret)
    children = []
    real_popen = subprocess.Popen

    def tracked_popen(args, **kwargs):
        children.append((args, kwargs["env"]))
        return real_popen(args, **kwargs)

    result = resolve_once(
        "mainnet.helius-rpc.com",
        resolver_script=script,
        popen=tracked_popen,
    )
    assert result.addresses == ()
    args, child_env = children[0]
    assert args[-1] == "mainnet.helius-rpc.com"
    assert secret not in " ".join(args)
    assert child_env == {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC"}
    assert secret not in " ".join(child_env.values())


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b"not json",
        json.dumps(
            {
                "status": "ok",
                "addresses": [{"family": 2, "ip": "8.8.8.8", "scope_id": 0, "extra": 1}],
            }
        ).encode(),
        json.dumps(
            {"status": "ok", "addresses": [{"family": 2, "ip": "8.8.8.8", "scope_id": 0}] * 65}
        ).encode(),
    ],
)
def test_resolver_output_malformed_and_oversized_rows_fail_closed(raw):
    with pytest.raises(WssSocketError):
        _parse_resolver_output(raw)


def test_resolver_filters_non_public_and_family_mismatched_addresses():
    raw = json.dumps(
        {
            "status": "ok",
            "addresses": [
                {"family": socket.AF_INET, "ip": "10.0.0.1", "scope_id": 0},
                {"family": socket.AF_INET6, "ip": "8.8.8.8", "scope_id": 0},
                {"family": socket.AF_INET6, "ip": "fec0::1", "scope_id": 0},
                {"family": socket.AF_INET6, "ip": "4000::1", "scope_id": 0},
                {"family": socket.AF_INET6, "ip": "64:ff9b::7f00:1", "scope_id": 0},
                {
                    "family": socket.AF_INET6,
                    "ip": "2001:4860:4860::8888",
                    "scope_id": 1,
                },
            ],
        }
    ).encode()
    assert _parse_resolver_output(raw).addresses == ()


def test_resolver_rejects_zone_scoped_ip_and_non_integer_family():
    for family, ip in [
        (socket.AF_INET6, "2001:4860:4860::8888%fixture"),
        ({"malformed": "family"}, "8.8.8.8"),
    ]:
        raw = json.dumps(
            {
                "status": "ok",
                "addresses": [{"family": family, "ip": ip, "scope_id": 0}],
            }
        ).encode()
        with pytest.raises(WssSocketError, match="dns_resolver_output_invalid"):
            _parse_resolver_output(raw)


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "helius-rpc.com.evil.example",
        "127.0.0.1",
        "project-secret.helius-rpc.com",
    ],
)
def test_resolver_rejects_non_helius_hostnames(tmp_path, host):
    with pytest.raises(WssSocketError, match="dns_hostname_invalid"):
        resolve_once(host, resolver_script=tmp_path / "absent.py")


class FakeSocket:
    def __init__(self, family, result=0):
        self.family = family
        self.result = result
        self.closed = False
        self.target = None

    def setblocking(self, value):
        assert value is False

    def connect_ex(self, target):
        self.target = target
        return self.result

    def close(self):
        self.closed = True


def test_connect_uses_one_address_and_does_not_fallback():
    calls = []
    first = Address(socket.AF_INET6, "2001:4860:4860::8888")

    def factory(family, kind):
        result = 61
        value = FakeSocket(family, result)
        calls.append(value)
        return value

    with pytest.raises(WssSocketError, match="tcp_connect_failed"):
        connect_once(first, socket_factory=factory)
    assert len(calls) == 1
    assert calls[0].target == ("2001:4860:4860::8888", 443, 0, 0)
    assert calls[0].closed


def test_connect_immediate_success_still_obeys_deadline():
    values = iter([0, 10])
    fake_socket = FakeSocket(socket.AF_INET, result=0)
    with pytest.raises(WssSocketError, match="tcp_connect_timeout"):
        connect_once(
            Address(socket.AF_INET, "8.8.8.8"),
            timeout_ns=5,
            monotonic_ns=lambda: next(values),
            socket_factory=lambda *_args: fake_socket,
        )
    assert fake_socket.closed


def test_connect_monotonic_regression_fails_closed_and_closes_socket():
    values = iter([100, 99])
    fake_socket = FakeSocket(socket.AF_INET, result=0)
    with pytest.raises(WssSocketError, match="tcp_monotonic_clock_invalid"):
        connect_once(
            Address(socket.AF_INET, "8.8.8.8"),
            monotonic_ns=lambda: next(values),
            socket_factory=lambda *_args: fake_socket,
        )
    assert fake_socket.closed


@pytest.mark.parametrize(
    "address",
    [
        Address(socket.AF_INET6, "fec0::1"),
        Address(socket.AF_INET6, "4000::1"),
        Address(socket.AF_INET6, "64:ff9b::7f00:1"),
        Address(socket.AF_INET6, "2001:4860:4860::8888%fixture"),
    ],
)
def test_connect_rejects_non_public_ipv6_before_socket_creation(address):
    calls = []
    with pytest.raises(WssSocketError, match="tcp_connect_failed"):
        connect_once(address, socket_factory=lambda *_args: calls.append("socket"))
    assert calls == []


def test_establish_one_records_dns_then_one_tcp_attempt_without_secret(tmp_path):
    authorization = default_authorization(
        c4_stop_sha256="0" * 64,
        account_budget_evidence_sha256="1" * 64,
        authorized_at_ns=100,
        expires_at_ns=100 + 100_000,
    )
    campaign = WssReferenceCampaign(tmp_path / "campaign", authorization, opened_ns=100)
    session = campaign.start_session(
        at_ns=101,
        free_disk_bytes=DISK_FLOOR_BYTES + 1,
        opened_monotonic_ns=10,
    )
    wall = [102]
    mono = [10]

    def clock():
        wall[0] += 1
        return wall[0]

    def monotonic():
        mono[0] += 1
        return mono[0]

    selected = Address(socket.AF_INET6, "2001:4860:4860::8888")
    calls = []
    fake_socket = FakeSocket(socket.AF_INET6)
    result = establish_one(
        campaign,
        host="mainnet.helius-rpc.com",
        clock_ns=clock,
        monotonic_ns=monotonic,
        resolver=lambda host, **kwargs: ResolverResult((selected,), 32, 1),
        connector=lambda address, **kwargs: calls.append(address) or fake_socket,
        free_disk_bytes=lambda: DISK_FLOOR_BYTES + 1,
    )
    assert result is fake_socket
    assert calls == [selected]
    kinds = [
        json.loads(path.read_text())["kind"]
        for path in sorted((session.root / "journal").glob("*.json"))
    ]
    assert kinds == ["session_open", "dns_resolution", "tcp_connect"]
    assert 'endpoint_or_credentials_recorded":false' in "".join(
        path.read_text() for path in (session.root / "journal").glob("*.json")
    )
    assert "api-key" not in "".join(path.read_text() for path in session.root.rglob("*.*"))
    campaign.stop("synthetic_test_end", session.last_event_ns + 1)
    campaign.close()


def test_dns_failure_seals_unobserved_attempt_and_allows_fresh_session(tmp_path):
    authorization = default_authorization(
        c4_stop_sha256="0" * 64,
        account_budget_evidence_sha256="1" * 64,
        authorized_at_ns=100,
        expires_at_ns=100 + 100_000,
    )
    campaign = WssReferenceCampaign(tmp_path / "campaign", authorization, opened_ns=100)
    session = campaign.start_session(
        at_ns=101,
        free_disk_bytes=DISK_FLOOR_BYTES + 1,
        opened_monotonic_ns=10,
    )
    wall = [102]
    mono = [10]

    def clock():
        wall[0] += 1
        return wall[0]

    def monotonic():
        mono[0] += 1
        return mono[0]

    def no_address(host, **kwargs):
        return ResolverResult((), 10, 0)

    with pytest.raises(WssSocketError, match="dns_no_usable_address"):
        establish_one(
            campaign,
            host="mainnet.helius-rpc.com",
            clock_ns=clock,
            monotonic_ns=monotonic,
            resolver=no_address,
            free_disk_bytes=lambda: DISK_FLOOR_BYTES + 1,
        )
    stop = json.loads(sorted((session.root / "journal").glob("*.json"))[-1].read_text())
    assert stop["reason"] == "dns_resolution_failed"
    assert stop["subscription_acknowledged"] is False
    second = campaign.start_session(
        at_ns=stop["at_ns"] + 1,
        free_disk_bytes=DISK_FLOOR_BYTES + 1,
    )
    assert second is campaign.active
    campaign.stop("synthetic_test_end", second.last_event_ns + 1)
    campaign.close()


def test_cancellation_after_dns_result_seals_attempt_before_propagating(tmp_path):
    authorization = default_authorization(
        c4_stop_sha256="0" * 64,
        account_budget_evidence_sha256="1" * 64,
        authorized_at_ns=100,
        expires_at_ns=100 + 100_000,
    )
    campaign = WssReferenceCampaign(tmp_path / "campaign", authorization, opened_ns=100)
    session = campaign.start_session(
        at_ns=101,
        free_disk_bytes=DISK_FLOOR_BYTES + 1,
        opened_monotonic_ns=10,
    )
    mono_calls = [0]

    def monotonic():
        mono_calls[0] += 1
        if mono_calls[0] == 2:
            raise KeyboardInterrupt
        return 10 + mono_calls[0]

    resolver_calls = [0]

    def resolver(*_args, **_kwargs):
        resolver_calls[0] += 1
        return ResolverResult((Address(socket.AF_INET, "8.8.8.8"),), 32, 1)

    with pytest.raises(KeyboardInterrupt):
        establish_one(
            campaign,
            host="mainnet.helius-rpc.com",
            clock_ns=lambda: 102,
            monotonic_ns=monotonic,
            resolver=resolver,
            connector=lambda *_args, **_kwargs: pytest.fail("TCP must not follow cancellation"),
            free_disk_bytes=lambda: DISK_FLOOR_BYTES + 1,
        )
    assert resolver_calls == [1]
    assert campaign.terminal
    assert campaign.active is None
    stop = json.loads(sorted((session.root / "journal").glob("*.json"))[-1].read_text())
    assert stop["kind"] == "session_stop"
    assert stop["reason"] == "client_failure"
    campaign.close()


def test_clock_failure_during_dns_error_bookkeeping_still_seals_campaign(tmp_path):
    authorization = default_authorization(
        c4_stop_sha256="0" * 64,
        account_budget_evidence_sha256="1" * 64,
        authorized_at_ns=100,
        expires_at_ns=100 + 100_000,
    )
    campaign = WssReferenceCampaign(tmp_path / "campaign", authorization, opened_ns=100)
    session = campaign.start_session(
        at_ns=101,
        free_disk_bytes=DISK_FLOOR_BYTES + 1,
        opened_monotonic_ns=10,
    )

    clock_calls = [0]

    def clock():
        clock_calls[0] += 1
        if clock_calls[0] == 1:
            return 102
        raise RuntimeError("injected wall clock failure")

    calls = [0]

    def resolver(*_args, **_kwargs):
        calls[0] += 1
        raise WssSocketError("dns_resolution_timeout")

    with pytest.raises(WssSocketError, match="dns_result_journal_failed"):
        establish_one(
            campaign,
            host="mainnet.helius-rpc.com",
            clock_ns=clock,
            monotonic_ns=lambda: 11,
            resolver=resolver,
            free_disk_bytes=lambda: DISK_FLOOR_BYTES + 1,
        )
    assert calls == [1]
    assert campaign.terminal
    assert campaign.active is None
    stop = json.loads(sorted((session.root / "journal").glob("*.json"))[-1].read_text())
    assert stop["kind"] == "session_stop"
    campaign.close()


def test_closed_campaign_preflight_prevents_resolver_side_effect(tmp_path):
    authorization = default_authorization(
        c4_stop_sha256="0" * 64,
        account_budget_evidence_sha256="1" * 64,
        authorized_at_ns=100,
        expires_at_ns=100 + 100_000,
    )
    campaign = WssReferenceCampaign(tmp_path / "campaign", authorization, opened_ns=100)
    campaign.start_session(
        at_ns=101,
        free_disk_bytes=DISK_FLOOR_BYTES + 1,
        opened_monotonic_ns=10,
    )
    calls = []
    campaign.close()
    with pytest.raises(ValueError, match="writer_released"):
        establish_one(
            campaign,
            host="mainnet.helius-rpc.com",
            resolver=lambda *_args, **_kwargs: calls.append("resolver"),
            connector=lambda *_args, **_kwargs: calls.append("connector"),
            free_disk_bytes=lambda: DISK_FLOOR_BYTES + 1,
        )
    assert calls == []


def test_post_connect_writer_release_closes_owned_socket(tmp_path):
    authorization = default_authorization(
        c4_stop_sha256="0" * 64,
        account_budget_evidence_sha256="1" * 64,
        authorized_at_ns=100,
        expires_at_ns=100 + 100_000,
    )
    campaign = WssReferenceCampaign(tmp_path / "campaign", authorization, opened_ns=100)
    campaign.start_session(
        at_ns=101,
        free_disk_bytes=DISK_FLOOR_BYTES + 1,
        opened_monotonic_ns=10,
    )
    wall = iter(range(102, 120))
    mono = iter(range(11, 40))
    fake_socket = FakeSocket(socket.AF_INET)

    def connect(_address, **_kwargs):
        campaign.close()
        return fake_socket

    with pytest.raises(WssSocketError):
        establish_one(
            campaign,
            host="mainnet.helius-rpc.com",
            clock_ns=lambda: next(wall),
            monotonic_ns=lambda: next(mono),
            resolver=lambda *_args, **_kwargs: ResolverResult(
                (Address(socket.AF_INET, "8.8.8.8"),), 32, 1
            ),
            connector=connect,
            free_disk_bytes=lambda: DISK_FLOOR_BYTES + 1,
        )
    assert fake_socket.closed


@pytest.mark.parametrize("gate", ["authorization", "disk"])
def test_completed_dns_is_journaled_before_next_phase_gate(tmp_path, gate):
    authorization = default_authorization(
        c4_stop_sha256="0" * 64,
        account_budget_evidence_sha256="1" * 64,
        authorized_at_ns=100,
        expires_at_ns=150,
    )
    campaign = WssReferenceCampaign(tmp_path / "campaign", authorization, opened_ns=100)
    session = campaign.start_session(
        at_ns=101,
        free_disk_bytes=DISK_FLOOR_BYTES + 1,
        opened_monotonic_ns=10,
    )
    wall = [101]
    mono = [10]
    disk_reads = [0]

    def clock():
        wall[0] += 1
        return wall[0]

    def disk():
        disk_reads[0] += 1
        if gate == "disk" and disk_reads[0] == 2:
            return DISK_FLOOR_BYTES - 1
        return DISK_FLOOR_BYTES + 1

    def resolver(*_args, **_kwargs):
        if gate == "authorization":
            wall[0] = 150
        return ResolverResult((Address(socket.AF_INET, "8.8.8.8"),), 32, 1)

    with pytest.raises(WssSocketError):
        establish_one(
            campaign,
            host="mainnet.helius-rpc.com",
            clock_ns=clock,
            monotonic_ns=lambda: mono.__setitem__(0, mono[0] + 1) or mono[0],
            resolver=resolver,
            connector=lambda *_args, **_kwargs: pytest.fail("TCP must be gated"),
            free_disk_bytes=disk,
        )
    kinds = [
        json.loads(path.read_text())["kind"]
        for path in sorted((session.root / "journal").glob("*.json"))
    ]
    assert kinds == ["session_open", "dns_resolution", "session_stop"]
    assert json.loads(sorted((session.root / "journal").glob("*.json"))[-1].read_text())[
        "reason"
    ] == ("authorization_expired" if gate == "authorization" else "disk_floor")
    campaign.close()


@pytest.mark.parametrize("gate", ["authorization", "disk"])
def test_completed_tcp_is_journaled_before_final_gate_and_socket_closes(tmp_path, gate):
    authorization = default_authorization(
        c4_stop_sha256="0" * 64,
        account_budget_evidence_sha256="1" * 64,
        authorized_at_ns=100,
        expires_at_ns=150,
    )
    campaign = WssReferenceCampaign(tmp_path / "campaign", authorization, opened_ns=100)
    session = campaign.start_session(
        at_ns=101,
        free_disk_bytes=DISK_FLOOR_BYTES + 1,
        opened_monotonic_ns=10,
    )
    wall = [101]
    mono = [10]
    disk_reads = [0]
    fake_socket = FakeSocket(socket.AF_INET)

    def clock():
        wall[0] += 1
        return wall[0]

    def disk():
        disk_reads[0] += 1
        if gate == "disk" and disk_reads[0] == 3:
            return DISK_FLOOR_BYTES - 1
        return DISK_FLOOR_BYTES + 1

    def connector(*_args, **_kwargs):
        if gate == "authorization":
            wall[0] = 150
        return fake_socket

    with pytest.raises(WssSocketError):
        establish_one(
            campaign,
            host="mainnet.helius-rpc.com",
            clock_ns=clock,
            monotonic_ns=lambda: mono.__setitem__(0, mono[0] + 1) or mono[0],
            resolver=lambda *_args, **_kwargs: ResolverResult(
                (Address(socket.AF_INET, "8.8.8.8"),), 32, 1
            ),
            connector=connector,
            free_disk_bytes=disk,
        )
    kinds = [
        json.loads(path.read_text())["kind"]
        for path in sorted((session.root / "journal").glob("*.json"))
    ]
    assert kinds == ["session_open", "dns_resolution", "tcp_connect", "session_stop"]
    tcp = json.loads(sorted((session.root / "journal").glob("*.json"))[2].read_text())
    assert tcp["outcome"] == "connected"
    assert fake_socket.closed
    campaign.close()
