import pytest

from gridmend.flisr.candidates import plan_restoration
from gridmend.flisr.execute import restore
from gridmend.flisr.explain import explain_incident
from gridmend.flisr.isolate import isolate
from gridmend.flisr.locate import locate_fault
from gridmend.flisr.whatif import WhatIfError, what_if
from gridmend.sim import apply_fault


def snapshot(graph) -> tuple:
    """Everything a what-if must leave alone on the graph it is asked about."""
    return (
        graph.ro_query("MATCH (n) RETURN labels(n)[0], count(n) ORDER BY labels(n)[0]").result_set,
        graph.ro_query("MATCH ()-[s:SW]->() RETURN s.id, s.status, s.operable ORDER BY s.id").result_set,
        graph.ro_query("MATCH ()-[s:SEG {faulted: true}]->() RETURN count(s)").result_set,
    )


def test_a_jam_on_a_healthy_feeder_shows_what_a_fault_would_cost(falkordb, graph):
    before = snapshot(graph)
    graphs_before = set(falkordb.list_graphs())

    r = what_if(falkordb, graph, ["6-7"], "4-5")

    assert r["as_is"]["critical_restored"] == ["hospital", "fire station"]
    assert r["jammed"]["isolation"] == ["open 2-3", "open 3-23", "open 6-26", "open 9-10", "close 1-2"]
    assert r["jammed"]["cut_off"] == [{"zone": 7, "via": "6-7"}]
    assert r["jammed"]["plan"]["ops"] == ["close 12-22"]
    assert r["critical_lost"] == ["hospital"] and r["kw_change"] == -235
    assert snapshot(graph) == before
    assert set(falkordb.list_graphs()) == graphs_before


def test_a_jam_mid_incident_replans_on_a_copy(falkordb, graph):
    apply_fault(graph, "4-5")
    locate_fault(graph)
    incident_id, _ = isolate(graph)
    plan_restoration(falkordb, graph, incident_id)
    before = snapshot(graph)

    r = what_if(falkordb, graph, ["8-21"])

    assert "close 8-21" in r["as_is"]["plan"]["ops"]
    assert all("8-21" not in op for op in r["jammed"]["plan"]["ops"])
    assert r["critical_lost"] == ["fire station"]
    assert snapshot(graph) == before


@pytest.mark.parametrize("switches, line, problem", [
    (["6-7"], None, "name a line to fault"),
    (["6-8"], "4-5", "unknown switches"),
    ([], "4-5", "at least one switch"),
])
def test_bad_questions_are_refused(falkordb, graph, switches, line, problem):
    with pytest.raises(WhatIfError, match=problem):
        what_if(falkordb, graph, switches, line)


def test_the_switching_order_numbers_isolation_then_restoration(falkordb, graph):
    apply_fault(graph, "4-5")
    locate_fault(graph)
    incident_id, _ = isolate(graph)
    restore(falkordb, graph, incident_id, approve=lambda plan: True)

    order = explain_incident(graph, incident_id)["switching_order"]
    assert [s["step"] for s in order] == list(range(1, 10))
    assert [(s["op"], s["switch_id"]) for s in order[:5]] == [
        ("open", "2-3"), ("open", "3-23"), ("open", "6-7"), ("open", "6-26"), ("close", "1-2")]
    assert {s["phase"] for s in order[5:]} == {"restore"}
    assert order[4]["switch_kind"] == "breaker" and order[-1]["switch_kind"] == "tie"
