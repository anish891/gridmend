from falkordb import Graph

from gridmend.flisr.trace import (
    SwitchOp,
    open_incident,
    record_incident,
    record_plan,
    set_incident_status,
)
from gridmend.grid.energize import apply_switch_ops


class IsolateError(Exception):
    pass


def faulted_zone(g: Graph) -> int | None:
    rows = g.ro_query("MATCH (z:Zone {faulted: true}) RETURN z.id").result_set
    return rows[0][0] if rows else None


def isolation_ops(g: Graph, zone: int) -> list[SwitchOp]:
    # Open every closed switch on the zone's edge, upstream side first, then reclose
    # the breaker so everything upstream of the fault is fed again.
    rows = g.ro_query(
        """
        MATCH (b:Bus)-[:IN_ZONE]->(:Zone {id: $zone}), (b)-[s:SW]-(o:Bus)
        WHERE s.status = 'closed' AND coalesce(o.zone, -1) <> $zone
        RETURN s.id, endNode(s) = b AS upstream
        """,
        {"zone": zone},
    ).result_set
    boundary = sorted(rows, key=lambda r: (not r[1], [int(x) for x in r[0].split("-")]))
    ops = [SwitchOp(switch_id, "open") for switch_id, _ in boundary]

    breaker = g.ro_query(
        "MATCH ()-[s:SW {kind: 'breaker'}]->() RETURN s.id, s.status"
    ).result_set[0]
    isolated = {op.switch_id for op in ops}
    if breaker[1] == "open" and breaker[0] not in isolated:
        ops.append(SwitchOp(breaker[0], "close"))
    return ops


def isolate(g: Graph) -> tuple[str, list[SwitchOp]]:
    zone = faulted_zone(g)
    if zone is None:
        raise IsolateError("no located fault; run locate first")
    incident = open_incident(g)
    if incident and incident["status"] != "located":
        raise IsolateError(f"Z{zone} is already isolated ({incident['id']})")

    incident_id = incident["id"] if incident else record_incident(g, zone)
    ops = isolation_ops(g, zone)
    apply_switch_ops(g, [(o.switch_id, o.op) for o in ops])
    record_plan(g, incident_id, f"{incident_id}-iso", "isolation", "executed", ops)
    set_incident_status(g, incident_id, "isolated")
    return incident_id, ops
