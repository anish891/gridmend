import contextvars
import json
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
import queue
import threading
import time
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from gridmend import db
from gridmend.config import load_settings
from gridmend.flisr.crew import CrewError, CrewModelError, crew_reports, file_report
from gridmend.flisr.execute import ExecuteError, execute_plan
from gridmend.flisr.explain import explain_incident
from gridmend.flisr.generation import generation_status, reconnect_generation
from gridmend.flisr.trace import incident_trace, open_incident, plan_details
from gridmend.grid.case33 import load_voltages
from gridmend.grid.dependency import line_dependencies
from gridmend.grid.energize import grid_status
from gridmend.grid import feeder
from gridmend.sim import FaultError, apply_faults, reset, set_jammed


def graph_name() -> str:
    """The graph a request is served from: the chosen feeder's live twin. Tests patch this."""
    return feeder.live_graph()


def load_caches() -> None:
    # Power flows computed while building the evaluation make a simulation on a slow host as
    # fast as on a laptop; pandapower, slow to import there, then only loads for a
    # configuration the evaluation never saw.
    from eval.run import out_dir

    for name in feeder.FEEDERS:
        with feeder.use(name):
            load_voltages(out_dir() / "voltages.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_caches()
    yield


app = FastAPI(title="GridMend", lifespan=lifespan)


@app.middleware("http")
async def choose_feeder(request: Request, call_next):
    # Every request names its feeder (?feeder=oberrhein); without one it gets the default.
    name = request.query_params.get("feeder", feeder.DEFAULT)
    if name not in feeder.FEEDERS:
        return JSONResponse({"detail": f"unknown feeder {name}"}, status_code=400)
    with feeder.use(name):
        return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001", "http://127.0.0.1:3001"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class FaultRequest(BaseModel):
    line: str | None = None
    # Several segments at once, for simultaneous faults.
    lines: list[str] | None = None


class JamRequest(BaseModel):
    switch_id: str
    jammed: bool = True


class ExecuteRequest(BaseModel):
    plan_id: str


class CrewRequest(BaseModel):
    note: str


def live():
    return db.connect().select_graph(graph_name())


def current_incident(g) -> dict:
    incident = open_incident(g)
    if incident is None:
        raise HTTPException(409, "no active incident")
    return incident


@app.get("/feeders")
def feeders() -> dict:
    """The feeders this server can serve: those whose twin has been loaded."""
    loaded = set(db.connect().list_graphs())
    available = []
    for name, label in feeder.FEEDERS.items():
        with feeder.use(name):
            if feeder.live_graph() in loaded:
                available.append({"id": name, "label": label})
    return {"default": feeder.DEFAULT, "feeders": available}


@app.get("/layout")
def layout() -> dict:
    """Map positions for feeders the UI has no hand-drawn layout for."""
    if feeder.name() == "ieee33":
        return {"positions": None}
    module = feeder.network_module()
    return {"positions": {str(bus): xy for bus, xy in module.positions().items()}}


@app.get("/state")
def state() -> dict:
    g = live()
    buses = g.ro_query(
        """
        MATCH (b:Bus)
        RETURN b.id, b.zone, b.energized, b.critical, b.facility, b.weight, b.p_kw, b.is_source,
               coalesce(b.dg_kw, 0), coalesce(b.dg_online, false)
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
             "facility": facility or None, "weight": weight, "p_kw": p_kw, "is_source": source,
             "dg_kw": dg_kw, "dg_online": dg_online}
            for b, zone, on, critical, facility, weight, p_kw, source, dg_kw, dg_online in buses
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
        lines = request.lines or ([request.line] if request.line else [])
        tripped = apply_faults(live(), lines)
    except FaultError as exc:
        raise HTTPException(400, str(exc))
    return {"line": lines[0], "lines": lines, "indicators_set": tripped}


@app.post("/jam")
def jam(request: JamRequest) -> dict:
    """Scenario setup: mark a switch as one that will not operate when the fault comes."""
    try:
        set_jammed(live(), request.switch_id, request.jammed)
    except FaultError as exc:
        raise HTTPException(400, str(exc))
    return {"switch_id": request.switch_id, "jammed": request.jammed}


@app.get("/generation")
def generation() -> dict:
    return {"units": generation_status(live())}


@app.post("/generation/reconnect")
def reconnect() -> dict:
    """Bring back generation on buses that have power again; may reopen planning."""
    return reconnect_generation(live())


@app.post("/execute")
def execute(request: ExecuteRequest) -> dict:
    # The operator's click in the UI is the approval.
    try:
        ops, tripped = execute_plan(live(), request.plan_id)
    except ExecuteError as exc:
        raise HTTPException(400, str(exc))
    return {"plan_id": request.plan_id, "ops": [{"switch_id": s, "op": op} for s, op in ops],
            "tripped_again": tripped}


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
    return {
        "incident": incident["id"],
        "recommended": next((p["plan_id"] for p in details if p["recommended"]), None),
        # Dropped zones are only known during verification, so plans read back from the trace
        # report none.
        "candidates": [{**p, "passed": p["status"] != "rejected", "dropped_zones": []}
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
    g = conn.select_graph(graph_name())
    faulted = g.ro_query("MATCH ()-[s:SEG {faulted: true}]->() RETURN count(s)").result_set[0][0]
    incident = open_incident(g)
    # The agent takes over a tripped feeder, or an isolated one that needs (re)planning, e.g.
    # after a crew report or once generation is back.
    if not faulted or (incident and incident["status"] == "restored"):
        raise HTTPException(409, "the agent starts from a tripped breaker: fault a line first")
    if not _agent_lock.acquire(blocking=False):
        raise HTTPException(429, "the agent is already working an incident; try again shortly")
    wait = AGENT_COOLDOWN_S - (time.monotonic() - _agent_last_finished)
    if wait > 0:
        _agent_lock.release()
        raise HTTPException(429, f"the agent is cooling down; try again in {int(wait) + 1} s")

    from gridmend.agent.loop import ADVISOR_PROMPT, INCIDENT_TASK
    from gridmend.agent.tools import GridTools

    def done() -> None:
        global _agent_last_finished
        _agent_last_finished = time.monotonic()
        _agent_lock.release()

    tools = GridTools(conn, g, approve=lambda plan: False, can_execute=False)
    return agent_stream(tools, ADVISOR_PROMPT, INCIDENT_TASK, done)


# Questions are short and read-only, so they wait less between runs than incidents.
ASK_COOLDOWN_S = float(os.getenv("ASK_COOLDOWN_S", "5"))
_ask_last_finished = 0.0


class Question(BaseModel):
    question: str


@app.post("/ask")
def ask(body: Question) -> StreamingResponse:
    from gridmend.agent.loop import ASK_PROMPT, ASK_STEPS
    from gridmend.agent.tools import READ_ONLY, GridTools

    if not load_settings().llm_configured:
        raise HTTPException(503, "no LLM is configured on this server")
    question = body.question.strip()
    if not question or len(question) > 300:
        raise HTTPException(422, "ask a question of at most 300 characters")
    if not _agent_lock.acquire(blocking=False):
        raise HTTPException(429, "the assistant is busy; try again shortly")
    wait = ASK_COOLDOWN_S - (time.monotonic() - _ask_last_finished)
    if wait > 0:
        _agent_lock.release()
        raise HTTPException(429, f"try again in {int(wait) + 1} s")

    def done() -> None:
        global _ask_last_finished
        _ask_last_finished = time.monotonic()
        _agent_lock.release()

    conn = db.connect()
    tools = GridTools(conn, conn.select_graph(graph_name()), approve=lambda plan: False,
                      can_execute=False, only=READ_ONLY)
    return agent_stream(tools, ASK_PROMPT, question, done, ASK_STEPS)


def agent_stream(tools, prompt: str, task: str, done: Callable[[], None],
                 max_steps: int | None = None) -> StreamingResponse:
    """Run the agent in a thread and stream one server-sent event per tool call, then its answer."""
    from gridmend.agent.loop import MAX_STEPS, run_agent

    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            client, model = agent_client()
            answer = run_agent(
                client, model, tools,
                on_step=lambda name, args, result: events.put(
                    {"type": "tool", "name": name, "args": args, "result": result}),
                prompt=prompt,
                task=task,
                max_steps=max_steps or MAX_STEPS,
            )
            events.put({"type": "final", "text": answer, "model": model})
        except Exception as exc:
            events.put({"type": "error", "detail": str(exc)})
        finally:
            done()
            events.put(None)

    # A plain thread starts with an empty context; run it in the request's, to keep its feeder.
    threading.Thread(target=contextvars.copy_context().run, args=(work,), daemon=True).start()

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

    return {"graph": graph_name(), "steps": run_steps(live())}


@app.get("/dependencies")
def dependencies() -> dict:
    return {"lines": line_dependencies(live())}


@app.get("/risk")
def risk() -> dict:
    """Per faulted line: what stays dark after the agent's restoration, from the evaluation."""
    from eval.run import load_results

    rows = load_results()
    if rows is None:
        raise HTTPException(404, "no evaluation results yet; run `gridmend eval`")
    return {"lines": [
        {"line": r.fault, "unserved_kw": r.unserved_kw, "restored_kw": r.restored_kw,
         "critical_dark": [c for c in r.critical_dark.split(";") if c]}
        for r in rows if r.method == "agent"
    ]}


@app.get("/results")
def results() -> dict:
    from eval.run import load_results, summarize

    rows = load_results()
    if rows is None:
        raise HTTPException(404, "no evaluation results yet; run `gridmend eval`")
    return {"summary": summarize(rows), "rows": [asdict(r) for r in rows]}


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


@app.post("/reset")
def reset_grid() -> dict:
    reset(db.connect())
    return {"ok": True}
