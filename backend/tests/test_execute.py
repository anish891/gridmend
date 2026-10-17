import pytest

from gridmend.flisr.execute import ExecuteError, execute_plan
from gridmend.flisr.explain import explain_incident
from gridmend.flisr.locate import LocateError, declare_fault
from gridmend.flisr.trace import open_incident
from gridmend.grid.energize import grid_status
from gridmend.sim import apply_fault

from conftest import isolated, run_plan

# Feeds the hospital through 8-21 and the fire station through 9-15, leaving Z10 out.
BOTH_SITES = {"close": ["8-21", "9-15"], "open": ["9-10", "13-14"]}
Z3_BOUNDARY = {"open": ["2-3", "3-23", "6-7", "6-26"], "close": ["1-2"]}


def test_only_plans_that_passed_simulation_move_live_switches(falkordb, graph):
    incident_id = isolated(graph, "4-5")
    loop = run_plan(falkordb, graph, incident_id, close=["8-21", "12-22"])
    assert not loop.passed
    with pytest.raises(ExecuteError, match="passed simulation"):
        execute_plan(graph, loop.plan_id)
    assert grid_status(graph)["energized_zones"] == [2, 19]

    assert run_plan(falkordb, graph, incident_id, **BOTH_SITES).passed
    status = grid_status(graph)
    assert status["energized_zones"] == [2, 7, 14, 19]
    assert status["served_kw"] == pytest.approx(1310)
    assert open_incident(graph)["status"] == "restored"


def test_a_plan_cannot_run_twice(falkordb, graph):
    incident_id = isolated(graph, "4-5")
    result = run_plan(falkordb, graph, incident_id, **BOTH_SITES)
    with pytest.raises(ExecuteError):
        execute_plan(graph, result.plan_id)


def test_the_agents_isolation_holds_when_it_read_the_indicators_right(falkordb, graph):
    apply_fault(graph, "4-5")
    incident_id = declare_fault(graph, [3], "1-2 and 2-3 set, 6-7, 3-23 and 6-26 clear")
    result = run_plan(falkordb, graph, incident_id, kind="isolation", **Z3_BOUNDARY)

    assert result.passed
    assert open_incident(graph)["status"] == "isolated"
    assert grid_status(graph)["energized_zones"] == [2, 19]


def test_reclosing_onto_the_real_fault_trips_the_breaker_again(falkordb, graph):
    apply_fault(graph, "4-5")
    # A misreading: the agent blames Z7, so the simulation (which only knows the diagnosis)
    # passes, and protection trips once power reaches the real fault in Z3.
    incident_id = declare_fault(graph, [7], "misread")
    assert run_plan(falkordb, graph, incident_id, kind="isolation",
                    open=["6-7", "9-10"], close=["1-2"]).passed
    assert open_incident(graph)["status"] == "tripped"
    assert grid_status(graph)["energized_zones"] == []

    # The agent can then declare the zone it missed and isolate again.
    declare_fault(graph, [3], "1-2 and 2-3 set again")
    assert run_plan(falkordb, graph, incident_id, kind="isolation", **Z3_BOUNDARY).passed
    assert open_incident(graph)["zones"] == [3, 7]


def test_faults_are_declared_only_after_a_trip_and_in_known_zones(graph):
    with pytest.raises(LocateError, match="no breaker has tripped"):
        declare_fault(graph, [3], "")
    apply_fault(graph, "4-5")
    with pytest.raises(LocateError, match="unknown zones"):
        declare_fault(graph, [4], "")


def test_explanation_traces_paths_margins_and_rejected_designs(falkordb, graph):
    incident_id = isolated(graph, "4-5")
    run_plan(falkordb, graph, incident_id, close=["8-21"])
    run_plan(falkordb, graph, incident_id, close=["8-21", "12-22"])
    run_plan(falkordb, graph, incident_id, **BOTH_SITES)
    e = explain_incident(graph, incident_id)

    assert e["status"] == "restored"
    assert e["plan"]["paths"] == {7: ["1-2", "2-19", "8-21"], 14: ["1-2", "2-19", "8-21", "9-15"]}
    assert 0.90 <= e["plan"]["min_v_pu"] and e["plan"]["max_loading_pct"] <= 100
    assert {r["violations"][0].split(":")[0] for r in e["rejected"]} == {"capacity", "radiality"}
    dark = {c["facility"]: c["reason"] for c in e["critical"] if not c["energized"]}
    assert set(dark) == {"water pumping", "telecom"}
    assert e["dark_zones"] == [3, 10, 23, 26, 30]
    assert e["load_served_kw"] == 1310 and e["load_unserved_kw"] == 2405


def test_explanation_names_the_check_that_kept_a_site_dark(falkordb, graph):
    incident_id = isolated(graph, "27-28")
    assert not run_plan(falkordb, graph, incident_id, close=["18-33"]).passed
    e = explain_incident(graph, incident_id)

    assert e["plan"] is None
    telecom = next(c for c in e["critical"] if c["facility"] == "telecom")
    assert "voltage" in telecom["reason"]
