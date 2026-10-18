import io
import json
import sqlite3
import urllib.error

import pytest
from test_population_acquisition import plan

from memecoin_experience.continuous import source as source_rows
from memecoin_experience.storage import atomic_json
from memecoin_population.acquisition import capture
from memecoin_population.corpus import roles, source


def campaign(tmp_path):
    p = plan()
    p.update(created_ns=10**12, max_requests=3)
    p["cases"] = [
        dict(pool="synthetic", base_mint="B", quote_mint="Q", role="train"),
        dict(pool="missing", base_mint="C", quote_mint="Q", role="train"),
        dict(pool="evaluation", base_mint="D", quote_mint="Q", role="research_evaluation"),
    ]
    for case in p["cases"]:
        case.update(event_s=0, month="synthetic")
    p["jobs"] = [dict(pool=c["pool"], start_s=1, end_s=601) for c in p["cases"]]
    atomic_json(tmp_path / "plan.json", p)
    clock = iter(range(10**12, 10**12 + 100 * 10**9, 4 * 10**9))

    class Response(io.BytesIO):
        status = 200

    def opener(request, **kwargs):
        if "/missing/" in request.full_url:
            raise urllib.error.HTTPError(request.full_url, 404, "gone", {}, io.BytesIO(b"{}"))
        if "/evaluation/" in request.full_url:
            return Response(b"must not be parsed by train exporter")
        return Response(
            json.dumps(
                dict(
                    meta=dict(base=dict(address="B"), quote=dict(address="Q")),
                    data=dict(
                        attributes=dict(ohlcv_list=[[t, 1, 1, 1, 1, 5] for t in (0, 300, 600, 900)])
                    ),
                )
            ).encode()
        )

    capture(
        tmp_path / "capture",
        p,
        guard=lambda: None,
        opener=opener,
        clock=lambda: next(clock),
        sleep=lambda n: None,
    )
    return p


def test_complete_source_retains_failed_markets_and_does_not_read_other_role(tmp_path):
    p = campaign(tmp_path)
    # Deleting the other role's payload must not affect train qualification.
    (tmp_path / "capture/requests/0000002/response.bin").unlink()
    out = tmp_path / "train"
    report = source(tmp_path, out, "train")
    assert report["enrollment"] == 2
    assert report["counts"]["requests/unsupported"] == 1
    assert report["counts"]["qualification/http_failure"] == 1
    rows = list(
        source_rows(
            dict(kind="continuous", path=str(out / "source"), pin=report["source_pin"]),
            report["cutoff_ns"],
        )
    )
    assert [r["open_s"] for r in rows] == [300, 600]
    assert source(tmp_path, out, "train") == report
    exported = json.loads((out / "source/meta/source.json").read_text())
    assert sum(r["expected_slots"] for r in exported["coverage"]) == 4
    assert sum(r["missing_slots"] for r in exported["coverage"]) == 2
    p["cases"][2]["base_mint"] = "B"
    with pytest.raises(ValueError, match="mint_overlap"):
        roles(p)


def test_unresolved_role_and_plan_mutation_fail_before_payload(tmp_path):
    p = campaign(tmp_path)
    with sqlite3.connect(tmp_path / "capture/requests.sqlite") as db:
        db.execute("update requests set status='reserved' where ordinal=0")
    with pytest.raises(ValueError, match="incomplete_role"):
        source(tmp_path, tmp_path / "out", "train")
    p["spacing_s"] = 5
    atomic_json(tmp_path / "plan.json", p, replace=True)
    with pytest.raises(ValueError, match="plan_binding"):
        source(tmp_path, tmp_path / "out", "train")


def test_unplanned_enrollment_and_birth_schema_rejected(tmp_path):
    p = campaign(tmp_path)
    p["cases"].append(
        dict(
            pool="orphan", base_mint="Z", quote_mint="Q", role="train", event_s=0, month="synthetic"
        )
    )
    with pytest.raises(ValueError, match="unplanned_enrollment"):
        roles(p)
    p["cases"].pop()
    del p["cases"][0]["event_s"]
    with pytest.raises(ValueError, match="birth_metadata"):
        roles(p)


def test_cached_export_verifies_its_portable_capture_closure(tmp_path):
    campaign(tmp_path)
    out = tmp_path / "out"
    source(tmp_path, out, "train")
    cap = next((out / "source/captures").iterdir())
    (cap / "response.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        source(tmp_path, out, "train")
