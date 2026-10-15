import pytest
from fastapi.testclient import TestClient

from gridmend import api
from conftest import TEST_GRAPH


@pytest.fixture
def client(graph, monkeypatch):
    monkeypatch.setattr(api, "GRAPH_NAME", TEST_GRAPH)
    return TestClient(api.app)


def test_state_has_buses_switches_and_zones(client):
    state = client.get("/state").json()
    assert len(state["buses"]) == 33
    assert len(state["lines"]) == 37
    assert sum(line["type"] == "switch" for line in state["lines"]) == 14
    assert [z["id"] for z in state["zones"]] == [2, 3, 7, 10, 14, 19, 23, 26, 30]
    assert state["served_kw"] == pytest.approx(3715)
    assert state["incident"] is None


def test_fault_to_restore_flow(client):
    assert client.post("/fault", json={"line": "4-5"}).json()["indicators_set"] == ["1-2", "2-3"]
    assert client.post("/locate").json() == {"zone": 3, "chain": ["1-2", "2-3"]}
    assert len(client.post("/isolate").json()["ops"]) == 5

    plan = client.post("/plan").json()
    assert len(plan["candidates"]) == 5
    recommended = next(c for c in plan["candidates"] if c["plan_id"] == plan["recommended"])
    assert recommended["critical"] == ["hospital", "fire station"]
    rejected = [c for c in plan["candidates"] if not c["passed"]]
    assert all(c["violations"] for c in rejected)

    assert client.post("/execute", json={"plan_id": plan["recommended"]}).status_code == 200
    explanation = client.get("/explain").json()
    assert explanation["status"] == "restored"
    assert client.get("/state").json()["served_kw"] == pytest.approx(1310)


def test_rejected_plan_cannot_be_executed(client):
    client.post("/fault", json={"line": "4-5"})
    client.post("/locate")
    client.post("/isolate")
    plan = client.post("/plan").json()
    rejected = next(c for c in plan["candidates"] if not c["passed"])
    response = client.post("/execute", json={"plan_id": rejected["plan_id"]})
    assert response.status_code == 400


def test_bad_requests_are_400_or_409(client):
    assert client.post("/fault", json={"line": "2-19"}).status_code == 400
    assert client.post("/plan").status_code == 409
    assert client.get("/explain").status_code == 409


def test_results_match_the_evaluation_output(client, tmp_path, monkeypatch):
    import eval.run as run

    monkeypatch.setattr(run, "OUT_DIR", tmp_path)
    assert client.get("/results").status_code == 404

    rows = [run.Row("4-5", "gridmend", 850.0, 2, 4, 0, 500.0, 850.0),
            run.Row("4-5", "optimal", 850.0, 2, 4, 0, 300.0, 850.0)]
    run.write_outputs(rows, run.summarize(rows))
    body = client.get("/results").json()
    assert body["rows"][0]["restored_kw"] == 850.0
    assert body["summary"] == run.summarize(rows)


def test_queries_run_live_and_follow_the_incident(client):
    healthy = {q["key"]: q for q in client.get("/queries").json()["steps"]}
    assert healthy["zones"]["total_rows"] == 9
    assert healthy["energize"]["total_rows"] == 33
    assert "skipped" in healthy["isolate"] and "skipped" in healthy["trace"]

    client.post("/fault", json={"line": "4-5"})
    client.post("/locate")
    client.post("/isolate")
    isolated = {q["key"]: q for q in client.get("/queries").json()["steps"]}
    assert isolated["isolate"]["params"] == {"zone": 3}
    assert isolated["options"]["rows"] == [[7, "8-21"], [10, "12-22"]]
    assert isolated["trace"]["rows"][0][:3] == ["inc1-iso", "isolation", "executed"]
