import pytest

from eval.isolation import isolate, locate_fault
from gridmend.flisr.explain import explain_incident
from gridmend.flisr.locate import declare_fault
from gridmend.flisr.trace import open_incident
from gridmend.grid.energize import grid_status
from gridmend.sim import FaultError, apply_faults

from conftest import run_plan


def test_faults_on_separate_branches_are_located_together(graph):
    indicators = apply_faults(graph, ["20-21", "27-28"])
    assert indicators == ["1-2", "2-19", "2-3", "6-26"]

    location = locate_fault(graph)
    assert location.zones == [19, 26]
    assert location.chains == {19: ["1-2", "2-19"], 26: ["1-2", "2-3", "6-26"]}

    _, ops = isolate(graph)
    assert [f"{o.op} {o.switch_id}" for o in ops] == ["open 2-19", "open 6-26", "open 29-30", "close 1-2"]
    assert open_incident(graph)["status"] == "isolated"
    assert grid_status(graph)["faulted_zones"] == [19, 26]


def test_a_fault_hidden_upstream_trips_the_breaker_again(graph):
    # 4-5 (Z3) lies on the path to 16-17 (Z14): every indicator up to Z14 is set, so only Z14
    # shows until it is cut out and the breaker recloses onto Z3.
    apply_faults(graph, ["4-5", "16-17"])
    assert locate_fault(graph).zones == [14]
    isolate(graph)
    assert open_incident(graph)["status"] == "tripped"

    second = locate_fault(graph)
    assert second.zones == [3]
    assert second.chain == ["1-2", "2-3"]
    isolate(graph)
    incident = open_incident(graph)
    assert incident["status"] == "isolated"
    assert incident["zones"] == [3, 14]


def test_the_agent_finds_a_hidden_fault_when_the_breaker_trips_again(falkordb, graph):
    apply_faults(graph, ["4-5", "16-17"])
    incident_id = declare_fault(graph, [14], "set through 13-14, nothing beyond")
    first = run_plan(falkordb, graph, incident_id, kind="isolation", open=["13-14"], close=["1-2"])
    assert first.passed and open_incident(graph)["status"] == "tripped"

    declare_fault(graph, [3], "1-2 and 2-3 set after reclosing")
    run_plan(falkordb, graph, incident_id, kind="isolation",
             open=["2-3", "3-23", "6-7", "6-26"], close=["1-2"])
    assert open_incident(graph)["status"] == "isolated"
    assert not {3, 14} & set(grid_status(graph)["energized_zones"])

    e = explain_incident(graph, incident_id)
    assert e["faulted_zones"] == [3, 14]
    assert e["fault_line"] == "16-17, 4-5"
    assert e["isolation"][:2] == ["open 13-14", "close 1-2"]


def test_the_same_segment_cannot_fault_twice(graph):
    with pytest.raises(FaultError):
        apply_faults(graph, ["4-5", "4-5"])
