"""Prepare exact Git code and switch a quiescent operator; never restore state backups.

Run on the runtime owner using its existing interpreter. Preparation runs tests but
does not stop services. Activation requires a paused operator and no unexpired exams.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import fcntl
import hashlib
import io
import json
import os
import plistlib
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from worker_artifacts import plain, read_json, sha
from worker_preflight import NoRedirect

LABEL = "ai.memecoin.brain.operator"


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def replace(path, raw):
    fd, name = tempfile.mkstemp(prefix=path.name + ".release-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def tree(root):
    result = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise ValueError("release_symlink")
        if p.is_file():
            result[str(p.relative_to(root))] = sha(p)
    return result


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def prepare(repo, revision, output):
    repo, output = plain(repo), plain(output)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("exact_git_revision_required")
    actual = git(repo, "rev-parse", revision + "^{commit}").decode().strip()
    if actual != revision:
        raise ValueError("git_revision_mismatch")
    output.mkdir(parents=True, exist_ok=False)
    code = output / "code"
    code.mkdir()
    archive = git(repo, "archive", "--format=zip", revision)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for info in zipped.infolist():
            name = Path(info.filename)
            if (
                name.is_absolute()
                or ".." in name.parts
                or (info.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("release_archive_path")
            if info.is_dir():
                continue
            dest = code / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("xb") as stream:
                stream.write(zipped.read(info))
    before = tree(code)
    env = dict(
        os.environ,
        PYTHONPATH=str(code / "src"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    checks = [
        [sys.executable, "-m", "ruff", "check", "--no-cache", "src", "tests", "tools"],
        [sys.executable, "-m", "ruff", "format", "--check", "--no-cache", "src", "tests", "tools"],
        [
            sys.executable,
            "-m",
            "mypy",
            "--cache-dir",
            str(output / "mypy-cache"),
            "src/memecoin_ai",
            "src/memecoin_registry",
            "src/memecoin_research",
            "src/memecoin_runner/contracts.py",
            "src/memecoin_runner/ledger.py",
            "src/memecoin_runner/scoring.py",
            "src/memecoin_sampling",
            "src/memecoin_collector",
            "src/memecoin_operational",
        ],
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        [sys.executable, "tools/smoke.py"],
    ]
    results = []
    for i, command in enumerate(checks):
        with (output / f"check-{i}.log").open("xb") as log:
            done = subprocess.run(
                command,
                cwd=code,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=1800,
                check=False,
            )
        results.append(dict(command=command, exit_code=done.returncode, log=f"check-{i}.log"))
        if done.returncode:
            write(output / "FAILED.json", dict(revision=revision, checks=results))
            raise ValueError("release_check_failed")
    # Smoke creates its own new synthetic run outputs under work; they are not release code.
    after = {name: pin for name, pin in tree(code).items() if not name.startswith("work/")}
    if before != after:
        raise ValueError("release_code_changed_by_checks")
    receipt = dict(
        schema=1,
        revision=revision,
        code=before,
        python_sha256=sha(Path(sys.executable).resolve()),
        checks=results,
        check_hashes={r["log"]: sha(output / r["log"]) for r in results},
        prepared_ns=time.time_ns(),
        activated=False,
    )
    write(output / "release.json", receipt)
    return receipt


def verify_release(release):
    release = plain(release)
    receipt = read_json(release / "release.json")
    code = {n: h for n, h in tree(release / "code").items() if not n.startswith("work/")}
    if receipt["schema"] != 1 or receipt["code"] != code:
        raise ValueError("release_code_integrity")
    if receipt["python_sha256"] != sha(Path(sys.executable).resolve()):
        raise ValueError("release_interpreter_changed")
    if any(r["exit_code"] != 0 for r in receipt["checks"]) or len(receipt["checks"]) != 5:
        raise ValueError("release_tests_missing")
    if any(sha(release / name) != pin for name, pin in receipt["check_hashes"].items()):
        raise ValueError("release_test_evidence_changed")
    return receipt


def pinned_call(code, script, *args):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys;sys.path.insert(0,sys.argv.pop(1));" + script,
            str(code),
            *map(str, args),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise ValueError("release_compatibility_failed")
    return json.loads(result.stdout)


def bind(release, root, bundle):
    receipt = verify_release(release)
    root, bundle = plain(root), plain(bundle)
    prior = read_json(root / "deployment.json")
    script = (
        "import json;from pathlib import Path;"
        "from memecoin_operator.deployment import guard,stage;"
        "r=Path(sys.argv[1]);old=guard(r);"
        "b=json.loads((Path(old['bundle'])/'binding.json').read_text());"
        "print(json.dumps(stage(r,Path(sys.argv[2]),"
        "historical_training_corpora=b.get('historical_training_corpora',[]),activate=False)))"
    )
    proposed = pinned_call(release / "code" / "src", script, root, bundle)
    record = dict(
        schema=1,
        revision=receipt["revision"],
        release=str(release.resolve()),
        release_sha256=sha(release / "release.json"),
        root=str(root),
        previous=prior,
        proposed=proposed,
    )
    write(bundle.parent / (bundle.name + "-update.json"), record)
    return record


def quiescent_state(root, *, allow_expired=False):
    """Read one transaction, without schema creation or a second SQLite writer."""
    config = read_json(root / "config.json")
    if not allow_expired and time.time_ns() >= config["expires_ns"]:
        raise ValueError("release_operator_expired")
    for path in root.glob("paper/*/config.json"):
        if read_json(path).get("expires_ns", 2**63 - 1) > time.time_ns():
            raise ValueError("release_unexpired_exam")
    db = sqlite3.connect((root / "operator.sqlite").as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("BEGIN")
        mode = db.execute("SELECT mode FROM revisions ORDER BY revision DESC LIMIT 1").fetchone()
        if mode is None or mode[0] not in {"paused", "stopped"}:
            raise ValueError("release_requires_pause")
        if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("release_active_job")
        # A logical digest covers every row, including reservations, events and incidents.
        logical = hashlib.sha256("\n".join(db.iterdump()).encode()).hexdigest()
    finally:
        db.close()
    files = {"config.json": sha(root / "config.json")}
    for pattern in (
        "STOP",
        "incident-*.json",
        "paper/*/config.json",
        "paper/*/deployment.json",
        "paper/*/ledger/*.json",
        "paper/*/STOP",
    ):
        for path in root.glob(pattern):
            files[str(path.relative_to(root))] = sha(plain(path))
    source = Path(config["source_root"])
    return dict(database=logical, files=files, source_config=sha(source / "config.json"))


@contextlib.contextmanager
def maintenance_locks(root):
    with contextlib.ExitStack() as stack:
        paths = [root / "operator/owner.lock", *sorted(root.glob("background/locks/*.lock"))]
        for path in paths:
            handle = stack.enter_context(plain(path).open("a"))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("release_runtime_busy") from None
        yield


def switch_stopped(record, plist, journal, rollback=False, *, recovery=False):
    """Only pointers change. Caller must have unloaded the operator service."""
    root = plain(Path(record["root"]))
    target = record["previous"] if rollback else record["proposed"]
    expected = record["proposed"] if rollback else record["previous"]
    plist, journal = plain(plist), plain(journal)
    with maintenance_locks(root):
        before = quiescent_state(root, allow_expired=recovery)
        if read_json(root / "deployment.json") != expected:
            raise ValueError("release_previous_changed")
        for revision in (target, expected):
            script = (
                "import json;from pathlib import Path;"
                "from memecoin_operator.deployment import guard_revision;"
                "print(json.dumps(guard_revision(Path(sys.argv[1]),json.loads(sys.argv[2]))))"
            )
            pinned_call(Path(revision["bundle"]) / "runtime", script, root, json.dumps(revision))
        schemas = []
        for revision in (target, expected):
            module = Path(revision["bundle"]) / "runtime/memecoin_operator/control.py"
            definitions = [
                n
                for n in ast.parse(module.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == "_schema"
            ]
            if len(definitions) != 1:
                raise ValueError("release_schema_unknown")
            schemas.append(ast.dump(definitions[0], include_attributes=False))
        if schemas[0] != schemas[1]:
            raise ValueError("release_schema_migration_not_supported")
        old_plist = plist.read_bytes()
        value = plistlib.loads(old_plist)
        old_path = str(Path(expected["bundle"]) / "runtime")
        args = value["ProgramArguments"]
        if value["Label"] != LABEL or args.count(old_path) != 1:
            raise ValueError("release_launchagent_binding")
        if Path(args[0]).resolve() != Path(sys.executable).resolve():
            raise ValueError("release_launchagent_interpreter")
        value["ProgramArguments"] = [
            str(Path(target["bundle"]) / "runtime") if a == old_path else a for a in args
        ]
        journal.mkdir(parents=True, exist_ok=False)
        write(
            journal / "intent.json",
            dict(
                previous=expected,
                proposed=target,
                rollback=rollback,
                preserved=before,
                at_ns=time.time_ns(),
            ),
        )
        (journal / "previous.plist").write_bytes(old_plist)
        try:
            replace(root / "deployment.json", (json.dumps(target) + "\n").encode())
            replace(plist, plistlib.dumps(value))
            if quiescent_state(root, allow_expired=recovery) != before:
                raise ValueError("release_state_changed")
            write(journal / "switched.json", dict(deployment=target, at_ns=time.time_ns()))
        except Exception:
            replace(root / "deployment.json", (json.dumps(expected) + "\n").encode())
            replace(plist, old_plist)
            write(journal / "failed-restored.json", dict(at_ns=time.time_ns()))
            raise
    return target


def owns_listener(domain, port, target):
    from memecoin_operator.host import _parse_job

    job = subprocess.run(
        ["launchctl", "print", domain + "/" + LABEL],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    info, parsed = _parse_job(job.stdout)
    if job.returncode or not parsed or info["state"] != "running" or not info["pid"]:
        return False
    listening = subprocess.run(
        ["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    pids = {line[1:] for line in listening.stdout.splitlines() if line.startswith("p")}
    process = subprocess.run(
        ["ps", "-p", str(info["pid"]), "-o", "command="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return (
        listening.returncode == process.returncode == 0
        and pids == {str(info["pid"])}
        and str(Path(target["bundle"]) / "runtime") in process.stdout
    )


def activate(record_path, journal, rollback=False):
    journal = plain(journal)
    if journal.exists():
        raise ValueError("release_journal_exists")
    record = read_json(plain(record_path))
    release = Path(record["release"])
    verify_release(release)
    if sha(release / "release.json") != record["release_sha256"]:
        raise ValueError("release_receipt_changed")
    root = plain(Path(record["root"]))
    quiescent_state(root)
    plist = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    expected = record["proposed"] if rollback else record["previous"]
    if read_json(root / "deployment.json") != expected:
        raise ValueError("release_previous_changed")
    registered = plistlib.loads(plist.read_bytes())
    if registered.get("Label") != LABEL or str(
        Path(expected["bundle"]) / "runtime"
    ) not in registered.get("ProgramArguments", []):
        raise ValueError("release_launchagent_binding")
    domain = f"gui/{os.getuid()}"
    # Require a registered service, so a typo cannot create a new production installation.
    subprocess.run(
        ["launchctl", "print", domain + "/" + LABEL], capture_output=True, check=True, timeout=10
    )
    subprocess.run(
        ["launchctl", "bootout", domain + "/" + LABEL], capture_output=True, check=True, timeout=20
    )
    try:
        target = switch_stopped(record, plist, journal, rollback)
    except Exception as switch_error:
        journal.mkdir(parents=True, exist_ok=True)
        failed = dict(error=type(switch_error).__name__, restored_bootstrap_exit=None)
        try:
            current = read_json(root / "deployment.json")
            value = plistlib.loads(plist.read_bytes())
            if (
                current != expected
                or str(Path(expected["bundle"]) / "runtime") not in value["ProgramArguments"]
            ):
                failed["manual_recovery_required"] = True
            elif time.time_ns() >= read_json(root / "config.json")["expires_ns"]:
                failed["expired_left_unloaded"] = True
            else:
                restored = subprocess.run(
                    ["launchctl", "bootstrap", domain, str(plist)],
                    capture_output=True,
                    check=False,
                    timeout=20,
                )
                failed["restored_bootstrap_exit"] = restored.returncode
        except Exception as recovery_error:
            failed["recovery_error"] = type(recovery_error).__name__
            raise
        finally:
            write(journal / "switch-failed.json", failed)
        raise
    try:
        subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist)],
            capture_output=True,
            check=True,
            timeout=20,
        )
        port = read_json(root / "config.json")["port"]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        ready = None
        for _ in range(20):
            try:
                with opener.open(f"http://127.0.0.1:{port}/api/status", timeout=2) as response:
                    ready = json.load(response)
                if (
                    ready.get("schema") == "operator-status-v1"
                    and ready.get("ok")
                    and ready.get("mode") in {"paused", "stopped"}
                    and owns_listener(domain, port, target)
                ):
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        else:
            raise ValueError("release_health_failed")
        write(journal / "healthy.json", dict(at_ns=time.time_ns(), mode=ready["mode"]))
    except Exception:
        recovery_result = dict(restored_bootstrap_exit=None, recovery_error=None, phase="bootout")
        try:
            unloaded = subprocess.run(
                ["launchctl", "bootout", domain + "/" + LABEL],
                capture_output=True,
                check=False,
                timeout=20,
            )
            if unloaded.returncode:
                still_registered = subprocess.run(
                    ["launchctl", "print", domain + "/" + LABEL],
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                if still_registered.returncode == 0 or b"Could not find service" not in (
                    still_registered.stderr or b""
                ):
                    raise ValueError("release_recovery_unregister_unconfirmed")
            recovery_result["phase"] = "restore_pointers"
            switch_stopped(record, plist, journal / "recovery", not rollback, recovery=True)
            if time.time_ns() < read_json(root / "config.json")["expires_ns"]:
                recovery_result["phase"] = "bootstrap_previous"
                result = subprocess.run(
                    ["launchctl", "bootstrap", domain, str(plist)],
                    capture_output=True,
                    check=False,
                    timeout=20,
                )
                recovery_result["restored_bootstrap_exit"] = result.returncode
            else:
                recovery_result["expired_left_unloaded"] = True
            recovery_result["phase"] = "finished"
        except Exception as recovery_error:
            recovery_result["recovery_error"] = type(recovery_error).__name__
            raise
        finally:
            write(journal / "startup-failed.json", recovery_result)
        raise
    return dict(deployment=target, journal=str(journal), health_check="passed", rollback=rollback)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("revision")
    prep.add_argument("output", type=Path)
    prep.add_argument("--repo", type=Path, default=Path.cwd())
    binder = sub.add_parser("bind")
    binder.add_argument("release", type=Path)
    binder.add_argument("root", type=Path)
    binder.add_argument("bundle", type=Path)
    for name in ("activate", "rollback"):
        action = sub.add_parser(name)
        action.add_argument("record", type=Path)
        action.add_argument("journal", type=Path)
    a = p.parse_args()
    if a.command == "prepare":
        result = prepare(a.repo, a.revision, a.output)
    elif a.command == "bind":
        result = bind(a.release.resolve(), a.root, a.bundle)
    else:
        result = activate(a.record, a.journal, a.command == "rollback")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
