from gridmend.flisr.candidates import TOP_N, generate_candidates, plan_restoration
from gridmend.flisr.isolate import isolate
from gridmend.flisr.locate import locate_fault
from gridmend.sim import apply_fault


def isolated(graph, line):
    apply_fault(graph, line)
    locate_fault(graph)
    incident_id, _ = isolate(graph)
    return incident_id


def ops(plan):
    return sorted((o.op, o.switch_id) for o in plan.ops)


def test_z3_candidates_mix_naive_and_repaired_plans(falkordb, graph):
    incident_id = isolated(graph, "4-5")
    verified = plan_restoration(falkordb, graph, incident_id)
    assert len(verified) == TOP_N

    by_ops = {tuple(ops(c)): r for c, r in verified}
    greedy = by_ops[(("close", "12-22"), ("close", "8-21"))]
    assert [k for k, _ in greedy.violations] == ["radiality"]
    tie_only = by_ops[(("close", "8-21"),)]
    assert {k for k, _ in tie_only.violations} == {"capacity"}

    passing = [(c, r) for c, r in verified if r.passed]
    best, best_result = max(passing, key=lambda cr: cr[0].score)
    assert best_result.restored_zones == [7, 14]
    assert ops(best) == [("close", "8-21"), ("close", "9-15"), ("open", "13-14"), ("open", "9-10")]


def test_candidates_are_ranked_by_score(graph):
    isolated(graph, "4-5")
    scores = [c.score for c in generate_candidates(graph)]
    assert scores == sorted(scores, reverse=True)


def test_isolation_switches_are_never_decision_variables(graph):
    isolated(graph, "4-5")
    touched = {o.switch_id for c in generate_candidates(graph) for o in c.ops}
    assert touched.isdisjoint({"2-3", "6-7", "3-23", "6-26", "1-2"})


def test_voltage_limited_fault_has_no_passing_plan(falkordb, graph):
    incident_id = isolated(graph, "27-28")
    verified = plan_restoration(falkordb, graph, incident_id)
    assert verified
    assert all(not r.passed for _, r in verified)
    assert {k for _, r in verified for k, _ in r.violations} == {"voltage"}


def test_nothing_to_restore_when_only_the_faulted_lateral_is_dark(graph):
    isolated(graph, "23-24")
    assert generate_candidates(graph) == []
