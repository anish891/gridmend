import pytest

from gridmend.flisr.locate import LocateError, locate_fault
from gridmend.sim import apply_fault

from conftest import segment_ids


def test_there_are_23_fault_scenarios():
    assert len(segment_ids()) == 23


@pytest.mark.parametrize("line", segment_ids())
def test_locates_every_single_fault(graph, line):
    expected_zone = graph.ro_query(
        "MATCH (a:Bus)-[:SEG {id: $id}]->(:Bus) RETURN a.zone", {"id": line}
    ).result_set[0][0]

    tripped = apply_fault(graph, line)
    location = locate_fault(graph)

    assert location.zone == expected_zone
    assert location.chain == tripped
    faulted = graph.ro_query("MATCH (z:Zone {faulted: true}) RETURN collect(z.id)").result_set
    assert faulted[0][0] == [expected_zone]


def test_fault_in_z3_has_breaker_and_2_3_as_evidence(graph):
    apply_fault(graph, "4-5")
    location = locate_fault(graph)
    assert location.zone == 3
    assert location.chain == ["1-2", "2-3"]


def test_no_fault_cannot_be_located(graph):
    with pytest.raises(LocateError):
        locate_fault(graph)
