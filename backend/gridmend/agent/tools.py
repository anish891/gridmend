from collections.abc import Callable

from falkordb import FalkorDB, Graph

from gridmend.flisr.candidates import Candidate, generate_candidates
from gridmend.flisr.execute import ExecuteError, execute_plan, verified_plans
from gridmend.flisr.explain import explain_incident
from gridmend.flisr.isolate import IsolateError, isolate
from gridmend.flisr.locate import LocateError, locate_fault
from gridmend.flisr.trace import SwitchOp, open_incident
from gridmend.flisr.verify import next_plan_id, verify_plan
from gridmend.grid.energize import grid_status


def schema(name: str, description: str, properties: dict | None = None,
           required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
        },
    }


SWITCH_LIST = {"type": "array", "items": {"type": "string"}}

SCHEMAS = [
    schema("grid_status", "Energized, dark and faulted zones, switch states and load served."),
    schema("locate_fault", "Find the faulted zone from the fault indicator chain."),
    schema("isolation_plan", "Open the faulted zone's boundary switches and reclose the breaker. "
                             "Returns the switch operations performed."),
    schema("restoration_candidates", "Ranked candidate restoration plans built from graph searches "
                                     "between dead and live zones. Not yet verified."),
    schema(
        "verify_plan",
        "Verify one plan on a forked copy of the grid: isolation, connectivity, radiality, "
        "capacity and voltage. Pass a candidate rank, or explicit switches to close and open.",
        {"rank": {"type": "integer", "description": "rank from restoration_candidates"},
         "close": {**SWITCH_LIST, "description": "switch ids to close"},
         "open": {**SWITCH_LIST, "description": "switch ids to open"}},
    ),
    schema("execute_plan", "Execute a verified plan on the live grid after operator approval.",
           {"plan_id": {"type": "string"}}, ["plan_id"]),
    schema("explain_plan", "Explain the incident: feeding paths of restored zones, margins, "
                           "rejected alternatives and the state of every critical load."),
]


class GridTools:
    """The agent's only way to touch the grid. Every write is deterministic code."""

    def __init__(self, db: FalkorDB, g: Graph, approve: Callable[[dict], bool]):
        self.db = db
        self.g = g
        self.approve = approve
        self.candidates: list[Candidate] = []

    def call(self, name: str, args: dict) -> dict:
        handler = getattr(self, name, None)
        if name not in {s["function"]["name"] for s in SCHEMAS} or handler is None:
            return {"error": f"unknown tool {name}"}
        try:
            return handler(**args)
        except (LocateError, IsolateError, ExecuteError, ValueError, TypeError) as exc:
            return {"error": str(exc)}

    def incident_id(self) -> str:
        incident = open_incident(self.g)
        if incident is None:
            raise ValueError("no active incident")
        return incident["id"]

    def grid_status(self) -> dict:
        s = grid_status(self.g)
        return {
            "energized_zones": s["energized_zones"],
            "dark_zones": s["dark_zones"],
            "faulted_zones": s["faulted_zones"],
            "load_served_kw": round(s["served_kw"]),
            "total_load_kw": round(s["total_kw"]),
            "switches": {sw["id"]: f"{sw['kind']} {sw['status']}, fi {sw['fi']}"
                         for sw in s["switches"]},
        }

    def locate_fault(self) -> dict:
        location = locate_fault(self.g)
        return {"faulted_zone": location.zone, "indicator_chain": location.chain}

    def isolation_plan(self) -> dict:
        incident_id, ops = isolate(self.g)
        return {"incident": incident_id, "executed": [f"{o.op} {o.switch_id}" for o in ops]}

    def restoration_candidates(self) -> dict:
        self.incident_id()
        self.candidates = generate_candidates(self.g)[:5]
        return {"candidates": [
            {"rank": rank, "origin": c.origin, "ops": [f"{o.op} {o.switch_id}" for o in c.ops],
             "claims_zones": list(c.claims), "claimed_kw": round(c.claimed_kw),
             "score": round(c.score)}
            for rank, c in enumerate(self.candidates, 1)
        ]}

    def verify_plan(self, rank: int | None = None, close: list[str] | None = None,
                    open: list[str] | None = None) -> dict:
        incident_id = self.incident_id()
        if rank is not None:
            if not 1 <= rank <= len(self.candidates):
                raise ValueError(f"no candidate with rank {rank}; call restoration_candidates")
            candidate = self.candidates[rank - 1]
            ops, claims = list(candidate.ops), list(candidate.claims)
        else:
            ops = ([SwitchOp(s, "open") for s in open or []]
                   + [SwitchOp(s, "close") for s in close or []])
            claims = None
        if not ops:
            raise ValueError("pass a rank or switches to close/open")
        known = {row[0] for row in self.g.ro_query("MATCH ()-[s:SW]->() RETURN s.id").result_set}
        unknown = [o.switch_id for o in ops if o.switch_id not in known]
        if unknown:
            raise ValueError(f"unknown switches: {', '.join(unknown)}")

        result = verify_plan(self.db, self.g, incident_id,
                             next_plan_id(self.g, incident_id, "p"), ops, claims)
        critical = next((p["critical"] for p in verified_plans(self.g, incident_id)
                         if p["id"] == result.plan_id), [])
        return {
            "plan_id": result.plan_id,
            "passed": result.passed,
            "restores_zones": result.restored_zones,
            "drops_zones": result.dropped_zones,
            "restored_kw": round(result.restored_kw),
            "critical_loads_restored": critical,
            "switch_ops": len(ops),
            "min_voltage_pu": result.min_v_pu and round(result.min_v_pu, 3),
            "max_loading_pct": result.max_loading_pct and round(result.max_loading_pct),
            "violations": [f"{kind}: {detail}" for kind, detail in result.violations],
        }

    def execute_plan(self, plan_id: str) -> dict:
        plan = next((p for p in verified_plans(self.g, self.incident_id()) if p["id"] == plan_id),
                    None)
        if plan is None:
            raise ExecuteError(f"{plan_id} is not a verified plan of this incident")
        if not self.approve(plan):
            return {"executed": False, "reason": "operator declined"}
        ops = execute_plan(self.g, plan_id)
        return {"executed": True, "ops": [f"{op} {switch_id}" for switch_id, op in ops]}

    def explain_plan(self) -> dict:
        return explain_incident(self.g, self.incident_id())
