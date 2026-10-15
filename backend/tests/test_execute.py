import pytest

from gridmend.flisr.execute import ExecuteError, execute_plan, restore, verified_plans
from gridmend.flisr.explain import explain_incident
from gridmend.flisr.isolate import isolate
from gridmend.flisr.locate import locate_fault
from gridmend.grid.energize import grid_status
from gridmend.sim import apply_fault


def isolated(graph, line):
    apply_fault(graph, line)
    locate_fault(graph)
    incident_id, _ = isolate(graph)
    return incident_id


def test_restore_executes_the_plan_with_most_critical_loads(falkordb, graph):
    incident_id = isolated(graph, "4-5")
    _, executed = restore(falkordb, graph, incident_id, approve=lambda plan: True)

    assert executed["critical"] == ["hospital", "fire station"]
    status = grid_status(graph)
    assert status["energized_zones"] == [2, 7, 14, 19]
    assert status["served_kw"] == pytest.approx(1310)


def test_declined_plan_changes_nothing(falkordb, graph):
    incident_id = isolated(graph, "4-5")
    _, executed = restore(falkordb, graph, incident_id, approve=lambda plan: False)

    assert executed is None
    assert grid_status(graph)["energized_zones"] == [2, 19]


def test_only_verified_plans_can_be_executed(falkordb, graph):
    incident_id = isolated(graph, "4-5")
    restore(falkordb, graph, incident_id, approve=lambda plan: False)
    rejected = graph.ro_query("MATCH (p:Plan {status: 'rejected'}) RETURN p.id LIMIT 1").result_set
    with pytest.raises(ExecuteError):
        execute_plan(graph, rejected[0][0])


def test_a_plan_cannot_run_twice(falkordb, graph):
    incident_id = isolated(graph, "4-5")
    restore(falkordb, graph, incident_id, approve=lambda plan: False)
    best = verified_plans(graph, incident_id)[0]["id"]
    execute_plan(graph, best)
    with pytest.raises(ExecuteError):
        execute_plan(graph, best)


def test_explanation_traces_paths_margins_and_shed_critical_loads(falkordb, graph):
    incident_id = isolated(graph, "4-5")
    restore(falkordb, graph, incident_id, approve=lambda plan: True)
    e = explain_incident(graph, incident_id)

    assert e["status"] == "restored"
    assert e["plan"]["paths"] == {7: ["1-2", "2-19", "8-21"], 14: ["1-2", "2-19", "8-21", "9-15"]}
    assert 0.90 <= e["plan"]["min_v_pu"] and e["plan"]["max_loading_pct"] <= 100
    dark = {c["facility"]: c["reason"] for c in e["critical"] if not c["energized"]}
    assert set(dark) == {"water pumping", "telecom"}
    assert "18-33" in dark["telecom"] and "overload" in dark["telecom"]
    assert {r["violations"][0].split(":")[0] for r in e["rejected"]} == {"capacity", "radiality"}


def test_explanation_when_no_plan_passes(falkordb, graph):
    incident_id = isolated(graph, "27-28")
    restore(falkordb, graph, incident_id, approve=lambda plan: True)
    e = explain_incident(graph, incident_id)

    assert e["plan"] is None
    telecom = next(c for c in e["critical"] if c["facility"] == "telecom")
    assert "voltage" in telecom["reason"]
