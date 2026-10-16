from uuid import uuid4

from falkordb import FalkorDB, Graph

from gridmend.flisr.candidates import plan_restoration
from gridmend.flisr.execute import verified_plans
from gridmend.flisr.explain import plan_steps
from gridmend.flisr.isolate import faulted_zone, isolate
from gridmend.flisr.locate import locate_fault
from gridmend.flisr.trace import open_incident
from gridmend.sim import apply_fault


class WhatIfError(Exception):
    pass


def to_planning(fork: Graph, fault_line: str | None) -> str:
    """Bring a copy of the grid to the point where restoration is planned; returns the incident."""
    incident = open_incident(fork)
    faulted = fork.ro_query("MATCH ()-[s:SEG {faulted: true}]->() RETURN count(s)").result_set[0][0]
    if incident and incident["status"] not in ("located", "isolated"):
        raise WhatIfError(f"{incident['id']} is already {incident['status']}")
    if not faulted:
        if not fault_line:
            raise WhatIfError("the feeder is healthy: name a line to fault")
        apply_fault(fork, fault_line)
    elif fault_line:
        raise WhatIfError("a fault is already active: leave the line out")
    if incident and incident["status"] == "isolated":
        return incident["id"]
    if faulted_zone(fork) is None:
        locate_fault(fork)
    incident_id, _ = isolate(fork)
    return incident_id


def outcome(db: FalkorDB, fork: Graph, incident_id: str) -> dict:
    results = plan_restoration(db, fork, incident_id, prefix="w")
    this_round = {r.plan_id for _, r in results}
    ranked = [p for p in verified_plans(fork, incident_id) if p["id"] in this_round]
    best = ranked[0] if ranked else None
    restored = best["critical"] if best else []
    critical = fork.ro_query(
        "MATCH (b:Bus {critical: true}) RETURN b.facility, b.energized ORDER BY b.id"
    ).result_set
    cut_off = fork.ro_query(
        "MATCH (:Incident {id: $id})-[c:CUTS_OFF]->(z:Zone) RETURN z.id, c.via ORDER BY z.id",
        {"id": incident_id},
    ).result_set
    return {
        "isolation": plan_steps(fork, f"{incident_id}-iso"),
        "cut_off": [{"zone": zone, "via": via} for zone, via in cut_off],
        "plans_checked": len(results),
        "plans_passed": sum(r.passed for _, r in results),
        "plan": best and {
            "ops": plan_steps(fork, best["id"]),
            "restored_kw": round(best["restored_kw"]),
            "min_v_pu": best["min_v_pu"],
            "max_loading_pct": best["max_loading_pct"],
        },
        "restored_kw": round(best["restored_kw"]) if best else 0,
        "critical_restored": restored,
        "critical_dark": [f for f, on in critical if not on and f not in restored],
    }


def on_copy(db: FalkorDB, g: Graph, jammed: list[str], fault_line: str | None) -> dict:
    fork = g.copy(f"whatif_{g.name}_{uuid4().hex[:8]}")
    try:
        fork.query(
            "MATCH ()-[s:SW]->() WHERE s.id IN $ids SET s.operable = false, s.report = 'what-if'",
            {"ids": jammed},
        )
        return outcome(db, fork, to_planning(fork, fault_line))
    finally:
        fork.delete()


def what_if(db: FalkorDB, g: Graph, switches: list[str], fault_line: str | None = None) -> dict:
    """Plan restoration on copies of the grid with and without these switches jammed, and
    compare. The live grid and its decision trace are never touched."""
    if not switches:
        raise WhatIfError("name at least one switch")
    known = {row[0] for row in g.ro_query("MATCH ()-[s:SW]->() RETURN s.id").result_set}
    unknown = [s for s in switches if s not in known]
    if unknown:
        raise WhatIfError(f"unknown switches: {', '.join(unknown)}")

    as_is = on_copy(db, g, [], fault_line)
    jammed = on_copy(db, g, switches, fault_line)
    return {
        "switches": switches,
        "fault_line": fault_line,
        "as_is": as_is,
        "jammed": jammed,
        "kw_change": jammed["restored_kw"] - as_is["restored_kw"],
        "critical_lost": [f for f in as_is["critical_restored"] if f not in jammed["critical_restored"]],
        "critical_gained": [f for f in jammed["critical_restored"] if f not in as_is["critical_restored"]],
    }
