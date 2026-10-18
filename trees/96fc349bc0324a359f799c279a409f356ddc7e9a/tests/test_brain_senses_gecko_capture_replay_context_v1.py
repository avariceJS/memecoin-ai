from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "src"), str(REPO / "tools"), str(REPO / "tests")]

import test_brain_senses_gecko_capture_only_v2_handoff_v1 as handoff_fixture  # noqa: E402
import test_brain_senses_gecko_capture_only_v2_verify as clock_fixture  # noqa: E402
import test_brain_senses_gecko_scout_chain_v2 as chain_fixture  # noqa: E402

from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_gecko_capture_activation_v2 as activation_v2  # noqa: E402
from tools import brain_senses_gecko_capture_owner_entry_v2 as owner_entry  # noqa: E402
from tools import brain_senses_gecko_capture_replay_context_v1 as replay_context  # noqa: E402
from tools import brain_senses_gecko_scout_chain_v2 as chain_v2  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json  # noqa: E402


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _launchd_runner(
    record: dict[str, Any], *, extra_label: bool = False, extra_after_first_list: bool = False
):
    roles = (record["collector"], record["watchdog"])
    pids: dict[str, int | None] = {
        cast(str, roles[0]["label"]): os.getpid(),
        cast(str, roles[1]["label"]): None,
    }
    if extra_label:
        pids["ai.memecoin.brain.senses.gecko-capture-only-v9"] = 499
    list_calls = 0

    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal list_calls
        if argv == ["launchctl", "list"]:
            list_calls += 1
            rows = ["PID\tStatus\tLabel"]
            current_pids = dict(pids)
            if extra_after_first_list and list_calls > 1:
                current_pids["ai.memecoin.brain.senses.gecko-capture-only-v9"] = 499
            rows.extend(
                f"{pid if pid is not None else '-'}\t0\t{label}"
                for label, pid in current_pids.items()
            )
            return subprocess.CompletedProcess(argv, 0, "\n".join(rows) + "\n", "")
        if argv[:2] == ["launchctl", "print"]:
            label = argv[-1].rsplit("/", 1)[-1]
            role = next(row for row in roles if row["label"] == label)
            args = "\n".join(
                f"  {index} = {shlex.quote(value)}"
                for index, value in enumerate(role["program_arguments"])
            )
            pid_line = f" pid = {pids[label]}\n" if pids[label] is not None else ""
            printed = (
                f"{owner_entry.owner_host.LAUNCHD_DOMAIN}/{label} = {{\n"
                f" path = {role['loaded_path']}\n"
                f"{pid_line}"
                " arguments = {\n"
                f"{args}\n"
                " }\n}\n"
            )
            return subprocess.CompletedProcess(argv, 0, printed, "")
        if argv[:3] == ["ps", "-o", "uid="]:
            return subprocess.CompletedProcess(argv, 0, f"{os.getuid()}\n", "")
        raise AssertionError(f"unexpected launchd query: {argv}")

    return runner


def _write_coordinator(writer: DescriptorWriter, storage: dict[str, Any]) -> None:
    roots = {
        "helius": ("services/external-rolling-v5",),
        "alchemy": ("services/external-alchemy-v1",),
        "gecko": ("services/gecko-market-v1/capture-only-v2",),
        "coordinator": (capacity.COORDINATOR_ROOT,),
    }
    locks = {
        "helius": "locks/capacity/helius.lock",
        "alchemy": "locks/capacity/alchemy.lock",
        "gecko": "locks/capacity/gecko.lock",
    }
    root_binding = {key: storage[key] for key in ("root_id", "volume_uuid", "device", "inode")}
    policy_raw = capacity.canonical(capacity.policy_document(root_binding, locks, roots))
    writer.mkdir(capacity.COORDINATOR_ROOT)
    writer.create(f"{capacity.COORDINATOR_ROOT}/.lock", b"coordinator lock\n")
    writer.create(capacity.POLICY_PATH, policy_raw)
    genesis = capacity._event(
        seq=0,
        kind="genesis",
        at_ns=1,
        previous=None,
        fields={"policy_sha256": _sha(policy_raw)},
    )
    writer.create(capacity.LEDGER_PATH, capacity.canonical(genesis))


def _write_v2_root_context(writer: DescriptorWriter, root: str, inputs: dict[str, str]) -> None:
    input_dir = "control/replay-context-inputs/root-context"
    writer.mkdir(input_dir)
    refs_path = f"{input_dir}/certificate-input-refs.json"
    refs = {
        name: {"path": path, "sha256": _sha(writer.read_bytes(path))}
        for name, path in inputs.items()
    }
    writer.atomic_write(refs_path, canonical_json(refs), create_only=True)

    def ref(path: str) -> dict[str, str]:
        return {"path": path, "sha256": _sha(writer.read_bytes(path))}

    context = {
        "schema": replay_context.ROOT_CONTEXT_SCHEMA,
        "schema_version": 1,
        "root": root,
        "root_kind": "chain_v2",
        "inputs": {
            "binding": ref(f"{root}/binding.json"),
            "policy": ref(f"{root}/policy.json"),
            "certificate": ref(f"{root}/predecessor-cutover.json"),
            "certificate_input_refs": ref(refs_path),
        },
    }
    release_id = root.rsplit("/", 1)[-1]
    writer.atomic_write(
        f"{replay_context.ROOT_CONTEXT_DIRECTORY}/{release_id}.json",
        canonical_json(context),
        create_only=True,
    )


def _prepare(writer: DescriptorWriter, tmp_path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    inputs = chain_fixture._certificate_inputs(writer)
    _write_coordinator(writer, inputs["expected_handoff_genesis"]["storage"])
    certificate = chain_v2.build_cutover_certificate(**inputs)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer,
        root=chain_fixture.ROOT,
        policy=chain_fixture._policy(),
        certificate=certificate,
        certificate_inputs=inputs,
    )
    journal.initialize()

    activation_id = "b" * 32
    input_dir = f"control/replay-context-inputs/{activation_id}"
    writer.mkdir(input_dir)
    paths: dict[str, str] = {}
    for name, value in inputs.items():
        path = f"{input_dir}/{name}.bin"
        raw = value if name == "handoff_raw" else canonical_json(value)
        writer.atomic_write(path, raw, create_only=True)
        paths[name] = path
    _write_v2_root_context(writer, chain_fixture.ROOT, paths)
    context = replay_context.build_replay_context(
        writer,
        activation_id=activation_id,
        successor_root=chain_fixture.ROOT,
        certificate_sha256=certificate["certificate_sha256"],
        input_paths=paths,
    )
    return context, paths


@pytest.fixture
def writer(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    opened = DescriptorWriter(fd)
    try:
        yield opened
    finally:
        opened.close()
        os.close(fd)


def test_context_recomputes_certificate_from_pinned_descriptor_inputs(
    writer: DescriptorWriter, tmp_path: Path
) -> None:
    context, _paths = _prepare(writer, tmp_path)
    raw = canonical_json(context)
    result = replay_context.verify_replay_context(
        writer,
        context_raw=raw,
        expected_activation_id="b" * 32,
        expected_context_sha256=_sha(raw),
        expected_successor_root=chain_fixture.ROOT,
        expected_certificate_sha256=context["certificate_sha256"],
    )
    assert result["replayed_certificate"]["certificate_sha256"] == context["certificate_sha256"]
    assert result["registered_roots"][0]["root"] == chain_fixture.ROOT


def test_context_create_only_and_readback_are_idempotence_blocked(
    writer: DescriptorWriter, tmp_path: Path
) -> None:
    _context, paths = _prepare(writer, tmp_path)
    certificate = chain_v2.build_cutover_certificate(**chain_fixture._certificate_inputs(writer))
    raw, digest = replay_context.create_replay_context(
        writer,
        activation_id="d" * 32,
        successor_root=chain_fixture.ROOT,
        certificate_sha256=certificate["certificate_sha256"],
        input_paths=paths,
        revalidate_storage=lambda: None,
    )
    assert _sha(raw) == digest
    with pytest.raises(replay_context.ReplayContextError, match="replay_context_create_failed"):
        replay_context.create_replay_context(
            writer,
            activation_id="d" * 32,
            successor_root=chain_fixture.ROOT,
            certificate_sha256=certificate["certificate_sha256"],
            input_paths=paths,
            revalidate_storage=lambda: None,
        )


def test_context_rejects_historical_input_mutation(
    writer: DescriptorWriter, tmp_path: Path
) -> None:
    context, paths = _prepare(writer, tmp_path)
    changed = paths["predecessor_attempts"]
    writer.atomic_write(changed, canonical_json([]), create_only=False)
    raw = canonical_json(context)
    with pytest.raises(replay_context.ReplayContextError, match="input_digest_mismatch"):
        replay_context.verify_replay_context(
            writer,
            context_raw=raw,
            expected_activation_id="b" * 32,
            expected_context_sha256=_sha(raw),
            expected_successor_root=chain_fixture.ROOT,
            expected_certificate_sha256=context["certificate_sha256"],
        )


def test_context_rejects_new_unregistered_successor_root(
    writer: DescriptorWriter, tmp_path: Path
) -> None:
    context, _paths = _prepare(writer, tmp_path)
    writer.mkdir(f"services/gecko-market-v1/capture-only-v2/{'e' * 32}")
    raw = canonical_json(context)
    with pytest.raises(replay_context.ReplayContextError, match="root_metadata_missing"):
        replay_context.verify_replay_context(
            writer,
            context_raw=raw,
            expected_activation_id="b" * 32,
            expected_context_sha256=_sha(raw),
            expected_successor_root=chain_fixture.ROOT,
            expected_certificate_sha256=context["certificate_sha256"],
        )


def test_context_rejects_swapped_or_malformed_reference_set(
    writer: DescriptorWriter, tmp_path: Path
) -> None:
    context, _paths = _prepare(writer, tmp_path)
    changed = dict(context)
    changed["certificate_inputs"] = dict(context["certificate_inputs"])
    changed["certificate_inputs"]["policy"] = context["certificate_inputs"]["predecessor_slots"]
    core = dict(changed)
    core.pop("context_sha256")
    changed["context_sha256"] = _sha(canonical_json(core))
    raw = canonical_json(changed)
    with pytest.raises(replay_context.ReplayContextError, match="duplicate_input_path"):
        replay_context.verify_replay_context(
            writer,
            context_raw=raw,
            expected_activation_id="b" * 32,
            expected_context_sha256=_sha(raw),
            expected_successor_root=chain_fixture.ROOT,
            expected_certificate_sha256=context["certificate_sha256"],
        )


def test_activation_v2_pins_context_and_preserves_v1_launch_gate(
    writer: DescriptorWriter, tmp_path: Path
) -> None:
    # Reuse the v1 test fixture for an isolated clean Git source tree and storage guard.
    from test_brain_senses_gecko_capture_activation_v1 import ActivationRecordTests

    fixture = ActivationRecordTests(methodName="test_create_only_record_and_restart_gate")
    fixture.setUp()
    try:
        active_writer = fixture.writer
        inputs = chain_fixture._certificate_inputs(active_writer)
        input_genesis = inputs["expected_handoff_genesis"]
        input_genesis["storage"] = {
            key: fixture.storage[key] for key in ("root_id", "volume_uuid", "device", "inode")
        }
        _write_coordinator(active_writer, input_genesis["storage"])
        inputs["handoff_raw"] = handoff_fixture.handoff.make_journal(
            input_genesis,
            handoff_fixture._partial_fields(),
            handoff_fixture._gap_fields(handoff_fixture.handoff.UNPLANNED_GAP_CLOSE_NS),
        )
        certificate = chain_v2.build_cutover_certificate(**inputs)
        journal = chain_v2.DescriptorScoutJournalV2(
            active_writer,
            root=chain_fixture.ROOT,
            policy=chain_fixture._policy(),
            certificate=certificate,
            certificate_inputs=inputs,
        )
        journal.initialize()
        paths: dict[str, str] = {}
        input_dir = f"control/replay-context-inputs/{fixture.activation_id}"
        active_writer.mkdir(input_dir)
        for name, value in inputs.items():
            path = f"{input_dir}/{name}.bin"
            active_writer.atomic_write(
                path,
                value if name == "handoff_raw" else canonical_json(value),
                create_only=True,
            )
            paths[name] = path
        _write_v2_root_context(active_writer, chain_fixture.ROOT, paths)
        context = replay_context.build_replay_context(
            active_writer,
            activation_id=fixture.activation_id,
            successor_root=chain_fixture.ROOT,
            certificate_sha256=certificate["certificate_sha256"],
            input_paths=paths,
        )
        context_raw = canonical_json(context)
        context_path = replay_context.context_path(fixture.activation_id)
        active_writer.atomic_write(context_path, context_raw, create_only=True)

        policy = chain_fixture._policy()
        fixture.binding = journal.binding
        source_map = fixture.source_map
        collector_loaded = (tmp_path / "loaded-collector.plist").resolve()
        watchdog_loaded = (tmp_path / "loaded-watchdog.plist").resolve()
        collector_loaded.write_bytes(fixture.collector_plist)
        watchdog_loaded.write_bytes(fixture.watchdog_plist)
        os.chmod(collector_loaded, 0o600)
        os.chmod(watchdog_loaded, 0o600)
        record = activation_v2.prepare_activation_record_v2(
            activation_id=fixture.activation_id,
            phase="continuous",
            source_commit=fixture.commit,
            source_manifest_raw=fixture.manifest_raw,
            source_manifest_sha256=fixture.manifest_sha,
            source_map=source_map,
            repo_root=fixture.repo,
            service_root=chain_fixture.ROOT,
            binding=journal.binding,
            certificate_sha256=certificate["certificate_sha256"],
            policy_sha256=policy["policy_sha256"],
            population_sha256=policy["population_sha256"],
            plan_sha256="1" * 64,
            storage=fixture.storage,
            data_root_fd=fixture.data_fd,
            writer=active_writer,
            storage_guard=fixture.storage_guard,
            collector_plist_raw=fixture.collector_plist,
            collector_plist_path="ops/collector.plist",
            collector_loaded_path=str(collector_loaded),
            collector_label=fixture.collector_label,
            collector_arguments=fixture.collector_args,
            watchdog_plist_raw=fixture.watchdog_plist,
            watchdog_plist_path="ops/watchdog.plist",
            watchdog_loaded_path=str(watchdog_loaded),
            watchdog_label=fixture.watchdog_label,
            watchdog_arguments=fixture.watchdog_args,
            predecessor_certificate_sha256="2" * 64,
            supervisor_certificate_sha256="3" * 64,
            revalidate_storage=lambda: None,
            replay_context_raw=context_raw,
            replay_context_sha256=_sha(context_raw),
            replay_context_path=context_path,
            replay_observed_now_ns=replay_context.HISTORICAL_CUTOVER_NS,
        )
        record_hash = activation_v2.create_activation_record_v2(
            writer=active_writer,
            record=record,
            data_root_fd=fixture.data_fd,
            revalidate_storage=lambda: None,
        )
        assert record_hash == _sha(canonical_json(record))
        wall_ns = replay_context.HISTORICAL_CUTOVER_NS + 1_000_000_000
        mono_ns = 30_000_000_000
        clock_record, _ = clock_fixture._clock(wall_ns, mono_ns)
        verified = owner_entry.verify_live_activation_v2(
            writer=active_writer,
            activation_id=fixture.activation_id,
            repo_root=fixture.repo,
            storage_guard=fixture.storage_guard,
            data_root_fd=fixture.data_fd,
            revalidate_storage=lambda: None,
            clock_record=clock_record,
            wall_ns=wall_ns,
            monotonic_ns=mono_ns,
            launchd_runner=_launchd_runner(record),
        )
        assert verified.record_sha256 == record_hash
        assert (
            verified.replayed_certificate["certificate_sha256"] == certificate["certificate_sha256"]
        )
        # The cutover is historical and immutable, while restart inventory/clock
        # validation uses this callback's fresh wall/monotonic sample.
        assert inputs["observed_now_ns"] == replay_context.HISTORICAL_CUTOVER_NS
        assert wall_ns > inputs["observed_now_ns"]
        assert active_writer.read_bytes(context_path) == context_raw
        _root_refs, _handoffs, fresh_chains = replay_context._load_root_contexts(
            active_writer,
            context["registered_roots"],
            observed_now_ns=wall_ns,
        )
        assert fresh_chains[chain_fixture.ROOT]["observed_now_ns"] == wall_ns
        historical_ref = verified.record["replay_context_sha256"]
        assert historical_ref == _sha(context_raw)

        with pytest.raises(owner_entry.OwnerEntryError, match="loaded_label_set_mismatch"):
            owner_entry.verify_live_activation_v2(
                writer=active_writer,
                activation_id=fixture.activation_id,
                repo_root=fixture.repo,
                storage_guard=fixture.storage_guard,
                data_root_fd=fixture.data_fd,
                revalidate_storage=lambda: None,
                clock_record=clock_record,
                wall_ns=wall_ns,
                monotonic_ns=mono_ns,
                launchd_runner=_launchd_runner(record, extra_label=True),
            )

        with pytest.raises(owner_entry.OwnerEntryError, match="loaded_label_set_mismatch"):
            owner_entry.verify_live_activation_v2(
                writer=active_writer,
                activation_id=fixture.activation_id,
                repo_root=fixture.repo,
                storage_guard=fixture.storage_guard,
                data_root_fd=fixture.data_fd,
                revalidate_storage=lambda: None,
                clock_record=clock_record,
                wall_ns=wall_ns,
                monotonic_ns=mono_ns,
                launchd_runner=_launchd_runner(record, extra_after_first_list=True),
            )

        with pytest.raises(owner_entry.OwnerEntryError, match="collector_pid_mismatch"):
            wrong_pid_runner = _launchd_runner(record)
            original_runner = wrong_pid_runner

            def mismatched_pid(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                if argv == ["launchctl", "list"]:
                    result = original_runner(argv, **kwargs)
                    result.stdout = result.stdout.replace(
                        f"{os.getpid()}\t0\t{record['collector']['label']}",
                        f"{os.getpid() + 1}\t0\t{record['collector']['label']}",
                    )
                    return result
                return original_runner(argv, **kwargs)

            owner_entry.verify_live_activation_v2(
                writer=active_writer,
                activation_id=fixture.activation_id,
                repo_root=fixture.repo,
                storage_guard=fixture.storage_guard,
                data_root_fd=fixture.data_fd,
                revalidate_storage=lambda: None,
                clock_record=clock_record,
                wall_ns=wall_ns,
                monotonic_ns=mono_ns,
                launchd_runner=mismatched_pid,
            )

        changed = dict(record)
        changed["replay_context_sha256"] = "0" * 64
        with pytest.raises(activation_v2.ActivationV2Error, match="record_binding_mismatch"):
            activation_v2.validate_activation_record_v2(
                record_raw=canonical_json(changed),
                expected_activation_id=fixture.activation_id,
                replay_context_raw=context_raw,
                observed_now_ns=replay_context.HISTORICAL_CUTOVER_NS,
                source_manifest_raw=fixture.manifest_raw,
                repo_root=fixture.repo,
                data_root_fd=fixture.data_fd,
                writer=active_writer,
                storage_guard=fixture.storage_guard,
                revalidate_storage=lambda: None,
                collector_plist_raw=fixture.collector_plist,
                watchdog_plist_raw=fixture.watchdog_plist,
                launch_snapshot={"loaded_services": []},
            )
    finally:
        fixture.tearDown()
