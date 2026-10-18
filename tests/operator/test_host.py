"""Read-only host display. Subprocess and home are mocked; nothing is powered off."""

import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from memecoin_operator import host
from memecoin_operator.host import snapshot

AC = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=SECRET-CELL)\t100%; charged;\n"
BATTERY = "Now drawing from 'Battery Power'\n\t0%; discharging; SECRET-CELL\n"
JOB = """gui/501/{label} = {{
\tstate = running
\tpid = 4242
\tprogram = /secret/path/python
\tenvironment = {{
\t\tTOKEN = SECRET-VALUE
\t}}
\truns = 4
\tlast exit code = 0
}}
"""
WAITING = """gui/501/ai.memecoin.brain.b12.paper = {
\tstate = not running
\tprogram = /secret/path/python
\truns = 277
\tlast exit code = 0
\tresource coalition = {
\t\tstate = active
\t}
}
"""
NEVER = """gui/501/ai.memecoin.brain.b10.paper = {
\tstate = not running
\truns = 1
\tlast exit code = (never exited)
}
"""


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    host._clear()
    monkeypatch.setattr(host, "_home", lambda: tmp_path / "home")

    def _forbid(*_args, **_kwargs):
        raise AssertionError("unmocked subprocess")

    monkeypatch.setattr(host.subprocess, "run", _forbid)
    yield
    host._clear()


def _done(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, out, err)


def _config(root: Path, labels: list | None = None, *, omit: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    body: dict = {}
    if not omit:
        body["managed_legacy_labels"] = [] if labels is None else labels
    (root / "config.json").write_text(json.dumps(body), encoding="utf-8")
    return root


def _install(monkeypatch, outputs: dict | None = None, *, platform: str = "darwin"):
    calls: list[tuple] = []
    outputs = outputs or {}
    monkeypatch.setattr(host.sys, "platform", platform)
    monkeypatch.setattr(host.os, "getuid", lambda: 501)

    def run(argv, capture_output, text, timeout, check):
        assert capture_output and text and check is False
        assert timeout <= 1
        record = tuple(argv)
        calls.append(record)
        assert argv[0] in {"launchctl", "pmset", "ps", "sysctl", "systemctl"}
        if argv[0] == "systemctl":
            assert argv == [
                "systemctl",
                "show",
                "--no-pager",
                "--property=LoadState,UnitFileState,ActiveState,MainPID",
                host._SYSTEMD_OPERATOR,
            ]
        if argv[0] == "launchctl":
            assert argv[1:][:1] == ["print"]
            assert len(argv) == 3
            assert "list" not in argv
            label = argv[2].rsplit("/", 1)[-1]
            assert label == host._OPERATOR or label in host._LEGACY_SET
        if record in outputs:
            result = outputs[record]
        elif argv[0] in outputs:
            result = outputs[argv[0]]
        elif argv[0] == "launchctl":
            result = _done(113, "", "Could not find service")
        elif argv[0] == "pmset":
            result = _done(0, AC)
        elif argv[0] == "sysctl":
            result = _done(0, "{ sec = 100, usec = 1 }\n")
        elif argv[0] == "systemctl":
            result = _done(
                0,
                "LoadState=not-found\nUnitFileState=\nActiveState=inactive\nMainPID=0\n",
            )
        else:
            result = _done(0, "12\n")
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(host.subprocess, "run", run)
    return calls


def _keys(value, found: set[str]) -> None:
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            _keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _keys(item, found)


def test_allowlist_matches_operator_legacy():
    from memecoin_operator.adapters import LEGACY_LABELS

    assert host._LEGACY == LEGACY_LABELS
    assert host._OPERATOR == "ai.memecoin.brain.operator"
    assert host._OPERATOR not in host._LEGACY
    assert host._BUDGET_S <= 6
    assert host._CALL_TIMEOUT_S <= 1
    assert host._TTL_S <= 60


def test_disk_power_and_waiting_job_are_display_only(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", ["ai.memecoin.brain.b12.paper"])
    monkeypatch.setattr(
        host.shutil, "disk_usage", lambda _path: SimpleNamespace(total=30, used=12, free=18)
    )
    label = "gui/501/ai.memecoin.brain.b12.paper"
    calls = _install(
        monkeypatch,
        {("launchctl", "print", label): _done(0, WAITING), "pmset": _done(0, AC)},
    )
    got = snapshot(root)
    assert got["reason"] is None
    assert got["cache_age_s"] == 0.0
    assert isinstance(got["measured_ns"], int)
    assert got["disk"] == {
        "total_bytes": 30,
        "used_bytes": 12,
        "free_bytes": 18,
        "reason": None,
    }
    assert got["power"] == {"source": "ac", "percent": 100, "reason": None}
    service = got["services"][0]
    assert service["label"] == "ai.memecoin.brain.b12.paper"
    assert service["loaded"] is True
    assert service["state"] == "not running"
    assert service["pid"] is None
    assert service["runs"] == 277
    assert service["last_exit"] == 0
    assert service["reason"] is None
    assert got["operator"]["disposition"] == "dev"
    assert got["operator"]["loaded"] is False
    assert got["uptime"]["process_s"] is None
    assert got["uptime"]["process_reason"] == "operator_not_launchd"
    assert got["uptime"]["boot_s"] is not None
    assert got["uptime"]["process_s"] != got["uptime"]["boot_s"]
    text = json.dumps(got)
    assert "SECRET" not in text and "/secret" not in text and "active" not in text
    names = {call[0] for call in calls}
    assert "launchctl" in names and names <= {"launchctl", "pmset", "sysctl"}
    found: set[str] = set()
    _keys(got, found)
    assert found.isdisjoint({"down", "healthy", "stopped", "freshness_s"})


def test_battery_zero_is_parsed_and_missing_percent_is_null(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", [])
    _install(monkeypatch, {"pmset": _done(0, BATTERY)})
    charged = snapshot(root)
    assert charged["power"] == {"source": "battery", "percent": 0, "reason": None}
    assert "SECRET-CELL" not in json.dumps(charged)
    host._clear()
    _install(monkeypatch, {"pmset": _done(0, "Now drawing from 'AC Power'\n")})
    plain = snapshot(root)
    assert plain["power"] == {"source": "ac", "percent": None, "reason": None}
    host._clear()
    _install(monkeypatch, {"pmset": _done(0, "SECRET-UNPARSED battery text\n")})
    broken = snapshot(root)
    assert broken["power"] == {"source": None, "percent": None, "reason": "power_unparsed"}
    assert "SECRET-UNPARSED" not in json.dumps(broken)


def test_non_mac_skips_power_and_boot(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", [])
    calls = _install(monkeypatch, platform="linux")
    monkeypatch.setattr(host, "_linux_boot", lambda: (None, "boot_unavailable"))
    got = snapshot(root)
    assert got["power"]["reason"] == "power_unsupported"
    assert got["power"]["percent"] is None
    assert got["uptime"]["boot_s"] is None
    assert got["uptime"]["boot_reason"] == "boot_unavailable"
    assert all(call[0] != "pmset" and call[0] != "sysctl" for call in calls)
    assert all(call[0] != "launchctl" for call in calls)
    assert got["operator"]["loaded"] is False
    assert got["operator"]["disposition"] == "not_installed"
    assert got["services"] == []


def test_linux_systemd_reports_disabled_and_inactive_without_launchd(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", ["ignored-mac-label"])
    calls = _install(
        monkeypatch,
        {
            "systemctl": _done(
                0,
                "LoadState=loaded\nUnitFileState=disabled\nActiveState=inactive\nMainPID=4242\n",
            )
        },
        platform="linux",
    )
    got = snapshot(root)
    assert got["operator"]["label"] == host._SYSTEMD_OPERATOR
    assert got["operator"]["loaded"] is True
    assert got["operator"]["disposition"] == "systemd"
    assert got["operator"]["state"] == "inactive"
    assert got["operator"]["unit_file_state"] == "disabled"
    assert got["operator"]["pid"] is None
    assert got["operator"]["runs"] is None and got["operator"]["last_exit"] is None
    assert got["services"] == []
    assert [call for call in calls if call[0] == "systemctl"] == [
        (
            "systemctl",
            "show",
            "--no-pager",
            "--property=LoadState,UnitFileState,ActiveState,MainPID",
            host._SYSTEMD_OPERATOR,
        )
    ]
    assert all(call[0] != "launchctl" for call in calls)


def test_linux_systemd_active_pid_is_distinct_from_enabled_state(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", [])
    calls = _install(
        monkeypatch,
        {
            "systemctl": _done(
                0,
                "LoadState=loaded\nUnitFileState=disabled\nActiveState=active\nMainPID=4242\n",
            ),
            "ps": _done(0, "12\n"),
        },
        platform="linux",
    )
    got = snapshot(root)
    assert got["operator"]["loaded"] is True
    assert got["operator"]["state"] == "active"
    assert got["operator"]["unit_file_state"] == "disabled"
    assert got["operator"]["pid"] == 4242
    assert got["operator"]["runs"] is None and got["operator"]["last_exit"] is None
    assert got["uptime"]["process_s"] == 12.0
    assert all(call[0] != "launchctl" for call in calls)


@pytest.mark.parametrize(
    ("output", "reason"),
    [
        ("LoadState=loaded\nLoadState=loaded\n", "systemd_malformed"),
        (
            "LoadState=loaded\nUnitFileState=disabled\nActiveState=unknown\nMainPID=0\n",
            "systemd_unrecognized_state",
        ),
    ],
)
def test_linux_systemd_rejects_malformed_output(tmp_path, monkeypatch, output, reason):
    root = _config(tmp_path / "op", [])
    _install(monkeypatch, {"systemctl": _done(0, output)}, platform="linux")
    got = snapshot(root)
    assert got["operator"]["loaded"] is None
    assert got["operator"]["state"] is None
    assert got["operator"]["unit_file_state"] is None
    assert got["operator"]["reason"] == reason


@pytest.mark.parametrize(
    "failure,reason",
    [
        (FileNotFoundError(), "systemd_unavailable"),
        (subprocess.TimeoutExpired("systemctl", 1), "systemctl_timeout"),
    ],
)
def test_linux_systemd_failure_stays_unknown(tmp_path, monkeypatch, failure, reason):
    root = _config(tmp_path / "op", [])
    _install(monkeypatch, {"systemctl": failure}, platform="linux")
    got = snapshot(root)
    assert got["operator"]["loaded"] is None
    assert got["operator"]["state"] is None
    assert got["operator"]["unit_file_state"] is None
    assert got["operator"]["reason"] == reason


def test_linux_systemd_display_cache_expires_before_rechecking(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", [])
    state = {"active": False, "clock": 1_000_000_000}

    def run(argv, **kwargs):
        if argv[0] == "ps":
            return _done(0, "12\n")
        assert argv[0] == "systemctl"
        active, pid = ("active", "4242") if state["active"] else ("inactive", "0")
        return _done(
            0,
            f"LoadState=loaded\nUnitFileState=disabled\nActiveState={active}\nMainPID={pid}\n",
        )

    monkeypatch.setattr(host.sys, "platform", "linux")
    monkeypatch.setattr(host.subprocess, "run", run)
    monkeypatch.setattr(host.time, "monotonic_ns", lambda: state["clock"])
    first = snapshot(root)
    state["active"] = True
    cached = snapshot(root)
    assert first["operator"]["state"] == "inactive"
    assert cached["operator"]["state"] == "inactive"
    state["clock"] += int((host._TTL_S + 1) * 1e9)
    fresh = snapshot(root)
    assert fresh["operator"]["state"] == "active"
    assert fresh["operator"]["pid"] == 4242


def test_launchd_process_uptime_is_not_boot_time(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", [])
    monkeypatch.setattr(host.time, "time", lambda: 1600.0)
    printed = JOB.format(label=host._OPERATOR)
    calls = _install(
        monkeypatch,
        {
            ("launchctl", "print", "gui/501/" + host._OPERATOR): _done(0, printed),
            "sysctl": _done(0, "{ sec = 100, usec = 0 }\n"),
            "ps": _done(0, " 12\n"),
        },
    )
    got = snapshot(root)
    assert got["operator"]["disposition"] == "launchd"
    assert got["operator"]["pid"] == 4242
    assert got["operator"]["runs"] == 4
    assert got["uptime"]["process_s"] == 12.0
    assert got["uptime"]["process_pid"] == 4242
    assert got["uptime"]["process_reason"] is None
    assert got["uptime"]["boot_s"] == 1500.0
    assert got["uptime"]["boot_reason"] is None
    assert "SECRET-VALUE" not in json.dumps(got)
    assert ("ps", "-o", "etimes=", "-p", "4242") in calls
    host._clear()
    _install(
        monkeypatch,
        {
            ("launchctl", "print", "gui/501/" + host._OPERATOR): _done(0, printed),
            "sysctl": _done(0, "{ sec = 100, usec = 0 }\n"),
            "ps": _done(0, "\n"),
        },
    )
    unknown = snapshot(root)
    assert unknown["uptime"]["process_s"] is None
    assert unknown["uptime"]["boot_s"] == 1500.0
    assert unknown["uptime"]["process_reason"] == "process_unavailable"


def test_never_exited_is_null_and_dev_versus_not_loaded(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", ["ai.memecoin.brain.b10.paper"])
    label = "gui/501/ai.memecoin.brain.b10.paper"
    _install(monkeypatch, {("launchctl", "print", label): _done(0, NEVER)})
    missing_plist = snapshot(root)
    assert missing_plist["operator"]["disposition"] == "dev"
    assert missing_plist["services"][0]["last_exit"] is None
    assert missing_plist["services"][0]["loaded"] is True
    host._clear()
    plist = tmp_path / "home" / "Library" / "LaunchAgents" / (host._OPERATOR + ".plist")
    plist.parent.mkdir(parents=True)
    plist.write_text("not-read", encoding="utf-8")
    _install(monkeypatch)
    installed = snapshot(root)
    assert installed["operator"]["loaded"] is False
    assert installed["operator"]["disposition"] == "not_loaded"
    assert plist.read_text(encoding="utf-8") == "not-read"


def test_rejected_labels_are_not_queried_or_echoed(tmp_path, monkeypatch):
    root = _config(
        tmp_path / "op",
        [
            "ai.memecoin.brain.f01.collector",
            "ai.memecoin.d43.live",
            "ai.memecoin.brain.b12.paper",
            "ai.memecoin.brain.b12.paper",
            host._OPERATOR,
        ],
    )
    calls = _install(monkeypatch)
    got = snapshot(root)
    assert got["rejected_count"] == 2
    assert [item["label"] for item in got["services"]] == ["ai.memecoin.brain.b12.paper"]
    text = json.dumps(got)
    assert "f01" not in text and "d43" not in text
    joined = " ".join(" ".join(call) for call in calls)
    assert "f01" not in joined and "d43" not in joined
    printed = [call[2] for call in calls if call[0] == "launchctl"]
    assert printed.count("gui/501/ai.memecoin.brain.b12.paper") == 1
    assert printed.count("gui/501/" + host._OPERATOR) == 1


def test_default_labels_follow_legacy_allowlist(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", omit=True)
    calls = _install(monkeypatch)
    got = snapshot(root)
    assert got["config_reason"] is None
    assert [item["label"] for item in got["services"]] == list(host._LEGACY)
    printed = [call[2].rsplit("/", 1)[-1] for call in calls if call[0] == "launchctl"]
    assert printed[0] == host._OPERATOR
    assert printed[1:] == list(host._LEGACY)


def test_malformed_config_and_timeout_stay_null(tmp_path, monkeypatch):
    root = tmp_path / "op"
    root.mkdir()
    (root / "config.json").write_text("{", encoding="utf-8")
    calls = _install(monkeypatch)
    malformed = snapshot(root)
    assert malformed["config_reason"] == "config_malformed"
    assert malformed["services"] == []
    assert malformed["operator"]["label"] == host._OPERATOR
    host._clear()
    _config(root, ["ai.memecoin.brain.b09.source"])
    (root / "config.json").write_text(
        json.dumps({"managed_legacy_labels": "ai.memecoin.brain.b09.source"}),
        encoding="utf-8",
    )

    def run(argv, capture_output, text, timeout, check):
        calls.append(tuple(argv))
        assert timeout <= 1
        if argv[0] == "launchctl":
            raise subprocess.TimeoutExpired(argv, timeout)
        if argv[0] == "pmset":
            return _done(0, AC)
        if argv[0] == "sysctl":
            return _done(0, "{ sec = 100, usec = 1 }\n")
        return _done(0, "12\n")

    monkeypatch.setattr(host.subprocess, "run", run)
    timed = snapshot(root)
    assert timed["config_reason"] == "config_labels_malformed"
    assert timed["operator"]["loaded"] is None
    assert timed["operator"]["disposition"] is None
    assert timed["operator"]["reason"] == "launchctl_timeout"
    assert timed["services"] == []
    host._clear()
    _install(monkeypatch, {("launchctl", "print", "gui/501/" + host._OPERATOR): _done(0, "nope")})
    _config(root, [])
    unparsed = snapshot(root)
    assert unparsed["operator"]["loaded"] is True
    assert unparsed["operator"]["state"] is None
    assert unparsed["operator"]["runs"] is None
    assert unparsed["operator"]["last_exit"] is None
    assert unparsed["operator"]["reason"] == "launchctl_unparsed"


def test_disk_errors_are_sanitized(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", [])
    _install(monkeypatch)

    def explode(_path):
        raise RuntimeError("SECRET-DISK /Users/hidden")

    monkeypatch.setattr(host.shutil, "disk_usage", explode)
    got = snapshot(root)
    assert got["reason"] is None
    assert got["disk"]["reason"] == "disk_unavailable"
    assert got["disk"]["free_bytes"] is None
    assert "SECRET-DISK" not in json.dumps(got)
    assert got["power"]["source"] == "ac"


def test_missing_root_creates_nothing(tmp_path, monkeypatch):
    _install(monkeypatch)
    missing = tmp_path / "absent"
    got = snapshot(missing)
    assert not missing.exists()
    assert got["config_reason"] == "config_missing"
    assert got["disk"]["reason"] == "disk_unavailable"
    assert got["services"] == []
    assert snapshot(None)["reason"] == "bad_root"  # type: ignore[arg-type]


def test_source_files_are_not_rewritten(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", ["ai.memecoin.brain.b09.audit"])
    (root / "STOP").write_text("keep", encoding="utf-8")
    (root / "raw").mkdir()
    (root / "raw" / "capture.bin").write_bytes(b"raw-bytes")
    (root / "index.sqlite").write_bytes(b"db")

    def files() -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    before = files()
    _install(monkeypatch)
    snapshot(root)
    assert files() == before


def test_cache_ttl_config_and_clock_rollback(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", ["ai.memecoin.brain.b12.paper"])
    clock = {"wall": 10**18, "mono": 10**15}
    monkeypatch.setattr(host.time, "time_ns", lambda: clock["wall"])
    monkeypatch.setattr(host.time, "monotonic_ns", lambda: clock["mono"])
    calls = _install(monkeypatch)
    first = snapshot(root)
    assert first["cache_age_s"] == 0.0
    launch = [call for call in calls if call[0] == "launchctl"]
    second = snapshot(root)
    assert [call for call in calls if call[0] == "launchctl"] == launch
    assert second["measured_ns"] == first["measured_ns"]
    clock["mono"] += int(60 * 1e9)
    third = snapshot(root)
    assert third["measured_ns"] == first["measured_ns"]
    assert third["cache_age_s"] == 60.0
    assert [call for call in calls if call[0] == "launchctl"] == launch
    clock["mono"] += 1
    snapshot(root)
    assert len([call for call in calls if call[0] == "launchctl"]) > len(launch)
    host._clear()
    calls.clear()
    clock["mono"] += 5
    clock["wall"] += 5
    snapshot(root)
    cached = len(calls)
    clock["wall"] -= 1
    snapshot(root)
    assert len(calls) > cached
    host._clear()
    calls.clear()
    clock["wall"] += 5
    clock["mono"] += 5
    snapshot(root)
    cached = len(calls)
    clock["mono"] -= 1
    snapshot(root)
    assert len(calls) > cached
    _config(root, ["ai.memecoin.brain.b10.paper"])
    changed = snapshot(root)
    assert [item["label"] for item in changed["services"]] == ["ai.memecoin.brain.b10.paper"]


def test_budget_skips_probes_without_marking_them_unloaded(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", ["ai.memecoin.brain.b09.source"])
    monkeypatch.setattr(host, "_BUDGET_S", 0.0)
    calls = _install(monkeypatch)
    monkeypatch.setattr(
        host.shutil, "disk_usage", lambda _path: SimpleNamespace(total=9, used=4, free=5)
    )
    got = snapshot(root)
    assert calls == []
    assert got["disk"]["total_bytes"] == 9
    assert got["power"]["reason"] == "inspection_budget"
    assert got["power"]["percent"] is None
    assert got["operator"]["loaded"] is None
    assert got["operator"]["reason"] == "inspection_budget"
    assert got["services"][0]["loaded"] is None
    assert got["services"][0]["reason"] == "inspection_budget"
    assert got["uptime"]["boot_s"] is None
    assert got["uptime"]["process_s"] is None


def test_returns_are_private_and_failures_hide_exception_text(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", [])
    _install(monkeypatch)
    monkeypatch.setattr(
        host.shutil, "disk_usage", lambda _path: SimpleNamespace(total=3, used=1, free=2)
    )
    first = snapshot(root)
    first["disk"]["total_bytes"] = -1
    second = snapshot(root)
    assert second["disk"]["total_bytes"] == 3
    host._clear()

    def explode(_root):
        raise RuntimeError("SECRET-PATH")

    monkeypatch.setattr(host, "_inspect", explode)
    failed = snapshot(root)
    assert failed["reason"] == "inspection_failed"
    assert failed["cache_age_s"] == 0.0
    assert "SECRET-PATH" not in json.dumps(failed)


def test_concurrent_callers_share_one_inspection(tmp_path, monkeypatch):
    root = _config(tmp_path / "op", [])
    _install(monkeypatch)
    monkeypatch.setattr(
        host.shutil, "disk_usage", lambda _path: SimpleNamespace(total=8, used=3, free=5)
    )
    real = host._inspect
    calls = []

    def wrapped(path):
        calls.append(threading.get_ident())
        time.sleep(0.2)
        return real(path)

    monkeypatch.setattr(host, "_inspect", wrapped)
    barrier = threading.Barrier(6)
    found = []
    errors = []

    def worker():
        try:
            barrier.wait()
            found.append(snapshot(root))
        except Exception as exc:  # noqa: BLE001 - collected across threads
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert errors == []
    assert len(calls) == 1
    assert len({id(item) for item in found}) == 6
    found[0]["disk"]["free_bytes"] = -1
    assert all(item["disk"]["free_bytes"] == 5 for item in found[1:])
    assert host._FLIGHTS == {}
