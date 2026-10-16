from falkordb import Graph

# Each line becomes a pair of FLOW edges, one per direction, because maxFlow follows edge
# direction and takes a single relationship type. Every healthy load bus drains into one sink
# through an edge rated at its own demand.
FLOW_NETWORK = [
    "MATCH (b:Bus)-[:IN_ZONE]->(:Zone {faulted: true}) DETACH DELETE b",
    """
    MATCH (a:Bus)-[r:SEG|SW]->(b:Bus)
    CREATE (a)-[:FLOW {id: r.id, cap_kva: r.cap_kva}]->(b),
           (b)-[:FLOW {id: r.id, cap_kva: r.cap_kva}]->(a)
    """,
    """
    CREATE (t:Sink)
    WITH t
    MATCH (b:Bus) WHERE NOT b.is_source AND b.s_kva > 0
    CREATE (b)-[:FLOW {cap_kva: b.s_kva}]->(t)
    """,
]

TRANSFER_QUERY = """
MATCH (s:Bus {is_source: true}), (t:Sink)
CALL algo.maxFlow({sourceNodes: [s], targetNodes: [t], capacityProperty: 'cap_kva',
                   relationshipTypes: ['FLOW']})
YIELD edges, edgeFlows, maxFlow
RETURN maxFlow AS deliverable_kva,
       [i IN range(0, size(edges) - 1)
        WHERE edges[i].id IS NOT NULL AND edgeFlows[i] >= edges[i].cap_kva - 0.001
        | edges[i].id] AS saturated
"""


def build_flow_network(fork: Graph) -> None:
    for query in FLOW_NETWORK:
        fork.query(query)


def transfer_limit(g: Graph) -> dict:
    """How much healthy load the feeder could carry if every switch outside the faulted zone
    were free to move, and which lines run at their rating in that best case.

    Max flow ignores radiality and voltage and adds kVA as scalars, so it bounds what any
    switching plan can restore rather than describing one.
    """
    fork = Graph(g.client, f"transfer_{g.name}")
    if fork.name in g.client.execute_command("GRAPH.LIST"):
        fork.delete()
    fork = g.copy(fork.name)
    try:
        build_flow_network(fork)
        demand = fork.ro_query(
            "MATCH (b:Bus) WHERE NOT b.is_source RETURN sum(b.s_kva)"
        ).result_set[0][0] or 0.0
        deliverable, saturated = fork.ro_query(TRANSFER_QUERY).result_set[0]
    finally:
        fork.delete()
    shortfall = max(demand - deliverable, 0)
    return {
        "demand_kva": round(demand),
        "deliverable_kva": round(deliverable),
        "shortfall_kva": round(shortfall),
        # With no shortfall a saturated line is just one of many equally good routings.
        "bottlenecks": sorted(set(saturated)) if round(shortfall) else [],
    }
