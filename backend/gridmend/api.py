import json
import os
from contextlib import asynccontextmanager
import queue
import threading
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from gridmend import db
from gridmend.config import load_settings
from gridmend.flisr.candidates import load_screens, plan_restoration
from gridmend.flisr.crew import CrewError, CrewModelError, crew_reports, file_report
from gridmend.flisr.execute import ExecuteError, execute_plan, verified_plans
from gridmend.flisr.explain import explain_incident
from gridmend.flisr.isolate import IsolateError, isolate
from gridmend.flisr.locate import LocateError, locate_fault
from gridmend.flisr.policy import (
    ModelError, NotHealthyError, PolicyError, clear_conditions, current_context, set_conditions,
)
from gridmend.flisr.trace import incident_trace, open_incident, plan_details
from gridmend.grid.case33 import load_voltages
from gridmend.grid.dependency import line_dependencies
from gridmend.grid.energize import grid_status
from gridmend.grid.load import LIVE_GRAPH
from gridmend.sim import FaultError, apply_fault, reset

# The graph the API serves; tests point it at a scratch copy.
GRAPH_NAME = LIVE_GRAPH

# Written next to the evaluation results by `gridmend eval` (see eval/run.py).
EVAL_OUT = Path(__file__).resolve().parents[1] / "eval" / "out"


def load_caches() -> None:
    # Screens and power flows computed while building the evaluation make the first plan on a
    # slow host as fast as later ones; pandapower, slow to import there, then only loads for a
    # configuration the evaluation never saw.
    load_screens(EVAL_OUT / "screens.json")
    load_voltages(EVAL_OUT / "voltages.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_caches()
    yield


app = FastAPI(title="GridMend", lifespan=lifespan)
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


class ContextRequest(BaseModel):
    conditions: str


class CrewRequest(BaseModel):
    note: str


class WhatIfRequest(BaseModel):
    switches: list[str]
    fault_line: str | None = None


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
        RETURN b.id, b.zone, b.energized, b.critical, b.facility, b.weight, b.p_kw, b.is_source
        ORDER BY b.id
        """
    ).result_set
    lines = g.ro_query(
        """
        MATCH (a:Bus)-[r:SEG|SW]->(b:Bus)
        RETURN r.id, a.id, b.id, type(r), r.kind, r.status, r.normally, r.fi, r.faulted,
               r.cap_kva, coalesce(r.operable, true), r.report
        ORDER BY r.id
        """
    ).result_set
    zones = g.ro_query(
        """
        MATCH (z:Zone)
        RETURN z.id, z.energized, z.faulted, coalesce(z.cut_off, false), z.p_kw ORDER BY z.id
        """
    ).result_set
    status = grid_status(g)
    carried = {d["line"]: d for d in line_dependencies(g)}
    return {
        "buses": [
            {"id": b, "zone": zone, "energized": on, "critical": critical,
             "facility": facility or None, "weight": weight, "p_kw": p_kw, "is_source": source}
            for b, zone, on, critical, facility, weight, p_kw, source in buses
        ],
        "lines": [
            {"id": lid, "from": a, "to": b, "type": "switch" if t == "SW" else "segment",
             "kind": kind or "segment", "status": status_ or "closed", "normally": normally,
             "fi": fi, "faulted": bool(faulted), "cap_kva": cap,
             "operable": operable, "report": report, "carries": carried.get(lid)}
            for lid, a, b, t, kind, status_, normally, fi, faulted, cap, operable, report in lines
        ],
        "zones": [
            {"id": z, "energized": on, "faulted": faulted, "cut_off": cut_off, "p_kw": p_kw}
            for z, on, faulted, cut_off, p_kw in zones
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
    # Recommend from this round only: planning again (e.g. after a crew report) leaves the
    # earlier plans in the trace, and the operator chooses from the list just shown.
    current = {r.plan_id for _, r in results}
    ranked = [p for p in verified_plans(g, incident["id"]) if p["id"] in current]
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


@app.get("/plans")
def plans() -> dict:
    g = live()
    incident = current_incident(g)
    details = plan_details(g, incident["id"])
    agent_pick = next((p["plan_id"] for p in details if p["recommended"]), None)
    ranked = verified_plans(g, incident["id"])
    return {
        "incident": incident["id"],
        "recommended": agent_pick or (ranked[0]["id"] if ranked else None),
        # Dropped zones are only known during verification, so plans read back from the trace
        # report none.
        "candidates": [{**p, "passed": p["status"] != "rejected", "origin": "agent", "dropped_zones": []}
                       for p in details],
    }


# One agent run at a time across all visitors, with a pause between runs, to protect the
# LLM provider's rate limits and quota.
AGENT_COOLDOWN_S = float(os.getenv("AGENT_COOLDOWN_S", "30"))
_agent_lock = threading.Lock()
_agent_last_finished = 0.0


@app.get("/agent")
def agent_info() -> dict:
    settings = load_settings()
    return {"available": settings.llm_configured,
            "model": settings.llm_model if settings.llm_configured else None}


@app.post("/agent/run")
def agent_run() -> StreamingResponse:
    global _agent_last_finished
    settings = load_settings()
    if not settings.llm_configured:
        raise HTTPException(503, "no LLM is configured on this server")
    conn = db.connect()
    g = conn.select_graph(GRAPH_NAME)
    faulted = g.ro_query("MATCH ()-[s:SEG {faulted: true}]->() RETURN count(s)").result_set[0][0]
    if not faulted or open_incident(g):
        raise HTTPException(409, "the agent starts from a tripped breaker: fault a line first")
    if not _agent_lock.acquire(blocking=False):
        raise HTTPException(429, "the agent is already working an incident; try again shortly")
    wait = AGENT_COOLDOWN_S - (time.monotonic() - _agent_last_finished)
    if wait > 0:
        _agent_lock.release()
        raise HTTPException(429, f"the agent is cooling down; try again in {int(wait) + 1} s")

    events: queue.Queue = queue.Queue()

    def work() -> None:
        global _agent_last_finished
        from gridmend.agent.loop import ADVISOR_PROMPT, run_agent
        from gridmend.agent.tools import GridTools

        try:
            client, model = agent_client()
            tools = GridTools(conn, g, approve=lambda plan: False, can_execute=False)
            answer = run_agent(
                client, model, tools,
                on_step=lambda name, args, result: events.put(
                    {"type": "tool", "name": name, "args": args, "result": result}),
                prompt=ADVISOR_PROMPT,
            )
            events.put({"type": "final", "text": answer, "model": model})
        except Exception as exc:
            events.put({"type": "error", "detail": str(exc)})
        finally:
            _agent_last_finished = time.monotonic()
            _agent_lock.release()
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while (event := events.get()) is not None:
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def agent_client():
    from gridmend.agent.llm import make_client

    return make_client()


@app.get("/queries")
def queries() -> dict:
    from gridmend.showcase import run_steps

    return {"graph": GRAPH_NAME, "steps": run_steps(live())}


@app.get("/dependencies")
def dependencies() -> dict:
    return {"lines": line_dependencies(live())}


@app.get("/risk")
def risk() -> dict:
    """Per faulted line: what stays dark after the best safe restoration, from the evaluation."""
    from eval.run import load_results

    rows = load_results()
    if rows is None:
        raise HTTPException(404, "no evaluation results yet; run `gridmend eval`")
    return {"lines": [
        {"line": r.fault, "unserved_kw": r.unserved_kw, "restored_kw": r.restored_kw,
         "critical_dark": [c for c in r.critical_dark.split(";") if c]}
        for r in rows if r.method == "gridmend"
    ]}


@app.get("/results")
def results() -> dict:
    from eval.run import load_results, summarize

    rows = load_results()
    if rows is None:
        raise HTTPException(404, "no evaluation results yet; run `gridmend eval`")
    return {"summary": summarize(rows), "rows": [asdict(r) for r in rows]}


@app.get("/context")
def context() -> dict:
    return current_context(live())


@app.post("/context")
def set_context(request: ContextRequest) -> dict:
    # The LLM reads the policy and the operator's report; deterministic code checks every
    # priority it proposes before anything is written.
    if not load_settings().llm_configured:
        raise HTTPException(503, "no language model is configured")
    # Shares the agent's lock so only one model call runs at a time.
    if not _agent_lock.acquire(blocking=False):
        raise HTTPException(429, "the model is busy with another request; try again shortly")
    try:
        client, model = agent_client()
        return set_conditions(live(), client, model, request.conditions)
    except NotHealthyError as exc:
        raise HTTPException(409, str(exc))
    except ModelError as exc:
        raise HTTPException(503, str(exc))
    except PolicyError as exc:
        raise HTTPException(422, str(exc))
    finally:
        _agent_lock.release()


@app.post("/whatif")
def whatif(request: WhatIfRequest) -> dict:
    from gridmend.flisr.whatif import WhatIfError, what_if

    conn = db.connect()
    try:
        return what_if(conn, conn.select_graph(GRAPH_NAME), request.switches, request.fault_line)
    except (WhatIfError, FaultError, LocateError, IsolateError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/crew")
def crew() -> dict:
    return {"reports": crew_reports(live())}


@app.post("/crew")
def file_crew_report(request: CrewRequest) -> dict:
    # The LLM reads the note; a finding only reaches the graph if it names a real switch and
    # quotes the note.
    if not load_settings().llm_configured:
        raise HTTPException(503, "no language model is configured")
    if not _agent_lock.acquire(blocking=False):
        raise HTTPException(429, "the model is busy with another request; try again shortly")
    try:
        client, model = agent_client()
        return file_report(live(), client, model, request.note)
    except CrewModelError as exc:
        raise HTTPException(503, str(exc))
    except CrewError as exc:
        raise HTTPException(422, str(exc))
    finally:
        _agent_lock.release()


@app.delete("/context")
def delete_context() -> dict:
    try:
        return clear_conditions(live())
    except NotHealthyError as exc:
        raise HTTPException(409, str(exc))


@app.post("/reset")
def reset_grid() -> dict:
    reset(db.connect())
    return {"ok": True}
