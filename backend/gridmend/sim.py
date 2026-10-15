from falkordb import FalkorDB, Graph

from gridmend.grid.energize import refresh_energization
from gridmend.grid.load import BASE_GRAPH, LIVE_GRAPH


class FaultError(Exception):
    pass


def apply_fault(g: Graph, line_id: str) -> list[str]:
    """Fault an unswitched segment; returns the switches whose indicators saw fault current."""
    if g.ro_query("MATCH ()-[s:SEG {faulted: true}]->() RETURN count(s)").result_set[0][0]:
        raise FaultError("a fault is already active; reset first")
    found = g.ro_query(
        "MATCH (a:Bus)-[s:SEG {id: $id}]->(:Bus) RETURN a.id, a.energized",
        {"id": line_id},
    ).result_set
    if not found:
        raise FaultError(f"{line_id} is not an unswitched segment")
    bus, energized = found[0]
    if not energized:
        raise FaultError(f"{line_id} is not energized")

    # Fault current flows from the source to the fault along the pre-fault radial path,
    # so exactly the indicators on that path pick up.
    path = g.ro_query(
        """
        MATCH (s:Bus {is_source: true})
        MATCH p = (s)-[:SEG|SW*0..40]-(:Bus {id: $bus})
        WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.status = 'closed')
        RETURN [r IN relationships(p) WHERE type(r) = 'SW' | r.id]
        ORDER BY length(p)
        LIMIT 1
        """,
        {"bus": bus},
    ).result_set[0][0]

    g.query("MATCH ()-[s:SEG {id: $id}]->() SET s.faulted = true", {"id": line_id})
    g.query(
        """
        MATCH ()-[s:SW]->() WHERE s.fi <> 'none'
        SET s.fi = CASE WHEN s.id IN $path THEN 'set' ELSE 'clear' END
        """,
        {"path": path},
    )
    g.query("MATCH ()-[s:SW {kind: 'breaker'}]->() SET s.status = 'open'")
    refresh_energization(g)
    return path


def reset(db: FalkorDB) -> None:
    if LIVE_GRAPH in db.list_graphs():
        db.select_graph(LIVE_GRAPH).delete()
    db.select_graph(BASE_GRAPH).copy(LIVE_GRAPH)
