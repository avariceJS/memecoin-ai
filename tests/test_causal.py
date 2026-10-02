"""Synthetic timing/perturbations around a real checkpoint; no fabricated live evidence."""

import base64
import copy
import json
import struct
from pathlib import Path

import pytest

from memecoin_ai.causal import (
    CausalBook,
    observation_targets,
    observed_checkpoint,
    publish_features,
)
from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.forward import collect_forward, verify_forward
from memecoin_ai.sources import Capture, HTTPSourceError, ReadOnlyRPC

BASE = Capture.load(Path(__file__).parent / "fixtures/token2022/checkpoint-2")
POOL = json.loads(BASE.request_json)["params"][0][0]
NS = 10**9


def observation(t=10, slot=100, price_multiplier=1):
    obs = observed_checkpoint(BASE, POOL, BASE.received_ns)
    obs.update(
        available_ns=t * NS, received_ns=t * NS - NS, slot=slot, chain_timestamp_seconds=t - 2
    )
    obs["economic"]["pool"]["open_time"] = 0
    obs["economic"]["effective_reserves"][1] *= price_multiplier
    obs["response_sha256"] = digest(canonical([t, slot, price_multiplier]).encode())
    return obs


def book():
    return CausalBook(
        [POOL], max_receipt_age_ns=25 * NS, max_chain_age_ns=45 * NS, max_gap_ns=25 * NS
    )


def test_future_append_and_mutation_leave_prior_features_unchanged():
    outputs = []
    for multiplier in (1, 10000):
        b = book()
        b.ingest(observation(10, 100))
        before = publish_features(b.features(11 * NS), 11 * NS + 1)
        b.ingest(observation(20, 101, multiplier))
        b.features(21 * NS)
        outputs.append(before)
        with pytest.raises(ValidationError, match="predates"):
            b.features(11 * NS)
    assert outputs[0] == outputs[1]


def test_receipt_and_feature_computation_delay_cannot_leak():
    with pytest.raises(ValidationError, match="precedes receipt"):
        observed_checkpoint(BASE, POOL, BASE.received_ns - 1)
    b = book()
    b.ingest(observation())
    rows = b.features(11 * NS)
    with pytest.raises(ValidationError, match="publication"):
        publish_features(rows, 10 * NS)
    published = publish_features(rows, 12 * NS)
    assert published[0]["as_of_ns"] == 11 * NS
    assert published[0]["decision_ns"] == published[0]["feature_available_ns"] == 12 * NS
    with pytest.raises(ValidationError, match="only validated"):
        b.ingest(dict(kind="cpmm_economic_state_tape", historical_available_ns=None))


def test_duplicate_bank_does_not_refresh_freshness_or_warmup():
    b = book()
    original = observation()
    b.ingest(original)
    duplicate = copy.deepcopy(original)
    duplicate.update(available_ns=30 * NS, received_ns=29 * NS)
    assert b.ingest(duplicate) == "duplicate"
    row = b.features(40 * NS)[0]
    assert not row["usable"] and "stale_receipt" in row["reasons"]
    assert row["input_available_ns"] == 10 * NS


@pytest.mark.parametrize("mode", ["explicit", "silent", "regressed", "conflict"])
def test_gap_resets_return_history_and_recovery_requires_warmup(mode):
    b = book()
    b.ingest(observation(10, 100))
    b.ingest(observation(20, 101))
    assert b.features(21 * NS)[0]["usable"]
    if mode == "explicit":
        b.gap(POOL, 22 * NS, "timeout")
    elif mode == "regressed":
        assert b.ingest(observation(22, 99)) == "regressed_slot"
    elif mode == "conflict":
        assert b.ingest(observation(22, 101, 2)) == "conflicting_same_bank"
    b.ingest(observation(60 if mode == "silent" else 30, 102))
    row = b.features((61 if mode == "silent" else 31) * NS)[0]
    assert row["segment"] == 1 and row["missing"]["log_price_change"]
    assert row["reasons"] == ["history_warmup"]


def test_future_chain_clock_masks_features_without_clamping_age():
    b = book()
    obs = observation()
    obs["chain_timestamp_seconds"] = 100
    b.ingest(obs)
    row = b.features(11 * NS)[0]
    assert "chain_clock_ahead" in row["reasons"]
    assert all(row["missing"].values())


def feature_sequence():
    b = book()
    rows = []
    for i, t in enumerate((10, 20, 30, 40, 50, 60)):
        b.ingest(observation(t, 100 + i, i + 1))
        rows.extend(publish_features(b.features(t * NS), t * NS + 1))
    return rows


def test_labels_maturity_boundary_purge_and_right_censoring():
    rows = feature_sequence()
    labels = observation_targets(
        rows, horizon_ns=20 * NS, tolerance_ns=NS, calibration_end_ns=40 * NS
    )
    assert labels[0]["censor_reason"] == "invalid_start_observation"
    assert labels[1]["partition"] == "purged"
    assert labels[1]["target_available_ns"] == rows[3]["feature_available_ns"]
    assert labels[-1]["censor_reason"] == labels[-2]["censor_reason"] == "right_censored"
    assert labels[3]["partition"] == "engineering_evaluation"
    # Appending future data may mature targets, never alter earlier feature rows.
    original = copy.deepcopy(rows)
    observation_targets(rows, horizon_ns=10 * NS, tolerance_ns=NS, calibration_end_ns=40 * NS)
    assert rows == original


def test_gap_censors_target_instead_of_interpolating_future_price():
    rows = feature_sequence()
    rows[2]["usable"] = False
    targets = observation_targets(
        rows, horizon_ns=20 * NS, tolerance_ns=NS, calibration_end_ns=40 * NS
    )
    assert targets[1]["censor_reason"] == "observation_gap" and targets[1]["value"] is None


class FakeTime:
    def __init__(self):
        self.now = BASE.received_ns + 100 * NS
        self.calls = 0

    def clock(self):
        self.now += 100000
        return self.now

    def mono(self):
        return self.now / NS

    def sleep(self, seconds):
        self.now += int(seconds * NS)

    def transport(self, endpoint, request, timeout, limit):
        self.calls += 1
        payload = json.loads(BASE.response)
        slot = payload["result"]["context"]["slot"] + self.calls
        payload["result"]["context"]["slot"] = slot
        data = bytearray(base64.b64decode(payload["result"]["value"][6]["data"][0]))
        struct.pack_into("<Q", data, 0, slot)
        struct.pack_into("<q", data, 32, self.now // NS - 2)
        payload["result"]["value"][6]["data"][0] = base64.b64encode(data).decode()
        return canonical(payload).encode()


def run_forward(path, fail=False):
    t = FakeTime()

    def transport(*args):
        if fail:
            raise HTTPSourceError(429)
        return t.transport(*args)

    client = ReadOnlyRPC(
        "https://example.invalid", "synthetic-forward", transport=transport, clock=t.clock
    )
    return collect_forward(
        client,
        [BASE],
        path,
        rounds=4,
        interval_seconds=10,
        clock=t.clock,
        monotonic=t.mono,
        sleep=t.sleep,
    )


def test_online_offline_identical_and_rehashed_leakage_rejected(tmp_path):
    root = tmp_path / "corpus"
    report = run_forward(root)
    assert report["complete"] and report["requests"] == 4
    assert verify_forward(root) == report
    frames = [json.loads(s) for s in (root / "frames.jsonl").read_text().splitlines()]
    frames[0]["rows"][0]["values"]["log_price_1_per_0"] = (
        frames[-1]["rows"][0]["values"]["log_price_1_per_0"] + 1
    )
    (root / "frames.jsonl").write_text("".join(canonical(r) + "\n" for r in frames))
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["files"]["frames.jsonl"] = digest((root / "frames.jsonl").read_bytes())
    (root / "manifest.json").write_text(canonical(manifest))
    with pytest.raises(ValidationError, match="online/offline"):
        verify_forward(root)


def test_access_failure_is_preserved_and_series_stops_without_retry(tmp_path):
    root = tmp_path / "failed"
    report = run_forward(root, fail=True)
    assert report["requests"] == 1 and not report["complete"]
    attempt = json.loads((root / "attempts.jsonl").read_text())
    assert attempt["http_status"] == 429 and attempt["category"] == "http_error"
    assert verify_forward(root) == report
    with pytest.raises(FileExistsError):
        run_forward(root)


def test_targets_reject_late_input_even_when_feature_publication_claims_to_be_timely():
    rows = feature_sequence()
    rows[1]["input_available_ns"] = rows[1]["as_of_ns"] + 1
    with pytest.raises(ValidationError, match="input unavailable"):
        observation_targets(rows, horizon_ns=20 * NS, tolerance_ns=NS, calibration_end_ns=40 * NS)


def test_unusable_snapshot_cannot_seed_return_history_after_recovery():
    b = book()
    invalid = observation()
    invalid["chain_timestamp_seconds"] = 100
    b.ingest(invalid)
    b.ingest(observation(20, 101))
    row = b.features(21 * NS)[0]
    assert row["reasons"] == ["history_warmup"] and row["segment"] == 1
