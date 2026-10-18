import pytest

from memecoin_paper.engine import advance, initial
from memecoin_paper.projection import restore, save
from memecoin_registry.contracts import digest
from memecoin_runner.ledger import Ledger


def event(ledger, state, t):
    tick = dict(at_ns=t, market=[], predictions=[], source_ok=True, activation_ns=0)
    next_state, records = advance(state, tick)
    ledger.append("tick", tick=tick, result_sha=digest(dict(state=next_state, records=records)))
    return next_state, records


def test_checkpoint_prefix_tail_recovery_and_state_binding(tmp_path):
    with Ledger(tmp_path / "ledger", dict(fixture=True)) as ledger:
        state, rec = event(ledger, initial(), 1)
        save(tmp_path, ledger.events, state, rec, rec)
        expected, rec2 = event(ledger, state, 2)
        got, all_rec, last = restore(tmp_path, ledger.events)
        assert got == expected and all_rec == rec + rec2 and last == rec2
        # Even a locally recomputed cache checksum cannot detach execution state
        # from the old immutable event result.
        from memecoin_ml.data import read
        from memecoin_paper.runtime import atomic

        bad = read(tmp_path / "projection.json")
        bad["payload"]["state"]["last_ns"] = 3
        bad["checksum"] = digest(bad["payload"])
        atomic(tmp_path / "projection.json", bad)
        with pytest.raises(ValueError, match="state_binding"):
            restore(tmp_path, ledger.events)
        (tmp_path / "projection.json").unlink()
        assert restore(tmp_path, ledger.events)[0] == expected
