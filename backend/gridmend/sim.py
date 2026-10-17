from falkordb import FalkorDB, Graph

from gridmend.grid.energize import refresh_energization
from gridmend.grid import feeder


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


def set_jammed(g: Graph, switch_id: str, jammed: bool) -> None:
    """Scenario setup: a switch that will not operate, as if found on patrol before the fault."""
    if g.ro_query("MATCH ()-[s:SEG {faulted: true}]->() RETURN count(s)").result_set[0][0]:
        raise FaultError("set up jammed switches before the fault; reset first")
    found = g.query(
        "MATCH ()-[s:SW {id: $id}]->() SET s.operable = $operable, s.report = null RETURN s.id",
        {"id": switch_id, "operable": not jammed},
    ).result_set
    if not found:
        raise FaultError(f"{switch_id} is not a switch")


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
            MATCH p = (s)-[:SEG|SW*0..100 {status: 'closed'}]-(:Bus {id: $bus})
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
    if feeder.live_graph() in db.list_graphs():
        db.select_graph(feeder.live_graph()).delete()
    db.select_graph(feeder.base_graph()).copy(feeder.live_graph())
