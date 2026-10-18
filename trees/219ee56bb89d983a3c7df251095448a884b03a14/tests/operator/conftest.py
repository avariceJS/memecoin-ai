"""Shared adapter double for operator tests. No network and no parent adapter module."""

import importlib.util
from pathlib import Path

import pytest


def _fake_source() -> str:
    path = Path(__file__).with_name("test_control.py")
    spec = importlib.util.spec_from_file_location("operator_test_control_source", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("operator_test_control_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FAKE


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    path = tmp_path / "fake_adapter.py"
    path.write_text(_fake_source())
    monkeypatch.setenv("MEMECOIN_OPERATOR_ADAPTER", str(path))
    return path
