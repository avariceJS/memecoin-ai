import copy

from memecoin_curriculum.trigger import evaluate


def evidence():
    return dict(
        as_of_ns=4000 * 10**9,
        previous_snapshot_sha="a" * 64,
        current_snapshot_sha="b" * 64,
        new_train_support=dict(
            added=dict(
                decisions=1500, effective_pool_count=10, disjoint_context_and_target_windows=150
            ),
            new_pool_days=10,
        ),
    )


def call(e):
    return evaluate(e, 4000 * 10**9, 0, quota_ok=True, source_ok=True, domain_stop=False)


def test_many_correlated_rows_do_not_unlock_training():
    e = evidence()
    e["new_train_support"]["added"].update(decisions=100000, effective_pool_count=1)
    assert not call(e)["eligible"]
    assert call(evidence())["eligible"]


def test_stale_unbound_or_missing_fails_closed():
    e = evidence()
    for mutation in (
        dict(as_of_ns=0),
        dict(current_snapshot_sha="invalid"),
        dict(current_snapshot_sha="a" * 64),
    ):
        c = copy.deepcopy(e)
        c.update(mutation)
        assert not call(c)["eligible"]
    assert not call(None)["eligible"]


def test_diverse_fast_path_does_not_wait_24h_or_bypass_quota():
    e = evidence()
    e["new_train_support"]["added"].update(
        decisions=20000, effective_pool_count=50, disjoint_context_and_target_windows=1000
    )
    e["new_train_support"]["new_pool_days"] = 50
    r = evaluate(e, e["as_of_ns"], 3400 * 10**9, quota_ok=True, source_ok=True, domain_stop=False)
    assert r["eligible"] and r["branches"]["fast"]["eligible"]
    assert not evaluate(e, e["as_of_ns"], 0, quota_ok=False, source_ok=True, domain_stop=False)[
        "eligible"
    ]


def test_invalid_support_cannot_unlock_training():
    for value in (None, float("inf"), -1, True):
        e = evidence()
        e["new_train_support"]["added"]["effective_pool_count"] = value
        assert not call(e)["eligible"]
    e = evidence()
    e["new_train_support"] = {}
    assert not call(e)["eligible"]
