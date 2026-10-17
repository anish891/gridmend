from falkordb import FalkorDB, Graph

from gridmend.grid.energize import refresh_energization
from gridmend.grid.load import BASE_GRAPH, LIVE_GRAPH


class FaultError(Exception):
    pass


def apply_fault(g: Graph, line_id: str) -> list[str]:
    """Fault an unswitched segment; returns the switches whose indicators saw fault current."""
    return apply_faults(g, [line_id])


def apply_faults(g: Graph, line_ids: list[str]) -> list[str]:
    """Fault several unswitched segments at once and trip the breaker."""
    if g.ro_query("MATCH ()-[s:SEG {faulted: true}]->() RETURN count(s)").result_set[0][0]:
        raise FaultError("a fault is already active; reset first")
    if not line_ids or len(set(line_ids)) != len(line_ids):
        raise FaultError("name one or more different segments")
    for line_id in line_ids:
        found = g.ro_query(
            "MATCH (a:Bus)-[s:SEG {id: $id}]->(:Bus) RETURN a.energized", {"id": line_id}
        ).result_set
        if not found:
            raise FaultError(f"{line_id} is not an unswitched segment")
        if not found[0][0]:
            raise FaultError(f"{line_id} is not energized")

    g.query("MATCH ()-[s:SEG]->() WHERE s.id IN $ids SET s.faulted = true", {"ids": line_ids})
    return trip(g)


def trip(g: Graph) -> list[str]:
    """Protection: if any faulted segment is energized, the breaker trips. Returns the switches
    whose indicators saw fault current, or nothing when no fault is energized."""
    buses = [row[0] for row in g.ro_query(
        "MATCH (a:Bus {energized: true})-[:SEG {faulted: true}]->(:Bus) RETURN a.id ORDER BY a.id"
    ).result_set]
    if not buses:
        return []

    # Fault current flows from the source to every energized fault along the radial paths
    # in service, so exactly the indicators on those paths pick up.
    path: list[str] = []
    for bus in buses:
        for switch_id in g.ro_query(
            """
            MATCH (s:Bus {is_source: true})
            MATCH p = (s)-[:SEG|SW*0..100]-(:Bus {id: $bus})
            WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.status = 'closed')
            RETURN [r IN relationships(p) WHERE type(r) = 'SW' | r.id]
            ORDER BY length(p)
            LIMIT 1
            """,
            {"bus": bus},
        ).result_set[0][0]:
            if switch_id not in path:
                path.append(switch_id)

    g.query(
        """
        MATCH ()-[s:SW]->() WHERE s.fi <> 'none'
        SET s.fi = CASE WHEN s.id IN $path THEN 'set' ELSE 'clear' END
        """,
        {"path": path},
    )
    # Only the breakers fault current flowed through trip: the faulted feeders, not the others.
    g.query("MATCH ()-[s:SW {kind: 'breaker'}]->() WHERE s.id IN $path SET s.status = 'open'",
            {"path": path})
    refresh_energization(g)
    return path


def reset(db: FalkorDB) -> None:
    if LIVE_GRAPH in db.list_graphs():
        db.select_graph(LIVE_GRAPH).delete()
    db.select_graph(BASE_GRAPH).copy(LIVE_GRAPH)
