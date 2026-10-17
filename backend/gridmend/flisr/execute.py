from falkordb import Graph

from gridmend.flisr.isolate import mark_cut_off
from gridmend.flisr.trace import set_incident_status
from gridmend.grid.energize import apply_switch_ops
from gridmend.sim import trip

# The incident state each kind of plan may run from.
RUNS_FROM = {"isolation": {"located"}, "restoration": {"isolated"}}


class ExecuteError(Exception):
    pass


def verified_plans(g: Graph, incident_id: str, kind: str = "restoration") -> list[dict]:
    """Plans of one kind that passed simulation, with the critical facilities each restores."""
    rows = g.ro_query(
        """
        MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {kind: $kind, status: 'verified'})
        RETURN p.id, p.restored_kw, p.restored_weighted, p.switch_ops, p.min_v_pu, p.max_loading_pct
        ORDER BY p.id
        """,
        {"id": incident_id, "kind": kind},
    ).result_set
    return [
        {"id": plan_id, "restored_kw": kw, "restored_weighted": weighted, "switch_ops": ops,
         "min_v_pu": min_v, "max_loading_pct": max_loading, "critical": critical_sites(g, plan_id)}
        for plan_id, kw, weighted, ops, min_v, max_loading in rows
    ]


def critical_sites(g: Graph, plan_id: str) -> list[str]:
    return [row[0] for row in g.ro_query(
        """
        MATCH (p:Plan {id: $id})-[:RESTORES]->(z:Zone)<-[:IN_ZONE]-(b:Bus {critical: true})
        RETURN b.facility ORDER BY b.id
        """,
        {"id": plan_id},
    ).result_set]


def execute_plan(g: Graph, plan_id: str) -> tuple[list[tuple[str, str]], bool]:
    """The interlock: only a plan that passed simulation on a fork moves live switches.
    Returns the operations and whether the breaker tripped again afterwards."""
    rows = g.ro_query(
        """
        MATCH (i:Incident)-[:HAS_PLAN]->(p:Plan {id: $id})
        RETURN i.id, i.status, p.status, p.kind
        """,
        {"id": plan_id},
    ).result_set
    if not rows:
        raise ExecuteError(f"no plan {plan_id}")
    incident_id, incident_status, plan_status, kind = rows[0]
    if plan_status != "verified":
        raise ExecuteError(f"{plan_id} is {plan_status}; only plans that passed simulation run")
    if incident_status not in RUNS_FROM[kind]:
        raise ExecuteError(f"{incident_id} is {incident_status}; a {kind} plan cannot run now")

    # Steps come from the stored plan, so what runs is exactly what was simulated.
    ops = [tuple(row) for row in g.ro_query(
        """
        MATCH (:Plan {id: $id})-[:STEP]->(o:SwitchOp)
        RETURN o.switch_id, o.op ORDER BY o.seq
        """,
        {"id": plan_id},
    ).result_set]
    apply_switch_ops(g, ops)
    g.query("MATCH (p:Plan {id: $id}) SET p.status = 'executed'", {"id": plan_id})
    # Protection is physics, not the agent's diagnosis: power reaching the real fault trips
    # the breaker again, and the incident goes back to being located.
    tripped = bool(trip(g))
    if tripped:
        set_incident_status(g, incident_id, "tripped")
    elif kind == "isolation":
        mark_cut_off(g, incident_id)
        set_incident_status(g, incident_id, "isolated")
    else:
        set_incident_status(g, incident_id, "restored")
    return ops, tripped
