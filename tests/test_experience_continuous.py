"""Only synthetic captures; never open an owner index or protected payload."""

import json
import shutil
import sqlite3
from collections import Counter

import pytest
from curriculum.test_admission import capture

from memecoin_brain.source import EXCLUDED
from memecoin_continuous.store import connect, ingest
from memecoin_experience.continuous import export, source
from memecoin_experience.sources import inputs
from memecoin_experience.storage import build, file_hash


def fixture(tmp_path, **kwargs):
    cap, birth = capture(tmp_path, **kwargs)
    index = tmp_path / "index.sqlite"
    with connect(index) as db:
        ingest(db, cap, birth["pool"])
    plan = dict(
        role="train",
        cutoff_ns=10**18,
        start_s=300000,
        end_s=318000,
        selection="synthetic enrollment before results",
        pools=[{k: birth[k] for k in ("pool", "base_mint", "quote_mint")}],
    )
    return index, cap, plan


def test_export_portable_into_experience(tmp_path):
    index, cap, plan = fixture(tmp_path)
    before = file_hash(index)
    out = tmp_path / "export"
    report = export(index, out, plan)
    assert report["bars"] == 60 and report["coverage"][0]["missing_slots"] == 0
    assert report["counts"]["qualified"] == 1
    assert file_hash(index) == before
    moved = tmp_path / "moved"
    shutil.move(out, moved)
    shutil.rmtree(cap)
    ref = dict(kind="continuous", path=str(moved), pin=report["pin"])
    counts = Counter()
    rows = list(inputs([ref], 10**18, counts))
    result = build(tmp_path / "store", rows, sources=[ref], cutoff=10**18)
    assert result["decisions"] == 53
    assert counts["source_rows"] == 60
    with pytest.raises(ValueError, match="exists"):
        export(index, moved, plan)


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"wrong": True}, "mint_mismatch"),
        ({"currency": "token"}, "unsupported_namespace"),
        ({"empty": True}, "qualified"),
    ],
)
def test_rejections_and_empty_pools_stay_in_denominator(tmp_path, kwargs, reason):
    index, _, plan = fixture(tmp_path, **kwargs)
    plan["pools"].append(dict(pool="no-data", base_mint="D", quote_mint="E"))
    report = export(index, tmp_path / "export", plan)
    assert report["bars"] == 0
    assert len(report["coverage"]) == 2
    assert all(p["missing_slots"] == 60 for p in report["coverage"])
    assert report["counts"][reason] == 1


def test_role_and_protected_plan_before_any_input(tmp_path):
    _, _, plan = fixture(tmp_path)
    plan["pools"][0]["pool"] = next(iter(EXCLUDED))
    with pytest.raises(ValueError, match="protected"):
        export(tmp_path / "nonexistent", tmp_path / "out", plan)
    plan["role"] = "reserved_holdout"
    with pytest.raises(ValueError, match="reserved"):
        export(tmp_path / "nonexistent", tmp_path / "out", plan)


def test_index_and_raw_corruption_abort_without_publication(tmp_path):
    index, _, plan = fixture(tmp_path)
    with sqlite3.connect(index) as db:
        db.execute("delete from versions where open_s=(select min(open_s) from versions)")
    with pytest.raises(ValueError, match="index_raw"):
        export(index, tmp_path / "out", plan)
    assert not (tmp_path / "out").exists()


def test_reserved_export_rejected_before_payload_read(tmp_path):
    index, _, plan = fixture(tmp_path)
    plan["role"] = "research_evaluation"
    out = tmp_path / "out"
    result = export(index, out, plan)
    (out / "meta/rows.jsonl").unlink()
    with pytest.raises(ValueError, match="reserved"):
        list(source(dict(path=str(out), pin=result["pin"]), 10**18))


def test_consistent_snapshot_with_writer_and_revision_ties(tmp_path, monkeypatch):
    from memecoin_experience import continuous
    from memecoin_ml.data import sha
    from memecoin_registry.contracts import seal

    index, cap, plan = fixture(tmp_path)
    cap2 = tmp_path / "revision"
    shutil.copytree(cap, cap2)
    body = json.loads((cap2 / "response.bin").read_text())
    for row in body["data"]["attributes"]["ohlcv_list"]:
        row[-1] += 1
    (cap2 / "response.bin").write_text(json.dumps(body))
    receipt = json.loads((cap2 / "receipt.json").read_text())
    receipt["response_sha256"] = sha(cap2 / "response.bin")
    (cap2 / "receipt.json").write_text(json.dumps(receipt))
    (cap2 / "manifest.json").unlink()
    seal(cap2)
    real = continuous.qualify_capture
    done = []

    def during_read(path, pool):
        if not done:
            with connect(index) as writer:
                ingest(writer, cap2, pool["pool"])
            done.append(True)
        return real(path, pool)

    monkeypatch.setattr(continuous, "qualify_capture", during_read)
    first = export(index, tmp_path / "first", plan)
    assert first["counts"]["indexed_captures"] == 1
    second = export(index, tmp_path / "second", plan)
    assert second["counts"]["indexed_captures"] == 2
    assert second["counts"]["equal_receipt_observations"] == 60
    assert second["counts"]["revised_observations"] == 60
    rows = list(source(dict(path=str(tmp_path / "second"), pin=second["pin"]), 10**18))
    assert {r["capture_sha"] for r in rows} == {
        min(sha(cap / "manifest.json"), sha(cap2 / "manifest.json"))
    }


def test_quarantined_evidence_remains_verifiable(tmp_path):
    index, _, plan = fixture(tmp_path, wrong=True)
    out = tmp_path / "export"
    report = export(index, out, plan)
    reference = dict(path=str(out), pin=report["pin"])
    cap = next((out / "captures").iterdir())
    (cap / "response.bin").write_text("lost quarantine evidence")
    with pytest.raises(ValueError, match="checksum"):
        list(source(reference, 10**18))


def test_missing_index_cleans_staging(tmp_path):
    _, _, plan = fixture(tmp_path)
    with pytest.raises(sqlite3.OperationalError):
        export(tmp_path / "missing.sqlite", tmp_path / "out", plan)
    assert not list(tmp_path.glob(".export-*"))


def test_interrupted_export_is_atomic_and_can_retry(tmp_path, monkeypatch):
    from memecoin_experience import continuous

    index, _, plan = fixture(tmp_path)
    out = tmp_path / "export"
    original = continuous._copy_capture

    def crash(*args):
        original(*args)
        raise RuntimeError("synthetic crash during export")

    with monkeypatch.context() as patch:
        patch.setattr(continuous, "_copy_capture", crash)
        with pytest.raises(RuntimeError):
            export(index, out, plan)
    assert not out.exists()
    assert export(index, out, plan)["bars"] == 60


def test_null_mint_metadata_quarantines_one_capture(tmp_path):
    from memecoin_ml.data import sha
    from memecoin_registry.contracts import seal

    cap, birth = capture(tmp_path)
    body = json.loads((cap / "response.bin").read_text())
    body["meta"] = None
    (cap / "response.bin").write_text(json.dumps(body))
    receipt = json.loads((cap / "receipt.json").read_text())
    receipt["response_sha256"] = sha(cap / "response.bin")
    (cap / "receipt.json").write_text(json.dumps(receipt))
    (cap / "manifest.json").unlink()
    seal(cap)
    index = tmp_path / "index.sqlite"
    with connect(index) as db:
        ingest(db, cap, birth["pool"])
    plan = dict(
        role="train",
        cutoff_ns=10**18,
        start_s=300000,
        end_s=318000,
        selection="synthetic",
        pools=[{k: birth[k] for k in ("pool", "base_mint", "quote_mint")}],
    )
    report = export(index, tmp_path / "export", plan)
    assert report["counts"]["mint_mismatch"] == 1
    assert report["bars"] == 0


def test_per_entity_intervals_preserve_denominator_and_payload_boundary(tmp_path):
    index, _, plan = fixture(tmp_path)
    entity = plan["pools"][0]["pool"]
    plan["pools"].append(dict(pool="unsupported", base_mint="D", quote_mint="E"))
    plan["intervals"] = {
        entity: [[300300, 300900], [303000, 303600]],
        "unsupported": [[303000, 303600]],
    }
    report = export(index, tmp_path / "out", plan)
    assert report["bars"] == 4
    assert sum(c["expected_slots"] for c in report["coverage"]) == 6
    assert sum(c["missing_slots"] for c in report["coverage"]) == 2
    rows = list(source(dict(path=str(tmp_path / "out"), pin=report["pin"]), 10**18))
    assert [r["open_s"] for r in rows] == [300300, 300600, 303000, 303300]
    plan["intervals"][entity] = [[300300, 300900], [300600, 301200]]
    with pytest.raises(ValueError, match="intervals"):
        export(index, tmp_path / "bad", plan)


@pytest.mark.parametrize("payload", [b"not json", b"[]", b'{"meta":{},"data":null}'])
def test_malformed_and_failed_captures_are_quarantined(tmp_path, payload):
    from memecoin_experience.continuous import qualify_capture
    from memecoin_experience.storage import atomic_json, seal_files

    _, cap, plan = fixture(tmp_path)
    (cap / "manifest.json").unlink()
    (cap / "response.bin").write_bytes(payload)
    receipt = json.loads((cap / "receipt.json").read_text())
    receipt["response_sha256"] = file_hash(cap / "response.bin")
    atomic_json(cap / "receipt.json", receipt, replace=True)
    seal_files(cap)
    rows, reason = qualify_capture(cap, plan["pools"][0])
    assert not rows and reason in {"malformed_payload", "mint_mismatch"}
    (cap / "manifest.json").unlink()
    receipt["http_status"] = 404
    atomic_json(cap / "receipt.json", receipt, replace=True)
    seal_files(cap)
    assert qualify_capture(cap, plan["pools"][0]) == ([], "http_failure")
