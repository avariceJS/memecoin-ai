from __future__ import annotations

import pytest

from tools import brain_senses_gecko_new_pool_discovery_host_v2 as host
from tools import brain_senses_gecko_new_pool_discovery_runtime_v2 as runtime
from tools import brain_senses_gecko_new_pool_discovery_v1 as discovery

NS = 1_000_000_000
SOURCE = "a" * 64
RELEASE = "b" * 32
RELEASE_SHA = "c" * 64
START = 1_800_000_000 * NS


def _policy(first_scan_ns: int, *, source: str = SOURCE) -> dict:
    return discovery.compile_policy(
        first_scan_ns=first_scan_ns,
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=1,
        sample_denominator=1,
        source_spec_sha256=source,
        max_scan_count=864,
    )


def _epoch(index: int, policy: dict, previous: dict | None = None, seal: dict | None = None):
    return runtime.epoch_envelope(
        release_id=RELEASE,
        lease_id=f"{index + 1:032x}",
        root_id=f"{index + 100:032x}",
        release_sha256=RELEASE_SHA,
        source_sha256=SOURCE,
        policy=policy,
        epoch_index=index,
        epoch_start_ns=START + index * runtime.EPOCH_NS,
        previous_epoch_sha256=seal["seal_sha256"] if seal else None,
        lifetime_requests_before=seal["lifetime_requests_after"] if seal else 0,
        lifetime_physical_before=seal["lifetime_physical_after"] if seal else 0,
        prior_allocated_physical=seal["allocated_physical_after"] if seal else 0,
        prior_pending_physical=seal["pending_physical_after"] if seal else 0,
    )


def _seal(epoch: dict, index: int) -> dict:
    requests = (index + 1) * 864 * 3
    return runtime.make_epoch_seal(
        epoch=epoch,
        scan_count=864,
        unresolved_count=0,
        terminal_replay_sha256=f"{index + 1:064x}",
        coordinator_prefix_sha256=f"{index + 1000:064x}",
        lifetime_requests_after=requests,
        lifetime_physical_after=0,
        allocated_physical_after=0,
        pending_physical_after=0,
        transport_quiescent=True,
        sealed_at_ns=epoch["epoch_end_ns"],
    )


def _history(count: int) -> list[dict]:
    result = []
    prior_epoch = None
    prior_seal = None
    policy = _policy(START)
    for index in range(count):
        if index:
            policy = discovery.compile_policy(
                first_scan_ns=START + index * runtime.EPOCH_NS,
                scan_interval_seconds=300,
                pages=3,
                page_spacing_seconds=12,
                sample_numerator=1,
                sample_denominator=1,
                source_spec_sha256=SOURCE,
                max_scan_count=864,
            )
        epoch = _epoch(index, policy, prior_epoch, prior_seal)
        seal = _seal(epoch, index) if index < count - 1 else None
        result.append({"policy": policy, "epoch": epoch, "seal": seal})
        prior_epoch, prior_seal = epoch, seal
    return result


def test_epoch_history_replays_per_epoch_policy_anchors_past_45_days():
    history = _history(17)
    replay = runtime.verify_epoch_history(history)
    assert replay["epoch_count"] == 17
    assert replay["current_epoch_index"] == 16
    assert replay["sealed"] is False
    assert len({entry["policy"]["policy_sha256"] for entry in history}) == 17
    assert len({entry["epoch"]["root_id"] for entry in history}) == 17
    assert len({entry["epoch"]["lease_id"] for entry in history}) == 17
    assert runtime.lifetime_alerts(
        start_ns=START,
        now_ns=START + 46 * 24 * 60 * 60 * NS,
        physical_bytes=0,
    ) == ["day45_diagnostic"]


def test_epoch_history_rejects_current_policy_substituted_for_prior_anchor():
    history = _history(3)
    history[0]["policy"] = history[-1]["policy"]
    with pytest.raises(runtime.RuntimeErrorV2, match="runtime_epoch_binding"):
        runtime.verify_epoch_history(history)


def test_epoch_history_rejects_source_or_policy_contract_change():
    history = _history(2)
    changed = _policy(START + runtime.EPOCH_NS, source="d" * 64)
    history[1]["policy"] = changed
    history[1]["epoch"] = runtime.epoch_envelope(
        release_id=RELEASE,
        lease_id="00000000000000000000000000000003",
        root_id="00000000000000000000000000000066",
        release_sha256=RELEASE_SHA,
        source_sha256="d" * 64,
        policy=changed,
        epoch_index=1,
        epoch_start_ns=START + runtime.EPOCH_NS,
        previous_epoch_sha256=history[0]["seal"]["seal_sha256"],
        lifetime_requests_before=2592,
        lifetime_physical_before=0,
        prior_allocated_physical=0,
        prior_pending_physical=0,
    )
    with pytest.raises(runtime.RuntimeErrorV2, match="runtime_epoch_history_link"):
        runtime.verify_epoch_history(history)


def test_epoch_history_rejects_unsealed_predecessor_and_skipped_epoch():
    history = _history(3)
    history[0]["seal"] = None
    with pytest.raises(runtime.RuntimeErrorV2, match="runtime_epoch_history_unsealed_predecessor"):
        runtime.verify_epoch_history(history)
    history = _history(3)
    history[2]["epoch"]["epoch_index"] = 4
    with pytest.raises(runtime.RuntimeErrorV2):
        runtime.verify_epoch_history(history)


def test_scan_plan_is_bound_to_epoch_namespace_and_path():
    history = _history(2)
    first, second = history
    plan0 = runtime.compile_epoch_scan_plan(
        policy=first["policy"],
        epoch=first["epoch"],
        scan_index=0,
        scheduled_ns=first["policy"]["first_scan_ns"],
    )
    plan1 = runtime.compile_epoch_scan_plan(
        policy=second["policy"],
        epoch=second["epoch"],
        scan_index=0,
        scheduled_ns=second["policy"]["first_scan_ns"],
    )
    assert plan0["scan_index"] == plan1["scan_index"] == 0
    assert plan0["plan_sha256"] != plan1["plan_sha256"]
    assert host._page_directory(0, 0) != host._page_directory(1, 0)
    assert runtime.verify_epoch_scan_plan(
        plan0, policy=first["policy"], epoch=first["epoch"]
    ) == discovery.compile_scan_plan(
        first["policy"], scan_index=0, scheduled_ns=first["policy"]["first_scan_ns"]
    )
    tampered = dict(plan0)
    tampered["epoch_index"] = 1
    with pytest.raises(runtime.RuntimeErrorV2, match="runtime_epoch_plan_binding"):
        runtime.verify_epoch_scan_plan(tampered, policy=first["policy"], epoch=first["epoch"])
