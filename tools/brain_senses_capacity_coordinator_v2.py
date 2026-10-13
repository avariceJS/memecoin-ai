#!/usr/bin/env python3
"""Shared, descriptor-bound Kingston capacity reservation coordinator v2.

This module coordinates bounded reservations among the Helius, Alchemy, and
Gecko lanes. It does not dispatch provider requests. Callers persist the exact
    request intent while ``reserve_and_persist_intent`` holds both locks. A
    caller-held lane lease can keep same-lane operations serialized across provider
    I/O while the shared lock remains released.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

POLICY_PATH = "services/senses-capacity-v2/policy.json"
LEDGER_PATH = "services/senses-capacity-v2/ledger.jsonl"
LOCK_PATH = "services/senses-capacity-v2/.lock"
V3_CUTOVER_POINTER_PATH = "services/senses-capacity-v3/v2-cutover-pointer.json"
COORDINATOR_ROOT = "services/senses-capacity-v2"
POLICY_SCHEMA = "brain-senses-capacity-coordinator-policy-v2"
LEDGER_SCHEMA = "brain-senses-capacity-coordinator-ledger-event-v2"
POLICY_VERSION = 2
LEDGER_VERSION = 2
COORDINATOR_APPEND_BLOCK_BYTES = 4096
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ROOT_ID = re.compile(r"[0-9a-f]{32}\Z")
_VOLUME_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_SERVICES = ("helius", "alchemy", "gecko", "coordinator")
_SERVICE_CAPS = {
    "helius": 150_000_000_000,
    "alchemy": 256_000_000,
    "gecko": 16_000_000_000,
    "coordinator": 64_000_000,
}
_FIXED_RESERVES = {
    "helius_disk_floor": 50 * 2**30,
    "operating_reserve": 8 * 2**30,
    "helius_next_epoch": 4_000_000_000,
    "helius_peak_metadata": 322_000_000,
    "alchemy_peak": 128_000_000,
    "alchemy_metadata": 32_000_000,
    "gecko_peak": 32_000_000,
    "gecko_metadata": 16_000_000,
}


class CoordinatorError(RuntimeError):
    """Fail-closed capacity, persistence, root, or ledger condition."""


class Writer(Protocol):
    root_fd: int

    def mkdir(self, relative: str) -> None: ...

    def create(self, relative: str, payload: bytes, *, mode: int = 0o600) -> None: ...

    def append(self, relative: str, payload: bytes) -> None: ...

    def read_bytes(self, relative: str, *, max_bytes: int = 64 * 1024 * 1024) -> bytes: ...

    def exists(self, relative: str) -> bool: ...

    def stat(self, relative: str) -> os.stat_result: ...

    def list_names(self, relative: str = "") -> tuple[str, ...]: ...

    def lock(self, relative: str = ".writer.lock", *, blocking: bool = False) -> Any: ...


IdentityCheck = Callable[[Writer], None]
InventoryBytes = Callable[[Writer, str], int]
FreeBytes = Callable[[Writer], int]
OutcomeWriter = Callable[[Writer, str, bytes], None]


class CapacitySource(Protocol):
    """Fresh descriptor-bound facts required under the coordinator locks."""

    def verify_root(self, writer: Writer) -> None: ...

    def allocated_bytes(self, writer: Writer, service: str) -> int: ...

    def free_bytes(self, writer: Writer) -> int: ...


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    service: str
    request_id: str
    intent_path: str
    outcome_path: str
    intent_sha256: str
    reserved_bytes: int
    intent_bytes: bytes


@dataclass
class LaneLockLease:
    """Unforgeable-in-process proof of a caller-held lane lock."""

    service: str
    thread_id: int
    _owner: object
    active: bool = True


def canonical(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CoordinatorError("canonical_json_invalid") from exc


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and str(path) == value


def _normalize_root_set(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        roots = (value,)
    elif isinstance(value, (tuple, list)) and all(isinstance(path, str) for path in value):
        roots = tuple(value)
    else:
        raise CoordinatorError("allocation_roots_invalid")
    if not roots or any(not _relative_path(path) for path in roots):
        raise CoordinatorError("allocation_roots_invalid")
    return roots


def policy_document(
    root_binding: Mapping[str, Any],
    service_lock_paths: Mapping[str, str],
    allocation_roots: Mapping[str, tuple[str, ...]],
) -> dict[str, Any]:
    return {
        "schema": POLICY_SCHEMA,
        "schema_version": POLICY_VERSION,
        "root_binding": dict(root_binding),
        "service_lock_paths": dict(service_lock_paths),
        "allocation_roots": {name: list(paths) for name, paths in allocation_roots.items()},
        "service_caps_bytes": _SERVICE_CAPS,
        "fixed_reserves_bytes": _FIXED_RESERVES,
        "coordinator_append_reservation_bytes": COORDINATOR_APPEND_BLOCK_BYTES,
        "ledger": "append_only_canonical_sha256_chain",
        "storage": "descriptor_bound_root_relative_flock_create_only_fsync",
    }


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CoordinatorError("ledger_duplicate_json_key")
        result[key] = value
    return result


def _decode(raw: bytes) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_strict_object_pairs, parse_constant=_bad_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoordinatorError("ledger_json_invalid") from exc


def _bad_constant(_: str) -> None:
    raise CoordinatorError("ledger_nonfinite_json")


def _event(
    *, seq: int, kind: str, at_ns: int, previous: str | None, fields: Mapping[str, Any]
) -> dict[str, Any]:
    unsigned = {
        "schema": LEDGER_SCHEMA,
        "schema_version": LEDGER_VERSION,
        "seq": seq,
        "kind": kind,
        "at_ns": at_ns,
        "previous_sha256": previous,
        "fields": dict(fields),
    }
    return {**unsigned, "event_sha256": sha256(canonical(unsigned))}


def _verify_ledger(raw: bytes) -> list[dict[str, Any]]:
    if not raw.endswith(b"\n"):
        raise CoordinatorError("ledger_torn_final_record")
    events: list[dict[str, Any]] = []
    previous: str | None = None
    reservations: dict[str, str] = {}
    terminal: set[str] = set()
    request_keys: set[tuple[str, str]] = set()
    for seq, line in enumerate(raw.splitlines(keepends=True)):
        value = _decode(line)
        if not isinstance(value, dict) or canonical(value) != line:
            raise CoordinatorError("ledger_record_not_canonical")
        required = {
            "schema",
            "schema_version",
            "seq",
            "kind",
            "at_ns",
            "previous_sha256",
            "fields",
            "event_sha256",
        }
        if (
            set(value) != required
            or value["schema"] != LEDGER_SCHEMA
            or value["schema_version"] != LEDGER_VERSION
            or type(value["seq"]) is not int
            or value["seq"] != seq
            or type(value["at_ns"]) is not int
            or value["at_ns"] <= 0
            or value["previous_sha256"] != previous
            or not isinstance(value["fields"], dict)
            or not isinstance(value["event_sha256"], str)
            or not _SHA256.fullmatch(value["event_sha256"])
        ):
            raise CoordinatorError("ledger_chain_binding_invalid")
        unsigned = {key: item for key, item in value.items() if key != "event_sha256"}
        if value["event_sha256"] != sha256(canonical(unsigned)):
            raise CoordinatorError("ledger_event_hash_invalid")
        kind, fields = value["kind"], value["fields"]
        if seq == 0:
            if (
                kind != "genesis"
                or set(fields) != {"policy_sha256"}
                or not isinstance(fields.get("policy_sha256"), str)
                or not _SHA256.fullmatch(fields["policy_sha256"])
            ):
                raise CoordinatorError("ledger_genesis_invalid")
        elif kind == "reserved":
            reservation_id = fields.get("reservation_id")
            if (
                set(fields)
                != {
                    "reservation_id",
                    "service",
                    "request_id",
                    "intent_path",
                    "outcome_path",
                    "intent_sha256",
                    "reserved_bytes",
                    "intent_bytes_sha256",
                    "allocated_before_bytes",
                }
                or not isinstance(reservation_id, str)
                or not reservation_id
                or reservation_id in reservations
                or fields.get("service") not in {"helius", "alchemy", "gecko"}
                or not isinstance(fields.get("request_id"), str)
                or not fields["request_id"]
                or not isinstance(fields.get("intent_path"), str)
                or not isinstance(fields.get("outcome_path"), str)
                or fields["intent_path"] == fields["outcome_path"]
                or not isinstance(fields.get("intent_sha256"), str)
                or not _SHA256.fullmatch(fields["intent_sha256"])
                or fields["intent_sha256"] != fields["intent_bytes_sha256"]
                or type(fields.get("reserved_bytes")) is not int
                or fields["reserved_bytes"] <= 0
                or type(fields.get("allocated_before_bytes")) is not int
                or fields["allocated_before_bytes"] < 0
            ):
                raise CoordinatorError("ledger_reservation_invalid")
            request_key = (fields["service"], fields["request_id"])
            if request_key in request_keys:
                raise CoordinatorError("ledger_request_id_reused")
            request_keys.add(request_key)
            reservations[reservation_id] = fields["service"]
        elif kind in {"settled", "ambiguous_terminal", "released"}:
            reservation_id = fields.get("reservation_id")
            base_fields = {"reservation_id", "outcome", "outcome_sha256"}
            actual_fields = frozenset(fields)
            allowed_fields = (
                {frozenset(base_fields | {"outcome_path", "actual_allocated_bytes"})}
                if kind == "settled"
                else {frozenset(base_fields)}
                if kind == "released"
                else {
                    frozenset(base_fields | {"actual_allocated_bytes"}),
                    frozenset(base_fields | {"outcome_path", "actual_allocated_bytes"}),
                }
            )
            if (
                actual_fields not in allowed_fields
                or reservation_id not in reservations
                or reservation_id in terminal
                or not isinstance(fields.get("outcome"), str)
                or not isinstance(fields.get("outcome_sha256"), str)
                or not _SHA256.fullmatch(fields["outcome_sha256"])
                or (kind == "released" and fields["outcome"] != "pre_intent_absent")
                or ("outcome_path" in fields and not isinstance(fields["outcome_path"], str))
                or (
                    kind in {"settled", "ambiguous_terminal"}
                    and (
                        type(fields.get("actual_allocated_bytes")) is not int
                        or fields["actual_allocated_bytes"] < 0
                    )
                )
            ):
                raise CoordinatorError("ledger_terminal_invalid")
            terminal.add(reservation_id)
        else:
            raise CoordinatorError("ledger_event_kind_invalid")
        previous = value["event_sha256"]
        events.append(value)
    if not events:
        raise CoordinatorError("ledger_missing_genesis")
    return events


def _reservation_state(events: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    reservations: dict[str, dict[str, Any]] = {}
    terminal: set[str] = set()
    for event in events[1:]:
        fields = event["fields"]
        if event["kind"] == "reserved":
            reservations[fields["reservation_id"]] = fields
        elif event["kind"] in {"settled", "ambiguous_terminal", "released"}:
            terminal.add(fields["reservation_id"])
    return reservations, terminal


def physical_inventory_bytes(writer: Writer, relative_root: str) -> int:
    """Sum allocated directory and file blocks through descriptor-relative APIs."""
    total = 0

    def visit(path: str) -> None:
        nonlocal total
        st = writer.stat(path)
        total += st.st_blocks * 512
        if stat.S_ISDIR(st.st_mode):
            for name in writer.list_names(path):
                child = f"{path}/{name}"
                child_stat = writer.stat(child)
                if stat.S_ISDIR(child_stat.st_mode):
                    visit(child)
                elif stat.S_ISREG(child_stat.st_mode):
                    total += child_stat.st_blocks * 512
                else:
                    raise CoordinatorError("physical_inventory_unsupported_entry")

    visit(relative_root)
    return total


class DescriptorCapacitySource:
    """Default live facts adapter; identity validation remains caller-owned."""

    def __init__(
        self,
        *,
        allocation_roots: Mapping[str, tuple[str, ...] | str],
        verify_root: IdentityCheck,
        free_bytes: FreeBytes | None = None,
        inventory_bytes: InventoryBytes | None = None,
    ) -> None:
        required = {"helius", "alchemy", "gecko", "coordinator"}
        if set(allocation_roots) != required:
            raise CoordinatorError("allocation_roots_invalid")
        normalized = {name: _normalize_root_set(paths) for name, paths in allocation_roots.items()}
        roots = tuple(path for paths in normalized.values() for path in paths)
        if (
            not roots
            or any(not _relative_path(path) for path in roots)
            or len(set(roots)) != len(roots)
            or any(
                left == right or left.startswith(right + "/") or right.startswith(left + "/")
                for index, left in enumerate(roots)
                for right in roots[index + 1 :]
            )
            or COORDINATOR_ROOT not in normalized["coordinator"]
            or any(
                path == COORDINATOR_ROOT
                or path.startswith(COORDINATOR_ROOT + "/")
                or COORDINATOR_ROOT.startswith(path + "/")
                for name, paths in normalized.items()
                if name != "coordinator"
                for path in paths
            )
        ):
            raise CoordinatorError("service_roots_overlap")
        self.allocation_roots = normalized
        self._verify_root = verify_root
        self._free_bytes = free_bytes or self._statvfs_free
        self._inventory_bytes = inventory_bytes or (
            lambda writer, service: sum(
                physical_inventory_bytes(writer, path) for path in self.allocation_roots[service]
            )
        )

    @staticmethod
    def _statvfs_free(writer: Writer) -> int:
        stats = os.fstatvfs(writer.root_fd)
        return stats.f_bavail * stats.f_frsize

    def verify_root(self, writer: Writer) -> None:
        self._verify_root(writer)

    def allocated_bytes(self, writer: Writer, service: str) -> int:
        return self._inventory_bytes(writer, service)

    def free_bytes(self, writer: Writer) -> int:
        return self._free_bytes(writer)


class CapacityCoordinator:
    """Atomic shared reservation ledger plus lane-intent persistence boundary."""

    def __init__(
        self,
        writer: Writer,
        *,
        source: CapacitySource,
        service_roots: Mapping[str, str],
        service_lock_paths: Mapping[str, str],
        allocation_roots: Mapping[str, tuple[str, ...] | str] | None = None,
        root_binding: Mapping[str, Any],
        now_ns: Callable[[], int],
    ) -> None:
        required = {"helius", "alchemy", "gecko"}
        if (
            set(service_roots) != required
            or set(service_lock_paths) != required
            or any(
                not _relative_path(path)
                for path in [*service_roots.values(), *service_lock_paths.values()]
            )
            or len(set(service_lock_paths.values())) != len(service_lock_paths)
            or set(root_binding) != {"root_id", "volume_uuid", "device", "inode"}
            or not isinstance(root_binding.get("root_id"), str)
            or not _ROOT_ID.fullmatch(root_binding["root_id"])
            or not isinstance(root_binding.get("volume_uuid"), str)
            or not _VOLUME_UUID.fullmatch(root_binding["volume_uuid"].lower())
            or type(root_binding.get("device")) is not int
            or root_binding["device"] < 0
            or type(root_binding.get("inode")) is not int
            or root_binding["inode"] <= 0
        ):
            raise CoordinatorError("service_roots_invalid")
        roots = tuple(service_roots.values())
        if (
            len(set(roots)) != len(roots)
            or any(
                left.startswith(right + "/") or right.startswith(left + "/")
                for index, left in enumerate(roots)
                for right in roots[index + 1 :]
            )
            or any(
                root == COORDINATOR_ROOT
                or root.startswith(COORDINATOR_ROOT + "/")
                or COORDINATOR_ROOT.startswith(root + "/")
                for root in roots
            )
        ):
            raise CoordinatorError("service_roots_overlap")
        allocation_roots = allocation_roots or {
            **{name: (path,) for name, path in service_roots.items()},
            "coordinator": (COORDINATOR_ROOT,),
        }
        if set(allocation_roots) != {"helius", "alchemy", "gecko", "coordinator"}:
            raise CoordinatorError("allocation_roots_invalid")
        normalized_allocation_roots = {
            name: _normalize_root_set(paths) for name, paths in allocation_roots.items()
        }
        inventory_roots = [path for paths in normalized_allocation_roots.values() for path in paths]
        if (
            not inventory_roots
            or any(not _relative_path(path) for path in inventory_roots)
            or len(set(inventory_roots)) != len(inventory_roots)
            or any(
                left.startswith(right + "/") or right.startswith(left + "/")
                for index, left in enumerate(inventory_roots)
                for right in inventory_roots[index + 1 :]
            )
            or any(
                service_roots[name] not in normalized_allocation_roots[name] for name in required
            )
            or COORDINATOR_ROOT not in normalized_allocation_roots["coordinator"]
        ):
            raise CoordinatorError("allocation_roots_invalid")
        if any(
            any(
                path == root or path.startswith(root + "/") or root.startswith(path + "/")
                for root in inventory_roots
            )
            for path in service_lock_paths.values()
        ):
            raise CoordinatorError("service_lock_path_overlaps_inventory")
        self.writer = writer
        self.source = source
        self.service_roots = dict(service_roots)
        self.allocation_roots = normalized_allocation_roots
        self.service_lock_paths = dict(service_lock_paths)
        self.root_binding = dict(root_binding)
        self._lease_owner = object()
        self.policy_bytes = canonical(
            policy_document(self.root_binding, self.service_lock_paths, self.allocation_roots)
        )
        writer_stat = os.fstat(writer.root_fd)
        if (writer_stat.st_dev, writer_stat.st_ino) != (
            self.root_binding["device"],
            self.root_binding["inode"],
        ):
            raise CoordinatorError("coordinator_root_binding_mismatch")
        self.now_ns = now_ns

    def _verify_root(self) -> None:
        writer_stat = os.fstat(self.writer.root_fd)
        if (writer_stat.st_dev, writer_stat.st_ino) != (
            self.root_binding["device"],
            self.root_binding["inode"],
        ):
            raise CoordinatorError("coordinator_root_binding_mismatch")
        self.source.verify_root(self.writer)

    def _assert_no_v3_cutover(self) -> None:
        """Refuse all v2 mutations once the v3 cutover pointer is durable."""
        self._verify_root()
        if self.writer.exists(V3_CUTOVER_POINTER_PATH):
            raise CoordinatorError("v2_mutation_disabled_after_v3_cutover")

    def initialize(self) -> None:
        self._verify_root()
        with self.writer.lock(LOCK_PATH, blocking=True):
            self._assert_no_v3_cutover()
            for root in {path for paths in self.allocation_roots.values() for path in paths}:
                self._assert_no_v3_cutover()
                self.writer.mkdir(root)
            for relative in self.service_lock_paths.values():
                if not self.writer.exists(relative):
                    self._assert_no_v3_cutover()
                    self.writer.create(relative, b"capacity-service-lock-v1\n")
            policy_exists, ledger_exists = (
                self.writer.exists(POLICY_PATH),
                self.writer.exists(LEDGER_PATH),
            )
            if policy_exists != ledger_exists:
                raise CoordinatorError("coordinator_partial_initialization")
            if not policy_exists:
                self._assert_no_v3_cutover()
                self.writer.create(POLICY_PATH, self.policy_bytes)
                genesis = _event(
                    seq=0,
                    kind="genesis",
                    at_ns=self.now_ns(),
                    previous=None,
                    fields={"policy_sha256": sha256(self.policy_bytes)},
                )
                self._assert_no_v3_cutover()
                self.writer.append(LEDGER_PATH, canonical(genesis))
            else:
                if self.writer.read_bytes(POLICY_PATH, max_bytes=1_000_000) != self.policy_bytes:
                    raise CoordinatorError("coordinator_policy_drift")
                events = _verify_ledger(self.writer.read_bytes(LEDGER_PATH, max_bytes=64_000_000))
                if events[0]["fields"]["policy_sha256"] != sha256(self.policy_bytes):
                    raise CoordinatorError("ledger_policy_binding_invalid")

    @contextmanager
    def _locks(self, service: str) -> Iterator[None]:
        # Lane lock first, then shared lock: every lane uses this order.
        self._verify_root()
        with self.writer.lock(self.service_lock_paths[service], blocking=True):
            with self.writer.lock(LOCK_PATH, blocking=True):
                self._verify_root()
                yield

    @contextmanager
    def lane_lock(self, service: str, *, blocking: bool = True) -> Iterator[LaneLockLease]:
        """Hold a lane lock across reservation, provider I/O, and settlement.

        The yielded lease lets reserve/settle take only the shared lock, avoiding
        recursive flock acquisition while preserving same-lane serialization.
        Long-lived schedulers should pass ``blocking=False`` so launchd never queues
        a late callback behind an active provider attempt.
        """
        if service not in self.service_lock_paths:
            raise CoordinatorError("service_invalid")
        self._verify_root()
        with self.writer.lock(self.service_lock_paths[service], blocking=blocking):
            self._verify_root()
            lease = LaneLockLease(service, threading.get_ident(), self._lease_owner)
            try:
                yield lease
            finally:
                lease.active = False

    @contextmanager
    def _shared_lock(self, service: str, lease: LaneLockLease | None) -> Iterator[None]:
        if lease is None:
            with self._locks(service):
                yield
            return
        self._validate_lane_lease(service, lease)
        with self.writer.lock(LOCK_PATH, blocking=True):
            self._assert_no_v3_cutover()
            yield

    def _validate_lane_lease(self, service: str, lease: LaneLockLease) -> None:
        if (
            not isinstance(lease, LaneLockLease)
            or lease._owner is not self._lease_owner
            or not lease.active
            or lease.service != service
            or lease.thread_id != threading.get_ident()
        ):
            raise CoordinatorError("lane_lock_lease_invalid")

    def _snapshot(self) -> tuple[dict[str, int], int]:
        allocated = {
            service: self.source.allocated_bytes(self.writer, service) for service in _SERVICES
        }
        if any(type(value) is not int or value < 0 for value in allocated.values()):
            raise CoordinatorError("physical_inventory_invalid")
        for lock_path in self.service_lock_paths.values():
            lock_stat = self.writer.stat(lock_path)
            if not stat.S_ISREG(lock_stat.st_mode):
                raise CoordinatorError("service_lock_inventory_invalid")
            allocated["coordinator"] += lock_stat.st_blocks * 512
        self._verify_root()
        free = self.source.free_bytes(self.writer)
        if type(free) is not int or free < 0:
            raise CoordinatorError("fresh_free_bytes_invalid")
        return allocated, free

    def _load_ledger(
        self,
        *,
        allow_missing_intent_ids: frozenset[str] = frozenset(),
        allow_missing_pending_intents: bool = False,
    ) -> tuple[bytes, list[dict[str, Any]]]:
        raw = self.writer.read_bytes(LEDGER_PATH, max_bytes=64_000_000)
        events = _verify_ledger(raw)
        if events[0]["fields"]["policy_sha256"] != sha256(self.policy_bytes):
            raise CoordinatorError("ledger_policy_binding_invalid")
        reservation_rows: dict[str, Mapping[str, Any]] = {}
        terminal_rows: dict[str, Mapping[str, Any]] = {}
        for event in events:
            if event["kind"] == "reserved":
                fields = event["fields"]
                service = fields["service"]
                self._validate_lane_path(
                    service, self.service_roots[service], fields["intent_path"]
                )
                self._validate_lane_path(
                    service, self.service_roots[service], fields["outcome_path"]
                )
                reservation_rows[fields["reservation_id"]] = fields
            elif event["kind"] in {"settled", "ambiguous_terminal", "released"}:
                fields = event["fields"]
                terminal_rows[fields["reservation_id"]] = event
                if event["kind"] == "released":
                    continue
                if "outcome_path" in fields:
                    service = reservation_rows[fields["reservation_id"]]["service"]
                    self._validate_lane_path(
                        service, self.service_roots[service], fields["outcome_path"]
                    )
                    if (
                        fields["outcome_path"]
                        != reservation_rows[fields["reservation_id"]]["outcome_path"]
                    ):
                        raise CoordinatorError("terminal_outcome_path_mismatch")
                    self._verify_artifact(
                        fields["outcome_path"], fields["outcome_sha256"], "outcome"
                    )
                elif event["kind"] == "settled":
                    raise CoordinatorError("settled_outcome_path_missing")
        for reservation_id, fields in reservation_rows.items():
            intent_path = fields["intent_path"]
            intent_exists = self.writer.exists(intent_path)
            outcome_exists = self.writer.exists(fields["outcome_path"])
            if outcome_exists and not intent_exists:
                raise CoordinatorError("outcome_artifact_without_intent")
            if intent_exists:
                self._verify_artifact(intent_path, fields["intent_sha256"], "intent")
                continue
            terminal = terminal_rows.get(reservation_id)
            proven_released = (
                terminal is not None
                and terminal["kind"] == "released"
                and terminal["fields"].get("outcome") == "pre_intent_absent"
            )
            if (
                reservation_id in allow_missing_intent_ids
                or (allow_missing_pending_intents and terminal is None)
                or proven_released
            ):
                continue
            raise CoordinatorError("intent_artifact_missing")
        return raw, events

    def _verify_artifact(self, path: str, expected_sha256: Any, label: str) -> None:
        if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
            raise CoordinatorError(f"{label}_artifact_digest_invalid")
        try:
            payload = self.writer.read_bytes(path)
        except Exception as exc:
            raise CoordinatorError(f"{label}_artifact_missing") from exc
        if sha256(payload) != expected_sha256:
            raise CoordinatorError(f"{label}_artifact_drift")

    def _append_event(
        self, raw: bytes, events: list[dict[str, Any]], kind: str, fields: Mapping[str, Any]
    ) -> dict[str, Any]:
        previous = events[-1]["event_sha256"] if events else None
        event = _event(
            seq=len(events),
            kind=kind,
            at_ns=self.now_ns(),
            previous=previous,
            fields=fields,
        )
        if len(raw) + len(canonical(event)) > 64_000_000:
            raise CoordinatorError("coordinator_ledger_cap_exceeded")
        self._assert_no_v3_cutover()
        self.writer.append(LEDGER_PATH, canonical(event))
        return event

    @staticmethod
    def _pending_by_service(
        events: list[dict[str, Any]],
        allocated: Mapping[str, int],
        *,
        exclude_reservation_id: str | None = None,
    ) -> tuple[dict[str, int], int, dict[str, dict[str, Any]], set[str]]:
        reservations, terminal = _reservation_state(events)
        pending = {service: 0 for service in _SERVICES}
        for reservation_id, fields in reservations.items():
            if reservation_id not in terminal and reservation_id != exclude_reservation_id:
                service = fields["service"]
                consumed = max(0, allocated[service] - fields["allocated_before_bytes"])
                pending[service] += max(0, fields["reserved_bytes"] - consumed)
        pending_coordinator = (
            sum(reservation_id not in terminal for reservation_id in reservations)
            * COORDINATOR_APPEND_BLOCK_BYTES
        )
        if exclude_reservation_id is not None and exclude_reservation_id not in terminal:
            pending_coordinator -= COORDINATOR_APPEND_BLOCK_BYTES
        return pending, pending_coordinator, reservations, terminal

    def _capacity_check(
        self,
        *,
        service: str,
        new_reservation: int,
        allocated: Mapping[str, int],
        free: int,
        pending: Mapping[str, int],
        pending_coordinator: int,
        coordinator_append_bytes: int,
    ) -> None:
        if (
            type(new_reservation) is not int
            or new_reservation < 0
            or service not in self.service_roots
            or type(coordinator_append_bytes) is not int
            or coordinator_append_bytes < 0
        ):
            raise CoordinatorError("reservation_bounds_invalid")
        for name in _SERVICES:
            lane_new = new_reservation if name == service else 0
            if allocated[name] + pending[name] + lane_new > _SERVICE_CAPS[name]:
                raise CoordinatorError(f"{name}_capacity_exceeded")
        if (
            allocated["coordinator"] + pending_coordinator + coordinator_append_bytes
            > _SERVICE_CAPS["coordinator"]
        ):
            raise CoordinatorError("coordinator_capacity_exceeded")
        protected = (
            sum(_FIXED_RESERVES.values())
            + sum(pending.values())
            + pending_coordinator
            + new_reservation
            + coordinator_append_bytes
        )
        if free < protected:
            raise CoordinatorError("shared_free_reserve_insufficient")

    @staticmethod
    def _validate_lane_path(service: str, root: str, relative: str) -> None:
        if (
            not _relative_path(relative)
            or not _relative_path(root)
            or not relative.startswith(root + "/")
            or PurePosixPath(relative).parts[: len(PurePosixPath(root).parts)]
            != PurePosixPath(root).parts
            or len(PurePosixPath(relative).parts) <= len(PurePosixPath(root).parts)
        ):
            raise CoordinatorError("lane_path_outside_service_root")
        if relative == f"{root}/.capacity-service.lock":
            raise CoordinatorError("lane_path_reserved")

    def reserve_and_persist_intent(
        self,
        *,
        service: str,
        reservation_id: str,
        request_id: str,
        intent_path: str,
        outcome_path: str,
        intent_bytes: bytes,
        intent_sha256: str,
        reserve_bytes: int,
        lane_lease: LaneLockLease | None = None,
        persist_intent: OutcomeWriter | None = None,
    ) -> Reservation:
        """Reserve capacity and create exact prehashed lane intent under both locks."""
        if service not in self.service_roots:
            raise CoordinatorError("service_invalid")
        root = self.service_roots[service]
        self._validate_lane_path(service, root, intent_path)
        self._validate_lane_path(service, root, outcome_path)
        if (
            not isinstance(reservation_id, str)
            or not reservation_id
            or not isinstance(request_id, str)
            or not request_id
            or not isinstance(intent_bytes, bytes)
            or not isinstance(intent_sha256, str)
            or not _SHA256.fullmatch(intent_sha256)
            or sha256(intent_bytes) != intent_sha256
            or type(reserve_bytes) is not int
            or reserve_bytes <= 0
        ):
            raise CoordinatorError("intent_preimage_invalid")
        if lane_lease is None:
            raise CoordinatorError("lane_lock_lease_required")
        self._validate_lane_lease(service, lane_lease)
        with self._shared_lock(service, lane_lease):
            raw, events = self._load_ledger()
            reservations, _ = _reservation_state(events)
            if reservation_id in reservations:
                raise CoordinatorError("reservation_id_already_used")
            if any(
                event["kind"] == "reserved"
                and event["fields"]["service"] == service
                and event["fields"]["request_id"] == request_id
                for event in events
            ):
                raise CoordinatorError("service_request_id_reused")
            allocated, free = self._snapshot()
            pending, pending_coordinator, _, _ = self._pending_by_service(events, allocated)
            self._capacity_check(
                service=service,
                new_reservation=reserve_bytes,
                allocated=allocated,
                free=free,
                pending=pending,
                pending_coordinator=pending_coordinator,
                coordinator_append_bytes=2 * COORDINATOR_APPEND_BLOCK_BYTES,
            )
            fields = {
                "reservation_id": reservation_id,
                "service": service,
                "request_id": request_id,
                "intent_path": intent_path,
                "outcome_path": outcome_path,
                "intent_sha256": intent_sha256,
                "reserved_bytes": reserve_bytes,
                "intent_bytes_sha256": intent_sha256,
                "allocated_before_bytes": allocated[service],
            }
            self._append_event(raw, events, "reserved", fields)
            try:
                _, latest_events = self._load_ledger(
                    allow_missing_intent_ids=frozenset({reservation_id})
                )
                allocated, free = self._snapshot()
                pending, pending_coordinator, _, _ = self._pending_by_service(
                    latest_events, allocated
                )
                self._capacity_check(
                    service=service,
                    new_reservation=0,
                    allocated=allocated,
                    free=free,
                    pending=pending,
                    pending_coordinator=pending_coordinator,
                    coordinator_append_bytes=0,
                )
                self._assert_no_v3_cutover()
                if persist_intent is None:
                    self.writer.create(intent_path, intent_bytes)
                else:
                    persist_intent(self.writer, intent_path, intent_bytes)
                _, latest_events = self._load_ledger()
                allocated, free = self._snapshot()
                pending, pending_coordinator, _, _ = self._pending_by_service(
                    latest_events, allocated
                )
                self._capacity_check(
                    service=service,
                    new_reservation=0,
                    allocated=allocated,
                    free=free,
                    pending=pending,
                    pending_coordinator=pending_coordinator,
                    coordinator_append_bytes=0,
                )
            except Exception as exc:
                if self.writer.exists(intent_path):
                    event_kind, outcome = "ambiguous_terminal", "intent_persisted_after_error"
                else:
                    event_kind, outcome = "released", "pre_intent_absent"
                latest_raw, latest_events = self._load_ledger(
                    allow_missing_intent_ids=frozenset({reservation_id})
                )
                terminal_fields: dict[str, Any] = {
                    "reservation_id": reservation_id,
                    "outcome": outcome,
                    "outcome_sha256": sha256(outcome.encode()),
                }
                if event_kind == "ambiguous_terminal":
                    allocated, _ = self._snapshot()
                    terminal_fields["actual_allocated_bytes"] = allocated[service]
                self._append_event(
                    latest_raw,
                    latest_events,
                    event_kind,
                    terminal_fields,
                )
                raise CoordinatorError("lane_intent_persistence_failed") from exc
        return Reservation(
            reservation_id=reservation_id,
            service=service,
            request_id=request_id,
            intent_path=intent_path,
            outcome_path=outcome_path,
            intent_sha256=intent_sha256,
            reserved_bytes=reserve_bytes,
            intent_bytes=intent_bytes,
        )

    def settle(
        self,
        reservation: Reservation,
        *,
        outcome: str,
        outcome_bytes: bytes,
        outcome_path: str,
        ambiguous: bool,
        persist_outcome: OutcomeWriter | None = None,
        lane_lease: LaneLockLease | None = None,
    ) -> str:
        """Persist lane outcome first, then rescan and append terminal coordinator state."""
        if (
            not isinstance(outcome_bytes, bytes)
            or not isinstance(outcome, str)
            or not outcome
            or not isinstance(outcome_path, str)
            or type(ambiguous) is not bool
        ):
            raise CoordinatorError("settlement_input_invalid")
        self._validate_lane_path(
            reservation.service, self.service_roots[reservation.service], outcome_path
        )
        if outcome_path != reservation.outcome_path:
            raise CoordinatorError("settlement_outcome_path_mismatch")
        service = reservation.service
        if lane_lease is None:
            lease_context: AbstractContextManager[LaneLockLease] = self.lane_lock(service)
        else:
            self._validate_lane_lease(service, lane_lease)
            lease_context = nullcontext(lane_lease)
        with lease_context as held_lease:
            if held_lease.service != service:
                raise CoordinatorError("lane_lock_lease_invalid")
            with self._shared_lock(service, held_lease):
                _, events = self._load_ledger()
                reservations, terminal = _reservation_state(events)
                if reservation.reservation_id not in reservations:
                    raise CoordinatorError("settlement_reservation_missing")
                if reservation.reservation_id in terminal:
                    raise CoordinatorError("settlement_already_terminal")
                pinned = reservations[reservation.reservation_id]
                if (
                    pinned["service"] != service
                    or pinned["request_id"] != reservation.request_id
                    or pinned["intent_path"] != reservation.intent_path
                    or pinned["outcome_path"] != reservation.outcome_path
                    or pinned["intent_sha256"] != reservation.intent_sha256
                ):
                    raise CoordinatorError("settlement_reservation_binding_invalid")
                self._assert_no_v3_cutover()
                if persist_outcome is None:
                    self.writer.create(outcome_path, outcome_bytes)
                else:
                    persist_outcome(self.writer, outcome_path, outcome_bytes)
            with self._shared_lock(service, held_lease):
                self._verify_root()
                raw, events = self._load_ledger()
                reservations, terminal = _reservation_state(events)
                if reservation.reservation_id not in reservations:
                    raise CoordinatorError("settlement_reservation_missing")
                if reservation.reservation_id in terminal:
                    raise CoordinatorError("settlement_already_terminal")
                allocated, free = self._snapshot()
                pending, pending_coordinator, _, _ = self._pending_by_service(
                    events, allocated, exclude_reservation_id=reservation.reservation_id
                )
                self._capacity_check(
                    service=reservation.service,
                    new_reservation=0,
                    allocated=allocated,
                    free=free,
                    pending=pending,
                    pending_coordinator=pending_coordinator,
                    coordinator_append_bytes=COORDINATOR_APPEND_BLOCK_BYTES,
                )
                kind = "ambiguous_terminal" if ambiguous else "settled"
                self._append_event(
                    raw,
                    events,
                    kind,
                    {
                        "reservation_id": reservation.reservation_id,
                        "outcome": outcome,
                        "outcome_sha256": sha256(outcome_bytes),
                        "outcome_path": outcome_path,
                        "actual_allocated_bytes": allocated[service],
                    },
                )
                return kind

    def recover(self, *, intent_exists: Callable[[Writer, str], bool]) -> dict[str, int]:
        """Release only proven pre-intent reservations; terminalize all existing intents."""
        released = ambiguous = 0
        # Ledger may contain several services. Acquire all service locks in the same
        # canonical order used by every recovery, then the shared coordinator lock.
        self._verify_root()
        with self._all_service_locks():
            with self.writer.lock(LOCK_PATH, blocking=True):
                self._verify_root()
                raw, events = self._load_ledger(allow_missing_pending_intents=True)
                reservations, terminal = _reservation_state(events)
                for reservation_id, fields in reservations.items():
                    if reservation_id in terminal:
                        continue
                    exists = intent_exists(self.writer, fields["intent_path"])
                    kind = "ambiguous_terminal" if exists else "released"
                    outcome = "intent_exists_after_recovery" if exists else "pre_intent_absent"
                    raw, events = self._load_ledger(allow_missing_pending_intents=True)
                    allocated, free = self._snapshot()
                    pending, pending_coordinator, _, _ = self._pending_by_service(
                        events, allocated, exclude_reservation_id=reservation_id
                    )
                    self._capacity_check(
                        service=fields["service"],
                        new_reservation=0,
                        allocated=allocated,
                        free=free,
                        pending=pending,
                        pending_coordinator=pending_coordinator,
                        coordinator_append_bytes=COORDINATOR_APPEND_BLOCK_BYTES,
                    )
                    fields_for_terminal: dict[str, Any] = {
                        "reservation_id": reservation_id,
                        "outcome": outcome,
                        "outcome_sha256": sha256(outcome.encode()),
                    }
                    if exists:
                        fields_for_terminal["actual_allocated_bytes"] = allocated[fields["service"]]
                        outcome_path = fields["outcome_path"]
                        if self.writer.exists(outcome_path):
                            outcome_bytes = self.writer.read_bytes(outcome_path)
                            fields_for_terminal["outcome"] = "recovered_outcome_artifact"
                            fields_for_terminal["outcome_sha256"] = sha256(outcome_bytes)
                            fields_for_terminal["outcome_path"] = outcome_path
                        else:
                            fields_for_terminal["outcome"] = "intent_exists_after_recovery"
                            fields_for_terminal["outcome_sha256"] = sha256(outcome.encode())
                    self._append_event(
                        raw,
                        events,
                        kind,
                        fields_for_terminal,
                    )
                    if exists:
                        ambiguous += 1
                    else:
                        released += 1
        return {"released_pre_intent": released, "ambiguous_terminal": ambiguous}

    def recover_service(
        self,
        service: str,
        *,
        intent_exists: Callable[[Writer, str], bool],
        lane_lease: LaneLockLease | None = None,
    ) -> dict[str, int]:
        """Recover one lane without waiting on or modifying other lane state."""
        if service not in self.service_lock_paths:
            raise CoordinatorError("service_invalid")
        if lane_lease is None:
            recovery_lock: AbstractContextManager[Any] = self._locks(service)
        else:
            self._validate_lane_lease(service, lane_lease)
            recovery_lock = self._shared_lock(service, lane_lease)
        released = ambiguous = 0
        with recovery_lock:
            raw, events = self._load_ledger(allow_missing_pending_intents=True)
            reservations, terminal = _reservation_state(events)
            for reservation_id, fields in reservations.items():
                if fields["service"] != service or reservation_id in terminal:
                    continue
                intent_path = fields["intent_path"]
                actual_intent = self.writer.exists(intent_path)
                if intent_exists(self.writer, intent_path) != actual_intent:
                    raise CoordinatorError("intent_exists_probe_drift")
                outcome_path = fields["outcome_path"]
                outcome_present = self.writer.exists(outcome_path)
                if outcome_present and not actual_intent:
                    raise CoordinatorError("outcome_artifact_without_intent")
                kind = "ambiguous_terminal" if actual_intent else "released"
                outcome = "intent_exists_after_recovery" if actual_intent else "pre_intent_absent"
                raw, events = self._load_ledger(allow_missing_pending_intents=True)
                allocated, free = self._snapshot()
                pending, pending_coordinator, _, _ = self._pending_by_service(
                    events, allocated, exclude_reservation_id=reservation_id
                )
                self._capacity_check(
                    service=service,
                    new_reservation=0,
                    allocated=allocated,
                    free=free,
                    pending=pending,
                    pending_coordinator=pending_coordinator,
                    coordinator_append_bytes=COORDINATOR_APPEND_BLOCK_BYTES,
                )
                terminal_fields: dict[str, Any] = {
                    "reservation_id": reservation_id,
                    "outcome": outcome,
                    "outcome_sha256": sha256(outcome.encode()),
                }
                if actual_intent:
                    terminal_fields["actual_allocated_bytes"] = allocated[service]
                    if outcome_present:
                        outcome_bytes = self.writer.read_bytes(outcome_path)
                        terminal_fields["outcome"] = "recovered_outcome_artifact"
                        terminal_fields["outcome_sha256"] = sha256(outcome_bytes)
                        terminal_fields["outcome_path"] = outcome_path
                self._append_event(raw, events, kind, terminal_fields)
                if actual_intent:
                    ambiguous += 1
                else:
                    released += 1
        return {"released_pre_intent": released, "ambiguous_terminal": ambiguous}

    @contextmanager
    def _all_service_locks(self) -> Iterator[None]:
        self._verify_root()
        with ExitStack() as stack:
            for service in sorted(self.service_lock_paths):
                stack.enter_context(
                    self.writer.lock(self.service_lock_paths[service], blocking=True)
                )
            yield
