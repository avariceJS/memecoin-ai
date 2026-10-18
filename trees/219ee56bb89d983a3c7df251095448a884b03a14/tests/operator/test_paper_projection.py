"""Display projection preserves separate virtual books and pending intent."""

from memecoin_operator.adapters import paper_projection


def test_same_exam_books_keep_their_own_decisions_pending_and_decimal_mtm():
    books = []
    for model, candidate, equity in [("a" * 64, "gru", "1001.2"), ("b" * 64, "gru", "999.3")]:
        books.append(
            dict(
                key=model + ":" + candidate,
                model_sha=model,
                candidate=candidate,
                cash="1000",
                equity=equity,
                realized="0.1",
                fees="0.02",
                positions=[],
                pending=[dict(pool=model[:8], decision_ns=10)],
                closed=[],
                signals=[dict(pool=model[:8], decision_ns=10, reason="enter_next_observed_close")],
            )
        )
    result = paper_projection([dict(id="same_exam", books=books)])
    a, b = result["books"]
    assert a["key"] != b["key"] and a["exam_id"] == b["exam_id"]
    assert a["unrealized"] == "1.1" and b["unrealized"] == "-0.8"
    for book in result["books"]:
        assert book["open_positions"] == 0 and book["pending_count"] == 1
        assert book["positions"][0]["status"] == "pending"
        assert book["decisions"][0]["model_sha"] == book["model_sha"]
        assert book["decisions"][0]["stage"] == "intent"
        assert book["decisions"][0]["filled"] is False
    assert result["independent_books"] is True
