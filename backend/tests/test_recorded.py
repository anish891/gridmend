from eval import run
from eval.run import Row, record_rows, with_recorded_rows


def test_recorded_model_rows_are_replayed_after_greedy(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RECORDED", {"llm-only": tmp_path / "llm_only.csv",
                                          "agent": tmp_path / "agent.csv"})
    recorded = [Row("4-5", "greedy", 0.0, 0, 2, 1, 1.0),
                Row("4-5", "llm-only", 0.0, 0, 7, 2, 400.0, claimed_kw=685.0),
                Row("4-5", "agent", 850.0, 2, 4, 0, 40000.0)]
    record_rows(recorded, ["llm-only", "agent"])

    fresh = [Row("4-5", "isolate-only", 0.0, 0, 0, 0, 0.0), Row("4-5", "greedy", 0.0, 0, 2, 1, 1.0),
             Row("4-5", "optimal", 850.0, 2, 4, 0, 70.0)]
    merged = with_recorded_rows(fresh, ["llm-only", "agent"])

    assert [r.method for r in merged] == ["isolate-only", "greedy", "llm-only", "agent", "optimal"]
    assert merged[2].claimed_kw == 685.0 and merged[3].restored_kw == 850.0


def test_nothing_is_added_without_a_recording(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RECORDED", {"agent": tmp_path / "missing.csv"})
    fresh = [Row("4-5", "greedy", 0.0, 0, 2, 1, 1.0)]
    assert with_recorded_rows(fresh, ["agent"]) == fresh
