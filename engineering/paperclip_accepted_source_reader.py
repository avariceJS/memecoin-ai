"""Read-only bounded access to a sanitized, Git-published accepted source projection.

The caller resolves publication and canonical acceptance evidence. This module reads only the
resulting immutable projection commit, and only its complete owner-approved tree manifest.
It never opens the canonical repository or a worker-controlled filesystem path.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_CURSOR_BYTES = 4096
MAX_PATH_BYTES = 1024
MAX_QUERY_BYTES = 512
MAX_FILE_BYTES = 2_000_000
MAX_PAGE_SIZE = 100
MAX_LINE_COUNT = 200
MAX_RESULT_BYTES = 48_000
MAX_EXCERPT_BYTES = 2_000
MAX_SEARCH_SCAN_BYTES = 8_000_000


class AcceptedSourceReadError(ValueError):
    """Invalid or stale immutable accepted-source read request."""


@dataclass(frozen=True)
class SourceFile:
    path: str
    mode: str
    blob_sha: str
    size_bytes: int


@dataclass(frozen=True)
class AcceptedSourceSnapshot:
    projection_sha: str
    canonical_sha: str
    acceptance_receipt_digest: str
    manifest_digest: str
    files: tuple[SourceFile, ...]


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _safe_path(value: Any) -> str:
    if type(value) is not str or not value:
        raise AcceptedSourceReadError("source_reader_manifest_invalid")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise AcceptedSourceReadError("source_reader_manifest_invalid") from exc
    if (
        len(encoded) > MAX_PATH_BYTES
        or "\0" in value
        or "\\" in value
        or value.startswith("/")
        or str(PurePosixPath(value)) != value
        or any(part in {"", ".", ".."} or part.startswith(".") for part in value.split("/"))
    ):
        raise AcceptedSourceReadError("source_reader_manifest_invalid")
    return value


def _within_roots(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == root or (root.endswith("/") and path.startswith(root)) for root in roots)


def _git(repo: str, *args: str, input_bytes: bytes | None = None, max_output: int = 8_000_000) -> bytes:
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C",
        "LC_ALL": "C",
        "HOME": "/var/empty",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_PAGER": "cat",
    }
    try:
        result = subprocess.run(
            ["/usr/bin/git", "-c", "protocol.allow=never", "-C", repo, *args],
            input=input_bytes,
            capture_output=True,
            env=env,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AcceptedSourceReadError("source_reader_projection_unavailable") from exc
    if result.returncode != 0 or len(result.stdout) > max_output:
        raise AcceptedSourceReadError("source_reader_projection_unavailable")
    return result.stdout


def build_snapshot(
    repo: str,
    projection_sha: str,
    canonical_sha: str,
    acceptance_receipt_digest: str,
    allowed_roots: tuple[str, ...] | list[str],
    *,
    max_manifest_bytes: int = 8_000_000,
) -> AcceptedSourceSnapshot:
    """Enumerate and hash the complete safe projection tree at one immutable commit."""
    if (
        type(repo) is not str
        or not repo.startswith("/")
        or type(projection_sha) is not str
        or not OID_RE.fullmatch(projection_sha)
        or type(canonical_sha) is not str
        or not OID_RE.fullmatch(canonical_sha)
        or type(acceptance_receipt_digest) is not str
        or not HASH_RE.fullmatch(acceptance_receipt_digest)
        or type(allowed_roots) not in (tuple, list)
        or not allowed_roots
        or len(allowed_roots) > 16
    ):
        raise AcceptedSourceReadError("source_reader_binding_invalid")
    roots = tuple(allowed_roots)
    for root in roots:
        if (
            type(root) is not str
            or not root
            or "\0" in root
            or "\\" in root
            or root.startswith("/")
            or str(PurePosixPath(root.rstrip("/"))) != root.rstrip("/")
            or any(part in {"", ".", ".."} for part in root.rstrip("/").split("/"))
            or (not root.endswith("/") and "/" in root)
        ):
            raise AcceptedSourceReadError("source_reader_binding_invalid")
    if len(set(roots)) != len(roots):
        raise AcceptedSourceReadError("source_reader_binding_invalid")
    commit = _git(repo, "rev-parse", "--verify", f"{projection_sha}^{{commit}}", max_output=256)
    if commit.decode("ascii", errors="strict").strip() != projection_sha:
        raise AcceptedSourceReadError("source_reader_projection_stale")
    raw = _git(
        repo,
        "ls-tree",
        "-r",
        "-z",
        "--long",
        "--full-tree",
        projection_sha,
        max_output=max_manifest_bytes,
    )
    files: list[SourceFile] = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        metadata, tab, raw_path = entry.partition(b"\t")
        if not tab:
            raise AcceptedSourceReadError("source_reader_manifest_invalid")
        try:
            mode, kind, blob_sha, size_text = metadata.decode("ascii", errors="strict").split()
            path = _safe_path(raw_path.decode("utf-8", errors="strict"))
            size = int(size_text)
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise AcceptedSourceReadError("source_reader_manifest_invalid") from exc
        if mode not in {"100644", "100755"} or kind != "blob" or not OID_RE.fullmatch(blob_sha):
            raise AcceptedSourceReadError("source_reader_manifest_invalid")
        if not _within_roots(path, roots):
            raise AcceptedSourceReadError("source_reader_projection_outside_manifest")
        if size < 0 or size > MAX_FILE_BYTES:
            raise AcceptedSourceReadError("source_reader_file_exceeds_limit")
        files.append(SourceFile(path, mode, blob_sha, size))
    if not files or len(files) > 50_000 or len({item.path for item in files}) != len(files):
        raise AcceptedSourceReadError("source_reader_manifest_invalid")
    files.sort(key=lambda item: item.path)
    manifest_value = {
        "projection_sha": projection_sha,
        "canonical_sha": canonical_sha,
        "acceptance_receipt_digest": acceptance_receipt_digest,
        "files": [
            {"path": item.path, "mode": item.mode, "blob_sha": item.blob_sha, "size_bytes": item.size_bytes}
            for item in files
        ],
    }
    manifest_digest = hashlib.sha256(_canonical(manifest_value)).hexdigest()
    return AcceptedSourceSnapshot(
        projection_sha, canonical_sha, acceptance_receipt_digest, manifest_digest, tuple(files)
    )


def _cursor_encode(
    snapshot: AcceptedSourceSnapshot,
    owner_config_digest: str,
    operation: str,
    selector: str,
    position: int | tuple[int, int],
) -> str:
    payload = {
        "schema": "paperclip-accepted-source-cursor-v1",
        "owner_config_digest": owner_config_digest,
        "projection_sha": snapshot.projection_sha,
        "canonical_sha": snapshot.canonical_sha,
        "acceptance_receipt_digest": snapshot.acceptance_receipt_digest,
        "manifest_digest": snapshot.manifest_digest,
        "operation": operation,
        "selector": selector,
        "position": position,
    }
    body = _canonical(payload)
    envelope = {"payload": payload, "digest": hashlib.sha256(body).hexdigest()}
    token = base64.urlsafe_b64encode(_canonical(envelope)).rstrip(b"=").decode("ascii")
    if len(token) > MAX_CURSOR_BYTES:
        raise AcceptedSourceReadError("source_reader_cursor_invalid")
    return token


def _cursor_position(
    cursor: Any,
    snapshot: AcceptedSourceSnapshot,
    owner_config_digest: str,
    operation: str,
    selector: str,
) -> int | tuple[int, int]:
    if cursor is None:
        return (0, 0) if operation == "search" else 0
    if type(cursor) is not str or not cursor or len(cursor) > MAX_CURSOR_BYTES:
        raise AcceptedSourceReadError("source_reader_cursor_invalid")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        envelope = json.loads(raw)
        payload = envelope["payload"]
        digest = envelope["digest"]
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise AcceptedSourceReadError("source_reader_cursor_invalid") from None
    if (
        type(envelope) is not dict
        or set(envelope) != {"payload", "digest"}
        or type(payload) is not dict
        or type(digest) is not str
        or not HASH_RE.fullmatch(digest)
        or hashlib.sha256(_canonical(payload)).hexdigest() != digest
        or payload.get("schema") != "paperclip-accepted-source-cursor-v1"
        or payload.get("owner_config_digest") != owner_config_digest
        or payload.get("projection_sha") != snapshot.projection_sha
        or payload.get("canonical_sha") != snapshot.canonical_sha
        or payload.get("acceptance_receipt_digest") != snapshot.acceptance_receipt_digest
        or payload.get("manifest_digest") != snapshot.manifest_digest
        or payload.get("operation") != operation
        or payload.get("selector") != selector
        or not (
            (type(payload.get("position")) is int and payload["position"] >= 0)
            or (
                type(payload.get("position")) is list
                and len(payload["position"]) == 2
                and all(type(value) is int and value >= 0 for value in payload["position"])
            )
        )
    ):
        if isinstance(payload, dict) and (
            payload.get("projection_sha") != snapshot.projection_sha
            or payload.get("canonical_sha") != snapshot.canonical_sha
            or payload.get("acceptance_receipt_digest") != snapshot.acceptance_receipt_digest
            or payload.get("manifest_digest") != snapshot.manifest_digest
        ):
            raise AcceptedSourceReadError("source_reader_cursor_stale_restart_required")
        raise AcceptedSourceReadError("source_reader_cursor_invalid")
    value = payload["position"]
    return tuple(value) if type(value) is list else value


def list_files(
    repo: str,
    snapshot: AcceptedSourceSnapshot,
    owner_config_digest: str,
    *,
    prefix: str = "",
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    if (
        type(owner_config_digest) is not str
        or not HASH_RE.fullmatch(owner_config_digest)
        or type(prefix) is not str
        or (prefix and _safe_path(prefix.rstrip("/")) != prefix.rstrip("/"))
        or type(limit) is not int
        or not 1 <= limit <= MAX_PAGE_SIZE
    ):
        raise AcceptedSourceReadError("source_reader_request_invalid")
    selector = prefix
    position = _cursor_position(cursor, snapshot, owner_config_digest, "list", selector)
    if type(position) is not int:
        raise AcceptedSourceReadError("source_reader_cursor_invalid")
    selected = [item for item in snapshot.files if item.path.startswith(prefix)]
    if position > len(selected):
        raise AcceptedSourceReadError("source_reader_cursor_invalid")
    page = selected[position : position + limit]
    next_position = position + len(page)
    truncated = next_position < len(selected)
    next_cursor = (
        _cursor_encode(snapshot, owner_config_digest, "list", selector, next_position)
        if truncated
        else None
    )
    return {
        "projection_sha": snapshot.projection_sha,
        "canonical_sha": snapshot.canonical_sha,
        "acceptance_receipt_digest": snapshot.acceptance_receipt_digest,
        "manifest_digest": snapshot.manifest_digest,
        "files": [
            {"path": item.path, "mode": item.mode, "blob_sha": item.blob_sha, "size_bytes": item.size_bytes}
            for item in page
        ],
        "total_matching": len(selected),
        "truncated": truncated,
        "next_cursor": next_cursor,
    }


def _read_blob(repo: str, snapshot: AcceptedSourceSnapshot, path: str) -> tuple[SourceFile, bytes]:
    clean_path = _safe_path(path)
    item = next((entry for entry in snapshot.files if entry.path == clean_path), None)
    if item is None:
        raise AcceptedSourceReadError("source_reader_path_not_in_manifest")
    size = int(_git(repo, "cat-file", "-s", item.blob_sha, max_output=64).decode("ascii").strip())
    if size != item.size_bytes or size > MAX_FILE_BYTES:
        raise AcceptedSourceReadError("source_reader_projection_changed")
    content = _git(repo, "cat-file", "blob", item.blob_sha, max_output=MAX_FILE_BYTES)
    if len(content) != size:
        raise AcceptedSourceReadError("source_reader_projection_changed")
    try:
        content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AcceptedSourceReadError("source_reader_non_text_file") from exc
    return item, content


def read_lines(
    repo: str,
    snapshot: AcceptedSourceSnapshot,
    path: str,
    *,
    start_line: int = 1,
    line_count: int = 80,
) -> dict[str, Any]:
    if (
        type(start_line) is not int
        or start_line < 1
        or type(line_count) is not int
        or not 1 <= line_count <= MAX_LINE_COUNT
    ):
        raise AcceptedSourceReadError("source_reader_request_invalid")
    item, content = _read_blob(repo, snapshot, path)
    lines = content.decode("utf-8", errors="strict").splitlines()
    content_sha256 = hashlib.sha256(content).hexdigest()
    selected = lines[start_line - 1 : start_line - 1 + line_count]
    result_lines = []
    used = 0
    for offset, line in enumerate(selected):
        encoded = line.encode("utf-8")
        line_truncated = len(encoded) > MAX_EXCERPT_BYTES
        if line_truncated:
            encoded = encoded[:MAX_EXCERPT_BYTES]
            while encoded:
                try:
                    clipped = encoded.decode("utf-8", errors="strict")
                    break
                except UnicodeDecodeError:
                    encoded = encoded[:-1]
            else:
                clipped = ""
        else:
            clipped = line
        size = len(clipped.encode("utf-8"))
        if used + size > MAX_RESULT_BYTES:
            break
        result_lines.append({"line": start_line + offset, "text": clipped, "line_truncated": line_truncated})
        used += size
    returned_end = (result_lines[-1]["line"] if result_lines else start_line - 1)
    next_line = returned_end + 1 if returned_end < len(lines) else None
    return {
        "projection_sha": snapshot.projection_sha,
        "canonical_sha": snapshot.canonical_sha,
        "acceptance_receipt_digest": snapshot.acceptance_receipt_digest,
        "manifest_digest": snapshot.manifest_digest,
        "path": item.path,
        "blob_sha": item.blob_sha,
        "content_sha256": content_sha256,
        "size_bytes": item.size_bytes,
        "line_count": len(lines),
        "lines": result_lines,
        "truncated": next_line is not None,
        "next_line": next_line,
    }


def search_text(
    repo: str,
    snapshot: AcceptedSourceSnapshot,
    owner_config_digest: str,
    query: str,
    *,
    prefix: str = "",
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    if (
        type(owner_config_digest) is not str
        or not HASH_RE.fullmatch(owner_config_digest)
        or type(query) is not str
        or not query
        or len(query.encode("utf-8", errors="strict")) > MAX_QUERY_BYTES
        or "\0" in query
        or type(prefix) is not str
        or (prefix and _safe_path(prefix.rstrip("/")) != prefix.rstrip("/"))
        or type(limit) is not int
        or not 1 <= limit <= MAX_PAGE_SIZE
    ):
        raise AcceptedSourceReadError("source_reader_request_invalid")
    selector = hashlib.sha256(_canonical({"query": query, "prefix": prefix})).hexdigest()
    position = _cursor_position(cursor, snapshot, owner_config_digest, "search", selector)
    files = [item for item in snapshot.files if item.path.startswith(prefix)]
    if type(position) is not tuple or len(position) != 2 or position[0] > len(files):
        raise AcceptedSourceReadError("source_reader_cursor_invalid")
    matches: list[dict[str, Any]] = []
    used = 0
    scan_bytes = 0
    next_position: tuple[int, int] | None = None
    start_file, start_line_index = position
    for index in range(start_file, len(files)):
        if scan_bytes + files[index].size_bytes > MAX_SEARCH_SCAN_BYTES:
            next_position = (index, 0)
            break
        item, content = _read_blob(repo, snapshot, files[index].path)
        scan_bytes += len(content)
        content_sha256 = hashlib.sha256(content).hexdigest()
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise AcceptedSourceReadError("source_reader_non_text_file") from exc
        lines = text.splitlines()
        first_line_index = start_line_index if index == start_file else 0
        for line_index in range(first_line_index, len(lines)):
            line = lines[line_index]
            line_number = line_index + 1
            if query not in line:
                continue
            encoded = line.encode("utf-8")
            line_truncated = len(encoded) > MAX_EXCERPT_BYTES
            if line_truncated:
                encoded = encoded[:MAX_EXCERPT_BYTES]
                while encoded:
                    try:
                        excerpt = encoded.decode("utf-8", errors="strict")
                        break
                    except UnicodeDecodeError:
                        encoded = encoded[:-1]
                else:
                    excerpt = ""
            else:
                excerpt = line
            row = {
                "path": item.path,
                "blob_sha": item.blob_sha,
                "content_sha256": content_sha256,
                "line": line_number,
                "text": excerpt,
                "line_truncated": line_truncated,
            }
            row_size = len(_canonical(row))
            if len(matches) >= limit or used + row_size > MAX_RESULT_BYTES:
                next_position = (index, line_index)
                break
            matches.append(row)
            used += row_size
        if next_position is not None:
            break
        next_position = (index + 1, 0)
        start_line_index = 0
    if next_position == (len(files), 0):
        next_position = None
    next_cursor = (
        _cursor_encode(snapshot, owner_config_digest, "search", selector, next_position)
        if next_position is not None
        else None
    )
    return {
        "projection_sha": snapshot.projection_sha,
        "canonical_sha": snapshot.canonical_sha,
        "acceptance_receipt_digest": snapshot.acceptance_receipt_digest,
        "manifest_digest": snapshot.manifest_digest,
        "matches": matches,
        "truncated": next_cursor is not None,
        "next_cursor": next_cursor,
    }
