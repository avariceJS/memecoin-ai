#!/usr/bin/env python3
"""Verify the persisted Agave metadata graph and publish an immutable audit receipt.

This command is read-only except for one create-only receipt beneath the verified Kingston data
root. It never reads or reconstructs raw Cargo stdout and never invokes Cargo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

_SOURCE_ROOT = str(Path(__file__).resolve().parents[1] / "src")
_TOOLS_ROOT = str(Path(__file__).resolve().parent)
for _root in (_SOURCE_ROOT, _TOOLS_ROOT):
    if _root not in sys.path:
        sys.path.insert(0, _root)

import brain_senses_agave_metadata_preflight_v2 as preflight  # noqa: E402
from brain_senses_agave_dependency_fetch_v1 import (  # noqa: E402
    EXPECTED_AGAVE_HEAD,
    EXPECTED_COMPOSITE_DIFF,
    EXPECTED_POST_V5_LOCK,
    EXPECTED_REGISTRY_SOURCE,
)
from brain_senses_build_storage_guard import MIN_FREE_BYTES  # noqa: E402

from memecoin_research_storage import writer_guard  # noqa: E402

EXPECTED_ROOT_ID = "9428695af6d4f46c08e1a444cdc66de8"
EXPECTED_VOLUME_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"
V1_FAILURE_RELATIVE = Path(
    "qualifications/agave-v3.0.0-token2022-metadata-v1-20261012/metadata-preflight-failure-v1.json"
)
V1_FAILURE_SHA256 = "060a604d86edc2a229e7ca24b83967ada72ba44e345c59a74235fe61ad70e0d8"
V1_CACHE_SOURCE_SHA256 = "8d7b00b284e11a416ff8c852b4ead27f3945b32ce88a78837f67eb78c2229450"
SOURCE_RELATIVE = Path("qualifications/agave-v3.0.0-token2022-runtime-20261012/source")
CACHE_SOURCE_RELATIVE = Path("qualifications/agave-v3.0.0-token2022-runtime-20261012/cargo-home")
TARGET_RELATIVE = preflight.METADATA_STAGE_RELATIVE / "target"
TMP_RELATIVE = preflight.METADATA_STAGE_RELATIVE / "tmp"
PROCESS_RELATIVE = preflight.METADATA_STAGE_RELATIVE / "metadata-process-capture.json"
GRAPH_RELATIVE = preflight.METADATA_STAGE_RELATIVE / "resolved-graph.json"
ADJUDICATION_RELATIVE = preflight.METADATA_STAGE_RELATIVE / "metadata-adjudication-v1.json"
MAX_FILE_BYTES = 64 * 1024**2
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_GRAPH_COUNTS = {
    "lock_tuple_count": 1_075,
    "graph_tuple_count": 960,
    "lock_only_tuple_count": 115,
    "source_null_workspace_member_count": 140,
}
GLOBAL_CACHE_PATH = ".global-cache"
GLOBAL_CACHE_MODE = 0o644
GLOBAL_CACHE_SIZE = 176_128


class AdjudicationError(RuntimeError):
    """Finite, sanitized refusal reason."""

    def __init__(self, code: str) -> None:
        if not re.fullmatch(r"[a-z0-9_]+", code):
            code = "adjudication_failed"
        self.code = code
        super().__init__(code)


def _open_root_from_guard(guard: Any) -> int:
    parts = Path(guard.config.data_root).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise AdjudicationError("storage_root_relative_path")
    current_fd = os.dup(guard.mount_fd)
    try:
        for part in parts:
            next_fd = os.open(
                part,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current_fd,
            )
            st = os.fstat(next_fd)
            if not stat.S_ISDIR(st.st_mode) or st.st_dev != os.fstat(guard.mount_fd).st_dev:
                os.close(next_fd)
                raise AdjudicationError("storage_root_identity")
            os.close(current_fd)
            current_fd = next_fd
        root_st = os.fstat(current_fd)
        if (
            root_st.st_uid != os.getuid()
            or stat.S_IMODE(root_st.st_mode) != 0o700
            or guard.root_id != EXPECTED_ROOT_ID
            or guard.volume_uuid != EXPECTED_VOLUME_UUID
        ):
            raise AdjudicationError("storage_binding_mismatch")
        marker_fd = os.open(
            ".research-root.json",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=current_fd,
        )
        try:
            marker_st = os.fstat(marker_fd)
            if (
                not stat.S_ISREG(marker_st.st_mode)
                or marker_st.st_dev != root_st.st_dev
                or marker_st.st_uid != os.getuid()
                or stat.S_IMODE(marker_st.st_mode) != 0o600
                or marker_st.st_nlink != 1
                or marker_st.st_size > 4096
            ):
                raise AdjudicationError("storage_marker_identity")
            marker_raw = _read_fd(marker_fd, 4096)
        finally:
            os.close(marker_fd)
        marker = preflight._strict_json(marker_raw, "storage_marker")
        if marker != {
            "data_root": "/".join(parts),
            "root_id": EXPECTED_ROOT_ID,
            "version": 1,
            "volume_uuid": EXPECTED_VOLUME_UUID,
        }:
            raise AdjudicationError("storage_marker_mismatch")
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _read_fd(file_fd: int, cap: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(file_fd, min(64 * 1024, cap + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > cap:
            raise AdjudicationError("artifact_size_cap")
    return b"".join(chunks)


def _read_relative(root_fd: int, relative: Path, *, device: int, cap: int) -> bytes:
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} or "\x00" in part for part in relative.parts)
    ):
        raise AdjudicationError("artifact_relative_path")
    parent_fd = preflight._open_directory_beneath(
        root_fd, Path(*relative.parts[:-1]), device=device
    )
    try:
        file_fd = os.open(
            relative.parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd
        )
        try:
            st = os.fstat(file_fd)
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_dev != device
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600
                or st.st_nlink != 1
                or st.st_size > cap
            ):
                raise AdjudicationError("artifact_file_identity")
            raw = _read_fd(file_fd, cap)
            if len(raw) != st.st_size:
                raise AdjudicationError("artifact_file_changed")
            return raw
        finally:
            os.close(file_fd)
    finally:
        os.close(parent_fd)


def _canonical_exact(raw: bytes, label: str) -> dict[str, Any]:
    parsed = preflight._strict_json(raw, label)
    if not isinstance(parsed, dict) or preflight._canonical(parsed) != raw:
        raise AdjudicationError(f"{label}_not_canonical")
    return parsed


def _verify_artifacts(
    process_raw: bytes,
    graph_raw: bytes,
    lock_path: Path,
    *,
    root_id: str,
    volume_uuid: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    process = _canonical_exact(process_raw, "process_receipt")
    expected_process_keys = {
        "schema",
        "schema_version",
        "storage_root_id",
        "volume_uuid",
        "stdout_sha256",
        "stdout_bytes",
        "stdout_truncated",
        "stdout_read_error",
        "stdout_complete",
        "stdout_hash_scope",
        "stderr_sha256",
        "stderr_bytes",
        "stderr_truncated",
        "stderr_read_error",
        "stderr_complete",
        "stderr_hash_scope",
        "stderr_sanitized",
        "exit_status",
        "classification",
    }
    if (
        set(process) != expected_process_keys
        or process.get("schema") != "brain-senses-agave-metadata-process-capture-v2"
        or process.get("schema_version") != 2
        or process.get("storage_root_id") != root_id
        or process.get("volume_uuid") != volume_uuid
        or process.get("exit_status") != 0
        or process.get("classification") != "process_completed"
        or process.get("stdout_complete") is not True
        or process.get("stderr_complete") is not True
        or process.get("stdout_hash_scope") != "full_stream"
        or process.get("stderr_hash_scope") != "full_stream"
        or process.get("stdout_truncated") is not False
        or process.get("stderr_truncated") is not False
        or process.get("stdout_read_error") is not False
        or process.get("stderr_read_error") is not False
        or not isinstance(process.get("stdout_bytes"), int)
        or not isinstance(process.get("stderr_bytes"), int)
        or not 0 < process["stdout_bytes"] <= preflight.MAX_STDOUT_BYTES
        or not 0 <= process["stderr_bytes"] <= preflight.MAX_STDERR_BYTES
        or not isinstance(process.get("stderr_sanitized"), str)
        or len(process["stderr_sanitized"]) > preflight.MAX_DIAGNOSTIC_CHARS
        or not isinstance(process.get("stdout_sha256"), str)
        or not _SHA256.fullmatch(process["stdout_sha256"])
        or not isinstance(process.get("stderr_sha256"), str)
        or not _SHA256.fullmatch(process["stderr_sha256"])
    ):
        raise AdjudicationError("process_receipt_invalid")
    graph = _canonical_exact(graph_raw, "graph_artifact")
    counts = preflight._validate_resolved_graph(graph, lock_path)
    if counts != EXPECTED_GRAPH_COUNTS:
        raise AdjudicationError("graph_accounting_mismatch")
    return process, graph, counts


def _read_directory_names(root_fd: int, relative: Path, *, device: int) -> list[str]:
    directory_fd = preflight._open_directory_beneath(root_fd, relative, device=device)
    try:
        return sorted(os.listdir(directory_fd))
    finally:
        os.close(directory_fd)


def _cache_evidence(
    source_rows: list[dict[str, Any]],
    copied_rows: list[dict[str, Any]],
    *,
    expected_registry_crates: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate the one approved opaque marker change plus ordinary crate extraction."""
    source_by_path = {row["path"]: row for row in source_rows}
    copied_by_path = {row["path"]: row for row in copied_rows}
    before = source_by_path.get(GLOBAL_CACHE_PATH)
    after = copied_by_path.get(GLOBAL_CACHE_PATH)
    if (
        before is None
        or after is None
        or before.get("kind") != "file"
        or after.get("kind") != "file"
        or before.get("mode") != GLOBAL_CACHE_MODE
        or after.get("mode") != GLOBAL_CACHE_MODE
        or before.get("size") != GLOBAL_CACHE_SIZE
        or after.get("size") != GLOBAL_CACHE_SIZE
        or not isinstance(before.get("sha256"), str)
        or not _SHA256.fullmatch(before["sha256"])
        or not isinstance(after.get("sha256"), str)
        or not _SHA256.fullmatch(after["sha256"])
        or before["sha256"] == after["sha256"]
    ):
        raise AdjudicationError("global_cache_bookkeeping_mismatch")
    source_without_global = [row for row in source_rows if row["path"] != GLOBAL_CACHE_PATH]
    copied_without_global = [row for row in copied_rows if row["path"] != GLOBAL_CACHE_PATH]
    extraction = preflight._cache_delta(
        source_without_global,
        copied_without_global,
        expected_registry_crates=expected_registry_crates,
    )
    return extraction, {
        "path": GLOBAL_CACHE_PATH,
        "classification": "opaque_cargo_bookkeeping",
        "before_sha256": before["sha256"],
        "after_sha256": after["sha256"],
        "mode": GLOBAL_CACHE_MODE,
        "size": GLOBAL_CACHE_SIZE,
    }


def verify_and_publish(config_path: Path) -> dict[str, Any]:
    """Read pinned Kingston evidence and publish one create-only adjudication receipt."""
    with writer_guard(config_path, min_free_bytes=MIN_FREE_BYTES) as guard:
        guard.revalidate(min_free_bytes=MIN_FREE_BYTES)
        root_fd = _open_root_from_guard(guard)
        try:
            device = os.fstat(root_fd).st_dev
            process_raw = _read_relative(root_fd, PROCESS_RELATIVE, device=device, cap=64 * 1024)
            graph_raw = _read_relative(root_fd, GRAPH_RELATIVE, device=device, cap=2 * 1024**2)
            previous_raw = _read_relative(
                root_fd, V1_FAILURE_RELATIVE, device=device, cap=64 * 1024
            )
            if hashlib.sha256(previous_raw).hexdigest() != V1_FAILURE_SHA256:
                raise AdjudicationError("prior_failure_receipt_hash")
            previous = preflight._strict_json(previous_raw, "prior_failure_receipt")
            if not isinstance(previous, dict):
                raise AdjudicationError("prior_failure_receipt_schema")
            if (
                previous.get("status") != "cargo_metadata_exited_zero_postprocess_rejected"
                or previous.get("failure_code") != "metadata_package_identity_collision"
                or previous.get("cargo_exit_status") != 0
                or previous.get("metadata_invocations") != 1
                or previous.get("source_head") != EXPECTED_AGAVE_HEAD
                or previous.get("source_diff_sha256") != EXPECTED_COMPOSITE_DIFF
                or previous.get("cargo_lock_sha256") != EXPECTED_POST_V5_LOCK
                or previous.get("cache_source_manifest_before_and_after_sha256")
                != V1_CACHE_SOURCE_SHA256
            ):
                raise AdjudicationError("prior_failure_receipt_mismatch")

            root_path = guard.path
            source_path = root_path / SOURCE_RELATIVE
            cargo_home = root_path / preflight.METADATA_STAGE_RELATIVE / "cargo-home"
            cache_source = root_path / CACHE_SOURCE_RELATIVE
            process, graph, counts = _verify_artifacts(
                process_raw,
                graph_raw,
                source_path / "Cargo.lock",
                root_id=guard.root_id,
                volume_uuid=guard.volume_uuid,
            )
            source_head, diff_sha, lock_sha, lock_count, source_status_sha = preflight._source_pins(
                source_path, cargo_home
            )
            if (
                source_head != EXPECTED_AGAVE_HEAD
                or diff_sha != EXPECTED_COMPOSITE_DIFF
                or lock_sha != EXPECTED_POST_V5_LOCK
                or lock_count != 1_075
            ):
                raise AdjudicationError("source_pin_mismatch")
            cache_source_manifest = preflight._locked_cache_manifest(cache_source)
            if cache_source_manifest != V1_CACHE_SOURCE_SHA256:
                raise AdjudicationError("source_cache_pin_mismatch")
            source_cache_rows = preflight._tree_manifest_rows(cache_source)
            copied_cache_rows = preflight._tree_manifest_rows(cargo_home)
            registry_crates = {
                f"{row['name']}-{row['version']}"
                for row in graph["packages"]
                if row["source"] == EXPECTED_REGISTRY_SOURCE
            }
            cache_delta, global_cache = _cache_evidence(
                source_cache_rows,
                copied_cache_rows,
                expected_registry_crates=registry_crates,
            )
            copied_manifest = hashlib.sha256(preflight._canonical(copied_cache_rows)).hexdigest()
            cache_delta_sha = hashlib.sha256(preflight._canonical(cache_delta)).hexdigest()
            target_probe_path, target_probe_sha = preflight._target_probe(
                root_path / TARGET_RELATIVE, device
            )
            if target_probe_path != ".rustc_info.json" or target_probe_sha is None:
                raise AdjudicationError("target_probe_missing")
            if _read_directory_names(root_fd, TMP_RELATIVE, device=device):
                raise AdjudicationError("tmp_directory_not_empty")
            guard.revalidate(min_free_bytes=MIN_FREE_BYTES)
            if (
                _read_relative(root_fd, PROCESS_RELATIVE, device=device, cap=64 * 1024)
                != process_raw
            ):
                raise AdjudicationError("process_receipt_changed")
            if _read_relative(root_fd, GRAPH_RELATIVE, device=device, cap=2 * 1024**2) != graph_raw:
                raise AdjudicationError("graph_artifact_changed")

            receipt = {
                "schema": "brain-senses-agave-metadata-adjudication-v1",
                "schema_version": 1,
                "status": "verified_offline_metadata_graph",
                "storage": {
                    "root_id": guard.root_id,
                    "volume_uuid": guard.volume_uuid,
                    "relative_root": guard.config.data_root,
                },
                "process_capture": {
                    "path": PROCESS_RELATIVE.as_posix(),
                    "sha256": hashlib.sha256(process_raw).hexdigest(),
                    "stdout_sha256": process["stdout_sha256"],
                    "stdout_bytes": process["stdout_bytes"],
                    "stderr_sha256": process["stderr_sha256"],
                    "stderr_bytes": process["stderr_bytes"],
                    "exit_status": process["exit_status"],
                    "classification": process["classification"],
                },
                "graph": {
                    "path": GRAPH_RELATIVE.as_posix(),
                    "sha256": hashlib.sha256(graph_raw).hexdigest(),
                    "schema": graph["schema"],
                    **counts,
                },
                "source": {
                    "relative_path": SOURCE_RELATIVE.as_posix(),
                    "head": source_head,
                    "diff_sha256": diff_sha,
                    "status_sha256": source_status_sha,
                    "cargo_lock_sha256": lock_sha,
                    "lock_tuple_count": lock_count,
                },
                "cache": {
                    "source_relative_path": CACHE_SOURCE_RELATIVE.as_posix(),
                    "source_manifest_sha256": cache_source_manifest,
                    "copied_relative_path": (
                        preflight.METADATA_STAGE_RELATIVE / "cargo-home"
                    ).as_posix(),
                    "copied_after_manifest_sha256": copied_manifest,
                    "extraction_delta_sha256": cache_delta_sha,
                    "extraction_entry_count": len(cache_delta),
                    "global_cache_bookkeeping": global_cache,
                },
                "prior_failure_receipt": {
                    "path": V1_FAILURE_RELATIVE.as_posix(),
                    "sha256": V1_FAILURE_SHA256,
                    "preserved": True,
                },
                "target_probe": {"path": target_probe_path, "sha256": target_probe_sha},
                "raw_stdout_to_graph_replay": False,
                "cargo_invocations_by_adjudicator": 0,
                "limitations": [
                    "The Cargo stdout digest and sanitized graph are independently pinned, but the "
                    "raw stdout bytes were not retained for transformation replay.",
                    "Cargo metadata changed only the copied-home .global-cache bookkeeping file; "
                    "the cache run was not mutation-free.",
                    "This verifies metadata structure only; it does not qualify runtime law or "
                    "production behavior.",
                ],
            }
            guard.revalidate(min_free_bytes=MIN_FREE_BYTES)
            receipt_raw = preflight._canonical(receipt)
            receipt_sha = publish_receipt(
                root_fd,
                receipt_raw,
                device=device,
                revalidate=lambda: guard.revalidate(min_free_bytes=MIN_FREE_BYTES),
            )
            return {"receipt": receipt, "receipt_sha256": receipt_sha}
        finally:
            os.close(root_fd)


def publish_receipt(root_fd: int, receipt_raw: bytes, *, device: int, revalidate: Any) -> str:
    """Publish the single canonical adjudication record without replacing prior bytes."""
    parsed = _canonical_exact(receipt_raw, "adjudication_receipt")
    if parsed.get("schema") != "brain-senses-agave-metadata-adjudication-v1":
        raise AdjudicationError("adjudication_receipt_schema")
    return preflight._write_create_only_beneath_root(
        root_fd,
        ADJUDICATION_RELATIVE,
        receipt_raw,
        device=device,
        revalidate=revalidate,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--storage-config",
        type=Path,
        default=Path.home() / ".config/brain-research/storage-v1.json",
    )
    args = parser.parse_args(argv)
    try:
        result = verify_and_publish(args.storage_config)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        reason = exc.code if isinstance(exc, AdjudicationError) else type(exc).__name__
        if not re.fullmatch(r"[a-zA-Z0-9_]+", reason):
            reason = "adjudication_failed"
        print(json.dumps({"status": "refused", "reason": reason}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
