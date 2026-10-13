"""Host-side, descriptor-bound release preparation for Gecko capture-only v2."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from memecoin_research_storage import StorageError  # noqa: E402
from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_external_clock_v1 as external_clock  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_health_v1 as health  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_scheduler as scheduler  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_service as service  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_verify as verifier  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools import brain_senses_gecko_scout_chain_v1 as chain  # noqa: E402
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v1 as usage_inventory  # noqa: E402
from tools.brain_senses_external_writer_v1 import (  # noqa: E402
    DescriptorWriter,
    canonical_json,
    sha256,
)
from tools.brain_senses_gecko_capture_only_v2_terminal_usage_v1 import (  # noqa: E402
    RECEIPT_SHA256 as A078_TERMINAL_RECEIPT_SHA256,
)

RELEASE_SCHEMA = "brain-senses-gecko-capture-only-v2-host-release-v1"
CUTOVER_PREFIX_SHA256 = "cd7c67de3657eed073bbe1e1a8f570da9753976057299e0df3a24402ee1d2ac6"
CUTOVER_LAST_DISPATCH_NS = 1_791_843_444_749_851_000
ZERO_HASH = "0" * 64
CONFIG_PATH = Path.home() / ".config/brain-research/storage-v1.json"
RELEASE_CODE_PATHS = (
    "tools/brain_senses_gecko_capture_only_v2_host.py",
    "tools/brain_senses_gecko_capture_only_v2_runtime.py",
    "tools/brain_senses_gecko_capture_only_v2_service.py",
    "tools/brain_senses_gecko_capture_only_v2_scheduler.py",
    "tools/brain_senses_gecko_capture_only_v2_health_v1.py",
    "tools/brain_senses_gecko_capture_only_v2_verify.py",
    "tools/brain_senses_gecko_scout_chain_v1.py",
    "tools/brain_senses_gecko_rotating_scout_v1.py",
    "tools/brain_senses_gecko_shared_usage_v1.py",
    "tools/brain_senses_gecko_usage_inventory_v1.py",
    "tools/brain_senses_capacity_coordinator_v2.py",
    "tools/brain_senses_external_clock_v1.py",
    "tools/brain_senses_external_writer_v1.py",
    "tools/brain_senses_gecko_lane_engine_v3.py",
    "tools/brain_senses_gecko_lane_live_sources_v2.py",
    "tools/brain_senses_gecko_lane_policy_v3.py",
    "tools/brain_senses_gecko_lane_verify_v4.py",
    "tools/brain_senses_gecko_lane_runtime_v4.py",
    "tools/brain_senses_gecko_capture_only_v2_terminal_usage_v3.py",
    "tools/brain_senses_gecko_capture_only_v2_terminal_usage_v1.py",
    "docs/evidence/gecko-capture-only-v2-a078-terminal-usage-2026-10-13.json",
    "src/memecoin_research_storage/__init__.py",
    "docs/evidence/gecko-v4-usage-reconciliation-2026-10-12.json",
)


class HostReleaseError(ValueError):
    """Invalid release identity or unsafe one-time initialization."""


A078_TERMINAL_RECEIPT_PATH = (
    "docs/evidence/gecko-capture-only-v2-a078-terminal-usage-2026-10-13.json"
)


def _release_source_map(policy_source_map: Mapping[str, str]) -> dict[str, str]:
    """Bind every release input, including the exact a078 bridge and terminal receipt."""
    source_map = dict(policy_source_map)
    for relative in RELEASE_CODE_PATHS:
        raw = (REPO / relative).read_bytes()
        if relative == A078_TERMINAL_RECEIPT_PATH:
            if sha256(raw) != A078_TERMINAL_RECEIPT_SHA256:
                raise HostReleaseError("a078_terminal_receipt_source_pin_mismatch")
        source_map[relative] = sha256(raw)
    return source_map


def compile_release_material(writer: DescriptorWriter, guard: Any) -> dict[str, Any]:
    """Recompile the frozen train-only population and bind exact code/evidence bytes."""
    if str(REPO / "tools") not in sys.path:
        sys.path.insert(0, str(REPO / "tools"))
    from tools import brain_senses_gecko_lane_live_sources_v2 as live_sources
    from tools import brain_senses_gecko_lane_policy_v3 as source_policy

    source_bytes: dict[str, bytes] = {}
    prefix = "/Volumes/Kingston/BrainData/"
    for name in ("train_enrollment", "train_role_manifest"):
        source_path = source_policy.SOURCE_PATHS[name]
        if source_path.startswith(prefix):
            relative = source_path[len(prefix) :]
        elif not Path(source_path).is_absolute() and ".." not in Path(source_path).parts:
            relative = source_path
        else:
            raise HostReleaseError("pinned_source_path_invalid")
        guard.revalidate()
        source_bytes[name] = writer.read_bytes(relative, max_bytes=32 * 1024 * 1024)
        guard.revalidate()
    pinned = live_sources.verify_source_binding(
        writer,
        expected_root_id=guard.root_id,
        expected_volume_uuid=guard.volume_uuid,
    )
    guard.revalidate()
    policy = scout.compile_pinned_population(
        enrollment_bytes=source_bytes["train_enrollment"],
        role_manifest_bytes=source_bytes["train_role_manifest"],
        plan_bytes=pinned.plan_bytes,
        selection_bytes=pinned.selection_bytes,
    )
    receipt_path = REPO / "docs/evidence/gecko-v4-usage-reconciliation-2026-10-12.json"
    receipt = json.loads(receipt_path.read_bytes())
    markers = receipt.get("dispatch_markers")
    if (
        receipt.get("root_prefix_sha256") != CUTOVER_PREFIX_SHA256
        or not isinstance(markers, list)
        or not markers
        or not isinstance(markers[-1], Mapping)
        or markers[-1].get("dispatch_ns") != CUTOVER_LAST_DISPATCH_NS
    ):
        raise HostReleaseError("cutover_receipt_pin_invalid")
    source_map = _release_source_map(policy["source"]["source_sha256"])
    release_id, release_sha, source_sha = release_identity(
        policy=policy, source_bindings=source_map
    )
    return {
        "policy": policy,
        "source_bindings": source_map,
        "release_id": release_id,
        "release_sha256": release_sha,
        "source_sha256": source_sha,
        "provider_io": False,
    }


def initialize_release(
    writer: DescriptorWriter,
    *,
    policy: Mapping[str, Any],
    release_id: str,
    release_sha256: str,
    source_sha256: str,
    first_bucket_close_ns: int,
    coordinator: capacity.CapacityCoordinator,
    now_ns: Callable[[], int] = time.time_ns,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> dict[str, Any]:
    """Create the plan-only root and bounded health sidecar exactly once.

    This function makes no provider requests and refuses to repair or adopt any present root.
    """
    if (
        type(release_id) is not str
        or re.fullmatch(r"[0-9a-f]{32}", release_id) is None
        or type(release_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", release_sha256) is None
        or type(source_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
        or release_id != release_sha256[:32]
    ):
        raise HostReleaseError("release_identity_invalid")
    root = f"{chain.ROOT_PREFIX}/{release_id}"
    health_root = f"{health.ROOT_PREFIX}/{release_id}"
    if _directory_exists(writer, root) or _directory_exists(writer, health_root):
        raise HostReleaseError("release_root_already_exists")
    storage_stat = os.fstat(writer.root_fd)
    coordinator_binding = coordinator.root_binding
    storage = {
        "root_id": coordinator_binding["root_id"],
        "volume_uuid": str(coordinator_binding["volume_uuid"]).lower(),
        "device": storage_stat.st_dev,
        "inode": storage_stat.st_ino,
    }
    if (
        coordinator_binding.get("device") != storage_stat.st_dev
        or coordinator_binding.get("inode") != storage_stat.st_ino
    ):
        raise HostReleaseError("release_storage_binding_invalid")
    journal = chain.DescriptorScoutJournal(
        writer,
        root=root,
        policy=dict(policy),
        storage_binding=storage,
        release_sha256=release_sha256,
    )
    with coordinator._locks("gecko"):
        _ledger_raw, coordinator_events = coordinator._load_ledger()
        allocated, free = coordinator._snapshot()
    if not coordinator_events:
        raise HostReleaseError("release_coordinator_ledger_empty")
    initialized = journal.initialize(first_bucket_close_ns=first_bucket_close_ns)
    from tools.brain_senses_gecko_capture_only_v2_runtime import DescriptorCaptureWriter

    capture_writer = DescriptorCaptureWriter(writer, root=root, binding=journal.binding)
    request_prefix = capture_writer.prefix_sha256()
    fields = {
        "release_sha256": release_sha256,
        "source_sha256": source_sha256,
        "binding_sha256": journal.binding["binding_sha256"],
        "root_id": storage["root_id"],
        "volume_uuid": storage["volume_uuid"],
        "device": storage["device"],
        "inode": storage["inode"],
        "observed_ns": now_ns(),
        "monotonic_ns": monotonic_ns(),
        "request_prefix_sha256": request_prefix,
        "coordinator_policy_sha256": sha256(coordinator.policy_bytes),
        "coordinator_sequence": coordinator_events[-1]["seq"],
        "coordinator_tail_sha256": coordinator_events[-1]["event_sha256"],
        "cutover_prefix_sha256": CUTOVER_PREFIX_SHA256,
        "cutover_last_dispatch_ns": CUTOVER_LAST_DISPATCH_NS,
        "last_dispatch_ns": None,
        "bucket_close_ns": None,
        "plan_sequence": None,
        "plan_record_sha256": ZERO_HASH,
        "cursor": 0,
        "selected_count": 0,
        "intent_count": 0,
        "received_count": 0,
        "durable_count": 0,
        "eligible_new_open_count": 0,
        "missing_count": 0,
        "daily_request_count": 0,
        "daily_response_bytes": 0,
        "rolling_request_count": 0,
        "rolling_response_bytes": 0,
        "allocated_bytes": allocated["gecko"],
        "free_bytes": free,
        "cooldown_until_ns": None,
        "last_progress_ns": now_ns(),
        "last_error_code": None,
    }
    snapshot = health.initialize_snapshot(
        writer,
        release_id=release_id,
        storage=storage,
        fields=fields,
    )
    return {
        "schema": RELEASE_SCHEMA,
        "release_id": release_id,
        "release_sha256": release_sha256,
        "source_sha256": source_sha256,
        "root": root,
        "health_root": health_root,
        "first_bucket_close_ns": first_bucket_close_ns,
        "plan_genesis_sha256": initialized["genesis_sha256"],
        "health_snapshot_sha256": snapshot["snapshot_sha256"],
        "provider_io": False,
        "activation_authorized": False,
    }


def release_identity(
    *, policy: Mapping[str, Any], source_bindings: Mapping[str, str]
) -> tuple[str, str, str]:
    """Return deterministic release id, digest, and source-map digest."""
    if not source_bindings or any(
        type(name) is not str
        or type(digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        for name, digest in source_bindings.items()
    ):
        raise HostReleaseError("release_source_bindings_invalid")
    source_hash = sha256(canonical_json(dict(sorted(source_bindings.items()))))
    core = {
        "schema": RELEASE_SCHEMA,
        "schema_version": 1,
        "policy_sha256": policy.get("policy_sha256"),
        "source_sha256": dict(sorted(source_bindings.items())),
        "cutover_prefix_sha256": CUTOVER_PREFIX_SHA256,
        "cutover_last_dispatch_ns": CUTOVER_LAST_DISPATCH_NS,
        "provider": "geckoterminal_public_api",
        "slot_count": scout.SLOTS_PER_BUCKET,
    }
    digest = sha256(canonical_json(core))
    return digest[:32], digest, source_hash


def render_launchd_plist(release_id: str) -> bytes:
    """Render the fixed path-only 12-second LaunchAgent for a verified release."""
    if re.fullmatch(r"[0-9a-f]{32}", release_id) is None:
        raise HostReleaseError("release_id_invalid")
    label = f"ai.memecoin.brain.senses.gecko-capture-only-v2-{release_id}"
    document = {
        "Label": label,
        "ProgramArguments": [
            str(REPO / ".venv/bin/python"),
            "-B",
            str(REPO / "tools/brain_senses_gecko_capture_only_v2_host.py"),
            "--scheduled-tick",
            "--release-id",
            release_id,
        ],
        "WorkingDirectory": str(REPO),
        "StartInterval": 12,
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Background",
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    return plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=True)


def verify_launchd_plist(raw: bytes, release_id: str) -> str:
    """Require exact installed bytes for one release; reject extra env/credential fields."""
    expected = render_launchd_plist(release_id)
    if raw != expected:
        raise HostReleaseError("launchd_plist_binding_changed")
    return sha256(raw)


def launchd_label(release_id: str) -> str:
    if re.fullmatch(r"[0-9a-f]{32}", release_id) is None:
        raise HostReleaseError("release_id_invalid")
    return f"ai.memecoin.brain.senses.gecko-capture-only-v2-{release_id}"


def launchd_agent_path(release_id: str) -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{launchd_label(release_id)}.plist"


def loaded_launchd_labels() -> set[str]:
    result = subprocess.run(
        ["launchctl", "list"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise HostReleaseError("launchd_list_failed")
    labels = set()
    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if fields:
            labels.add(fields[-1])
    return labels


def verify_launchd_loaded(release_id: str) -> str:
    label = launchd_label(release_id)
    labels = loaded_launchd_labels()
    competing = {
        value
        for value in labels
        if value.startswith("ai.memecoin.brain.senses.gecko") and value != label
    }
    if label not in labels or competing:
        raise HostReleaseError("launchd_label_state_invalid")
    path = launchd_agent_path(release_id)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise HostReleaseError("launchd_plist_file_unsafe")
    raw = path.read_bytes()
    return verify_launchd_plist(raw, release_id)


def install_launchd_agent(release_id: str) -> dict[str, Any]:
    """Install and bootstrap the exact nonexpiring 12-second path-only LaunchAgent."""
    path = launchd_agent_path(release_id)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    parent_info = path.parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid():
        raise HostReleaseError("launchd_directory_unsafe")
    raw = render_launchd_plist(release_id)
    competing = {
        value
        for value in loaded_launchd_labels()
        if value.startswith("ai.memecoin.brain.senses.gecko") and value != launchd_label(release_id)
    }
    if competing:
        raise HostReleaseError("competing_gecko_launchd_agent_loaded")
    if path.exists():
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise HostReleaseError("launchd_plist_file_unsafe")
        existing = path.read_bytes()
        verify_launchd_plist(existing, release_id)
    else:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            view = memoryview(raw)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise HostReleaseError("launchd_plist_write_failed")
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
    domain = f"gui/{os.getuid()}"
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0 and launchd_label(release_id) not in loaded_launchd_labels():
        raise HostReleaseError("launchd_bootstrap_failed")
    plist_sha256 = verify_launchd_loaded(release_id)
    return {
        "label": launchd_label(release_id),
        "plist_path": str(path),
        "plist_sha256": plist_sha256,
        "start_interval_seconds": 12,
        "provider_io": False,
    }


def install_from_owner_host(release_id: str) -> dict[str, Any]:
    """Install only after source-bound policy/root and health replay both verify."""
    from memecoin_research_storage import _open_relative_directory, writer_guard
    from tools import brain_senses_gecko_lane_runtime_v4 as v4_host
    from tools.brain_senses_external_writer_v1 import DescriptorWriter

    with writer_guard(CONFIG_PATH) as guard:
        data_root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)

        def before_write() -> None:
            guard.revalidate()

        try:
            writer = DescriptorWriter(data_root_fd, before_write=before_write)
        finally:
            os.close(data_root_fd)
        try:
            _coordinator = v4_host.make_capacity_coordinator(writer, guard, now_ns=time.time_ns)
            verify_prepared_release(writer, guard, release_id)
            guard.revalidate()
            return install_launchd_agent(release_id)
        finally:
            writer.close()


def read_capture_state(writer: DescriptorWriter, root: str) -> dict[str, Any]:
    """Read canonical local v2 records without using display timestamps as dispatch authority."""
    state: dict[str, Any] = {}
    for directory in ("intents", "results", "confirmations", "skips"):
        path = f"{root}/{directory}"
        if not _directory_exists(writer, path):
            state[directory] = []
            continue
        rows: list[dict[str, Any]] = []
        for name in writer.list_names(path):
            if not name.endswith(".json"):
                raise HostReleaseError("capture_state_name_invalid")
            raw = writer.read_bytes(f"{path}/{name}", max_bytes=8 * 1024 * 1024)
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HostReleaseError("capture_state_json_invalid") from exc
            if type(row) is not dict or canonical_json(row) != raw:
                raise HostReleaseError("capture_state_noncanonical")
            rows.append(row)
        state[directory] = rows
    dispatch_path = f"{root}/dispatch.jsonl"
    if not writer.exists(dispatch_path):
        state["dispatches"] = []
        return state
    raw_dispatch = writer.read_bytes(dispatch_path, max_bytes=16 * 1024 * 1024)
    if raw_dispatch and not raw_dispatch.endswith(b"\n"):
        raise HostReleaseError("capture_dispatch_torn")
    dispatches: list[dict[str, Any]] = []
    for line in raw_dispatch.splitlines():
        try:
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HostReleaseError("capture_dispatch_json_invalid") from exc
        if type(row) is not dict or canonical_json(row).rstrip(b"\n") != line:
            raise HostReleaseError("capture_dispatch_noncanonical")
        dispatches.append(row)
    state["dispatches"] = dispatches
    return state


def recovery_gate(state: Mapping[str, Any]) -> str:
    """Require every possible dispatch to have a terminal and durable confirmation."""
    dispatches = state.get("dispatches")
    results = state.get("results")
    confirmations = state.get("confirmations")
    if type(dispatches) is not list or type(results) is not list or type(confirmations) is not list:
        raise HostReleaseError("capture_state_shape_invalid")
    dispatched = {row.get("request_id") for row in dispatches}
    terminal = {row.get("request_id") for row in results}
    confirmed = {row.get("request_id") for row in confirmations}
    if dispatched - terminal:
        return "possibly_spent_unresolved"
    if terminal - confirmed:
        return "durable_confirmation_missing"
    return "recovered"


def publish_health(
    writer: DescriptorWriter,
    *,
    release: Mapping[str, Any],
    coordinator: capacity.CapacityCoordinator,
    capture_writer: Any,
    plan: Mapping[str, Any],
    state: Mapping[str, Any],
    inventory: Mapping[str, Any] | None,
    status: str,
    reason: str,
    stop_latched: bool = False,
    cooldown_until_ns: int | None = None,
    last_error_code: str | None = None,
    now_ns: Callable[[], int] = time.time_ns,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> dict[str, Any]:
    """Refresh bounded health from verified request/coordinator projections."""
    release_id = str(release["release_id"])
    storage = release["binding"]["storage"]
    old = health.read_snapshot(writer, release_id=release_id, storage=storage)
    if old["stop_latched"]:
        if not stop_latched or status != "stopped":
            return old
    observed = now_ns()
    capture_state = dict(state)
    intents = capture_state.get("intents", [])
    results = capture_state.get("results", [])
    confirmations = capture_state.get("confirmations", [])
    dispatches = capture_state.get("dispatches", [])
    selection = plan.get("selection")
    selected_pools = (
        selection.get("selected_pool_ids", []) if isinstance(selection, Mapping) else []
    )
    plan_sequence = plan.get("sequence")
    plan_intents = [
        row
        for row in intents
        if isinstance(row.get("request"), Mapping)
        and row["request"].get("plan_sequence") == plan_sequence
    ]
    plan_ids = {row.get("request_id") for row in plan_intents}
    plan_results = [row for row in results if row.get("request_id") in plan_ids]
    plan_result_ids = {row.get("request_id") for row in plan_results}
    confirmed_ids = {
        row.get("request_id") for row in confirmations if row.get("request_id") in plan_result_ids
    }
    received = sum(row.get("status") in {"received", "empty_response"} for row in plan_results)
    eligible = sum(
        row.get("request_id") in confirmed_ids
        and row.get("status") == "received"
        and any(
            isinstance(bar, Mapping) and bar.get("region") == "new_open"
            for bar in row.get("bars", [])
        )
        for row in plan_results
    )
    daily_requests = daily_bytes = rolling_requests = rolling_bytes = 0
    if inventory is not None:
        attempts = [
            attempt
            for snapshot in inventory.get("snapshots", [])
            for attempt in snapshot.get("attempts", [])
        ]
        day_start = observed - observed % shared_usage.NS_PER_DAY
        rolling_start = observed - shared_usage.ROLLING_NS
        for attempt in attempts:
            at = attempt["decision_ns"]
            response_size = (
                attempt["response_bytes"]
                if attempt["response_sha256"] is not None
                else attempt["maximum_response_bytes"]
            )
            if day_start <= at <= observed:
                daily_requests += 1
                daily_bytes += response_size
            if rolling_start <= at <= observed:
                rolling_requests += 1
                rolling_bytes += response_size
    with coordinator.lane_lock("gecko", blocking=False) as lane:
        with coordinator._shared_lock("gecko", lane):
            _ledger_raw, events = coordinator._load_ledger()
            allocated, free = coordinator._snapshot()
    fields = {
        key: value
        for key, value in old.items()
        if key
        not in {
            "schema",
            "schema_version",
            "release_id",
            "generation",
            "observed_ns",
            "monotonic_ns",
            "transition_count",
            "transition_tail_sha256",
            "snapshot_sha256",
        }
    }
    dispatch_times = [
        row["dispatch_ns"] for row in dispatches if type(row.get("dispatch_ns")) is int
    ]
    fields.update(
        observed_ns=observed,
        monotonic_ns=monotonic_ns(),
        status=status,
        reason=reason,
        stop_latched=stop_latched,
        request_prefix_sha256=capture_writer.prefix_sha256(),
        coordinator_policy_sha256=sha256(coordinator.policy_bytes),
        coordinator_sequence=events[-1]["seq"],
        coordinator_tail_sha256=events[-1]["event_sha256"],
        last_dispatch_ns=max(dispatch_times) if dispatch_times else old["last_dispatch_ns"],
        bucket_close_ns=plan.get("bucket_close_ns"),
        plan_sequence=plan_sequence,
        plan_record_sha256=plan.get("record_sha256", ZERO_HASH),
        cursor=plan.get("cursor_out", plan.get("cursor", 0)),
        selected_count=len(selected_pools),
        intent_count=len(plan_intents),
        received_count=received,
        durable_count=len(confirmed_ids),
        eligible_new_open_count=eligible,
        missing_count=max(0, len(selected_pools) - len(plan_intents)),
        daily_request_count=daily_requests,
        daily_response_bytes=daily_bytes,
        rolling_request_count=rolling_requests,
        rolling_response_bytes=rolling_bytes,
        allocated_bytes=allocated["gecko"],
        free_bytes=free,
        cooldown_until_ns=cooldown_until_ns,
        last_progress_ns=observed,
        last_error_code=last_error_code,
    )
    return health.publish_snapshot(
        writer,
        release_id=release_id,
        storage=storage,
        fields=fields,
    )


def verify_prepared_release(
    writer: DescriptorWriter, guard: Any, release_id: str
) -> dict[str, Any]:
    """Recompile and replay an existing release; missing or changed pins fail closed."""
    if re.fullmatch(r"[0-9a-f]{32}", release_id) is None:
        raise HostReleaseError("release_id_invalid")
    material = compile_release_material(writer, guard)
    if material["release_id"] != release_id:
        raise HostReleaseError("release_source_or_code_changed")
    root = f"{chain.ROOT_PREFIX}/{release_id}"
    policy_raw = writer.read_bytes(f"{root}/policy.json", max_bytes=2 * 1024 * 1024)
    binding_raw = writer.read_bytes(f"{root}/binding.json", max_bytes=8192)
    try:
        stored_policy = json.loads(policy_raw)
        stored_binding = json.loads(binding_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostReleaseError("release_binding_json_invalid") from exc
    if (
        type(stored_policy) is not dict
        or canonical_json(stored_policy) != policy_raw
        or canonical_json(stored_policy) != canonical_json(material["policy"])
        or type(stored_binding) is not dict
        or canonical_json(stored_binding) != binding_raw
    ):
        raise HostReleaseError("release_binding_changed")
    storage = stored_binding.get("storage")
    if type(storage) is not dict:
        raise HostReleaseError("release_storage_binding_invalid")
    journal = chain.DescriptorScoutJournal(
        writer,
        root=root,
        policy=material["policy"],
        storage_binding=storage,
        release_sha256=material["release_sha256"],
    )
    if canonical_json(journal.binding) != binding_raw:
        raise HostReleaseError("release_binding_changed")
    records = journal.records()
    chain_state = chain.verify_chain(material["policy"], records)
    snapshot = health.read_snapshot(
        writer,
        release_id=release_id,
        storage=storage,
    )
    if (
        snapshot["release_sha256"] != material["release_sha256"]
        or snapshot["source_sha256"] != material["source_sha256"]
        or snapshot["binding_sha256"] != stored_binding["binding_sha256"]
    ):
        raise HostReleaseError("release_health_binding_changed")
    return {
        **material,
        "root": root,
        "health_root": f"{health.ROOT_PREFIX}/{release_id}",
        "binding": stored_binding,
        "journal": journal,
        "chain_state": chain_state,
        "health": snapshot,
        "provider_io": False,
    }


def attempt_one_slot(
    writer: DescriptorWriter,
    *,
    coordinator: capacity.CapacityCoordinator,
    capture_writer: Any,
    policy: Mapping[str, Any],
    binding: Mapping[str, Any],
    plan: Mapping[str, Any],
    cooldown_until_ns: int | None = None,
    now_ns: Callable[[], int] = time.time_ns,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    clock_probe: Callable[[], dict[str, Any]] = external_clock.probe_pair,
    fetch_once: Callable[[Mapping[str, Any]], service.FetchResult] = service.fetch_once,
) -> dict[str, Any]:
    """Admit and execute at most one fixed slot under the shared lane and v2 root locks."""
    close_ns = plan.get("bucket_close_ns")
    if type(close_ns) is not int:
        raise HostReleaseError("plan_bucket_invalid")
    try:
        lane_context = coordinator.lane_lock("gecko", blocking=False)
        with lane_context as lane:
            first_now = now_ns()
            state = read_capture_state(writer, capture_writer.root)
            recovery_status = recovery_gate(state)
            if recovery_status != "recovered":
                return {
                    "status": recovery_status,
                    "provider_io": False,
                    "manual_reconciliation_required": True,
                }
            intents_by_id = {row.get("request_id"): row for row in state["intents"]}
            cooldown_stops: list[tuple[int, int]] = []
            for outcome in state["results"]:
                bound_intent = intents_by_id.get(outcome.get("request_id"))
                bound_request = bound_intent.get("request", {}) if bound_intent else {}
                if (
                    bound_request.get("plan_sequence") == plan.get("sequence")
                    and outcome.get("stop_reason") is not None
                ):
                    return {
                        "status": "bucket_stopped",
                        "provider_io": False,
                        "cooldown_until_ns": outcome.get("cooldown_until_ns"),
                        "stop_reason": outcome.get("stop_reason"),
                    }
                if (
                    outcome.get("stop_reason")
                    in {"rate_limited", "provider_error", "transport_uncertain"}
                    and type(outcome.get("receipt_ns")) is int
                    and type(outcome.get("cooldown_until_ns")) is int
                    and type(bound_request.get("bucket_close_ns")) is int
                ):
                    cooldown_stops.append((outcome["receipt_ns"], bound_request["bucket_close_ns"]))
            if cooldown_stops:
                last_stop_receipt, last_stop_bucket = max(cooldown_stops)
                if (
                    close_ns > last_stop_bucket
                    and cooldown_until_ns is not None
                    and first_now >= cooldown_until_ns
                ):
                    later_intents = [
                        row
                        for row in state["intents"]
                        if type(row.get("decision_ns")) is int
                        and row["decision_ns"] > last_stop_receipt
                        and type(row.get("request")) is dict
                        and type(row["request"].get("bucket_close_ns")) is int
                    ]
                    if later_intents:
                        probe = min(later_intents, key=lambda row: row["decision_ns"])
                        if probe["request"]["bucket_close_ns"] == close_ns:
                            return {
                                "status": "bucket_stopped",
                                "provider_io": False,
                                "cooldown_until_ns": cooldown_until_ns,
                                "stop_reason": "recovery_probe_already_used",
                            }
            slot = scheduler.due_slot(plan, state["intents"], state["skips"], now_ns=first_now)
            if slot is None:
                if (
                    len(scheduler.used_slots(plan, state["intents"], state["skips"]))
                    == scout.SLOTS_PER_BUCKET
                ):
                    return {"status": "bucket_complete", "provider_io": False}
                return {"status": "outside_window_or_slots_spent", "provider_io": False}
            if cooldown_until_ns is not None and first_now < cooldown_until_ns:
                return {
                    "status": "cooldown",
                    "provider_io": False,
                    "cooldown_until_ns": cooldown_until_ns,
                }
            clock_record = clock_probe()
            decision_ns = now_ns()
            decision_monotonic_ns = monotonic_ns()
            external_clock.validate_progress(
                clock_record,
                wall_ns=decision_ns,
                monotonic_ns=decision_monotonic_ns,
            )
            request = verifier.expected_request(policy, str(binding["binding_sha256"]), plan, slot)
            inventory = usage_inventory.build_usage_inventory(writer)
            old_root = "services/gecko-market-v1/capture-only-v1/f4412b7a531d4e3cb3414a2862b8ee76"
            old_snapshot = next(
                (row for row in inventory["snapshots"] if row["root"] == old_root), None
            )
            if (
                old_snapshot is None
                or old_snapshot["status"] != "verified_prefix"
                or old_snapshot["source_prefix_sha256"] != CUTOVER_PREFIX_SHA256
            ):
                raise HostReleaseError("cutover_root_changed")
            roots = [row["root"] for row in inventory["snapshots"]]
            admission = shared_usage.assess_usage(
                inventory["snapshots"],
                expected_roots=roots,
                checked_ns=decision_ns,
                prospective_request_id=request["request_id"],
                prospective_maximum_response_bytes=scout.MAX_RESPONSE_BYTES,
            )
            if not admission["admitted"]:
                return {
                    "status": "usage_cap",
                    "provider_io": False,
                    "reasons": admission["reasons"],
                    "admission": admission,
                }
            snapshot = next(
                row for row in inventory["snapshots"] if row["root"] == capture_writer.root
            )
            try:
                runner_context = capture_writer.runner_lock(
                    coordinator=coordinator,
                    lane_lease=lane,
                    expected_prefix_sha256=snapshot["source_prefix_sha256"],
                )
                with runner_context as runner:
                    state = read_capture_state(writer, capture_writer.root)
                    slot = scheduler.due_slot(
                        plan, state["intents"], state["skips"], now_ns=now_ns()
                    )
                    pre_dispatch_ns = now_ns()
                    if (
                        slot is None
                        or pre_dispatch_ns >= close_ns + verifier.REQUEST_START_END_NS
                        or close_ns + verifier.REQUEST_START_END_NS - pre_dispatch_ns
                        < 25 * scout.NS
                    ):
                        if (
                            len(scheduler.used_slots(plan, state["intents"], state["skips"]))
                            == scout.SLOTS_PER_BUCKET
                        ):
                            return {"status": "bucket_complete", "provider_io": False}
                        if (
                            pre_dispatch_ns >= close_ns + verifier.REQUEST_START_END_NS
                            or close_ns + verifier.REQUEST_START_END_NS - pre_dispatch_ns
                            < 25 * scout.NS
                        ):
                            return {"status": "no_intent_window_expired", "provider_io": False}
                        return {"status": "outside_window_or_slots_spent", "provider_io": False}
                    external_clock.validate_progress(
                        clock_record,
                        wall_ns=pre_dispatch_ns,
                        monotonic_ns=monotonic_ns(),
                    )
                    if not scheduler.dispatch_allowed(
                        pre_dispatch_ns,
                        state["dispatches"],
                        cutover_last_dispatch_ns=CUTOVER_LAST_DISPATCH_NS,
                    ):
                        return {"status": "pacing_wait", "provider_io": False}
                    intent = verifier.build_intent(
                        root=capture_writer.root,
                        policy=policy,
                        binding=binding,
                        plan_record=plan,
                        slot_index=slot,
                        decision_ns=decision_ns,
                        decision_monotonic_ns=decision_monotonic_ns,
                        clock_record=clock_record,
                        admission=admission,
                    )
                    capture_writer.initialize_attempt_directories(runner)
                    reservation = capture_writer.reserve_intent(
                        runner,
                        coordinator=coordinator,
                        lane_lease=lane,
                        intent=intent,
                    )
                    dispatch_ns = now_ns()
                    if (
                        dispatch_ns < close_ns + scout.DISPATCH_EARLIEST_NS
                        or dispatch_ns >= close_ns + verifier.REQUEST_START_END_NS
                        or not scheduler.dispatch_allowed(
                            dispatch_ns,
                            state["dispatches"],
                            cutover_last_dispatch_ns=CUTOVER_LAST_DISPATCH_NS,
                        )
                    ):
                        return {
                            "status": "intent_spent_without_dispatch",
                            "provider_io": False,
                            "request_id": request["request_id"],
                        }
                    dispatch = capture_writer.append_dispatch(
                        runner,
                        request_id=intent["request_id"],
                        intent_sha256=intent["intent_sha256"],
                        dispatch_ns=dispatch_ns,
                    )
                    request_started_ns = now_ns()
                    fetched = fetch_once(intent["request"])
                    receipt_ns = now_ns()
                    terminal, ambiguous = service.terminal_envelope(
                        intent=intent,
                        dispatch=dispatch,
                        request_started_ns=request_started_ns,
                        receipt_ns=receipt_ns,
                        fetched=fetched,
                    )
                    terminal_event = capture_writer.settle_outcome(
                        runner,
                        coordinator=coordinator,
                        lane_lease=lane,
                        reservation=reservation,
                        outcome=terminal,
                        ambiguous=ambiguous,
                    )
                    availability_ns = now_ns()
                    availability_monotonic_ns = monotonic_ns()
                    capture_writer.write_confirmation(
                        runner,
                        request_id=intent["request_id"],
                        outcome_sha256=sha256(canonical_json(terminal)),
                        availability_ns=availability_ns,
                        availability_monotonic_ns=availability_monotonic_ns,
                        availability_clock=clock_record,
                        coordinator_terminal=terminal_event,
                    )
                    return {
                        "status": terminal["status"],
                        "provider_io": True,
                        "request_id": intent["request_id"],
                        "dispatch_ns": dispatch_ns,
                        "request_started_ns": request_started_ns,
                        "receipt_ns": receipt_ns,
                        "availability_ns": availability_ns,
                        "response_bytes": terminal["response_bytes"],
                        "cooldown_until_ns": terminal["cooldown_until_ns"],
                        "stop_reason": terminal["stop_reason"],
                    }
            except StorageError as exc:
                if "active" in str(exc) or "lock" in str(exc):
                    return {"status": "root_busy", "provider_io": False}
                raise
            except capacity.CoordinatorError as exc:
                code = (
                    exc.args[0]
                    if exc.args and isinstance(exc.args[0], str)
                    else "coordinator_error"
                )
                if code in {"gecko_capacity_exceeded", "coordinator_capacity_exceeded"}:
                    return {"status": "physical_cap", "provider_io": False}
                if code == "shared_free_reserve_insufficient":
                    return {"status": "storage_floor", "provider_io": False}
                raise
    except StorageError as exc:
        if "active" in str(exc) or "lock" in str(exc):
            return {"status": "lane_busy", "provider_io": False}
        raise


def mark_expired_slots(
    writer: DescriptorWriter,
    *,
    coordinator: capacity.CapacityCoordinator,
    capture_writer: Any,
    plan: Mapping[str, Any],
    now_ns: int,
) -> int:
    """Persist only explicit no-request misses for an expired plan under lane/root locks."""
    try:
        with coordinator.lane_lock("gecko", blocking=False) as lane:
            inventory = usage_inventory.build_usage_inventory(writer)
            snapshot = next(
                row for row in inventory["snapshots"] if row["root"] == capture_writer.root
            )
            with capture_writer.runner_lock(
                coordinator=coordinator,
                lane_lease=lane,
                expected_prefix_sha256=snapshot["source_prefix_sha256"],
            ) as runner:
                latest = read_capture_state(writer, capture_writer.root)
                skips = scheduler.expired_slot_skips(
                    plan,
                    latest["intents"],
                    latest["skips"],
                    now_ns=now_ns,
                )
                if not skips:
                    return 0
                capture_writer.initialize_attempt_directories(runner)
                for row in skips:
                    capture_writer.append_missed_skip(
                        runner,
                        plan=plan,
                        slot_index=row["slot_index"],
                        at_ns=row["at_ns"],
                    )
                return len(skips)
    except StorageError as exc:
        if "active" in str(exc) or "lock" in str(exc):
            return 0
        raise


def target_bucket_close(now_ns: int) -> int:
    """Choose only the open or next future bucket, never a past acquisition window."""
    if type(now_ns) is not int or now_ns < 0:
        raise HostReleaseError("clock_invalid")
    close = now_ns - now_ns % scout.BUCKET_NS
    return close if now_ns < close + verifier.REQUEST_START_END_NS else close + scout.BUCKET_NS


def run_callback(
    writer: DescriptorWriter,
    *,
    release: Mapping[str, Any],
    coordinator: capacity.CapacityCoordinator,
    now_ns: Callable[[], int] = time.time_ns,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    clock_probe: Callable[[], dict[str, Any]] = external_clock.probe_pair,
    fetch_once: Callable[[Mapping[str, Any]], service.FetchResult] = service.fetch_once,
) -> dict[str, Any]:
    """Run one 12-second callback; it can issue at most one provider GET and never queues."""
    if release.get("health", {}).get("stop_latched") is True:
        return {"status": "manual_reconciliation_required", "provider_io": False}
    policy = release["policy"]
    root = str(release["root"])
    journal = release["journal"]
    binding = release["binding"]
    from tools.brain_senses_gecko_capture_only_v2_runtime import DescriptorCaptureWriter

    capture_writer = DescriptorCaptureWriter(writer, root=root, binding=binding)
    observed = now_ns()
    close_ns = observed - observed % scout.BUCKET_NS
    records = journal.records()
    window_open = (
        close_ns + scout.DISPATCH_EARLIEST_NS <= observed < close_ns + verifier.REQUEST_START_END_NS
        and close_ns >= records[0].get("first_bucket_close_ns", close_ns)
    )
    last = records[-1]
    if (
        window_open
        and last.get("schema") == chain.PLAN_SCHEMA
        and type(last.get("bucket_close_ns")) is int
        and last["bucket_close_ns"] < close_ns
        and observed >= last["bucket_close_ns"] + verifier.REQUEST_START_END_NS
    ):
        expired_plan = dict(last)
        expired_state = read_capture_state(writer, root)
        health_snapshot = publish_health(
            writer,
            release=release,
            coordinator=coordinator,
            capture_writer=capture_writer,
            plan=expired_plan,
            state=expired_state,
            inventory=None,
            status="paused",
            reason="outside_window",
            cooldown_until_ns=release["health"].get("cooldown_until_ns"),
            now_ns=now_ns,
            monotonic_ns=monotonic_ns,
        )
        required_misses = scheduler.expired_slot_skips(
            expired_plan,
            expired_state["intents"],
            expired_state["skips"],
            now_ns=observed,
        )
        missed = mark_expired_slots(
            writer,
            coordinator=coordinator,
            capture_writer=capture_writer,
            plan=expired_plan,
            now_ns=observed,
        )
        if required_misses and missed != len(required_misses):
            return {
                "status": "missed_slot_persistence_waiting",
                "provider_io": False,
                "required_misses": len(required_misses),
            }
        if missed:
            expired_state = read_capture_state(writer, root)
            publish_health(
                writer,
                release=release,
                coordinator=coordinator,
                capture_writer=capture_writer,
                plan=expired_plan,
                state=expired_state,
                inventory=None,
                status="paused",
                reason="outside_window",
                cooldown_until_ns=health_snapshot.get("cooldown_until_ns"),
                now_ns=now_ns,
                monotonic_ns=monotonic_ns,
            )
        records = journal.records()
        last = records[-1]
    if window_open:
        if last.get("schema") == chain.PLAN_SCHEMA and last.get("bucket_close_ns") == close_ns:
            plan = dict(last)
        else:
            plan = journal.ensure_plan_for_current_bucket(
                now_ns=observed,
                bucket_close_ns=close_ns,
            )
    elif last.get("schema") == chain.PLAN_SCHEMA and last.get("bucket_close_ns") == close_ns:
        plan = dict(last)
    elif last.get("schema") == chain.GAP_SCHEMA and last.get("bucket_close_ns") == close_ns:
        plan = dict(last)
    elif observed >= close_ns + verifier.REQUEST_START_END_NS and close_ns >= records[0].get(
        "first_bucket_close_ns", close_ns
    ):
        try:
            journal.ensure_plan_for_current_bucket(now_ns=observed, bucket_close_ns=close_ns)
        except chain.ChainError as exc:
            if exc.args != ("current_bucket_window_elapsed",):
                raise
        plan = journal.records()[-1]
    else:
        plan = dict(last)
    state = read_capture_state(writer, root)
    recovery_status = recovery_gate(state)
    if recovery_status != "recovered":
        publish_health(
            writer,
            release=release,
            coordinator=coordinator,
            capture_writer=capture_writer,
            plan=plan,
            state=state,
            inventory=None,
            status="stopped",
            reason="manual_reconciliation_required",
            stop_latched=True,
            last_error_code=recovery_status,
            now_ns=now_ns,
            monotonic_ns=monotonic_ns,
        )
        return {"status": recovery_status, "provider_io": False}
    existing_health = release["health"]
    status = "healthy" if window_open else "paused"
    reason = (
        "resumed"
        if window_open and existing_health.get("status") != "healthy"
        else "none"
        if window_open
        else "outside_window"
    )
    snapshot = publish_health(
        writer,
        release=release,
        coordinator=coordinator,
        capture_writer=capture_writer,
        plan=plan,
        state=state,
        inventory=None,
        status=status,
        reason=reason,
        cooldown_until_ns=existing_health.get("cooldown_until_ns"),
        now_ns=now_ns,
        monotonic_ns=monotonic_ns,
    )
    if not window_open:
        expired_count = 0
        if (
            plan.get("schema") == chain.PLAN_SCHEMA
            and type(plan.get("bucket_close_ns")) is int
            and observed >= plan["bucket_close_ns"] + verifier.REQUEST_START_END_NS
        ):
            expired_count = mark_expired_slots(
                writer,
                coordinator=coordinator,
                capture_writer=capture_writer,
                plan=plan,
                now_ns=observed,
            )
            if expired_count:
                state = read_capture_state(writer, root)
                snapshot = publish_health(
                    writer,
                    release=release,
                    coordinator=coordinator,
                    capture_writer=capture_writer,
                    plan=plan,
                    state=state,
                    inventory=None,
                    status="paused",
                    reason="outside_window",
                    cooldown_until_ns=snapshot.get("cooldown_until_ns"),
                    now_ns=now_ns,
                    monotonic_ns=monotonic_ns,
                )
        return {
            "status": (
                "bucket_complete"
                if plan.get("schema") == chain.PLAN_SCHEMA
                and len(scheduler.used_slots(plan, state["intents"], state["skips"]))
                == scout.SLOTS_PER_BUCKET
                else "outside_window"
            ),
            "provider_io": False,
            "explicit_missed_slots": expired_count,
            "health": snapshot,
        }
    result = attempt_one_slot(
        writer,
        coordinator=coordinator,
        capture_writer=capture_writer,
        policy=policy,
        binding=binding,
        plan=plan,
        cooldown_until_ns=snapshot.get("cooldown_until_ns"),
        now_ns=now_ns,
        monotonic_ns=monotonic_ns,
        clock_probe=clock_probe,
        fetch_once=fetch_once,
    )
    state_after = read_capture_state(writer, root)
    recovery_after = recovery_gate(state_after)
    post_inventory = usage_inventory.build_usage_inventory(writer)
    terminal_status = result.get("status")
    stop_latched = (
        recovery_after != "recovered"
        or terminal_status
        in {
            "client_or_access_error",
            "malformed_response",
            "response_over_budget",
        }
        or result.get("stop_reason") == "review_required_retry_after"
    )
    if stop_latched:
        health_status, health_reason = "stopped", "manual_reconciliation_required"
        error_code = recovery_after if recovery_after != "recovered" else str(terminal_status)
    elif terminal_status in {
        "rate_limited",
        "provider_error",
        "transport_uncertain_possibly_spent",
        "cooldown",
        "bucket_stopped",
    }:
        health_status, health_reason = "paused", "cooldown"
        error_code = str(result.get("stop_reason") or terminal_status)
    elif terminal_status == "usage_cap":
        reasons = result.get("reasons", [])
        health_reason = (
            "byte_cap" if any("byte" in str(item) for item in reasons) else "request_cap"
        )
        health_status, error_code = "paused", health_reason
    elif terminal_status == "physical_cap":
        health_status, health_reason, error_code = "paused", "physical_cap", "physical_cap"
    elif terminal_status == "storage_floor":
        health_status, health_reason, error_code = "paused", "storage_floor", "storage_floor"
    elif terminal_status in {"root_busy", "lane_busy", "pacing_wait"}:
        health_status, health_reason, error_code = "paused", "busy", str(terminal_status)
    elif terminal_status == "no_intent_window_expired":
        health_status, health_reason, error_code = "paused", "outside_window", None
    elif terminal_status == "bucket_complete":
        health_status, health_reason, error_code = "paused", "bucket_complete", None
    elif terminal_status in {"intent_spent_without_dispatch", "window_expired_after_census"}:
        health_status, health_reason, error_code = (
            "stopped",
            "manual_reconciliation_required",
            str(terminal_status),
        )
        stop_latched = True
    elif terminal_status == "outside_window_or_slots_spent":
        health_status, health_reason, error_code = "paused", "outside_window", None
    else:
        health_status, health_reason, error_code = "healthy", "none", None
    refreshed = publish_health(
        writer,
        release=release,
        coordinator=coordinator,
        capture_writer=capture_writer,
        plan=plan,
        state=state_after,
        inventory=post_inventory,
        status=health_status,
        reason=health_reason,
        stop_latched=stop_latched,
        cooldown_until_ns=result.get("cooldown_until_ns") or snapshot.get("cooldown_until_ns"),
        last_error_code=error_code,
        now_ns=now_ns,
        monotonic_ns=monotonic_ns,
    )
    return {**result, "health": refreshed}


def prepare_from_owner_host() -> dict[str, Any]:
    """Build one inert, source-pinned release under the configured storage descriptor."""
    from memecoin_research_storage import _open_relative_directory, writer_guard
    from tools import brain_senses_gecko_lane_runtime_v4 as v4_host
    from tools.brain_senses_external_writer_v1 import DescriptorWriter

    with writer_guard(CONFIG_PATH) as guard:
        data_root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)

        def before_write() -> None:
            guard.revalidate()

        try:
            writer = DescriptorWriter(data_root_fd, before_write=before_write)
        finally:
            os.close(data_root_fd)
        try:
            material = compile_release_material(writer, guard)
            coordinator = v4_host.make_capacity_coordinator(writer, guard, now_ns=time.time_ns)
            observed_ns = time.time_ns()
            first_close = (observed_ns // scout.BUCKET_NS + 1) * scout.BUCKET_NS
            receipt = initialize_release(
                writer,
                policy=material["policy"],
                release_id=material["release_id"],
                release_sha256=material["release_sha256"],
                source_sha256=material["source_sha256"],
                first_bucket_close_ns=first_close,
                coordinator=coordinator,
                now_ns=time.time_ns,
                monotonic_ns=time.monotonic_ns,
            )
            guard.revalidate()
            return {
                **receipt,
                "source_bindings": material["source_bindings"],
                "provider_io": False,
                "activation_authorized": False,
            }
        finally:
            writer.close()


def run_from_owner_host(release_id: str) -> dict[str, Any]:
    """Verify the loaded release, Kingston binding, and LaunchAgent before one callback."""
    from memecoin_research_storage import _open_relative_directory, writer_guard
    from tools import brain_senses_gecko_lane_runtime_v4 as v4_host
    from tools.brain_senses_external_writer_v1 import DescriptorWriter

    with writer_guard(CONFIG_PATH) as guard:
        data_root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)

        def before_write() -> None:
            guard.revalidate()

        try:
            writer = DescriptorWriter(data_root_fd, before_write=before_write)
        finally:
            os.close(data_root_fd)
        try:
            coordinator = v4_host.make_capacity_coordinator(writer, guard, now_ns=time.time_ns)
            release = verify_prepared_release(writer, guard, release_id)
            verify_launchd_loaded(release_id)
            result = run_callback(writer, release=release, coordinator=coordinator)
            guard.revalidate()
            return result
        finally:
            writer.close()


def _directory_exists(writer: DescriptorWriter, path: str) -> bool:
    try:
        writer.list_names(path)
    except FileNotFoundError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepare-release",
        action="store_true",
        help="create the source-pinned plan-only root and health snapshot; never dispatch",
    )
    parser.add_argument("--scheduled-tick", action="store_true")
    parser.add_argument("--install-launchd", action="store_true")
    parser.add_argument("--release-id")
    args = parser.parse_args(argv)
    selected_modes = sum(
        (bool(args.prepare_release), bool(args.scheduled_tick), bool(args.install_launchd))
    )
    if selected_modes != 1:
        parser.error(
            "choose exactly one of --prepare-release, --install-launchd, or --scheduled-tick"
        )
    if args.prepare_release == (args.release_id is not None):
        parser.error("--release-id is required only with --install-launchd or --scheduled-tick")
    try:
        result = (
            prepare_from_owner_host()
            if args.prepare_release
            else install_from_owner_host(args.release_id)
            if args.install_launchd
            else run_from_owner_host(args.release_id)
        )
    except Exception as exc:
        code = exc.args[0] if exc.args and isinstance(exc.args[0], str) else type(exc).__name__
        status = (
            "prepare_failed"
            if args.prepare_release
            else "launchd_failed"
            if args.install_launchd
            else "tick_failed"
        )
        print(json.dumps({"status": status, "reason": code}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


__all__ = [
    "CUTOVER_LAST_DISPATCH_NS",
    "CUTOVER_PREFIX_SHA256",
    "HostReleaseError",
    "RELEASE_SCHEMA",
    "compile_release_material",
    "initialize_release",
    "install_from_owner_host",
    "install_launchd_agent",
    "main",
    "prepare_from_owner_host",
    "release_identity",
    "run_callback",
    "run_from_owner_host",
    "verify_launchd_loaded",
]


if __name__ == "__main__":
    raise SystemExit(main())
