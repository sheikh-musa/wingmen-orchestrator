from ihsanos_drain.granted_work import candidate_query, summarize


def test_candidate_query_filters_granted_signal():
    sql, params = candidate_query()
    assert "execution_status = %s" in sql
    assert params == ("granted",)


def test_summarize_splits_executable_from_held():
    rows = [
        # executable: granted + ihsanos + closed window
        dict(decision_ref="A", execution_status="granted",
             repos_affected=["ihsanos"], challenge_status="accepted_by_timeout",
             decision="x"),
        # held: window still open
        dict(decision_ref="B", execution_status="granted",
             repos_affected=["ihsanos"], challenge_status="challenge_window",
             decision="x"),
        # held: not ihsanos executor
        dict(decision_ref="C", execution_status="granted",
             repos_affected=["cosem-tdu"], challenge_status="accepted",
             decision="x"),
    ]
    out = summarize(rows)
    assert out["executable"] == ["A"]
    held_refs = {ref for ref, _ in out["held"]}
    assert held_refs == {"B", "C"}


def test_summarize_empty():
    out = summarize([])
    assert out["executable"] == []
    assert out["held"] == []
