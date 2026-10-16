#!/usr/bin/env python3
"""Build the v7 Recovery source archive from a pinned Git commit only."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

KIT_VERSION = "v7"
ROSTER_VERSION = "v7-source-roster-v1"
MANIFEST_SCHEMA = "brain-portability-offline-execution-kit-v7-source-manifest-v1"
BUILDER_PATH = "tools/brain_host_offline_kit_v7.py"
MINIMUM_MACOS = "26.0"
RUNTIME_REQUIREMENT = {
    "implementation": "CPython",
    "version": "3.13.6",
    "architecture": "arm64",
    "minimum_macos": MINIMUM_MACOS,
    "delivery": "separately pinned runtime tree; not emitted by this source-only builder",
}

# This is the reviewed source boundary. Entry roles are intentionally separate so
# each operator surface has a visible closure in the emitted manifest.
SOURCE_ROSTER: dict[str, dict[str, str]] = {
    "tools": {"path": "tools/__init__.py", "role": "package_root"},
    "tools.brain_host_recovery": {
        "path": "tools/brain_host_recovery.py",
        "role": "operator_status_entrypoint",
    },
    "tools.brain_host_recovery_preflight_v1": {
        "path": "tools/brain_host_recovery_preflight_v1.py",
        "role": "recovery_preflight_entrypoint",
    },
    "tools.brain_host_held_data_t_cli_v1": {
        "path": "tools/brain_host_held_data_t_cli_v1.py",
        "role": "held_view_entrypoint",
    },
    "tools.brain_host_held_data_t_v1": {
        "path": "tools/brain_host_held_data_t_v1.py",
        "role": "held_view_dependency",
    },
    "tools.brain_host_tree_capture_v5_production": {
        "path": "tools/brain_host_tree_capture_v5_production.py",
        "role": "bound_v2_capacity_capture_entrypoint",
    },
    "tools.brain_host_tree_capture_v5": {
        "path": "tools/brain_host_tree_capture_v5.py",
        "role": "bound_v2_capture_dependency",
    },
    "tools.brain_host_tree_custody": {
        "path": "tools/brain_host_tree_custody.py",
        "role": "capture_dependency",
    },
    "tools.brain_host_tree_custody_v3": {
        "path": "tools/brain_host_tree_custody_v3.py",
        "role": "capture_dependency",
    },
    "tools.brain_senses_external_schema_v1": {
        "path": "tools/brain_senses_external_schema_v1.py",
        "role": "storage_writer_dependency",
    },
    "tools.brain_senses_external_writer_v1": {
        "path": "tools/brain_senses_external_writer_v1.py",
        "role": "storage_writer_dependency",
    },
    "memecoin_research_storage": {
        "path": "src/memecoin_research_storage/__init__.py",
        "role": "storage_binding_dependency",
    },
}
ENTRYPOINTS = {
    "operator_status": "tools.brain_host_recovery",
    "recovery_preflight": "tools.brain_host_recovery_preflight_v1",
    "held_view": "tools.brain_host_held_data_t_cli_v1",
    "bound_v2_capacity_capture": "tools.brain_host_tree_capture_v5_production",
}
# There are no approved dynamic imports or package-resource reads. If one is
# needed later, its exception must bind a module, source location, target, and
# reason; module-wide exemptions are intentionally unsupported.
DYNAMIC_IMPORT_EXEMPTIONS: tuple[dict[str, str], ...] = ()
PACKAGE_RESOURCE_EXEMPTIONS: tuple[dict[str, str], ...] = ()
LOCAL_PREFIXES = ("tools", "memecoin_research_storage")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")


class OfflineKitV7Error(ValueError):
    """Stable fail-closed builder errors."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OfflineKitV7Error("git_command_failed") from exc
    if result.returncode != 0:
        raise OfflineKitV7Error("git_object_or_worktree_check_failed")
    return result.stdout


def _commit_id(repo: Path, commit: str) -> str:
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise OfflineKitV7Error("pinned_commit_invalid")
    resolved = _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    if resolved != commit:
        raise OfflineKitV7Error("pinned_commit_not_canonical")
    root = Path(_git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != repo.resolve():
        raise OfflineKitV7Error("repository_root_not_canonical")
    return resolved


def _tracked_tree(repo: Path, commit: str) -> set[str]:
    raw = _git(repo, "ls-tree", "-r", "--name-only", "-z", commit)
    try:
        return {item.decode("utf-8", "strict") for item in raw.split(b"\0") if item}
    except UnicodeDecodeError as exc:
        raise OfflineKitV7Error("git_tree_path_not_utf8") from exc


def _resolve_module_path(module: str, tree: set[str]) -> str | None:
    roots = [module.replace(".", "/")]
    if module == "memecoin_research_storage" or module.startswith("memecoin_research_storage."):
        roots = ["src/" + roots[0]]
    candidates = [
        path for root in roots for path in (root + "/__init__.py", root + ".py") if path in tree
    ]
    if len(candidates) > 1:
        raise OfflineKitV7Error("local_module_path_ambiguous")
    return candidates[0] if candidates else None


def _local_module(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in LOCAL_PREFIXES)


def _module_for_path(path: str) -> str:
    if path.startswith("tools/"):
        relative = path.removesuffix(".py").replace("/", ".")
        return relative.removesuffix(".__init__")
    if path.startswith("src/memecoin_research_storage/"):
        relative = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
        return relative.removesuffix(".__init__")
    raise OfflineKitV7Error("roster_source_path_outside_local_packages")


def _relative_import(module: str, level: int, imported: str | None) -> str:
    package = module if module.endswith(".__init__") else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    if level > len(parts):
        raise OfflineKitV7Error("relative_import_root_escape")
    prefix = parts[: len(parts) - level + 1]
    if imported:
        prefix.extend(imported.split("."))
    return ".".join(prefix)


def _dotted_expression(node: ast.expr) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _assignment_names(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return set().union(*(_assignment_names(item) for item in node.elts))
    return set()


def _scan_module_imports(module: str, source: bytes, tree: set[str]) -> set[str]:
    try:
        parsed = ast.parse(source.decode("utf-8", "strict"), filename=module)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise OfflineKitV7Error("roster_source_parse_failed") from exc
    found: set[str] = set()
    resource_roots: set[str] = set()
    resource_functions: set[str] = set()
    dynamic_functions: set[str] = set()
    dynamic_attributes = {"import_module", "__import__"}
    resource_attributes = {
        "files",
        "read_binary",
        "read_text",
        "contents",
        "open_binary",
        "open_text",
        "path",
        "get_data",
    }
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    resource_roots.add(f"{alias.asname or 'importlib'}.resources")
                elif alias.name == "importlib.resources":
                    resource_roots.add(alias.asname or "importlib.resources")
                elif alias.name == "pkgutil":
                    resource_roots.add(alias.asname or "pkgutil")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "importlib":
                for alias in node.names:
                    if alias.name == "resources":
                        resource_roots.add(alias.asname or alias.name)
                    elif alias.name == "import_module":
                        dynamic_functions.add(alias.asname or alias.name)
                    elif alias.name == "*":
                        raise OfflineKitV7Error("dynamic_import_without_review_reason")
            elif node.module == "importlib.resources":
                for alias in node.names:
                    if alias.name in {
                        "files",
                        "read_binary",
                        "read_text",
                        "contents",
                        "open_binary",
                        "open_text",
                        "path",
                    }:
                        resource_functions.add(alias.asname or alias.name)
                    elif alias.name == "*":
                        raise OfflineKitV7Error("package_resource_without_review_reason")
            elif node.module == "pkgutil":
                resource_functions.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "get_data"
                )
            elif node.module == "builtins":
                dynamic_functions.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "__import__"
                )

    # Track simple assignment aliases so `load = import_module; load(...)` and
    # equivalent package-resource aliases stay fail-closed. Dynamic import
    # aliases are a conservative global name set; any matching call is rejected.
    assign_nodes = [
        node
        for node in ast.walk(parsed)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
    ]
    changed = True
    while changed:
        changed = False
        for assignment in assign_nodes:
            value = assignment.value
            targets = (
                assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            )
            dotted = _dotted_expression(value)
            is_dynamic = (isinstance(value, ast.Name) and value.id in dynamic_functions) or (
                dotted is not None and dotted.rsplit(".", 1)[-1] in dynamic_attributes
            )
            resource_alias = (isinstance(value, ast.Name) and value.id in resource_functions) or (
                dotted is not None
                and any(dotted == root or dotted.startswith(root + ".") for root in resource_roots)
                and dotted.rsplit(".", 1)[-1] in resource_attributes
            )
            resource_root_alias = (isinstance(value, ast.Name) and value.id in resource_roots) or (
                dotted is not None
                and any(dotted == root or dotted.startswith(root + ".") for root in resource_roots)
                and dotted.endswith(".resources")
            )
            for target in targets:
                for name in _assignment_names(target):
                    if is_dynamic and name not in dynamic_functions:
                        dynamic_functions.add(name)
                        changed = True
                    if resource_alias and name not in resource_functions:
                        resource_functions.add(name)
                        changed = True
                    if resource_root_alias and name not in resource_roots:
                        resource_roots.add(name)
                        changed = True
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _local_module(alias.name):
                    if _resolve_module_path(alias.name, tree) is None:
                        raise OfflineKitV7Error("local_static_import_unresolved")
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = (
                _relative_import(module, node.level, node.module)
                if node.level
                else (node.module or "")
            )
            if not _local_module(base):
                continue
            if _resolve_module_path(base, tree) is None:
                raise OfflineKitV7Error("local_static_import_unresolved")
            found.add(base)
            for alias in node.names:
                if alias.name == "*":
                    raise OfflineKitV7Error("local_star_import_unresolved")
                child = f"{base}.{alias.name}"
                if _resolve_module_path(child, tree) is not None:
                    found.add(child)
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                reflected = node.args[1].value
                if reflected in dynamic_attributes:
                    raise OfflineKitV7Error("dynamic_import_without_review_reason")
                if reflected == "resources" or reflected in resource_attributes:
                    raise OfflineKitV7Error("package_resource_without_review_reason")
            dynamic_name = (
                func.id
                if isinstance(func, ast.Name)
                and (func.id == "__import__" or func.id in dynamic_functions)
                else func.attr
                if isinstance(func, ast.Attribute) and func.attr in dynamic_attributes
                else None
            )
            if dynamic_name is not None:
                raise OfflineKitV7Error("dynamic_import_without_review_reason")
            resource_call = isinstance(func, ast.Name) and func.id in resource_functions
            if isinstance(func, ast.Attribute):
                dotted_name = _dotted_expression(func) or ""
                resource_call = (
                    resource_call
                    or any(
                        dotted_name == root or dotted_name.startswith(root + ".")
                        for root in resource_roots
                    )
                    and dotted_name.rsplit(".", 1)[-1] in resource_attributes
                )
            if resource_call:
                raise OfflineKitV7Error("package_resource_without_review_reason")
    return found


def _validate_roster_closure(
    tree: set[str], blobs: dict[str, bytes]
) -> tuple[list[dict[str, str]], str]:
    module_to_path: dict[str, str] = {}
    for module, row in SOURCE_ROSTER.items():
        path = row["path"]
        if _module_for_path(path) != module or path not in tree or path not in blobs:
            raise OfflineKitV7Error("source_roster_module_missing_or_ambiguous")
        if module in module_to_path:
            raise OfflineKitV7Error("source_roster_duplicate_module")
        module_to_path[module] = path

    closure: set[str] = set()
    for role, module in ENTRYPOINTS.items():
        if module not in module_to_path or SOURCE_ROSTER[module]["role"] != f"{role}_entrypoint":
            raise OfflineKitV7Error("entrypoint_roster_role_mismatch")
        pending = [module]
        while pending:
            current = pending.pop()
            if current in closure:
                continue
            if current not in module_to_path:
                raise OfflineKitV7Error("local_static_import_outside_roster")
            closure.add(current)
            pending.extend(_scan_module_imports(current, blobs[module_to_path[current]], tree))
    extras = set(module_to_path) - closure
    if extras:
        raise OfflineKitV7Error("unjustified_source_roster_extra")
    rows = [
        {
            "module": module,
            "path": row["path"],
            "role": row["role"],
        }
        for module, row in sorted(SOURCE_ROSTER.items())
    ]
    roster_contract = {
        "roster_version": ROSTER_VERSION,
        "entrypoints": ENTRYPOINTS,
        "source_members": rows,
        "dynamic_import_exemptions": DYNAMIC_IMPORT_EXEMPTIONS,
        "package_resource_exemptions": PACKAGE_RESOURCE_EXEMPTIONS,
    }
    roster_sha = hashlib.sha256(_canonical_json(roster_contract)).hexdigest()
    return rows, roster_sha


def _read_pinned_blobs(repo: Path, commit: str) -> tuple[dict[str, bytes], dict[str, str]]:
    tree = _tracked_tree(repo, commit)
    paths = sorted(row["path"] for row in SOURCE_ROSTER.values())
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no", "--", *paths)
    if dirty.strip():
        raise OfflineKitV7Error("rostered_worktree_differs_from_pinned_commit")
    blobs: dict[str, bytes] = {}
    object_ids: dict[str, str] = {}
    for path in paths:
        record = _git(repo, "ls-tree", "-z", commit, "--", path).rstrip(b"\0")
        if not record:
            raise OfflineKitV7Error("source_roster_module_missing_or_ambiguous")
        entries = record.split(b"\0")
        if len(entries) != 1:
            raise OfflineKitV7Error("source_roster_module_missing_or_ambiguous")
        try:
            metadata, found_path = entries[0].split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            found_path_text = found_path.decode("utf-8", "strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise OfflineKitV7Error("source_roster_git_entry_invalid") from exc
        if found_path_text != path or kind != "blob" or mode not in {"100644", "100755"}:
            raise OfflineKitV7Error("source_roster_member_not_regular_blob")
        blobs[path] = _git(repo, "cat-file", "blob", f"{commit}:{path}")
        object_ids[path] = object_id
    _validate_roster_closure(tree, blobs)
    return blobs, object_ids


def _read_pinned_builder(repo: Path, commit: str) -> dict[str, Any]:
    """Prove the running builder bytes are the builder blob in the pinned commit."""
    record = _git(repo, "ls-tree", "-z", commit, "--", BUILDER_PATH).rstrip(b"\0")
    if not record or b"\0" in record:
        raise OfflineKitV7Error("builder_source_missing_or_ambiguous")
    try:
        metadata, found_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        found_path_text = found_path.decode("utf-8", "strict")
    except (ValueError, UnicodeDecodeError) as exc:
        raise OfflineKitV7Error("builder_source_git_entry_invalid") from exc
    if found_path_text != BUILDER_PATH or kind != "blob" or mode not in {"100644", "100755"}:
        raise OfflineKitV7Error("builder_source_not_regular_blob")
    pinned_bytes = _git(repo, "cat-file", "blob", f"{commit}:{BUILDER_PATH}")
    try:
        executed_bytes = Path(__file__).read_bytes()
    except OSError as exc:
        raise OfflineKitV7Error("executed_builder_unreadable") from exc
    if executed_bytes != pinned_bytes:
        raise OfflineKitV7Error("executed_builder_differs_from_pinned_commit")
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no", "--", BUILDER_PATH)
    if dirty.strip():
        raise OfflineKitV7Error("pinned_builder_worktree_differs")
    return {
        "path": BUILDER_PATH,
        "git_blob_oid": object_id,
        "size_bytes": len(pinned_bytes),
        "sha256": hashlib.sha256(pinned_bytes).hexdigest(),
    }


def _zip_source(blobs: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for path, content in sorted(blobs.items()):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o100644 & 0xFFFF) << 16
            info.extra = b""
            info.comment = b""
            archive.writestr(info, content)
    return output.getvalue()


def verify_source_archive(archive_bytes: bytes, manifest: dict[str, Any]) -> None:
    if hashlib.sha256(archive_bytes).hexdigest() != manifest.get("source_archive_sha256"):
        raise OfflineKitV7Error("source_archive_digest_mismatch")
    expected = manifest.get("source_members")
    if not isinstance(expected, list):
        raise OfflineKitV7Error("source_manifest_roster_invalid")
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
            names = archive.namelist()
            expected_names = [row["path"] for row in expected]
            if names != expected_names or len(names) != len(set(names)):
                raise OfflineKitV7Error("source_archive_member_set_mismatch")
            for row in expected:
                raw = archive.read(row["path"])
                if (
                    len(raw) != row["size_bytes"]
                    or hashlib.sha256(raw).hexdigest() != row["sha256"]
                ):
                    raise OfflineKitV7Error("source_archive_member_digest_mismatch")
    except (OSError, zipfile.BadZipFile, KeyError, TypeError) as exc:
        raise OfflineKitV7Error("source_archive_invalid") from exc


def build_source_kit(repo: Path, commit: str) -> tuple[bytes, dict[str, Any], bytes]:
    repo = repo.resolve(strict=True)
    pinned_commit = _commit_id(repo, commit)
    builder_identity = _read_pinned_builder(repo, pinned_commit)
    blobs, object_ids = _read_pinned_blobs(repo, pinned_commit)
    roster_rows, roster_sha = _validate_roster_closure(_tracked_tree(repo, pinned_commit), blobs)
    archive_bytes = _zip_source(blobs)
    member_rows = []
    for row in sorted(roster_rows, key=lambda item: item["path"]):
        source = blobs[row["path"]]
        member_rows.append(
            {
                **row,
                "git_blob_oid": object_ids[row["path"]],
                "size_bytes": len(source),
                "sha256": hashlib.sha256(source).hexdigest(),
            }
        )
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "kit_version": KIT_VERSION,
        "source_roster_version": ROSTER_VERSION,
        "source_commit": pinned_commit,
        "source_roster_sha256": roster_sha,
        "builder": builder_identity,
        "source_archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "minimum_macos": MINIMUM_MACOS,
        "runtime_requirement": RUNTIME_REQUIREMENT,
        "entrypoints": ENTRYPOINTS,
        "dynamic_import_exemptions": DYNAMIC_IMPORT_EXEMPTIONS,
        "package_resource_exemptions": PACKAGE_RESOURCE_EXEMPTIONS,
        "source_members": member_rows,
    }
    verify_source_archive(archive_bytes, manifest)
    return archive_bytes, manifest, _canonical_json(manifest) + b"\n"


def _write_create_only(path: Path, content: bytes) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise OfflineKitV7Error("output_create_only_open_failed") from exc
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OfflineKitV7Error("output_write_failed")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-source", help="build deterministic v7 source ZIP and manifest")
    build.add_argument("--repo", type=Path, required=True)
    build.add_argument("--commit", required=True)
    build.add_argument("--source-zip", type=Path, required=True)
    build.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.source_zip.resolve(strict=False) == args.manifest.resolve(strict=False):
            raise OfflineKitV7Error("output_paths_must_be_distinct")
        archive, _manifest, manifest_bytes = build_source_kit(args.repo, args.commit)
        _write_create_only(args.source_zip, archive)
        _write_create_only(args.manifest, manifest_bytes)
    except OfflineKitV7Error as exc:
        sys.stdout.buffer.write(_canonical_json({"state": "blocked", "error": str(exc)}) + b"\n")
        return 2
    sys.stdout.buffer.write(
        _canonical_json(
            {
                "state": "source_archive_built",
                "kit_version": KIT_VERSION,
                "source_sha256": hashlib.sha256(archive).hexdigest(),
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "member_count": len(SOURCE_ROSTER),
                "minimum_macos": MINIMUM_MACOS,
                "runtime_bundled": False,
            }
        )
        + b"\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
