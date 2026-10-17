from collections.abc import Callable

from falkordb import FalkorDB, Graph
from redis.exceptions import ResponseError

from gridmend.flisr.crew import crew_reports, jammed_switches
from gridmend.flisr.execute import ExecuteError, execute_plan, verified_plans
from gridmend.flisr.explain import explain_incident, feeding_path
from gridmend.flisr.generation import generation_status
from gridmend.flisr.locate import LocateError, declare_fault
from gridmend.flisr.transfer import transfer_limit
from gridmend.flisr.trace import SwitchOp, open_incident
from gridmend.flisr.verify import next_plan_id, verify_plan
from gridmend.grid.dependency import line_dependencies
from gridmend.grid.energize import grid_status
from gridmend.grid.zones import zone_map
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
PLAN_ID = {"plan_id": {"type": "string"}}

SCHEMAS = [
    schema("field_reports", "Field crew reports and the switches they found jammed."),
    schema("grid_status", "Energized, dark and faulted zones, switch states, load served, and "
                          "distributed generation with whether each unit is online."),
    schema("zone_map", "The feeder as you switch it: every zone with its state (live, dark, "
                       "faulted, cut off with the fault), load in kW and kVA and critical sites, "
                       "and every switch with its kind, status, rating in kVA, fault indicator "
                       "(set means fault current passed through it) and the two zones it joins, "
                       "the zone nearer the substation first."),
    schema("line_dependencies", "For each energized line, the buses, load (kW) and critical "
                                "facilities that depend on it in the current switching, "
                                "heaviest first.",
           {"top": {"type": "integer", "description": "how many lines to return (default 8)"}}),
    schema("transfer_capacity", "Max-flow bound on the healthy load (kVA) the feeder could carry "
                                "if every switch outside the faulted zone were free to move, "
                                "and the lines that limit it."),
    schema("declare_fault", "Record the zones you conclude are faulted and the indicator evidence "
                            "you read. Required before an isolation plan can be simulated.",
           {"zones": {"type": "array", "items": {"type": "integer"},
                      "description": "faulted zone numbers, e.g. [3] for Z3"},
            "evidence": {"type": "string", "description": "the indicator readings behind it"}},
           ["zones", "evidence"]),
    schema("simulate_plan",
           "Apply switching to a forked copy of the grid and check it: faulted zones stay dark, "
           "claimed zones get power, no loops, no line over its rating, every live bus at or "
           "above the voltage limit. Failed checks come back as violations. Only plans that "
           "pass can be executed.",
           {"purpose": {"type": "string", "enum": ["isolation", "restoration"]},
            "close": {**SWITCH_LIST, "description": "switch ids to close"},
            "open": {**SWITCH_LIST, "description": "switch ids to open"}},
           ["purpose"]),
    schema("execute_isolation", "Carry out an isolation plan that passed simulation. If power "
                                "reaches a fault, the breaker trips again.", PLAN_ID, ["plan_id"]),
    schema("execute_restoration", "Carry out a restoration plan that passed simulation, after "
                                  "operator approval.", PLAN_ID, ["plan_id"]),
    schema("recommend_plan", "Record which passed restoration plan you recommend and why. The "
                             "reason is stored with the plan in the decision trace.",
           {"plan_id": {"type": "string"}, "reason": {"type": "string"}}, ["plan_id", "reason"]),
    schema("fault_impact", "What a fault on an unswitched line segment would cost: load still "
                           "dark after the agent's restoration and critical sites lost, from the "
                           "evaluation of every single fault. Omit line to get every segment, "
                           "worst first.",
           {"line": {"type": "string", "description": "segment id, for example 4-5"}}),
    schema("feeding_path", "The switches power crosses from the substation to one bus right now, "
                           "and whether the bus has power. Give a bus number, or a critical "
                           "facility by name such as 'hospital'.",
           {"bus": {"type": "integer", "description": "bus number, 1 to 33"},
            "facility": {"type": "string", "description": "critical facility name"}}),
    schema("explain_plan", "Explain the incident: feeding paths of restored zones, margins, "
                           "rejected alternatives and the state of every critical load."),
]


# Tools that only read the grid, for answering questions.
READ_ONLY = frozenset({"field_reports", "grid_status", "zone_map", "line_dependencies",
                       "transfer_capacity", "fault_impact", "feeding_path", "explain_plan"})


class GridTools:
    """The agent's only way to touch the grid. It decides; these tools read, simulate on forks,
    and move live switches only for plans that passed simulation."""

    def __init__(self, db: FalkorDB, g: Graph, approve: Callable[[dict], bool],
                 can_execute: bool = True, only: frozenset[str] | None = None):
        self.db = db
        self.g = g
        self.approve = approve
        self.declined = False
        # An advisor isolates on its own but never restores: the operator approves and executes.
        self.schemas = [s for s in SCHEMAS
                        if (can_execute or s["function"]["name"] != "execute_restoration")
                        and (only is None or s["function"]["name"] in only)]

    def call(self, name: str, args: dict) -> dict:
        handler = getattr(self, name, None)
        schema = next((s["function"] for s in self.schemas if s["function"]["name"] == name), None)
        if schema is None or handler is None:
            return {"error": f"unknown tool {name}"}
        # Models sometimes add arguments a tool does not take; ignore them rather than fail.
        args = {k: v for k, v in args.items() if k in schema["parameters"]["properties"]}
        try:
            return handler(**args)
        except (LocateError, ExecuteError, FaultError, ValueError, TypeError) as exc:
            return {"error": str(exc)}
        except ResponseError as exc:
            # A query FalkorDB could not run is reported to the agent, which can work around it.
            return {"error": f"the graph database could not answer: {exc}"}

    def incident(self) -> dict:
        incident = open_incident(self.g)
        if incident is None:
            raise ValueError("no incident yet: read zone_map and declare_fault first")
        return incident

    def unfinished(self) -> str | None:
        """A reminder for an agent that stops before the incident is handled."""
        offered = {s["function"]["name"] for s in self.schemas}
        if "execute_isolation" not in offered or self.declined:
            return None
        incident = open_incident(self.g)
        if incident is None:
            tripped = self.g.ro_query(
                "MATCH ()-[s:SW {kind: 'breaker', status: 'open', normally: 'closed'}]->() "
                "RETURN count(s)"
            ).result_set[0][0]
            return ("The breaker is still tripped. Read zone_map, declare_fault, then isolate."
                    if tripped else None)
        if incident["status"] in ("tripped", "located"):
            return ("The faulted zones are not isolated yet. Declare the fault from the "
                    "indicators, then simulate and execute an isolation plan.")
        if incident["status"] != "isolated":
            return None
        if not verified_plans(self.g, incident["id"]):
            tried = self.g.ro_query(
                "MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {kind: 'restoration'}) "
                "RETURN count(p)",
                {"id": incident["id"]},
            ).result_set[0][0]
            dark = self.g.ro_query(
                "MATCH (z:Zone {energized: false, faulted: false}) "
                "WHERE NOT coalesce(z.cut_off, false) RETURN count(z)"
            ).result_set[0][0]
            return ("Healthy zones are still dark and no restoration plan was simulated. Design "
                    "one from zone_map and simulate it." if dark and not tried else None)
        if "execute_restoration" in offered:
            return ("Restoration plans have passed simulation but none was executed. Choose one, "
                    "execute it and explain it.")
        recommended = self.g.ro_query(
            "MATCH (:Incident {id: $id})-[:HAS_PLAN]->(p:Plan {recommended: true}) RETURN count(p)",
            {"id": incident["id"]},
        ).result_set[0][0]
        if not recommended:
            return ("Restoration plans have passed simulation but none was recommended. Choose "
                    "one and call recommend_plan.")
        return None

    def field_reports(self) -> dict:
        return {
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
            # Online generation offsets load on its lateral; it trips whenever its bus loses power.
            "generation": generation_status(self.g),
        }

    def zone_map(self) -> dict:
        return zone_map(self.g)

    def line_dependencies(self, top: int | None = None) -> dict:
        lines = line_dependencies(self.g)[: top or 8]
        return {"lines": [{**d, "kw": round(d["kw"])} for d in lines]}

    def transfer_capacity(self) -> dict:
        limit = transfer_limit(self.g)
        if limit is None:
            raise ValueError("the max-flow bound could not be computed for this configuration")
        return limit

    def declare_fault(self, zones: list, evidence: str) -> dict:
        numbers = [int(str(z).upper().lstrip("Z")) for z in zones]
        incident_id = declare_fault(self.g, numbers, evidence)
        return {"incident": incident_id, "faulted_zones": numbers}

    def simulate_plan(self, purpose: str, close: list[str] | None = None,
                      open: list[str] | None = None) -> dict:
        incident = self.incident()
        needs = {"isolation": "located", "restoration": "isolated"}
        if purpose not in needs:
            raise ValueError("purpose is isolation or restoration")
        if incident["status"] != needs[purpose]:
            raise ValueError(f"{incident['id']} is {incident['status']}; {purpose} plans are "
                             f"simulated once it is {needs[purpose]}")
        ops = ([SwitchOp(s, "open") for s in open or []]
               + [SwitchOp(s, "close") for s in close or []])
        if not ops:
            raise ValueError("give switches to close and/or open")
        known = {row[0] for row in self.g.ro_query("MATCH ()-[s:SW]->() RETURN s.id").result_set}
        unknown = [o.switch_id for o in ops if o.switch_id not in known]
        if unknown:
            raise ValueError(f"unknown switches: {', '.join(unknown)}")

        prefix = "iso" if purpose == "isolation" else "p"
        result = verify_plan(self.db, self.g, incident["id"],
                             next_plan_id(self.g, incident["id"], prefix), ops,
                             origin="agent", kind=purpose)
        critical = next((p["critical"] for p in verified_plans(self.g, incident["id"], purpose)
                         if p["id"] == result.plan_id), [])
        return {
            "plan_id": result.plan_id,
            "purpose": purpose,
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

    def passed_plan(self, plan_id: str, kind: str) -> dict:
        plan = next((p for p in verified_plans(self.g, self.incident()["id"], kind)
                     if p["id"] == plan_id), None)
        if plan is None:
            raise ExecuteError(f"{plan_id} is not a {kind} plan of this incident that passed "
                               "simulation")
        return plan

    def execute_isolation(self, plan_id: str) -> dict:
        self.passed_plan(plan_id, "isolation")
        ops, tripped = execute_plan(self.g, plan_id)
        result = {"executed": [f"{op} {switch_id}" for switch_id, op in ops]}
        if tripped:
            result["breaker_tripped_again"] = (
                "Power reached a fault, so the breaker tripped again: either the declared zone "
                "was wrong or a second fault was hidden upstream. Read the indicators in "
                "zone_map again, declare_fault, and isolate again.")
        else:
            result["cut_off_with_fault"] = [
                {"zone": z, "jammed_switch": via} for z, via in self.g.ro_query(
                    "MATCH (:Incident {id: $id})-[c:CUTS_OFF]->(z:Zone) "
                    "RETURN z.id, c.via ORDER BY z.id",
                    {"id": self.incident()["id"]},
                ).result_set]
        return result

    def execute_restoration(self, plan_id: str) -> dict:
        plan = self.passed_plan(plan_id, "restoration")
        if not self.approve(plan):
            self.declined = True
            return {"executed": False, "reason": "operator declined"}
        ops, tripped = execute_plan(self.g, plan_id)
        result = {"executed": [f"{op} {switch_id}" for switch_id, op in ops]}
        if tripped:
            result["breaker_tripped_again"] = "Power reached a fault, so the breaker tripped again."
        return result

    def recommend_plan(self, plan_id: str, reason: str) -> dict:
        plan = self.passed_plan(plan_id, "restoration")
        self.g.query(
            """
            MATCH (:Incident {id: $incident})-[:HAS_PLAN]->(p:Plan)
            SET p.recommended = (p.id = $id),
                p.recommendation = CASE WHEN p.id = $id THEN $reason ELSE null END
            """,
            {"incident": self.incident()["id"], "id": plan_id, "reason": reason},
        )
        return {"recommended": plan_id, "critical_loads_restored": plan["critical"],
                "restored_kw": round(plan["restored_kw"])}

    def fault_impact(self, line: str | None = None) -> dict:
        from eval.run import load_results

        rows = {r.fault: r for r in load_results() or [] if r.method == "agent"}
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
        return explain_incident(self.g, self.incident()["id"])
