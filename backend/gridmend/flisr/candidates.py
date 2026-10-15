from dataclasses import dataclass
from itertools import combinations

from falkordb import FalkorDB, Graph

from gridmend.flisr.trace import SwitchOp
from gridmend.flisr.verify import VerifyResult, next_plan_id, verify_plan

# Weighted kW given up per switch operation, so equal restorations prefer fewer moves.
SWITCH_OP_PENALTY = 10.0
TOP_N = 5


@dataclass(frozen=True)
class Screen:
    zones: frozenset[int]
    radial: bool
    within_capacity: bool


@dataclass(frozen=True)
class Candidate:
    origin: str  # "greedy", "tie-only" or "repaired"
    ops: tuple[SwitchOp, ...]
    claims: tuple[int, ...]
    claimed_kw: float
    claimed_weighted: float
    score: float


@dataclass(frozen=True)
class Situation:
    closed: frozenset[str]
    live_zones: frozenset[int]
    zone_kw: dict[int, float]
    zone_weighted: dict[int, float]
    ties: list[str]
    sectionalizers: list[str]
    tie_feeds: list[tuple[int, str]]


def read_situation(g: Graph) -> Situation:
    closed = {row[0] for row in g.ro_query(
        "MATCH ()-[s:SW {status: 'closed'}]->() RETURN s.id"
    ).result_set}
    zones = g.ro_query(
        "MATCH (z:Zone {faulted: false}) RETURN z.id, z.energized, z.p_kw, z.weighted_kw"
    ).result_set
    # Decision variables: open ties and closed sectionalizers touching a dead healthy zone.
    # Isolation switches border the faulted zone and are never touched.
    switches = g.ro_query(
        """
        MATCH (a:Zone)-[b:BOUNDARY]-(d:Zone {energized: false, faulted: false})
        WHERE NOT a.faulted
        MATCH ()-[s:SW {id: b.switch_id}]->()
        WHERE (s.kind = 'tie' AND s.status = 'open')
           OR (s.kind = 'sectionalizer' AND s.status = 'closed')
        RETURN DISTINCT s.id, s.kind
        """
    ).result_set
    # Dead healthy zones that a tie connects straight to a live zone, heaviest first.
    tie_feeds = g.ro_query(
        """
        MATCH (d:Zone {energized: false, faulted: false})-[b:BOUNDARY {kind: 'tie'}]-
              (:Zone {energized: true, faulted: false})
        RETURN d.id, b.switch_id
        ORDER BY d.weighted_kw DESC, b.switch_id
        """
    ).result_set
    return Situation(
        closed=frozenset(closed),
        live_zones=frozenset(z for z, on, _, _ in zones if on),
        zone_kw={z: kw for z, _, kw, _ in zones},
        zone_weighted={z: w for z, _, _, w in zones},
        ties=sorted(s for s, kind in switches if kind == "tie"),
        sectionalizers=sorted(s for s, kind in switches if kind == "sectionalizer"),
        tie_feeds=[(z, s) for z, s in tie_feeds],
    )


def screen(g: Graph, closed: frozenset[str]) -> Screen:
    """What-if energization, radiality and summed-load capacity on the live graph, without a fork."""
    params = {"closed": sorted(closed)}
    buses = g.ro_query(
        """
        MATCH (s:Bus {is_source: true})
        MATCH p = (s)-[:SEG|SW*0..40]-(b:Bus)
        WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.id IN $closed)
        RETURN DISTINCT b.id, b.zone
        """,
        params,
    ).result_set
    energized = [bus for bus, _ in buses]
    zones = frozenset(zone for _, zone in buses if zone is not None)

    edges = g.ro_query(
        """
        MATCH (a:Bus)-[r:SEG|SW]->(b:Bus)
        WHERE a.id IN $energized AND b.id IN $energized
          AND (type(r) = 'SEG' OR r.id IN $closed)
        RETURN count(r)
        """,
        {**params, "energized": energized},
    ).result_set[0][0]
    if edges != len(energized) - 1:
        return Screen(zones, radial=False, within_capacity=False)

    overloaded = g.ro_query(
        """
        MATCH (s:Bus {is_source: true})
        MATCH p = (s)-[:SEG|SW*0..40]-(b:Bus)
        WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.id IN $closed)
        UNWIND relationships(p) AS r
        WITH r, sum(b.p_kw) AS p_kw, sum(b.q_kvar) AS q_kvar
        WHERE sqrt(p_kw * p_kw + q_kvar * q_kvar) > r.cap_kva
        RETURN count(r)
        """,
        params,
    ).result_set[0][0]
    return Screen(zones, radial=True, within_capacity=overloaded == 0)


def make_candidate(
    situation: Situation, origin: str, closes: tuple[str, ...], opens: tuple[str, ...],
    result: Screen,
) -> Candidate | None:
    restored = sorted(result.zones - situation.live_zones)
    if not restored:
        return None
    ops = tuple(SwitchOp(s, "open") for s in opens) + tuple(SwitchOp(s, "close") for s in closes)
    weighted = sum(situation.zone_weighted[z] for z in restored)
    return Candidate(
        origin=origin,
        ops=ops,
        claims=tuple(restored),
        claimed_kw=sum(situation.zone_kw[z] for z in restored),
        claimed_weighted=weighted,
        score=weighted - SWITCH_OP_PENALTY * len(ops),
    )


def configuration(situation: Situation, closes, opens) -> frozenset[str]:
    return (situation.closed | set(closes)) - set(opens)


def greedy_candidate(g: Graph, situation: Situation) -> Candidate | None:
    # The operator's reflex: for every dead zone with a tie to a live zone, close that tie.
    closes, fed = [], set()
    for zone, tie in situation.tie_feeds:
        if zone not in fed:
            fed.add(zone)
            closes.append(tie)
    closes = tuple(sorted(set(closes)))
    result = screen(g, configuration(situation, closes, ()))
    return make_candidate(situation, "greedy", closes, (), result)


def best_openings(g: Graph, situation: Situation, closes: tuple[str, ...]) -> Candidate | None:
    """Cheapest sectionalizer openings that make the tie set radial and within capacity."""
    best = None
    for size in range(len(situation.sectionalizers) + 1):
        for opens in combinations(situation.sectionalizers, size):
            result = screen(g, configuration(situation, closes, opens))
            if not (result.radial and result.within_capacity):
                continue
            candidate = make_candidate(situation, "repaired", closes, opens, result)
            if candidate and (best is None or candidate.score > best.score):
                best = candidate
    return best


def generate_candidates(g: Graph) -> list[Candidate]:
    situation = read_situation(g)
    pool = [greedy_candidate(g, situation)]
    for size in range(1, len(situation.ties) + 1):
        for closes in combinations(situation.ties, size):
            if size == 1:
                result = screen(g, configuration(situation, closes, ()))
                pool.append(make_candidate(situation, "tie-only", closes, (), result))
            pool.append(best_openings(g, situation, closes))

    # Keep every distinct unrepaired plan, but only the best repaired plan per restored set:
    # extra ties that restore nothing more only add switch operations.
    kept, seen_ops, seen_repaired = [], set(), set()
    for candidate in sorted(filter(None, pool), key=rank_key):
        op_set = frozenset((o.switch_id, o.op) for o in candidate.ops)
        if op_set in seen_ops:
            continue
        if candidate.origin == "repaired":
            if candidate.claims in seen_repaired:
                continue
            seen_repaired.add(candidate.claims)
        seen_ops.add(op_set)
        kept.append(candidate)
    return kept


def rank_key(c: Candidate) -> tuple:
    return (-c.score, len(c.ops), [(o.op, o.switch_id) for o in c.ops])


def plan_restoration(
    db: FalkorDB, g: Graph, incident_id: str
) -> list[tuple[Candidate, VerifyResult]]:
    """Generate and rank candidates, then verify the top ones on forks."""
    verified = []
    for candidate in generate_candidates(g)[:TOP_N]:
        plan_id = next_plan_id(g, incident_id, "p")
        result = verify_plan(db, g, incident_id, plan_id, list(candidate.ops), list(candidate.claims))
        verified.append((candidate, result))
    return verified
