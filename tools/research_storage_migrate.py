#!/usr/bin/env python3
"""Stage and verify one closed legacy research root on the bound external volume."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path

from memecoin_research_storage import StorageError, writer_guard
from memecoin_research_storage.migration import (
    MigrationError,
    _is_protected_name,
    _open_dir_at,
    _plain_absolute,
    _validate_source,
    _write_create_only_at,
    load_preflight_receipt,
    publish_locator,
    stage_migration,
    verify_migration,
)


def _under_root(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or "\\" in relative or "\x00" in relative or ".." in candidate.parts:
        raise MigrationError("unsafe_relative_path")
    root = Path(os.path.abspath(root))
    target = root / candidate
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise MigrationError("path_outside_verified_root") from exc
    cursor = root
    for part in candidate.parts:
        cursor /= part
        try:
            if stat.S_ISLNK(cursor.lstat().st_mode):
                raise MigrationError("symlink_under_verified_root")
        except FileNotFoundError:
            pass
    return target


def _read_preflight(path: Path, source_root: Path, group: str) -> dict[str, object]:
    try:
        value, _raw = load_preflight_receipt(
            path,
            expected_source_root=source_root,
            group=group,
        )
    except (OSError, ValueError, UnicodeDecodeError, MigrationError) as exc:
        raise MigrationError(f"preflight_receipt_unreadable: {exc}") from exc
    return value


def _canonical_work_source(source: Path, expected: Path, group: str) -> Path:
    repo = Path(__file__).resolve().parents[1]
    work = repo / "work"
    canonical = _plain_absolute(source, "source")
    expected_canonical = _plain_absolute(expected, "expected_source")
    try:
        relative = canonical.relative_to(work)
    except ValueError as exc:
        raise MigrationError("source_must_be_under_repository_work_root") from exc
    if canonical != expected_canonical or not relative.parts:
        raise MigrationError("source_must_be_canonical_repository_work_root")
    if any(_is_protected_name(part) for part in relative.parts):
        raise MigrationError("protected_source_path")
    return _validate_source(canonical, expected_canonical, group)


def _mkdir_private_under_root(root: Path, relative: str) -> Path:
    target = _under_root(root, relative)
    cursor = root
    for part in Path(relative).parts:
        cursor /= part
        try:
            os.mkdir(cursor, 0o700)
        except FileExistsError:
            pass
        st = cursor.lstat()
        if not stat.S_ISDIR(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o700:
            raise MigrationError("unsafe_destination_parent_mode_or_type")
    return target


class _MigrationGuardCheck:
    """Run cheap identity checks often and full resolution within five seconds."""

    def __init__(self, guard: object) -> None:
        self.guard = guard
        self.last_full_check = float("-inf")

    def __call__(self) -> None:
        self.guard.revalidate_identity()
        now = time.monotonic()
        if now - self.last_full_check >= 5.0:
            self.guard.revalidate()
            self.last_full_check = time.monotonic()

    def before_publish(self) -> None:
        self.guard.revalidate_identity()
        self.guard.revalidate()
        self.last_full_check = time.monotonic()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage", help="copy and create a manifest for one closed source root")
    stage.add_argument(
        "--config", type=Path, required=True, help="host-only external storage binding"
    )
    stage.add_argument("--source-root", type=Path, required=True)
    stage.add_argument("--expected-source-root", type=Path, required=True)
    stage.add_argument(
        "--group", required=True, help="must equal the exact source directory basename"
    )
    stage.add_argument(
        "--preflight-receipt",
        type=Path,
        required=True,
        help="parent-reviewed closed-root attestation",
    )
    stage.add_argument(
        "--destination-relative",
        default="archive/work",
        help="directory relative to verified research root",
    )

    verify = sub.add_parser("verify", help="read-only source, manifest, and staged-tree replay")
    verify.add_argument("--config", type=Path, required=True)
    verify.add_argument("--source-root", type=Path, required=True)
    verify.add_argument("--expected-source-root", type=Path, required=True)
    verify.add_argument(
        "--destination-relative", required=True, help="staged directory relative to research root"
    )
    verify.add_argument(
        "--verification-output", type=Path, help="create-only JSON acceptance receipt"
    )

    locator = sub.add_parser(
        "publish-locator", help="write separate create-only old/new locator after replay"
    )
    locator.add_argument("--config", type=Path, required=True)
    locator.add_argument("--old-root", type=Path, required=True)
    locator.add_argument("--new-relative", required=True)
    locator.add_argument("--group", required=True)
    locator.add_argument("--verification", type=Path, required=True)
    locator.add_argument("--verification-sha256", required=True)
    locator.add_argument("--locator-relative", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "stage":
            source = _canonical_work_source(args.source_root, args.expected_source_root, args.group)
            _value, receipt_raw = load_preflight_receipt(
                args.preflight_receipt,
                expected_source_root=source,
                group=args.group,
            )
            receipt_hash = hashlib.sha256(receipt_raw).hexdigest()
            with writer_guard(args.config) as guard:
                checks = _MigrationGuardCheck(guard)
                checks.before_publish()
                source_parent = source.relative_to(
                    Path(__file__).resolve().parents[1] / "work"
                ).parent
                destination_parent_relative = Path(args.destination_relative) / source_parent
                destination_parent = guard.create_directory(str(destination_parent_relative))

                def recheck_preflight(_root: Path) -> None:
                    _value, current_raw = load_preflight_receipt(
                        args.preflight_receipt,
                        expected_source_root=source,
                        group=args.group,
                    )
                    current = hashlib.sha256(current_raw).hexdigest()
                    if current != receipt_hash:
                        raise MigrationError("preflight_receipt_changed")

                result = stage_migration(
                    source,
                    guard.path,
                    destination_parent / args.group,
                    group=args.group,
                    expected_source_root=source,
                    closed_preflight=recheck_preflight,
                    preflight_receipt_path=args.preflight_receipt,
                    preflight_receipt_sha256=receipt_hash,
                    before_write=checks,
                    before_publish=checks.before_publish,
                    expected_destination_device=guard.root.mount_device,
                )
        elif args.command == "verify":
            source = _canonical_work_source(
                args.source_root, args.expected_source_root, Path(args.source_root).name
            )
            with writer_guard(args.config) as guard:
                checks = _MigrationGuardCheck(guard)
                checks.before_publish()
                destination = _under_root(guard.path, args.destination_relative)
                result = verify_migration(
                    source,
                    destination,
                    destination.with_name(destination.name + ".manifest.json"),
                    expected_source_root=source,
                    expected_destination_root=destination,
                    before_read=checks,
                    expected_destination_device=guard.root.mount_device,
                )
                if args.verification_output:
                    output_path = Path(args.verification_output)
                    try:
                        relative_output = output_path.relative_to(guard.path)
                    except ValueError as exc:
                        raise MigrationError("verification_output_outside_external_root") from exc
                    if not relative_output.parts:
                        raise MigrationError("verification_output_must_be_file")
                    external_fd = os.open(
                        guard.path,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    )
                    try:
                        parent_fd = _open_dir_at(
                            external_fd,
                            "/".join(relative_output.parts[:-1]),
                            create=False,
                            before_write=guard.revalidate,
                            expected_device=guard.root.mount_device,
                        )
                    finally:
                        os.close(external_fd)
                    try:
                        checks.before_publish()
                        _write_create_only_at(
                            parent_fd,
                            relative_output.name,
                            (
                                json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
                            ).encode(),
                            0o444,
                        )
                        checks.before_publish()
                    finally:
                        os.close(parent_fd)
        else:
            with writer_guard(args.config) as guard:
                checks = _MigrationGuardCheck(guard)
                checks.before_publish()
                result = publish_locator(
                    guard.path,
                    _under_root(guard.path, args.locator_relative),
                    group=args.group,
                    old_root=args.old_root,
                    new_root=_under_root(guard.path, args.new_relative),
                    verification_receipt_path=args.verification,
                    verification_receipt_sha256=args.verification_sha256,
                    before_write=checks,
                    before_publish=checks.before_publish,
                    expected_destination_device=guard.root.mount_device,
                )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (StorageError, MigrationError, OSError) as exc:
        print(f"research storage migration failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
