import hashlib
import json
import sqlite3

import pytest

from memecoin_operator.control import initialize
from memecoin_operator.preview import validate_preview
from tools.brain_dev_operator_preview import _backup_sqlite, build_snapshot, latest_snapshot


@pytest.fixture
def preview(tmp_path):
    root = tmp_path / "snapshot"
    source = root / "source"
    source.mkdir(parents=True)
    (root / "operator.sqlite").write_bytes(b"unused fixture operator database")
    (source / "index.sqlite").write_bytes(b"unused fixture source database")
    (root / "config.json").write_text(
        json.dumps(
            {"source_root": str(source), "champion_model_path": str(root / "models/not-copied")}
        )
    )
    (root / "dev-preview.json").write_text(
        json.dumps(
            {
                "schema": "brain-dev-operator-preview-v1",
                "source_snapshot_at": "2026-10-19T00:00:00Z",
                "operator_database_sha256": hashlib.sha256(
                    (root / "operator.sqlite").read_bytes()
                ).hexdigest(),
                "source_index_sha256": hashlib.sha256(
                    (source / "index.sqlite").read_bytes()
                ).hexdigest(),
            }
        )
    )
    return root


def test_preview_rejects_external_binding_before_opening_sqlite(preview, tmp_path):
    config_path = preview / "config.json"
    config = json.loads(config_path.read_text())
    config["source_root"] = str(tmp_path / "authoritative-source")
    config_path.write_text(json.dumps(config))
    from memecoin_operator.web import make_server

    with pytest.raises(ValueError, match="developer_preview_binding_invalid"):
        make_server(preview, 0, read_only=True)


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_preview_rejects_linked_databases(preview, tmp_path, link_kind):
    original = tmp_path / "authoritative.sqlite"
    original.write_bytes(b"must not open")
    database = preview / "operator.sqlite"
    database.unlink()
    if link_kind == "symlink":
        database.symlink_to(original)
    else:
        database.hardlink_to(original)
    with pytest.raises(ValueError, match="developer_preview_link_invalid"):
        validate_preview(preview)
    assert original.read_bytes() == b"must not open"


def test_preview_rejects_changed_database(preview):
    (preview / "source/index.sqlite").write_bytes(b"changed")
    with pytest.raises(ValueError, match="developer_preview_database_changed"):
        validate_preview(preview)


def test_latest_rejects_symlinked_snapshot_directory(tmp_path):
    source = tmp_path / "snapshots"
    source.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="snapshot_destination_invalid"):
        latest_snapshot(alias)


@pytest.mark.parametrize("name", ["brain-f01-exam", "brain_d43_capture", "reserved-corpus"])
def test_nonordinary_scope_is_rejected_before_read(tmp_path, monkeypatch, name):
    source = tmp_path / name
    source.mkdir()
    monkeypatch.setattr(
        "tools.brain_dev_operator_preview._read_json",
        lambda *_: pytest.fail("out-of-scope payload read"),
    )
    with pytest.raises(ValueError, match="snapshot_source_root_invalid"):
        build_snapshot(source, tmp_path / "snapshots", "preview")


def test_snapshot_destination_cannot_write_into_source_family(tmp_path):
    owner = tmp_path / "owner" / "operator"
    owner.mkdir(parents=True)
    with pytest.raises(ValueError, match="snapshot_destination_inside_source_family"):
        build_snapshot(owner, owner.parent / "snapshots", "preview")


def test_latest_preview_ignores_links_and_pending_directories(tmp_path):
    for name, captured in (("older", 10), ("newer", 20), (".pending-incomplete", 30)):
        path = tmp_path / name
        path.mkdir()
        (path / "dev-preview.json").write_text(
            json.dumps(
                {"schema": "brain-dev-operator-preview-v1", "snapshot_completed_ns": captured}
            )
        )
    (tmp_path / "linked").symlink_to(tmp_path / ".pending-incomplete", target_is_directory=True)
    assert latest_snapshot(tmp_path) == tmp_path / "newer"


def test_snapshot_is_create_only_and_rebinds_paths_without_artifacts(tmp_path):
    owner = tmp_path / "owner" / "operator"
    source = tmp_path / "owner" / "source"
    legacy = tmp_path / "owner" / "legacy-paper"
    snapshots = tmp_path / "snapshots"
    owner.mkdir(parents=True)
    source.mkdir()
    legacy.mkdir()
    source_config = {
        "product": "operator",
        "mode": "operator",
        "provider": "geckoterminal",
        "profile": "balanced",
        "init_ns": 10,
        "expires_ns": 20,
        "paid_budget_usd": 0,
        "additional_requests": 0,
        "max_background_requests": 1,
        "max_requests": 1,
        "hot_pools": [],
        "api_key": "must-not-copy",
    }
    (source / "config.json").write_text(json.dumps(source_config))
    (source / "health.json").write_text(json.dumps({"at_ns": 15, "api_key": "drop"}))
    (source / "STOP").write_text("stopped")
    with sqlite3.connect(source / "index.sqlite") as db:
        db.execute("CREATE TABLE firsts (pool TEXT, open_s INTEGER, available INTEGER)")
        db.execute("INSERT INTO firsts VALUES ('pool-a', 1, 2)")
    (legacy / "config.json").write_text(
        json.dumps(
            {
                "activation_ns": 1,
                "expires_ns": 2,
                "training_max_runs": 3,
                "frozen_exam_models": ["exam-id"],
                "port": 9012,
                "source": str(source),
                "api_key": "must-not-copy",
            }
        )
    )
    (legacy / "health.json").write_text(json.dumps({"at_ns": 2, "api_key": "drop"}))
    (legacy / "paper-health.json").write_text(
        json.dumps({"status": "stopped", "category": "manual", "secret": "drop"})
    )
    (legacy / "STOP").write_text("stopped")

    config = {
        "product": "operator",
        "mode": "operator",
        "provider": "geckoterminal",
        "profile": "balanced",
        "init_ns": 10,
        "expires_ns": 20,
        "paid_budget_usd": 0,
        "additional_requests": 0,
        "max_background_requests": 1,
        "source_root": str(source),
        "champion_model_path": str(tmp_path / "private-model"),
        "champion_model_sha": "a" * 64,
        "legacy_papers": [{"label": "old-paper", "root": str(legacy)}],
    }
    initialize(owner, config)
    (owner / "deployment.json").write_text('{"bundle":"private"}')
    (owner / "candidates").mkdir()
    (owner / "candidates" / "candidate-a.json").write_text(
        json.dumps({"id": "candidate-a", "artifact": str(tmp_path / "private-model")})
    )
    (owner / "exams").mkdir()
    paper = owner / "paper" / "job-a"
    paper.mkdir(parents=True)
    (paper / "dashboard.json").write_text(json.dumps({"books": []}))
    (owner / "exams" / "job-a.json").write_text(
        json.dumps({"id": "job-a", "exam_root": str(paper), "status": "completed"})
    )

    destination = build_snapshot(owner, snapshots, "test")
    copied_config = json.loads((destination / "config.json").read_text())
    copied_source_config = json.loads((destination / "source" / "config.json").read_text())
    copied_candidate = json.loads((destination / "candidates" / "candidate-a.json").read_text())
    copied_exam = json.loads((destination / "exams" / "job-a.json").read_text())
    copied_legacy = copied_config["legacy_papers"][0]
    copied_legacy_root = destination / "legacy" / "old-paper"
    copied_legacy_config = json.loads((copied_legacy_root / "config.json").read_text())
    assert copied_config["source_root"] == str(destination / "source")
    assert copied_config["champion_model_path"] == str(destination / "models" / "not-copied")
    assert copied_legacy == {"label": "old-paper", "root": str(copied_legacy_root)}
    assert copied_legacy_config["source"] == str(destination / "source")
    assert copied_legacy_config["frozen_exam_models"] is True
    assert copied_legacy_config["port"] == 0
    assert "api_key" not in copied_legacy_config
    assert json.loads((copied_legacy_root / "paper-health.json").read_text()) == {
        "category": "manual",
        "status": "stopped",
    }
    assert "api_key" not in copied_source_config
    assert "api_key" not in json.loads((destination / "source" / "health.json").read_text())
    assert "deployment.json" not in {path.name for path in destination.iterdir()}
    assert "artifact" not in copied_candidate
    assert copied_exam["exam_root"] == str(destination / "paper" / "job-a")
    assert (destination / "paper" / "job-a" / "dashboard.json").is_file()
    with sqlite3.connect(destination / "source" / "index.sqlite") as db:
        assert db.execute("SELECT pool FROM firsts").fetchone() == ("pool-a",)
    with pytest.raises(FileExistsError, match="snapshot_exists"):
        build_snapshot(owner, snapshots, "test")


def test_snapshot_rejects_source_outside_owner_family(tmp_path):
    owner = tmp_path / "owner" / "operator"
    owner.mkdir(parents=True)
    outside = tmp_path / "outside" / "source"
    outside.mkdir(parents=True)
    (owner / "config.json").write_text(json.dumps({"source_root": str(outside)}))
    with pytest.raises(ValueError, match="snapshot_source_binding_outside_owner_family"):
        build_snapshot(owner, tmp_path / "snapshots", "unsafe")
    assert not (tmp_path / "snapshots" / "unsafe").exists()


def test_sqlite_backup_detects_wal_change_during_copy(tmp_path, monkeypatch):
    database = tmp_path / "source.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE sample (value INTEGER)")
    wal = tmp_path / "source.sqlite-wal"
    shm = tmp_path / "source.sqlite-shm"
    wal.write_bytes(b"stable wal bytes")
    shm.write_bytes(b"stable shm bytes")
    original_copyfile = __import__("shutil").copyfile

    def mutate_wal_after_capture(source, destination):
        original_copyfile(source, destination)
        if str(source) == str(wal):
            wal.write_bytes(b"changed during capture")

    monkeypatch.setattr(
        "tools.brain_dev_operator_preview.shutil.copyfile", mutate_wal_after_capture
    )
    with pytest.raises(ValueError, match="snapshot_database_changed_during_copy"):
        _backup_sqlite(database, tmp_path / "snapshot.sqlite")


def test_preview_server_rejects_missing_or_invalid_snapshot_metadata(tmp_path):
    from memecoin_operator.web import make_server

    root = tmp_path / "snapshot"
    root.mkdir()
    with sqlite3.connect(root / "operator.sqlite"):
        pass
    with pytest.raises(ValueError, match="developer_preview_metadata_required"):
        make_server(root, 0, read_only=True)
    (root / "dev-preview.json").write_text(json.dumps({"schema": "untrusted"}))
    with pytest.raises(ValueError, match="developer_preview_metadata_invalid"):
        make_server(root, 0, read_only=True)
