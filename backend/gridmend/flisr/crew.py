import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import openai
from falkordb import Graph

from gridmend.agent.llm import parse_json_object
from gridmend.flisr.trace import open_incident

if TYPE_CHECKING:
    from openai import OpenAI


class CrewError(Exception):
    pass


class CrewModelError(CrewError):
    pass


@dataclass(frozen=True)
class Finding:
    switch_id: str
    operable: bool
    evidence: str


PROMPT = """You read a field crew's note about a power distribution feeder and record what it says
about the feeder's switches.

SWITCHES ON THE FEEDER (id is "lower bus-higher bus")
{switches}

CREW NOTE
{note}

List every listed switch the note says cannot be operated (jammed, seized, stuck, broken, will not
open or close), and every one it says is repaired and works again. Use the id exactly as listed:
a switch "between 6 and 7", "6/7" or "S6-7" is "6-7". Ignore anything not about a listed switch.
Copy the evidence word for word from the note. If the note says which position a switch is
stuck in, give it as "open" or "closed"; otherwise null.
Reply with only a JSON object:
{{"findings": [{{"switch": "<id>", "operable": <false if it cannot be operated, true if repaired>,
"stuck": <"open", "closed" or null>, "evidence": "<exact words from the note>"}}],
"other": "<anything else the crew reported, in one short sentence, or an empty string>"}}"""


def switch_list(g: Graph) -> list[dict]:
    rows = g.ro_query(
        "MATCH (a:Bus)-[s:SW]->(b:Bus) RETURN s.id, s.kind, s.status ORDER BY a.id, b.id"
    ).result_set
    return [{"id": sid, "kind": kind, "status": status} for sid, kind, status in rows]


def ask_findings(client: "OpenAI", model: str, switches: list[dict], note: str) -> dict:
    listing = "\n".join(f"- {s['id']} ({s['kind']}, {s['status']})" for s in switches)
    reply = client.chat.completions.create(
        model=model,
        temperature=0,
        # Reasoning models spend most of their budget thinking before the JSON appears.
        max_tokens=8000,
        messages=[{"role": "user", "content": PROMPT.format(switches=listing, note=note)}],
    )
    return parse_json_object(reply.choices[0].message.content)


def normalized(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def validate(reply: dict, known: dict[str, str], note: str) -> tuple[list[Finding], str]:
    """Accept a finding only for a real switch and only if its evidence is in the note itself,
    so a switch the model imagines can never be marked jammed. `known` maps each switch to its
    position in the twin; a jam is recorded in that position, so a note that puts the switch in
    the other one is refused until the operator confirms where it really is."""
    entries = reply.get("findings")
    if not isinstance(entries, list):
        raise CrewError("the reply has no list of findings")
    findings: dict[str, Finding] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise CrewError(f"not a finding: {entry!r}")
        switch, operable, evidence = entry.get("switch"), entry.get("operable"), entry.get("evidence")
        if switch not in known:
            raise CrewError(f"unknown switch {switch!r}")
        if switch in findings:
            raise CrewError(f"{switch} is reported twice")
        if not isinstance(operable, bool):
            raise CrewError(f"{switch}: operable must be true or false, not {operable!r}")
        if not isinstance(evidence, str) or not normalized(evidence) \
                or normalized(evidence) not in normalized(note):
            raise CrewError(f"{switch}: the evidence {evidence!r} is not in the crew's note")
        stuck = entry.get("stuck")
        if not operable and stuck in ("open", "closed") and stuck != known[switch]:
            raise CrewError(f"the crew says {switch} is stuck {stuck}, but the twin shows it "
                            f"{known[switch]}; confirm its position before filing")
        findings[switch] = Finding(switch, operable, evidence.strip())
    other = reply.get("other")
    return list(findings.values()), other.strip()[:300] if isinstance(other, str) else ""


def record_report(g: Graph, note: str, findings: list[Finding], other: str, model: str) -> str:
    count = g.ro_query("MATCH (r:CrewReport) RETURN count(r)").result_set[0][0]
    report_id = f"cr{count + 1}"
    rows = [{"switch": f.switch_id, "operable": f.operable, "evidence": f.evidence} for f in findings]
    g.query(
        "CREATE (:CrewReport {id: $id, note: $note, other: $other, model: $model, ts: $ts})",
        {"id": report_id, "note": note, "other": other, "model": model,
         "ts": datetime.now(timezone.utc).isoformat()},
    )
    g.query(
        """
        MATCH (r:CrewReport {id: $id})
        UNWIND $rows AS f
        MATCH ()-[s:SW {id: f.switch}]->()
        SET s.operable = f.operable, s.report = CASE WHEN f.operable THEN null ELSE $id END
        CREATE (r)-[:FINDS]->(:Finding {switch_id: f.switch, operable: f.operable,
                                        evidence: f.evidence})
        """,
        {"id": report_id, "rows": rows},
    )
    incident = open_incident(g)
    if incident:
        g.query(
            "MATCH (i:Incident {id: $incident}), (r:CrewReport {id: $id}) CREATE (i)-[:HAS_REPORT]->(r)",
            {"incident": incident["id"], "id": report_id},
        )
    return report_id


def reject_plans_using(g: Graph, report_id: str, switches: list[str]) -> list[str]:
    """A plan verified before the jam was known can no longer be carried out."""
    incident = open_incident(g)
    if not incident or not switches:
        return []
    rows = g.query(
        """
        MATCH (:Incident {id: $incident})-[:HAS_PLAN]->(p:Plan {kind: 'restoration', status: 'verified'})
              -[:STEP]->(o:SwitchOp)
        WHERE o.switch_id IN $switches
        SET p.status = 'rejected', p.recommended = false
        CREATE (p)-[:VIOLATES]->(:Violation {
            type: 'operability',
            detail: o.switch_id + ' is jammed and cannot be operated (crew report ' + $report + ')'})
        RETURN DISTINCT p.id ORDER BY p.id
        """,
        {"incident": incident["id"], "switches": switches, "report": report_id},
    ).result_set
    return [row[0] for row in rows]


def file_report(g: Graph, client: "OpenAI", model: str, note: str) -> dict:
    """Turn a crew's free-text note into switch states on the graph."""
    note = note.strip()
    if not note:
        raise CrewError("paste the crew's note")
    switches = switch_list(g)
    try:
        reply = ask_findings(client, model, switches, note)
    except openai.APIError as exc:
        raise CrewModelError(f"the model could not answer: {exc.message}") from exc
    except ValueError as exc:
        raise CrewError(f"the model's reply was not valid JSON: {exc}") from exc
    findings, other = validate(reply, {s["id"]: s["status"] for s in switches}, note)
    report_id = record_report(g, note, findings, other, model)
    rejected = reject_plans_using(g, report_id, [f.switch_id for f in findings if not f.operable])
    return {
        "report": report_id,
        "findings": [f.__dict__ for f in findings],
        "other": other,
        "rejected_plans": rejected,
    }


def crew_reports(g: Graph, incident_id: str | None = None) -> list[dict]:
    """Every crew report, or only those an incident took into account."""
    scope = ("MATCH (:Incident {id: $incident})-[:HAS_REPORT]->(r:CrewReport)" if incident_id
             else "MATCH (r:CrewReport)")
    rows = g.ro_query(
        scope + """
        OPTIONAL MATCH (r)-[:FINDS]->(f:Finding)
        RETURN r.id, r.note, r.other, collect([f.switch_id, f.operable, f.evidence])
        ORDER BY r.id
        """,
        {"incident": incident_id},
    ).result_set
    return [
        {"id": rid, "note": note, "other": other,
         "findings": [{"switch_id": s, "operable": op, "evidence": ev}
                      for s, op, ev in findings if s is not None]}
        for rid, note, other, findings in rows
    ]


def jammed_switches(g: Graph) -> list[dict]:
    rows = g.ro_query(
        "MATCH ()-[s:SW]->() WHERE s.operable = false RETURN s.id, s.status, s.report ORDER BY s.id"
    ).result_set
    return [{"id": sid, "stuck": status, "report": report} for sid, status, report in rows]
