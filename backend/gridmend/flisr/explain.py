from falkordb import Graph

from gridmend.flisr.candidates import screen
from gridmend.flisr.crew import crew_reports
from gridmend.flisr.policy import incident_context
from gridmend.flisr.transfer import transfer_limit
from gridmend.grid.energize import grid_status


# Switches on the conducting path from the substation into a zone, in order.
FEEDING_PATH_QUERY = """
MATCH (s:Bus {is_source: true}), (t:Bus {id: $zone})
MATCH p = (s)-[:SEG|SW*0..40]-(t)
WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.status = 'closed')
RETURN [r IN relationships(p) WHERE type(r) = 'SW' | r.id] AS switches
ORDER BY length(p)
LIMIT 1
"""


def feeding_path(g: Graph, zone: int) -> list[str]:
    rows = g.ro_query(FEEDING_PATH_QUERY, {"zone": zone}).result_set
    return rows[0][0] if rows else []


def plan_steps(g: Graph, plan_id: str) -> list[str]:
    rows = g.ro_query(
        "MATCH (:Plan {id: $id})-[:STEP]->(o:SwitchOp) RETURN o.op, o.switch_id ORDER BY o.seq",
        {"id": plan_id},
    ).result_set
    return [f"{op} {switch_id}" for op, switch_id in rows]


def switching_order(g: Graph, incident_id: str) -> list[dict]:
    """Every switch operation carried out for the incident, isolation first, numbered as an
    operator's switching order."""
    rows = g.ro_query(
        """
        MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {status: 'executed'})-[:STEP]->(o:SwitchOp)
        MATCH ()-[s:SW]->() WHERE s.id = o.switch_id
        RETURN p.id, p.kind, o.seq, o.op, o.switch_id, s.kind
        """,
        {"id": incident_id},
    ).result_set
    ordered = sorted(rows, key=lambda r: (r[1] != "isolation", r[2]))
    return [
        {"step": i, "plan": plan_id, "phase": "isolate" if kind == "isolation" else "restore",
         "op": op, "switch_id": switch_id, "switch_kind": switch_kind}
        for i, (plan_id, kind, _, op, switch_id, switch_kind) in enumerate(ordered, 1)
    ]


def rejected_plans(g: Graph, incident_id: str) -> list[dict]:
    rows = g.ro_query(
        """
        MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {status: 'rejected'})-[:VIOLATES]->(v)
        RETURN p.id, collect(v.type + ': ' + v.detail)
        ORDER BY p.id
        """,
        {"id": incident_id},
    ).result_set
    return [{"id": plan_id, "ops": plan_steps(g, plan_id), "violations": violations}
            for plan_id, violations in rows]


def why_dark(g: Graph, incident_id: str, zone: int, faulted: bool) -> str:
    if faulted:
        return "inside the faulted zone; it stays isolated until the line is repaired"

    cut = g.ro_query(
        "MATCH (:Incident {id: $id})-[c:CUTS_OFF]->(:Zone {id: $zone}) RETURN c.via",
        {"id": incident_id, "zone": zone},
    ).result_set
    if cut:
        return (f"cut out with the fault: switch {cut[0][0]} is jammed closed, so this zone could "
                f"not be separated from the faulted one")

    # A safe plan existed but another one served the priorities in force better.
    passed_over = g.ro_query(
        """
        MATCH (i:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {status: 'verified'})-[:RESTORES]->
              (:Zone {id: $zone})
        OPTIONAL MATCH (i)-[:HAS_PLAN]->(chosen:Plan {kind: 'restoration', status: 'executed'})
        RETURN p.id, chosen.id ORDER BY p.id LIMIT 1
        """,
        {"id": incident_id, "zone": zone},
    ).result_set
    if passed_over:
        plan_id, chosen = passed_over[0]
        if chosen:
            return (f"{plan_id} would restore it safely, but {chosen} restores critical sites with "
                    f"a higher total priority under the conditions in force")
        return f"{plan_id} would restore it safely; awaiting the operator's choice"

    blocked = g.ro_query(
        """
        MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {status: 'rejected'})-[:RESTORES]->
              (:Zone {id: $zone})
        MATCH (p)-[:VIOLATES]->(v)
        RETURN p.id, v.type, v.detail ORDER BY p.id LIMIT 1
        """,
        {"id": incident_id, "zone": zone},
    ).result_set
    if blocked:
        plan_id, kind, detail = blocked[0]
        return f"the plan that would restore it ({plan_id}) was rejected: {kind}, {detail}"

    # Otherwise try each tie to a now-live neighbour against the current configuration.
    ties = g.ro_query(
        """
        MATCH (:Zone {id: $zone})-[b:BOUNDARY {kind: 'tie'}]-(o:Zone {energized: true})
        RETURN b.switch_id, o.id ORDER BY b.switch_id
        """,
        {"zone": zone},
    ).result_set
    if not ties:
        return "no tie connects it to a live zone"
    closed = frozenset(row[0] for row in g.ro_query(
        "MATCH ()-[s:SW {status: 'closed'}]->() RETURN s.id"
    ).result_set)
    reasons = []
    for tie, neighbour in ties:
        result = screen(g, closed | {tie})
        if not result.radial:
            reasons.append(f"closing {tie} from Z{neighbour} would close a loop")
        elif not result.within_capacity:
            reasons.append(f"closing {tie} from Z{neighbour} would overload its feeding path")
        else:
            reasons.append(f"closing {tie} from Z{neighbour} passes screening but was not "
                           f"among the verified plans")
    return "; ".join(reasons)


def explain_incident(g: Graph, incident_id: str) -> dict:
    incident = g.ro_query(
        """
        MATCH (i:Incident {id: $id})-[:FAULT_IN]->(z:Zone)
        RETURN i.fault_line, i.status, z.id
        """,
        {"id": incident_id},
    ).result_set[0]
    executed = g.ro_query(
        """
        MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {kind: 'restoration', status: 'executed'})
        RETURN p.id, p.restored_kw, p.min_v_pu, p.max_loading_pct
        """,
        {"id": incident_id},
    ).result_set

    plan = None
    if executed:
        plan_id, kw, min_v, max_loading = executed[0]
        zones = [row[0] for row in g.ro_query(
            "MATCH (:Plan {id: $id})-[:RESTORES]->(z:Zone) RETURN z.id ORDER BY z.id",
            {"id": plan_id},
        ).result_set]
        plan = {
            "id": plan_id,
            "ops": plan_steps(g, plan_id),
            "restored_kw": kw,
            "min_v_pu": min_v,
            "max_loading_pct": max_loading,
            "paths": {zone: feeding_path(g, zone) for zone in zones},
        }

    critical = []
    for bus, facility, zone, energized, faulted in g.ro_query(
        """
        MATCH (b:Bus {critical: true})-[:IN_ZONE]->(z:Zone)
        RETURN b.id, b.facility, z.id, b.energized, z.faulted ORDER BY b.id
        """
    ).result_set:
        critical.append({
            "bus": bus,
            "facility": facility,
            "zone": zone,
            "energized": energized,
            "reason": None if energized else why_dark(g, incident_id, zone, faulted),
        })

    status = grid_status(g)
    return {
        "incident": incident_id,
        "fault_line": incident[0],
        "status": incident[1],
        "faulted_zone": incident[2],
        "isolation": plan_steps(g, f"{incident_id}-iso"),
        "switching_order": switching_order(g, incident_id),
        "plan": plan,
        "rejected": rejected_plans(g, incident_id),
        "transfer": transfer_limit(g),
        "critical": critical,
        "context": incident_context(g, incident_id),
        "crew_reports": crew_reports(g, incident_id),
        "cut_off": [{"zone": zone, "via": via} for zone, via in g.ro_query(
            "MATCH (:Incident {id: $id})-[c:CUTS_OFF]->(z:Zone) RETURN z.id, c.via ORDER BY z.id",
            {"id": incident_id},
        ).result_set],
        "dark_zones": status["dark_zones"],
        "load_served_kw": round(status["served_kw"]),
        "load_unserved_kw": round(status["total_kw"] - status["served_kw"]),
    }
