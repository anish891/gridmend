from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from gridmend import db
from gridmend.flisr.candidates import plan_restoration
from gridmend.flisr.execute import ExecuteError, execute_plan, verified_plans
from gridmend.flisr.explain import explain_incident
from gridmend.flisr.isolate import IsolateError, isolate
from gridmend.flisr.locate import LocateError, locate_fault
from gridmend.flisr.trace import incident_trace, open_incident
from gridmend.grid.energize import grid_status
from gridmend.grid.load import LIVE_GRAPH
from gridmend.sim import FaultError, apply_fault, reset

# The graph the API serves; tests point it at a scratch copy.
GRAPH_NAME = LIVE_GRAPH

app = FastAPI(title="GridMend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001", "http://127.0.0.1:3001"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class FaultRequest(BaseModel):
    line: str


class ExecuteRequest(BaseModel):
    plan_id: str


def live():
    return db.connect().select_graph(GRAPH_NAME)


def current_incident(g) -> dict:
    incident = open_incident(g)
    if incident is None:
        raise HTTPException(409, "no active incident")
    return incident


@app.get("/state")
def state() -> dict:
    g = live()
    buses = g.ro_query(
        """
        MATCH (b:Bus)
        RETURN b.id, b.zone, b.energized, b.critical, b.facility, b.p_kw, b.is_source
        ORDER BY b.id
        """
    ).result_set
    lines = g.ro_query(
        """
        MATCH (a:Bus)-[r:SEG|SW]->(b:Bus)
        RETURN r.id, a.id, b.id, type(r), r.kind, r.status, r.normally, r.fi, r.faulted,
               r.cap_kva
        ORDER BY r.id
        """
    ).result_set
    zones = g.ro_query(
        "MATCH (z:Zone) RETURN z.id, z.energized, z.faulted, z.p_kw ORDER BY z.id"
    ).result_set
    status = grid_status(g)
    return {
        "buses": [
            {"id": b, "zone": zone, "energized": on, "critical": critical,
             "facility": facility or None, "p_kw": p_kw, "is_source": source}
            for b, zone, on, critical, facility, p_kw, source in buses
        ],
        "lines": [
            {"id": lid, "from": a, "to": b, "type": "switch" if t == "SW" else "segment",
             "kind": kind or "segment", "status": status_ or "closed", "normally": normally,
             "fi": fi, "faulted": bool(faulted), "cap_kva": cap}
            for lid, a, b, t, kind, status_, normally, fi, faulted, cap in lines
        ],
        "zones": [
            {"id": z, "energized": on, "faulted": faulted, "p_kw": p_kw}
            for z, on, faulted, p_kw in zones
        ],
        "served_kw": status["served_kw"],
        "total_kw": status["total_kw"],
        "incident": open_incident(g),
    }


@app.post("/fault")
def fault(request: FaultRequest) -> dict:
    try:
        tripped = apply_fault(live(), request.line)
    except FaultError as exc:
        raise HTTPException(400, str(exc))
    return {"line": request.line, "indicators_set": tripped}


@app.post("/locate")
def locate() -> dict:
    try:
        location = locate_fault(live())
    except LocateError as exc:
        raise HTTPException(400, str(exc))
    return {"zone": location.zone, "chain": location.chain}


@app.post("/isolate")
def isolate_fault() -> dict:
    try:
        incident_id, ops = isolate(live())
    except IsolateError as exc:
        raise HTTPException(400, str(exc))
    return {"incident": incident_id, "ops": [{"switch_id": o.switch_id, "op": o.op} for o in ops]}


@app.post("/plan")
def plan() -> dict:
    conn = db.connect()
    g = conn.select_graph(GRAPH_NAME)
    incident = current_incident(g)
    if incident["status"] != "isolated":
        raise HTTPException(409, f"incident is {incident['status']}, not isolated")

    results = plan_restoration(conn, g, incident["id"])
    ranked = verified_plans(g, incident["id"])
    critical = {p["id"]: p["critical"] for p in ranked}
    return {
        "incident": incident["id"],
        "recommended": ranked[0]["id"] if ranked else None,
        "candidates": [
            {
                "plan_id": r.plan_id,
                "origin": c.origin,
                "score": c.score,
                "ops": [{"switch_id": o.switch_id, "op": o.op} for o in c.ops],
                "passed": r.passed,
                "restored_zones": r.restored_zones,
                "dropped_zones": r.dropped_zones,
                "restored_kw": r.restored_kw,
                "critical": critical.get(r.plan_id, []),
                "min_v_pu": r.min_v_pu,
                "max_loading_pct": r.max_loading_pct,
                "violations": [{"type": k, "detail": d} for k, d in r.violations],
            }
            for c, r in results
        ],
    }


@app.post("/execute")
def execute(request: ExecuteRequest) -> dict:
    # The operator's click in the UI is the approval.
    try:
        ops = execute_plan(live(), request.plan_id)
    except ExecuteError as exc:
        raise HTTPException(400, str(exc))
    return {"plan_id": request.plan_id, "ops": [{"switch_id": s, "op": op} for s, op in ops]}


@app.get("/explain")
def explain() -> dict:
    g = live()
    return explain_incident(g, current_incident(g)["id"])


@app.get("/trace")
def trace() -> dict:
    g = live()
    incident = current_incident(g)
    return {"incident": incident, "plans": incident_trace(g, incident["id"])}


@app.post("/reset")
def reset_grid() -> dict:
    reset(db.connect())
    return {"ok": True}
