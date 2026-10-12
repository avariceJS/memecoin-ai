"""Create and verify the two-stage no-provider Gecko v4 release gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from memecoin_research_storage import _open_relative_directory, writer_guard  # noqa: E402
from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_gecko_lane_runtime_v3 as runtime  # noqa: E402
from tools.brain_senses_external_writer_v1 import (  # noqa: E402
    DescriptorWriter,
    canonical_json,
    sha256,
)

CONFIG_PATH = Path.home() / ".config/brain-research/storage-v1.json"
PILOT_PLIST = "ops/launchd/ai.memecoin.brain.senses.gecko-lane-v3.plist"
INSTALLED_PLIST = Path.home() / "Library/LaunchAgents/ai.memecoin.brain.senses.gecko-lane-v3.plist"
TEST_PATHS = tuple(sorted({node.split("::", 1)[0] for node in runtime.RELEASE_TEST_NODES}))
LABEL = "ai.memecoin.brain.senses.gecko-lane-v3"


class ReleaseError(RuntimeError):
    """Fail-closed release qualification error."""


def _canonical_plist_argv(pilot_id: str) -> list[str]:
    return [
        str(REPO / ".venv/bin/python"),
        "-B",
        str(REPO / "tools/brain_senses_gecko_lane_runtime_v3.py"),
        "--scheduled-tick",
        "--pilot-id",
        pilot_id,
    ]


def _verify_plist(pilot_id: str) -> str:
    import plistlib

    path = REPO / PILOT_PLIST
    try:
        canonical_bytes = path.read_bytes()
        installed_bytes = INSTALLED_PLIST.read_bytes()
        value = plistlib.loads(canonical_bytes)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ReleaseError("launchd_plist_invalid") from exc
    if installed_bytes != canonical_bytes:
        raise ReleaseError("installed_launchd_plist_mismatch")
    if (
        value.get("Label") != LABEL
        or value.get("ProgramArguments") != _canonical_plist_argv(pilot_id)
        or value.get("StartInterval") != 60
        or value.get("RunAtLoad") is not True
        or value.get("KeepAlive") is not False
        or "StartCalendarInterval" in value
        or value.get("StandardOutPath") != "/dev/null"
        or value.get("StandardErrorPath") != "/dev/null"
    ):
        raise ReleaseError("launchd_plist_policy")
    return sha256(canonical_bytes)


def _test_hashes() -> dict[str, str]:
    return {name: sha256((REPO / name).read_bytes()) for name in TEST_PATHS}


def _run_qualification() -> dict[str, Any]:
    allowed_environment = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
    env = {key: value for key, value in os.environ.items() if key in allowed_environment}
    command = [str(REPO / ".venv/bin/python"), "-m", "pytest", "-q", *runtime.RELEASE_TEST_NODES]
    completed = subprocess.run(
        command,
        cwd=REPO,
        env=env,
        capture_output=True,
        timeout=240,
        check=False,
    )
    return {
        "test_nodes": list(runtime.RELEASE_TEST_NODES),
        "test_source_sha256": _test_hashes(),
        "pytest_exit_code": completed.returncode,
        "pytest_stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "pytest_stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
    }


def _open_store(writer: DescriptorWriter, guard: Any, pilot_id: str):
    store = runtime.DescriptorLaneStore(writer, guard=guard, root=runtime._pilot_root(pilot_id))
    plan = store.read_json("plan.json")
    runtime.verify_live_plan(plan)
    store.bind_verified_plan(plan)
    store._verify_guard(plan)
    if plan["source_sha256"] != runtime._source_bindings_for_store(writer, guard):
        raise ReleaseError("source_binding_changed")
    receipt_raw = writer.read_bytes(store._path("pilot-receipt.json"))
    receipt = store.read_json("pilot-receipt.json")
    runtime.verify_pilot_receipt(plan, receipt, sha256(receipt_raw))
    if receipt["summary"].get("pilot_qualification", {}).get("status") != "passed":
        raise ReleaseError("pilot_not_qualified")
    summary = runtime.verify_live_store(store, plan, require_clock=True)
    if (
        summary["intents"] != 4
        or summary.get("release_qualification_allowed") is not True
        or summary["dispatches"] != 4
        or summary["durable_results"] != 4
        or summary["possibly_spent"] != 0
    ):
        raise ReleaseError("pilot_prefix_not_qualified")
    return store, plan, receipt_raw, receipt


def _coordinator_sha(writer: DescriptorWriter, guard: Any) -> str:
    coordinator = runtime.make_capacity_coordinator(writer, guard)
    with writer.lock(capacity.LOCK_PATH, blocking=True):
        raw, rows = coordinator._load_ledger()
        reservations, terminal_ids = capacity._reservation_state(rows)
        if any(
            row["service"] == "gecko" and reservation_id not in terminal_ids
            for reservation_id, row in reservations.items()
        ):
            raise ReleaseError("gecko_coordinator_reservation_pending")
    return sha256(raw)


def _create_preflight(
    store, plan, receipt_raw: bytes, receipt: dict[str, Any], writer, guard
) -> dict[str, Any]:
    path = store._path("release-preflight.json")
    if writer.exists(path):
        raise ReleaseError("release_preflight_already_exists")
    plist_sha = _verify_plist(plan["pilot_id"])
    if plan["source_sha256"].get(PILOT_PLIST) != plist_sha:
        raise ReleaseError("launchd_plist_source_binding")
    qualification = _run_qualification()
    if (
        qualification["pytest_exit_code"] != 0
        or qualification["test_source_sha256"] != _test_hashes()
    ):
        raise ReleaseError("release_qualification_failed")
    generated = time.time_ns()
    snapshot = guard.revalidate()
    if snapshot.available_bytes < plan["policy"]["capacity"]["minimum_free_bytes"]:
        raise ReleaseError("storage_floor")
    value = {
        "schema": runtime.RELEASE_PREFLIGHT_SCHEMA,
        "schema_version": 4,
        "pilot_id": plan["pilot_id"],
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "pilot_receipt_sha256": sha256(receipt_raw),
        "root_id": plan["storage"]["root_id"],
        "volume_uuid": plan["storage"]["volume_uuid"],
        "plist_sha256": plist_sha,
        "builder_sha256": plan["source_sha256"]["tools/brain_senses_gecko_lane_release_v4.py"],
        "qualification_sha256": qualification,
        "service_prefix_sha256": runtime._service_prefix_sha256(store),
        "coordinator_ledger_sha256": _coordinator_sha(writer, guard),
        "generated_ns": generated,
        "expires_ns": generated + 15 * 60 * runtime.offline.NS,
        "status": "passed",
    }
    value["receipt_sha256"] = sha256(canonical_json(value))
    runtime._verify_scheduled_release(plan, value, sha256(receipt_raw), receipt, store)
    store._create_exact("release-preflight.json", value)
    return {
        "status": "release_preflight_created",
        "provider_io": False,
        "preflight_sha256": sha256(canonical_json(value)),
    }


def _activate(store, plan, receipt_raw, receipt, writer, guard) -> dict[str, Any]:
    preflight_raw = writer.read_bytes(store._path("release-preflight.json"))
    preflight = store.read_json("release-preflight.json")
    runtime._verify_scheduled_release(plan, preflight, sha256(receipt_raw), receipt, store)
    callback_raw = writer.read_bytes(store._path("launchd-callback.json"))
    callback = store.read_json("launchd-callback.json")
    runtime._verify_launchd_callback(plan, preflight, preflight_raw, callback, callback_raw, store)
    if time.time_ns() > preflight["expires_ns"]:
        raise ReleaseError("release_preflight_expired")
    if runtime._service_prefix_sha256(store) != preflight["service_prefix_sha256"]:
        raise ReleaseError("service_prefix_changed_before_activation")
    if _coordinator_sha(writer, guard) != preflight["coordinator_ledger_sha256"]:
        raise ReleaseError("coordinator_prefix_changed_before_activation")
    path = store._path("activation.json")
    if writer.exists(path):
        raise ReleaseError("activation_already_exists")
    activation = {
        "schema": runtime.ACTIVATION_SCHEMA,
        "schema_version": 4,
        "pilot_id": plan["pilot_id"],
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "root_id": plan["storage"]["root_id"],
        "volume_uuid": plan["storage"]["volume_uuid"],
        "release_preflight_sha256": sha256(preflight_raw),
        "launchd_callback_sha256": sha256(callback_raw),
        "activation_authorized": True,
        "provider_io": True,
        "qualification_status": "passed",
    }
    runtime.verify_activation(plan, activation)
    store._create_exact("activation.json", activation)
    return {
        "status": "activation_created",
        "provider_io": False,
        "activation_sha256": sha256(canonical_json(activation)),
    }


def run(mode: str, pilot_id: str) -> dict[str, Any]:
    if mode not in {"create-preflight", "activate"} or not runtime._is_pilot_id(pilot_id):
        raise ReleaseError("release_arguments_invalid")
    with writer_guard(CONFIG_PATH) as guard:
        data_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
        try:
            writer = DescriptorWriter(data_fd, before_write=guard.revalidate)
        finally:
            os.close(data_fd)
        try:
            store, plan, receipt_raw, receipt = _open_store(writer, guard, pilot_id)
            return (
                _create_preflight(store, plan, receipt_raw, receipt, writer, guard)
                if mode == "create-preflight"
                else _activate(store, plan, receipt_raw, receipt, writer, guard)
            )
        finally:
            writer.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--create-preflight", action="store_true")
    modes.add_argument("--activate", action="store_true")
    parser.add_argument("--pilot-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = run("create-preflight" if args.create_preflight else "activate", args.pilot_id)
    except Exception as exc:  # sanitized: provider values and subprocess output are never emitted
        result = {
            "status": "failed_closed",
            "reason": str(exc) if isinstance(exc, ReleaseError) else "release_gate_failure",
            "provider_io": False,
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") in {"release_preflight_created", "activation_created"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
