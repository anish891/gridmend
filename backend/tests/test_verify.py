import pytest

from gridmend.flisr.isolate import isolate
from gridmend.flisr.locate import locate_fault
from gridmend.flisr.trace import SwitchOp
from gridmend.flisr.verify import check_capacity, energized_zones, verify_plan
from gridmend.sim import apply_fault


@pytest.fixture
def z3_isolated(falkordb, graph):
    apply_fault(graph, "4-5")
    locate_fault(graph)
    incident_id, _ = isolate(graph)
    return falkordb, graph, incident_id


def verify(z3_isolated, plan_id, closes=(), opens=()):
    conn, g, incident_id = z3_isolated
    ops = [SwitchOp(s, "open") for s in opens] + [SwitchOp(s, "close") for s in closes]
    return verify_plan(conn, g, incident_id, plan_id, ops)


def kinds(result):
    return [kind for kind, _ in result.violations]


def test_closing_both_ties_into_z19_closes_a_loop(z3_isolated):
    result = verify(z3_isolated, "t1", closes=["8-21", "12-22"])
    assert kinds(result) == ["radiality"]


def test_closing_8_21_alone_overloads_lateral_2_19(z3_isolated):
    result = verify(z3_isolated, "t2", closes=["8-21"])
    assert set(kinds(result)) == {"capacity"}
    assert result.violations[0][1].startswith("2-19 carries 1584 kVA of 1400 kVA")
    assert result.restored_zones == [7, 10, 14]


def test_shedding_downstream_of_z7(z3_isolated):
    result = verify(z3_isolated, "t3", closes=["8-21"], opens=["9-10"])
    assert result.passed
    assert result.restored_zones == [7]
    assert result.restored_kw == pytest.approx(460)
    assert result.max_loading_pct < 100


def test_hospital_and_fire_station_fit_when_z10_is_shed(z3_isolated):
    result = verify(z3_isolated, "t8", closes=["8-21", "9-15"], opens=["9-10", "13-14"])
    assert result.passed
    assert result.restored_zones == [7, 14]


def test_capacity_is_skipped_when_radiality_fails(z3_isolated):
    result = verify(z3_isolated, "t9", closes=["8-21", "12-22"])
    assert kinds(result) == ["radiality"]
    assert result.max_loading_pct is None


def test_healthy_base_case_is_within_ratings(graph):
    violations, max_loading = check_capacity(graph)
    assert violations == []
    assert 90 < max_loading < 100


def test_reenergizing_the_faulted_zone_violates_isolation(z3_isolated):
    result = verify(z3_isolated, "t4", closes=["2-3"])
    assert "isolation" in kinds(result)


def test_claimed_zone_that_stays_dark_violates_connectivity(z3_isolated):
    result = verify(z3_isolated, "t5", closes=["8-21"], opens=["2-19"])
    assert kinds(result) == ["connectivity"]
    assert result.dropped_zones == [19]


def test_verification_leaves_live_graph_untouched_and_records_plans(z3_isolated):
    conn, g, _ = z3_isolated
    before = energized_zones(g)
    verify(z3_isolated, "t6", closes=["8-21", "12-22"])
    verify(z3_isolated, "t7", closes=["8-21"], opens=["9-10"])

    assert energized_zones(g) == before
    assert not [name for name in conn.list_graphs() if name.startswith("fork_")]
    plans = g.ro_query(
        "MATCH (:Incident)-[:HAS_PLAN]->(p:Plan {kind: 'restoration'}) "
        "RETURN p.id, p.status ORDER BY p.id"
    ).result_set
    assert plans == [["t6", "rejected"], ["t7", "verified"]]


def test_violations_are_stored_on_the_plan(z3_isolated):
    _, g, _ = z3_isolated
    verify(z3_isolated, "t10", closes=["8-21"])
    stored = g.ro_query(
        "MATCH (:Plan {id: 't10'})-[:VIOLATES]->(v:Violation) RETURN v.type, v.detail"
    ).result_set
    assert ["capacity", "2-19 carries 1584 kVA of 1400 kVA (113%)"] in stored
