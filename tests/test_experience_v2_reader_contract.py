import copy
import pytest

from memecoin_experience.schema import checked_samples


def sample(availability="historical_replay", *, status="unsupported"):
    schema = dict(
        schema="brain-experience-v2",
        layout="dense-sequence-f64-v1",
        context=1,
        features=[dict(name="price", unit="USD", modality="price")],
        targets=[dict(name="return", unit="ratio", kind="endpoint_log_return")],
        representation=dict(name="synthetic", version="1", code_sha="c" * 64),
        availability=availability,
    )
    row = dict(
        id="synthetic:1", pool="synthetic", decision_ns=10, input_start_ns=5,
        input_available_ns=20,
        input_dependencies=[dict(artifact_sha="a" * 64, event_ns=5, available_ns=20)],
        values=[[1.0]], targets=[None], target_status=[status],
        target_available_ns=[None], target_maturity_ns=[20], target_captures=[[]],
        target_details=[dict(reason="origin_not_available_at_decision")],
    )
    return schema, row


@pytest.mark.parametrize(
    "reason",
    ["origin_not_available_at_decision", "no_exact_endpoint_unknown_return", "ambiguous_endpoint"],
)
def test_historical_replay_preserves_real_late_receipt_and_reason(reason):
    schema, row = sample()
    row["target_details"][0]["reason"] = reason
    assert list(checked_samples([row], schema, 30)) == [row]
    assert row["target_details"][0]["reason"] == reason


def test_point_in_time_rejects_relabelled_late_dependency():
    schema, row = sample("point_in_time")
    with pytest.raises(ValueError, match="sample_contract|dependency"):
        list(checked_samples([row], schema, 30))


@pytest.mark.parametrize("status", ["pending", "missing_future", "unsupported", "censored"])
def test_nonobserved_statuses_remain_valid_with_pending_maturity_or_reason(status):
    schema, row = sample(status=status)
    if status == "pending":
        row["target_maturity_ns"] = [40]
        row["target_details"][0]["reason"] = "fit_cutoff"
    assert list(checked_samples([row], schema, 30)) == [row]


def test_observed_price_provenance_must_agree_with_target_clock():
    schema, row = sample()
    a, b = "b" * 64, "d" * 64
    row.update(targets=[0.1], target_status=["observed"], target_available_ns=[25],
               target_maturity_ns=[20], target_captures=[[a, b]])
    row["target_details"] = [dict(reason="exact_observed_endpoint", price_provenance=dict(
        base=dict(event_ns=10, available_ns=10, evidence=[a]),
        endpoint=dict(event_ns=20, available_ns=25, evidence=[b]),
    ))]
    assert list(checked_samples([row], schema, 30)) == [row]
    forged = copy.deepcopy(row)
    forged["target_available_ns"] = [22]
    with pytest.raises(ValueError, match="provenance"):
        list(checked_samples([forged], schema, 30))


@pytest.mark.parametrize("availability", ["historical_replay", "point_in_time"])
def test_late_exact_price_base_depends_on_availability_contract(availability):
    schema, row = sample(availability)
    row.update(input_available_ns=10,
               input_dependencies=[dict(artifact_sha="a" * 64, event_ns=5, available_ns=10)],
               targets=[0.1], target_status=["observed"], target_available_ns=[25],
               target_captures=[["b" * 64, "d" * 64]])
    row["target_details"] = [dict(price_provenance=dict(
        base=dict(event_ns=10, available_ns=15, evidence=["b" * 64]),
        endpoint=dict(event_ns=20, available_ns=25, evidence=["d" * 64]),
    ))]
    if availability == "historical_replay":
        assert list(checked_samples([row], schema, 30)) == [row]
    else:
        with pytest.raises(ValueError, match="provenance"):
            list(checked_samples([row], schema, 30))


def test_metadata_only_count_censor_cannot_mature_after_sealed_cutoff():
    from memecoin_experience.schema import validate_v2_temporal_metadata

    schema, row = sample(status="censored")
    schema["targets"][0]["kind"] = "observed_process_count@10ns"
    row["target_maturity_ns"] = [40]
    row["target_details"] = [dict(lower_bound=2, lower_bound_available_ns=25)]
    with pytest.raises(ValueError, match="censor_target_maturity"):
        validate_v2_temporal_metadata(row, schema, 30)


def test_omitted_or_null_optional_details_keep_older_contract():
    schema, row = sample()
    for details in (None,):
        row["target_details"] = details
        assert list(checked_samples([row], schema, 30)) == [row]
    row.pop("target_details")
    assert list(checked_samples([row], schema, 30)) == [row]
