import pytest

from gridmend.flisr.isolate import IsolateError, isolate
from gridmend.flisr.locate import locate_fault
from gridmend.flisr.trace import incident_trace
from gridmend.grid.energize import grid_status
from gridmend.sim import apply_fault

from conftest import segment_ids


def test_isolating_z3_opens_its_boundary_and_recloses_breaker(graph):
    apply_fault(graph, "4-5")
    locate_fault(graph)
    incident_id, ops = isolate(graph)

    assert [(o.op, o.switch_id) for o in ops] == [
        ("open", "2-3"), ("open", "3-23"), ("open", "6-7"), ("open", "6-26"), ("close", "1-2"),
    ]
    status = grid_status(graph)
    assert status["energized_zones"] == [2, 19]

    [plan] = incident_trace(graph, incident_id)
    assert plan["kind"] == "isolation"
    assert plan["status"] == "executed"
    assert plan["ops"] == ["open 2-3", "open 3-23", "open 6-7", "open 6-26", "close 1-2"]


@pytest.mark.parametrize("line", segment_ids())
def test_isolation_deenergizes_only_from_the_faulted_zone_down(graph, line):
    apply_fault(graph, line)
    zone = locate_fault(graph).zone
    isolate(graph)

    status = grid_status(graph)
    assert zone not in status["energized_zones"]
    assert 2 in status["energized_zones"]
    incident = graph.ro_query(
        "MATCH (i:Incident)-[:FAULT_IN]->(z:Zone) RETURN i.status, i.fault_line, z.id"
    ).result_set
    assert incident == [["isolated", line, zone]]


def test_isolate_twice_is_refused(graph):
    apply_fault(graph, "4-5")
    locate_fault(graph)
    isolate(graph)
    with pytest.raises(IsolateError):
        isolate(graph)
