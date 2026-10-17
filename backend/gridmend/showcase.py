"""The Cypher behind each FLISR step, run read-only against the live graph (or a throwaway copy
of it) for the UI."""
import time
from uuid import uuid4

from falkordb import Graph
from redis.exceptions import ResponseError

from gridmend.flisr.explain import FEEDING_PATH_QUERY
from gridmend.flisr.trace import TRACE_QUERY, open_incident
from gridmend.flisr.transfer import TRANSFER_QUERY, build_flow_network
from gridmend.flisr.verify import LOADING_QUERY, RADIALITY_QUERY, keep_conducting_edges
from gridmend.grid.dependency import DEPENDENCY_QUERY
from gridmend.grid.energize import ENERGIZED_QUERY
from gridmend.grid.zones import ZONE_SWITCHES_QUERY, ZONES_QUERY

MAX_ROWS = 12

FORK_SETUP = {"conducting": keep_conducting_edges, "flow": build_flow_network}

# (key, step, title, what it decides, query, needs: none | zone | path_zone | incident, or a
# fork setup in FORK_SETUP for queries that run on a throwaway copy of the live graph)
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
    ("indicators", "Locate", "What the agent reads",
     "Every switch with its state, fault indicator and the two zones it joins. The agent "
     "locates the fault from these indicators and designs isolation and restoration "
     "switching from them; nothing here decides for it.", ZONE_SWITCHES_QUERY, None),
    ("loading", "Verify", "Line loading",
     "In a radial feeder every bus has one path from the source, so a line carries the load "
     "of the buses whose path uses it. Run on each plan's fork; shown here on the live graph.",
     LOADING_QUERY, None),
    ("radiality", "Verify", "Radiality",
     "algo.MSF builds the spanning tree of the energized network on a copy that keeps only "
     "conducting edges. Any conducting edge the tree leaves out closes a loop.",
     RADIALITY_QUERY, "conducting"),
    ("transfer", "Explain", "Transfer limit",
     "algo.maxFlow pushes power from the substation to every healthy load, with each line "
     "capped at its rating and the faulted zone removed. It bounds what any switching plan "
     "can restore; lines at their rating are the bottleneck when load must stay dark.",
     TRANSFER_QUERY, "flow"),
    ("path", "Explain", "Feeding path",
     "The switches power crosses from the substation into a zone, used to explain each "
     "restoration.", FEEDING_PATH_QUERY, "path_zone"),
    ("trace", "Explain", "Decision trace",
     "Every plan the agent simulated, passed or rejected, is stored with its switch steps "
     "under the incident.",
     TRACE_QUERY, "incident"),
]


# What an empty result means at each step, so "no rows" reads as an answer.
WHEN_EMPTY = {
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


def run_on_fork(g: Graph, setup, query: str):
    # A unique name per call: the page can be loaded twice at once.
    fork = g.copy(f"showcase_{g.name}_{uuid4().hex[:8]}")
    try:
        setup(fork)
        start = time.perf_counter()
        result = fork.ro_query(query)
        return result, round(1000 * (time.perf_counter() - start), 1)
    finally:
        fork.delete()


def run_steps(g: Graph) -> list[dict]:
    incident = open_incident(g)
    available = {
        None: {},
        "path_zone": {"zone": path_zone(g)},
        "incident": {"id": incident["id"] if incident else None},
    }
    missing = {
        "path_zone": "needs an energized zone",
        "incident": "needs an incident: fault a line first",
    }

    steps = []
    for key, step, title, purpose, query, needs in STEPS:
        params = available.get(needs, {})
        entry = {"key": key, "step": step, "title": title, "purpose": purpose,
                 "cypher": query.strip(), "params": params, "when_empty": WHEN_EMPTY.get(key)}
        if needs in available and None in params.values():
            steps.append({**entry, "skipped": missing[needs]})
            continue
        if needs in FORK_SETUP:
            try:
                result, entry["ms"] = run_on_fork(g, FORK_SETUP[needs], query)
            except ResponseError as exc:
                steps.append({**entry, "skipped": f"FalkorDB could not run it here: {exc}"})
                continue
        else:
            start = time.perf_counter()
            result = g.ro_query(query, params)
            entry["ms"] = round(1000 * (time.perf_counter() - start), 1)
        entry["columns"] = [name for _, name in result.header]
        entry["rows"] = [[json_value(v) for v in row] for row in result.result_set[:MAX_ROWS]]
        entry["total_rows"] = len(result.result_set)
        steps.append(entry)
    return steps
