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
