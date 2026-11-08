#!/usr/bin/env python3
"""Configure and inspect an explicit, UUID-bound research storage root."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from memecoin_research_storage import (
    ResolvedRoot,
    StorageError,
    bind_existing,
    configure,
    create_root,
    resolve,
    verify_root,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    configure_p = sub.add_parser("configure", help="write a host-only volume binding")
    configure_p.add_argument("--config", type=Path, required=True)
    configure_p.add_argument("--mount", type=Path, required=True)
    configure_p.add_argument(
        "--data-root", required=True, help="relative directory beneath the mounted volume"
    )
    configure_p.add_argument("--min-free-bytes", type=int, required=True)
    configure_p.add_argument("--volume-uuid", help="optional explicit expected UUID")
    configure_p.add_argument("--platform", choices=("macos", "linux"), default="macos")
    bind_p = sub.add_parser(
        "bind-existing", help="bind a verified existing root using recovery identity pins"
    )
    bind_p.add_argument("--config", type=Path, required=True)
    bind_p.add_argument("--mount", type=Path, required=True)
    bind_p.add_argument("--data-root", required=True)
    bind_p.add_argument("--expected-volume-uuid", required=True)
    bind_p.add_argument("--expected-root-id", required=True)
    bind_p.add_argument("--min-free-bytes", type=int, required=True)
    bind_p.add_argument("--platform", choices=("macos", "linux"), default="macos")
    check_p = sub.add_parser(
        "check", help="check the mounted volume; use --require-root to verify the root marker"
    )
    check_p.add_argument("--config", type=Path, required=True)
    check_p.add_argument("--min-free-bytes", type=int, default=0)
    check_p.add_argument("--require-root", action="store_true")
    root_p = sub.add_parser("create-root", help="guardedly create the new bound root")
    root_p.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "configure":
            config = configure(
                args.config,
                args.mount,
                args.data_root,
                min_free_bytes=args.min_free_bytes,
                volume_uuid=args.volume_uuid,
                platform=args.platform,
            )
            payload = config.to_json()
        elif args.command == "bind-existing":
            config = bind_existing(
                args.config,
                args.mount,
                args.data_root,
                expected_volume_uuid=args.expected_volume_uuid,
                expected_root_id=args.expected_root_id,
                min_free_bytes=args.min_free_bytes,
                platform=args.platform,
            )
            payload = config.to_json()
        elif args.command == "create-root":
            resolved = create_root(args.config)
            payload = _resolved_json(resolved)
        else:
            root_verified = args.require_root
            resolved = (
                verify_root(args.config, min_free_bytes=args.min_free_bytes)
                if root_verified
                else resolve(args.config, min_free_bytes=args.min_free_bytes)
            )
            payload = _resolved_json(resolved)
            payload["root_verified"] = root_verified
            payload["status"] = "root_verified" if root_verified else "volume_preconditions_passed"
        print(json.dumps(payload, sort_keys=True))
        return 0
    except StorageError as exc:
        print(f"storage check failed: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"storage operation failed: {exc}", file=sys.stderr)
        return 2


def _resolved_json(result: ResolvedRoot) -> dict[str, object]:
    return {
        "root_id": result.root_id,
        "volume_uuid": result.volume_uuid,
        "mount_path": str(result.mount_path),
        "path": str(result.path),
        "available_bytes": result.available_bytes,
    }


if __name__ == "__main__":
    raise SystemExit(main())
