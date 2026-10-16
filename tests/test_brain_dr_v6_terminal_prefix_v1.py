from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from memecoin_research_storage import ReadOnlyStorageGuard
from tools import brain_dr_v6_terminal_prefix_v1 as audit
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json


def _journal(*, seq_override: Any = None, partial: bool = False, large: bool = False) -> bytes:
    kinds = [
        "plan",
        *(["intent"] * 5),
        *(["dispatch"] * 4),
        *(["outcome"] * 5),
        *(["confirmation"] * 5),
        "missed",
    ]
    previous: str | None = None
    lines: list[bytes] = []
    for seq, kind in enumerate(kinds):
        core = {
            "schema": "brain-senses-gecko-capture-event-v6",
            "schema_version": 6,
            "seq": seq if seq_override is None or seq else seq_override,
            "kind": kind,
            "at_ns": seq + 1,
            "previous_sha256": previous,
            "fields": {"opaque": "x" * 70_000} if large and seq == 0 else {},
        }
        digest = audit._sha(canonical_json(core))
        lines.append(canonical_json({**core, "event_sha256": digest}))
        previous = digest
    result = b"".join(lines)
    return result[:-1] if partial else result


def test_events_accepts_exact_pinned_shape_and_chain() -> None:
    rows = audit._events(_journal())
    assert len(rows) == 21
    assert rows[-1]["kind"] == "missed"


def test_events_rejects_bool_sequence_even_when_resealed() -> None:
    with pytest.raises(audit.PrefixAuditError):
        audit._events(_journal(seq_override=True))


@pytest.mark.parametrize("kwargs", [{"partial": True}, {"large": True}])
def test_events_rejects_partial_tail_and_oversized_line(kwargs: dict[str, bool]) -> None:
    with pytest.raises(audit.PrefixAuditError):
        audit._events(_journal(**kwargs))


def test_events_rejects_noncanonical_duplicate_json_key() -> None:
    row = json.loads(_journal().splitlines()[0])
    canonical = canonical_json(row)
    duplicate = canonical.replace(b'"seq":0', b'"seq":0,"seq":0', 1)
    raw = duplicate + b"".join(_journal().splitlines(keepends=True)[1:])
    with pytest.raises(audit.PrefixAuditError):
        audit._events(raw)


def test_external_observations_require_exact_disabled_unloaded_labels() -> None:
    expected = {"label-a": "a" * 64, "label-b": "b" * 64}
    observation = {
        "schema": audit.OBSERVATION_SCHEMA,
        "observed_ns": 1,
        "labels": {label: {"disabled": True, "loaded": False, "pid": None} for label in expected},
        "active_callback_pids": 0,
        "active_callback_fds": 0,
        "plist_sha256": expected,
    }
    assert audit._observations(canonical_json(observation), expected) == observation

    observation["labels"]["label-a"]["loaded"] = True  # type: ignore[index]
    with pytest.raises(audit.PrefixAuditError):
        audit._observations(canonical_json(observation), expected)


@pytest.fixture
def full_prefix(tmp_path: Path):
    """Actual read-only DescriptorWriter over a disposable synthetic guard/tree."""
    data = tmp_path / "BrainData"
    release = "a" * 32
    service = f"{audit.SERVICE_PREFIX}/{release}"
    control = f"{audit.CONTROL_PREFIX}/{release}"
    volume, root_id = "11111111-2222-3333-4444-555555555555", "b" * 32

    def put(path: str, raw: bytes) -> None:
        target = data / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o600)

    def sealed(core: dict[str, Any], key: str) -> dict[str, Any]:
        return {**core, key: audit._sha(canonical_json(core))}

    source_map = {f"synthetic-source-{i}.py": "c" * 64 for i in range(15)}
    manifest = sealed(
        {
            "schema": "brain-senses-gecko-capture-v6-source-manifest",
            "schema_version": 6,
            "source_commit": "d" * 40,
            "source_map": source_map,
            "source_paths": list(source_map),
        },
        "source_manifest_sha256",
    )
    policy = sealed(
        {"schema": "brain-senses-gecko-capture-policy-v6", "schema_version": 6}, "policy_sha256"
    )
    prior_path = f"control/gecko-v6-prior-usage/{'e' * 64}.json"
    prior_raw = b"[]\n"
    epoch = sealed(
        {
            "schema": "brain-senses-gecko-capture-epoch-v6",
            "schema_version": 6,
            "release_id": release,
            "root": service,
            "policy_sha256": policy["policy_sha256"],
            "prior_usage": {"path": prior_path, "sha256": audit._sha(prior_raw)},
        },
        "epoch_sha256",
    )
    activation = sealed(
        {
            "schema": "brain-senses-gecko-capture-v6-activation",
            "schema_version": 6,
            "release_id": release,
            "service_root": service,
            "activation_id": "f" * 32,
            "capture_authorized": True,
            "epoch_sha256": epoch["epoch_sha256"],
            "policy_sha256": policy["policy_sha256"],
            "source_manifest_sha256": manifest["source_manifest_sha256"],
            "source_commit": manifest["source_commit"],
        },
        "activation_sha256",
    )
    genesis = sealed(
        {
            "schema": "brain-senses-gecko-capture-genesis-v6",
            "schema_version": 6,
            "epoch_sha256": epoch["epoch_sha256"],
            "policy_sha256": policy["policy_sha256"],
        },
        "genesis_sha256",
    )
    for name, value in (("epoch.json", epoch), ("policy.json", policy), ("genesis.json", genesis)):
        put(f"{service}/{name}", canonical_json(value))
    put(f"{control}/activation.json", canonical_json(activation))
    put(f"{control}/source-manifest.json", canonical_json(manifest))
    put(f"{service}/{prior_path}", prior_raw)
    put(f"{service}/.lane.lock", b"")
    put(audit.GLOBAL_LOCK, b"")
    for directory in ("intents", "confirmations", "responses"):
        (data / service / directory).mkdir(parents=True, exist_ok=True)

    events: list[dict[str, Any]] = []

    def append(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
        row = sealed(
            {
                "schema": "brain-senses-gecko-capture-event-v6",
                "schema_version": 6,
                "seq": len(events),
                "kind": kind,
                "at_ns": len(events) + 1,
                "previous_sha256": events[-1]["event_sha256"] if events else None,
                "fields": fields,
            },
            "event_sha256",
        )
        events.append(row)
        return row

    plan_raw, plan_sha = b"opaque plan bytes; never replayed\n", "1" * 64
    put(f"{service}/plans/123.json", plan_raw)
    append(
        "plan",
        {
            "close_ns": 123,
            "plan_path": "plans/123.json",
            "plan_sha256": plan_sha,
            "plan_file_sha256": audit._sha(plan_raw),
        },
    )
    request_ids = [audit._sha(f"synthetic-{i}".encode()) for i in range(5)]
    for request in request_ids:
        raw = b"opaque request bytes; never parsed: " + request.encode()
        put(f"{service}/requests/{request}.json", raw)
        append(
            "intent",
            {
                "request_id": request,
                "close_ns": 123,
                "request_path": f"requests/{request}.json",
                "request_sha256": audit._sha(raw),
                "plan_sha256": plan_sha,
            },
        )
    for request in request_ids[:4]:
        append("dispatch", {"request_id": request})
    outcomes = []
    for index, request in enumerate(request_ids):
        # Deliberately not JSON: the auditor must hash these bytes opaquely.
        raw = b"\x00\xffopaque-provider-body" + bytes([index])
        present = index < 4
        path = f"responses/{request}.bin" if present else None
        if path:
            put(f"{service}/{path}", raw)
        outcomes.append(
            append(
                "outcome",
                {
                    "request_id": request,
                    "raw_path": path,
                    "raw_sha256": audit._sha(raw) if present else None,
                    "raw_bytes": len(raw) if present else 0,
                },
            )
        )
    for request, outcome in zip(request_ids, outcomes, strict=True):
        append("confirmation", {"request_id": request, "outcome_sha256": outcome["event_sha256"]})
    append("missed", {"plan_sha256": plan_sha})
    journal = b"".join(canonical_json(row) for row in events)
    put(f"{service}/events.jsonl", journal)
    labels = {
        "ai.memecoin.brain.senses.gecko-capture-only-v6": "2" * 64,
        "ai.memecoin.brain.senses.gecko-canary-watchdog-v2-3569c27ff9916382c209ea71ef23e345": "3"
        * 64,
        "ai.memecoin.brain.senses.gecko-canary-watchdog-v1-c22205dad614fe5d00b6bbe7869a0c3b": "4"
        * 64,
    }
    prior = {
        "schema": "portability-dr-senses-prequiescence-v1",
        "activation": {
            **activation,
            "source_mismatch_count": 1,
            "source_mismatch_paths": ["src/memecoin_research_storage/__init__.py"],
        },
        "journal": {
            "sha256": audit._sha(journal),
            "kind_counts": audit.COUNTS,
            "unresolved_intents": 0,
        },
        "kingston": {"volume_uuid": volume, "root_id": root_id},
        "services": [{"label": label, "plist_sha256": digest} for label, digest in labels.items()],
    }
    prior_bytes = canonical_json(prior)
    quiescence = canonical_json(
        {
            "schema": "portability-dr-senses-quiescence-v1",
            "prequiescence_receipt_sha256": audit._sha(prior_bytes),
            "journal_sha256": audit._sha(journal),
            "disabled_labels": list(labels),
            "result": {
                "all_target_labels_absent_for_window": True,
                "all_target_labels_disabled": True,
                "journal_unchanged": True,
            },
        }
    )
    observation = {
        "schema": audit.OBSERVATION_SCHEMA,
        "observed_ns": 1,
        "labels": {label: {"disabled": True, "loaded": False, "pid": None} for label in labels},
        "active_callback_pids": 0,
        "active_callback_fds": 0,
        "plist_sha256": labels,
    }
    inputs = {
        "quiescence_raw": quiescence,
        "prequiescence_raw": prior_bytes,
        "pre_observation_raw": canonical_json(observation),
        "post_observation_raw": canonical_json({**observation, "observed_ns": 2}),
    }
    spec = {
        "schema": audit.SPEC_SCHEMA,
        "volume_uuid": volume,
        "root_id": root_id,
        "journal_sha256": audit._sha(journal),
        "quiescence_sha256": audit._sha(quiescence),
        "prequiescence_sha256": audit._sha(prior_bytes),
    }
    for directory, _dirs, _files in os.walk(data):
        Path(directory).chmod(0o700)

    class SyntheticGuard(ReadOnlyStorageGuard):
        def revalidate(self):
            return self.root

    guard = object.__new__(SyntheticGuard)
    guard.mount_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    guard.root_fd = os.open(data, os.O_RDONLY | os.O_DIRECTORY)
    guard.config = SimpleNamespace(data_root="BrainData")
    guard.root = SimpleNamespace(volume_uuid=volume, root_id=root_id)
    try:
        with DescriptorWriter.read_only_from_storage_guard(guard) as reader:
            yield SimpleNamespace(
                reader=reader,
                guard=guard,
                spec=spec,
                inputs=inputs,
                data=data,
                service=service,
                prior_path=prior_path,
                request_ids=request_ids,
            )
    finally:
        guard.close()


def _run_fixture(full_prefix, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        audit, "read_only_guard", lambda _path: contextlib.nullcontext(full_prefix.guard)
    )
    return audit.audit_terminal_prefix_read_only(
        Path("unused-synthetic-storage-config"), full_prefix.spec, **full_prefix.inputs
    )


def test_full_audit_hashes_opaque_bytes_and_exact_membership(
    full_prefix,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = {
        str(p): audit._sha(p.read_bytes()) for p in full_prefix.data.rglob("*") if p.is_file()
    }
    result = _run_fixture(full_prefix, monkeypatch)
    assert result["ok"] is True
    assert result["opaque_response_count"] == 4
    assert result["intent_count"] == result["outcome_count"] == result["confirmation_count"] == 5
    assert result["source_manifest_mismatch_unresolved"] is True
    assert result["runtime_absence_independently_verified"] is False
    assert result["runtime_observations_matching_external_claims_only"] is True
    assert result["runtime_observation_bracketing_verified"] is False
    assert result["locks_acquired"] is False
    assert result["global_K_qualified"] is result["T_qualified"] is result["erase_ready"] is False
    assert full_prefix.request_ids[0] not in json.dumps(result)
    assert before == {
        str(p): audit._sha(p.read_bytes()) for p in full_prefix.data.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize("mutation", ["extra", "raw_hash", "wrong_prior_root", "wrong_global_root"])
def test_full_audit_rejects_membership_payload_and_rooting_mutations(
    full_prefix,
    mutation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, service = full_prefix.data, full_prefix.service
    if mutation == "extra":
        (data / service / "unexpected-empty-directory").mkdir(mode=0o700)
    elif mutation == "raw_hash":
        (data / service / "responses" / f"{full_prefix.request_ids[0]}.bin").write_bytes(b"changed")
    elif mutation == "wrong_prior_root":
        source = data / service / full_prefix.prior_path
        target = data / full_prefix.prior_path
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    else:
        (data / audit.GLOBAL_LOCK).rename(data / service / ".wrong-global-lock")
    with pytest.raises(audit.PrefixAuditError, match="^prefix_audit_rejected$"):
        _run_fixture(full_prefix, monkeypatch)


def test_full_audit_rejects_held_file_replacement_before_final_check(
    full_prefix,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = audit._HeldFiles.check

    def replace_then_check(self):
        path = full_prefix.data / full_prefix.service / "events.jsonl"
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(path)
        original(self)

    monkeypatch.setattr(audit._HeldFiles, "check", replace_then_check)
    with pytest.raises(audit.PrefixAuditError):
        _run_fixture(full_prefix, monkeypatch)
