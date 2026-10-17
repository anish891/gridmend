from falkordb import Graph

from gridmend.flisr.trace import (
    SwitchOp,
    open_incident,
    record_incident,
    record_plan,
    set_incident_status,
)
from gridmend.flisr.locate import locate_fault
from gridmend.grid.energize import apply_switch_ops
from gridmend.sim import trip

# A fault hidden behind another is found on each reclose; more rounds than faults means a bug.
MAX_ROUNDS = 5


class IsolateError(Exception):
    pass


# Zones a jammed closed switch ties to the region: they cannot be separated from the fault.
JAMMED_NEIGHBOURS_QUERY = """
MATCH (b:Bus)-[:IN_ZONE]->(z:Zone), (b)-[s:SW]-(:Bus)-[:IN_ZONE]->(o:Zone)
WHERE z.id IN $zones AND NOT o.id IN $zones AND s.status = 'closed' AND s.operable = false
RETURN DISTINCT o.id, s.id
"""

BOUNDARY_QUERY = """
MATCH (b:Bus)-[:IN_ZONE]->(z:Zone), (b)-[s:SW]-(o:Bus)
WHERE z.id IN $zones AND s.status = 'closed' AND NOT coalesce(o.zone, -1) IN $zones
RETURN s.id AS switch, endNode(s) = b AS upstream
"""

BREAKER_QUERY = """
MATCH (:Bus {is_source: true})-[s:SW {kind: 'breaker'}]->(b:Bus)
RETURN s.id, s.status, coalesce(s.operable, true), b.zone
"""


def faulted_zones(g: Graph) -> list[int]:
    return [row[0] for row in g.ro_query(
        "MATCH (z:Zone {faulted: true}) RETURN z.id ORDER BY z.id"
    ).result_set]


def faulted_zone(g: Graph) -> int | None:
    zones = faulted_zones(g)
    return zones[0] if zones else None


def isolation_region(g: Graph, zone: int) -> dict[int, str | None]:
    """The faulted zone plus every zone joined to it by a jammed closed switch, each mapped to
    the jammed switch that drags it in. They are cut out together."""
    region: dict[int, str | None] = {zone: None}
    while True:
        rows = g.ro_query(JAMMED_NEIGHBOURS_QUERY, {"zones": list(region)}).result_set
        added = {z: switch for z, switch in rows if z not in region}
        if not added:
            return region
        region.update(added)


def isolation_ops(g: Graph, zones: list[int]) -> list[SwitchOp]:
    # Open every closed switch on the region's edge, upstream side first, then reclose the
    # breaker so everything upstream is fed again, unless the breaker itself borders the region.
    rows = g.ro_query(BOUNDARY_QUERY, {"zones": zones}).result_set
    boundary = sorted(rows, key=lambda r: (not r[1], [int(x) for x in r[0].split("-")]))
    ops = [SwitchOp(switch_id, "open") for switch_id, _ in boundary]

    breaker, status, operable, fed_zone = g.ro_query(BREAKER_QUERY).result_set[0]
    if status == "open" and operable and fed_zone not in zones:
        ops.append(SwitchOp(breaker, "close"))
    return ops


def isolate(g: Graph) -> tuple[str, list[SwitchOp]]:
    """Cut out every located faulted zone and reclose the breaker. If the breaker trips again,
    another fault was hidden upstream: the incident goes back to 'tripped' to be located."""
    zones = faulted_zones(g)
    if not zones:
        raise IsolateError("no located fault; run locate first")
    incident = open_incident(g)
    if incident and incident["status"] != "located":
        raise IsolateError(f"{incident['id']} is already {incident['status']}")

    incident_id = incident["id"] if incident else record_incident(g, zones)
    region: dict[int, str | None] = {}
    for zone in zones:
        region.update(isolation_region(g, zone))
    region.update({zone: None for zone in zones})
    ops = isolation_ops(g, list(region))
    apply_switch_ops(g, [(o.switch_id, o.op) for o in ops])
    # Healthy zones cut out with the fault are recorded with the jammed switch that forced it.
    g.query(
        """
        UNWIND $cut AS c
        MATCH (i:Incident {id: $incident}), (z:Zone {id: c.zone})
        SET z.cut_off = true
        MERGE (i)-[:CUTS_OFF {via: c.switch}]->(z)
        """,
        {"incident": incident_id,
         "cut": [{"zone": z, "switch": s} for z, s in region.items() if s is not None]},
    )
    record_plan(g, incident_id, isolation_plan_id(g, incident_id), "isolation", "executed", ops)
    set_incident_status(g, incident_id, "tripped" if trip(g) else "isolated")
    return incident_id, ops


def isolation_plan_id(g: Graph, incident_id: str) -> str:
    done = g.ro_query(
        "MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {kind: 'isolation'}) RETURN count(p)",
        {"id": incident_id},
    ).result_set[0][0]
    return f"{incident_id}-iso" if done == 0 else f"{incident_id}-iso{done + 1}"


def isolation_plans(g: Graph, incident_id: str) -> list[str]:
    return [row[0] for row in g.ro_query(
        "MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {kind: 'isolation'}) RETURN p.id ORDER BY p.id",
        {"id": incident_id},
    ).result_set]


def secure(g: Graph) -> tuple[str, list[SwitchOp]]:
    """Locate and isolate until the breaker holds, returning every isolation operation."""
    ops: list[SwitchOp] = []
    for _ in range(MAX_ROUNDS):
        incident = open_incident(g)
        if incident is None or incident["status"] == "tripped":
            locate_fault(g)
        incident_id, round_ops = isolate(g)
        ops += round_ops
        if open_incident(g)["status"] == "isolated":
            return incident_id, ops
    raise IsolateError(f"the breaker still trips after {MAX_ROUNDS} rounds of isolation")
