from falkordb import Graph

# What depends on each line in the current configuration. The feeder is radial, so every
# energized bus has one path from the source and depends on every line along it.
DEPENDENCY_QUERY = """
MATCH (s:Bus {is_source: true})
MATCH p = (s)-[:SEG|SW*0..100 {status: 'closed'}]-(b:Bus {energized: true})
UNWIND relationships(p) AS r
WITH r, count(b) AS buses, sum(b.p_kw) AS kw,
     collect(CASE WHEN b.critical THEN b.facility END) AS critical
RETURN r.id AS line, buses, kw, critical
ORDER BY kw DESC
"""


def line_dependencies(g: Graph) -> list[dict]:
    return [
        {"line": line, "buses": buses, "kw": kw, "critical": critical}
        for line, buses, kw, critical in g.ro_query(DEPENDENCY_QUERY).result_set
    ]
