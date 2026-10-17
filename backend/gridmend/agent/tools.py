from collections.abc import Callable

from falkordb import FalkorDB, Graph

from gridmend.flisr.candidates import Candidate, generate_candidates
from gridmend.flisr.crew import crew_reports, jammed_switches
from gridmend.flisr.execute import ExecuteError, execute_plan, verified_plans
from gridmend.flisr.explain import explain_incident, feeding_path
from gridmend.flisr.isolate import IsolateError, isolate
from gridmend.flisr.locate import LocateError, locate_fault
from gridmend.flisr.policy import current_context, load_policy
from gridmend.flisr.transfer import transfer_limit
from gridmend.flisr.trace import SwitchOp, open_incident
from gridmend.flisr.verify import next_plan_id, verify_plan
from gridmend.flisr.whatif import WhatIfError, what_if
from gridmend.grid.dependency import line_dependencies
from gridmend.grid.energize import grid_status
from gridmend.sim import FaultError


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
    schema("operating_context", "Today's operating conditions as reported by the operator, the "
                                "priority weight of every facility for this incident with the "
                                "policy rule behind it, the full restoration priority policy, "
                                "and field crew reports with the switches they found jammed."),
    schema("grid_status", "Energized, dark and faulted zones, switch states and load served."),
    schema("locate_fault", "Find the faulted zones from the chains of tripped fault indicators. "
                           "Faults on separate branches are all found at once."),
    schema("line_dependencies", "For each energized line, the buses, load (kW) and critical "
                                "facilities that depend on it in the current switching, "
                                "heaviest first.",
           {"top": {"type": "integer", "description": "how many lines to return (default 8)"}}),
    schema("isolation_plan", "Open the faulted zones' boundary switches and reclose the breaker. "
                             "Returns the switch operations performed. If the breaker trips "
                             "again, a fault was hidden upstream: locate and isolate again."),
    schema("restoration_candidates", "Ranked candidate restoration plans built from graph searches "
                                     "between dead and live zones. Not yet verified."),
    schema("transfer_capacity", "Max-flow bound on the healthy load (kVA) the feeder could carry "
                                "if every switch outside the faulted zone were free to move, "
                                "and the lines that limit it. Explains what must stay dark."),
    schema(
        "verify_plan",
        "Verify one plan on a forked copy of the grid: isolation, connectivity, radiality, "
        "capacity and voltage. Pass a candidate rank, or explicit switches to close and open.",
        {"rank": {"type": "integer", "description": "rank from restoration_candidates"},
         "close": {**SWITCH_LIST, "description": "switch ids to close"},
         "open": {**SWITCH_LIST, "description": "switch ids to open"}},
    ),
    schema(
        "what_if",
        "Plan on throwaway copies of the grid as if these switches could not be operated, and "
        "compare with planning as things are: isolation, zones cut out with the fault, the best "
        "safe plan, kW and critical sites gained or lost. Nothing on the live grid changes. Give "
        "fault_line only when the feeder is healthy.",
        {"switches": {**SWITCH_LIST, "description": "switch ids that cannot be operated"},
         "fault_line": {"type": "string", "description": "segment to fault, only if healthy"}},
        ["switches"],
    ),
    schema("recommend_plan", "Record which verified plan you recommend and why. The reason is "
                             "stored with the plan in the decision trace.",
           {"plan_id": {"type": "string"}, "reason": {"type": "string"}}, ["plan_id", "reason"]),
    schema("execute_plan", "Execute a verified plan on the live grid after operator approval.",
           {"plan_id": {"type": "string"}}, ["plan_id"]),
    schema("fault_impact", "What a fault on an unswitched line segment would cost: load still "
                           "dark after the best safe restoration, critical sites lost and switch "
                           "moves, from the evaluation of every single fault. Omit line to get "
                           "every segment, worst first. Switches such as 1-2 cannot fault here.",
           {"line": {"type": "string", "description": "segment id, for example 4-5"}}),
    schema("feeding_path", "The switches power crosses from the substation to one bus right now, "
                           "and whether the bus has power. Give a bus number, or a critical "
                           "facility by name such as 'hospital'.",
           {"bus": {"type": "integer", "description": "bus number, 1 to 33"},
            "facility": {"type": "string", "description": "critical facility name"}}),
    schema("explain_plan", "Explain the incident: feeding paths of restored zones, margins, "
                           "rejected alternatives and the state of every critical load."),
]


# Tools that only read the grid or plan on throwaway copies, for answering questions.
READ_ONLY = frozenset({"operating_context", "grid_status", "line_dependencies", "transfer_capacity",
                       "what_if", "fault_impact", "feeding_path", "explain_plan"})


class GridTools:
    """The agent's only way to touch the grid. Every write is deterministic code."""

    def __init__(self, db: FalkorDB, g: Graph, approve: Callable[[dict], bool],
                 can_execute: bool = True, only: frozenset[str] | None = None):
        self.db = db
        self.g = g
        self.approve = approve
        self.candidates: list[Candidate] = []
        # An advisor never gets the execute tool: the operator approves and executes.
        self.schemas = [s for s in SCHEMAS
                        if (can_execute or s["function"]["name"] != "execute_plan")
                        and (only is None or s["function"]["name"] in only)]

    def call(self, name: str, args: dict) -> dict:
        handler = getattr(self, name, None)
        if name not in {s["function"]["name"] for s in self.schemas} or handler is None:
            return {"error": f"unknown tool {name}"}
        try:
            return handler(**args)
        except (LocateError, IsolateError, ExecuteError, WhatIfError, FaultError, ValueError,
                TypeError) as exc:
            return {"error": str(exc)}

    def incident_id(self) -> str:
        incident = open_incident(self.g)
        if incident is None:
            raise ValueError("no active incident")
        return incident["id"]

    def operating_context(self) -> dict:
        context = current_context(self.g)
        return {
            "conditions": context["conditions"] or "normal day: no special conditions reported",
            "priorities": [{k: p[k] for k in ("facility", "zone", "weight", "rule")}
                           for p in context["priorities"]],
            "policy": load_policy(),
            "jammed_switches": jammed_switches(self.g),
            "crew_reports": [{"id": r["id"], "note": r["note"]} for r in crew_reports(self.g)],
        }

    def grid_status(self) -> dict:
        s = grid_status(self.g)
        return {
            "energized_zones": s["energized_zones"],
            "dark_zones": s["dark_zones"],
            "faulted_zones": s["faulted_zones"],
            "load_served_kw": round(s["served_kw"]),
            "total_load_kw": round(s["total_kw"]),
            "switches": {sw["id"]: f"{sw['kind']} {sw['status']}, fi {sw['fi']}"
                                   + ("" if sw["operable"] else ", JAMMED")
                         for sw in s["switches"]},
        }

    def line_dependencies(self, top: int | None = None) -> dict:
        lines = line_dependencies(self.g)[: top or 8]
        return {"lines": [{**d, "kw": round(d["kw"])} for d in lines]}

    def locate_fault(self) -> dict:
        location = locate_fault(self.g)
        return {"faulted_zone": location.zone, "indicator_chain": location.chain,
                "faulted_zones": location.zones,
                "indicator_chains": {f"Z{z}": chain for z, chain in location.chains.items()}}

    def isolation_plan(self) -> dict:
        incident_id, ops = isolate(self.g)
        cut_off = self.g.ro_query(
            "MATCH (:Incident {id: $id})-[c:CUTS_OFF]->(z:Zone) RETURN z.id, c.via ORDER BY z.id",
            {"id": incident_id},
        ).result_set
        result = {"incident": incident_id, "executed": [f"{o.op} {o.switch_id}" for o in ops],
                  "cut_off_with_fault": [{"zone": z, "jammed_switch": via} for z, via in cut_off]}
        if open_incident(self.g)["status"] == "tripped":
            result["breaker_tripped_again"] = (
                "Reclosing fed another fault hidden upstream, so the breaker tripped again. "
                "Call locate_fault, then isolation_plan, before planning restoration.")
        return result

    def restoration_candidates(self) -> dict:
        self.incident_id()
        self.candidates = generate_candidates(self.g)[:5]
        return {"candidates": [
            {"rank": rank, "origin": c.origin, "ops": [f"{o.op} {o.switch_id}" for o in c.ops],
             "claims_zones": list(c.claims), "claimed_kw": round(c.claimed_kw),
             "score": round(c.score)}
            for rank, c in enumerate(self.candidates, 1)
        ]}

    def transfer_capacity(self) -> dict:
        return transfer_limit(self.g)

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

    def what_if(self, switches: list[str], fault_line: str | None = None) -> dict:
        return what_if(self.db, self.g, switches, fault_line)

    def recommend_plan(self, plan_id: str, reason: str) -> dict:
        incident_id = self.incident_id()
        plan = next((p for p in verified_plans(self.g, incident_id) if p["id"] == plan_id), None)
        if plan is None:
            raise ValueError(f"{plan_id} is not a verified plan of this incident")
        self.g.query(
            """
            MATCH (:Incident {id: $incident})-[:HAS_PLAN]->(p:Plan)
            SET p.recommended = (p.id = $id),
                p.recommendation = CASE WHEN p.id = $id THEN $reason ELSE null END
            """,
            {"incident": incident_id, "id": plan_id, "reason": reason},
        )
        return {"recommended": plan_id, "critical_loads_restored": plan["critical"],
                "priority_restored": plan["priority"], "restored_kw": round(plan["restored_kw"])}

    def execute_plan(self, plan_id: str) -> dict:
        plan = next((p for p in verified_plans(self.g, self.incident_id()) if p["id"] == plan_id),
                    None)
        if plan is None:
            raise ExecuteError(f"{plan_id} is not a verified plan of this incident")
        if not self.approve(plan):
            return {"executed": False, "reason": "operator declined"}
        ops = execute_plan(self.g, plan_id)
        return {"executed": True, "ops": [f"{op} {switch_id}" for switch_id, op in ops]}

    def fault_impact(self, line: str | None = None) -> dict:
        from eval.run import load_results

        rows = {r.fault: r for r in load_results() or [] if r.method == "gridmend"}
        if not rows:
            raise ValueError("no evaluation results on this server")

        def impact(r) -> dict:
            return {"line": r.fault, "restored_kw": round(r.restored_kw),
                    "still_dark_kw": round(r.unserved_kw),
                    "critical_dark": [c for c in r.critical_dark.split(";") if c],
                    "switch_moves": r.switch_ops}

        if not line:
            ranked = sorted(rows.values(), key=lambda r: r.unserved_kw, reverse=True)
            return {"segments": [impact(r) for r in ranked]}
        if line.strip() not in rows:
            raise ValueError(f"{line} is not an unswitched segment; segments are {', '.join(rows)}")
        return impact(rows[line.strip()])

    def feeding_path(self, bus: int | None = None, facility: str | None = None) -> dict:
        if bus is None and facility:
            rows = self.g.ro_query(
                "MATCH (b:Bus) WHERE toLower(b.facility) = toLower($name) RETURN b.id",
                {"name": facility.strip()},
            ).result_set
            if not rows:
                raise ValueError(f"no facility named {facility}")
            bus = rows[0][0]
        if bus is None:
            raise ValueError("give a bus number or a facility name")
        rows = self.g.ro_query(
            "MATCH (b:Bus {id: $id}) RETURN b.energized, b.zone, b.facility", {"id": bus}
        ).result_set
        if not rows:
            raise ValueError(f"no bus {bus}")
        energized, zone, name = rows[0]
        return {"bus": bus, "facility": name, "zone": zone, "energized": energized,
                "switches": feeding_path(self.g, bus) if energized else []}

    def explain_plan(self) -> dict:
        return explain_incident(self.g, self.incident_id())
