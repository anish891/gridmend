from dataclasses import dataclass, field

from falkordb import FalkorDB, Graph

from gridmend.flisr.trace import SwitchOp, record_plan
from gridmend.grid.energize import apply_switch_ops


@dataclass
class VerifyResult:
    plan_id: str
    ops: list[SwitchOp]
    claims: list[int]
    violations: list[tuple[str, str]] = field(default_factory=list)
    restored_zones: list[int] = field(default_factory=list)
    dropped_zones: list[int] = field(default_factory=list)
    restored_kw: float = 0.0
    restored_weighted: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.violations


def energized_zones(g: Graph) -> set[int]:
    rows = g.ro_query("MATCH (z:Zone {energized: true, faulted: false}) RETURN z.id").result_set
    return {row[0] for row in rows}


def tie_claims(g: Graph, ops: list[SwitchOp]) -> list[int]:
    # A plan with no explicit claims claims the dark healthy zones its closed ties touch.
    closes = [o.switch_id for o in ops if o.op == "close"]
    rows = g.ro_query(
        """
        MATCH (z:Zone {energized: false, faulted: false})-[b:BOUNDARY]-(:Zone)
        WHERE b.switch_id IN $closes
        RETURN DISTINCT z.id ORDER BY z.id
        """,
        {"closes": closes},
    ).result_set
    return [row[0] for row in rows]


def check_isolation(fork: Graph) -> list[tuple[str, str]]:
    rows = fork.ro_query(
        """
        MATCH (b:Bus {energized: true})-[:IN_ZONE]->(z:Zone {faulted: true})
        RETURN z.id, collect(b.id)
        """
    ).result_set
    return [("isolation", f"faulted Z{z} energized at buses {buses}") for z, buses in rows]


def check_connectivity(fork: Graph, claims: list[int]) -> list[tuple[str, str]]:
    rows = fork.ro_query(
        "MATCH (z:Zone {energized: false}) WHERE z.id IN $claims RETURN z.id ORDER BY z.id",
        {"claims": claims},
    ).result_set
    return [("connectivity", f"Z{z} is claimed but stays dark") for (z,) in rows]


def check_radiality(fork: Graph) -> list[tuple[str, str]]:
    # Energized buses are reachable from the source by construction, so they form one
    # component; it is a tree exactly when it has one conducting edge fewer than buses.
    buses = fork.ro_query("MATCH (b:Bus {energized: true}) RETURN count(b)").result_set[0][0]
    edges = fork.ro_query(
        """
        MATCH (a:Bus {energized: true})-[r:SEG|SW]->(b:Bus {energized: true})
        WHERE type(r) = 'SEG' OR r.status = 'closed'
        RETURN count(r)
        """
    ).result_set[0][0]
    if edges == buses - 1:
        return []
    return [("radiality", f"{edges} conducting edges among {buses} energized buses "
                          f"(a tree has {buses - 1}): the plan closes a loop")]


def run_checks(fork: Graph, claims: list[int]) -> list[tuple[str, str]]:
    return check_isolation(fork) + check_connectivity(fork, claims) + check_radiality(fork)


def net_restored_load(fork: Graph, gained: list[int], lost: list[int]) -> tuple[float, float]:
    # Zones a plan darkens that were live before count against it.
    row = fork.ro_query(
        """
        MATCH (z:Zone) WHERE z.id IN $gained OR z.id IN $lost
        WITH z, CASE WHEN z.id IN $lost THEN -1 ELSE 1 END AS sign
        RETURN sum(sign * z.p_kw), sum(sign * z.weighted_kw)
        """,
        {"gained": gained, "lost": lost},
    ).result_set[0]
    return row[0] or 0.0, row[1] or 0.0


def verify_plan(
    db: FalkorDB,
    live: Graph,
    incident_id: str,
    plan_id: str,
    ops: list[SwitchOp],
    claims: list[int] | None = None,
) -> VerifyResult:
    """Apply the plan to a copy of the live graph, check it there, and record the outcome on live."""
    result = VerifyResult(plan_id, ops, claims if claims is not None else tie_claims(live, ops))
    before = energized_zones(live)

    fork_name = f"fork_{plan_id}"
    if fork_name in db.list_graphs():
        db.select_graph(fork_name).delete()
    fork = live.copy(fork_name)
    try:
        apply_switch_ops(fork, [(o.switch_id, o.op) for o in ops])
        result.violations = run_checks(fork, result.claims)
        after = energized_zones(fork)
        result.restored_zones = sorted(after - before)
        result.dropped_zones = sorted(before - after)
        result.restored_kw, result.restored_weighted = net_restored_load(
            fork, result.restored_zones, result.dropped_zones
        )
    finally:
        fork.delete()

    record_plan(live, incident_id, plan_id, "restoration",
                "verified" if result.passed else "rejected", ops)
    live.query(
        """
        MATCH (p:Plan {id: $id})
        SET p.restored_kw = $kw, p.restored_weighted = $weighted
        """,
        {"id": plan_id, "kw": result.restored_kw, "weighted": result.restored_weighted},
    )
    return result


def next_plan_id(g: Graph, incident_id: str, prefix: str) -> str:
    count = g.ro_query(
        "MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan) WHERE p.id STARTS WITH $prefix "
        "RETURN count(p)",
        {"id": incident_id, "prefix": f"{incident_id}-{prefix}"},
    ).result_set[0][0]
    return f"{incident_id}-{prefix}{count + 1}"
