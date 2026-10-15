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
