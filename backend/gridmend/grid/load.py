import math
from pathlib import Path

import yaml
from falkordb import FalkorDB

from gridmend.grid.case33 import ieee_bus, line_id, load_network
from gridmend.grid.zones import build_zones

ASSUMPTIONS_PATH = Path(__file__).resolve().parents[2] / "data" / "assumptions.yaml"

BASE_GRAPH = "grid_base"
LIVE_GRAPH = "grid_live"


def load_assumptions() -> dict:
    with open(ASSUMPTIONS_PATH) as f:
        return yaml.safe_load(f)


def bus_rows(net, assumptions: dict) -> list[dict]:
    p_kw = {ieee_bus(i): 0.0 for i in net.bus.index}
    q_kvar = dict(p_kw)
    for bus, p, q in zip(net.load.bus, net.load.p_mw, net.load.q_mvar):
        p_kw[ieee_bus(bus)] += p * 1000
        q_kvar[ieee_bus(bus)] += q * 1000

    critical = assumptions["critical_loads"]
    weights = assumptions["weights"]
    rows = []
    for pp_index in net.bus.index:
        bus = ieee_bus(pp_index)
        facility = critical.get(bus)
        rows.append({
            "id": bus,
            "pp_index": int(pp_index),
            "p_kw": p_kw[bus],
            "q_kvar": q_kvar[bus],
            "s_kva": math.hypot(p_kw[bus], q_kvar[bus]),
            "critical": facility is not None,
            "facility": facility or "",
            "weight": weights["critical"] if facility else weights["default"],
            "is_source": bus == assumptions["substation"],
        })
    return rows


def is_trunk(a: int, b: int, main_feeder_end: int) -> bool:
    return b == a + 1 and b <= main_feeder_end


def line_rows(net, assumptions: dict) -> tuple[list[dict], list[dict]]:
    sectionalizers = set(assumptions["sectionalizers"])
    ratings = assumptions["ratings_kva"]
    segs, switches = [], []

    for line in net.line.itertuples():
        lid = line_id(line.from_bus, line.to_bus)
        a, b = (int(x) for x in lid.split("-"))
        row = {
            "id": lid,
            "a": a,
            "b": b,
            "r_ohm": line.r_ohm_per_km * line.length_km,
            "x_ohm": line.x_ohm_per_km * line.length_km,
            "cap_kva": ratings["trunk"] if is_trunk(a, b, assumptions["main_feeder_end"])
            else ratings["lateral"],
        }

        if not line.in_service:
            kind = "tie"
        elif lid == assumptions["breaker"]:
            kind = "breaker"
        elif lid in sectionalizers:
            kind = "sectionalizer"
        else:
            segs.append(row)
            continue

        status = "open" if kind == "tie" else "closed"
        switches.append({
            **row,
            "kind": kind,
            "status": status,
            "normally": status,
            "fi": "none" if kind == "tie" else "clear",
        })

    return segs, switches


def write_graph(db: FalkorDB) -> dict:
    net = load_network()
    assumptions = load_assumptions()
    buses = bus_rows(net, assumptions)
    segs, switches = line_rows(net, assumptions)

    existing = set(db.list_graphs())
    for name in (BASE_GRAPH, LIVE_GRAPH):
        if name in existing:
            db.select_graph(name).delete()

    g = db.select_graph(BASE_GRAPH)
    for label in ("Bus", "Zone", "Plan", "Incident"):
        g.query(f"CREATE INDEX FOR (n:{label}) ON (n.id)")

    g.query("UNWIND $rows AS r CREATE (b:Bus) SET b = r", {"rows": buses})
    g.query(
        """
        UNWIND $rows AS r
        MATCH (a:Bus {id: r.a}), (b:Bus {id: r.b})
        CREATE (a)-[:SEG {id: r.id, r_ohm: r.r_ohm, x_ohm: r.x_ohm,
                          cap_kva: r.cap_kva, faulted: false}]->(b)
        """,
        {"rows": segs},
    )
    g.query(
        """
        UNWIND $rows AS r
        MATCH (a:Bus {id: r.a}), (b:Bus {id: r.b})
        CREATE (a)-[:SW {id: r.id, kind: r.kind, status: r.status, normally: r.normally,
                         r_ohm: r.r_ohm, x_ohm: r.x_ohm, cap_kva: r.cap_kva, fi: r.fi}]->(b)
        """,
        {"rows": switches},
    )
    build_zones(g)

    g.copy(LIVE_GRAPH)
    return graph_summary(db.select_graph(LIVE_GRAPH))


def graph_summary(g) -> dict:
    buses = g.ro_query("MATCH (b:Bus) RETURN count(b)").result_set[0][0]
    segs = g.ro_query("MATCH ()-[s:SEG]->() RETURN count(s)").result_set[0][0]
    kinds = g.ro_query(
        "MATCH ()-[s:SW]->() RETURN s.kind, count(s) ORDER BY s.kind"
    ).result_set
    zones = g.ro_query("MATCH (z:Zone) RETURN count(z)").result_set[0][0]
    return {"buses": buses, "seg": segs, "sw": dict(kinds), "zones": zones}
