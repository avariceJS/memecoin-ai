from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tools.brain_host_held_data_root_set_v2_cli import (
    SPEC_SCHEMA,
    CLIError,
    _read_spec,
    main,
)


def _spec() -> dict[str, Any]:
    return {
        "schema": SPEC_SCHEMA,
        "plan_path": "/private/plan.json",
        "plan_sha256": "a" * 64,
        "source_mountpoint": "/Volumes/Data",
        "storage_config_path": "/private/storage.json",
        "storage_volume_uuid": "8f8f1081-9be4-4914-9111-75f1b90255bf",
        "storage_root_id": "9428695af6d4f46c08e1a444cdc66de8",
        "minimum_free_bytes": 50 * 1024**3,
        "role_policy_path": "/private/roles.json",
        "role_policy_sha256": "b" * 64,
        "directory_policies": {
            "owner_checkout": {"path": "/private/tree.json", "sha256": "c" * 64}
        },
        "file_policies": {},
        "scratch_root_relative": "host-recovery/scratch/root-set-v2",
        "scratch_budget_bytes_per_role": 1024 * 1024,
    }


def _write_spec(path: Path, document: dict[str, Any]) -> str:
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()


def test_spec_accepts_pinned_canonical_private_document(tmp_path: Path) -> None:
    path = tmp_path / "spec.json"
    digest = _write_spec(path, _spec())
    assert _read_spec(path, digest)["schema"] == SPEC_SCHEMA


@pytest.mark.parametrize("mode", [0o644, 0o400])
def test_spec_requires_owner_private_mode(tmp_path: Path, mode: int) -> None:
    path = tmp_path / "spec.json"
    digest = _write_spec(path, _spec())
    path.chmod(mode)
    with pytest.raises(CLIError, match="spec_file_unsafe"):
        _read_spec(path, digest)


def test_spec_rejects_digest_drift_duplicate_fields_and_extra_schema(tmp_path: Path) -> None:
    path = tmp_path / "spec.json"
    digest = _write_spec(path, _spec())
    with pytest.raises(CLIError, match="spec_sha256_mismatch"):
        _read_spec(path, "d" * 64)
    path.write_text('{"schema":"x","schema":"y"}\n')
    path.chmod(0o600)
    with pytest.raises(CLIError, match="spec_duplicate_json_field"):
        _read_spec(path, hashlib.sha256(path.read_bytes()).hexdigest())
    document = _spec()
    document["unexpected"] = True
    digest = _write_spec(path, document)
    with pytest.raises(CLIError, match="spec_schema_fields_invalid"):
        _read_spec(path, digest)


def test_spec_rejects_bad_role_pin_roster(tmp_path: Path) -> None:
    document = _spec()
    document["file_policies"] = {
        "codex_mcp_config": {"path": "/private/file.json", "sha256": "not-a-digest"}
    }
    path = tmp_path / "spec.json"
    digest = _write_spec(path, document)
    with pytest.raises(CLIError, match="spec_file_policy_pin_invalid"):
        _read_spec(path, digest)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        (
            "storage_volume_uuid",
            "00000000-0000-0000-0000-000000000000",
            "spec_storage_binding_not_canonical",
        ),
        ("minimum_free_bytes", 0, "spec_capacity_values_invalid"),
    ],
)
def test_spec_rejects_unpinned_storage_or_low_headroom(
    tmp_path: Path, field: str, value: object, error: str
) -> None:
    document = _spec()
    document[field] = value
    path = tmp_path / "spec.json"
    digest = _write_spec(path, document)
    with pytest.raises(CLIError, match=error):
        _read_spec(path, digest)


def test_cli_argument_failures_are_machine_readable_and_fail_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["verify", "--spec", "/missing", "--spec-sha256", "a" * 64]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "verify_requires_set_id_and_index_pin"
    assert output["safe_to_erase"] is False
    with pytest.raises(SystemExit) as help_exit:
        main(["--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "verify only" in help_text
    assert "capture" in help_text
