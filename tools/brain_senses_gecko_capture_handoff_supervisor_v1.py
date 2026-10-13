"""Source-bound, idempotent canary-to-continuous handoff supervisor.

This module contains no launchd, provider, or storage configuration loader. The
owner adapter supplies verified callbacks; this state machine enforces order,
immutable intent journaling, and restart behavior for synthetic qualification.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from tools.brain_senses_external_writer_v1 import canonical_json

SCHEMA = "brain-senses-gecko-capture-handoff-supervisor-v1"
EVENT_SCHEMA = f"{SCHEMA}-event"
VERSION = 1
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
ROOT_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
UUID_RE = re.compile(r"[0-9a-fA-F-]{36}\Z")
COLLECTOR_LABEL_RE = re.compile(r"ai\.memecoin\.brain\.senses\.gecko-capture-only-v[0-9]+\Z")
BUCKET_NS = 900 * 1_000_000_000


class HandoffError(ValueError):
    """A handoff binding or durable phase failed closed."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash(value: Any) -> bool:
    return type(value) is str and HASH_RE.fullmatch(value) is not None


def verify_handoff_certificate(certificate: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an immutable supervisor certificate, including storage identity."""
    required = {
        "schema",
        "schema_version",
        "canary_receipt_sha256",
        "source_manifest_sha256",
        "continuous_release_sha256",
        "continuous_policy_sha256",
        "collector_plist_sha256",
        "watchdog_plist_sha256",
        "storage",
        "predecessor_root",
        "successor_root",
        "predecessor_cursor",
        "predecessor_tail_sha256",
        "preinit_census_sha256",
        "coordinator_prefix_sha256",
        "continuous_label",
        "continuous_program_arguments",
        "first_bucket_close_ns",
        "created_ns",
        "certificate_sha256",
    }
    core = dict(certificate)
    claimed = core.pop("certificate_sha256", None)
    storage = certificate.get("storage")
    if (
        set(certificate) != required
        or certificate.get("schema") != SCHEMA
        or certificate.get("schema_version") != VERSION
        or claimed != sha256(canonical_json(core))
        or not all(
            _hash(certificate.get(field))
            for field in (
                "canary_receipt_sha256",
                "source_manifest_sha256",
                "continuous_release_sha256",
                "continuous_policy_sha256",
                "collector_plist_sha256",
                "watchdog_plist_sha256",
                "predecessor_tail_sha256",
                "preinit_census_sha256",
                "coordinator_prefix_sha256",
            )
        )
        or type(storage) is not dict
        or set(storage) != {"root_id", "volume_uuid", "device", "inode"}
        or type(storage.get("root_id")) is not str
        or ROOT_ID_RE.fullmatch(storage["root_id"]) is None
        or type(storage.get("volume_uuid")) is not str
        or UUID_RE.fullmatch(storage["volume_uuid"]) is None
        or type(storage.get("device")) is not int
        or type(storage.get("inode")) is not int
        or storage["device"] < 0
        or storage["inode"] <= 0
        or type(certificate.get("predecessor_root")) is not str
        or not certificate["predecessor_root"].startswith("services/")
        or type(certificate.get("successor_root")) is not str
        or not certificate["successor_root"].startswith("services/")
        or certificate["successor_root"] == certificate["predecessor_root"]
        or type(certificate.get("predecessor_cursor")) is not int
        or certificate["predecessor_cursor"] < 0
        or COLLECTOR_LABEL_RE.fullmatch(str(certificate.get("continuous_label"))) is None
        or type(certificate.get("continuous_program_arguments")) is not list
        or not certificate["continuous_program_arguments"]
        or type(certificate.get("first_bucket_close_ns")) is not int
        or certificate["first_bucket_close_ns"] <= 0
        or certificate["first_bucket_close_ns"] % BUCKET_NS != 0
        or type(certificate.get("created_ns")) is not int
        or certificate["created_ns"] <= 0
        or certificate["created_ns"] >= certificate["first_bucket_close_ns"]
    ):
        raise HandoffError("handoff_certificate_invalid")
    return dict(certificate)


def verify_event_chain(
    events: Sequence[Mapping[str, Any]], certificate_sha256: str
) -> list[dict[str, Any]]:
    """Replay the only lawful transition sequence: intent, then loaded proof."""
    if not _hash(certificate_sha256):
        raise HandoffError("handoff_certificate_hash_invalid")
    expected_previous = "0" * 64
    allowed = ("bootstrap_intent", "loaded_verified")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(events):
        row = dict(raw)
        core = dict(row)
        claimed = core.pop("event_sha256", None)
        if index >= len(allowed) or (
            set(row)
            != {
                "schema",
                "schema_version",
                "seq",
                "kind",
                "at_ns",
                "certificate_sha256",
                "previous_event_sha256",
                "event_sha256",
            }
            or row.get("schema") != EVENT_SCHEMA
            or row.get("schema_version") != VERSION
            or row.get("seq") != index
            or row.get("kind") != allowed[index]
        ):
            raise HandoffError("handoff_event_order_invalid")
        if (
            type(row.get("at_ns")) is not int
            or row["at_ns"] <= 0
            or row.get("certificate_sha256") != certificate_sha256
            or row.get("previous_event_sha256") != expected_previous
            or claimed != sha256(canonical_json(core))
            or (index > 0 and row.get("at_ns", 0) < normalized[index - 1]["at_ns"])
        ):
            raise HandoffError("handoff_event_hash_invalid")
        expected_previous = claimed
        normalized.append(row)
    if len(normalized) > 2:
        raise HandoffError("handoff_event_count_invalid")
    return normalized


def make_event(
    *, kind: str, seq: int, at_ns: int, certificate_sha256: str, previous_event_sha256: str
) -> dict[str, Any]:
    core = {
        "schema": EVENT_SCHEMA,
        "schema_version": VERSION,
        "seq": seq,
        "kind": kind,
        "at_ns": at_ns,
        "certificate_sha256": certificate_sha256,
        "previous_event_sha256": previous_event_sha256,
    }
    if kind not in {"bootstrap_intent", "loaded_verified"} or not _hash(certificate_sha256):
        raise HandoffError("handoff_event_input_invalid")
    return {**core, "event_sha256": sha256(canonical_json(core))}


def _append_and_replay(
    event: Mapping[str, Any],
    *,
    certificate_sha256: str,
    append_event_create_only: Callable[[Mapping[str, Any]], Any],
    read_events: Callable[[], Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    append_event_create_only(event)
    replayed = verify_event_chain(read_events(), certificate_sha256)
    if not replayed or replayed[-1] != dict(event):
        raise HandoffError("handoff_event_not_durable")
    return replayed


def run_handoff(
    *,
    certificate: Mapping[str, Any],
    now_ns: int,
    read_events: Callable[[], Sequence[Mapping[str, Any]]],
    append_event_create_only: Callable[[Mapping[str, Any]], Any],
    with_supervisor_lock: Callable[[], Any],
    with_ordered_locks: Callable[[], Any],
    reverify_predecessor_and_global_census: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    create_successor_or_verify: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    launch_snapshot: Callable[[], Mapping[str, Any]],
    bootstrap_exact: Callable[[str, Sequence[str]], Any],
    prove_absent_means_no_callback: Callable[[], bool],
) -> dict[str, Any]:
    """Prepare and bootstrap once; recovery inspects launchd before any retry.

    `with_supervisor_lock` serializes this local transition. Inside it,
    `with_ordered_locks` acquires storage -> Gecko lane -> successor root for
    replay/create-root work. Those three locks are released before launchd.
    """
    bound = verify_handoff_certificate(certificate)
    if type(now_ns) is not int or now_ns <= 0:
        raise HandoffError("handoff_clock_invalid")
    cert_hash = bound["certificate_sha256"]
    if bound["first_bucket_close_ns"] <= now_ns:
        raise HandoffError("handoff_first_bucket_not_future")
    with with_supervisor_lock():
        events = verify_event_chain(read_events(), cert_hash)
        with with_ordered_locks():
            pre = reverify_predecessor_and_global_census(bound)
            if (
                pre.get("verified") is not True
                or pre.get("canary_receipt_sha256") != bound["canary_receipt_sha256"]
                or pre.get("predecessor_tail_sha256") != bound["predecessor_tail_sha256"]
                or pre.get("preinit_census_sha256") != bound["preinit_census_sha256"]
                or pre.get("coordinator_prefix_sha256") != bound["coordinator_prefix_sha256"]
                or pre.get("old_collector_absent") is not True
                or pre.get("process_quiescent") is not True
            ):
                raise HandoffError("handoff_predecessor_replay_failed")
            prepared = create_successor_or_verify(bound)
            if (
                prepared.get("successor_root") != bound["successor_root"]
                or prepared.get("successor_verified") is not True
                or prepared.get("certificate_sha256") != cert_hash
                or (not events and prepared.get("empty_attempt_prefix_verified") is not True)
                or (not events and prepared.get("attempt_count") != 0)
                or prepared.get("root_identity") != bound["storage"]
                or prepared.get("global_census_stable") is not True
                or prepared.get("provider_io") is not False
            ):
                raise HandoffError("handoff_successor_preparation_failed")

        if events and events[-1]["kind"] == "loaded_verified":
            snapshot = launch_snapshot()
            if _snapshot_matches(snapshot, bound):
                return {
                    "status": "already_loaded_verified",
                    "event_tail_sha256": events[-1]["event_sha256"],
                }
            raise HandoffError("handoff_loaded_identity_changed")

        if not events:
            event = make_event(
                kind="bootstrap_intent",
                seq=0,
                at_ns=now_ns,
                certificate_sha256=cert_hash,
                previous_event_sha256="0" * 64,
            )
            events = _append_and_replay(
                event,
                certificate_sha256=cert_hash,
                append_event_create_only=append_event_create_only,
                read_events=read_events,
            )

        snapshot = launch_snapshot()
        if not _snapshot_matches(snapshot, bound):
            if snapshot.get("loaded") is not False or prove_absent_means_no_callback() is not True:
                raise HandoffError("handoff_launch_state_ambiguous")
            # The durable bootstrap intent already exists. A proven absent job and
            # no callback evidence make a single recovery bootstrap safe.
            bootstrap_exact(bound["continuous_label"], bound["continuous_program_arguments"])
            snapshot = launch_snapshot()
        if not _snapshot_matches(snapshot, bound):
            raise HandoffError("handoff_loaded_identity_mismatch")
        verified = make_event(
            kind="loaded_verified",
            seq=1,
            at_ns=now_ns,
            certificate_sha256=cert_hash,
            previous_event_sha256=events[-1]["event_sha256"],
        )
        _append_and_replay(
            verified,
            certificate_sha256=cert_hash,
            append_event_create_only=append_event_create_only,
            read_events=read_events,
        )
        return {"status": "loaded_verified", "event_tail_sha256": verified["event_sha256"]}


def _snapshot_matches(snapshot: Mapping[str, Any], certificate: Mapping[str, Any]) -> bool:
    return (
        snapshot.get("loaded") is True
        and snapshot.get("label") == certificate.get("continuous_label")
        and snapshot.get("program_arguments") == certificate.get("continuous_program_arguments")
        and snapshot.get("plist_sha256") == certificate.get("collector_plist_sha256")
        and snapshot.get("source_manifest_sha256") == certificate.get("source_manifest_sha256")
        and snapshot.get("uid") == os.getuid()
        and type(snapshot.get("pid")) is int
        and snapshot["pid"] > 0
        and snapshot.get("competing_gecko_labels") == []
    )
