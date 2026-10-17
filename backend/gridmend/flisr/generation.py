from falkordb import Graph

from gridmend.flisr.trace import open_incident, set_incident_status

# Load a bus draws from the grid once its own online generation is netted off (unity power
# factor, so only real power changes). Used by the capacity check and the candidate screen.
NET_P_KW = "b.p_kw - CASE WHEN coalesce(b.dg_online, false) THEN b.dg_kw ELSE 0 END"


def online_generation(g: Graph) -> dict[int, float]:
    return {bus: kw for bus, kw in g.ro_query(
        "MATCH (b:Bus) WHERE coalesce(b.dg_online, false) RETURN b.id, b.dg_kw ORDER BY b.id"
    ).result_set}


def generation_status(g: Graph) -> list[dict]:
    return [
        {"bus": bus, "kw": kw, "online": online, "energized": energized, "zone": zone}
        for bus, kw, online, energized, zone in g.ro_query(
            """
            MATCH (b:Bus) WHERE coalesce(b.dg_kw, 0) > 0
            RETURN b.id, b.dg_kw, b.dg_online, b.energized, b.zone ORDER BY b.id
            """
        ).result_set
    ]


def reconnect_generation(g: Graph) -> dict:
    """Bring back every unit whose bus has power again, as it would after the reconnection
    delay. If healthy zones are still dark after a restoration, the incident goes back to
    'isolated' so a second restoration can be planned with the extra headroom."""
    back = [row[0] for row in g.query(
        """
        MATCH (b:Bus) WHERE coalesce(b.dg_kw, 0) > 0 AND b.energized AND NOT b.dg_online
        SET b.dg_online = true
        RETURN b.id ORDER BY b.id
        """
    ).result_set]
    incident = open_incident(g)
    dark = g.ro_query(
        "MATCH (z:Zone {energized: false, faulted: false}) "
        "WHERE NOT coalesce(z.cut_off, false) RETURN count(z)"
    ).result_set[0][0]
    replan = bool(back and incident and incident["status"] == "restored" and dark)
    if replan:
        # Plans left from the first round were checked against a grid that has since changed.
        g.query(
            """
            MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {kind: 'restoration', status: 'verified'})
            SET p.status = 'superseded'
            """,
            {"id": incident["id"]},
        )
        set_incident_status(g, incident["id"], "isolated")
    return {"reconnected": back, "replan": replan}
