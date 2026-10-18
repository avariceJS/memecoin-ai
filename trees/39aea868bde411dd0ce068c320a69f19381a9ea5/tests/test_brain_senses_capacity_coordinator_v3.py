from __future__ import annotations

import hashlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools import brain_senses_capacity_coordinator_v2 as v2
from tools import brain_senses_capacity_coordinator_v3 as v3

ROOT_BINDING = {
    "root_id": "a" * 32,
    "volume_uuid": "12345678-1234-1234-1234-123456789abc",
    "device": 7,
    "inode": 99,
}
SERVICE_ROOTS = {
    "helius": "services/helius-v5",
    "alchemy": "services/alchemy-sentinel-v2",
    "gecko": "services/gecko-market-v1",
}
LOCKS = {
    "helius": ".external-rolling-v4.lock",
    "alchemy": ".alchemy-sentinel-v2.lock",
    "gecko": ".gecko-market-v1.lock",
}
ALLOCATION_ROOTS = {
    "helius": ("services/helius-v4", SERVICE_ROOTS["helius"]),
    "alchemy": (SERVICE_ROOTS["alchemy"],),
    "gecko": (SERVICE_ROOTS["gecko"],),
    "coordinator": (v2.COORDINATOR_ROOT,),
}
H = "a" * 64
OUTCOME_SHA = "b" * 64


def _v2_fixture(*, pending=True, ambiguous=True):
    policy = v2.canonical(v2.policy_document(ROOT_BINDING, LOCKS, ALLOCATION_ROOTS))
    events = [
        v2._event(
            seq=0,
            kind="genesis",
            at_ns=1_900_000_000_000_000_000,
            previous=None,
            fields={"policy_sha256": v2.sha256(policy)},
        )
    ]
    rows = [
        ("helius", "1" * 32, "settled-q", SERVICE_ROOTS["helius"]),
        ("alchemy", "2" * 32, "pending-q", SERVICE_ROOTS["alchemy"]),
        ("gecko", "3" * 32, "ambiguous-q", SERVICE_ROOTS["gecko"]),
    ]
    for service, rid, qid, root in rows:
        fields = {
            "reservation_id": rid,
            "service": service,
            "request_id": qid,
            "intent_path": f"{root}/intents/{rid}.json",
            "outcome_path": f"{root}/receipts/{rid}.json",
            "intent_sha256": H,
            "reserved_bytes": 1000,
            "intent_bytes_sha256": H,
            "allocated_before_bytes": 0,
        }
        events.append(
            v2._event(
                seq=len(events),
                kind="reserved",
                at_ns=1_900_000_000_000_000_000 + len(events),
                previous=events[-1]["event_sha256"],
                fields=fields,
            )
        )
        if service == "helius":
            terminal_fields = {
                "reservation_id": rid,
                "outcome": "success",
                "outcome_sha256": OUTCOME_SHA,
                "outcome_path": fields["outcome_path"],
                "actual_allocated_bytes": 1200,
            }
            events.append(
                v2._event(
                    seq=len(events),
                    kind="settled",
                    at_ns=1_900_000_000_000_000_000 + len(events),
                    previous=events[-1]["event_sha256"],
                    fields=terminal_fields,
                )
            )
        if service == "gecko" and ambiguous:
            terminal_fields = {
                "reservation_id": rid,
                "outcome": "uncertain",
                "outcome_sha256": OUTCOME_SHA,
                "actual_allocated_bytes": 500,
            }
            events.append(
                v2._event(
                    seq=len(events),
                    kind="ambiguous_terminal",
                    at_ns=1_900_000_000_000_000_000 + len(events),
                    previous=events[-1]["event_sha256"],
                    fields=terminal_fields,
                )
            )
    ledger = b"".join(v2.canonical(event) for event in events)
    roots = sorted(path for group in ALLOCATION_ROOTS.values() for path in group)
    inventory = {path: 0 for path in roots}
    if pending is False:
        # The pending request is represented by an explicit pre-intent release.
        rid = "2" * 32
        event = v2._event(
            seq=len(events),
            kind="released",
            at_ns=1_900_000_000_000_000_000 + len(events),
            previous=events[-1]["event_sha256"],
            fields={
                "reservation_id": rid,
                "outcome": "pre_intent_absent",
                "outcome_sha256": v2.sha256(b"pre_intent_absent"),
            },
        )
        ledger += v2.canonical(event)
    artifacts = {
        f"{SERVICE_ROOTS['helius']}/intents/{'1' * 32}.json": H,
        f"{SERVICE_ROOTS['helius']}/receipts/{'1' * 32}.json": OUTCOME_SHA,
        f"{SERVICE_ROOTS['alchemy']}/intents/{'2' * 32}.json": H,
        f"{SERVICE_ROOTS['gecko']}/intents/{'3' * 32}.json": H,
    }
    return policy, ledger, inventory, artifacts


def _bootstrap():
    p2, ledger2, inv2, artifacts = _v2_fixture()
    bridge = v3.build_v2_bridge(
        v2_policy_bytes=p2,
        v2_ledger_bytes=ledger2,
        v2_root_inventory=inv2,
        artifact_sha256_by_path=artifacts,
    )
    docs = v3.migration_documents(
        v2_policy_bytes=p2,
        v2_ledger_bytes=ledger2,
        v2_root_inventory=inv2,
        artifact_sha256_by_path=artifacts,
        bridge=bridge,
        root_binding=ROOT_BINDING,
        cutover_ns=1_900_000_000_100_000_000,
        migration_id="f" * 32,
    )
    return p2, ledger2, inv2, artifacts, bridge, docs


def _all_root_inventory(docs, bridge, *, discovery=0, coordinator_v3=0):
    policy = v3._decode(docs["policy"])
    roots = sorted(path for group in policy["allocation_roots"].values() for path in group)
    inventory = {path: bridge["v2_root_inventory"].get(path, 0) for path in roots}
    inventory[v3.DISCOVERY_ROOT] = discovery
    inventory[v3.COORDINATOR_ROOT] = coordinator_v3
    return inventory


def _hold_open(ledger, docs, bridge, *, hold_id="9" * 32, at_ns=1_900_000_000_200_000_000):
    hold_bytes = v3.DISCOVERY_EPOCH_HOLD_BYTES
    return v3.append_epoch_hold_open(
        ledger_bytes=ledger,
        policy_bytes=docs["policy"],
        pointer_bytes=docs["pointer"],
        bridge=bridge,
        v2_policy_bytes=_v2_fixture()[0],
        current_v2_ledger_bytes=_v2_fixture()[1],
        current_v2_root_inventory=_v2_fixture()[2],
        v2_artifact_sha256_by_path=_v2_fixture()[3],
        current_root_inventory=_all_root_inventory(docs, bridge),
        free_bytes=200 * 1024**3,
        at_ns=at_ns,
        hold_id=hold_id,
        successor_epoch_id="8" * 32,
        standby_sha256=H,
        intent_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/epochs/000001/standby.json",
        outcome_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/outcomes/epoch-hold-{'9' * 32}.json",
        intent_sha256=OUTCOME_SHA,
        reserved_bytes=hold_bytes,
        page_request_ids=[
            hashlib.sha256(f"page-{index}".encode()).hexdigest()
            for index in range(v3.DISCOVERY_EPOCH_PAGE_COUNT)
        ],
    )


def _reserve_args(
    boot, *, reservation_id="9" * 32, request_id="new-q", reserved=5000, free=250_000_000_000
):
    p2, ledger2, inv2, artifacts, bridge, docs = boot
    return dict(
        ledger_bytes=docs["ledger"],
        policy_bytes=docs["policy"],
        pointer_bytes=docs["pointer"],
        bridge=bridge,
        v2_policy_bytes=p2,
        current_v2_ledger_bytes=ledger2,
        current_v2_root_inventory=inv2,
        v2_artifact_sha256_by_path=artifacts,
        current_root_inventory=_all_root_inventory(docs, bridge),
        free_bytes=free,
        at_ns=1_900_000_000_200_000_000,
        service="gecko_discovery",
        reservation_id=reservation_id,
        request_id=request_id,
        intent_path=f"{v3.DISCOVERY_ROOT}/intents/{request_id}.json",
        outcome_path=f"{v3.DISCOVERY_ROOT}/outcomes/{request_id}.json",
        intent_sha256=H,
        reserve_bytes=reserved,
    )


def test_v2_bridge_carries_tail_roots_request_ids_and_conservative_liabilities():
    p2, ledger2, inv2, artifacts, bridge, _docs = _bootstrap()
    events = v2._verify_ledger(ledger2)
    assert bridge["v2_policy_sha256"] == v2.sha256(p2)
    assert bridge["v2_ledger_sha256"] == v2.sha256(ledger2)
    assert bridge["v2_tail_sha256"] == events[-1]["event_sha256"]
    assert bridge["v2_root_inventory"] == inv2
    assert len(bridge["v2_reservation_ids"]) == 3
    assert len(bridge["v2_request_ids"]) == 3
    assert {row["state"] for row in bridge["pending_liabilities"]} == {
        "pending",
        "ambiguous_full_reserve",
    }
    assert bridge["pending_liability_bytes"] == 2000
    assert (
        v3.verify_v2_prefix(
            bridge,
            v2_policy_bytes=p2,
            v2_ledger_bytes=ledger2,
            v2_root_inventory=inv2,
            artifact_sha256_by_path=artifacts,
        )
        == bridge
    )


def test_migration_documents_cutover_pointer_last_and_exact_partial_recovery():
    _p2, _ledger2, _inv2, _artifacts, bridge, docs = _bootstrap()
    empty = v3.reconcile_migration_prefix(docs)
    assert [row["path"] for row in empty["writes"]] == [
        v3.POLICY_PATH,
        v3.LEDGER_PATH,
        v3.POINTER_PATH,
    ]
    assert empty["activation_ready"] is False
    partial = v3.reconcile_migration_prefix(docs, existing_policy=docs["policy"])
    assert [row["path"] for row in partial["writes"]] == [v3.LEDGER_PATH, v3.POINTER_PATH]
    before_pointer = v3.reconcile_migration_prefix(
        docs, existing_policy=docs["policy"], existing_ledger=docs["ledger"]
    )
    assert [row["path"] for row in before_pointer["writes"]] == [v3.POINTER_PATH]
    complete = v3.reconcile_migration_prefix(
        docs,
        existing_policy=docs["policy"],
        existing_ledger=docs["ledger"],
        existing_pointer=docs["pointer"],
    )
    assert complete["writes"] == []
    assert complete["activation_ready"] is True
    events = v3.verify_pointer_and_ledger(
        pointer_bytes=docs["pointer"],
        ledger_bytes=docs["ledger"],
        policy_bytes=docs["policy"],
        bridge=bridge,
    )
    assert len(events) == 1 and events[0]["kind"] == "genesis"
    with pytest.raises(v3.CoordinatorV3Error, match="create_only_conflict"):
        v3.reconcile_migration_prefix(docs, existing_policy=b"{}\n")
    with pytest.raises(v3.CoordinatorV3Error, match="pointer_before_genesis"):
        v3.reconcile_migration_prefix(docs, existing_pointer=docs["pointer"])


def test_v2_torn_or_post_cutover_append_and_artifact_drift_stop():
    p2, ledger2, inv2, artifacts, bridge, docs = _bootstrap()
    with pytest.raises(v3.CoordinatorV3Error, match="v2_ledger_replay_failed"):
        v3.build_v2_bridge(
            v2_policy_bytes=p2,
            v2_ledger_bytes=ledger2[:-1],
            v2_root_inventory=inv2,
            artifact_sha256_by_path=artifacts,
        )
    events = v2._verify_ledger(ledger2)
    suffix = v2._event(
        seq=len(events),
        kind="reserved",
        at_ns=1_900_000_000_300_000_000,
        previous=events[-1]["event_sha256"],
        fields={
            "reservation_id": "e" * 32,
            "service": "alchemy",
            "request_id": "post-cutover",
            "intent_path": f"{SERVICE_ROOTS['alchemy']}/intents/post.json",
            "outcome_path": f"{SERVICE_ROOTS['alchemy']}/receipts/post.json",
            "intent_sha256": H,
            "reserved_bytes": 10,
            "intent_bytes_sha256": H,
            "allocated_before_bytes": 0,
        },
    )
    appended = ledger2 + v2.canonical(suffix)
    with pytest.raises(v3.CoordinatorV3Error, match="frozen_prefix_drift"):
        v3.verify_v2_prefix(
            bridge,
            v2_policy_bytes=p2,
            v2_ledger_bytes=appended,
            v2_root_inventory=inv2,
            artifact_sha256_by_path={
                **artifacts,
                f"{SERVICE_ROOTS['alchemy']}/intents/post.json": H,
            },
        )
    wrong_artifacts = {**artifacts, f"{SERVICE_ROOTS['helius']}/intents/{'1' * 32}.json": "c" * 64}
    with pytest.raises(v3.CoordinatorV3Error, match="intent_artifact_mismatch"):
        v3.build_v2_bridge(
            v2_policy_bytes=p2,
            v2_ledger_bytes=ledger2,
            v2_root_inventory=inv2,
            artifact_sha256_by_path=wrong_artifacts,
        )


def test_v3_reservation_uses_v2_global_prefix_dedup_and_shared_free_floor():
    boot = _bootstrap()
    next_ledger = v3.append_reservation(**_reserve_args(boot))
    events = v3.verify_v3_ledger(
        next_ledger,
        expected_policy_bytes=boot[-1]["policy"],
        bridge=boot[-2],
    )
    assert [row["kind"] for row in events] == ["genesis", "reserved"]
    duplicate = _reserve_args(boot, reservation_id="4" * 32, request_id="settled-q")
    with pytest.raises(v3.CoordinatorV3Error, match="request_id_reused"):
        v3.append_reservation(**duplicate)
    # A migrated reservation ID is permanently consumed even when settled.
    duplicate_reservation = _reserve_args(boot, reservation_id="1" * 32, request_id="new-q")
    with pytest.raises(v3.CoordinatorV3Error, match="reservation_id_reused"):
        v3.append_reservation(**duplicate_reservation)
    low_free = _reserve_args(boot, reservation_id="a" * 32, request_id="low-free", free=1)
    with pytest.raises(v3.CoordinatorV3Error, match="combined_free_floor"):
        v3.append_reservation(**low_free)


def test_v3_service_cap_and_ambiguous_terminal_keep_full_reserve():
    boot = _bootstrap()
    args = _reserve_args(boot, reservation_id="4" * 32, request_id="amb-q", reserved=1_000_000)
    ledger = v3.append_reservation(**args)
    ledger = v3.append_terminal(
        ledger_bytes=ledger,
        policy_bytes=boot[-1]["policy"],
        bridge=boot[-2],
        at_ns=args["at_ns"] + 1,
        reservation_id="4" * 32,
        outcome="possibly_spent",
        outcome_sha256=H,
        actual_allocated_bytes=0,
        kind="ambiguous_terminal",
    )
    # An ambiguous terminal is terminal to the request but its liability remains reserved.
    low_free = _reserve_args(boot, reservation_id="5" * 32, request_id="next-q", reserved=1)
    low_free["ledger_bytes"] = ledger
    low_free["free_bytes"] = v3.FIXED_FREE_FLOOR_BYTES + 1 + v3.COORDINATOR_APPEND_RESERVE_BYTES
    low_free["at_ns"] += 2
    with pytest.raises(v3.CoordinatorV3Error, match="combined_free_floor"):
        v3.append_reservation(**low_free)
    over_cap = _reserve_args(
        boot,
        reservation_id="6" * 32,
        request_id="cap-q",
        reserved=v3.SERVICE_CAPS["gecko_discovery"] + 1,
    )
    with pytest.raises(v3.CoordinatorV3Error):
        v3.append_reservation(**over_cap)


def test_two_cross_lane_reservations_are_serializable_and_unique():
    boot = _bootstrap()
    first = v3.append_reservation(
        **_reserve_args(boot, reservation_id="7" * 32, request_id="g-discovery")
    )
    args = _reserve_args(boot, reservation_id="8" * 32, request_id="alchemy-new")
    args["service"] = "alchemy"
    args["intent_path"] = f"{SERVICE_ROOTS['alchemy']}/intents/alchemy-new.json"
    args["outcome_path"] = f"{SERVICE_ROOTS['alchemy']}/outcomes/alchemy-new.json"
    args["ledger_bytes"] = first
    args["at_ns"] += 1
    second = v3.append_reservation(**args)
    events = v3.verify_v3_ledger(second, expected_policy_bytes=boot[-1]["policy"], bridge=boot[-2])
    assert [event["fields"].get("request_id") for event in events[1:]] == [
        "g-discovery",
        "alchemy-new",
    ]


def test_epoch_hold_converts_runway_to_ordered_page_reservations_and_closes():
    boot = _bootstrap()
    _p2, _ledger2, _inv2, _artifacts, bridge, docs = boot
    ledger = _hold_open(docs["ledger"], docs, bridge)
    base = 1_900_000_000_200_000_100

    page_ids = [
        hashlib.sha256(f"page-{index}".encode()).hexdigest()
        for index in range(v3.DISCOVERY_EPOCH_PAGE_COUNT)
    ]

    def convert(current: bytes, *, request_id: str, reservation_id: str, index: int) -> bytes:
        return v3.append_page_reserved_from_hold(
            ledger_bytes=current,
            policy_bytes=docs["policy"],
            pointer_bytes=docs["pointer"],
            bridge=bridge,
            at_ns=base + index,
            hold_id="9" * 32,
            standby_sha256=H,
            reservation_id=reservation_id,
            request_id=request_id,
            page_ordinal=index + 1,
            intent_path=(
                f"{v3.DISCOVERY_ROOT}/runtime-v2/epochs/000001/scans/0000/"
                f"page-{index + 1:02d}.intent.json"
            ),
            outcome_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/outcomes/{reservation_id}.json",
            intent_sha256=OUTCOME_SHA,
            reserved_bytes=2_621_440,
            allocated_before_bytes=0,
        )

    with pytest.raises(v3.CoordinatorV3Error, match="hold_page_not_available|hold_page_conversion"):
        convert(ledger, request_id="f" * 64, reservation_id="1" * 32, index=0)
    ledger = convert(ledger, request_id=page_ids[0], reservation_id="4" * 32, index=0)
    # A durable reservation is part of the held budget but cannot be closed
    # until its terminal is appended and replayable.
    with pytest.raises(v3.CoordinatorV3Error, match="epoch_hold_close_binding"):
        v3.append_epoch_hold_close(
            ledger_bytes=ledger,
            policy_bytes=docs["policy"],
            pointer_bytes=docs["pointer"],
            bridge=bridge,
            at_ns=base + 1,
            hold_id="9" * 32,
            standby_sha256=H,
            remaining_bytes=v3.DISCOVERY_EPOCH_HOLD_BYTES - v3.DISCOVERY_PAGE_RESERVE_BYTES,
            outcome_sha256=OUTCOME_SHA,
        )
    with pytest.raises(v3.CoordinatorV3Error, match="hold_page_not_available|hold_page_conversion"):
        convert(ledger, request_id=page_ids[0], reservation_id="6" * 32, index=1)
    ledger = convert(ledger, request_id=page_ids[1], reservation_id="5" * 32, index=1)
    for at_ns, reservation_id in ((base + 2, "4" * 32), (base + 3, "5" * 32)):
        ledger = v3.append_terminal(
            ledger_bytes=ledger,
            policy_bytes=docs["policy"],
            bridge=bridge,
            at_ns=at_ns,
            reservation_id=reservation_id,
            outcome="success",
            outcome_sha256=OUTCOME_SHA,
            actual_allocated_bytes=1,
            kind="settled",
        )
    closed = v3.append_epoch_hold_close(
        ledger_bytes=ledger,
        policy_bytes=docs["policy"],
        pointer_bytes=docs["pointer"],
        bridge=bridge,
        at_ns=base + 4,
        hold_id="9" * 32,
        standby_sha256=H,
        remaining_bytes=v3.DISCOVERY_EPOCH_HOLD_BYTES - 2 * v3.DISCOVERY_PAGE_RESERVE_BYTES,
        outcome_sha256=OUTCOME_SHA,
    )
    events = v3.verify_v3_ledger(closed, expected_policy_bytes=docs["policy"], bridge=bridge)
    assert [event["kind"] for event in events[-6:]] == [
        "epoch_hold_open",
        "page_reserved_from_hold",
        "page_reserved_from_hold",
        "settled",
        "settled",
        "epoch_hold_close",
    ]
    assert events[-6]["fields"]["reserved_bytes"] == v3.DISCOVERY_EPOCH_HOLD_BYTES
    assert events[-1]["fields"]["remaining_bytes"] == (
        v3.DISCOVERY_EPOCH_HOLD_BYTES - 2 * v3.DISCOVERY_PAGE_RESERVE_BYTES
    )
    with pytest.raises(v3.CoordinatorV3Error, match="epoch_hold_binding"):
        _hold_open(closed, docs, bridge, hold_id="7" * 32, at_ns=base + 5)


def test_epoch_hold_ordinals_allow_missed_prefix_but_refuse_late_backfill():
    boot = _bootstrap()
    _p2, _ledger2, _inv2, _artifacts, bridge, docs = boot
    ledger = _hold_open(docs["ledger"], docs, bridge)
    page_ids = [hashlib.sha256(f"page-{index}".encode()).hexdigest() for index in range(3)]
    base = 1_900_000_000_250_000_000

    def reserve(current, *, ordinal, at_ns, reservation_id):
        return v3.append_page_reserved_from_hold(
            ledger_bytes=current,
            policy_bytes=docs["policy"],
            pointer_bytes=docs["pointer"],
            bridge=bridge,
            at_ns=at_ns,
            hold_id="9" * 32,
            standby_sha256=H,
            reservation_id=reservation_id,
            request_id=page_ids[ordinal - 1],
            page_ordinal=ordinal,
            intent_path=(
                f"{v3.DISCOVERY_ROOT}/runtime-v2/epochs/000000/scans/0000/"
                f"page-{ordinal:02d}.intent.json"
            ),
            outcome_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/outcomes/{reservation_id}.json",
            intent_sha256=OUTCOME_SHA,
            reserved_bytes=v3.DISCOVERY_PAGE_RESERVE_BYTES,
            allocated_before_bytes=0,
        )

    # The discovery schedule records the missed first slot separately. The
    # capacity ledger permits a later ordinal, while permanently preventing
    # that skipped page from being reserved afterward.
    ledger = reserve(ledger, ordinal=2, at_ns=base + 1, reservation_id="c" * 32)
    with pytest.raises(v3.CoordinatorV3Error, match="hold_page_ordinal|hold_page_conversion"):
        reserve(ledger, ordinal=1, at_ns=base + 2, reservation_id="d" * 32)
    with pytest.raises(v3.CoordinatorV3Error, match="hold_page_not_available|hold_page_conversion"):
        reserve(ledger, ordinal=2, at_ns=base + 2, reservation_id="e" * 32)


def test_epoch_hold_ordinal_must_bind_to_matching_request_identity():
    boot = _bootstrap()
    _p2, _ledger2, _inv2, _artifacts, bridge, docs = boot
    ledger = _hold_open(docs["ledger"], docs, bridge)
    page_ids = [hashlib.sha256(f"page-{index}".encode()).hexdigest() for index in range(3)]
    with pytest.raises(v3.CoordinatorV3Error, match="hold_page_ordinal|hold_page_conversion"):
        v3.append_page_reserved_from_hold(
            ledger_bytes=ledger,
            policy_bytes=docs["policy"],
            pointer_bytes=docs["pointer"],
            bridge=bridge,
            at_ns=1_900_000_000_260_000_000,
            hold_id="9" * 32,
            standby_sha256=H,
            reservation_id="1" * 32,
            request_id=page_ids[1],
            page_ordinal=1,
            intent_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/epochs/000000/scans/0000/page-02.intent.json",
            outcome_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/outcomes/{'1' * 32}.json",
            intent_sha256=OUTCOME_SHA,
            reserved_bytes=v3.DISCOVERY_PAGE_RESERVE_BYTES,
            allocated_before_bytes=0,
        )


@settings(max_examples=12, deadline=None)
@given(st.lists(st.sampled_from(["success", "ambiguous", "absent", "crash"]), max_size=5))
def test_epoch_hold_conservation_and_replay_at_every_crash_prefix(outcomes):
    boot = _bootstrap()
    _p2, _ledger2, _inv2, _artifacts, bridge, docs = boot
    ledger = _hold_open(docs["ledger"], docs, bridge)
    base = 1_900_000_000_300_000_000
    hold_id = "9" * 32
    reserve_bytes = v3.DISCOVERY_PAGE_RESERVE_BYTES
    page_ids = [hashlib.sha256(f"page-{index}".encode()).hexdigest() for index in range(5)]
    for index, outcome in enumerate(outcomes):
        reservation_id = f"{100 + index:032x}"
        request_id = page_ids[index]
        ledger = v3.append_page_reserved_from_hold(
            ledger_bytes=ledger,
            policy_bytes=docs["policy"],
            pointer_bytes=docs["pointer"],
            bridge=bridge,
            at_ns=base + index * 4 + 1,
            hold_id=hold_id,
            standby_sha256=H,
            reservation_id=reservation_id,
            request_id=request_id,
            page_ordinal=index + 1,
            intent_path=(
                f"{v3.DISCOVERY_ROOT}/runtime-v2/epochs/000000/scans/0000/"
                f"page-{index + 1:02d}.intent.json"
            ),
            outcome_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/outcomes/{reservation_id}.json",
            intent_sha256=OUTCOME_SHA,
            reserved_bytes=reserve_bytes,
            allocated_before_bytes=0,
        )
        # This is the state after the atomic hold split and before any
        # terminal: the hold plus child reserve equals the original runway.
        events = v3.verify_v3_ledger(ledger, expected_policy_bytes=docs["policy"], bridge=bridge)
        opened = next(event for event in events if event["kind"] == "epoch_hold_open")
        converted = sum(
            event["fields"]["reserved_bytes"]
            for event in events
            if event["kind"] == "page_reserved_from_hold" and event["fields"]["hold_id"] == hold_id
        )
        assert converted == (index + 1) * reserve_bytes
        remaining = opened["fields"]["reserved_bytes"] - converted
        assert remaining + converted == v3.DISCOVERY_EPOCH_HOLD_BYTES

        if outcome != "crash":
            kind, name, allocated = {
                "success": ("settled", "success", 1),
                "ambiguous": ("ambiguous_terminal", "transport_ambiguous", 0),
                "absent": ("released", "pre_intent_absent", 0),
            }[outcome]
            ledger = v3.append_terminal(
                ledger_bytes=ledger,
                policy_bytes=docs["policy"],
                bridge=bridge,
                at_ns=base + index * 4 + 2,
                reservation_id=reservation_id,
                outcome=name,
                outcome_sha256=OUTCOME_SHA,
                actual_allocated_bytes=allocated,
                kind=kind,
            )
        # Every valid event prefix is independently replayable. Retained
        # ambiguous debt stays at the full child reserve; settled allocation
        # cannot exceed that reserve.
        events = v3.verify_v3_ledger(ledger, expected_policy_bytes=docs["policy"], bridge=bridge)
        terminal_kind = {
            event["fields"]["reservation_id"]: event["kind"]
            for event in events
            if event["kind"] in {"settled", "ambiguous_terminal", "released"}
        }
        pending_children = sum(
            event["fields"]["reserved_bytes"]
            for event in events
            if event["kind"] == "page_reserved_from_hold"
            and terminal_kind.get(event["fields"]["reservation_id"]) in {None, "ambiguous_terminal"}
        )
        allocated_success = sum(
            event["fields"]["actual_allocated_bytes"]
            for event in events
            if event["kind"] == "settled"
        )
        remaining = v3.DISCOVERY_EPOCH_HOLD_BYTES - converted
        assert remaining + pending_children + allocated_success <= v3.DISCOVERY_EPOCH_HOLD_BYTES

    if "crash" in outcomes:
        with pytest.raises(v3.CoordinatorV3Error, match="epoch_hold_close_binding"):
            v3.append_epoch_hold_close(
                ledger_bytes=ledger,
                policy_bytes=docs["policy"],
                pointer_bytes=docs["pointer"],
                bridge=bridge,
                at_ns=base + len(outcomes) * 4 + 1,
                hold_id=hold_id,
                standby_sha256=H,
                remaining_bytes=v3.DISCOVERY_EPOCH_HOLD_BYTES - len(outcomes) * reserve_bytes,
                outcome_sha256=OUTCOME_SHA,
            )


def test_epoch_hold_accepts_exactly_the_2592_pinned_page_ids():
    boot = _bootstrap()
    _p2, _ledger2, _inv2, _artifacts, bridge, docs = boot
    ledger = _hold_open(docs["ledger"], docs, bridge)
    events = v3.verify_v3_ledger(ledger, expected_policy_bytes=docs["policy"], bridge=bridge)
    previous = events[-1]["event_sha256"]
    page_ids = [
        hashlib.sha256(f"page-{index}".encode()).hexdigest()
        for index in range(v3.DISCOVERY_EPOCH_PAGE_COUNT)
    ]
    base = 1_900_000_000_400_000_000
    for index, request_id in enumerate(page_ids):
        reservation_id = f"{10_000 + index:032x}"
        fields = {
            "reservation_id": reservation_id,
            "service": "gecko_discovery",
            "request_id": request_id,
            "reserved_bytes": v3.DISCOVERY_PAGE_RESERVE_BYTES,
            "intent_path": (
                f"{v3.DISCOVERY_ROOT}/runtime-v2/epochs/000000/scans/"
                f"{index // 3:04d}/page-{index % 3 + 1:02d}.intent.json"
            ),
            "outcome_path": f"{v3.DISCOVERY_ROOT}/runtime-v2/outcomes/{reservation_id}.json",
            "intent_sha256": OUTCOME_SHA,
            "allocated_before_bytes": 0,
            "hold_id": "9" * 32,
            "standby_sha256": H,
            "page_ordinal": index + 1,
        }
        event = v3._event(
            seq=len(events) + index,
            kind="page_reserved_from_hold",
            at_ns=base + index,
            previous=previous,
            fields=fields,
        )
        ledger += v3.canonical(event)
        previous = event["event_sha256"]
    replay = v3.verify_v3_ledger(ledger, expected_policy_bytes=docs["policy"], bridge=bridge)
    converted = sum(
        event["fields"]["reserved_bytes"]
        for event in replay
        if event["kind"] == "page_reserved_from_hold"
    )
    assert converted == v3.DISCOVERY_EPOCH_HOLD_BYTES
    assert sum(event["kind"] == "page_reserved_from_hold" for event in replay) == 2_592
    with pytest.raises(v3.CoordinatorV3Error, match="hold_page_not_available|hold_page_conversion"):
        v3.append_page_reserved_from_hold(
            ledger_bytes=ledger,
            policy_bytes=docs["policy"],
            pointer_bytes=docs["pointer"],
            bridge=bridge,
            at_ns=base + len(page_ids) + 1,
            hold_id="9" * 32,
            standby_sha256=H,
            reservation_id="f" * 32,
            request_id=page_ids[-1],
            page_ordinal=len(page_ids),
            intent_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/epochs/000000/scans/0863/extra.intent.json",
            outcome_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/outcomes/{'f' * 32}.json",
            intent_sha256=OUTCOME_SHA,
            reserved_bytes=v3.DISCOVERY_PAGE_RESERVE_BYTES,
            allocated_before_bytes=0,
        )


def test_ambiguous_child_debt_carries_across_hold_rollover_once():
    boot = _bootstrap()
    p2, ledger2, inv2, artifacts, bridge, docs = boot
    ledger = _hold_open(docs["ledger"], docs, bridge)
    page_ids = [hashlib.sha256(f"page-{index}".encode()).hexdigest() for index in range(3)]
    base = 1_900_000_000_500_000_000
    reservation_id = "a" * 32
    ledger = v3.append_page_reserved_from_hold(
        ledger_bytes=ledger,
        policy_bytes=docs["policy"],
        pointer_bytes=docs["pointer"],
        bridge=bridge,
        at_ns=base,
        hold_id="9" * 32,
        standby_sha256=H,
        reservation_id=reservation_id,
        request_id=page_ids[0],
        page_ordinal=1,
        intent_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/epochs/000000/scans/0000/page-01.intent.json",
        outcome_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/outcomes/{reservation_id}.json",
        intent_sha256=OUTCOME_SHA,
        reserved_bytes=v3.DISCOVERY_PAGE_RESERVE_BYTES,
        allocated_before_bytes=0,
    )
    ledger = v3.append_terminal(
        ledger_bytes=ledger,
        policy_bytes=docs["policy"],
        bridge=bridge,
        at_ns=base + 1,
        reservation_id=reservation_id,
        outcome="transport_ambiguous",
        outcome_sha256=OUTCOME_SHA,
        actual_allocated_bytes=0,
        kind="ambiguous_terminal",
    )
    remaining = v3.DISCOVERY_EPOCH_HOLD_BYTES - v3.DISCOVERY_PAGE_RESERVE_BYTES
    ledger = v3.append_epoch_hold_close(
        ledger_bytes=ledger,
        policy_bytes=docs["policy"],
        pointer_bytes=docs["pointer"],
        bridge=bridge,
        at_ns=base + 2,
        hold_id="9" * 32,
        standby_sha256=H,
        remaining_bytes=remaining,
        outcome_sha256=OUTCOME_SHA,
    )

    next_page_ids = [
        hashlib.sha256(f"successor-page-{index}".encode()).hexdigest()
        for index in range(v3.DISCOVERY_EPOCH_PAGE_COUNT)
    ]
    ledger = v3.append_epoch_hold_open(
        ledger_bytes=ledger,
        policy_bytes=docs["policy"],
        pointer_bytes=docs["pointer"],
        bridge=bridge,
        v2_policy_bytes=p2,
        current_v2_ledger_bytes=ledger2,
        current_v2_root_inventory=inv2,
        v2_artifact_sha256_by_path=artifacts,
        current_root_inventory=_all_root_inventory(docs, bridge),
        free_bytes=250 * 1024**3,
        at_ns=base + 3,
        hold_id="b" * 32,
        successor_epoch_id="c" * 32,
        standby_sha256=OUTCOME_SHA,
        intent_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/epochs/000001/standby.json",
        outcome_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/outcomes/epoch-hold-{'b' * 32}.json",
        intent_sha256=H,
        reserved_bytes=v3.DISCOVERY_EPOCH_HOLD_BYTES,
        page_request_ids=next_page_ids,
    )

    args = _reserve_args(
        boot,
        reservation_id="d" * 32,
        request_id="capacity-proof",
        reserved=v3.SERVICE_CAPS["gecko_discovery"] - v3.DISCOVERY_EPOCH_HOLD_BYTES,
        free=250 * 1024**3,
    )
    args["ledger_bytes"] = ledger
    args["at_ns"] = base + 4
    with pytest.raises(v3.CoordinatorV3Error, match="v3_gecko_discovery_capacity"):
        v3.append_reservation(**args)


def test_abandoned_epoch_hold_releases_capacity_but_spends_all_page_ids():
    boot = _bootstrap()
    _p2, _ledger2, _inv2, _artifacts, bridge, docs = boot
    ledger = _hold_open(docs["ledger"], docs, bridge)
    base = 1_900_000_000_600_000_000
    ledger = v3.append_epoch_hold_close(
        ledger_bytes=ledger,
        policy_bytes=docs["policy"],
        pointer_bytes=docs["pointer"],
        bridge=bridge,
        at_ns=base,
        hold_id="9" * 32,
        standby_sha256=H,
        remaining_bytes=v3.DISCOVERY_EPOCH_HOLD_BYTES,
        outcome_sha256=OUTCOME_SHA,
    )
    events = v3.verify_v3_ledger(ledger, expected_policy_bytes=docs["policy"], bridge=bridge)
    assert events[-1]["kind"] == "epoch_hold_close"
    assert events[-1]["fields"]["remaining_bytes"] == v3.DISCOVERY_EPOCH_HOLD_BYTES
    with pytest.raises(v3.CoordinatorV3Error, match="v3_epoch_hold_binding"):
        _hold_open(ledger, docs, bridge, hold_id="9" * 32, at_ns=base + 1)

    # A different hold cannot recycle the abandoned standby's deterministic
    # page identities, even though its physical hold has been released.
    page_ids = [
        hashlib.sha256(f"page-{index}".encode()).hexdigest()
        for index in range(v3.DISCOVERY_EPOCH_PAGE_COUNT)
    ]
    p2, ledger2, inv2, artifacts, _bridge, _docs = boot
    with pytest.raises(v3.CoordinatorV3Error, match="v3_epoch_hold_binding"):
        v3.append_epoch_hold_open(
            ledger_bytes=ledger,
            policy_bytes=docs["policy"],
            pointer_bytes=docs["pointer"],
            bridge=bridge,
            v2_policy_bytes=p2,
            current_v2_ledger_bytes=ledger2,
            current_v2_root_inventory=inv2,
            v2_artifact_sha256_by_path=artifacts,
            current_root_inventory=_all_root_inventory(docs, bridge),
            free_bytes=250 * 1024**3,
            at_ns=base + 2,
            hold_id="a" * 32,
            successor_epoch_id="b" * 32,
            standby_sha256=OUTCOME_SHA,
            intent_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/epochs/000001/standby.json",
            outcome_path=f"{v3.DISCOVERY_ROOT}/runtime-v2/outcomes/epoch-hold-{'a' * 32}.json",
            intent_sha256=H,
            reserved_bytes=v3.DISCOVERY_EPOCH_HOLD_BYTES,
            page_request_ids=page_ids,
        )


@pytest.mark.parametrize("bad", [True, -1, 1.5, "100"])
def test_bridge_rejects_malformed_root_inventory_values(bad):
    p2, ledger2, inv2, artifacts, _bridge, _docs = _bootstrap()
    changed = dict(inv2)
    changed[next(iter(changed))] = bad
    with pytest.raises(v3.CoordinatorV3Error, match="root_inventory_sizes"):
        v3.build_v2_bridge(
            v2_policy_bytes=p2,
            v2_ledger_bytes=ledger2,
            v2_root_inventory=changed,
            artifact_sha256_by_path=artifacts,
        )
