from dataclasses import dataclass
from datetime import datetime, timezone

from falkordb import Graph


@dataclass(frozen=True)
class SwitchOp:
    switch_id: str
    op: str  # "open" or "close"


def open_incident(g: Graph) -> dict | None:
    rows = g.ro_query(
        """
        MATCH (i:Incident)-[:FAULT_IN]->(z:Zone)
        WHERE i.status <> 'closed'
        RETURN i.id, i.status, z.id
        """
    ).result_set
    if not rows:
        return None
    incident_id, status, zone = rows[0]
    return {"id": incident_id, "status": status, "zone": zone}


def record_incident(g: Graph, zone: int) -> str:
    count = g.ro_query("MATCH (i:Incident) RETURN count(i)").result_set[0][0]
    incident_id = f"inc{count + 1}"
    # The simulator knows which segment it faulted; the agent only ever sees the zone.
    g.query(
        """
        MATCH (z:Zone {id: $zone})
        OPTIONAL MATCH ()-[s:SEG {faulted: true}]->()
        CREATE (i:Incident {id: $id, ts: $ts, fault_line: s.id, status: 'located'})
        CREATE (i)-[:FAULT_IN]->(z)
        """,
        {"id": incident_id, "zone": zone, "ts": datetime.now(timezone.utc).isoformat()},
    )
    # The incident is handled under the operating conditions in force when it was found.
    g.query(
        """
        MATCH (i:Incident {id: $id}), (c:Context {current: true})
        CREATE (i)-[:UNDER]->(c)
        """,
        {"id": incident_id},
    )
    # Crew reports filed before the fault (e.g. a switch found jammed on patrol) apply to it too.
    g.query(
        """
        MATCH (i:Incident {id: $id}), (r:CrewReport)
        WHERE NOT ()-[:HAS_REPORT]->(r)
        CREATE (i)-[:HAS_REPORT]->(r)
        """,
        {"id": incident_id},
    )
    return incident_id


def set_incident_status(g: Graph, incident_id: str, status: str) -> None:
    g.query("MATCH (i:Incident {id: $id}) SET i.status = $status", {"id": incident_id, "status": status})


def record_plan(
    g: Graph, incident_id: str, plan_id: str, kind: str, status: str, ops: list[SwitchOp]
) -> None:
    g.query(
        """
        MATCH (i:Incident {id: $incident})
        CREATE (i)-[:HAS_PLAN]->(p:Plan {id: $id, kind: $kind, status: $status,
                                         switch_ops: size($ops)})
        WITH p
        UNWIND range(0, size($ops) - 1) AS seq
        CREATE (p)-[:STEP]->(:SwitchOp {seq: seq + 1, switch_id: $ops[seq].switch_id,
                                        op: $ops[seq].op})
        """,
        {
            "incident": incident_id,
            "id": plan_id,
            "kind": kind,
            "status": status,
            "ops": [{"switch_id": o.switch_id, "op": o.op} for o in ops],
        },
    )


TRACE_QUERY = """
MATCH (i:Incident {id: $id})-[:HAS_PLAN]->(p:Plan)-[:STEP]->(o:SwitchOp)
WITH p, o ORDER BY o.seq
RETURN p.id AS plan, p.kind AS kind, p.status AS status,
       collect(o.op + ' ' + o.switch_id) AS steps
ORDER BY p.id
"""


def incident_trace(g: Graph, incident_id: str) -> list[dict]:
    rows = g.ro_query(TRACE_QUERY, {"id": incident_id}).result_set
    return [dict(zip(("plan", "kind", "status", "ops"), row)) for row in rows]


def plan_details(g: Graph, incident_id: str) -> list[dict]:
    """Every restoration plan of an incident with its steps, outcome and violations."""
    params = {"id": incident_id}
    scope = "MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {kind: 'restoration'})"
    plans = {
        plan_id: {"plan_id": plan_id, "status": status, "restored_kw": kw or 0.0, "min_v_pu": min_v,
                  "max_loading_pct": loading, "recommended": bool(recommended),
                  "recommendation": reason, "ops": [], "violations": [], "restored_zones": [],
                  "critical": []}
        for plan_id, status, kw, min_v, loading, recommended, reason in g.ro_query(
            scope + """
            RETURN p.id, p.status, p.restored_kw, p.min_v_pu, p.max_loading_pct,
                   p.recommended, p.recommendation
            """, params).result_set
    }
    for plan_id, op, switch_id in g.ro_query(
        scope + "-[:STEP]->(o:SwitchOp) RETURN p.id, o.op, o.switch_id ORDER BY o.seq", params
    ).result_set:
        plans[plan_id]["ops"].append({"switch_id": switch_id, "op": op})
    for plan_id, kind, detail in g.ro_query(
        scope + "-[:VIOLATES]->(v:Violation) RETURN p.id, v.type, v.detail", params
    ).result_set:
        plans[plan_id]["violations"].append({"type": kind, "detail": detail})
    for plan_id, zone in g.ro_query(
        scope + "-[:RESTORES]->(z:Zone) RETURN p.id, z.id ORDER BY z.id", params
    ).result_set:
        plans[plan_id]["restored_zones"].append(zone)
    for plan_id, facility in g.ro_query(
        scope + """
        -[:RESTORES]->(:Zone)<-[:IN_ZONE]-(b:Bus {critical: true})
        RETURN p.id, b.facility ORDER BY b.id
        """, params
    ).result_set:
        plans[plan_id]["critical"].append(facility)
    return sorted(plans.values(), key=lambda p: int(p["plan_id"].rsplit("p", 1)[1]))
