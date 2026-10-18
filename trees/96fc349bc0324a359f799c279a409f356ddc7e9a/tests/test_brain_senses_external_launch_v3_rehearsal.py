from __future__ import annotations

import hashlib
import plistlib
from pathlib import Path
from typing import Any

import pytest

from tools import brain_senses_external_launch_v3 as launch_v3
from tools import brain_senses_external_launch_v3_rehearsal as entry
from tools import brain_senses_external_launch_v3_rehearsal_qualify_v1 as qualifier
from tools import brain_senses_launch_v3_synthetic_fixture_v1 as fixture


def test_source_pinned_synthetic_activation_and_empty_journal_fixtures() -> None:
    assert hashlib.sha256(fixture.ACTIVATION_BYTES).hexdigest() == fixture.ACTIVATION_SHA256
    assert hashlib.sha256(fixture.JOURNAL_BYTES).hexdigest() == fixture.JOURNAL_SHA256
    binding = fixture.validate_activation(fixture.ACTIVATION_BYTES, fixture.WINDOW_START_NS + 1)
    snapshot = fixture.verify_journal(fixture.JOURNAL_BYTES, binding)
    assert snapshot.verified is True
    assert snapshot.reservation_ids == ()
    assert snapshot.intent_ids == ()
    assert snapshot.event_tail_sha256 == "2" * 64


def test_untrusted_envelope_and_out_of_window_have_no_dispatch_or_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"dispatch": 0, "receipt": 0}
    with pytest.raises(launch_v3.LaunchV3Error, match="synthetic_plan_untrusted"):
        launch_v3.dispatch_once(
            fixture.PLAN_BYTES + b"tampered",
            plan_validator=lambda raw, started: fixture.validate_synthetic_plan(
                raw, started.utc_ns
            ),
            locks=launch_v3.OrderedLocks(*(_null_lock for _ in range(4))),
            dispatch=lambda _binding: calls.__setitem__("dispatch", calls["dispatch"] + 1),
            proof_locks=launch_v3.OrderedLocks(*(_null_lock for _ in range(4))),
            snapshot_reader=lambda binding: fixture.verify_journal(fixture.JOURNAL_BYTES, binding),
            failure_sink=lambda **_kwargs: calls.__setitem__("receipt", calls["receipt"] + 1),
        )
    assert calls == {"dispatch": 0, "receipt": 0}

    with pytest.raises(launch_v3.LaunchV3Error, match="synthetic_activation_untrusted"):
        fixture.validate_activation(fixture.ACTIVATION_BYTES + b"tampered", fixture.WINDOW_START_NS)

    monkeypatch.setattr(
        launch_v3,
        "callback_start",
        lambda: launch_v3.CallbackTimes(fixture.WINDOW_END_NS, 999),
    )
    with pytest.raises(launch_v3.LaunchV3Error, match="callback_outside_synthetic_window"):
        launch_v3.dispatch_once(
            fixture.PLAN_BYTES,
            plan_validator=lambda raw, started: fixture.validate_synthetic_plan(
                raw, started.utc_ns
            ),
            locks=launch_v3.OrderedLocks(*(_null_lock for _ in range(4))),
            dispatch=lambda _binding: calls.__setitem__("dispatch", calls["dispatch"] + 1),
            proof_locks=launch_v3.OrderedLocks(*(_null_lock for _ in range(4))),
            snapshot_reader=lambda binding: fixture.verify_journal(fixture.JOURNAL_BYTES, binding),
            failure_sink=lambda **_kwargs: calls.__setitem__("receipt", calls["receipt"] + 1),
        )
    assert calls == {"dispatch": 0, "receipt": 0}


def test_create_only_result_marker_suppresses_restart_dispatch(tmp_path: Path) -> None:
    path = tmp_path / "state" / "result.json"
    payload = entry._canonical(
        {
            "schema": entry.RESULT_SCHEMA,
            "synthetic": True,
            "status": "pre_intent_failure_receipted",
            "dispatch_attempts": 1,
        }
    )
    assert entry._persist_result(path, payload) is True
    before = path.read_bytes()
    assert entry._persist_result(path, b"must never replace") is False
    assert path.read_bytes() == before


def test_versioned_plist_template_is_manual_path_only_and_has_no_triggers() -> None:
    repo = Path(__file__).resolve().parents[1]
    path = repo / "ops/launchd/ai.memecoin.brain.senses.launch-v3-rehearsal.plist"
    value: Any = plistlib.loads(path.read_bytes())
    assert isinstance(value, dict)
    assert set(value) == {"Label", "ProgramArguments", "StandardOutPath", "StandardErrorPath"}
    assert value["Label"].startswith("ai.memecoin.brain.senses.launch-v3-rehearsal-")
    assert all(isinstance(item, str) and item for item in value["ProgramArguments"])
    assert "--config" in value["ProgramArguments"]
    for key in (
        "RunAtLoad",
        "KeepAlive",
        "StartCalendarInterval",
        "StartInterval",
        "EnvironmentVariables",
    ):
        assert key not in value


def test_launchctl_enable_clears_disabled_state_without_loading_label() -> None:
    label = "ai.memecoin.brain.senses.launch-v3-rehearsal-testnonce"
    assert qualifier._parse_override_state(label, f'"{label}" => disabled\n') == "disabled"
    assert qualifier._parse_override_state(label, f'"{label}" => enabled\n') == "enabled"
    assert qualifier._parse_override_state(label, "") == "default"
    with pytest.raises(qualifier.QualificationError, match="launchctl_disabled_state_ambiguous"):
        qualifier._parse_override_state(
            label,
            f'"{label}" => disabled\n"{label}" => enabled\n',
        )


def _null_lock():
    from contextlib import nullcontext

    return nullcontext()
