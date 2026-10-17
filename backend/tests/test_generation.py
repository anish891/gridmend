from gridmend.flisr.execute import restore
from gridmend.flisr.explain import explain_incident
from gridmend.flisr.generation import generation_status, reconnect_generation
from gridmend.flisr.isolate import secure
from gridmend.flisr.trace import SwitchOp, open_incident
from gridmend.flisr.verify import check_capacity, verify_plan
from gridmend.grid.energize import grid_status
from gridmend.sim import apply_fault


def online(g):
    return {u["bus"]: u["online"] for u in generation_status(g)}


def test_generation_trips_on_a_fault_and_stays_off_until_reconnected(graph):
    assert online(graph) == {21: True}
    apply_fault(graph, "4-5")
    assert online(graph) == {21: False}
    secure(graph)
    # Z19 has power again, but a unit waits out the reconnection delay before it returns.
    assert online(graph) == {21: False}
    assert reconnect_generation(graph)["reconnected"] == [21]
    assert online(graph) == {21: True}


def test_healthy_feeder_with_generation_is_within_ratings(graph):
    violations, _ = check_capacity(graph)
    assert violations == []


def test_online_solar_relieves_the_lateral_that_limits_restoration(falkordb, graph):
    apply_fault(graph, "4-5")
    incident_id, _ = secure(graph)
    close_8_21 = [SwitchOp("8-21", "close")]
    without = verify_plan(falkordb, graph, incident_id, "t1", close_8_21)
    assert {kind for kind, _ in without.violations} == {"capacity"}

    reconnect_generation(graph)
    with_solar = verify_plan(falkordb, graph, incident_id, "t2", close_8_21)
    assert with_solar.passed
    assert with_solar.restored_zones == [7, 10, 14]


def test_staged_restoration_picks_up_more_once_solar_is_back(falkordb, graph):
    apply_fault(graph, "4-5")
    incident_id, _ = secure(graph)
    _, first = restore(falkordb, graph, incident_id, approve=lambda plan: True)
    assert first["restored_kw"] == 850

    assert reconnect_generation(graph) == {"reconnected": [21], "replan": True}
    assert open_incident(graph)["status"] == "isolated"
    _, second = restore(falkordb, graph, incident_id, approve=lambda plan: True)
    assert second["restored_kw"] == 225
    assert 10 in grid_status(graph)["energized_zones"]

    plan = explain_incident(graph, incident_id)["plan"]
    assert plan["stages"] == 2
    assert plan["restored_kw"] == 1075
    assert set(plan["paths"]) == {7, 10, 14}
