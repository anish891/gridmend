"""Deterministic fault location and isolation for the evaluation baselines (isolate-only,
greedy, llm-only), which all start from the same isolated feeder. The agent does its own."""
from dataclasses import dataclass

from falkordb import Graph

from gridmend.flisr.trace import (
    SwitchOp,
    add_fault_zones,
    open_incident,
    record_incident,
    record_plan,
    set_incident_status,
)
from gridmend.grid.energize import apply_switch_ops
from gridmend.sim import trip

# A fault hidden behind another is found on each reclose; more rounds than faults means a bug.
MAX_ROUNDS = 5


class LocateError(Exception):
    pass


class IsolateError(Exception):
    pass


@dataclass(frozen=True)
class Location:
    # One zone per fault the indicators can see, each with its chain of tripped indicators.
    zones: list[int]
    chains: dict[int, list[str]]

    @property
    def zone(self) -> int:
        return self.zones[0]

    @property
    def chain(self) -> list[str]:
        return self.chains[self.zones[0]]


LOCATE_QUERY = """
MATCH (:Bus)-[up:SW]->(:Bus)-[:IN_ZONE]->(z:Zone)
WHERE up.kind <> 'tie' AND up.fi = 'set'
OPTIONAL MATCH (b:Bus)-[:IN_ZONE]->(z), (b)-[down:SW]->(:Bus)
WHERE down.kind = 'sectionalizer' AND down.fi = 'set'
WITH z, count(down) AS set_downstream
WHERE set_downstream = 0
RETURN z.id AS faulted_zone
"""


def find_faulted_zones(g: Graph) -> list[int]:
    # A fault sits in a zone whose upstream indicator is set while every downstream indicator
    # is clear: current reached the zone but went no further. Faults on separate branches each
    # end their own chain; a fault upstream of another on the same path stays hidden until the
    # downstream one is cut out and the breaker recloses onto it.
    rows = g.ro_query(LOCATE_QUERY).result_set
    if not rows:
        raise LocateError("no chain of tripped indicators to follow")
    return sorted(row[0] for row in rows)


def indicator_chain(g: Graph, zone: int) -> list[str]:
    # Switches on the normal radial path from the source into the zone, in order.
    rows = g.ro_query(
        """
        MATCH (s:Bus {is_source: true}), (z:Zone {id: $zone})<-[:IN_ZONE]-(t:Bus)
        MATCH p = (s)-[:SEG|SW*1..100 {normally: 'closed'}]-(t)
        RETURN [r IN relationships(p) WHERE type(r) = 'SW' | [r.id, r.fi]]
        ORDER BY length(p)
        LIMIT 1
        """,
        {"zone": zone},
    ).result_set
    chain = rows[0][0]
    unset = [sid for sid, fi in chain if fi != "set"]
    if unset:
        raise LocateError(f"indicators on the path to Z{zone} are not set: {', '.join(unset)}")
    return [sid for sid, _ in chain]


def locate_fault(g: Graph) -> Location:
    zones = find_faulted_zones(g)
    chains = {zone: indicator_chain(g, zone) for zone in zones}
    # Zones found in an earlier round stay faulted.
    g.query("MATCH (z:Zone) SET z.faulted = z.faulted OR z.id IN $zones", {"zones": zones})
    incident = open_incident(g)
    if incident and incident["status"] == "tripped":
        add_fault_zones(g, incident["id"], zones)
        set_incident_status(g, incident["id"], "located")
    return Location(zones=zones, chains=chains)


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

    for breaker, status, operable, fed_zone in g.ro_query(BREAKER_QUERY).result_set:
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
