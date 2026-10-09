import json

import pytest

from memecoin_ai.sources import Capture, SourceError
from memecoin_experience import block_inventory
from memecoin_experience.storage import canonical

NS = 1_000_000_000


def _save_capture(root, relative, *, method, params, result=None, error=None, clock=0):
    request = canonical(dict(jsonrpc="2.0", id=1, method=method, params=params)).decode()
    response_value = dict(jsonrpc="2.0", id=1)
    if error is None:
        response_value["result"] = result
    else:
        response_value["error"] = error
    capture = Capture(
        source_alias="synthetic-block-fixture",
        method=method,
        request_json=request,
        request_started_ns=clock * NS,
        received_ns=clock * NS + 1,
        response=canonical(response_value),
    )
    capture.save(root / relative)
    return dict(path=relative)


def _block(slot, parent, parent_hash):
    return dict(
        blockhash=f"hash-{slot}",
        previousBlockhash=parent_hash,
        parentSlot=parent,
        blockTime=slot * 10,
        transactions=[],
    )


def _fixture(
    root,
    *,
    start=10,
    end=17,
    listed=None,
    body_by_slot=None,
    errors=None,
    stopped=(),
    unattempted=(),
    boundary=True,
    boundary_left_slot=None,
    boundary_right_slot=None,
    boundary_right_body=None,
    enumeration_error=None,
):
    root.mkdir(parents=True)
    listed = [10, 12] if listed is None else listed
    body_by_slot = (
        {10: _block(10, 9, "hash-9"), 12: _block(12, 10, "hash-10")}
        if body_by_slot is None
        else body_by_slot
    )
    errors = {} if errors is None else errors
    captures = []
    clock = 1
    enumeration = _save_capture(
        root,
        "enumeration",
        method="getBlocks",
        params=[start, end - 1, {"commitment": "finalized"}],
        result=listed,
        error=enumeration_error,
        clock=clock,
    )
    captures.append(dict(purpose="enumeration", **enumeration))
    clock += 1
    if boundary:
        left_slot = start - 1 if boundary_left_slot is None else boundary_left_slot
        left = _save_capture(
            root,
            "boundary-left",
            method="getBlock",
            params=[left_slot, block_inventory.BLOCK_OPTIONS],
            result=_block(left_slot, left_slot - 1, f"hash-{left_slot - 1}"),
            clock=clock,
        )
        captures.append(dict(purpose="boundary", side="left", slot=left_slot, **left))
        clock += 1
    for slot in range(start, end):
        if slot in stopped or slot in unattempted:
            continue
        if slot in errors:
            error = errors[slot]
            result = None
        else:
            error = None
            result = body_by_slot.get(slot)
        ref = _save_capture(
            root,
            f"slots/{slot}",
            method="getBlock",
            params=[slot, block_inventory.BLOCK_OPTIONS],
            result=result,
            error=error,
            clock=clock,
        )
        captures.append(dict(purpose="slot", slot=slot, **ref))
        clock += 1
    if boundary_right_slot is not None:
        right = _save_capture(
            root,
            "boundary-right",
            method="getBlock",
            params=[boundary_right_slot, block_inventory.BLOCK_OPTIONS],
            result=boundary_right_body
            or _block(
                boundary_right_slot,
                boundary_right_slot - 1,
                f"hash-{boundary_right_slot - 1}",
            ),
            clock=clock,
        )
        captures.append(dict(purpose="boundary", side="right", slot=boundary_right_slot, **right))
    manifest = dict(
        version="solana-block-range-fixture-v1",
        start_slot=start,
        end_slot=end,
        interval_convention="half-open-start-inclusive-end-exclusive-v1",
        captures=captures,
        stopped_slots=sorted(stopped),
    )
    (root / "fixture.json").write_bytes(canonical(manifest))
    return root


def test_offline_range_inventory_ledgers_all_statuses_and_replicates(tmp_path):
    root = tmp_path / "fixture"
    errors = {
        13: dict(code=-32000, message="temporary fixture failure"),
        14: dict(code=-32601, message="unsupported fixture method"),
    }
    body_by_slot = {
        10: _block(10, 9, "hash-9"),
        12: _block(12, 10, "hash-10"),
    }
    _fixture(root, body_by_slot=body_by_slot, errors=errors, stopped=(16,))
    source = tmp_path / "source"
    pin = block_inventory.publish_finalized_block_inventory(source, root)
    before = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    receipt = block_inventory.load_finalized_block_inventory(source, pin)
    after = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert receipt["capabilities"] == ["finalized_block_inventory"]
    assert receipt["time_basis"] == "slot_only_no_unix_time_conversion"
    assert receipt["ledger_counts"] == {
        "complete": 2,
        "failed": 1,
        "skipped": 1,
        "stopped": 1,
        "unknown": 1,
        "unsupported": 1,
    }
    assert "observation_sources" not in receipt
    ledger = []
    for page in receipt["ledger_pages"]:
        rows = json.loads(
            (source / "qualification-block-inventory-pages" / f"{page['pin']}.json").read_text()
        )
        assert len(rows) == page["count"]
        ledger.extend(rows)
    assert [(row["slot"], row["status"]) for row in ledger] == [
        (10, "complete"),
        (11, "skipped"),
        (12, "complete"),
        (13, "failed"),
        (14, "unsupported"),
        (15, "unknown"),
        (16, "stopped"),
    ]
    assert [(row["slot"], row["transport_status"]) for row in ledger] == [
        (10, "captured_result"),
        (11, "captured_null_result"),
        (12, "captured_result"),
        (13, "failed"),
        (14, "unsupported"),
        (15, "captured_null_result"),
        (16, "stopped"),
    ]
    assert all("event_ns" not in row and "zero" not in row for row in ledger)

    replica = tmp_path / "replica"
    copied = block_inventory.replicate_finalized_block_inventory(source, replica, pin)
    assert copied["receipt_pin"] == pin
    assert block_inventory.load_finalized_block_inventory(replica, pin) == receipt


def test_absent_getblocks_entry_alone_stays_unknown_not_skipped(tmp_path):
    root = tmp_path / "fixture"
    _fixture(
        root,
        start=10,
        end=12,
        listed=[10],
        body_by_slot={10: _block(10, 9, "hash-9")},
    )
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    assert receipt["ledger_counts"]["unknown"] == 1
    assert receipt["ledger_counts"]["skipped"] == 0
    pages = [
        row
        for ref in receipt["ledger_pages"]
        for row in json.loads(
            (store / "qualification-block-inventory-pages" / f"{ref['pin']}.json").read_text()
        )
    ]
    unresolved = next(row for row in pages if row["slot"] == 11)
    assert unresolved["reason"] == "getblock_null_result"
    assert unresolved["transport_status"] == "captured_null_result"
    assert unresolved["observation_disposition"] == "block_presence_unresolved"


def test_explicit_null_getblock_is_skipped_only_with_enumeration_and_parent_bridge(tmp_path):
    root = tmp_path / "fixture"
    _fixture(
        root,
        start=10,
        end=12,
        listed=[10],
        body_by_slot={10: _block(10, 9, "hash-9")},
        boundary_right_slot=12,
        boundary_right_body=_block(12, 10, "hash-10"),
    )
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    skipped = next(row for row in _read_ledger(store, receipt) if row["slot"] == 11)
    assert skipped["status"] == "skipped"
    assert skipped["transport_status"] == "captured_null_result"
    assert skipped["observation_disposition"] == "block_absent_confirmed"
    assert skipped["lineage_status"] == "linked"
    assert len(skipped["evidence"]) == 5
    assert skipped["proof_available_ns"] is not None


@pytest.mark.parametrize(
    ("capture_kwargs", "status", "transport"),
    [
        ({"errors": {11: dict(code=-32000, message="fixture failure")}}, "failed", "failed"),
        ({"stopped": (11,)}, "stopped", "stopped"),
    ],
)
def test_lineage_skip_disposition_does_not_erase_failed_or_stopped_transport(
    tmp_path, capture_kwargs, status, transport
):
    root = tmp_path / "fixture"
    _fixture(
        root,
        start=10,
        end=12,
        listed=[10],
        body_by_slot={10: _block(10, 9, "hash-9")},
        boundary_right_slot=12,
        boundary_right_body=_block(12, 10, "hash-10"),
        **capture_kwargs,
    )
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    row = next(row for row in _read_ledger(store, receipt) if row["slot"] == 11)
    assert row["status"] == status
    assert row["transport_status"] == transport
    assert row["observation_disposition"] == "block_absent_confirmed"
    assert row["proof_available_ns"] is not None
    assert len(row["evidence"]) == (5 if status == "failed" else 4)


def test_getblocks_error_preserves_positive_per_slot_block_facts(tmp_path):
    root = tmp_path / "fixture"
    _fixture(
        root,
        start=10,
        end=12,
        listed=[],
        body_by_slot={10: _block(10, 9, "hash-9")},
        enumeration_error=dict(code=-32000, message="enumeration unavailable"),
        unattempted=(11,),
    )
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    assert receipt["ledger_counts"]["complete"] == 1
    assert receipt["ledger_counts"]["failed"] == 0
    assert receipt["ledger_counts"]["skipped"] == 0
    assert receipt["enumeration_status"] == "failed"
    pages = [
        row
        for ref in receipt["ledger_pages"]
        for row in json.loads(
            (store / "qualification-block-inventory-pages" / f"{ref['pin']}.json").read_text()
        )
    ]
    assert next(row for row in pages if row["slot"] == 11)["status"] == "unknown"
    assert next(row for row in pages if row["slot"] == 11)["reason"] == "enumeration_failed"


def test_partial_lineage_conflict_does_not_discard_independent_block_fact(tmp_path):
    root = tmp_path / "fixture"
    _fixture(
        root,
        start=10,
        end=14,
        listed=[10, 11, 13],
        body_by_slot={10: _block(10, 9, "hash-9"), 13: _block(13, 10, "hash-10")},
        errors={11: dict(code=-32000, message="intermediate block unavailable")},
    )
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    assert receipt["ledger_counts"]["complete"] == 2
    assert receipt["ledger_counts"]["failed"] == 1
    assert receipt["ledger_counts"]["unknown"] == 1
    assert receipt["ledger_counts"]["skipped"] == 0


def test_unavailable_parent_keeps_full_child_block_positive_without_gap_claim(tmp_path):
    root = tmp_path / "fixture"
    _fixture(
        root,
        start=10,
        end=13,
        listed=[10, 11, 12],
        body_by_slot={
            10: _block(10, 9, "hash-9"),
            12: _block(12, 11, "hash-11"),
        },
        errors={11: dict(code=-32000, message="parent response unavailable")},
    )
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    assert receipt["ledger_counts"]["complete"] == 2
    assert receipt["ledger_counts"]["failed"] == 1
    assert receipt["ledger_counts"]["skipped"] == 0


@pytest.mark.parametrize("mutation", ["unlisted_block", "parent", "boundary"])
def test_partial_enumeration_or_parent_conflict_is_localized(tmp_path, mutation):
    root = tmp_path / "fixture"
    if mutation == "duplicate_list":
        _fixture(
            root,
            start=10,
            end=12,
            listed=[10, 10],
            body_by_slot={10: _block(10, 9, "hash-9")},
        )
    elif mutation == "unlisted_block":
        _fixture(
            root,
            start=10,
            end=12,
            listed=[10],
            body_by_slot={
                10: _block(10, 9, "hash-9"),
                11: _block(11, 10, "hash-10"),
            },
        )
    elif mutation == "parent":
        _fixture(
            root,
            body_by_slot={10: _block(10, 9, "hash-9"), 12: _block(12, 9, "wrong-parent-hash")},
        )
    else:
        _fixture(
            root,
            start=10,
            end=12,
            listed=[10],
            body_by_slot={10: _block(10, 9, "wrong-boundary-hash")},
        )
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    pages = [
        row
        for ref in receipt["ledger_pages"]
        for row in json.loads(
            (store / "qualification-block-inventory-pages" / f"{ref['pin']}.json").read_text()
        )
    ]
    affected_slot = {"unlisted_block": 11, "parent": 12, "boundary": 10}[mutation]
    expected_status = "complete"
    affected = next(row for row in pages if row["slot"] == affected_slot)
    assert affected["status"] == expected_status
    if mutation != "unlisted_block":
        assert affected["lineage_status"] == "conflict"
    else:
        assert affected["enumeration_relation"] == "not_listed"


def test_duplicate_enumeration_is_failed_without_inference(tmp_path):
    root = tmp_path / "fixture"
    _fixture(root, start=10, end=12, listed=[10, 10], body_by_slot={10: _block(10, 9, "hash-9")})
    pin = block_inventory.publish_finalized_block_inventory(tmp_path / "store", root)
    receipt = block_inventory.load_finalized_block_inventory(tmp_path / "store", pin)
    assert receipt["enumeration_status"] == "failed"
    assert receipt["ledger_counts"]["unknown"] == 1
    assert receipt["ledger_counts"]["complete"] == 1


def test_range_is_half_open_bounded_and_slot_time_is_not_converted(tmp_path):
    root = tmp_path / "fixture"
    _fixture(root, start=1, end=258, listed=[], body_by_slot={})
    store = tmp_path / "store"
    with pytest.raises(ValueError, match="fixture_bounds"):
        block_inventory.publish_finalized_block_inventory(store, root)

    other = tmp_path / "fixture2"
    _fixture(other, start=10, end=12, listed=[10], body_by_slot={10: _block(10, 9, "hash-9")})
    pin = block_inventory.publish_finalized_block_inventory(store, other)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    assert receipt["interval"] == {
        "start_slot": 10,
        "end_slot": 12,
        "convention": "half-open-start-inclusive-end-exclusive-v1",
    }
    assert receipt["time_basis"] == "slot_only_no_unix_time_conversion"


def test_missing_capture_page_or_ledger_page_breaks_dependency_closure(tmp_path):
    root = tmp_path / "fixture"
    _fixture(root)
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    replica = tmp_path / "replica"
    block_inventory.replicate_finalized_block_inventory(store, replica, pin)
    page_pin = receipt["ledger_pages"][0]["pin"]
    (replica / "qualification-block-inventory-pages" / f"{page_pin}.json").unlink()
    with pytest.raises((FileNotFoundError, ValueError)):
        block_inventory.load_finalized_block_inventory(replica, pin)


def test_mutated_raw_block_dependency_is_rejected(tmp_path):
    root = tmp_path / "fixture"
    _fixture(root)
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    capture_root = store / "qualification-block-captures" / receipt["capture_set_pin"]
    response = capture_root / "slots/10/response.json"
    response.write_bytes(response.read_bytes().replace(b"hash-10", b"hash-xx"))
    with pytest.raises((ValueError, SourceError)):
        block_inventory.load_finalized_block_inventory(store, pin)


def test_ledger_pages_stream_at_the_declared_page_boundary(tmp_path):
    root = tmp_path / "fixture"
    start, end = 1000, 1130
    blocks = {slot: _block(slot, slot - 1, f"hash-{slot - 1}") for slot in range(start, end)}
    _fixture(root, start=start, end=end, listed=list(blocks), body_by_slot=blocks)
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    assert [page["count"] for page in receipt["ledger_pages"]] == [128, 2]
    assert receipt["ledger_counts"]["complete"] == 130


def _read_ledger(store, receipt):
    return [
        row
        for reference in receipt["ledger_pages"]
        for row in json.loads(
            (store / "qualification-block-inventory-pages" / f"{reference['pin']}.json").read_text()
        )
    ]


def test_empty_bracket_with_bounded_nonexact_witnesses_closes_skipped_slots(tmp_path):
    root = tmp_path / "fixture"
    _fixture(
        root,
        start=10,
        end=14,
        listed=[],
        body_by_slot={},
        unattempted=range(10, 14),
        boundary_left_slot=8,
        boundary_right_slot=16,
        boundary_right_body=_block(16, 8, "hash-8"),
    )
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    assert receipt["ledger_counts"]["skipped"] == 4
    assert [row["status"] for row in _read_ledger(store, receipt)] == ["skipped"] * 4


def test_conflicting_block_never_enters_lineage_or_authorizes_later_skips(tmp_path):
    root = tmp_path / "fixture"
    _fixture(
        root,
        start=10,
        end=18,
        listed=[10, 12, 15],
        body_by_slot={
            10: _block(10, 9, "hash-9"),
            12: _block(12, 10, "wrong-hash"),
            15: _block(15, 12, "hash-12"),
        },
        unattempted=(11, 13, 14, 16, 17),
    )
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    rows = _read_ledger(store, receipt)
    assert next(row for row in rows if row["slot"] == 12)["status"] == "complete"
    assert next(row for row in rows if row["slot"] == 12)["lineage_status"] == "conflict"
    assert next(row for row in rows if row["slot"] == 11)["status"] == "unknown"
    assert next(row for row in rows if row["slot"] == 13)["status"] == "unknown"
    assert next(row for row in rows if row["slot"] == 14)["status"] == "unknown"
    assert next(row for row in rows if row["slot"] == 16)["status"] == "unknown"


def test_unlisted_full_block_cannot_join_lineage_or_close_neighboring_gaps(tmp_path):
    root = tmp_path / "fixture"
    _fixture(
        root,
        start=10,
        end=15,
        listed=[10, 13],
        body_by_slot={
            10: _block(10, 9, "hash-9"),
            12: _block(12, 10, "hash-10"),
            13: _block(13, 12, "hash-12"),
        },
        unattempted=(11, 14),
    )
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    rows = _read_ledger(store, receipt)
    assert next(row for row in rows if row["slot"] == 12)["status"] == "complete"
    assert next(row for row in rows if row["slot"] == 12)["lineage_status"] == "conflict"
    assert next(row for row in rows if row["slot"] == 11)["status"] == "unknown"
    assert next(row for row in rows if row["slot"] == 14)["status"] == "unknown"


def test_replica_is_self_contained_after_source_removal(tmp_path):
    root = tmp_path / "fixture"
    _fixture(root)
    source, replica = tmp_path / "source", tmp_path / "replica"
    pin = block_inventory.publish_finalized_block_inventory(source, root)
    block_inventory.replicate_finalized_block_inventory(source, replica, pin)
    import shutil

    shutil.rmtree(source)
    assert block_inventory.load_finalized_block_inventory(replica, pin)["capture_set_pin"]


def test_bundle_publish_is_atomic_and_immutable_conflicts_fail(tmp_path, monkeypatch):
    root = tmp_path / "fixture"
    _fixture(root)
    store = tmp_path / "store"
    original = block_inventory._seal_capture_tree

    def interrupt(stage):
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(block_inventory, "_seal_capture_tree", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        block_inventory.publish_finalized_block_inventory(store, root)
    assert not list((store / "qualification-block-captures").glob("*"))
    monkeypatch.setattr(block_inventory, "_seal_capture_tree", original)
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    bundle = store / "qualification-block-captures" / receipt["capture_set_pin"]
    (bundle / "fixture.json").write_bytes(b"conflict")
    with pytest.raises(ValueError, match="capture_bundle_seal|immutable_conflict"):
        block_inventory.publish_finalized_block_inventory(store, root)


def test_large_single_capture_and_fixture_bounds(tmp_path):
    root = tmp_path / "fixture"
    large = _block(10, 9, "hash-9")
    large["transactions"] = ["x" * 4_100_000]
    _fixture(root, start=10, end=11, listed=[10], body_by_slot={10: large}, boundary=False)
    store = tmp_path / "store"
    pin = block_inventory.publish_finalized_block_inventory(store, root)
    receipt = block_inventory.load_finalized_block_inventory(store, pin)
    assert receipt["ledger_counts"]["complete"] == 1

    too_large_fixture = tmp_path / "large-fixture"
    too_large_fixture.mkdir()
    (too_large_fixture / "fixture.json").write_bytes(b" " * (block_inventory.MAX_FIXTURE_BYTES + 1))
    with pytest.raises(ValueError, match="fixture_byte_bound"):
        block_inventory.publish_finalized_block_inventory(tmp_path / "other", too_large_fixture)


@pytest.mark.parametrize("path", ["./enumeration", "enumeration//child", "../enumeration"])
def test_capture_paths_reject_noncanonical_posix_segments(tmp_path, path):
    root = tmp_path / "fixture"
    _fixture(root)
    manifest_path = root / "fixture.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["captures"][0]["path"] = path
    manifest_path.write_bytes(canonical(manifest))
    with pytest.raises(ValueError, match="capture_ref"):
        block_inventory.publish_finalized_block_inventory(tmp_path / "store", root)


def test_capture_path_rejects_symlink_before_reading_capture(tmp_path):
    root = tmp_path / "fixture"
    _fixture(root)
    (root / "enumeration-link").symlink_to(root / "enumeration", target_is_directory=True)
    manifest_path = root / "fixture.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["captures"][0]["path"] = "enumeration-link"
    manifest_path.write_bytes(canonical(manifest))
    with pytest.raises(ValueError, match="experience_symlink"):
        block_inventory.publish_finalized_block_inventory(tmp_path / "store", root)
