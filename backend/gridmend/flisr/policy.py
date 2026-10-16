from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import openai
from falkordb import Graph

from gridmend.agent.llm import parse_json_object
from gridmend.flisr.trace import open_incident
from gridmend.grid.load import load_assumptions
from gridmend.grid.zones import refresh_zone_weights

if TYPE_CHECKING:
    from openai import OpenAI

POLICY_PATH = Path(__file__).resolve().parents[2] / "data" / "policy.md"
MIN_WEIGHT, MAX_WEIGHT = 1, 20
STANDING_RULE = "standing priority (policy 2)"


class PolicyError(Exception):
    pass


class NotHealthyError(PolicyError):
    pass


class ModelError(PolicyError):
    pass


@dataclass(frozen=True)
class Priority:
    facility: str
    weight: int
    rule: str


def load_policy() -> str:
    return POLICY_PATH.read_text()


def facilities(g: Graph) -> list[dict]:
    rows = g.ro_query(
        "MATCH (b:Bus) WHERE b.facility <> '' RETURN b.facility, b.id, b.zone ORDER BY b.id"
    ).result_set
    return [{"facility": f, "bus": bus, "zone": zone} for f, bus, zone in rows]


def standing_weight(facility: str) -> int:
    assumptions = load_assumptions()
    weights = assumptions["weights"]
    return weights["critical"] if facility in assumptions["critical_loads"].values() else weights["default"]


PROMPT = """You apply a utility's service restoration priority policy to today's conditions.

POLICY
{policy}

FACILITIES ON THE FEEDER
{facilities}

OPERATOR'S REPORT OF TODAY'S CONDITIONS
{conditions}

Give every facility listed above its priority weight for this incident, following the policy
exactly. Only apply a condition the report states. Cite the rule numbers you applied.
Reply with only a JSON object:
{{"priorities": [{{"facility": "<name exactly as listed>", "weight": <whole number {low}-{high}>,
"rule": "<rule numbers and a short reason>"}}]}}"""


def ask_priorities(client: "OpenAI", model: str, sites: list[dict], conditions: str) -> dict:
    listing = "\n".join(f"- {s['facility']} (bus {s['bus']}, zone Z{s['zone']})" for s in sites)
    reply = client.chat.completions.create(
        model=model,
        temperature=0,
        # Reasoning models spend most of their budget thinking before the JSON appears.
        max_tokens=8000,
        messages=[{"role": "user", "content": PROMPT.format(
            policy=load_policy(), facilities=listing, conditions=conditions,
            low=MIN_WEIGHT, high=MAX_WEIGHT)}],
    )
    return parse_json_object(reply.choices[0].message.content)


def validate(reply: dict, names: list[str]) -> list[Priority]:
    """The LLM only proposes; nothing reaches the graph unless every entry checks out.
    Facilities it leaves out keep their standing priority (policy 3.5)."""
    entries = reply.get("priorities")
    if not isinstance(entries, list):
        raise PolicyError("the reply has no list of priorities")
    chosen: dict[str, Priority] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PolicyError(f"not a priority entry: {entry!r}")
        facility, weight, rule = entry.get("facility"), entry.get("weight"), entry.get("rule")
        if facility not in names:
            raise PolicyError(f"unknown facility {facility!r}")
        if facility in chosen:
            raise PolicyError(f"{facility} is given twice")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight != int(weight):
            raise PolicyError(f"{facility}: weight {weight!r} is not a whole number")
        if not MIN_WEIGHT <= weight <= MAX_WEIGHT:
            raise PolicyError(f"{facility}: weight {weight} is outside {MIN_WEIGHT}-{MAX_WEIGHT}")
        if not isinstance(rule, str) or not rule.strip():
            raise PolicyError(f"{facility}: no policy rule cited")
        chosen[facility] = Priority(facility, int(weight), rule.strip()[:300])
    return [chosen.get(name) or Priority(name, standing_weight(name), STANDING_RULE) for name in names]


def is_healthy(g: Graph) -> bool:
    faulted = g.ro_query("MATCH ()-[s:SEG {faulted: true}]->() RETURN count(s)").result_set[0][0]
    return open_incident(g) is None and faulted == 0


def require_healthy(g: Graph) -> None:
    # Priorities are fixed for the life of an incident so its decision trace stays consistent.
    if not is_healthy(g):
        raise NotHealthyError("conditions can only change while the feeder is healthy; reset it first")


def write_priorities(g: Graph, priorities: list[Priority], conditions: str | None,
                     model: str | None) -> None:
    rows = [{"facility": p.facility, "weight": p.weight, "rule": p.rule} for p in priorities]
    # A facility above the ordinary weight is a critical site for this incident (policy 1.2).
    g.query(
        """
        UNWIND $rows AS r
        MATCH (b:Bus {facility: r.facility})
        SET b.weight = r.weight, b.critical = r.weight > 1
        """,
        {"rows": rows},
    )
    refresh_zone_weights(g)
    g.query("MATCH (c:Context {current: true}) SET c.current = false")
    if conditions is None:
        return
    g.query(
        """
        CREATE (c:Context {current: true, conditions: $conditions, model: $model, ts: $ts})
        WITH c
        UNWIND $rows AS r
        MATCH (b:Bus {facility: r.facility})
        CREATE (c)-[:SETS]->(:Priority {facility: r.facility, weight: r.weight, rule: r.rule})
               -[:APPLIES_TO]->(b)
        """,
        {"rows": rows, "conditions": conditions, "model": model,
         "ts": datetime.now(timezone.utc).isoformat()},
    )


def set_conditions(g: Graph, client: "OpenAI", model: str, conditions: str) -> dict:
    """Turn the operator's report and the written policy into this incident's priorities."""
    conditions = conditions.strip()
    if not conditions:
        raise PolicyError("describe today's conditions")
    require_healthy(g)
    sites = facilities(g)
    try:
        reply = ask_priorities(client, model, sites, conditions)
    except openai.APIError as exc:
        raise ModelError(f"the model could not answer: {exc.message}") from exc
    except ValueError as exc:
        raise PolicyError(f"the model's reply was not valid JSON: {exc}") from exc
    priorities = validate(reply, [s["facility"] for s in sites])
    write_priorities(g, priorities, conditions, model)
    return current_context(g)


def clear_conditions(g: Graph) -> dict:
    require_healthy(g)
    standing = [Priority(s["facility"], standing_weight(s["facility"]), STANDING_RULE)
                for s in facilities(g)]
    write_priorities(g, standing, None, None)
    return current_context(g)


def current_context(g: Graph) -> dict:
    """The conditions in force and every facility's priority, with the rule behind it."""
    context = g.ro_query(
        "MATCH (c:Context {current: true}) RETURN c.conditions, c.model, c.ts"
    ).result_set
    rules = dict(g.ro_query(
        "MATCH (:Context {current: true})-[:SETS]->(p:Priority) RETURN p.facility, p.rule"
    ).result_set)
    rows = g.ro_query(
        "MATCH (b:Bus) WHERE b.facility <> '' RETURN b.facility, b.id, b.zone, b.weight ORDER BY b.id"
    ).result_set
    conditions, model, ts = context[0] if context else (None, None, None)
    return {
        "conditions": conditions,
        "model": model,
        "ts": ts,
        "priorities": [
            {"facility": f, "bus": bus, "zone": zone, "weight": weight,
             "standing": standing_weight(f), "rule": rules.get(f, STANDING_RULE)}
            for f, bus, zone, weight in rows
        ],
    }


def incident_context(g: Graph, incident_id: str) -> dict | None:
    """The conditions an incident was handled under, read from its decision trace."""
    rows = g.ro_query(
        """
        MATCH (:Incident {id: $id})-[:UNDER]->(c:Context)-[:SETS]->(p:Priority)
        RETURN c.conditions, p.facility, p.weight, p.rule ORDER BY p.weight DESC, p.facility
        """,
        {"id": incident_id},
    ).result_set
    if not rows:
        return None
    return {
        "conditions": rows[0][0],
        "priorities": [{"facility": f, "weight": w, "standing": standing_weight(f), "rule": r}
                       for _, f, w, r in rows],
    }
