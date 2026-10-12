from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from tools import brain_senses_agave_confinement_v2 as confinement  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").exists(),
    reason="macOS Seatbelt fixture required",
)


def _policy(tmp_path: Path) -> tuple[confinement.SandboxPolicy, Path, Path]:
    stage = (tmp_path / "stage").resolve()
    stage.mkdir(mode=0o700)
    stage.chmod(0o700)
    outside = (tmp_path / "outside").resolve()
    outside.mkdir(mode=0o700)
    python = Path(os.path.realpath(sys.executable))
    python_app = Path(sys.base_prefix) / "Resources/Python.app/Contents/MacOS/Python"
    policy = confinement.build_policy(
        stage_root=str(stage),
        read_roots=(str(Path(sys.base_prefix).resolve()),),
        executable_paths=(str(python), str(python_app.resolve(strict=True))),
    )
    return policy, stage, outside


def _python() -> str:
    return os.path.realpath(sys.executable)


def _run(
    policy: confinement.SandboxPolicy,
    code: str,
    *args: str,
    timeout: float = 10,
) -> confinement.RunResult:
    return confinement.run_confined(
        [_python(), "-B", "-S", "-c", code, *args],
        policy,
        timeout_seconds=timeout,
    )


def test_default_deny_profile_blocks_parent_and_child_read_write_network(tmp_path: Path) -> None:
    policy, stage, outside = _policy(tmp_path)
    secret = outside / "private.txt"
    secret.write_text("synthetic-secret")
    child = (
        "import json,socket,sys; result={}; "
        'exec(\'try: open(sys.argv[1]).read(); result["read"]="allowed"\\n'
        'except OSError: result["read"]="denied"\'); '
        'exec(\'try: open(sys.argv[2],"w").write("bad"); result["write"]="allowed"\\n'
        'except OSError: result["write"]="denied"\'); '
        'exec(\'try: socket.socket().connect(("127.0.0.1",1)); result["network"]="allowed"\\n'
        'except OSError: result["network"]="denied"\'); print(json.dumps(result) )'
    )
    parent = (
        "import json,socket,subprocess,sys; result={}; "
        'exec(\'try: open(sys.argv[1]).read(); result["read"]="allowed"\\n'
        'except OSError: result["read"]="denied"\'); '
        'exec(\'try: open(sys.argv[2],"w").write("bad"); result["write"]="allowed"\\n'
        'except OSError: result["write"]="denied"\'); '
        'exec(\'try: socket.socket().connect(("127.0.0.1",1)); result["network"]="allowed"\\n'
        'except OSError: result["network"]="denied"\'); '
        f"child=subprocess.run([{_python()!r},'-B','-S','-c',{child!r},"
        "sys.argv[1],sys.argv[2]],capture_output=True,text=True,check=True); "
        "result['child']=json.loads(child.stdout); "
        "open(sys.argv[3],'w').write(json.dumps(result))"
    )
    result_path = stage / "result.json"
    result = _run(policy, parent, str(secret), str(outside / "parent-write"), str(result_path))
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    observed = json.loads(result_path.read_text())
    assert observed == {
        "read": "denied",
        "write": "denied",
        "network": "denied",
        "child": {"read": "denied", "write": "denied", "network": "denied"},
    }
    assert not (outside / "parent-write").exists()
    assert result.process_census_complete
    assert result.survivors_after_cleanup == ()


def test_symlink_cannot_escape_allowed_stage_reads(tmp_path: Path) -> None:
    policy, stage, outside = _policy(tmp_path)
    secret = outside / "secret.txt"
    secret.write_text("do-not-read")
    link = stage / "escape"
    link.symlink_to(secret)
    code = (
        "import sys; "
        'exec(\'try: open(sys.argv[1]).read(); print("readable")\\n'
        'except OSError: print("denied")\')'
    )
    result = _run(policy, code, str(link))
    assert result.returncode == 0
    assert result.stdout.strip() == b"denied"


def test_process_census_kills_detached_descendant_on_timeout(tmp_path: Path) -> None:
    policy, stage, _outside = _policy(tmp_path)
    pid_file = stage / "child.pid"
    child_code = (
        "import os,sys,time; os.setsid(); "
        "open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)"
    )
    parent_code = (
        "import subprocess,sys,time; subprocess.Popen(["
        f"{_python()!r},'-B','-S','-c',{child_code!r},{str(pid_file)!r}]); "
        "time.sleep(60)"
    )
    result = _run(policy, parent_code, timeout=0.5)
    assert result.timed_out
    assert result.process_census_complete
    assert result.observed_descendant_pids
    assert result.survivors_after_cleanup == ()
    assert pid_file.exists()
    child_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("detached sandbox child survived cleanup")


def test_path_replacement_demonstrates_no_mount_identity_confinement(tmp_path: Path) -> None:
    policy, stage, _outside = _policy(tmp_path)
    original_stage = stage.with_name("stage-detached")
    target = stage / "underlay-write.json"
    code = "import sys,time; time.sleep(.7); open(sys.argv[1],'w').write('underlay')"
    before = b""
    results: list[confinement.RunResult] = []

    def execute() -> None:
        results.append(_run(policy, code, str(target), timeout=5))

    thread = threading.Thread(target=execute)
    thread.start()
    time.sleep(0.2)
    stage.rename(original_stage)
    stage.mkdir(mode=0o700)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(results) == 1 and results[0].returncode == 0
    # Seatbelt grants are tied to this pathname, so the replacement underlay is
    # writable. This is a concrete NO-GO result for mountpoint-backed builds.
    after = target.read_bytes() if target.exists() else b""
    assert after == b"underlay"
    assert (
        confinement.path_replacement_is_safe(underlay_before=before, underlay_after=after) is False
    )


def test_policy_rejects_broad_or_home_read_roots(tmp_path: Path) -> None:
    stage = (tmp_path / "stage").resolve()
    stage.mkdir(mode=0o700)
    with pytest.raises(confinement.ConfinementError, match="broad_or_home_read_root"):
        confinement.build_policy(
            stage_root=str(stage),
            read_roots=("/",),
            executable_paths=(os.path.realpath(sys.executable),),
        )
    with pytest.raises(confinement.ConfinementError, match="broad_or_home_read_root"):
        confinement.build_policy(
            stage_root=str(stage),
            read_roots=(str(Path.home().resolve()),),
            executable_paths=(os.path.realpath(sys.executable),),
        )
