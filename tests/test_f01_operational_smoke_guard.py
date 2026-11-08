from pathlib import Path

import pytest

from tools.f01_operational_smoke import _audit_guard


@pytest.mark.parametrize("path_type", [str, bytes, Path])
def test_protected_open_is_denied_through_physical_and_alias_paths(tmp_path, path_type):
    physical_work = tmp_path / "physical-work"
    protected = physical_work / "calendar-execution-01"
    protected.mkdir(parents=True)
    alias = tmp_path / "owner-work"
    alias.symlink_to(physical_work, target_is_directory=True)
    audit = _audit_guard(alias / "calendar-execution-01")
    for candidate in [protected / "unopened.json", alias / "calendar-execution-01/unopened.json"]:
        argument = bytes(candidate) if path_type is bytes else path_type(candidate)
        with pytest.raises(PermissionError, match="D43_protected"):
            audit("open", (argument, "r", 0))
    assert list(protected.iterdir()) == []


def test_guard_preserves_network_denial_and_sibling_path_boundary(tmp_path):
    protected = tmp_path / "calendar-execution-01"
    protected.mkdir()
    audit = _audit_guard(protected)
    with pytest.raises(PermissionError, match="D50_network_forbidden"):
        audit("socket.connect", (None, ("127.0.0.1", 1)))
    audit("open", (str(tmp_path / "calendar-execution-010/unopened.json"), "r", 0))
