import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_message_tool_call import Function

from eval.isolation import secure
from gridmend import api
from gridmend.flisr.trace import SwitchOp
from gridmend.flisr.verify import verify_plan
from conftest import TEST_GRAPH, isolated

BOTH_SITES = [SwitchOp("9-10", "open"), SwitchOp("13-14", "open"),
              SwitchOp("8-21", "close"), SwitchOp("9-15", "close")]


@pytest.fixture
def client(graph, monkeypatch):
    monkeypatch.setattr(api, "graph_name", lambda: TEST_GRAPH)
    return TestClient(api.app)


def test_state_has_buses_switches_and_zones(client):
    state = client.get("/state").json()
    assert len(state["buses"]) == 33
    assert len(state["lines"]) == 37
    assert sum(line["type"] == "switch" for line in state["lines"]) == 14
    assert [z["id"] for z in state["zones"]] == [2, 3, 7, 10, 14, 19, 23, 26, 30]
    assert state["served_kw"] == pytest.approx(3715)
    assert state["incident"] is None


def test_the_operator_executes_a_plan_the_agent_simulated(client, falkordb, graph):
    assert client.post("/fault", json={"line": "4-5"}).json()["indicators_set"] == ["1-2", "2-3"]
    incident_id, _ = secure(graph)
    loop = verify_plan(falkordb, graph, incident_id, "inc1-p1",
                       [SwitchOp("8-21", "close"), SwitchOp("12-22", "close")], origin="agent")
    good = verify_plan(falkordb, graph, incident_id, "inc1-p2", BOTH_SITES, origin="agent")

    assert client.post("/execute", json={"plan_id": loop.plan_id}).status_code == 400
    response = client.post("/execute", json={"plan_id": good.plan_id}).json()
    assert response["tripped_again"] is False
    assert client.get("/explain").json()["status"] == "restored"
    assert client.get("/state").json()["served_kw"] == pytest.approx(1310)


def test_bad_requests_are_400_or_409(client):
    assert client.post("/fault", json={"line": "2-19"}).status_code == 400
    assert client.get("/explain").status_code == 409
    for removed in ("/locate", "/isolate", "/plan", "/whatif"):
        assert client.post(removed).status_code in (404, 405)


def test_results_match_the_evaluation_output(client, tmp_path, monkeypatch):
    import eval.run as run

    monkeypatch.setattr(run, "out_dir", lambda: tmp_path)
    assert client.get("/results").status_code == 404

    rows = [run.Row("4-5", "agent", 850.0, 2, 4, 0, 40000.0, 850.0),
            run.Row("4-5", "optimal", 850.0, 2, 4, 0, 300.0, 850.0)]
    run.write_outputs(rows, run.summarize(rows))
    body = client.get("/results").json()
    assert body["rows"][0]["restored_kw"] == 850.0
    assert body["summary"] == run.summarize(rows)


def test_queries_run_live_and_follow_the_incident(client, graph):
    healthy = {q["key"]: q for q in client.get("/queries").json()["steps"]}
    assert healthy["zones"]["total_rows"] == 9
    assert healthy["energize"]["total_rows"] == 33
    assert healthy["indicators"]["total_rows"] == 14
    assert "skipped" in healthy["trace"]

    isolated(graph, "4-5")
    after = {q["key"]: q for q in client.get("/queries").json()["steps"]}
    switches = {row[0]: row for row in after["indicators"]["rows"]}
    assert switches["2-3"][5] == "set"
    assert after["trace"]["rows"][0][:3] == ["inc1-iso", "isolation", "executed"]


Z3_SCRIPT = [
    ("zone_map", {}),
    ("declare_fault", {"zones": [3], "evidence": "1-2 and 2-3 set"}),
    ("simulate_plan", {"purpose": "isolation", "open": ["2-3", "3-23", "6-7", "6-26"],
                       "close": ["1-2"]}),
    ("execute_isolation", lambda r: {"plan_id": r[2]["plan_id"]}),
    ("simulate_plan", {"purpose": "restoration", "close": ["8-21"]}),
    ("simulate_plan", {"purpose": "restoration", "close": ["8-21", "9-15"],
                       "open": ["9-10", "13-14"]}),
    ("recommend_plan", lambda r: {"plan_id": r[5]["plan_id"],
                                  "reason": "restores the hospital and fire station"}),
]


class ScriptedAdvisor:
    """Stands in for the LLM: plays a script of tool calls, filling plan ids from real results."""

    def __init__(self, script=Z3_SCRIPT):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.offered = []
        self.script = script

    def create(self, model, messages, tools, temperature):
        self.offered = [t["function"]["name"] for t in tools]
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if len(results) >= len(self.script):
            return self.wrap(ChatCompletionMessage(role="assistant", content="Recommend it."))
        name, args = self.script[len(results)]
        return self.call(name, args(results) if callable(args) else args)

    def call(self, name, args):
        tool_call = ChatCompletionMessageToolCall(
            id=f"c{name}{len(self.offered)}", type="function",
            function=Function(name=name, arguments=json.dumps(args)))
        return self.wrap(ChatCompletionMessage(role="assistant", tool_calls=[tool_call]))

    @staticmethod
    def wrap(message):
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def agent(client, monkeypatch):
    advisor = ScriptedAdvisor()
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setattr(api, "agent_client", lambda: (advisor, "scripted"))
    monkeypatch.setattr(api, "AGENT_COOLDOWN_S", 0)
    return advisor


def stream_events(client):
    with client.stream("POST", "/agent/run") as response:
        assert response.status_code == 200
        return [json.loads(line[len("data: "):]) for line in response.iter_lines()
                if line.startswith("data: ")]


def test_agent_is_hidden_without_an_llm(client, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    assert client.get("/agent").json() == {"available": False, "model": None}
    client.post("/fault", json={"line": "4-5"})
    assert client.post("/agent/run").status_code == 503


def test_agent_needs_a_tripped_breaker(client, agent):
    assert client.post("/agent/run").status_code == 409


def test_agent_advises_and_the_operator_executes(client, agent):
    client.post("/fault", json={"line": "4-5"})
    events = stream_events(client)

    assert "execute_restoration" not in agent.offered and "execute_isolation" in agent.offered
    assert [e["name"] for e in events if e["type"] == "tool"][:4] == [
        "zone_map", "declare_fault", "simulate_plan", "execute_isolation"]
    assert events[-1] == {"type": "final", "text": "Recommend it.", "model": "scripted"}
    assert client.get("/state").json()["served_kw"] == pytest.approx(460)

    plans = client.get("/plans").json()
    recommended = next(p for p in plans["candidates"] if p["plan_id"] == plans["recommended"])
    assert recommended["recommended"] and recommended["critical"] == ["hospital", "fire station"]
    assert recommended["recommendation"] == "restores the hospital and fire station"
    assert all(set(p) >= {"ops", "violations", "restored_zones", "dropped_zones", "critical"}
               for p in plans["candidates"])
    assert [p["passed"] for p in plans["candidates"]] == [False, True]
    assert {p["origin"] for p in plans["candidates"]} == {"agent"}
    assert client.post("/execute", json={"plan_id": plans["recommended"]}).status_code == 200
    assert client.get("/state").json()["served_kw"] == pytest.approx(1310)


def test_agent_cannot_recommend_a_rejected_plan(falkordb, graph):
    from gridmend.agent.tools import GridTools

    incident_id = isolated(graph, "4-5")
    tools = GridTools(falkordb, graph, approve=lambda plan: False, can_execute=False)
    rejected = tools.call("simulate_plan", {"purpose": "restoration", "close": ["8-21", "12-22"]})
    assert "error" in tools.call("recommend_plan", {"plan_id": rejected["plan_id"], "reason": "x"})
    assert incident_id == rejected["plan_id"].split("-")[0]


def test_the_agent_can_take_over_an_isolated_incident_but_not_a_restored_one(client, falkordb,
                                                                            graph, agent):
    incident_id = isolated(graph, "4-5")
    with client.stream("POST", "/agent/run") as response:
        assert response.status_code == 200
    good = verify_plan(falkordb, graph, incident_id, "inc1-p9", BOTH_SITES, origin="agent")
    client.post("/execute", json={"plan_id": good.plan_id})
    assert client.post("/agent/run").status_code == 409


def test_agent_runs_are_spaced_out(client, agent, monkeypatch):
    import time

    client.post("/fault", json={"line": "4-5"})
    monkeypatch.setattr(api, "AGENT_COOLDOWN_S", 600)
    monkeypatch.setattr(api, "_agent_last_finished", time.monotonic())
    response = client.post("/agent/run")
    assert response.status_code == 429
    assert "cooling down" in response.json()["detail"]


def test_deployed_site_loads_the_build_time_caches(tmp_path, monkeypatch):
    from gridmend import site

    calls = []
    monkeypatch.setattr(site, "load_caches", lambda: calls.append(True))
    (tmp_path / "index.html").write_text("ok")
    with TestClient(site.create_site(tmp_path)) as client:
        assert client.get("/").text == "ok"
    assert calls == [True]


def test_state_reports_what_each_line_carries(client):
    lines = {l["id"]: l for l in client.get("/state").json()["lines"]}
    assert lines["1-2"]["carries"]["kw"] == pytest.approx(3715)
    assert set(lines["6-7"]["carries"]["critical"]) == {"hospital", "fire station"}
    assert lines["8-21"]["carries"] is None


def test_risk_map_comes_from_the_evaluation(client, tmp_path, monkeypatch):
    import eval.run as run

    monkeypatch.setattr(run, "out_dir", lambda: tmp_path)
    assert client.get("/risk").status_code == 404
    rows = [run.Row("4-5", "agent", 850.0, 2, 4, 0, 500.0, 850.0, 2405.0, "water pumping;telecom"),
            run.Row("4-5", "optimal", 850.0, 2, 4, 0, 300.0, 850.0)]
    run.write_outputs(rows, run.summarize(rows))
    assert client.get("/risk").json()["lines"] == [
        {"line": "4-5", "unserved_kw": 2405.0, "restored_kw": 850.0,
         "critical_dark": ["water pumping", "telecom"]}]


def test_a_jam_reported_mid_incident_rejects_the_plans_that_need_it(client, falkordb, graph,
                                                                   fixed_model, monkeypatch):
    incident_id = isolated(graph, "4-5")
    plan = verify_plan(falkordb, graph, incident_id, "inc1-p1", BOTH_SITES, origin="agent")
    reply = json.dumps({"findings": [{"switch": "8-21", "operable": False, "evidence": "seized"}]})
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setattr(api, "agent_client", lambda: (fixed_model(reply), "m"))

    filed = client.post("/crew", json={"note": "The 8-21 tie is seized."}).json()
    assert filed["rejected_plans"] == [plan.plan_id]
    assert client.post("/execute", json={"plan_id": plan.plan_id}).status_code == 400
    state = client.get("/state").json()
    assert next(l for l in state["lines"] if l["id"] == "8-21")["operable"] is False


class ScriptedAsker(ScriptedAdvisor):
    """Answers "how is the hospital fed?" by tracing its feeding path."""

    def create(self, model, messages, tools, temperature):
        self.offered = [t["function"]["name"] for t in tools]
        self.question = next(m["content"] for m in messages if m["role"] == "user")
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if not results:
            return self.call("feeding_path", {"facility": "hospital"})
        path = " -> ".join(results[0]["switches"])
        return self.wrap(ChatCompletionMessage(role="assistant", content=f"Fed via {path}."))


@pytest.fixture
def asker(client, monkeypatch):
    model = ScriptedAsker()
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setattr(api, "agent_client", lambda: (model, "scripted"))
    monkeypatch.setattr(api, "ASK_COOLDOWN_S", 0)
    return model


def ask(client, question):
    with client.stream("POST", "/ask", json={"question": question}) as response:
        assert response.status_code == 200
        return [json.loads(line[len("data: "):]) for line in response.iter_lines()
                if line.startswith("data: ")]


def test_questions_are_answered_from_the_graph_with_read_only_tools(client, asker):
    events = ask(client, "How is the hospital fed?")

    assert asker.question == "How is the hospital fed?"
    assert set(asker.offered) == {"field_reports", "grid_status", "zone_map", "line_dependencies",
                                  "transfer_capacity", "fault_impact", "feeding_path",
                                  "explain_plan"}
    tool = events[0]
    assert tool["name"] == "feeding_path"
    assert tool["result"] == {"bus": 7, "facility": "hospital", "zone": 7, "energized": True,
                              "switches": ["1-2", "2-3", "6-7"]}
    assert events[-1] == {"type": "final", "text": "Fed via 1-2 -> 2-3 -> 6-7.", "model": "scripted"}


def test_questions_must_be_short(client, asker):
    assert client.post("/ask", json={"question": " "}).status_code == 422
    assert client.post("/ask", json={"question": "x" * 301}).status_code == 422


@pytest.fixture
def both_feeders(client, falkordb, monkeypatch):
    """Serve the 33-bus scratch graph by default and an Oberrhein scratch copy on request."""
    from gridmend.grid import feeder
    from gridmend.grid.load import write_graph

    with feeder.use("oberrhein"):
        if feeder.base_graph() not in falkordb.list_graphs():
            write_graph(falkordb)
        name = "oberrhein_test"
        if name in falkordb.list_graphs():
            falkordb.select_graph(name).delete()
        copy = falkordb.select_graph(feeder.base_graph()).copy(name)
    monkeypatch.setattr(api, "graph_name", lambda: name if feeder.name() == "oberrhein" else TEST_GRAPH)
    yield client
    copy.delete()


def test_each_request_is_served_from_the_feeder_it_names(both_feeders):
    client = both_feeders
    assert len(client.get("/state").json()["buses"]) == 33
    assert len(client.get("/state?feeder=oberrhein").json()["buses"]) == 177
    assert client.get("/state?feeder=nowhere").status_code == 400

    assert client.get("/layout").json() == {"positions": None}
    assert len(client.get("/layout?feeder=oberrhein").json()["positions"]) == 177
    # Every page-load call works on the real network too.
    for path in ("/explain", "/trace", "/results", "/risk", "/queries", "/crew", "/generation"):
        assert client.get(f"{path}?feeder=oberrhein").status_code in (200, 404, 409), path



def test_the_agent_works_an_oberrhein_fault_through_the_api(both_feeders, monkeypatch):
    client = both_feeders
    advisor = ScriptedAdvisor([
        ("declare_fault", {"zones": [27], "evidence": "set into Z27, clear beyond"}),
        ("simulate_plan", {"purpose": "isolation", "open": ["22-27", "71-76"], "close": ["2-6"]}),
        ("execute_isolation", lambda r: {"plan_id": r[1]["plan_id"]}),
    ])
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setattr(api, "agent_client", lambda: (advisor, "scripted"))
    monkeypatch.setattr(api, "AGENT_COOLDOWN_S", 0)

    # Endpoints and the agent run in worker threads; the feeder must follow the request into them.
    assert client.post("/fault?feeder=oberrhein", json={"line": "27-33"}).status_code == 200
    with client.stream("POST", "/agent/run?feeder=oberrhein") as response:
        events = [json.loads(line[len("data: "):]) for line in response.iter_lines()
                  if line.startswith("data: ")]
    isolation = next(e for e in events if e.get("name") == "execute_isolation")["result"]
    assert isolation["executed"] == ["open 22-27", "open 71-76", "close 2-6"]
    assert "breaker_tripped_again" not in isolation
    assert client.get("/state?feeder=oberrhein").json()["incident"]["status"] == "isolated"
    # The 33-bus twin is untouched.
    assert client.get("/state").json()["incident"] is None
