from eval import run
from eval.run import Row, record_llm_only, with_recorded_llm_only


def test_recorded_llm_only_rows_are_replayed_after_greedy(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RECORDED_LLM", tmp_path / "llm_only.csv")
    recorded = [Row("4-5", "greedy", 0.0, 0, 2, 1, 1.0),
                Row("4-5", "llm-only", 0.0, 0, 7, 2, 400.0, claimed_kw=685.0)]
    record_llm_only(recorded)

    fresh = [Row("4-5", "isolate-only", 0.0, 0, 0, 0, 0.0), Row("4-5", "greedy", 0.0, 0, 2, 1, 1.0),
             Row("4-5", "gridmend", 850.0, 2, 4, 0, 70.0)]
    merged = with_recorded_llm_only(fresh)

    assert [r.method for r in merged] == ["isolate-only", "greedy", "llm-only", "gridmend"]
    assert merged[2].claimed_kw == 685.0


def test_nothing_is_added_without_a_recording(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RECORDED_LLM", tmp_path / "missing.csv")
    fresh = [Row("4-5", "greedy", 0.0, 0, 2, 1, 1.0)]
    assert with_recorded_llm_only(fresh) == fresh
