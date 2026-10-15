from falkordb import Graph

from gridmend.flisr.candidates import screen


def feeding_path(g: Graph, zone: int) -> list[str]:
    """Switches on the conducting path from the substation into a zone, in order."""
    rows = g.ro_query(
        """
        MATCH (s:Bus {is_source: true}), (t:Bus {id: $zone})
        MATCH p = (s)-[:SEG|SW*0..40]-(t)
        WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.status = 'closed')
        RETURN [r IN relationships(p) WHERE type(r) = 'SW' | r.id]
        ORDER BY length(p)
        LIMIT 1
        """,
        {"zone": zone},
    ).result_set
    return rows[0][0] if rows else []


def plan_steps(g: Graph, plan_id: str) -> list[str]:
    rows = g.ro_query(
        "MATCH (:Plan {id: $id})-[:STEP]->(o:SwitchOp) RETURN o.op, o.switch_id ORDER BY o.seq",
        {"id": plan_id},
    ).result_set
    return [f"{op} {switch_id}" for op, switch_id in rows]


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

    return {
        "incident": incident_id,
        "fault_line": incident[0],
        "status": incident[1],
        "faulted_zone": incident[2],
        "isolation": plan_steps(g, f"{incident_id}-iso"),
        "plan": plan,
        "rejected": rejected_plans(g, incident_id),
        "critical": critical,
    }
