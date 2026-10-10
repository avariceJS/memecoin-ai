from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tools import brain_senses_campaign3_handoff as handoff


def _authority_record() -> dict:
    next_hour = (datetime.now(UTC) + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return {
        "authority": {"lease_expires_ns": 9_999_999_999_999_999_999},
        "first_frame": {"expected_bucket": next_hour.strftime("%Y%m%dT%H0000Z")},
    }


def _prepare_activation(monkeypatch, tmp_path):
    root = tmp_path / "campaign3"
    root.mkdir()
    (root / "predecessor-lineage.json").write_text("{}\n")
    monkeypatch.setattr(handoff, "CAMPAIGN3_ROOT", root)
    monkeypatch.setattr(handoff, "_record", lambda _root: _authority_record())
    monkeypatch.setattr(
        handoff,
        "_campaign_plist",
        lambda _root, _lineage, label: {"Label": label, "ProgramArguments": ["local-test"]},
    )
    monkeypatch.setattr(handoff.shutil, "disk_usage", lambda _path: SimpleNamespace(free=10**12))
    return root


def test_activate_fails_closed_if_controller_or_campaign_is_already_loaded(monkeypatch, tmp_path):
    root = _prepare_activation(monkeypatch, tmp_path)
    label = f"ai.memecoin.brain.senses.{handoff.CAMPAIGN3_ID}"
    monkeypatch.setattr(
        handoff, "_loaded", lambda current, runner: current == handoff.CONTROLLER_LABEL
    )
    calls = []

    def runner(args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    with pytest.raises(ValueError, match="duplicate_or_predecessor_service_loaded"):
        handoff.activate(launch_agents=tmp_path, runner=runner)

    assert calls == []
    assert not (tmp_path / f"{label}.plist").exists()
    assert (root / "predecessor-lineage.json").exists()


def test_activation_crash_boundary_never_retries_launch(monkeypatch, tmp_path):
    _prepare_activation(monkeypatch, tmp_path)
    monkeypatch.setattr(handoff, "_loaded", lambda _label, runner: False)
    calls = []

    def interrupted_runner(args, **_kwargs):
        calls.append(args)
        raise TimeoutError("simulated crash after durable plist creation")

    with pytest.raises(TimeoutError):
        handoff.activate(launch_agents=tmp_path, runner=interrupted_runner)

    plist_path = tmp_path / f"ai.memecoin.brain.senses.{handoff.CAMPAIGN3_ID}.plist"
    assert plist_path.is_file()
    assert len(calls) == 1

    def retry_runner(args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    with pytest.raises(ValueError, match="c3_plist_already_exists_single_use"):
        handoff.activate(launch_agents=tmp_path, runner=retry_runner)
    assert len(calls) == 1
