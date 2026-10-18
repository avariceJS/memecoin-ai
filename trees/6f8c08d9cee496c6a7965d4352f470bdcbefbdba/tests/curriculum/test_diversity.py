from memecoin_curriculum.diversity import bucket, correlation, delta, support


def row(pool, close):
    return dict(
        id=f"{pool}:{close}",
        pool=pool,
        close=close,
        maturity=close + 900,
        age="1",
        volatility="0",
        volume="2",
        feature_sha=f"{pool}:{close}",
        returns=[close / 10000, close / 3000],
    )


def test_concentration_is_observed_mass_not_sampler_weights():
    rows = [row("a", i * 300) for i in range(9)] + [row("b", 300)]
    s = support(rows)
    assert s["largest_pool_share"] == 0.9
    assert abs(s["effective_pool_count"] - 100 / 82) < 1e-10
    assert s["independent_sample_size"] is None


def test_new_membership_and_new_markets_are_distinct():
    old = [row("a", 300)]
    new = old + [row("a", 600), row("b", 300)]
    d = delta(old, new)
    assert d["added"]["decisions"] == 2
    assert d["new_pools"] == ["b"]
    assert d["familiar_pool_rows"] == 1
    assert d["new_pool_days"] == 1
    assert d["exact_feature_duplicate_rows"] == 0


def test_overlap_does_not_cross_missing_intervals_or_pools():
    s = support([row("a", 300), row("a", 600), row("a", 1200), row("b", 900)])
    assert s["adjacent_decisions"] == 1
    assert s["disjoint_context_and_target_windows"] == 2
    assert s["lag1_return_correlation"]["a"]["5"]["pearson"] is None


def test_unknown_regime_and_undefined_correlation():
    assert bucket(None, [1, 2]) == "unknown"
    assert bucket(float("nan"), [1, 2]) == "unknown"
    assert correlation([(1, 2)] * 3) is None
    assert support([])["largest_pool_share"] is None
