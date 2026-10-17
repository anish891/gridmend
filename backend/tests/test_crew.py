import json

import pytest

from gridmend.flisr.crew import CrewError, file_report, jammed_switches, validate
from gridmend.flisr.explain import explain_incident
from gridmend.flisr.locate import declare_fault
from gridmend.flisr.trace import SwitchOp, plan_details
from gridmend.flisr.verify import verify_plan
from gridmend.sim import apply_fault

from conftest import isolated, run_plan

KNOWN = {"6-7": "closed", "9-10": "closed", "12-22": "open", "8-21": "open"}
NOTE = "Patrol at the 6-7 sectionalizer: it is JAMMED shut, operating handle seized. Tree near pole 14."


def reply(*findings, other="") -> str:
    return json.dumps({"findings": [
        {"switch": s, "operable": op, "evidence": ev} for s, op, ev in findings
    ], "other": other})


@pytest.mark.parametrize("finding, problem", [
    ({"switch": "6-8", "operable": False, "evidence": "jammed"}, "unknown switch"),
    ({"switch": "6-7", "operable": "no", "evidence": "jammed"}, "true or false"),
    ({"switch": "6-7", "operable": False, "evidence": "rusted solid"}, "not in the crew's note"),
    ({"switch": "6-7", "operable": False, "evidence": ""}, "not in the crew's note"),
    ({"switch": "6-7", "operable": False, "stuck": "open", "evidence": "JAMMED"}, "confirm its position"),
])
def test_findings_must_name_a_real_switch_and_quote_the_note(finding, problem):
    with pytest.raises(CrewError, match=problem):
        validate({"findings": [finding]}, KNOWN, NOTE)


def test_evidence_matches_despite_case_and_punctuation():
    findings, other = validate(
        {"findings": [{"switch": "6-7", "operable": False, "stuck": "closed",
                       "evidence": "it is jammed shut operating handle seized"}],
         "other": "tree near pole 14"}, KNOWN, NOTE)
    assert [(f.switch_id, f.operable) for f in findings] == [("6-7", False)]
    assert other == "tree near pole 14"


Z3_ISOLATION = {"open": ["2-3", "3-23", "6-26", "9-10"], "close": ["1-2"]}


def test_a_switch_jammed_on_patrol_cuts_its_neighbour_out_with_the_fault(falkordb, graph, fixed_model):
    model = fixed_model(reply(("6-7", False, "it is JAMMED shut"), other="tree near pole 14"))
    report = file_report(graph, model, "m", NOTE)
    assert report["report"] == "cr1" and "6-7" in model.prompt
    assert jammed_switches(graph) == [{"id": "6-7", "stuck": "closed", "report": "cr1"}]

    apply_fault(graph, "4-5")
    incident_id = declare_fault(graph, [3], "1-2 and 2-3 set")
    # 6-7 cannot open, so the agent's first isolation fails and Z7 has to go with the fault.
    usual = run_plan(falkordb, graph, incident_id, kind="isolation",
                     open=["2-3", "3-23", "6-7", "6-26"], close=["1-2"])
    assert usual.violations[0][0] == "operability"
    assert run_plan(falkordb, graph, incident_id, kind="isolation", **Z3_ISOLATION).passed
    assert 14 in run_plan(falkordb, graph, incident_id, close=["12-22"]).restored_zones

    explanation = explain_incident(graph, incident_id)
    assert explanation["cut_off"] == [{"zone": 7, "via": "6-7"}]
    assert [r["id"] for r in explanation["crew_reports"]] == ["cr1"]
    hospital = next(c for c in explanation["critical"] if c["facility"] == "hospital")
    assert "6-7 is jammed" in hospital["reason"]


def test_feeding_a_zone_cut_off_by_a_jam_reenergizes_the_fault(falkordb, graph, fixed_model):
    file_report(graph, fixed_model(reply(("6-7", False, "it is JAMMED shut"))), "m", NOTE)
    apply_fault(graph, "4-5")
    incident_id = declare_fault(graph, [3], "1-2 and 2-3 set")
    run_plan(falkordb, graph, incident_id, kind="isolation", **Z3_ISOLATION)

    # 8-21 would feed Z7, and through the jammed 6-7 the faulted Z3 behind it.
    into_z7 = run_plan(falkordb, graph, incident_id, close=["8-21"])
    assert "isolation" in {kind for kind, _ in into_z7.violations}
    assert run_plan(falkordb, graph, incident_id, close=["12-22"]).passed


def test_a_jam_found_mid_incident_rejects_the_plans_that_need_it(falkordb, graph, fixed_model):
    incident_id = isolated(graph, "4-5")
    plan = verify_plan(falkordb, graph, incident_id, "inc1-p1",
                       [SwitchOp("13-14", "open"), SwitchOp("12-22", "close")])
    assert plan.passed

    note = "Crew at the 12-22 tie: it will not close, the spring mechanism is broken."
    report = file_report(graph, fixed_model(reply(("12-22", False, "it will not close"))), "m", note)

    assert report["rejected_plans"] == [plan.plan_id]
    details = {p["plan_id"]: p for p in plan_details(graph, incident_id)}
    assert details[plan.plan_id]["status"] == "rejected"
    assert details[plan.plan_id]["violations"][-1]["type"] == "operability"


def test_verification_refuses_a_plan_that_operates_a_jammed_switch(falkordb, graph, fixed_model):
    incident_id = isolated(graph, "4-5")
    file_report(graph, fixed_model(reply(("8-21", False, "8-21 is seized"))), "m", "8-21 is seized")

    result = verify_plan(falkordb, graph, incident_id, "inc1-px", [SwitchOp("8-21", "close")])
    assert not result.passed
    assert result.violations[0] == ("operability",
                                    "8-21 is jammed and cannot be operated (crew report cr1)")


def test_a_repair_report_frees_the_switch(falkordb, graph, fixed_model):
    file_report(graph, fixed_model(reply(("6-7", False, "it is JAMMED shut"))), "m", NOTE)
    file_report(graph, fixed_model(reply(("6-7", True, "6-7 freed and tested"))), "m",
                "Switch 6-7 freed and tested, works normally.")
    assert jammed_switches(graph) == []
    apply_fault(graph, "4-5")
    incident_id = declare_fault(graph, [3], "1-2 and 2-3 set")
    assert run_plan(falkordb, graph, incident_id, kind="isolation",
                    open=["2-3", "3-23", "6-7", "6-26"], close=["1-2"]).passed


def test_a_bad_reply_changes_nothing(graph, fixed_model):
    with pytest.raises(CrewError):
        file_report(graph, fixed_model(reply(("6-7", False, "seized by gremlins"))), "m", NOTE)
    with pytest.raises(CrewError):
        file_report(graph, fixed_model("Switch 6-7 looks jammed to me."), "m", NOTE)
    assert jammed_switches(graph) == []
    assert graph.ro_query("MATCH (r:CrewReport) RETURN count(r)").result_set[0][0] == 0
