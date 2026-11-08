"""No-inference qualification of the installed Codex native permissions profile.

Only disposable sentinels are read. Reports contain hashes and verdicts, never
command output, home configuration, credentials, account details, or tokens.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from .codex_rpc import Codex


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _inspect_schema(codex: str, root: Path) -> dict:
    """Require the actual installed RPC contract before issuing any RPC probes."""
    destination = root / "schema"
    result = subprocess.run(
        [codex, "app-server", "generate-json-schema", "--experimental", "--out", str(destination)],
        cwd=root,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("native_qualification_schema_generation_failed")
    request_bytes = (destination / "v2/CommandExecParams.json").read_bytes()
    response_bytes = (destination / "v2/CommandExecResponse.json").read_bytes()
    request, response = json.loads(request_bytes), json.loads(response_bytes)
    required = {"command", "cwd", "permissionProfile", "timeoutMs", "outputBytesCap"}
    if not required <= set(request.get("properties", {})):
        raise RuntimeError("native_qualification_rpc_request_contract_changed")
    if not {"exitCode", "stdout", "stderr"} <= set(response.get("required", [])):
        raise RuntimeError("native_qualification_rpc_response_contract_changed")
    return {"request_sha256": _hash(request_bytes), "response_sha256": _hash(response_bytes)}


def _persist(path: Path, report: dict) -> None:
    path = path.absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".native-qualification-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(report, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def qualify_native_profile(
    base_dir: str | Path, *, codex: str = "codex", evidence_path: str | Path | None = None
) -> dict:
    """Probe production adapter's configured and default native policy, without a model.

    Every command uses installed command/exec. No thread/start, turn/start, model,
    account, remote endpoint, or credential request is made. The parent may persist
    this hashed receipt outside all worker roots and reject qualified=false.
    """
    executable = shutil.which(codex)
    report = {
        "schema": "brain-native-permission-qualification-v1",
        "observed_at": time.time(),
        "qualified": False,
        "no_inference": True,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "uid": os.getuid(),
        "profile": "org-native",
        "checks": [],
        "adapter_sha256": _hash(Path(__file__).with_name("codex_rpc.py").read_bytes()),
    }
    try:
        if not executable:
            raise RuntimeError("native_qualification_codex_unavailable")
        resolved = Path(executable).resolve(strict=True)
        report["executable_sha256"] = _hash(resolved.read_bytes())
        version = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        if not version.startswith("codex-cli ") or len(version) > 100:
            raise RuntimeError("native_qualification_unexpected_codex_version")
        report["version"] = version
        with ExitStack() as stack:
            temporary = stack.enter_context(
                tempfile.TemporaryDirectory(prefix="native-boundary-", dir=base_dir)
            )
            root = Path(temporary).resolve()
            work, outside, harness = (root / name for name in ("work", "outside", "harness"))
            for directory in (work, outside, harness):
                directory.mkdir()
            allowed_marker = "allowed-disposable-" + os.urandom(12).hex()
            forbidden_marker = "denied-disposable-" + os.urandom(12).hex()
            allowed_file, forbidden_file = work / "readable", outside / "private-sentinel"
            allowed_file.write_text(allowed_marker)
            forbidden_file.write_text(forbidden_marker)
            (work / "escape").symlink_to(outside, target_is_directory=True)
            extra_canaries = []
            # Always test both global /tmp and Darwin's per-user temporary root,
            # even when deployment itself lives safely outside either root.
            canary_parents = [
                ("shared_tmp", "/tmp"),
                ("user_tmp", None),
                ("owner_home", Path.home()),
            ]
            if (Path.home() / ".codex").is_dir():
                canary_parents.append(("codex_home", Path.home() / ".codex"))
            for label, directory in canary_parents:
                extra = Path(
                    stack.enter_context(
                        tempfile.TemporaryDirectory(prefix="native-canary-", dir=directory)
                    )
                ).resolve()
                path = extra / "private-sentinel"
                path.write_text(forbidden_marker)
                extra_canaries.append((label, extra, path))
            report["rpc_schema"] = _inspect_schema(executable, root)
            checks = report["checks"]
            with Codex(work, codex=executable, harness_cwd=harness) as client:

                def probe(
                    name, argv, *, denied=False, expected=None, explicit=True, diagnostic=True
                ):
                    params = {
                        "command": argv,
                        "cwd": str(work),
                        "timeoutMs": 3000,
                        "outputBytesCap": 4096,
                    }
                    if explicit:
                        params["permissionProfile"] = "org-native"
                    result = client.call("command/exec", params, timeout=15)
                    stdout, stderr = result.get("stdout", ""), result.get("stderr", "")
                    status = result.get("exitCode")
                    if denied:
                        passed = (
                            type(status) is int
                            and status != 0
                            and forbidden_marker not in stdout
                            and (
                                not diagnostic
                                or any(
                                    word in stderr.lower()
                                    for word in (
                                        "operation not permitted",
                                        "permission denied",
                                        "sandbox",
                                        "not allowed",
                                    )
                                )
                            )
                        )
                    else:
                        passed = status == 0 and (expected is None or stdout.strip() == expected)
                    checks.append(
                        {
                            "name": name,
                            "passed": passed,
                            "exit_code": status,
                            "stdout_sha256": _hash(stdout.encode()),
                            "stderr_sha256": _hash(stderr.encode()),
                            "profile_explicit": explicit,
                        }
                    )

                probe(
                    "allowed_platform_command",
                    ["/bin/echo", "native-ready"],
                    expected="native-ready",
                )
                probe(
                    "allowed_checkout_read",
                    ["/bin/cat", str(allowed_file)],
                    expected=allowed_marker,
                )
                probe("deny_outside_read", ["/bin/cat", str(forbidden_file)], denied=True)
                probe(
                    "default_profile_denies_outside_read",
                    ["/bin/cat", str(forbidden_file)],
                    denied=True,
                    explicit=False,
                )
                probe(
                    "deny_symlink_escape",
                    ["/bin/cat", str(work / "escape/private-sentinel")],
                    denied=True,
                )
                for label, directory, path in extra_canaries:
                    probe("deny_" + label + "_read", ["/bin/cat", str(path)], denied=True)
                    probe(
                        "deny_" + label + "_write",
                        [
                            "/bin/sh",
                            "-c",
                            "printf forbidden > " + shlex.quote(str(directory / "new-file")),
                        ],
                        denied=True,
                    )
                for name, path in (
                    ("deny_checkout_write", work / "new-file"),
                    ("deny_outside_write", outside / "new-file"),
                    ("deny_policy_overwrite", forbidden_file),
                ):
                    probe(
                        name,
                        ["/bin/sh", "-c", "printf forbidden > " + shlex.quote(str(path))],
                        denied=True,
                    )
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    listener.listen()
                    probe(
                        "deny_active_loopback_connection",
                        [
                            "/usr/bin/nc",
                            "-v",
                            "-z",
                            "-w",
                            "1",
                            "127.0.0.1",
                            str(listener.getsockname()[1]),
                        ],
                        denied=True,
                    )
                unix_directory = next(p for name, p, _ in extra_canaries if name == "shared_tmp")
                with socket.socket(socket.AF_UNIX) as listener:
                    path = unix_directory / "control.sock"
                    listener.bind(str(path))
                    listener.listen()
                    listener.settimeout(0.1)
                    # Darwin nc -z on a UNIX socket can exit1 even after a
                    # successful connect, so exit status alone is not proof.
                    argv = ["/usr/bin/nc", "-v", "-z", "-w", "1", "-U", str(path)]
                    subprocess.run(argv, capture_output=True, timeout=3, check=False)
                    control, _ = listener.accept()
                    control.close()
                    checks.append({"name": "unix_listener_positive_control", "passed": True})
                    probe(
                        "deny_active_unix_control_connection",
                        argv,
                        denied=True,
                        diagnostic=False,
                    )
                    try:
                        connection, _ = listener.accept()
                    except TimeoutError:
                        blocked = True
                    else:
                        connection.close()
                        blocked = False
                    checks.append(
                        {"name": "unix_no_connection_reached_listener", "passed": blocked}
                    )
                checks.append(
                    {
                        "name": "outside_sentinels_unchanged",
                        "passed": (
                            forbidden_file.read_text() == forbidden_marker
                            and not (outside / "new-file").exists()
                            and not (work / "new-file").exists()
                            and all(
                                path.read_text() == forbidden_marker
                                and not (directory / "new-file").exists()
                                for _, directory, path in extra_canaries
                            )
                        ),
                    }
                )
            report["qualified"] = all(check["passed"] for check in checks)
            report["baseline_limitations"] = [
                "Darwin platform runtime reads from :minimal remain permitted",
                "Shared and per-user temporary storage ambient grants are explicitly removed",
                "This is not a cwd-only filesystem policy or protection against the host owner",
            ]
            report["prior_vulnerability"] = {
                "profile": ":minimal read plus checkout read, before temporary-root exclusions",
                "observed": "Disposable sibling /tmp canary read and write both exited zero",
                "raw_secrets_accessed": False,
            }
    except Exception as error:
        # Deliberately exclude exception text: third-party errors could contain
        # unexpected host/configuration details. Failed evidence is still useful.
        report["error_type"] = type(error).__name__
    body = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report["report_sha256"] = _hash(body)
    if evidence_path is not None:
        _persist(Path(evidence_path), report)
    return report
