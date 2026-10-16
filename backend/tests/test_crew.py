import json

import pytest

from gridmend.flisr.candidates import generate_candidates, plan_restoration
from gridmend.flisr.crew import CrewError, file_report, jammed_switches, validate
from gridmend.flisr.execute import restore
from gridmend.flisr.explain import explain_incident
from gridmend.flisr.isolate import isolate
from gridmend.flisr.locate import locate_fault
from gridmend.flisr.trace import SwitchOp, plan_details
from gridmend.flisr.verify import verify_plan
from gridmend.sim import apply_fault

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


def isolated(graph, line):
    apply_fault(graph, line)
    locate_fault(graph)
    incident_id, ops = isolate(graph)
    return incident_id, [(o.op, o.switch_id) for o in ops]


def test_a_switch_jammed_on_patrol_cuts_its_neighbour_out_with_the_fault(falkordb, graph, fixed_model):
    model = fixed_model(reply(("6-7", False, "it is JAMMED shut"), other="tree near pole 14"))
    report = file_report(graph, model, "m", NOTE)
    assert report["report"] == "cr1" and "6-7" in model.prompt
    assert jammed_switches(graph) == [{"id": "6-7", "stuck": "closed", "report": "cr1"}]

    incident_id, ops = isolated(graph, "4-5")

    # 6-7 cannot open, so Z7 goes with the fault and 9-10 opens instead.
    assert ops == [("open", "2-3"), ("open", "3-23"), ("open", "6-26"), ("open", "9-10"),
                   ("close", "1-2")]
    _, executed = restore(falkordb, graph, incident_id, approve=lambda plan: True)
    assert executed["critical"] == ["fire station"]

    explanation = explain_incident(graph, incident_id)
    assert explanation["cut_off"] == [{"zone": 7, "via": "6-7"}]
    assert [r["id"] for r in explanation["crew_reports"]] == ["cr1"]
    hospital = next(c for c in explanation["critical"] if c["facility"] == "hospital")
    assert "6-7 is jammed" in hospital["reason"]


def test_planning_never_operates_a_jammed_switch_or_feeds_a_cut_off_zone(graph, fixed_model):
    file_report(graph, fixed_model(reply(("6-7", False, "it is JAMMED shut"))), "m", NOTE)
    isolated(graph, "4-5")

    for candidate in generate_candidates(graph):
        switches = {o.switch_id for o in candidate.ops}
        # 8-21 and 9-15 are the ties into the cut-off Z7.
        assert not switches & {"6-7", "8-21", "9-15"}
        assert 7 not in candidate.claims


def test_a_jam_found_mid_incident_rejects_the_plans_that_need_it(falkordb, graph, fixed_model):
    incident_id, _ = isolated(graph, "4-5")
    plan_restoration(falkordb, graph, incident_id)
    users = {p["plan_id"] for p in plan_details(graph, incident_id)
             if p["status"] == "verified" and any(o["switch_id"] == "12-22" for o in p["ops"])}
    assert users

    note = "Crew at the 12-22 tie: it will not close, the spring mechanism is broken."
    report = file_report(graph, fixed_model(reply(("12-22", False, "it will not close"))), "m", note)

    assert set(report["rejected_plans"]) == users
    details = {p["plan_id"]: p for p in plan_details(graph, incident_id)}
    for plan_id in users:
        assert details[plan_id]["status"] == "rejected"
        assert details[plan_id]["violations"][-1]["type"] == "operability"
    assert all("12-22" not in {o.switch_id for o in c.ops} for c in generate_candidates(graph))


def test_verification_refuses_a_plan_that_operates_a_jammed_switch(falkordb, graph, fixed_model):
    incident_id, _ = isolated(graph, "4-5")
    file_report(graph, fixed_model(reply(("8-21", False, "8-21 is seized"))), "m", "8-21 is seized")

    result = verify_plan(falkordb, graph, incident_id, "inc1-px", [SwitchOp("8-21", "close")])
    assert not result.passed
    assert result.violations[0] == ("operability",
                                    "8-21 is jammed and cannot be operated (crew report cr1)")


def test_a_repair_report_frees_the_switch(graph, fixed_model):
    file_report(graph, fixed_model(reply(("6-7", False, "it is JAMMED shut"))), "m", NOTE)
    file_report(graph, fixed_model(reply(("6-7", True, "6-7 freed and tested"))), "m",
                "Switch 6-7 freed and tested, works normally.")
    assert jammed_switches(graph) == []
    _, ops = isolated(graph, "4-5")
    assert ("open", "6-7") in ops


def test_a_bad_reply_changes_nothing(graph, fixed_model):
    with pytest.raises(CrewError):
        file_report(graph, fixed_model(reply(("6-7", False, "seized by gremlins"))), "m", NOTE)
    with pytest.raises(CrewError):
        file_report(graph, fixed_model("Switch 6-7 looks jammed to me."), "m", NOTE)
    assert jammed_switches(graph) == []
    assert graph.ro_query("MATCH (r:CrewReport) RETURN count(r)").result_set[0][0] == 0
