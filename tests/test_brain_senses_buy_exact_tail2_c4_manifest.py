from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import brain_senses_block_epoch_v2 as epoch_v2
from tools import brain_senses_buy_exact_tail2_c4_manifest as manifest
from tools import brain_senses_campaign4_recovery as campaign4


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def _complete_frame(root: Path, bucket: str = "20261010T230000Z") -> Path:
    epoch = root / bucket
    selected = list(range(100, 116))
    plan = {
        "epoch_id": bucket,
        "bucket": bucket,
        "schedule_id": campaign4.CAMPAIGN_ID,
        "provider": "helius-free",
        "bootstrap": False,
        "code_sha256": epoch_v2._sha(Path(epoch_v2.__file__)),
    }
    checks = [
        {
            "slot": slot,
            "status": "qualified_structural_reference_count",
            "block_identity_equal": True,
            "signature_order_equal": True,
        }
        for slot in selected
    ]
    _write(epoch / "epoch-plan.json", plan)
    _write(
        epoch / "frame.json",
        {"status": "captured", "start": 1, "end_exclusive": 2, "width": 1},
    )
    _write(epoch / "selection-plan.json", {"selected_slots": selected})
    _write(
        epoch / "epoch-summary.json",
        {
            "status": "frame_complete",
            "selected_slots": selected,
            "request_reservations": campaign4.REQUESTS_PER_FRAME,
            "structural_checks": checks,
        },
    )
    _write(epoch / "STOP", {"status": "frame_complete"})
    _write(
        epoch / "frame-reservation.json",
        {"bucket": bucket, "request_count": campaign4.REQUESTS_PER_FRAME},
    )
    import hashlib

    summary_hash = hashlib.sha256((epoch / "epoch-summary.json").read_bytes()).hexdigest()
    _write(
        epoch / "frame-reservation-release.json",
        {
            "bucket": bucket,
            "actual_requests": campaign4.REQUESTS_PER_FRAME,
            "summary_sha256": summary_hash,
        },
    )
    return epoch


def test_scheduled_bucket_enumerator_uses_hour_starts_after_commit() -> None:
    assert manifest._scheduled_buckets("20261011T020000Z") == [
        "20261010T230000Z",
        "20261011T000000Z",
        "20261011T010000Z",
        "20261011T020000Z",
    ]


def test_metadata_candidate_requires_all_structural_self_checks(tmp_path: Path) -> None:
    epoch = _complete_frame(tmp_path)

    assert manifest._candidate_status(epoch, epoch.name)[0] == "successful"

    summary_path = epoch / "epoch-summary.json"
    summary = json.loads(summary_path.read_text())
    summary["structural_checks"][0]["signature_order_equal"] = False
    _write(summary_path, summary)

    assert manifest._candidate_status(epoch, epoch.name)[0] == "failed_or_partial"
    summary["structural_checks"][0] = None
    _write(summary_path, summary)

    assert manifest._candidate_status(epoch, epoch.name)[0] == "failed_or_partial"


def test_unmaterialized_bucket_is_explicitly_missing(tmp_path: Path) -> None:
    assert manifest._candidate_status(tmp_path / "absent", "20261010T230000Z") == (
        "missing",
        None,
    )


def test_metadata_builder_enforces_the_preregistered_source_pins() -> None:
    prereg = json.loads(manifest.PREREG_PATH.read_text())
    actual = manifest._pinned_source_hashes(prereg)
    assert actual.keys() == prereg["source_pins"].keys()
    prereg["source_pins"]["census_tool"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="manifest_preregistered_source_hash_mismatch"):
        manifest._pinned_source_hashes(prereg)


def test_build_manifest_does_not_hash_any_frame_until_four_metadata_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "campaign"
    root.mkdir()
    _write(root / "frontier-v3.json", {"last_attempt_bucket": "20261011T010000Z"})
    monkeypatch.setattr(campaign4, "ROOT", root)
    monkeypatch.setattr(
        manifest,
        "_candidate_status",
        lambda _epoch, bucket: ("successful", {"bucket": bucket}),
    )

    def unexpected_source_binding(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("compressed frame hashing started before four metadata candidates")

    monkeypatch.setattr(manifest, "_frame_binding", unexpected_source_binding)
    with pytest.raises(
        ValueError,
        match="manifest_four_metadata_complete_frames_not_yet_available",
    ):
        manifest.build_manifest(root, tmp_path / "manifest.json")


def test_frame_binding_reconciles_metadata_and_capture_hashes_without_decompression(
    tmp_path: Path,
) -> None:
    bucket = "20261010T230000Z"
    epoch = tmp_path / "epochs" / bucket
    epoch.mkdir(parents=True)
    inventory = list(range(100, 116))
    inventory_sha = "a" * 64
    plan = {
        "epoch_id": bucket,
        "bucket": bucket,
        "schedule_id": campaign4.CAMPAIGN_ID,
        "provider": "helius-free",
        "bootstrap": False,
        "code_sha256": epoch_v2._sha(Path(epoch_v2.__file__)),
        "seed": "metadata-test",
        "selection_rule": "uniform_without_replacement_from_finalized_getblocks_members",
        "selection_count": 16,
        "start": 100,
        "next_start": 100,
    }
    selection = epoch_v2._build_selection(
        plan=plan, inventory_slots=inventory, inventory_sha256=inventory_sha
    )
    frame = {"status": "captured", "start": 100, "end_exclusive": 116, "width": 16}
    checks = [
        {
            "slot": slot,
            "status": "qualified_structural_reference_count",
            "block_identity_equal": True,
            "signature_order_equal": True,
        }
        for slot in selection["selected_slots"]
    ]
    _write(epoch / "epoch-plan.json", plan)
    _write(epoch / "frame.json", frame)
    _write(epoch / "selection-plan.json", selection)
    _write(epoch / "attempt-context.json", {"bucket": bucket, "missed_slot_backfill": False})
    (epoch / "events").mkdir()
    operations = epoch_v2._operations_for(epoch, plan, selection)
    requests_dir = epoch / "requests"
    results_dir = epoch / "request-results"
    requests_dir.mkdir()
    results_dir.mkdir()
    raw_bytes = stored_bytes = 0
    for sequence, operation in enumerate(operations, start=1):
        request_body = epoch_v2._request_body(operation["method"], operation["params"])
        response_hash = inventory_sha if operation["phase"] == "inventory" else f"{sequence:064x}"
        raw_size = 100
        compressed = f"compressed-{sequence}".encode()
        compressed_hash = hashlib.sha256(compressed).hexdigest()
        capture_path = epoch / "captures" / f"{sequence:012d}"
        capture_path.mkdir(parents=True)
        (capture_path / "response.json.zst").write_bytes(compressed)
        capture_meta = {
            "source_alias": "helius-free",
            "method": operation["method"],
            "request_json": request_body.decode(),
            "request_started_ns": 1_000 + sequence,
            "received_ns": 2_000 + sequence,
            "response_sha256": response_hash,
            "response_bytes": raw_size,
            "storage": {
                "codec": "zstd",
                "path": "response.json.zst",
                "sha256": compressed_hash,
                "bytes": len(compressed),
            },
        }
        _write(capture_path / "receipt.json", capture_meta)
        request = {
            "sequence": sequence,
            "phase": operation["phase"],
            "provider": "helius-free",
            "method": operation["method"],
            "params": operation["params"],
            "request_sha256": hashlib.sha256(request_body).hexdigest(),
        }
        result = {
            "sequence": sequence,
            "phase": operation["phase"],
            "provider": "helius-free",
            "method": operation["method"],
            "slot": operation.get("slot"),
            "detail": operation.get("detail"),
            "status": "captured",
            "result_status": "parsed",
            "capture_path": f"captures/{sequence:012d}",
            "request_started_ns": capture_meta["request_started_ns"],
            "receipt_ns": capture_meta["received_ns"],
            "response_sha256": response_hash,
            "response_bytes": raw_size,
            "stored_response_bytes": len(compressed),
        }
        if operation["phase"] == "inventory":
            result["inventory_slots"] = inventory
        _write(requests_dir / f"{sequence:012d}.json", request)
        _write(results_dir / f"{sequence:012d}.json", result)
        raw_bytes += raw_size
        stored_bytes += len(compressed)
    summary = {
        "status": "frame_complete",
        "selected_slots": selection["selected_slots"],
        "request_reservations": len(operations),
        "captured_response_bytes": raw_bytes,
        "stored_response_bytes": stored_bytes,
        "structural_checks": checks,
    }
    reservation = {"bucket": bucket, "request_count": len(operations)}
    _write(epoch / "epoch-summary.json", summary)
    _write(epoch / "STOP", {"status": "frame_complete"})
    _write(epoch / "frame-reservation.json", reservation)
    _write(
        epoch / "frame-reservation-release.json",
        {
            "bucket": bucket,
            "actual_requests": len(operations),
            "actual_raw_bytes": raw_bytes,
            "actual_stored_bytes": stored_bytes,
            "reservation_sha256": hashlib.sha256(
                (epoch / "frame-reservation.json").read_bytes()
            ).hexdigest(),
            "summary_sha256": hashlib.sha256(
                (epoch / "epoch-summary.json").read_bytes()
            ).hexdigest(),
        },
    )

    with pytest.raises(ValueError, match="manifest_summary_changed_during_selection"):
        manifest._frame_binding(epoch.parent.parent, bucket, {**summary, "status": "changed"})
    binding = manifest._frame_binding(epoch.parent.parent, bucket, summary)

    assert binding["request_count"] == 34
    assert binding["selected_slots"] == selection["selected_slots"]
    assert binding["reconciled_raw_bytes"] == raw_bytes
    assert binding["reconciled_stored_bytes"] == stored_bytes
    assert len(binding["artifact_index"]) == 144
    (epoch / "events" / "unbound.json").write_text("{}\n")
    with pytest.raises(ValueError, match="manifest_frame_artifact_inventory_mismatch"):
        manifest._frame_binding(epoch.parent.parent, bucket, summary)
