from dataclasses import dataclass

from falkordb import Graph


class LocateError(Exception):
    pass


@dataclass(frozen=True)
class Location:
    zone: int
    chain: list[str]


def find_faulted_zone(g: Graph) -> int:
    # The fault sits in the zone whose upstream indicator is set while every
    # downstream indicator is clear: current reached the zone but went no further.
    rows = g.ro_query(
        """
        MATCH (:Bus)-[up:SW]->(:Bus)-[:IN_ZONE]->(z:Zone)
        WHERE up.kind <> 'tie' AND up.fi = 'set'
        OPTIONAL MATCH (b:Bus)-[:IN_ZONE]->(z), (b)-[down:SW]->(:Bus)
        WHERE down.kind = 'sectionalizer' AND down.fi = 'set'
        WITH z, count(down) AS set_downstream
        WHERE set_downstream = 0
        RETURN z.id
        """
    ).result_set
    if len(rows) != 1:
        raise LocateError(f"expected one faulted zone, indicators point to {len(rows)}")
    return rows[0][0]


def indicator_chain(g: Graph, zone: int) -> list[str]:
    # Switches on the normal radial path from the source into the zone, in order.
    rows = g.ro_query(
        """
        MATCH (s:Bus {is_source: true}), (z:Zone {id: $zone})<-[:IN_ZONE]-(t:Bus)
        MATCH p = (s)-[:SEG|SW*1..40]-(t)
        WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.kind <> 'tie')
        RETURN [r IN relationships(p) WHERE type(r) = 'SW' | [r.id, r.fi]]
        ORDER BY length(p)
        LIMIT 1
        """,
        {"zone": zone},
    ).result_set
    chain = rows[0][0]
    unset = [sid for sid, fi in chain if fi != "set"]
    if unset:
        raise LocateError(f"indicators on the path to Z{zone} are not set: {', '.join(unset)}")
    return [sid for sid, _ in chain]


def locate_fault(g: Graph) -> Location:
    zone = find_faulted_zone(g)
    chain = indicator_chain(g, zone)
    g.query("MATCH (z:Zone) SET z.faulted = (z.id = $zone)", {"zone": zone})
    return Location(zone=zone, chain=chain)
