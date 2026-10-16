from falkordb import Graph

# A bus is energized when some path from the source runs only over segments and closed switches.
ENERGIZED_QUERY = """
MATCH (s:Bus {is_source: true})
MATCH p = (s)-[:SEG|SW*0..40]-(b:Bus)
WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.status = 'closed')
RETURN DISTINCT b.id AS bus
"""


def energized_buses(g: Graph) -> set[int]:
    return {row[0] for row in g.ro_query(ENERGIZED_QUERY).result_set}


def refresh_energization(g: Graph) -> set[int]:
    live = energized_buses(g)
    g.query(
        "MATCH (b:Bus) SET b.energized = b.id IN $live",
        {"live": sorted(live)},
    )
    g.query(
        """
        MATCH (b:Bus)-[:IN_ZONE]->(z:Zone)
        WITH z, any(e IN collect(b.energized) WHERE e) AS energized
        SET z.energized = energized
        """
    )
    return live


def set_switch(g: Graph, switch_id: str, status: str) -> bool:
    result = g.query(
        "MATCH ()-[s:SW {id: $id}]->() SET s.status = $status RETURN s.id",
        {"id": switch_id, "status": status},
    )
    if not result.result_set:
        return False
    refresh_energization(g)
    return True


def apply_switch_ops(g: Graph, ops: list[tuple[str, str]]) -> None:
    """Apply (switch_id, "open"|"close") operations in order, then recompute energization once."""
    for switch_id, op in ops:
        g.query(
            "MATCH ()-[s:SW {id: $id}]->() SET s.status = $status",
            {"id": switch_id, "status": "closed" if op == "close" else "open"},
        )
    refresh_energization(g)


def grid_status(g: Graph) -> dict:
    zones = g.ro_query(
        "MATCH (z:Zone) RETURN z.id, z.energized, z.faulted, z.p_kw ORDER BY z.id"
    ).result_set
    switches = g.ro_query(
        """
        MATCH ()-[s:SW]->()
        RETURN s.id, s.kind, s.status, s.normally, s.fi, coalesce(s.operable, true)
        ORDER BY s.kind, s.id
        """
    ).result_set
    dark_buses = g.ro_query(
        "MATCH (b:Bus) WHERE NOT b.energized RETURN b.id ORDER BY b.id"
    ).result_set
    served_kw, total_kw = g.ro_query(
        """
        MATCH (b:Bus)
        RETURN sum(CASE WHEN b.energized THEN b.p_kw ELSE 0 END), sum(b.p_kw)
        """
    ).result_set[0]
    return {
        "energized_zones": [z for z, on, _, _ in zones if on],
        "dark_zones": [z for z, on, _, _ in zones if not on],
        "faulted_zones": [z for z, _, faulted, _ in zones if faulted],
        "dark_buses": [b for (b,) in dark_buses],
        "switches": [
            dict(zip(("id", "kind", "status", "normally", "fi", "operable"), row)) for row in switches
        ],
        "served_kw": served_kw,
        "total_kw": total_kw,
    }
