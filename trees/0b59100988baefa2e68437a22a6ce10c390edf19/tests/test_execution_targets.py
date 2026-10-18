"""Temporal and accounting properties of conditional probes; synthetic paths."""

import copy
import json

import pytest
from test_causal import NS, book, observation
from test_prospective import resume, setup

from memecoin_ai.causal import publish_features
from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.execution_targets import (
    condition_segment,
    execution_spec,
    export_execution_targets,
    quote,
    verify_execution_targets,
)


def sequence(n=22):
    b = book()
    rows, observations = [], {}
    for i in range(n):
        o = observation(10 + i * 5, 100 + i)
        o["economic"]["pool"]["open_time"] = 0
        b.ingest(o)
        rows.extend(publish_features(b.features((10 + i * 5) * NS), (10 + i * 5) * NS + 1))
        observations[o["response_sha256"]] = o
    return rows, observations


def targets(rows, observations):
    return condition_segment(rows, observations, calibration_end_ns=60 * NS, spec=execution_spec())


def test_size_fixed_at_decision_and_two_leg_integer_accounting():
    rows, observations = sequence()
    labels = targets(rows, observations)
    done = [t for t in labels if t["status"] == "conditional_roundtrip"]
    assert done
    for t in done:
        start = next(r for r in rows if r["sample_id"] == t["sample_id"])
        initial = observations[start["input_response_sha256"]]
        assert (
            t["gross_input"]
            == initial["economic"]["effective_reserves"][t["input_index"]]
            * t["size_ppm"]
            // 1_000_000
        )
        entry = observations[t["legs"][0]["response_sha256"]]
        end = observations[t["legs"][-1]["response_sha256"]]
        assert t["entry_net_output"] == quote(entry, t["input_index"], t["gross_input"])
        assert t["exit_net_output"] == quote(end, 1 - t["input_index"], t["entry_net_output"])
        assert t["pnl_before_network_costs"] == t["exit_net_output"] - t["gross_input"] < 0
        assert t["net_pnl"] is None and t["residual_token_units"] == 0
        assert t["legs"][0]["input_available_ns"] >= t["decision_ns"] + t["assumed_delay_ns"]
        assert t["legs"][1]["input_available_ns"] >= t["legs"][0]["decision_ns"] + t["holding_ns"]
        assert (
            t["legs"][2]["input_available_ns"]
            >= t["legs"][1]["decision_ns"] + t["assumed_delay_ns"]
        )
        if t["partition"] == "calibration":
            assert t["target_available_ns"] < 60 * NS
    assert any(t["partition"] == "purged" for t in done)


def test_carry_forward_cannot_be_used_as_fill_and_gap_censors():
    rows, observations = sequence()
    for row in rows[2:]:
        row.update(
            slot=rows[1]["slot"],
            input_available_ns=rows[1]["input_available_ns"],
            input_response_sha256=rows[1]["input_response_sha256"],
        )
    label = targets(rows, observations)[8]
    assert label["status"] == "censored" and label["reason"] == "entry_right_censored"
    rows, observations = sequence()
    rows[3]["usable"] = False
    assert any(
        t["reason"] == "entry_observation_gap" or t["reason"] == "exit_decision_observation_gap"
        for t in targets(rows, observations)
    )


def test_slippage_failure_keeps_residual_and_never_invents_net_pnl():
    rows, observations = sequence()
    baseline = next(
        t
        for t in targets(rows, observations)
        if t["status"] == "conditional_roundtrip" and t["input_index"] == 0
    )
    end = observations[baseline["legs"][-1]["response_sha256"]]
    vaults = list(end["economic"]["state"]["vaults"])
    vaults[0] //= 2
    end["economic"]["state"]["vaults"] = vaults
    changed = next(
        t
        for t in targets(rows, observations)
        if t["sample_id"] == baseline["sample_id"]
        and t["input_index"] == 0
        and t["size_ppm"] == baseline["size_ppm"]
        and t["assumed_delay_ns"] == baseline["assumed_delay_ns"]
    )
    assert changed["status"] == "exit_unfilled"
    assert changed["residual_token_units"] == changed["entry_net_output"] > 0
    assert changed["net_pnl"] is None and changed["pnl_before_network_costs"] is None


def test_future_append_changes_only_unmatured_labels():
    rows, observations = sequence()
    before = copy.deepcopy(rows)
    prefix = targets(rows[:14], observations)
    full = targets(rows, observations)

    def key(t):
        return (t["sample_id"], t["input_index"], t["size_ppm"], t["assumed_delay_ns"])

    later = {key(t): t for t in full}
    for t in prefix:
        if t["status"] != "censored":
            assert t == later[key(t)]
    assert rows == before


def test_export_reproduction_and_rehashed_tamper(tmp_path):
    root = tmp_path / "campaign"
    t, client = setup(root)
    resume(root, t, client)
    output = tmp_path / "targets"
    report = export_execution_targets(root, output)
    assert verify_execution_targets(root, output) == report
    assert report["complete_net_pnl_labels"] == 0
    rows = [json.loads(line) for line in (output / "targets.jsonl").read_text().splitlines()]
    rows[0]["net_pnl"] = 100
    (output / "targets.jsonl").write_text("".join(canonical(r) + "\n" for r in rows))
    manifest = json.loads((output / "manifest.json").read_text())
    manifest["targets.jsonl"] = digest((output / "targets.jsonl").read_bytes())
    (output / "manifest.json").write_text(canonical(manifest))
    with pytest.raises(ValidationError, match="reproduction"):
        verify_execution_targets(root, output)


def test_provenance_mismatch_fails_closed_instead_of_becoming_censor():
    rows, observations = sequence()
    observations[rows[1]["input_response_sha256"]]["available_ns"] += 1
    with pytest.raises(ValidationError, match="provenance"):
        targets(rows, observations)


def test_bad_entry_slippage_is_not_roundtrip_or_profit():
    rows, observations = sequence()
    baseline = next(
        t
        for t in targets(rows, observations)
        if t["status"] == "conditional_roundtrip" and t["input_index"] == 0
    )
    entry = observations[baseline["legs"][0]["response_sha256"]]
    vaults = list(entry["economic"]["state"]["vaults"])
    vaults[1] //= 2
    entry["economic"]["state"]["vaults"] = vaults
    changed = next(
        t
        for t in targets(rows, observations)
        if t["sample_id"] == baseline["sample_id"]
        and t["input_index"] == 0
        and t["size_ppm"] == baseline["size_ppm"]
        and t["assumed_delay_ns"] == baseline["assumed_delay_ns"]
    )
    assert changed["status"] == "entry_unfilled"
    assert changed["residual_token_units"] == 0 and changed["net_pnl"] is None
    assert changed["pnl_before_network_costs"] is None


def test_appending_campaign_requires_new_derived_artifact(tmp_path):
    root = tmp_path / "campaign"
    t, client = setup(root)
    resume(root, t, client)
    output = tmp_path / "targets"
    export_execution_targets(root, output)
    resume(root, t, client)
    with pytest.raises(ValidationError, match="parent campaign changed"):
        verify_execution_targets(root, output)
