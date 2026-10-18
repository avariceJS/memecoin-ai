"""Synthetic metadata only: no protected corpus, network, training or operator changes."""

import copy
from pathlib import Path

import pytest

from memecoin_curriculum.benchmark import age_bucket, cell_support, readiness
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, seal
from memecoin_runner.ledger import publish


def config():
    result = read(Path(__file__).resolve().parents[2] / "configs/brain-benchmark-v1.json")
    result["required_support"] = [
        dict(lane="auto", seen="any", age="any", mints=1, days=1, windows=1)
    ]
    return result


def packet(purpose="development", start=86400):
    rows = []
    for horizon in (5, 15):
        origin = start + 5000
        rows.append(
            dict(
                lane="auto",
                pool="synthetic-pool",
                mint="synthetic-mint",
                origin_s=origin,
                horizon_minutes=horizon,
                seen="unseen",
                status="scored",
                input_start_s=origin - 4500,
                maturity_s=origin + horizon * 60,
                input_available_ns=origin * 10**9,
                predicted_ns=(origin + 1) * 10**9,
                target_available_ns=(origin + horizon * 60 + 1) * 10**9,
                birth_s=start - 1000,
                age="lt1d",
            )
        )
    return dict(
        purpose=purpose,
        source_role="research",
        source_manifest_shas=["synthetic-source"],
        labels_exposed=False,
        used_for_fit=False,
        start_s=start,
        end_s=start + 86400,
        cutoff_ns=(start + 86400) * 10**9,
        enrollment_frozen_ns=(start - 1) * 10**9,
        models_frozen_ns=(start - 2) * 10**9,
        calibration_frozen_ns=(start - 1) * 10**9,
        reference_train_mints=[],
        reference_train_mints_sha=digest([]),
        rows=rows,
    )


def assignment(tmp_path, value):
    root = tmp_path / value["purpose"]
    root.mkdir()
    publish(root / "benchmark-input.json", value)
    seal(root)
    return dict(path=str(root), manifest_sha=sha(root / "manifest.json"))


def test_no_data_is_not_ready():
    result = readiness(config(), [])
    assert not result["support_ready"]
    assert len(result["reasons"]) == 3
    assert not result["admission_ready"]


def test_complete_synthetic_support_never_admits(tmp_path):
    refs = [
        assignment(tmp_path, packet(role, (i + 1) * 86400))
        for i, role in enumerate(config()["purposes"])
    ]
    result = readiness(config(), refs)
    assert result["support_ready"]
    assert result["raw_receipt_verification_required"] and not result["admission_ready"]


@pytest.mark.parametrize(
    "key,value,error",
    [
        ("source_role", "reserved_holdout", "protected"),
        ("labels_exposed", True, "previously_used"),
        ("used_for_fit", True, "previously_used"),
        ("enrollment_frozen_ns", 10**30, "window_or_enrollment"),
        ("cutoff_ns", 0, "window_incomplete"),
    ],
)
def test_invalid_role_or_clock(tmp_path, key, value, error):
    p = packet()
    p[key] = value
    with pytest.raises(ValueError, match=error):
        readiness(config(), [assignment(tmp_path, p)])


def test_cannot_repackage_exposed_dataset(tmp_path):
    p = packet()
    p["source_manifest_shas"] = [config()["exposure_registry"][0]["manifest_sha"]]
    with pytest.raises(ValueError, match="exposed_source"):
        readiness(config(), [assignment(tmp_path, p)])


@pytest.mark.parametrize(
    "key,value,error",
    [
        ("seen", "seen", "seen_membership"),
        ("input_start_s", 0, "dependency_interval"),
        ("maturity_s", 10**10, "dependency_interval"),
        ("target_available_ns", 0, "availability"),
        ("age", "ge7d", "age"),
    ],
)
def test_row_guards(tmp_path, key, value, error):
    p = packet()
    p["rows"][0][key] = value
    with pytest.raises(ValueError, match=error):
        readiness(config(), [assignment(tmp_path, p)])


def test_future_replay_is_not_prospective(tmp_path):
    p = packet("prospective")
    p["rows"][0]["predicted_ns"] = p["rows"][0]["maturity_s"] * 10**9
    with pytest.raises(ValueError, match="backdated_forecast"):
        readiness(config(), [assignment(tmp_path, p)])


def test_late_freeze(tmp_path):
    p = packet("prospective")
    p["models_frozen_ns"] = p["start_s"] * 10**9
    with pytest.raises(ValueError, match="future_freeze"):
        readiness(config(), [assignment(tmp_path, p)])


def test_same_origin_cannot_cross_roles(tmp_path):
    a = assignment(tmp_path, packet())
    b = assignment(tmp_path, packet("calibration"))
    with pytest.raises(ValueError, match="split_overlap"):
        readiness(config(), [a, b])


def test_poll_duplicates_rejected(tmp_path):
    p = packet()
    p["rows"].append(copy.deepcopy(p["rows"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        readiness(config(), [assignment(tmp_path, p)])


def test_nonoverlap_is_by_mint_not_pool():
    rows = packet()["rows"]
    other = dict(rows[0], pool="same-mint-another-pool")
    support = cell_support([rows[0], other])
    assert support["windows"] == 1 and support["mints"] == 1
    assert support["independent_n"] is None


def test_missing_outcomes_preserved(tmp_path):
    p = packet()
    p["rows"][0]["status"] = "missing"
    result = readiness(config(), [assignment(tmp_path, p)])
    assert result["roles"]["development"]["scheduled"] == 2
    assert result["roles"]["development"]["statuses"] == {"missing": 1, "scored": 1}
    assert not result["support_ready"]


def test_age_is_at_origin():
    assert [age_bucket(x) for x in (0, 86400, 7 * 86400)] == ["lt1d", "1to7d", "ge7d"]
    with pytest.raises(ValueError, match="future_birth"):
        age_bucket(-1)


def test_offline_development_not_mislabeled_prospective(tmp_path):
    p = packet()
    for row in p["rows"]:
        row["predicted_ns"] = row["target_available_ns"] + 1
    assert readiness(config(), [assignment(tmp_path, p)])["roles"]["development"]["scored"] == 2


def test_reference_membership_cannot_drift(tmp_path):
    a = assignment(tmp_path, packet())
    p = packet("calibration", 2 * 86400)
    p["reference_train_mints"] = ["another-synthetic-mint"]
    p["reference_train_mints_sha"] = digest(p["reference_train_mints"])
    with pytest.raises(ValueError, match="membership_changed"):
        readiness(config(), [a, assignment(tmp_path, p)])
