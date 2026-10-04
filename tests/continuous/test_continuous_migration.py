import json

from memecoin_continuous.migration import migrate
from memecoin_paper.audit import replay
from memecoin_paper.engine import advance, initial
from memecoin_registry.contracts import digest
from memecoin_runner.ledger import Ledger, publish


def test_open_position_cash_and_prefix_survive_new_source(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    old_source = tmp_path / "old-source"
    (old_source / "shadow").mkdir(parents=True)
    champion = tmp_path / "champion"
    champion.mkdir()
    publish(champion / "manifest.json", {})
    for name in ("training", "candidate-shadows"):
        (old / name).mkdir()
    publish(old / "config.json", dict(source=str(old_source), activation_ns=310 * 10**9))
    publish(source / "config.json", dict(expires_ns=9999 * 10**9))
    obs = dict(
        pool="pool",
        price="1",
        close_s=300,
        available_ns=305 * 10**9,
        quality="active",
        capture_sha="fixture",
    )
    pred = dict(
        id="fixture",
        pool="pool",
        model_sha="fixture-model",
        predicted_ns=315 * 10**9,
        input_available_ns=305 * 10**9,
        event_close_s=300,
        q_by_candidate={"gru": [-0.1, 0.03, 0.1]},
    )
    ticks = [
        dict(
            at_ns=320 * 10**9,
            activation_ns=310 * 10**9,
            market=[obs],
            predictions=[pred],
            source_ok=True,
        ),
        dict(
            at_ns=610 * 10**9,
            activation_ns=310 * 10**9,
            market=[dict(obs, close_s=600, available_ns=605 * 10**9)],
            predictions=[],
            source_ok=True,
        ),
    ]
    state = initial()
    with Ledger(old / "ledger", dict(fixture=True)) as ledger:
        for tick in ticks:
            state, records = advance(state, tick)
            ledger.append("tick", tick=tick, result_sha=digest(dict(state=state, records=records)))
    before = replay(old)
    assert before["open_positions"] == 1
    root = tmp_path / "new"
    migrate(old, root, source, "fixture-pin", champion)
    assert replay(root) == before
    assert json.loads((root / "migration.json").read_text())["no_cash_reset"]
    assert json.loads((root / "config.json").read_text())["training_max_runs"] == 8
    tick = dict(
        ticks[-1], at_ns=1510 * 10**9, market=[dict(obs, close_s=1500, available_ns=1505 * 10**9)]
    )
    with Ledger(root / "ledger", dict(fixture=True)) as ledger:
        state, records = advance(state, tick)
        ledger.append("tick", tick=tick, result_sha=digest(dict(state=state, records=records)))
    after = replay(root)
    assert after["counts"] == dict(decision=1, entry=1, exit=1)
    assert replay(old) == before  # original history never mutated
