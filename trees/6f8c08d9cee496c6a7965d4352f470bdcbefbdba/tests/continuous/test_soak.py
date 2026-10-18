from memecoin_continuous.soak import record


def test_sleep_gap_is_not_uptime(tmp_path):
    def sample(s, fresh=4):
        return dict(
            at_ns=s * 10**9, fresh_hot=fresh, source_ok=True, source_attempts=1, corpus_bars=10
        )

    record(tmp_path, sample(1000))
    record(tmp_path, sample(1030))
    r = record(tmp_path, sample(4630, 0))[0]
    assert r["span_hours"] > 1 and r["observed_hours"] == 120 / 3600
    assert r["max_sample_gap_s"] == 3600 and r["four_fresh_fraction"] == 2 / 3
    assert len(list((tmp_path / "soak").glob("*.json"))) == 3


def test_same_receipt_is_idempotent_but_changed_value_is_rejected(tmp_path):
    import pytest

    sample = dict(at_ns=1000, fresh_hot=4, source_ok=True, source_attempts=1, corpus_bars=10)
    record(tmp_path, sample)
    assert record(tmp_path, sample)[0]["samples"] == 1
    with pytest.raises(ValueError, match="timestamp_collision"):
        record(tmp_path, dict(sample, fresh_hot=0))
