"""Descriptor-bound, create-only host adapter for the v2-to-v3 capacity cutover.

The adapter is deliberately a one-shot offline migration primitive. It never
reserves capacity or performs provider work. A caller must hold the configured
StorageGuard for the whole operation and supply a DescriptorWriter rooted at
that guard's BrainData directory.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import pwd
import re
import shlex
import stat
import subprocess
import time
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from tools import brain_senses_capacity_coordinator_v2 as v2
from tools import brain_senses_capacity_coordinator_v3 as v3


class CutoverHostError(RuntimeError):
    """Storage, source, or create-only cutover failure."""


QUIESCENCE_SCHEMA = "brain-senses-capacity-coordinator-v2-quiescence-v1"
# Exact shipped labels whose current/legacy entrypoints can mutate the v2
# coordinator. Unrelated Brain/operator jobs are intentionally outside this
# migration gate.
V2_MUTATING_CLIENT_LABELS = (
    "ai.memecoin.brain.senses.external-rolling-v4",
    "ai.memecoin.brain.senses.external-rolling-v5",
    "ai.memecoin.brain.senses.external-rolling-v5r2",
    "ai.memecoin.brain.senses.gecko-capture-only-v1",
    "ai.memecoin.brain.senses.gecko-capture-only-v2",
    "ai.memecoin.brain.senses.gecko-capture-only-v3",
    "ai.memecoin.brain.senses.gecko-capture-only-v4",
    "ai.memecoin.brain.senses.gecko-lane-v3",
    "ai.memecoin.brain.senses.gecko-lane-v4",
)
V2_MUTATING_ENTRYPOINTS = frozenset(
    {
        "brain_senses_external_orchestrator_v4.py",
        "brain_senses_external_orchestrator_v5.py",
        "brain_senses_external_rolling_v5r2.py",
        "brain_senses_gecko_capture_only_runtime_v1.py",
        "brain_senses_gecko_capture_only_v2_host.py",
        "brain_senses_gecko_capture_only_v3_host.py",
        "brain_senses_gecko_capture_only_v4_host.py",
        "brain_senses_gecko_lane_runtime_v3.py",
        "brain_senses_gecko_lane_runtime_v4.py",
    }
)
V2_MUTATING_LABEL_PREFIXES = (
    "ai.memecoin.brain.senses.external-rolling-v4",
    "ai.memecoin.brain.senses.external-rolling-v5",
    "ai.memecoin.brain.senses.external-rolling-v5r2",
    "ai.memecoin.brain.senses.gecko-capture-only-v1",
    "ai.memecoin.brain.senses.gecko-capture-only-v2",
    "ai.memecoin.brain.senses.gecko-capture-only-v3",
    "ai.memecoin.brain.senses.gecko-capture-only-v4",
    "ai.memecoin.brain.senses.gecko-lane-v3",
    "ai.memecoin.brain.senses.gecko-lane-v4",
)
V2_STATIC_ENTRYPOINTS = {
    "ai.memecoin.brain.senses.external-rolling-v4": "brain_senses_external_orchestrator_v4.py",
    "ai.memecoin.brain.senses.external-rolling-v5": "brain_senses_external_orchestrator_v5.py",
    "ai.memecoin.brain.senses.external-rolling-v5r2": "brain_senses_external_rolling_v5r2.py",
    "ai.memecoin.brain.senses.gecko-capture-only-v1": (
        "brain_senses_gecko_capture_only_runtime_v1.py"
    ),
    "ai.memecoin.brain.senses.gecko-capture-only-v2": "brain_senses_gecko_capture_only_v2_host.py",
    "ai.memecoin.brain.senses.gecko-capture-only-v3": "brain_senses_gecko_capture_only_v3_host.py",
    "ai.memecoin.brain.senses.gecko-capture-only-v4": "brain_senses_gecko_capture_only_v4_host.py",
    "ai.memecoin.brain.senses.gecko-lane-v3": "brain_senses_gecko_lane_runtime_v3.py",
    "ai.memecoin.brain.senses.gecko-lane-v4": "brain_senses_gecko_lane_runtime_v4.py",
}


def _raise(reason: str) -> None:
    raise CutoverHostError(reason)


def _registered_v2_clients(uid: int) -> dict[str, dict[str, str]]:
    """Read exact installed LaunchAgent definitions that import coordinator v2."""
    try:
        home = Path(pwd.getpwuid(uid).pw_dir)
    except KeyError as exc:
        raise CutoverHostError("cutover_owner_uid_unresolved") from exc
    directories = (home / "Library/LaunchAgents", Path("/Library/LaunchAgents"))
    clients: dict[str, dict[str, str]] = {}
    for directory in directories:
        try:
            paths = sorted(directory.glob("*.plist"))
        except OSError as exc:
            raise CutoverHostError("cutover_launchagent_directory_unreadable") from exc
        for path in paths:
            try:
                before = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(before.st_mode) or before.st_size > 1_000_000:
                _raise("cutover_launchagent_plist_type_invalid")
            if before.st_uid not in {0, uid}:
                _raise("cutover_launchagent_plist_owner_invalid")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
            try:
                opened = os.fstat(fd)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    _raise("cutover_launchagent_plist_replaced")
                raw = os.read(fd, 1_000_001)
            finally:
                os.close(fd)
            if len(raw) != before.st_size or len(raw) > 1_000_000:
                _raise("cutover_launchagent_plist_size_changed")
            try:
                document = plistlib.loads(raw)
            except Exception as exc:
                raise CutoverHostError("cutover_launchagent_plist_invalid") from exc
            args = document.get("ProgramArguments") if type(document) is dict else None
            label = document.get("Label") if type(document) is dict else None
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                continue
            matching = sorted(
                {
                    token.rsplit("/", maxsplit=1)[-1]
                    for token in args
                    if token.rsplit("/", maxsplit=1)[-1] in V2_MUTATING_ENTRYPOINTS
                }
            )
            if not matching:
                continue
            if not isinstance(label, str) or not label or label in clients:
                _raise("cutover_launchagent_label_invalid_or_duplicate")
            if not any(label.startswith(prefix) for prefix in V2_MUTATING_LABEL_PREFIXES):
                _raise("cutover_launchagent_v2_label_unrecognized")
            clients[label] = {
                "entrypoint": matching[0],
                "plist_sha256": hashlib.sha256(raw).hexdigest(),
                "installed": True,
            }
    for label, entrypoint in V2_STATIC_ENTRYPOINTS.items():
        clients.setdefault(
            label,
            {"entrypoint": entrypoint, "plist_sha256": None, "installed": False},
        )
    if not clients:
        _raise("cutover_v2_client_registry_empty")
    return dict(sorted(clients.items()))


def produce_owner_quiescence_proof(
    *,
    service_lock_paths: Mapping[str, str],
    uid: int | None = None,
    run: Any = subprocess.run,
    observed_at_ns: int | None = None,
) -> dict[str, Any]:
    """Read the owner launchd/process state and return a sanitized v2 proof.

    Command output is consumed in memory and never included in errors or the
    returned proof. The fixed client and entrypoint allowlists keep unrelated
    Brain/operator jobs outside this cutover gate.
    """
    owner_uid = os.getuid() if uid is None else uid
    if type(owner_uid) is not int or owner_uid < 0:
        _raise("cutover_owner_uid_invalid")
    domain = f"gui/{owner_uid}"

    def command(argv: list[str]) -> str:
        try:
            completed = run(argv, capture_output=True, text=True, check=False, timeout=5)
        except Exception as exc:
            raise CutoverHostError("cutover_quiescence_probe_unavailable") from exc
        if getattr(completed, "returncode", None) != 0 or not isinstance(
            getattr(completed, "stdout", None), str
        ):
            _raise("cutover_quiescence_probe_failed")
        return completed.stdout

    registered = _registered_v2_clients(owner_uid)
    registered_labels = set(registered)
    launch_rows: dict[str, int | None] = {}
    launch_output = command(["/bin/launchctl", "list"])
    for line in launch_output.splitlines():
        fields = line.split()
        if len(fields) >= 3 and (
            fields[-1] in registered_labels
            or any(fields[-1].startswith(prefix + "-") for prefix in V2_MUTATING_LABEL_PREFIXES)
            or fields[-1] in V2_MUTATING_LABEL_PREFIXES
        ):
            pid = None if fields[0] == "-" else int(fields[0]) if fields[0].isdigit() else -1
            if pid == -1 or fields[-1] in launch_rows:
                _raise("cutover_launchctl_list_ambiguous")
            launch_rows[fields[-1]] = pid
    unregistered_loaded = sorted(set(launch_rows) - registered_labels)
    if unregistered_loaded:
        raise CutoverHostError(f"cutover_unregistered_v2_client_loaded:{unregistered_loaded[0]}")

    disabled_output = command(["/bin/launchctl", "print-disabled", domain])
    disabled: dict[str, bool] = {}
    for label, state in re.findall(
        r'"([^"\n]+)"\s*=>\s*(true|false|enabled|disabled)', disabled_output
    ):
        if label in registered_labels:
            if label in disabled:
                _raise("cutover_disabled_state_ambiguous")
            disabled[label] = state in {"true", "disabled"}

    clients = {
        label: {
            "loaded": label in launch_rows,
            "disabled": disabled.get(label, False),
            "pid": launch_rows.get(label),
        }
        for label in registered
    }
    active_or_enabled = next(
        (label for label, row in clients.items() if row["loaded"] or not row["disabled"]),
        None,
    )
    if active_or_enabled is not None:
        raise CutoverHostError(f"cutover_v2_client_active_or_enabled:{active_or_enabled}")

    active_pids: list[int] = []
    for row in clients.values():
        if row["pid"] is not None:
            active_pids.append(row["pid"])
    process_rows = command(["/bin/ps", "-axo", "pid=,uid=,args="])
    active_children: list[dict[str, int | str]] = []
    for line in process_rows.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        pid, process_uid = int(fields[0]), int(fields[1])
        if process_uid != owner_uid:
            continue
        # Keep only the executable basename, never raw argv, environment, or
        # endpoint text. Exact old entrypoints are the only provider clients.
        try:
            tokens = shlex.split(fields[2])
        except ValueError:
            _raise("cutover_process_census_malformed")
        for token in tokens:
            basename = token.rsplit("/", maxsplit=1)[-1]
            if basename in V2_MUTATING_ENTRYPOINTS:
                active_children.append({"pid": pid, "entrypoint": basename})
                break
    if active_children:
        _raise("cutover_v2_provider_process_active")

    proof: dict[str, Any] = {
        "schema": QUIESCENCE_SCHEMA,
        "schema_version": 1,
        "service_lock_paths": dict(service_lock_paths),
        "registered_clients": registered,
        "client_labels": clients,
        "active_v2_pids": sorted(set(active_pids)),
        "active_provider_children": active_children,
        "observed_at_ns": time.time_ns() if observed_at_ns is None else observed_at_ns,
    }
    if type(proof["observed_at_ns"]) is not int or proof["observed_at_ns"] <= 0:
        _raise("cutover_quiescence_time_invalid")
    proof["proof_sha256"] = hashlib.sha256(v3.canonical(proof)).hexdigest()
    return proof


def _check_guard(guard: Any, writer: Any, expected: Mapping[str, Any]) -> None:
    try:
        guard.revalidate(min_free_bytes=v3.FIXED_FREE_FLOOR_BYTES)
        st = os.fstat(writer.root_fd)
        if (
            guard.root_id != expected["root_id"]
            or guard.volume_uuid.lower() != expected["volume_uuid"]
            or st.st_dev != expected["device"]
            or st.st_ino != expected["inode"]
        ):
            _raise("cutover_storage_identity_changed")
    except CutoverHostError:
        raise
    except Exception as exc:
        raise CutoverHostError("cutover_storage_revalidation_failed") from exc


def _read_v2_snapshot(
    writer: Any, guard: Any
) -> tuple[bytes, bytes, dict[str, int], dict[str, str]]:
    """Read the frozen v2 policy, ledger, allocated roots, and referenced bytes."""
    policy_bytes = writer.read_bytes(v2.POLICY_PATH, max_bytes=4_000_000)
    policy = v3._v2_policy(policy_bytes)
    binding = policy["root_binding"]
    _check_guard(guard, writer, binding)
    ledger_bytes = writer.read_bytes(v2.LEDGER_PATH, max_bytes=v3.MAX_LEDGER_BYTES)
    events = v3._v2_ledger(ledger_bytes, policy_bytes)
    paths = v3._v2_root_paths(policy)
    inventory = {
        root: v2.physical_inventory_bytes(writer, root)
        for group in paths.values()
        for root in group
    }
    artifacts: dict[str, str] = {}
    for event in events:
        fields = event["fields"]
        referenced: list[tuple[str, str]] = []
        if event["kind"] == "reserved":
            referenced.append((fields["intent_path"], fields["intent_sha256"]))
        elif event["kind"] in {"settled", "ambiguous_terminal", "released"}:
            if fields.get("outcome_path") is not None:
                referenced.append((fields["outcome_path"], fields["outcome_sha256"]))
        for path, expected_sha in referenced:
            if path in artifacts:
                continue
            try:
                payload = writer.read_bytes(path, max_bytes=64_000_000)
            except FileNotFoundError:
                if event["kind"] == "released" and fields.get("outcome") == "pre_intent_absent":
                    continue
                raise
            actual_sha = hashlib.sha256(payload).hexdigest()
            if actual_sha != expected_sha:
                _raise("cutover_v2_artifact_hash_mismatch")
            artifacts[path] = actual_sha
    return policy_bytes, ledger_bytes, inventory, artifacts


def _all_v3_inventory(writer: Any, policy_bytes: bytes) -> dict[str, int]:
    policy = v3._decode(policy_bytes)
    roots = sorted(path for values in policy["allocation_roots"].values() for path in values)
    result: dict[str, int] = {}
    for root in roots:
        try:
            result[root] = v2.physical_inventory_bytes(writer, root)
        except FileNotFoundError:
            result[root] = 0
    return result


def _verify_quiescence(proof: Any, *, expected_locks: Mapping[str, str]) -> dict[str, Any]:
    keys = {
        "schema",
        "schema_version",
        "service_lock_paths",
        "registered_clients",
        "client_labels",
        "active_v2_pids",
        "active_provider_children",
        "observed_at_ns",
        "proof_sha256",
    }
    if type(proof) is not dict or set(proof) != keys:
        _raise("cutover_quiescence_proof_shape")
    core = dict(proof)
    digest = core.pop("proof_sha256")
    registry = proof.get("registered_clients") if type(proof) is dict else None
    client_states = proof.get("client_labels") if type(proof) is dict else None
    valid_registry = (
        type(registry) is dict
        and bool(registry)
        and all(
            isinstance(label, str)
            and any(label.startswith(prefix) for prefix in V2_MUTATING_LABEL_PREFIXES)
            and type(binding) is dict
            and set(binding) == {"entrypoint", "plist_sha256", "installed"}
            and binding.get("entrypoint") in V2_MUTATING_ENTRYPOINTS
            and type(binding.get("installed")) is bool
            and (
                isinstance(binding.get("plist_sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", binding["plist_sha256"]) is not None
                if binding["installed"]
                else binding.get("plist_sha256") is None
            )
            for label, binding in registry.items()
        )
    )
    if (
        proof["schema"] != QUIESCENCE_SCHEMA
        or proof["schema_version"] != 1
        or proof["service_lock_paths"] != dict(expected_locks)
        or not valid_registry
        or type(client_states) is not dict
        or set(client_states) != set(registry)
        or client_states
        != {label: {"loaded": False, "disabled": True, "pid": None} for label in registry}
        or proof["active_v2_pids"] != []
        or proof["active_provider_children"] != []
        or type(proof["observed_at_ns"]) is not int
        or proof["observed_at_ns"] <= 0
        or type(digest) is not str
        or hashlib.sha256(v3.canonical(core)).hexdigest() != digest
    ):
        _raise("cutover_v2_not_quiescent_or_jobs_not_disabled")
    return proof


@contextmanager
def _ordered_cutover_locks(writer: Any, guard: Any, policy: Mapping[str, Any]):
    locks = policy["service_lock_paths"]
    if type(locks) is not dict or set(locks) != {"helius", "alchemy", "gecko"}:
        _raise("cutover_lane_lock_schema")
    with ExitStack() as stack:
        expected_binding = policy["root_binding"]
        for service in sorted(locks):
            _check_guard(guard, writer, expected_binding)
            # Cutover must not manufacture lock paths while deciding whether
            # older writers are quiescent.
            writer.stat(locks[service])
            stack.enter_context(writer.lock(locks[service], blocking=False))
        _check_guard(guard, writer, expected_binding)
        writer.stat(v3.SHARED_LOCK_PATH)
        stack.enter_context(writer.lock(v3.SHARED_LOCK_PATH, blocking=False))
        yield locks


def publish_cutover_v3(
    *,
    writer: Any,
    guard: Any,
    cutover_ns: int | None = None,
    free_bytes_probe: Any | None = None,
    quiescence_verifier: Any | None = None,
    quiescence_runner: Any = subprocess.run,
) -> dict[str, Any]:
    """Publish policy, genesis ledger, and pointer with pointer durably last.

    The shared v2 coordinator lock is held while snapshotting and publishing.
    Exact partial prefixes can be resumed; any differing bytes or unexplained
    entry in the v3 coordinator root stops before another write.
    """
    requested_cutover_ns = cutover_ns
    if requested_cutover_ns is not None and (
        type(requested_cutover_ns) is not int or requested_cutover_ns <= 0
    ):
        _raise("cutover_time_invalid")
    observed: dict[str, Any] | None = None
    migration_id = ""
    writes_done: list[str] = []
    try:
        preliminary_policy_bytes = writer.read_bytes(v2.POLICY_PATH, max_bytes=4_000_000)
        preliminary_policy = v3._v2_policy(preliminary_policy_bytes)
        if quiescence_verifier is None:

            def live_quiescence_verifier() -> dict[str, Any]:
                return produce_owner_quiescence_proof(
                    service_lock_paths=preliminary_policy["service_lock_paths"],
                    run=quiescence_runner,
                )

            quiescence_verifier = live_quiescence_verifier
        if not callable(quiescence_verifier):
            _raise("cutover_quiescence_verifier_required")
        _check_guard(guard, writer, preliminary_policy["root_binding"])
        with _ordered_cutover_locks(writer, guard, preliminary_policy):
            # The first policy read establishes the only accepted root identity.
            policy_bytes = writer.read_bytes(v2.POLICY_PATH, max_bytes=4_000_000)
            v2_policy = v3._v2_policy(policy_bytes)
            if policy_bytes != preliminary_policy_bytes:
                _raise("cutover_v2_policy_changed_before_locks")
            proof = _verify_quiescence(
                quiescence_verifier(), expected_locks=v2_policy["service_lock_paths"]
            )
            observed = dict(v2_policy["root_binding"])
            _check_guard(guard, writer, observed)
            stat = os.fstat(writer.root_fd)
            if (stat.st_dev, stat.st_ino) != (observed["device"], observed["inode"]):
                _raise("cutover_writer_descriptor_mismatch")

            policy_bytes, ledger_bytes, old_inventory, artifacts = _read_v2_snapshot(writer, guard)
            bridge = v3.build_v2_bridge(
                v2_policy_bytes=policy_bytes,
                v2_ledger_bytes=ledger_bytes,
                v2_root_inventory=old_inventory,
                artifact_sha256_by_path=artifacts,
            )
            root_sets = v3._v3_root_sets(v2_policy)
            allocated = {name: 0 for name in v3.SERVICE_CAPS}
            for service, roots in root_sets.items():
                if service == "gecko_discovery":
                    continue
                for root in roots:
                    allocated[service] += old_inventory.get(root, 0)
            pending = {name: 0 for name in v3.SERVICE_CAPS}
            for row in bridge["pending_liabilities"]:
                service = "gecko_legacy" if row["service"] == "gecko" else row["service"]
                pending[service] += row["liability_bytes"]
            for service, cap in v3.SERVICE_CAPS.items():
                if allocated[service] + pending[service] > cap:
                    _raise("cutover_service_capacity_exceeded")
            if free_bytes_probe is None:
                statvfs = os.fstatvfs(writer.root_fd)
                available = statvfs.f_bavail * statvfs.f_frsize
            else:
                available = free_bytes_probe(writer)
                if type(available) is not int or available < 0:
                    _raise("cutover_free_bytes_invalid")
            required = (
                v3.FIXED_FREE_FLOOR_BYTES
                + sum(pending.values())
                + v3.COORDINATOR_APPEND_RESERVE_BYTES
            )
            if available < required:
                _raise("cutover_combined_free_floor")
            # Make the migration byte-stable on restart. The genesis time is a
            # logical cutover marker after the frozen v2 tail, and is therefore
            # deterministic even if only the policy file survived a crash.
            v2_events = v3._v2_ledger(ledger_bytes, policy_bytes)
            existing_ledger: bytes | None
            try:
                existing_ledger = writer.read_bytes(v3.LEDGER_PATH, max_bytes=v3.MAX_LEDGER_BYTES)
            except FileNotFoundError:
                existing_ledger = None
            existing_genesis: dict[str, Any] | None = None
            if existing_ledger is not None:
                first_line = (
                    existing_ledger.splitlines(keepends=True)[0] if existing_ledger else b""
                )
                try:
                    candidate = v3._decode(first_line)
                except Exception as exc:
                    raise CutoverHostError("cutover_existing_genesis_invalid") from exc
                if (
                    type(candidate) is not dict
                    or candidate.get("kind") != "genesis"
                    or candidate.get("fields", {}).get("bridge_sha256") != bridge["bridge_sha256"]
                    or candidate.get("fields", {}).get("v2_tail_sha256") != bridge["v2_tail_sha256"]
                ):
                    _raise("cutover_existing_genesis_conflict")
                existing_genesis = candidate
            if existing_genesis is not None:
                effective_cutover_ns = existing_genesis["at_ns"]
                if (
                    requested_cutover_ns is not None
                    and requested_cutover_ns != effective_cutover_ns
                ):
                    _raise("cutover_time_conflict")
            else:
                effective_cutover_ns = max(
                    requested_cutover_ns or time.time_ns(), v2_events[-1]["at_ns"] + 1
                )
            migration_core = {
                "bridge_sha256": bridge["bridge_sha256"],
                "cutover_ns": effective_cutover_ns,
                "root_binding": observed,
            }
            migration_id = hashlib.sha256(v3.canonical(migration_core)).hexdigest()[:32]
            docs = v3.migration_documents(
                v2_policy_bytes=policy_bytes,
                v2_ledger_bytes=ledger_bytes,
                v2_root_inventory=old_inventory,
                artifact_sha256_by_path=artifacts,
                bridge=bridge,
                root_binding=observed,
                cutover_ns=effective_cutover_ns,
                migration_id=migration_id,
            )
            if existing_genesis is not None:
                _need_migration = existing_genesis == v3._decode(docs["ledger"])
                if not _need_migration:
                    _raise("cutover_existing_genesis_mismatch")
            # New allocation roots are created descriptor-relatively. An empty
            # root after a crash is a valid inert prefix.
            for root in (v3.DISCOVERY_ROOT, v3.COORDINATOR_ROOT):
                _check_guard(guard, writer, observed)
                writer.mkdir(root)
                _check_guard(guard, writer, observed)
            present: dict[str, bytes | None] = {}
            for name, path in (
                ("policy", v3.POLICY_PATH),
                ("ledger", v3.LEDGER_PATH),
                ("pointer", v3.POINTER_PATH),
            ):
                try:
                    present[name] = writer.read_bytes(path, max_bytes=v3.MAX_LEDGER_BYTES)
                except FileNotFoundError:
                    present[name] = None
            root_names = set(writer.list_names(v3.COORDINATOR_ROOT))
            expected_names = {"policy.json", "ledger.jsonl", "v2-cutover-pointer.json"}
            if not root_names <= expected_names:
                _raise("cutover_unexpected_coordinator_entry")
            action = v3.reconcile_migration_prefix(
                docs,
                existing_policy=present["policy"],
                existing_ledger=present["ledger"],
                existing_pointer=present["pointer"],
            )
            for item in action["writes"]:
                if item["path"] == v3.POINTER_PATH:
                    # A final independent replay guards the frozen v2 prefix
                    # immediately before the cutover pointer becomes visible.
                    fresh_policy, fresh_ledger, fresh_inventory, fresh_artifacts = (
                        _read_v2_snapshot(writer, guard)
                    )
                    fresh_bridge = v3.build_v2_bridge(
                        v2_policy_bytes=fresh_policy,
                        v2_ledger_bytes=fresh_ledger,
                        v2_root_inventory=fresh_inventory,
                        artifact_sha256_by_path=fresh_artifacts,
                    )
                    if (
                        fresh_policy != policy_bytes
                        or fresh_ledger != ledger_bytes
                        or fresh_bridge != bridge
                    ):
                        _raise("cutover_v2_prefix_changed_before_pointer")
                    second_proof = _verify_quiescence(
                        quiescence_verifier(), expected_locks=v2_policy["service_lock_paths"]
                    )
                    if second_proof["observed_at_ns"] < proof["observed_at_ns"]:
                        _raise("cutover_quiescence_time_regressed")
                _check_guard(guard, writer, observed)
                writer.create(item["path"], item["payload"], mode=0o600)
                _check_guard(guard, writer, observed)
                writes_done.append(item["path"])
            readback = {
                "policy": writer.read_bytes(v3.POLICY_PATH, max_bytes=4_000_000),
                "ledger": writer.read_bytes(v3.LEDGER_PATH, max_bytes=v3.MAX_LEDGER_BYTES),
                "pointer": writer.read_bytes(v3.POINTER_PATH, max_bytes=4_000_000),
            }
            if readback != docs:
                _raise("cutover_readback_mismatch")
            v3.verify_pointer_and_ledger(
                pointer_bytes=readback["pointer"],
                ledger_bytes=readback["ledger"],
                policy_bytes=readback["policy"],
                bridge=bridge,
            )
            fresh_policy, fresh_ledger, fresh_inventory, fresh_artifacts = _read_v2_snapshot(
                writer, guard
            )
            fresh_bridge = v3.build_v2_bridge(
                v2_policy_bytes=fresh_policy,
                v2_ledger_bytes=fresh_ledger,
                v2_root_inventory=fresh_inventory,
                artifact_sha256_by_path=fresh_artifacts,
            )
            if (
                fresh_policy != policy_bytes
                or fresh_ledger != ledger_bytes
                or fresh_bridge != bridge
            ):
                _raise("cutover_v2_prefix_changed_after_pointer")
            _check_guard(guard, writer, observed)
            return {
                "schema": "brain-senses-capacity-coordinator-v3-cutover-result",
                "schema_version": 1,
                "migration_id": migration_id,
                "bridge_sha256": bridge["bridge_sha256"],
                "policy_sha256": v3.sha256(readback["policy"]),
                "ledger_sha256": v3.sha256(readback["ledger"]),
                "pointer_sha256": v3.sha256(readback["pointer"]),
                "writes_created": writes_done,
                "activation_ready": True,
                "provider_io": False,
            }
    except CutoverHostError:
        raise
    except Exception as exc:
        raise CutoverHostError("cutover_host_failed_closed") from exc
