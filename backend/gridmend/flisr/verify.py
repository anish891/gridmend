from dataclasses import dataclass, field

from falkordb import FalkorDB, Graph

from gridmend.flisr.generation import NET_P_KW, online_generation
from gridmend.flisr.trace import SwitchOp, record_plan
from gridmend.grid.case33 import PowerFlowError, bus_voltages
from gridmend.grid.energize import apply_switch_ops
from gridmend.grid.load import load_assumptions


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
    max_loading_pct: float | None = None
    min_v_pu: float | None = None

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


def check_operability(live: Graph, ops: list[SwitchOp]) -> list[tuple[str, str]]:
    rows = live.ro_query(
        """
        MATCH ()-[s:SW]->() WHERE s.id IN $ids AND s.operable = false
        RETURN s.id, s.report ORDER BY s.id
        """,
        {"ids": [o.switch_id for o in ops]},
    ).result_set
    return [("operability", f"{switch} is jammed and cannot be operated"
             + (f" (crew report {report})" if report else "")) for switch, report in rows]


def check_connectivity(fork: Graph, claims: list[int]) -> list[tuple[str, str]]:
    rows = fork.ro_query(
        "MATCH (z:Zone {energized: false}) WHERE z.id IN $claims RETURN z.id ORDER BY z.id",
        {"claims": claims},
    ).result_set
    return [("connectivity", f"Z{z} is claimed but stays dark") for (z,) in rows]


def keep_conducting_edges(fork: Graph) -> None:
    # Graph algorithms filter by relationship type, not property, so the fork drops its open
    # switches and becomes the conducting network itself. Ties weigh more so the spanning
    # tree leaves them out first and a loop is reported at the tie that closed it.
    fork.query("MATCH ()-[s:SW {status: 'open'}]->() DELETE s")
    fork.query(
        "MATCH ()-[r:SEG|SW]->() SET r.tree_weight = CASE WHEN r.kind = 'tie' THEN 1 ELSE 0 END"
    )


RADIALITY_QUERY = """
MATCH (s:Bus {is_source: true})
CALL algo.MSF({nodeLabels: ['Bus'], relationshipTypes: ['SEG', 'SW'],
               weightAttribute: 'tree_weight'})
YIELD nodes, edges
WITH s, nodes, edges WHERE s IN nodes
WITH nodes, [e IN edges | e.id] AS tree
OPTIONAL MATCH (a:Bus)-[r:SEG|SW]->(:Bus)
WHERE a IN nodes AND NOT r.id IN tree
RETURN size(nodes) AS energized_buses, collect(r.id) AS loop_edges
"""


def check_radiality(fork: Graph) -> list[tuple[str, str]]:
    """Expects a fork holding only conducting edges (see keep_conducting_edges)."""
    # The source's spanning tree covers every energized bus; any conducting edge it cannot
    # use joins two buses that are already connected, so it closes a loop.
    buses, loops = fork.ro_query(RADIALITY_QUERY).result_set[0]
    if not loops:
        return []
    verb = "closes a loop" if len(loops) == 1 else "close loops"
    return [("radiality", f"{', '.join(sorted(loops))} {verb}: {buses - 1 + len(loops)} "
                          f"conducting edges among {buses} energized buses (a tree has "
                          f"{buses - 1})")]


LOADING_QUERY = f"""
MATCH (s:Bus {{is_source: true}})
MATCH p = (s)-[:SEG|SW*0..40]-(b:Bus {{energized: true}})
WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.status = 'closed')
UNWIND relationships(p) AS r
WITH r, sum({NET_P_KW}) AS p_kw, sum(b.q_kvar) AS q_kvar
RETURN r.id AS line, sqrt(p_kw * p_kw + q_kvar * q_kvar) AS kva, r.cap_kva AS rating_kva
"""


def edge_loadings(fork: Graph) -> list[tuple[str, float, float]]:
    # In a radial feeder every energized bus has exactly one path from the source, so the
    # load an edge carries is the sum of the loads of the buses whose path runs through it.
    rows = fork.ro_query(LOADING_QUERY).result_set
    return [(edge_id, s_kva, cap_kva) for edge_id, s_kva, cap_kva in rows]


def check_capacity(fork: Graph) -> tuple[list[tuple[str, str]], float]:
    """Only meaningful once radiality holds; returns violations and the highest loading in %."""
    loadings = edge_loadings(fork)
    violations = [
        ("capacity", f"{edge_id} carries {s_kva:.0f} kVA of {cap_kva:.0f} kVA "
                     f"({100 * s_kva / cap_kva:.0f}%)")
        for edge_id, s_kva, cap_kva in sorted(loadings, key=lambda e: e[1] / e[2], reverse=True)
        if s_kva > cap_kva
    ]
    max_loading = max((100 * s_kva / cap_kva for _, s_kva, cap_kva in loadings), default=0.0)
    return violations, max_loading


def check_voltage(fork: Graph) -> tuple[list[tuple[str, str]], float | None]:
    """Power flow over the fork's switch configuration; returns violations and the lowest voltage."""
    conducting = {
        row[0] for row in fork.ro_query(
            """
            MATCH ()-[r:SEG|SW]->()
            WHERE type(r) = 'SEG' OR r.status = 'closed'
            RETURN r.id
            """
        ).result_set
    }
    try:
        voltages = bus_voltages(conducting, online_generation(fork))
    except PowerFlowError:
        return [("voltage", "power flow did not converge")], None

    assumptions = load_assumptions()
    limit, ceiling = assumptions["voltage_min_pu"], assumptions["voltage_max_pu"]
    min_v, min_bus = min((v, bus) for bus, v in voltages.items())
    max_v, max_bus = max((v, bus) for bus, v in voltages.items())
    violations = []
    low = sorted(bus for bus, v in voltages.items() if v < limit)
    if low:
        violations.append(("voltage", f"{len(low)} buses below {limit:.2f} pu ({', '.join(map(str, low))}); "
                                      f"lowest bus {min_bus} at {min_v:.3f} pu"))
    # Generation feeding a lightly loaded branch can push voltage up instead.
    high = sorted(bus for bus, v in voltages.items() if v > ceiling)
    if high:
        violations.append(("voltage", f"{len(high)} buses above {ceiling:.2f} pu ({', '.join(map(str, high))}); "
                                      f"highest bus {max_bus} at {max_v:.3f} pu"))
    return violations, min_v


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
        keep_conducting_edges(fork)
        result.violations = (check_operability(live, ops) + check_isolation(fork)
                             + check_connectivity(fork, result.claims))
        radiality = check_radiality(fork)
        result.violations += radiality
        if not radiality:
            capacity, result.max_loading_pct = check_capacity(fork)
            voltage, result.min_v_pu = check_voltage(fork)
            result.violations += capacity + voltage
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
    record_verification(live, result)
    return result


def record_verification(live: Graph, result: VerifyResult) -> None:
    live.query(
        """
        MATCH (p:Plan {id: $id})
        SET p.restored_kw = $kw, p.restored_weighted = $weighted,
            p.max_loading_pct = $max_loading, p.min_v_pu = $min_v
        WITH p
        UNWIND $violations AS v
        CREATE (p)-[:VIOLATES]->(:Violation {type: v[0], detail: v[1]})
        """,
        {
            "id": result.plan_id,
            "kw": result.restored_kw,
            "weighted": result.restored_weighted,
            "max_loading": result.max_loading_pct,
            "min_v": result.min_v_pu,
            "violations": [list(v) for v in result.violations],
        },
    )
    live.query(
        "MATCH (p:Plan {id: $id}), (z:Zone) WHERE z.id IN $restored CREATE (p)-[:RESTORES]->(z)",
        {"id": result.plan_id, "restored": result.restored_zones},
    )


def next_plan_id(g: Graph, incident_id: str, prefix: str) -> str:
    count = g.ro_query(
        "MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan) WHERE p.id STARTS WITH $prefix "
        "RETURN count(p)",
        {"id": incident_id, "prefix": f"{incident_id}-{prefix}"},
    ).result_set[0][0]
    return f"{incident_id}-{prefix}{count + 1}"
