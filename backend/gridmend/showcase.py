"""The Cypher behind each FLISR step, run read-only against the live graph for the UI."""
import time

from falkordb import Graph

from gridmend.flisr.candidates import TIE_FEEDS_QUERY
from gridmend.flisr.explain import FEEDING_PATH_QUERY
from gridmend.flisr.isolate import BOUNDARY_QUERY, faulted_zone
from gridmend.flisr.locate import LOCATE_QUERY
from gridmend.flisr.trace import TRACE_QUERY, open_incident
from gridmend.flisr.verify import LOADING_QUERY
from gridmend.grid.dependency import DEPENDENCY_QUERY
from gridmend.grid.energize import ENERGIZED_QUERY
from gridmend.grid.zones import ZONES_QUERY

MAX_ROWS = 12

# (key, step, title, what it decides, query, parameter needed: none | zone | path_zone | incident)
STEPS = [
    ("zones", "Model", "Zones",
     "Zones are connected components over unswitched segments, found with algo.WCC. "
     "A zone is the smallest piece an operator can isolate.", ZONES_QUERY, None),
    ("energize", "Model", "Energization",
     "A bus is live when some path from the substation crosses only segments and closed "
     "switches.", ENERGIZED_QUERY, None),
    ("depends", "Assess", "Line dependencies",
     "What depends on each line right now: buses, load and critical facilities downstream. "
     "The agent can call this as a tool, and the Feeder tab shows it on hover.",
     DEPENDENCY_QUERY, None),
    ("locate", "Locate", "Fault location",
     "The faulted zone is the one whose upstream indicator is set while every downstream "
     "indicator is clear.", LOCATE_QUERY, None),
    ("isolate", "Isolate", "Isolation boundary",
     "Every closed switch with exactly one end in the faulted zone is opened.",
     BOUNDARY_QUERY, "zone"),
    ("options", "Plan", "Restoration options",
     "Dead healthy zones that a normally open tie connects to a live zone, heaviest first.",
     TIE_FEEDS_QUERY, None),
    ("loading", "Verify", "Line loading",
     "In a radial feeder every bus has one path from the source, so a line carries the load "
     "of the buses whose path uses it. Run on each plan's fork; shown here on the live graph.",
     LOADING_QUERY, None),
    ("path", "Explain", "Feeding path",
     "The switches power crosses from the substation into a zone, used to explain each "
     "restoration.", FEEDING_PATH_QUERY, "path_zone"),
    ("trace", "Explain", "Decision trace",
     "Every plan, accepted or rejected, is stored with its switch steps under the incident.",
     TRACE_QUERY, "incident"),
]


# What an empty result means at each step, so "no rows" reads as an answer.
WHEN_EMPTY = {
    "locate": "No zone matches: no indicator chain is set, so there is no fault to locate.",
    "isolate": "Nothing left to open: every boundary switch of the faulted zone is already open.",
    "options": "No dark healthy zone borders a live zone through a tie right now.",
    "path": "That zone is not fed from the substation.",
}


def json_value(value):
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, list):
        return [json_value(v) for v in value]
    return value


def path_zone(g: Graph) -> int | None:
    # Prefer the live zone with the most critical loads, then the furthest one.
    rows = g.ro_query(
        """
        MATCH (z:Zone {energized: true})
        OPTIONAL MATCH (b:Bus {critical: true})-[:IN_ZONE]->(z)
        WITH z, count(b) AS critical
        RETURN z.id ORDER BY critical DESC, z.id DESC LIMIT 1
        """
    ).result_set
    return rows[0][0] if rows else None


def run_steps(g: Graph) -> list[dict]:
    incident = open_incident(g)
    available = {
        None: {},
        "zone": {"zone": faulted_zone(g)},
        "path_zone": {"zone": path_zone(g)},
        "incident": {"id": incident["id"] if incident else None},
    }
    missing = {
        "zone": "needs a located fault: fault a line first",
        "path_zone": "needs an energized zone",
        "incident": "needs an incident: fault a line first",
    }

    steps = []
    for key, step, title, purpose, query, needs in STEPS:
        params = available[needs]
        entry = {"key": key, "step": step, "title": title, "purpose": purpose,
                 "cypher": query.strip(), "params": params, "when_empty": WHEN_EMPTY.get(key)}
        if needs and None in params.values():
            steps.append({**entry, "skipped": missing[needs]})
            continue
        start = time.perf_counter()
        result = g.ro_query(query, params)
        entry["ms"] = round(1000 * (time.perf_counter() - start), 1)
        entry["columns"] = [name for _, name in result.header]
        entry["rows"] = [[json_value(v) for v in row] for row in result.result_set[:MAX_ROWS]]
        entry["total_rows"] = len(result.result_set)
        steps.append(entry)
    return steps
