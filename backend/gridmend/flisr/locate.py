from dataclasses import dataclass

from falkordb import Graph

from gridmend.flisr.trace import add_fault_zones, open_incident, set_incident_status


class LocateError(Exception):
    pass


@dataclass(frozen=True)
class Location:
    # One zone per fault the indicators can see, each with its chain of tripped indicators.
    zones: list[int]
    chains: dict[int, list[str]]

    @property
    def zone(self) -> int:
        return self.zones[0]

    @property
    def chain(self) -> list[str]:
        return self.chains[self.zones[0]]


LOCATE_QUERY = """
MATCH (:Bus)-[up:SW]->(:Bus)-[:IN_ZONE]->(z:Zone)
WHERE up.kind <> 'tie' AND up.fi = 'set'
OPTIONAL MATCH (b:Bus)-[:IN_ZONE]->(z), (b)-[down:SW]->(:Bus)
WHERE down.kind = 'sectionalizer' AND down.fi = 'set'
WITH z, count(down) AS set_downstream
WHERE set_downstream = 0
RETURN z.id AS faulted_zone
"""


def find_faulted_zones(g: Graph) -> list[int]:
    # A fault sits in a zone whose upstream indicator is set while every downstream indicator
    # is clear: current reached the zone but went no further. Faults on separate branches each
    # end their own chain; a fault upstream of another on the same path stays hidden until the
    # downstream one is cut out and the breaker recloses onto it.
    rows = g.ro_query(LOCATE_QUERY).result_set
    if not rows:
        raise LocateError("no chain of tripped indicators to follow")
    return sorted(row[0] for row in rows)


def indicator_chain(g: Graph, zone: int) -> list[str]:
    # Switches on the normal radial path from the source into the zone, in order.
    rows = g.ro_query(
        """
        MATCH (s:Bus {is_source: true}), (z:Zone {id: $zone})<-[:IN_ZONE]-(t:Bus)
        MATCH p = (s)-[:SEG|SW*1..100]-(t)
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
    zones = find_faulted_zones(g)
    chains = {zone: indicator_chain(g, zone) for zone in zones}
    # Zones found in an earlier round stay faulted.
    g.query("MATCH (z:Zone) SET z.faulted = z.faulted OR z.id IN $zones", {"zones": zones})
    incident = open_incident(g)
    if incident and incident["status"] == "tripped":
        add_fault_zones(g, incident["id"], zones)
        set_incident_status(g, incident["id"], "located")
    return Location(zones=zones, chains=chains)
