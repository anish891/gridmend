from falkordb import Graph


def isolation_plans(g: Graph, incident_id: str) -> list[str]:
    return [row[0] for row in g.ro_query(
        """
        MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {kind: 'isolation', status: 'executed'})
        RETURN p.id ORDER BY p.id
        """,
        {"id": incident_id},
    ).result_set]


def mark_cut_off(g: Graph, incident_id: str) -> None:
    # A healthy zone joined to a faulted one by a jammed closed switch cannot be separated from
    # the fault, so it stays dark with it; recorded with the switch that forced it.
    g.query(
        """
        MATCH (i:Incident {id: $id}), (f:Zone {faulted: true})<-[:IN_ZONE]-(:Bus)
              -[s:SW {status: 'closed', operable: false}]-(:Bus)-[:IN_ZONE]->(z:Zone {faulted: false})
        SET z.cut_off = true
        MERGE (i)-[:CUTS_OFF {via: s.id}]->(z)
        """,
        {"id": incident_id},
    )
