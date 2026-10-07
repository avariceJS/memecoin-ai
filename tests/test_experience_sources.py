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
