from collections.abc import Callable

from falkordb import FalkorDB, Graph

from gridmend.flisr.candidates import plan_restoration
from gridmend.flisr.trace import set_incident_status
from gridmend.grid.energize import apply_switch_ops


class ExecuteError(Exception):
    pass


def verified_plans(g: Graph, incident_id: str) -> list[dict]:
    """Verified plans with the critical facilities each one restores, best first: highest
    total priority of critical sites restored (policy 1.3), then most load restored, then
    fewest switch operations. With equal standing priorities this is the count of critical sites."""
    rows = g.ro_query(
        """
        MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {kind: 'restoration', status: 'verified'})
        RETURN p.id, p.restored_kw, p.restored_weighted, p.switch_ops, p.min_v_pu, p.max_loading_pct
        """,
        {"id": incident_id},
    ).result_set
    plans = []
    for plan_id, kw, weighted, ops, min_v, max_loading in rows:
        sites = critical_sites(g, plan_id)
        plans.append({
            "id": plan_id,
            "restored_kw": kw,
            "restored_weighted": weighted,
            "switch_ops": ops,
            "min_v_pu": min_v,
            "max_loading_pct": max_loading,
            "critical": [facility for facility, _ in sites],
            "priority": sum(weight for _, weight in sites),
        })
    return sorted(plans, key=lambda p: (-p["priority"], -p["restored_kw"], p["switch_ops"]))


def critical_sites(g: Graph, plan_id: str) -> list[tuple[str, int]]:
    rows = g.ro_query(
        """
        MATCH (p:Plan {id: $id})-[:RESTORES]->(z:Zone)<-[:IN_ZONE]-(b:Bus {critical: true})
        RETURN b.facility, b.weight ORDER BY b.id
        """,
        {"id": plan_id},
    ).result_set
    return [(facility, weight) for facility, weight in rows]


def execute_plan(g: Graph, plan_id: str) -> list[tuple[str, str]]:
    rows = g.ro_query(
        """
        MATCH (i:Incident)-[:HAS_PLAN]->(p:Plan {id: $id})
        RETURN i.id, i.status, p.status
        """,
        {"id": plan_id},
    ).result_set
    if not rows:
        raise ExecuteError(f"no plan {plan_id}")
    incident_id, incident_status, plan_status = rows[0]
    if plan_status != "verified":
        raise ExecuteError(f"{plan_id} is {plan_status}; only verified plans can be executed")
    if incident_status != "isolated":
        raise ExecuteError(f"{incident_id} is {incident_status}, not awaiting restoration")

    # Steps come from the stored plan, so what runs is exactly what was verified.
    ops = [tuple(row) for row in g.ro_query(
        """
        MATCH (:Plan {id: $id})-[:STEP]->(o:SwitchOp)
        RETURN o.switch_id, o.op ORDER BY o.seq
        """,
        {"id": plan_id},
    ).result_set]
    apply_switch_ops(g, ops)
    g.query("MATCH (p:Plan {id: $id}) SET p.status = 'executed'", {"id": plan_id})
    set_incident_status(g, incident_id, "restored")
    return ops


def restore(
    db: FalkorDB, g: Graph, incident_id: str, approve: Callable[[dict], bool]
) -> tuple[list, dict | None]:
    """Plan and verify candidates, pick the best verified one and execute it if approved.
    Returns the verified candidates and the executed plan (None if nothing was executed)."""
    verified = plan_restoration(db, g, incident_id)
    plans = verified_plans(g, incident_id)
    if not plans or not approve(plans[0]):
        return verified, None
    execute_plan(g, plans[0]["id"])
    return verified, plans[0]
