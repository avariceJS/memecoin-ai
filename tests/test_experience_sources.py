import json
from collections import Counter

import pytest
from curriculum.test_admission import capture

from memecoin_curriculum.admission import compile_corpus, load_corpus
from memecoin_experience.sources import inputs
from memecoin_experience.storage import build, file_hash


def test_existing_qualified_contract_end_to_end(tmp_path):
    cap, birth = capture(tmp_path)
    root = tmp_path / "admitted"
    compile_corpus([(cap, birth)], root)
    pin = file_hash(root / "manifest.json")
    reference = dict(kind="admitted", path=str(root), pin=pin)
    counts = Counter()
    rows = list(inputs([reference, reference], 10**18, counts))
    assert rows == load_corpus(dict(path=str(root), sha=pin), 10**18)
    assert counts == dict(source_rows=120, duplicate_rows=60, revised_keys=0)
    result = build(tmp_path / "experience", iter(rows), sources=[reference], cutoff=10**18)
    assert result["decisions"] == 53
    assert result["unique_bars"] == 60 and result["context_unready_bars"] == 7
    (cap / "response.bin").write_text("corrupt")
    with pytest.raises(ValueError):
        list(inputs([reference], 10**18, Counter()))


def test_cli_build_and_inspect(tmp_path, monkeypatch, capsys):
    from memecoin_experience.__main__ import main

    cap, birth = capture(tmp_path)
    root = tmp_path / "admitted"
    compile_corpus([(cap, birth)], root)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            dict(
                sources=[
                    dict(kind="admitted", path=str(root), pin=file_hash(root / "manifest.json"))
                ],
                cutoff_ns=10**18,
                context=16,
                chunk=29,
            )
        )
    )
    store = tmp_path / "experience"
    monkeypatch.setattr(
        "sys.argv", ["experience", "build", "--plan", str(plan), "--store", str(store)]
    )
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["decisions"] == 45
    monkeypatch.setattr(
        "sys.argv",
        ["experience", "inspect", "--store", str(store), "--snapshot", result["snapshot"]],
    )
    main()
    assert json.loads(capsys.readouterr().out)["verified"]


def test_many_sources_bounded_handles_and_first_receipt(monkeypatch):
    from test_experience import bars

    live, maximum = 0, 0

    def stream(reference, cutoff):
        nonlocal live, maximum
        live += 1
        maximum = max(maximum, live)
        try:
            for row in bars(2):
                row["available_ns"] += reference["i"]
                row["ohlcv"][4] += reference["i"]
                yield row
        finally:
            live -= 1

    monkeypatch.setattr("memecoin_experience.sources.admitted", stream)
    counts = Counter()
    refs = [dict(kind="admitted", i=i) for i in reversed(range(1000))]
    result = list(inputs(refs, 10**18, counts))
    assert result == list(bars(2))
    assert maximum == 1 and live == 0
    assert counts == dict(source_rows=2000, duplicate_rows=1998, revised_keys=2)
