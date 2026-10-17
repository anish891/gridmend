from falkordb import Graph


ZONES_QUERY = """
CALL algo.WCC({nodeLabels: ['Bus'], relationshipTypes: ['SEG']})
YIELD node, componentId
WITH componentId, collect(node) AS nodes
WHERE none(n IN nodes WHERE n.is_source)
RETURN [n IN nodes | n.id] AS buses
"""


def build_zones(g: Graph) -> int:
    # A zone is a connected component over unswitched segments: the smallest isolatable piece.
    g.query("MATCH (z:Zone) DETACH DELETE z")

    components = g.ro_query(ZONES_QUERY).result_set
    zones = [{"id": min(buses), "buses": buses} for (buses,) in components]

    g.query(
        """
        UNWIND $zones AS r
        CREATE (z:Zone {id: r.id, faulted: false})
        WITH z, r
        UNWIND r.buses AS bus_id
        MATCH (b:Bus {id: bus_id})
        SET b.zone = z.id
        CREATE (b)-[:IN_ZONE]->(z)
        """,
        {"zones": zones},
    )
    refresh_zone_weights(g)
    # Line ids put the upstream bus first on every normally closed line, so for
    # sectionalizers the edge points from the upstream zone to the downstream one.
    g.query(
        """
        MATCH (a:Bus)-[s:SW]->(b:Bus),
              (a)-[:IN_ZONE]->(za:Zone), (b)-[:IN_ZONE]->(zb:Zone)
        CREATE (za)-[:BOUNDARY {switch_id: s.id, kind: s.kind}]->(zb)
        """
    )
    return len(zones)


def refresh_zone_weights(g: Graph) -> None:
    g.query(
        """
        MATCH (b:Bus)-[:IN_ZONE]->(z:Zone)
        WITH z, sum(b.p_kw) AS p_kw, sum(b.p_kw * b.weight) AS weighted_kw
        SET z.p_kw = p_kw, z.weighted_kw = weighted_kw
        """
    )


def zone_table(g: Graph) -> list[dict]:
    zones = {
        zid: {"id": zid, "buses": buses, "p_kw": p_kw, "weighted_kw": weighted_kw,
              "upstream": [], "downstream": [], "ties": []}
        for zid, buses, p_kw, weighted_kw in g.ro_query(
            """
            MATCH (b:Bus)-[:IN_ZONE]->(z:Zone)
            WITH z, collect(b.id) AS buses
            RETURN z.id, buses, z.p_kw, z.weighted_kw
            """
        ).result_set
    }

    # The breaker feeds Z2 from the substation bus, which belongs to no zone.
    for switch_id, zid in g.ro_query(
        """
        MATCH (:Bus {is_source: true})-[s:SW]->(:Bus)-[:IN_ZONE]->(z:Zone)
        RETURN s.id, z.id
        """
    ).result_set:
        zones[zid]["upstream"].append(switch_id)

    for up, switch_id, kind, down in g.ro_query(
        "MATCH (a:Zone)-[r:BOUNDARY]->(b:Zone) RETURN a.id, r.switch_id, r.kind, b.id"
    ).result_set:
        if kind == "tie":
            zones[up]["ties"].append(f"{switch_id} (Z{down})")
            zones[down]["ties"].append(f"{switch_id} (Z{up})")
        else:
            zones[up]["downstream"].append(switch_id)
            zones[down]["upstream"].append(switch_id)

    return [zones[zid] for zid in sorted(zones)]


def zone_state(energized: bool, faulted: bool, cut_off: bool) -> str:
    if faulted:
        return "faulted"
    if cut_off:
        return "cut off with the fault"
    return "live" if energized else "dark"


def zone_map(g: Graph) -> dict:
    """The feeder at the level an operator switches it: each zone with its load and state, and
    each switch with the zones it joins. A breaker joins a substation to its first zone."""
    critical: dict[int, list[str]] = {}
    for zid, facility in g.ro_query(
        "MATCH (b:Bus {critical: true}) RETURN b.zone, b.facility ORDER BY b.id"
    ).result_set:
        critical.setdefault(zid, []).append(facility)
    zones = [
        {"zone": f"Z{zid}", "state": zone_state(energized, faulted, cut_off),
         "kw": round(p_kw), "kva": round(kva), "critical": critical.get(zid, [])}
        for zid, energized, faulted, cut_off, p_kw, kva in g.ro_query(
            """
            MATCH (b:Bus)-[:IN_ZONE]->(z:Zone)
            RETURN z.id, z.energized, z.faulted, coalesce(z.cut_off, false), z.p_kw, sum(b.s_kva)
            ORDER BY z.id
            """
        ).result_set
    ]
    switches = [
        {"id": switch_id, "kind": kind, "status": status, "cap_kva": round(cap),
         "joins": [f"Z{a}" if a is not None else "substation", f"Z{b}"],
         **({"jammed": True} if operable is False else {})}
        for switch_id, kind, status, operable, cap, a, b in g.ro_query(
            """
            MATCH (x:Bus)-[s:SW]->(y:Bus)-[:IN_ZONE]->(zb:Zone)
            OPTIONAL MATCH (x)-[:IN_ZONE]->(za:Zone)
            RETURN s.id, s.kind, s.status, s.operable, s.cap_kva, za.id, zb.id
            ORDER BY s.kind, s.id
            """
        ).result_set
    ]
    return {"zones": zones, "switches": switches}
