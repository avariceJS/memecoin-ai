"""Process entry for the operator service and its one-shot job worker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import sys
import threading
from pathlib import Path
from typing import Any

from memecoin_operator.control import (
    artifact_dir,
    assess_result,
    atomic_write,
    initialize,
    lease_path,
    load_adapter,
    private_log,
    wall_ns,
)


def execute_job(root: Path, job_id: str) -> int:
    """Run one adapter action. Exit without a marker when the parent lease dies."""
    root = Path(root)
    art = artifact_dir(root, job_id)
    request_path = art / "operator-request.json"
    if not request_path.exists():
        private_log(root, f"missing request for {job_id}")
        return 4
    request = json.loads(request_path.read_text(encoding="utf-8"))
    action = str(request["action"])
    params = request["params"] if isinstance(request.get("params"), dict) else {}
    stop = threading.Event()

    def watch() -> None:
        parent_raw = os.environ.get("OPERATOR_PARENT_FD")
        parent_fd = int(parent_raw) if parent_raw and parent_raw.isdigit() else None
        while not stop.wait(0.2):
            if parent_fd is not None:
                try:
                    readable, _, _ = select.select([parent_fd], [], [], 0)
                except (OSError, ValueError):
                    os._exit(3)
                if readable:
                    try:
                        chunk = os.read(parent_fd, 1)
                    except OSError:
                        os._exit(3)
                    if chunk == b"":
                        os._exit(3)
            path = lease_path(root, job_id)
            try:
                lease = json.loads(path.read_text(encoding="utf-8"))
                expires = int(lease["expires_ns"])
            except (OSError, ValueError, TypeError, KeyError):
                os._exit(3)
            if wall_ns() >= expires or (art / "operator-stop-reason").exists():
                os._exit(3)

    threading.Thread(target=watch, name="operator-lease", daemon=True).start()
    try:
        result = load_adapter().run(root, action, params, art)
    except Exception as exc:
        private_log(root, "worker_failed", exc)
        result = {"ok": False, "reason": "adapter_error"}
    finally:
        stop.set()
    if (art / "operator-stop-reason").exists():
        return 3
    ok, _reason, _extracted = assess_result(action, params, result)
    try:
        raw = json.dumps(result, sort_keys=True, allow_nan=False).encode()
    except (TypeError, ValueError):
        raw = b'{"ok":false,"reason":"invalid_result"}'
        ok = False
    atomic_write(art / "operator-result.json", raw)
    if not ok:
        return 2
    digest = hashlib.sha256(raw).hexdigest()
    atomic_write(art / "operator-published.marker", f"v1\n{job_id}\n{digest}\n".encode())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memecoin_operator.runtime")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--job", default="")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.root
    if args.worker:
        if not args.job:
            return 2
        return execute_job(root, args.job)
    config: dict[str, Any] | None = None
    if args.config is not None:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            return 2
    started = initialize(root, config)
    if not isinstance(started, dict) or not started.get("ok"):
        return 3
    if args.init:
        return 0
    if not 1 <= args.port <= 65535:
        return 2
    from memecoin_operator.web import serve

    serve(root, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
