"""Build a disposable, data-only operator snapshot and serve it read-only on loopback."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path.home() / "Developer/memecoin-runtime/brain/owner-work/brain-b13/operator04"
DEFAULT_SNAPSHOTS = REPO / "work/dev-local/operator-preview/snapshots"
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
PROTECTED_PARTS = {"d43", "f01", "holdout", "reserved", "protected"}
ROOT_CONFIG_KEYS = {
    "product",
    "mode",
    "provider",
    "profile",
    "init_ns",
    "expires_ns",
    "paid_budget_usd",
    "additional_requests",
    "max_background_requests",
    "max_training_runs",
    "min_refit_interval_s",
    "minimum_new_matured",
    "run_envelope_s",
    "real_execution_enabled",
    "champion_model_sha",
    "keep_awake_on_ac",
    "created_ns",
}
SOURCE_CONFIG_KEYS = {
    "product",
    "mode",
    "provider",
    "profile",
    "init_ns",
    "expires_ns",
    "paid_budget_usd",
    "additional_requests",
    "max_background_requests",
    "max_requests",
    "hot_pools",
}
DROP_KEYS = {
    "artifact",
    "exam_root",
    "model_path",
    "weights_path",
    "private_key",
    "api_key",
    "access_token",
    "refresh_token",
    "credential",
    "credentials",
    "secret",
    "secrets",
}


def _protected(path: Path) -> bool:
    return any(
        PROTECTED_PARTS.intersection(re.split(r"[^a-z0-9]+", part.casefold()))
        for part in path.parts
    )


def _plain_dir(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir() or _protected(path):
        raise ValueError("snapshot_source_root_invalid")
    resolved = path.resolve(strict=True)
    if _protected(resolved):
        raise ValueError("snapshot_source_root_invalid")
    return resolved


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or _protected(path):
        raise ValueError("snapshot_input_invalid")
    if path.stat().st_size > 8_000_000:
        raise ValueError("snapshot_input_oversized")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("snapshot_input_invalid")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _copy_regular(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file() or _protected(source):
        raise ValueError("snapshot_input_invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _file_signatures(path: Path) -> dict[str, dict[str, Any] | None]:
    result: dict[str, dict[str, Any] | None] = {}
    for suffix in ("", "-wal", "-shm"):
        item = Path(str(path) + suffix)
        if not item.exists():
            result[suffix or "database"] = None
            continue
        if item.is_symlink() or not item.is_file() or _protected(item):
            raise ValueError("snapshot_database_invalid")
        stat = item.stat()
        result[suffix or "database"] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": _sha256(item),
        }
    return result


def _backup_sqlite(source_path: Path, destination_path: Path) -> dict[str, Any]:
    if source_path.is_symlink() or not source_path.is_file() or _protected(source_path):
        raise ValueError("snapshot_database_invalid")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    before = _file_signatures(source_path)
    with tempfile.TemporaryDirectory(prefix=".sqlite-capture-", dir=destination_path.parent) as raw:
        scratch = Path(raw) / source_path.name
        for suffix in ("", "-wal", "-shm"):
            original = Path(str(source_path) + suffix)
            if original.exists():
                if original.is_symlink() or not original.is_file():
                    raise ValueError("snapshot_database_invalid")
                shutil.copyfile(original, Path(str(scratch) + suffix))
        scratch_signatures = _file_signatures(scratch)
        after_copy = _file_signatures(source_path)
        source_content = {
            key: None if value is None else {"size": value["size"], "sha256": value["sha256"]}
            for key, value in before.items()
        }
        scratch_content = {
            key: None if value is None else {"size": value["size"], "sha256": value["sha256"]}
            for key, value in scratch_signatures.items()
        }
        if before != after_copy or source_content != scratch_content:
            raise ValueError("snapshot_database_changed_during_copy")
        source = sqlite3.connect(scratch.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
        target = sqlite3.connect(destination_path)
        try:
            source.execute("PRAGMA query_only=ON")
            source.execute("BEGIN")
            source.execute("SELECT count(*) FROM sqlite_master").fetchone()
            source.backup(target)
        finally:
            target.close()
            source.close()
    after_backup = _file_signatures(source_path)
    if before != after_backup:
        destination_path.unlink(missing_ok=True)
        raise ValueError("snapshot_database_changed_during_backup")
    return before


def _prune(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _prune(item) for key, item in value.items() if key.lower() not in DROP_KEYS}
    if isinstance(value, list):
        return [_prune(item) for item in value]
    return value


def _copy_json_tree(
    source: Path,
    destination: Path,
    *,
    registry: bool = False,
    preview_root: Path | None = None,
) -> None:
    if not source.exists():
        return
    if source.is_symlink() or not source.is_dir() or _protected(source):
        raise ValueError("snapshot_input_invalid")
    for item in sorted(source.iterdir()):
        if item.suffix != ".json":
            continue
        original = _read_json(item)
        row = _prune(original)
        if registry and source.name == "exams" and isinstance(original.get("exam_root"), str):
            row["exam_root"] = str((preview_root or destination.parent) / "paper" / item.stem)
        _write_json(destination / item.name, row)


def _copy_health(source: Path, destination: Path, allowed: set[str]) -> None:
    if not source.exists():
        return
    value = _read_json(source)
    _write_json(destination, {key: item for key, item in value.items() if key in allowed})


def _copy_operator_files(
    owner: Path, destination: Path, source_clone: Path, preview_root: Path
) -> dict[str, Any]:
    config = _read_json(owner / "config.json")
    clone_config = {key: value for key, value in config.items() if key in ROOT_CONFIG_KEYS}
    legacy_papers = _copy_legacy_papers(
        owner, destination, source_clone, preview_root, config.get("legacy_papers", [])
    )
    clone_config.update(
        source_root=str(source_clone),
        champion_model_path=str(preview_root / "models" / "not-copied"),
        legacy_papers=legacy_papers,
    )
    _write_json(destination / "config.json", clone_config)

    marker = owner / "STOP"
    if marker.exists():
        _copy_regular(marker, destination / "STOP")
    for name in ("collection-profile.json", "legacy-paused.json", "resource-priority.json"):
        path = owner / name
        if path.exists():
            _write_json(destination / name, _prune(_read_json(path)))

    operator_files = _backup_sqlite(owner / "operator.sqlite", destination / "operator.sqlite")
    for name in ("datasets", "candidates", "exams", "progress", "incidents"):
        _copy_json_tree(owner / name, destination / name, registry=True, preview_root=preview_root)
    return operator_files


def _copy_legacy_papers(
    owner: Path,
    destination: Path,
    source_clone: Path,
    preview_root: Path,
    entries: Any,
) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        return []
    work_root = owner.parent.parent.resolve(strict=True)
    copied: list[dict[str, Any]] = []
    for index, entry in enumerate(entries[:20]):
        if isinstance(entry, dict):
            raw = entry.get("root")
            label = entry.get("label")
        else:
            raw, label = entry, None
        if not isinstance(raw, str) or not Path(raw).is_absolute():
            continue
        source = Path(raw)
        if source.is_symlink() or _protected(source):
            continue
        try:
            source = source.resolve(strict=True)
            relative = source.relative_to(work_root)
        except (OSError, ValueError):
            continue
        if (
            not source.is_dir()
            or _protected(source)
            or any(part.startswith(".") for part in relative.parts)
        ):
            continue
        safe_label = re.sub(r"[^A-Za-z0-9_-]+", "-", str(label or source.name)).strip("-")
        safe_label = (safe_label or f"paper-{index + 1}")[:48]
        target = destination / "legacy" / safe_label
        if target.exists():
            safe_label = f"{safe_label}-{index + 1}"
            target = destination / "legacy" / safe_label
        target.mkdir(parents=True, exist_ok=True)
        config_path = source / "config.json"
        if config_path.is_symlink() or not config_path.is_file():
            continue
        paper_config = _read_json(config_path)
        config_allow = {
            "activation_ns",
            "expires_ns",
            "training_max_runs",
            "port",
        }
        safe_config = {key: value for key, value in paper_config.items() if key in config_allow}
        safe_config["source"] = str(source_clone)
        safe_config["frozen_exam_models"] = bool(paper_config.get("frozen_exam_models"))
        safe_config["port"] = 0
        _write_json(target / "config.json", safe_config)
        _copy_health(source / "health.json", target / "health.json", {"at_ns"})
        _copy_health(
            source / "paper-health.json",
            target / "paper-health.json",
            {"status", "category", "exit_code"},
        )
        marker = source / "STOP"
        if marker.exists():
            _copy_regular(marker, target / "STOP")
        dashboard = source / "dashboard.json"
        if dashboard.is_file() and not dashboard.is_symlink():
            _write_json(target / "dashboard.json", _prune(_read_json(dashboard)))
        training = source / "training"
        if training.is_dir() and not training.is_symlink():
            reservations = []
            for run in sorted(training.iterdir()):
                reservation = run / "reservation.json"
                if run.is_dir() and not run.is_symlink() and reservation.is_file():
                    value = _read_json(reservation)
                    if isinstance(value.get("started_ns"), int):
                        reservations.append((run.name, value["started_ns"]))
            for run_name, started_ns in reservations:
                _write_json(
                    target / "training" / run_name / "reservation.json",
                    {"started_ns": started_ns},
                )
        copied.append(
            {
                "label": str(label or source.name),
                "root": str(preview_root / "legacy" / safe_label),
            }
        )
    return copied


def _copy_source(owner: Path, destination: Path) -> tuple[Path, dict[str, Any]]:
    owner_config = _read_json(owner / "config.json")
    raw = owner_config.get("source_root")
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        raise ValueError("snapshot_source_binding_invalid")
    source = _plain_dir(Path(raw))
    if source.parent.resolve() != owner.parent.resolve():
        raise ValueError("snapshot_source_binding_outside_owner_family")
    source_clone = destination / "source"
    source_clone.mkdir(parents=True, exist_ok=True)

    source_config = _read_json(source / "config.json")
    _write_json(
        source_clone / "config.json",
        {key: value for key, value in source_config.items() if key in SOURCE_CONFIG_KEYS},
    )
    _copy_health(
        source / "health.json",
        source_clone / "health.json",
        {"at_ns", "attempts", "cooldown_ns", "failures", "pools"},
    )
    marker = source / "STOP"
    if marker.exists():
        _copy_regular(marker, source_clone / "STOP")
    source_files = _backup_sqlite(source / "index.sqlite", source_clone / "index.sqlite")
    return source_clone, source_files


def _copy_paper_roots(
    owner: Path, destination: Path, source_clone: Path, preview_root: Path
) -> None:
    exams = owner / "exams"
    if not exams.is_dir():
        return
    paper_base = owner / "paper"
    for entry in sorted(exams.glob("*.json")):
        record = _read_json(entry)
        raw = record.get("exam_root")
        if not isinstance(raw, str):
            continue
        source = Path(raw)
        if source.is_symlink() or _protected(source):
            continue
        try:
            source = source.resolve(strict=True)
            relative = source.relative_to(paper_base.resolve(strict=True))
        except (OSError, ValueError):
            continue
        if len(relative.parts) != 1 or source.is_symlink() or not source.is_dir():
            continue
        target = destination / "paper" / relative
        target.mkdir(parents=True, exist_ok=True)
        dashboard = source / "dashboard.json"
        if dashboard.is_file() and not dashboard.is_symlink():
            _write_json(target / "dashboard.json", _prune(_read_json(dashboard)))
        config_path = source / "config.json"
        if config_path.is_file() and not config_path.is_symlink():
            config = _read_json(config_path)
            allow = {"activation_ns", "expires_ns", "training_max_runs", "frozen_exam_models"}
            copied = {key: value for key, value in config.items() if key in allow}
            copied["source"] = str(source_clone)
            _write_json(target / "config.json", copied)
        _copy_health(source / "health.json", target / "health.json", {"at_ns"})
        _copy_health(
            source / "paper-health.json",
            target / "paper-health.json",
            {"status", "category", "exit_code"},
        )
        for marker_name in ("STOP", "TRAIN_STOP"):
            marker = source / marker_name
            if marker.exists():
                _copy_regular(marker, target / marker_name)
        training = source / "training"
        if training.is_dir() and not training.is_symlink():
            for run in sorted(training.iterdir()):
                reservation = run / "reservation.json"
                if run.is_dir() and not run.is_symlink() and reservation.is_file():
                    _write_json(
                        target / "training" / run.name / "reservation.json",
                        _prune(_read_json(reservation)),
                    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_snapshot(
    owner_root: Path, snapshots_root: Path, name: str, *, refresh: bool = False
) -> Path:
    owner = _plain_dir(Path(owner_root))
    snapshots = Path(snapshots_root)
    if _protected(snapshots) or not NAME_RE.fullmatch(name):
        raise ValueError("snapshot_destination_invalid")
    if snapshots.resolve().is_relative_to(owner.parent):
        raise ValueError("snapshot_destination_inside_source_family")
    snapshots.mkdir(parents=True, exist_ok=True)
    if snapshots.is_symlink():
        raise ValueError("snapshot_destination_invalid")
    final_name = name
    if (snapshots / final_name).exists():
        if not refresh:
            raise FileExistsError("snapshot_exists; pass --refresh for a new snapshot")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        final_name = f"{name}-{stamp}"
    destination = snapshots / final_name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("snapshot_exists")
    stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=snapshots))
    started_ns = time.time_ns()
    try:
        source_clone, source_files = _copy_source(owner, stage)
        source_reference = destination / "source"
        operator_files = _copy_operator_files(owner, stage, source_reference, destination)
        _copy_paper_roots(owner, stage, source_reference, destination)
        completed_ns = time.time_ns()
        _write_json(
            stage / "dev-preview.json",
            {
                "schema": "brain-dev-operator-preview-v1",
                "source_snapshot_at": datetime.fromtimestamp(completed_ns / 1e9, UTC).isoformat(
                    timespec="seconds"
                ),
                "snapshot_started_ns": started_ns,
                "snapshot_completed_ns": completed_ns,
                "source_label": f"{owner.parent.name}/{owner.name}",
                "operator_database_sha256": _sha256(stage / "operator.sqlite"),
                "source_index_sha256": _sha256(source_clone / "index.sqlite"),
                "source_family_files": {
                    "operator_database": operator_files,
                    "source_index": source_files,
                },
                "omitted": [
                    "deployment and binding files",
                    "credentials and private configuration",
                    "model weights and corpus artifacts",
                    "provider journals and captures",
                    "Watch Market sessions and provider state",
                ],
            },
        )
        stage.rename(destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return destination


def _snapshot_path(raw: Path, snapshots_root: Path) -> Path:
    if snapshots_root.is_symlink():
        raise ValueError("snapshot_destination_invalid")
    path = Path(raw)
    if path.is_symlink() or not path.is_dir() or _protected(path):
        raise ValueError("snapshot_invalid")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(snapshots_root.resolve()):
        raise ValueError("snapshot_outside_workspace")
    info = _read_json(resolved / "dev-preview.json")
    if info.get("schema") != "brain-dev-operator-preview-v1":
        raise ValueError("snapshot_invalid")
    return resolved


def latest_snapshot(snapshots_root: Path) -> Path:
    """Select the newest completed preview, never a pending directory or a link."""
    if Path(snapshots_root).is_symlink():
        raise ValueError("snapshot_destination_invalid")
    candidates = []
    for path in Path(snapshots_root).iterdir():
        if path.name.startswith(".") or path.is_symlink() or not path.is_dir():
            continue
        if not (path / "dev-preview.json").is_file():
            continue
        resolved = _snapshot_path(path, snapshots_root)
        captured = _read_json(resolved / "dev-preview.json").get("snapshot_completed_ns")
        if type(captured) is not int or captured <= 0:
            raise ValueError("snapshot_capture_time_invalid")
        candidates.append((captured, resolved.name, resolved))
    if not candidates:
        raise ValueError("no_completed_preview; run the snapshot command first")
    return max(candidates)[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot", help="copy an owner state to a disposable snapshot")
    snapshot.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    snapshot.add_argument("--snapshots-root", type=Path, default=DEFAULT_SNAPSHOTS)
    snapshot.add_argument("--name", default="owner-operator")
    snapshot.add_argument("--refresh", action="store_true")
    serve = sub.add_parser("serve", help="serve one snapshot on loopback, GET-only")
    selection = serve.add_mutually_exclusive_group(required=True)
    selection.add_argument("--snapshot", type=Path)
    selection.add_argument("--latest", action="store_true", help="use the newest completed preview")
    serve.add_argument("--snapshots-root", type=Path, default=DEFAULT_SNAPSHOTS)
    serve.add_argument("--port", type=int, default=8793)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = build_snapshot(
                args.source_root, args.snapshots_root, args.name, refresh=args.refresh
            )
            print(result)
            return 0
        selected = latest_snapshot(args.snapshots_root) if args.latest else args.snapshot
        snapshot_root = _snapshot_path(selected, args.snapshots_root)
        from memecoin_operator.web import serve as serve_operator

        print(f"Developer read-only preview: http://127.0.0.1:{args.port}/", flush=True)
        print("Stop with Ctrl-C; create a new snapshot and restart to refresh.", flush=True)
        serve_operator(snapshot_root, args.port, read_only=True)
        return 0
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"preview failed: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Developer read-only preview stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
