from __future__ import annotations

import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools.brain_senses_external_writer_v1 import DescriptorWriter
from tools.brain_senses_sparse_observation_schema_v1 import (
    NS_PER_SECOND,
    SparseReferenceError,
    canonical,
    digest,
)
from tools.brain_senses_sparse_observation_v1 import (
    ROLE_EXPECTED,
    ROLE_SOURCE_PINS,
    _campaign_role_summary,
    compile_campaign,
    compile_role,
    current_code_hashes,
    publish_role,
    serialize_role,
    verify_role_artifact,
    verify_role_from_writer,
    write_create_only,
)

SOURCE_PINS = {
    "campaign_receipt_sha256": "b976178f5fdfeacbf9f46f5e6ed4e88e9054f918a9ba0315a433539758551086",
    "source_pin": ROLE_SOURCE_PINS["train"],
    "campaign_source_pin": "c" * 64,
    "meta_manifest_sha256": "d" * 64,
    "rows_sha256": "e" * 64,
    "captures_index_sha256": "f" * 64,
    "accounting_manifest_sha256": "1" * 64,
    "code_sha256": current_code_hashes(),
}


def _capture_id(entity: str, open_s: int) -> str:
    return digest(f"{entity}:{open_s}".encode())


def _make_case(
    times: dict[str, list[int]],
    *,
    intervals: dict[str, list[list[int]]] | None = None,
    availability: dict[tuple[str, int], int | None] | None = None,
    request_statuses: dict[str, tuple[str, str]] | None = None,
) -> dict:
    entity_ids = sorted(times)
    if intervals is None:
        intervals = {
            entity: [[0, ((max(values) + 1) if values else 1) * 300]]
            for entity, values in times.items()
        }
    source_rows = []
    capture_rows = []
    for entity in entity_ids:
        for open_s in sorted(times[entity]):
            capture = _capture_id(entity, open_s)
            received = (
                availability[(entity, open_s)]
                if availability and (entity, open_s) in availability
                else open_s * NS_PER_SECOND + 300 * NS_PER_SECOND + 1
            )
            source_rows.append(
                {
                    "pool": entity,
                    "open_s": open_s,
                    "available_ns": received,
                    "capture_sha": capture,
                    "lane": "historical",
                    "ohlcv": [1, 2, 0.5, 1.5, 4],
                }
            )
            capture_rows.append({"pool": entity, "pin": capture, "reason": "qualified"})
    pools = [
        {"pool": entity, "base_mint": f"base-{entity}", "quote_mint": f"quote-{entity}"}
        for entity in entity_ids
    ]
    support_coverage = []
    for entity in entity_ids:
        slots = sum((end - start) // 300 for start, end in intervals[entity])
        support_coverage.append(
            {
                "pool": entity,
                "bars": len(times[entity]),
                "expected_slots": slots,
                "missing_slots": slots - len(times[entity]),
            }
        )
    requests = []
    for ordinal, entity in enumerate(entity_ids):
        acquisition, qualification = (request_statuses or {}).get(entity, ("received", "qualified"))
        start_s = intervals[entity][0][0]
        end_s = intervals[entity][-1][1]
        requests.append(
            {
                "acquisition_status": acquisition,
                "bars": len(times[entity]),
                "capture": None
                if qualification in {"http_failure", "unsupported_namespace"}
                else _capture_id(entity, max(times[entity], default=0)),
                "job": {"end_s": end_s, "pool": entity, "start_s": start_s},
                "ordinal": ordinal,
                "qualification": qualification,
            }
        )
    source = {
        "version": "continuous-qualified-source-v1",
        "namespace": "geckoterminal/usd/base/5min/first-qualified-receipt",
        "plan": {
            "role": "train",
            "pools": pools,
            "intervals": intervals,
        },
        "bars": len(source_rows),
        "coverage": support_coverage,
        "counts": {"revised_observations": 0, "duplicate_observations": 0},
        "selection_policy": "first_qualified_receipt_then_capture_sha",
        "historical_online_availability_known": False,
    }
    return {
        "source": source,
        "rows": [canonical(row).rstrip(b"\n") for row in source_rows],
        "captures": [canonical(row).rstrip(b"\n") for row in capture_rows],
        "requests": requests,
    }


def _compile(case: dict, *, role: str = "train") -> dict:
    case["source"]["plan"]["role"] = role
    return compile_role(
        role,
        case["source"],
        case["rows"],
        case["captures"],
        case["requests"],
        source_pins=dict(SOURCE_PINS),
    )


def _files(compiled: dict) -> dict[str, bytes]:
    return serialize_role(compiled)


def test_short_disjoint_and_long_gaps_are_sparse_and_explicit() -> None:
    case = _make_case(
        {"pool-a": [0, 300, 1800, 2700]},
        intervals={"pool-a": [[0, 600], [1800, 3000]]},
    )
    compiled = _compile(case)
    files = _files(compiled)
    summary = verify_role_artifact(files, expected_role="train")
    contexts = [__import__("json").loads(line) for line in files["contexts.jsonl"].splitlines()]
    last = contexts[-1]
    assert summary["qualified_bars"] == 4
    assert last["history_length"] == 3
    assert last["elapsed_grid_steps"] == [1, 5, 3]
    assert last["unknown_gap_mask"] == [False, True, True]
    assert last["point_in_time_eligible"] is False
    assert last["decision_ns"] is None


def test_full_bar_availability_is_null_when_receipt_precedes_bar_end() -> None:
    case = _make_case({"pool-a": [0]}, availability={("pool-a", 0): 10})
    compiled = _compile(case)
    row = compiled["observations"][0]
    assert row["full_bar_receipt_available_ns"] is None
    assert row["availability_status"] == "full_bar_availability_unproved"
    assert row["units"]["volume_unit"] is None
    assert row["units"]["volume_unit_status"] == "provider_reported_unverified"
    assert "ohlcv" not in row


@given(st.lists(st.integers(min_value=1, max_value=25), min_size=1, max_size=45))
@settings(max_examples=40, deadline=None)
def test_generated_history_steps_are_exact_and_never_filled(gaps: list[int]) -> None:
    opens = [0]
    for gap in gaps:
        opens.append(opens[-1] + gap * 300)
    case = _make_case({"pool-a": opens})
    files = _files(_compile(case))
    contexts = [__import__("json").loads(line) for line in files["contexts.jsonl"].splitlines()]
    last = contexts[-1]
    expected = gaps[-32:]
    assert last["elapsed_grid_steps"] == expected
    assert last["unknown_gap_mask"] == [step > 1 for step in expected]
    assert all(isinstance(index, int) for index in last["history_indices"])
    assert len(last["history_indices"]) <= 32


def test_missing_failed_and_unsupported_enrollments_are_preserved() -> None:
    case = _make_case(
        {"pool-observed": [0], "pool-failed": [], "pool-unsupported": []},
        intervals={
            "pool-observed": [[0, 300]],
            "pool-failed": [[0, 300]],
            "pool-unsupported": [[0, 300]],
        },
        request_statuses={
            "pool-failed": ("received", "http_failure"),
            "pool-unsupported": ("received", "unsupported_namespace"),
        },
    )
    compiled = _compile(case)
    by_entity = {row["entity_id"]: row["status"] for row in compiled["enrollment"]}
    assert by_entity == {
        "pool-failed": "failed_no_observation",
        "pool-observed": "has_qualified_observations",
        "pool-unsupported": "unsupported_no_observation",
    }
    assert compiled["summary"]["failed_request_interval_count"] == 1
    assert compiled["summary"]["unsupported_request_interval_count"] == 1
    assert compiled["summary"]["complete_support_claims"] == 0


def test_duplicate_open_revision_is_rejected_instead_of_overwriting_first_receipt() -> None:
    case = _make_case({"pool-a": [0]})
    case["rows"].append(case["rows"][0])
    with pytest.raises(SparseReferenceError, match="duplicate_bar_open"):
        _compile(case)


def test_overlapping_support_intervals_are_rejected() -> None:
    case = _make_case(
        {"pool-a": [0]},
        intervals={"pool-a": [[0, 600], [300, 900]]},
    )
    with pytest.raises(SparseReferenceError, match="support_interval_invalid"):
        _compile(case)


def test_future_or_cross_role_context_reference_is_rejected() -> None:
    files = _files(_compile(_make_case({"pool-a": [0, 300]})))
    contexts = [__import__("json").loads(line) for line in files["contexts.jsonl"].splitlines()]
    contexts[0]["history_indices"] = [1]
    files["contexts.jsonl"] = b"".join(canonical(row) for row in contexts)
    with pytest.raises(SparseReferenceError):
        verify_role_artifact(files, expected_role="train")
    with pytest.raises(SparseReferenceError, match="role_summary_identity"):
        verify_role_artifact(
            _files(_compile(_make_case({"pool-a": [0]}))), expected_role="research_evaluation"
        )


def test_verifier_rejects_observations_outside_declared_support() -> None:
    files = _files(_compile(_make_case({"pool-a": [0, 300]})))
    support = [
        __import__("json").loads(line) for line in files["support-intervals.jsonl"].splitlines()
    ]
    support[0]["start_ns"] = 300 * NS_PER_SECOND
    support[0]["end_ns"] = 90_600 * NS_PER_SECOND
    files["support-intervals.jsonl"] = b"".join(canonical(row) for row in support)
    with pytest.raises(SparseReferenceError, match="observation_outside_support_interval"):
        verify_role_artifact(files, expected_role="train")


def test_verifier_recomputes_request_and_enrollment_denominators() -> None:
    files = _files(
        _compile(
            _make_case(
                {"pool-a": [0], "pool-failed": []},
                intervals={"pool-a": [[0, 300]], "pool-failed": [[0, 300]]},
                request_statuses={"pool-failed": ("received", "http_failure")},
            )
        )
    )
    enrollment = [__import__("json").loads(line) for line in files["enrollment.jsonl"].splitlines()]
    enrollment[0]["request_status_counts"] = {"unknown": 4}
    files["enrollment.jsonl"] = b"".join(canonical(row) for row in enrollment)
    with pytest.raises(SparseReferenceError, match="enrollment_request_counts"):
        verify_role_artifact(files, expected_role="train")


def test_verifier_rejects_changed_implementation_pin() -> None:
    files = _files(_compile(_make_case({"pool-a": [0]})))
    summary = __import__("json").loads(files["summary.json"])
    summary["code_sha256"]["descriptor_writer"] = "0" * 64
    files["summary.json"] = canonical(summary)
    with pytest.raises(SparseReferenceError, match="role_code_pin_mismatch"):
        verify_role_artifact(files, expected_role="train")


def test_campaign_reader_opens_only_role_manifest_and_summary() -> None:
    class SummaryOnlyReader:
        def __init__(self, files: dict[str, bytes]):
            self.files = files
            self.reads: list[str] = []

        def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
            self.reads.append(path)
            raw = self.files[path]
            assert len(raw) <= max_bytes
            return raw

    compiled = _compile(_make_case({"pool-a": [0]}))
    summary = compiled["summary"]
    summary["source_pins"]["source_pin"] = ROLE_SOURCE_PINS["train"]
    names = {
        "observations.jsonl",
        "contexts.jsonl",
        "support-intervals.jsonl",
        "request-windows.jsonl",
        "enrollment.jsonl",
        "summary.json",
    }
    manifest = {
        "schema": summary["schema"],
        "schema_version": summary["schema_version"],
        "role": "train",
        "files": {name: "0" * 64 for name in names} | {"summary.json": digest(canonical(summary))},
        "source_pins": summary["source_pins"],
        "code_sha256": summary["code_sha256"],
    }
    reader = SummaryOnlyReader(
        {
            "campaign/train/manifest.json": canonical(manifest),
            "campaign/train/summary.json": canonical(summary),
        }
    )
    loaded, _binding = _campaign_role_summary(reader, "campaign", "train")
    assert loaded == summary
    assert reader.reads == ["campaign/train/manifest.json", "campaign/train/summary.json"]


def test_campaign_reconciles_only_fixed_role_summaries_and_baselines() -> None:
    def summary(role: str, source_pin: str, entities: int, bars: int, baseline: int) -> dict:
        value = _compile(_make_case({"pool-a": [0]}))["summary"]
        value["role"] = role
        value["source_pins"]["source_pin"] = source_pin
        value["code_sha256"] = dict(SOURCE_PINS["code_sha256"])
        value["enrollment_entities"] = entities
        value["qualified_bars"] = bars
        value["origins"] = bars
        value["entities_with_qualified_bars"] = entities
        value["entities_without_qualified_bars"] = 0
        value["declared_support_interval_count"] = entities
        value["declared_support_slots"] = ROLE_EXPECTED[role]["support_slots"]
        value["qualified_observed_bar_slots"] = bars
        value["unobserved_declared_slots"] = ROLE_EXPECTED[role]["support_slots"] - bars
        value["consecutive_32_including_origin"] = baseline
        value["entities_with_consecutive_32"] = ROLE_EXPECTED[role]["consecutive32_entities"]
        value["context_history_length_histogram"] = {str(i): int(i == 0) * bars for i in range(33)}
        return value

    campaign = compile_campaign(
        summary("train", ROLE_SOURCE_PINS["train"], 170, 38_705, 8_792),
        summary("research_evaluation", ROLE_SOURCE_PINS["research_evaluation"], 137, 14_375, 6_465),
    )
    assert campaign["totals"]["qualified_bars"] == 53_080
    assert campaign["totals"]["enrollment_entities"] == 307
    assert campaign["totals"]["consecutive_32_including_origin"] == 15_257
    assert campaign["point_in_time_eligible"] is False
    assert campaign["decision_ns"] is None


def test_campaign_rejects_role_swaps_and_unpinned_source() -> None:
    train = {"schema": "brain-senses-sparse-observation-role-v1", "role": "research_evaluation"}
    with pytest.raises(SparseReferenceError, match="campaign_role_identity"):
        compile_campaign(train, train)


def test_descriptor_publication_is_create_only_idempotent_and_verifiable(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        compiled = _compile(_make_case({"pool-a": [0, 300]}))
        with DescriptorWriter(root_fd) as writer:
            first = publish_role(writer, "artifact", compiled)
            second = publish_role(writer, "artifact", compiled)
            assert first["manifest_sha256"] == second["manifest_sha256"]
            assert verify_role_from_writer(writer, "artifact/train", "train")["qualified_bars"] == 2
            with pytest.raises(SparseReferenceError, match="immutable_output_collision"):
                write_create_only(writer, "artifact/train/summary.json", b"different\n")
    finally:
        os.close(root_fd)


def test_source_role_baseline_constants_are_pinned() -> None:
    assert ROLE_EXPECTED["train"]["bars"] + ROLE_EXPECTED["research_evaluation"]["bars"] == 53_080
    assert (
        ROLE_EXPECTED["train"]["consecutive32"]
        + ROLE_EXPECTED["research_evaluation"]["consecutive32"]
        == 15_257
    )
