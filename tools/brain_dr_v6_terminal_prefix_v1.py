#!/usr/bin/env python3
"""Opt-in read-only v6 structural prefix witness; no runtime or semantic replay.

Runtime observations are externally supplied evidence, not collected by this tool.
No lock is acquired and no STOP state is invented. This proves a stable byte
prefix at the held descriptors, not global K, T, source qualification, or restore.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import sys
from collections import Counter
from pathlib import Path
from typing import Any, cast

if __package__ in {None, ""}:
    _ROOT = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from memecoin_research_storage import ReadOnlyStorageGuard, read_only_guard
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json

SCHEMA = "brain-dr-v6-terminal-prefix-v1"
OBSERVATION_SCHEMA = "brain-dr-v6-external-quiescence-observation-v1"
SPEC_SCHEMA = "brain-dr-v6-terminal-prefix-spec-v1"
SERVICE_PREFIX = "services/gecko-market-v1/capture-only-v6"
CONTROL_PREFIX = "control/gecko-capture-v6/releases"
GLOBAL_LOCK = "control/.gecko-capture-v6-global.lock"
COUNTS = {"plan": 1, "intent": 5, "dispatch": 4, "outcome": 5, "confirmation": 5, "missed": 1}
SHA = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[0-9a-f]{32}\Z")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
MAX_JOURNAL = 1024 * 1024
MAX_LINE = 64 * 1024
MAX_FILES = 64


class PrefixAuditError(ValueError):
    """Static, path-free structural audit rejection."""


def _require(ok: bool) -> None:
    if not ok:
        raise PrefixAuditError("prefix_audit_rejected")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw)
    _require(type(value) is dict and canonical_json(value) == raw)
    return cast(dict[str, Any], value)


def _pinned_receipt_json(raw: bytes) -> dict[str, Any]:
    """Parse a hash-pinned repository receipt without requiring canonical whitespace."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            _require(key not in value)
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=unique_object)
    _require(type(value) is dict)
    return cast(dict[str, Any], value)


def _hash(value: Any) -> bool:
    return type(value) is str and SHA.fullmatch(value) is not None


def _self_hash(value: dict[str, Any], key: str) -> str:
    core = dict(value)
    claimed = core.pop(key, None)
    _require(_hash(claimed) and _sha(canonical_json(core)) == claimed)
    return str(claimed)


def _identity(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_uid,
        st.st_nlink,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


class _HeldFiles:
    """Retain read-only no-follow file descriptors and hash them again at the end."""

    def __init__(self, reader: DescriptorWriter, stack: contextlib.ExitStack) -> None:
        self.reader = reader
        self.stack = stack
        self.rows: dict[str, tuple[int, tuple[int, ...], str, int]] = {}

    def read(self, path: str, limit: int) -> bytes:
        _require(path not in self.rows and len(self.rows) < MAX_FILES)
        parent, _, name = path.rpartition("/")
        parent_fd = self.reader.open_directory(parent)
        self.stack.callback(os.close, parent_fd)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        self.stack.callback(os.close, fd)
        st = os.fstat(fd)
        _require(_identity(self.reader.stat(path)) == _identity(st))
        raw = self._bytes(fd, limit)
        self.rows[path] = (fd, _identity(st), _sha(raw), limit)
        return raw

    def _bytes(self, fd: int, limit: int) -> bytes:
        before = os.fstat(fd)
        _require(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and before.st_uid == os.getuid()
            and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_dev == self.reader.device
            and before.st_size <= limit
        )
        os.lseek(fd, 0, os.SEEK_SET)
        parts = []
        count = 0
        while chunk := os.read(fd, min(65536, limit + 1 - count)):
            parts.append(chunk)
            count += len(chunk)
            _require(count <= limit)
        _require(_identity(before) == _identity(os.fstat(fd)) and count == before.st_size)
        return b"".join(parts)

    def check(self) -> None:
        for path, (fd, identity, digest, limit) in self.rows.items():
            _require(
                _identity(os.fstat(fd)) == identity
                and _identity(self.reader.stat(path)) == identity
                and _sha(self._bytes(fd, limit)) == digest
            )


def _members(reader: DescriptorWriter, prefix: str) -> tuple[set[str], set[str]]:
    """Bound traversal to this release; never inventory unrelated BrainData."""
    files: set[str] = set()
    directory_paths: set[str] = set()
    directories = 0

    def visit(path: str, depth: int) -> None:
        nonlocal directories
        directories += 1
        directory_paths.add(path)
        _require(depth <= 4 and directories <= 16)
        fd = reader.open_directory(path)
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    _require(len(files) < MAX_FILES and entry.name not in {".", ".."})
                    child = f"{path}/{entry.name}"
                    st = reader.stat(child)
                    if stat.S_ISDIR(st.st_mode):
                        visit(child, depth + 1)
                    else:
                        files.add(child)
        finally:
            os.close(fd)

    visit(prefix, 0)
    return files, directory_paths


def _events(raw: bytes) -> list[dict[str, Any]]:
    _require(0 < len(raw) <= MAX_JOURNAL and raw.endswith(b"\n"))
    lines = raw.splitlines(keepends=True)
    _require(len(lines) == 21 and all(len(line) <= MAX_LINE for line in lines))
    rows = []
    previous = None
    at_ns = 0
    for seq, line in enumerate(lines):
        row = _json(line)
        _require(
            set(row)
            == {
                "schema",
                "schema_version",
                "seq",
                "kind",
                "at_ns",
                "previous_sha256",
                "fields",
                "event_sha256",
            }
        )
        _require(
            row["schema"] == "brain-senses-gecko-capture-event-v6"
            and type(row["schema_version"]) is int
            and row["schema_version"] == 6
            and type(row["seq"]) is int
            and row["seq"] == seq
            and type(row["at_ns"]) is int
            and row["at_ns"] > 0
            and row["at_ns"] >= at_ns
            and row["previous_sha256"] == previous
            and type(row["fields"]) is dict
            and row["kind"] in COUNTS
        )
        previous = _self_hash(row, "event_sha256")
        at_ns = row["at_ns"]
        rows.append(row)
    _require(Counter(row["kind"] for row in rows) == COUNTS)
    return rows


def _observations(raw: bytes, expected: dict[str, str]) -> dict[str, Any]:
    value = _json(raw)
    _require(
        set(value)
        == {
            "schema",
            "observed_ns",
            "labels",
            "active_callback_pids",
            "active_callback_fds",
            "plist_sha256",
        }
        and value["schema"] == OBSERVATION_SCHEMA
        and type(value["observed_ns"]) is int
        and value["observed_ns"] > 0
        and type(value["active_callback_pids"]) is int
        and value["active_callback_pids"] == 0
        and type(value["active_callback_fds"]) is int
        and value["active_callback_fds"] == 0
        and value["plist_sha256"] == expected
        and type(value["labels"]) is dict
        and set(value["labels"]) == set(expected)
    )
    for row in value["labels"].values():
        _require(
            type(row) is dict
            and set(row) == {"disabled", "loaded", "pid"}
            and row["disabled"] is True
            and row["loaded"] is False
            and row["pid"] is None
        )
    return value


def _audit(
    reader: DescriptorWriter,
    guard: ReadOnlyStorageGuard,
    spec: dict[str, Any],
    *,
    quiescence_raw: bytes,
    prequiescence_raw: bytes,
    pre_observation_raw: bytes,
    post_observation_raw: bytes,
) -> dict[str, Any]:
    _require(
        set(spec)
        == {
            "schema",
            "volume_uuid",
            "root_id",
            "quiescence_sha256",
            "prequiescence_sha256",
            "journal_sha256",
        }
        and spec["schema"] == SPEC_SCHEMA
        and type(spec["volume_uuid"]) is str
        and UUID.fullmatch(spec["volume_uuid"]) is not None
        and type(spec["root_id"]) is str
        and ID.fullmatch(spec["root_id"]) is not None
        and all(
            _hash(spec[key])
            for key in ("quiescence_sha256", "prequiescence_sha256", "journal_sha256")
        )
        and isinstance(guard, ReadOnlyStorageGuard)
        and reader._read_only
        and reader.storage_guard is guard
        and guard.volume_uuid.lower() == spec["volume_uuid"]
        and guard.root_id == spec["root_id"]
    )
    _require(
        _sha(quiescence_raw) == spec["quiescence_sha256"]
        and _sha(prequiescence_raw) == spec["prequiescence_sha256"]
    )
    quiescence = _pinned_receipt_json(quiescence_raw)
    prior = _pinned_receipt_json(prequiescence_raw)
    _require(
        quiescence["schema"] == "portability-dr-senses-quiescence-v1"
        and prior["schema"] == "portability-dr-senses-prequiescence-v1"
        and quiescence["prequiescence_receipt_sha256"] == spec["prequiescence_sha256"]
        and quiescence["journal_sha256"] == prior["journal"]["sha256"] == spec["journal_sha256"]
        and prior["journal"]["kind_counts"] == COUNTS
        and all(type(v) is int for v in prior["journal"]["kind_counts"].values())
        and type(prior["journal"]["unresolved_intents"]) is int
        and prior["journal"]["unresolved_intents"] == 0
        and type(prior["activation"]["source_mismatch_count"]) is int
        and prior["activation"]["source_mismatch_count"] == 1
        and prior["activation"]["source_mismatch_paths"]
        == ["src/memecoin_research_storage/__init__.py"]
        and quiescence["result"]["all_target_labels_absent_for_window"] is True
        and quiescence["result"]["all_target_labels_disabled"] is True
        and quiescence["result"]["journal_unchanged"] is True
        and prior["kingston"]["volume_uuid"] == spec["volume_uuid"]
        and prior["kingston"]["root_id"] == spec["root_id"]
    )
    expected_plists = {row["label"]: row["plist_sha256"] for row in prior["services"]}
    expected_labels = {
        "ai.memecoin.brain.senses.gecko-capture-only-v6",
        "ai.memecoin.brain.senses.gecko-canary-watchdog-v2-3569c27ff9916382c209ea71ef23e345",
        "ai.memecoin.brain.senses.gecko-canary-watchdog-v1-c22205dad614fe5d00b6bbe7869a0c3b",
    }
    _require(
        len(prior["services"]) == 3
        and set(expected_plists) == expected_labels
        and all(_hash(v) for v in expected_plists.values())
        and set(quiescence["disabled_labels"]) == expected_labels
        and len(quiescence["disabled_labels"]) == 3
    )
    before = _observations(pre_observation_raw, expected_plists)
    after = _observations(post_observation_raw, expected_plists)
    _require(
        before["observed_ns"] <= after["observed_ns"]
        and {k: v for k, v in before.items() if k != "observed_ns"}
        == {k: v for k, v in after.items() if k != "observed_ns"}
    )
    release = prior["activation"]["release_id"]
    _require(type(release) is str and ID.fullmatch(release) is not None)
    service = f"{SERVICE_PREFIX}/{release}"
    control = f"{CONTROL_PREFIX}/{release}"
    data_identity = _identity(os.fstat(reader.root_fd))

    with contextlib.ExitStack() as stack:
        directories: dict[str, int] = {}
        for path in (service, control):
            fd = reader.open_directory(path)
            stack.callback(os.close, fd)
            directories[path] = fd
        directory_identities = {p: _identity(os.fstat(fd)) for p, fd in directories.items()}

        def boundary() -> None:
            guard.revalidate()
            reader.assert_storage_binding(guard)
            _require(_identity(os.fstat(reader.root_fd)) == data_identity)
            for path, fd in directories.items():
                _require(
                    _identity(reader.stat(path)) == directory_identities[path]
                    and _identity(os.fstat(fd)) == directory_identities[path]
                )

        boundary()
        members_before = _members(reader, service)
        control_before = _members(reader, control)
        files = _HeldFiles(reader, stack)
        activation = _json(files.read(f"{control}/activation.json", 256 * 1024))
        manifest = _json(files.read(f"{control}/source-manifest.json", 1024 * 1024))
        epoch = _json(files.read(f"{service}/epoch.json", 256 * 1024))
        policy = _json(files.read(f"{service}/policy.json", 512 * 1024))
        genesis = _json(files.read(f"{service}/genesis.json", 256 * 1024))
        _self_hash(activation, "activation_sha256")
        epoch_hash = _self_hash(epoch, "epoch_sha256")
        policy_hash = _self_hash(policy, "policy_sha256")
        manifest_hash = _self_hash(manifest, "source_manifest_sha256")
        _require(
            type(manifest["source_map"]) is dict
            and len(manifest["source_map"]) == 15
            and set(manifest["source_paths"]) == set(manifest["source_map"])
            and len(manifest["source_paths"]) == 15
            and all(_hash(v) for v in manifest["source_map"].values())
        )
        _self_hash(genesis, "genesis_sha256")
        for value, schema in (
            (activation, "brain-senses-gecko-capture-v6-activation"),
            (manifest, "brain-senses-gecko-capture-v6-source-manifest"),
            (epoch, "brain-senses-gecko-capture-epoch-v6"),
            (policy, "brain-senses-gecko-capture-policy-v6"),
            (genesis, "brain-senses-gecko-capture-genesis-v6"),
        ):
            _require(
                value["schema"] == schema
                and type(value["schema_version"]) is int
                and value["schema_version"] == 6
            )
        _require(
            activation["release_id"] == epoch["release_id"] == release
            and activation["service_root"] == epoch["root"] == service
            and activation["capture_authorized"] is True
            and activation["activation_id"] == prior["activation"]["activation_id"]
            and epoch_hash
            == prior["activation"]["epoch_sha256"]
            == activation["epoch_sha256"]
            == genesis["epoch_sha256"]
            and policy_hash
            == activation["policy_sha256"]
            == epoch["policy_sha256"]
            == genesis["policy_sha256"]
            and manifest_hash
            == activation["source_manifest_sha256"]
            == prior["activation"]["source_manifest_sha256"]
            and activation["source_commit"]
            == manifest["source_commit"]
            == prior["activation"]["source_commit"]
        )
        journal_raw = files.read(f"{service}/events.jsonl", MAX_JOURNAL)
        _require(_sha(journal_raw) == spec["journal_sha256"])
        events = _events(journal_raw)
        expected = {
            f"{service}/{name}"
            for name in ("epoch.json", "policy.json", "genesis.json", "events.jsonl", ".lane.lock")
        }
        # Existing lock bytes/identity only: no flock, O_CREAT, or writable FD.
        _require(
            files.read(f"{service}/.lane.lock", 0) == b"" and files.read(GLOBAL_LOCK, 0) == b""
        )
        prior_path = epoch["prior_usage"]["path"]
        _require(
            type(prior_path) is str
            and re.fullmatch(r"control/gecko-v6-prior-usage/[0-9a-f]{64}\.json", prior_path)
            is not None
        )
        prior_path_full = f"{service}/{prior_path}"
        _require(
            _sha(files.read(prior_path_full, 4 * 1024 * 1024)) == epoch["prior_usage"]["sha256"]
        )
        expected.add(prior_path_full)
        plans: dict[str, dict[str, Any]] = {}
        intents: dict[str, dict[str, Any]] = {}
        outcomes: dict[str, dict[str, Any]] = {}
        confirmations: set[str] = set()
        dispatches: set[str] = set()
        raw_count = 0
        for event in events:
            fields, kind = event["fields"], event["kind"]
            if kind == "plan":
                close = fields["close_ns"]
                _require(type(close) is int and close > 0)
                path = f"plans/{close}.json"
                _require(fields["plan_path"] == path and _hash(fields["plan_sha256"]))
                _require(
                    _sha(files.read(f"{service}/{path}", 256 * 1024)) == fields["plan_file_sha256"]
                )
                plans[fields["plan_sha256"]] = event
                expected.add(f"{service}/{path}")
                continue
            if kind == "missed":
                _require(fields["plan_sha256"] in plans)
                continue
            request = fields["request_id"]
            _require(_hash(request))
            if kind == "intent":
                path = f"requests/{request}.json"
                _require(
                    request not in intents
                    and fields["request_path"] == path
                    and fields["plan_sha256"] in plans
                    and fields["close_ns"] == plans[fields["plan_sha256"]]["fields"]["close_ns"]
                )
                request_raw = files.read(f"{service}/{path}", 64 * 1024)
                _require(_sha(request_raw) == fields["request_sha256"])
                intents[request] = event
                expected.add(f"{service}/{path}")
            elif kind == "dispatch":
                _require(
                    request in intents and request not in dispatches and request not in outcomes
                )
                dispatches.add(request)
            elif kind == "outcome":
                _require(request in intents and request not in outcomes)
                outcomes[request] = event
                path = fields["raw_path"]
                if path is None:
                    _require(
                        fields["raw_sha256"] is None
                        and type(fields["raw_bytes"]) is int
                        and fields["raw_bytes"] == 0
                    )
                else:
                    _require(
                        path == f"responses/{request}.bin"
                        and _hash(fields["raw_sha256"])
                        and type(fields["raw_bytes"]) is int
                    )
                    raw = files.read(f"{service}/{path}", 16 * 1024)
                    _require(len(raw) == fields["raw_bytes"] and _sha(raw) == fields["raw_sha256"])
                    expected.add(f"{service}/{path}")
                    raw_count += 1
            elif kind == "confirmation":
                _require(
                    request in outcomes
                    and request not in confirmations
                    and fields["outcome_sha256"] == outcomes[request]["event_sha256"]
                )
                confirmations.add(request)
        expected_directories = {
            service,
            *(
                f"{service}/{name}"
                for name in ("plans", "requests", "responses", "intents", "confirmations")
            ),
        }
        for path in expected:
            parent = path.rpartition("/")[0]
            while parent != service:
                expected_directories.add(parent)
                parent = parent.rpartition("/")[0]
        _require(
            len(intents) == 5
            and set(intents) == set(outcomes) == confirmations
            and len(dispatches) == 4
            and members_before == (expected, expected_directories)
            and control_before
            == ({f"{control}/activation.json", f"{control}/source-manifest.json"}, {control})
        )
        files.check()
        _require(
            _members(reader, service) == members_before
            and _members(reader, control) == control_before
        )
        boundary()
        inventory = [
            {"path_sha256": _sha(p.encode()), "sha256": row[2]}
            for p, row in sorted(files.rows.items())
        ]
        return {
            "schema": SCHEMA,
            "ok": True,
            "scope": "stable_structural_prefix_only",
            "event_count": 21,
            "intent_count": 5,
            "outcome_count": 5,
            "confirmation_count": 5,
            "unresolved_intent_count": 0,
            "opaque_response_count": raw_count,
            "hashed_member_count": len(files.rows),
            "journal_sha256": spec["journal_sha256"],
            "event_tail_sha256": events[-1]["event_sha256"],
            "inventory_sha256": _sha(canonical_json({"members": inventory})),
            "quiescence_receipt_sha256": spec["quiescence_sha256"],
            "pre_observation_sha256": _sha(pre_observation_raw),
            "post_observation_sha256": _sha(post_observation_raw),
            "runtime_observations_external": True,
            "runtime_observations_matching_external_claims_only": True,
            "runtime_observation_bracketing_verified": False,
            "storage_descriptor_stability_only": True,
            "runtime_absence_independently_verified": False,
            "locks_acquired": False,
            "runtime_STOP_identity": False,
            "capture_authorized_remains_true": True,
            "source_manifest_replay_performed": False,
            "source_manifest_mismatch_unresolved": True,
            "semantic_replay_performed": False,
            "provider_io": False,
            "global_K_qualified": False,
            "T_qualified": False,
            "restore_verified": False,
            "erase_ready": False,
        }


def audit_terminal_prefix_read_only(
    storage_config: Path,
    spec: dict[str, Any],
    *,
    quiescence_raw: bytes,
    prequiescence_raw: bytes,
    pre_observation_raw: bytes,
    post_observation_raw: bytes,
) -> dict[str, Any]:
    """Inspect only the pinned release through a read-only storage guard."""
    try:
        with read_only_guard(storage_config) as guard:
            with DescriptorWriter.read_only_from_storage_guard(guard) as reader:
                return _audit(
                    reader,
                    guard,
                    spec,
                    quiescence_raw=quiescence_raw,
                    prequiescence_raw=prequiescence_raw,
                    pre_observation_raw=pre_observation_raw,
                    post_observation_raw=post_observation_raw,
                )
    except Exception:
        raise PrefixAuditError("prefix_audit_rejected") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-config", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--spec-sha256", required=True)
    for name in ("quiescence", "prequiescence", "pre-observation", "post-observation"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args(argv)
    try:

        def read(path: Path) -> bytes:
            with path.open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
            _require(len(raw) <= 1024 * 1024)
            return raw

        spec_raw = read(args.spec)
        _require(_hash(args.spec_sha256) and _sha(spec_raw) == args.spec_sha256)
        result = audit_terminal_prefix_read_only(
            args.storage_config,
            _json(spec_raw),
            quiescence_raw=read(args.quiescence),
            prequiescence_raw=read(args.prequiescence),
            pre_observation_raw=read(args.pre_observation),
            post_observation_raw=read(args.post_observation),
        )
        sys.stdout.buffer.write(canonical_json(result))
        return 0
    except Exception:
        sys.stdout.buffer.write(
            canonical_json(
                {
                    "schema": SCHEMA,
                    "ok": False,
                    "error": "prefix_audit_rejected",
                    "global_K_qualified": False,
                    "T_qualified": False,
                    "erase_ready": False,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
