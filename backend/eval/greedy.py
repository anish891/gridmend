"""The greedy baseline: close a tie into every dark zone, without checking the result."""
from dataclasses import dataclass

from falkordb import Graph

from gridmend.flisr.trace import SwitchOp

# Dead healthy zones that a tie connects straight to a live zone, heaviest first.
TIE_FEEDS_QUERY = """
MATCH (d:Zone {energized: false, faulted: false})-[b:BOUNDARY {kind: 'tie'}]-
      (:Zone {energized: true, faulted: false})
WHERE NOT coalesce(d.cut_off, false)
MATCH ()-[s:SW {id: b.switch_id}]->()
WHERE coalesce(s.operable, true)
RETURN d.id AS dead_zone, b.switch_id AS tie
ORDER BY d.weighted_kw DESC, b.switch_id
"""


@dataclass(frozen=True)
class Candidate:
    ops: tuple[SwitchOp, ...]


def greedy_candidate(g: Graph) -> Candidate | None:
    # The operator's reflex: for every dead zone with a tie to a live zone, close that tie.
    closes, fed = set(), set()
    for zone, tie in g.ro_query(TIE_FEEDS_QUERY).result_set:
        if zone not in fed:
            fed.add(zone)
            closes.add(tie)
    if not closes:
        return None
    return Candidate(tuple(SwitchOp(s, "close") for s in sorted(closes)))
