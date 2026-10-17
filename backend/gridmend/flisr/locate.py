from falkordb import Graph

from gridmend.flisr.trace import add_fault_zones, open_incident, record_incident, set_incident_status


class LocateError(Exception):
    pass


def declare_fault(g: Graph, zones: list[int], evidence: str) -> str:
    """Record where the agent concluded the fault is. The diagnosis is the agent's: nothing here
    checks it against the indicators. A wrong one shows up when reclosing trips the breaker."""
    incident = open_incident(g)
    if incident and incident["status"] != "tripped":
        raise LocateError(f"{incident['id']} is {incident['status']}; faults are declared after "
                          "a breaker trips")
    if incident is None and not g.ro_query(
        "MATCH ()-[s:SW {kind: 'breaker', status: 'open', normally: 'closed'}]->() RETURN count(s)"
    ).result_set[0][0]:
        raise LocateError("no breaker has tripped")
    known = {row[0] for row in g.ro_query("MATCH (z:Zone) RETURN z.id").result_set}
    unknown = [z for z in zones if z not in known]
    if not zones or unknown:
        raise LocateError(f"unknown zones: {unknown}" if unknown else "name the faulted zones")

    g.query("MATCH (z:Zone) WHERE z.id IN $zones SET z.faulted = true", {"zones": zones})
    if incident is None:
        incident_id = record_incident(g, zones)
    else:
        incident_id = incident["id"]
        add_fault_zones(g, incident_id, zones)
        set_incident_status(g, incident_id, "located")
    g.query(
        """
        MATCH (i:Incident {id: $id})
        SET i.evidence = CASE WHEN i.evidence IS NULL THEN $evidence
                              ELSE i.evidence + ' | ' + $evidence END
        """,
        {"id": incident_id, "evidence": evidence.strip()[:500]},
    )
    return incident_id
