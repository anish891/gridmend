# GridMend: a self-healing grid agent on a FalkorDB twin

> **100% of the best possible restoration, with 0 unsafe plans, across all 23 single faults.**
> A language model planning the same faults alone, without the graph and the verifier, produced
> unsafe switching in 20 of 23. GridMend plans from the graph, checks every plan on a forked copy
> of it, and only then acts.

GridMend restores power after a fault on a distribution feeder. It locates the fault from
fault-indicator readings, isolates it, searches the graph for ways to re-feed the healthy
areas, checks every candidate on a forked copy of the graph, and only then lets an operator
(or an LLM operator agent) execute a plan. Every decision is written back into the graph, so
any restoration can be explained afterwards, path by path.

It also acts on what people tell it in plain words. An operator describes the day ("heat
advisory, the cooling centre is open") and the LLM applies the utility's written restoration
policy to it. A field crew reports a jammed switch, and GridMend isolates and replans around it.
An operator asks "what if 6-7 won't open?" and gets the answer from throwaway copies of the
graph. The LLM only proposes; deterministic code checks every value before it reaches the graph.

And it answers questions. **Ask the grid** takes "which line is most critical right now?" or "how
is the hospital fed?", lets the LLM chain read-only graph tools to find out, answers with the
evidence and the tools it used, and highlights the path or lines it relied on on the map.

**Track:** 01, Agents That Act on Connected Data.

**Try it:**

* **Run the published image** (full speed, needs only Docker), then open http://localhost:7860:

  ```bash
  docker run --rm -p 7860:7860 ghcr.io/anish891/gridmend
  ```

* **Live demo:** https://gridmend.onrender.com. It runs on Render's free tier (0.1 CPU) and sleeps
  when idle, so the first visit takes about half a minute to wake it. After that a fault is
  planned and verified in a few seconds; the LLM agent mode takes about a minute.

Press **Play the demo** for a 30-second guided run of the main-line fault, or pick today's
conditions, then a demo fault (or click any line). Watch the indicators, isolation
and switching replay on the map, select a plan to see it (or why it failed) on the map, and
approve one that passed. File a crew note such as "the sectionalizer between 6 and 7 is jammed
shut" and fault 4-5 again to see the hospital's zone cut out with the fault. Ask **What if a
switch won't operate?** to compare outcomes without touching the live grid. Switch the map to
**Risk map** to see every line coloured by what a fault there would leave dark. The **Graph** tab
shows the live Cypher behind every step and what it returns right now. Ask the grid a question
in plain words below the map.

![GridMend after a fault on 4-5: hospital and fire station re-fed through ties 8-21 and 9-15](docs/feeder.png)

## The problem

A feeder carries power from a substation to homes, hospitals and businesses. When a line
faults, the breaker at the substation trips and everyone downstream goes dark, not only the
customers near the fault. Operators must then:

1. find which section faulted,
2. cut it out by opening switches,
3. re-feed the healthy sections through normally open tie switches,

without creating loops, overloading lines or letting voltage sag below safe limits. Utilities
call this FLISR (fault location, isolation and service restoration). It normally lives inside
expensive distribution-management systems, and its decisions are hard to inspect. The rules
that decide who gets power first live in policy documents, and the state of the field arrives
as crew notes, not as clean data.

## What GridMend does

| Step | What happens | Where the graph does the work |
|---|---|---|
| Locate | Follow the chain of tripped fault indicators from the substation | Zone whose upstream indicator is set and downstream ones are clear |
| Isolate | Open the faulted zone's boundary switches, reclose the breaker; a zone tied to the fault by a jammed switch is cut out with it | Switches with exactly one end in the isolated region; traversal across jammed closed switches |
| Plan | Build candidate restoration plans from the switches that can still operate | What-if energization, radiality and line loading for each tie subset |
| Verify | Apply each candidate to `GRAPH.COPY` of the live graph and check operability, isolation, connectivity, radiality, capacity and voltage | A fork per plan; power flow on the forked configuration |
| Decide | Rank verified plans by the total priority of the critical sites they restore (from the policy in force), then load restored, then fewest operations | `RESTORES` edges from each plan to zones, `Priority` weights on buses |
| Act | Execute the approved plan on the live graph | Only plans whose status is `verified` |
| Explain | Feeding path of every restored zone, margins, rejected alternatives, why each critical site is lit or dark, the conditions and crew reports in force, and a numbered switching order | Stored decision trace |

The LLM never writes Cypher and never flips a switch. As the operator agent it calls fourteen
tools, chooses among plans that already passed verification, and explains the choice using only
facts the tools returned. As a reader it turns the written policy and crew notes into structured
values that code validates before writing. All writes to the graph are deterministic code.

### Example: fault on line 4-5 (zone Z3)

* The breaker trips and all 33 buses go dark. Indicators on 1-2 and 2-3 read set, all
  others clear, so the fault is in Z3.
* Isolation opens 2-3, 3-23, 6-7 and 6-26 and recloses 1-2: Z2 and Z19 come back.
* Candidates, each verified on its own fork:
  * close 8-21 alone: rejected, line 2-19 would carry 1584 kVA of 1400 (113%);
  * close 8-21 and 12-22: rejected, closes a loop;
  * open 9-10 and 13-14, close 8-21 and 9-15: **passes**, restores the hospital (Z7) and the
    fire station (Z14) at 95% loading and 0.946 pu;
  * open 13-14, close 12-22: passes, but restores only the hospital.
* The plan with both critical sites is executed. Water pumping stays dark (no tie reaches a
  live zone) and so does telecom (closing 18-33 from Z14 would overload the path through 2-19).

The same fault under different conditions:

* **Heat advisory.** Policy rule 3.1 raises the open cooling centre (bus 11, Z10) from 1 to 10;
  rule 3.2 lowers the fire station, which has backup generators, from 10 to 6. The plan that
  restores the hospital and the cooling centre (open 13-14, close 12-22) now ranks first.
* **Switch 6-7 reported jammed shut.** Isolation cannot open 6-7, so Z7 (the hospital) is cut out
  with the fault and 9-10 opens instead. The ties into Z7 are never planned. The best safe plan
  closes 12-22 and restores the fire station's zone: 615 kW instead of 850, which the what-if
  shows before the fault ever happens.

## What the LLM does, and what it does not

The LLM is the operator's assistant, not the controller. It has three jobs, and each one ends
at a deterministic check.

* **Operator agent.** It decides which tools to call and in what order: the conditions in force,
  locate, isolate, candidates, which candidates to verify, what-ifs. It chooses among verified
  plans following the policy and the day's conditions (crew arrival, weather) and justifies the
  choice citing the policy's rule numbers. `recommend_plan` refuses a plan that did not pass
  verification, and the choice and reason are written into the decision trace.
* **Policy reader.** From the operator's description of the day and the written policy
  ([policy.md](backend/data/policy.md)), it proposes a priority weight for every site with the
  rule it applied. Code accepts it only if every site is known, every weight is a whole number
  from 1 to 20 and every entry cites a rule; otherwise nothing changes. Conditions can only
  change while the feeder is healthy, so an incident is handled under one set of priorities.
* **Crew-note reader.** From a crew's free-text note it lists the switches the note says are
  jammed or repaired, with the evidence. Code accepts a finding only if the switch exists, the
  evidence is quoted from the note itself, and any position the note states matches the twin.
  A note that says a closed switch is "stuck open" is refused until the operator confirms where
  it really is.

It never writes Cypher, never flips a switch, never decides whether a plan is safe (the fork
verifier does), and in the web UI it never executes: the operator does. Without an LLM, the same
pipeline runs with a deterministic ranking on standing priorities, which is what the evaluation's
`gridmend` row uses. The evaluation's `llm-only` row shows what happens when a model plans
without the graph and the verifier: 20 of 23 plans unsafe.

## Why FalkorDB is load-bearing

Remove FalkorDB and nothing is left: no twin, no forks, no paths, no trace.

* **Zones** are connected components over unswitched segments (`algo.WCC` filtered to `SEG`).
* **Energization** is reachability from the substation over segments and closed switches.
* **Fault location** is the zone at the end of the chain of set indicators.
* **Isolation around a jammed switch** is a traversal: from the faulted zone, across every closed
  switch a crew reported jammed, to the zones that must be cut out with it.
* **Restoration** is a search between dead and live zones over `BOUNDARY` edges, skipping jammed
  switches and cut-out zones, screened with parameterised what-if traversals that never touch
  the live state.
* **Line dependencies** come from the same traversal: for every line, the buses, load and
  critical facilities downstream of it right now. The agent can call it as a tool
  (`line_dependencies`), and the diagram shows it on hover.
* **Line loading** is computed in one query: every energized bus has exactly one path from the
  source in a radial feeder, so a line carries the load of the buses whose path uses it.
* **Radiality** is a minimum spanning forest (`algo.MSF`) on the conducting edges of a fork: any
  conducting edge the tree cannot use closes a loop, and that tie is named.
* **Transfer limit** is `algo.maxFlow` from the substation to every healthy load: the most any
  switching could restore, and the lines that cap it.
* **Safety** comes from a forked graph per plan (`GRAPH.COPY`), checked before any switch moves.
  A what-if forks the whole grid twice, with and without the jam, and plans on both.
* **Explainability** comes from the decision trace stored next to the grid: incidents, plans,
  switch operations, violations, the zones each plan restores, the conditions and priorities an
  incident was handled under, and the crew reports it took into account.

## Graph data model

Graphs: `grid_base` (pristine feeder, used for reset), `grid_live` (current state and decision
trace), `fork_<planId>` (temporary, deleted after verification) and `whatif_*` (temporary,
deleted after a what-if).

```
(:Bus {id, pp_index, p_kw, q_kvar, s_kva, critical, facility, weight, zone, energized, is_source})
(:Zone {id, p_kw, weighted_kw, energized, faulted, cut_off})

(:Bus)-[:SEG {id, r_ohm, x_ohm, cap_kva, faulted}]->(:Bus)                 unswitched line
(:Bus)-[:SW {id, kind, status, normally, r_ohm, x_ohm, cap_kva, fi, operable, report}]->(:Bus)
        kind: breaker | sectionalizer | tie     status: open | closed     fi: set | clear | none
(:Bus)-[:IN_ZONE]->(:Zone)
(:Zone)-[:BOUNDARY {switch_id, kind}]->(:Zone)                            upstream -> downstream

(:Context {current, conditions, model, ts})-[:SETS]->(:Priority {facility, weight, rule})
        -[:APPLIES_TO]->(:Bus)
(:CrewReport {id, note, other, model, ts})-[:FINDS]->(:Finding {switch_id, operable, evidence})

(:Incident {id, ts, fault_line, status})-[:FAULT_IN]->(:Zone)
(:Incident)-[:UNDER]->(:Context)
(:Incident)-[:HAS_REPORT]->(:CrewReport)
(:Incident)-[:CUTS_OFF {via}]->(:Zone)                    healthy zone isolated with the fault
(:Incident)-[:HAS_PLAN]->(:Plan {id, kind, status, switch_ops, restored_kw, restored_weighted,
                                 min_v_pu, max_loading_pct, recommended, recommendation})
        kind: isolation | restoration     status: verified | rejected | executed
(:Plan)-[:STEP]->(:SwitchOp {seq, switch_id, op})
(:Plan)-[:VIOLATES]->(:Violation {type, detail})
(:Plan)-[:RESTORES]->(:Zone)
```

Indexes on `Bus.id`, `Zone.id`, `Plan.id` and `Incident.id`. Separate `SEG` and `SW` types keep
the two core queries simple: zones are components over `SEG` only, and energization is `SEG`
plus closed `SW`.

## The Cypher the product depends on

![Graph tab: each step's Cypher run live against grid_live](docs/graph.png)

Zones: connected components over unswitched segments
([zones.py](backend/gridmend/grid/zones.py)).

```cypher
CALL algo.WCC({nodeLabels: ['Bus'], relationshipTypes: ['SEG']})
YIELD node, componentId
WITH componentId, collect(node) AS nodes
WHERE none(n IN nodes WHERE n.is_source)
RETURN [n IN nodes | n.id] AS buses
```

Energized buses: reachability through segments and closed switches
([energize.py](backend/gridmend/grid/energize.py)).

```cypher
MATCH (s:Bus {is_source: true})
MATCH p = (s)-[:SEG|SW*0..40]-(b:Bus)
WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.status = 'closed')
RETURN DISTINCT b.id
```

Fault location: the zone whose upstream indicator is set and whose downstream indicators are
all clear ([locate.py](backend/gridmend/flisr/locate.py)).

```cypher
MATCH (:Bus)-[up:SW]->(:Bus)-[:IN_ZONE]->(z:Zone)
WHERE up.kind <> 'tie' AND up.fi = 'set'
OPTIONAL MATCH (b:Bus)-[:IN_ZONE]->(z), (b)-[down:SW]->(:Bus)
WHERE down.kind = 'sectionalizer' AND down.fi = 'set'
WITH z, count(down) AS set_downstream
WHERE set_downstream = 0
RETURN z.id
```

Isolation ([isolate.py](backend/gridmend/flisr/isolate.py)): first grow the region to cut out,
repeating until it stops growing. A zone joined to it by a jammed closed switch cannot be
separated from the fault.

```cypher
MATCH (b:Bus)-[:IN_ZONE]->(z:Zone), (b)-[s:SW]-(:Bus)-[:IN_ZONE]->(o:Zone)
WHERE z.id IN $zones AND NOT o.id IN $zones AND s.status = 'closed' AND s.operable = false
RETURN DISTINCT o.id, s.id
```

Then open every closed switch with exactly one end in the region.

```cypher
MATCH (b:Bus)-[:IN_ZONE]->(z:Zone), (b)-[s:SW]-(o:Bus)
WHERE z.id IN $zones AND s.status = 'closed' AND NOT coalesce(o.zone, -1) IN $zones
RETURN s.id, endNode(s) = b AS upstream
```

Restoration options: dead healthy zones a working tie connects to a live zone
([candidates.py](backend/gridmend/flisr/candidates.py)).

```cypher
MATCH (d:Zone {energized: false, faulted: false})-[b:BOUNDARY {kind: 'tie'}]-
      (:Zone {energized: true, faulted: false})
WHERE NOT coalesce(d.cut_off, false)
MATCH ()-[s:SW {id: b.switch_id}]->()
WHERE coalesce(s.operable, true)
RETURN d.id, b.switch_id
ORDER BY d.weighted_kw DESC, b.switch_id
```

What-if screening of a candidate configuration without forking: the proposed closed-switch set
is a parameter.

```cypher
MATCH (s:Bus {is_source: true})
MATCH p = (s)-[:SEG|SW*0..40]-(b:Bus)
WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.id IN $closed)
RETURN b.id, b.zone, b.p_kw, b.q_kvar,
       [r IN relationships(p) | r.id], [r IN relationships(p) | r.cap_kva]
```

Line loading on a fork: in a radial feeder each energized bus has one path from the source, so
summing the loads of the buses behind each edge gives the edge's flow
([verify.py](backend/gridmend/flisr/verify.py)).

```cypher
MATCH (s:Bus {is_source: true})
MATCH p = (s)-[:SEG|SW*0..40]-(b:Bus {energized: true})
WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.status = 'closed')
UNWIND relationships(p) AS r
WITH r, sum(b.p_kw) AS p_kw, sum(b.q_kvar) AS q_kvar
RETURN r.id, sqrt(p_kw * p_kw + q_kvar * q_kvar), r.cap_kva
```

Radiality on a fork that keeps only conducting edges: the source's spanning tree covers every
energized bus, and any conducting edge it leaves out closes a loop. Ties weigh more, so the loop
is reported at the tie that closed it.

```cypher
MATCH (s:Bus {is_source: true})
CALL algo.MSF({nodeLabels: ['Bus'], relationshipTypes: ['SEG', 'SW'],
               weightAttribute: 'tree_weight'})
YIELD nodes, edges
WITH s, nodes, edges WHERE s IN nodes
WITH nodes, [e IN edges | e.id] AS tree
OPTIONAL MATCH (a:Bus)-[r:SEG|SW]->(:Bus)
WHERE a IN nodes AND NOT r.id IN tree
RETURN size(nodes), collect(r.id)
```

Forking is `GRAPH.COPY grid_live fork_<planId>`. The checks run on the fork, the outcome is
written to `grid_live` as a `Plan` with `STEP`, `VIOLATES` and `RESTORES` edges, and the fork is
deleted.

Transfer limit on a throwaway copy where every line is a pair of `FLOW` edges and every healthy
load drains into one sink ([transfer.py](backend/gridmend/flisr/transfer.py)).

```cypher
MATCH (s:Bus {is_source: true}), (t:Sink)
CALL algo.maxFlow({sourceNodes: [s], targetNodes: [t], capacityProperty: 'cap_kva',
                   relationshipTypes: ['FLOW']})
YIELD edges, edgeFlows, maxFlow
RETURN maxFlow,
       [i IN range(0, size(edges) - 1)
        WHERE edges[i].id IS NOT NULL AND edgeFlows[i] >= edges[i].cap_kva - 0.001
        | edges[i].id] AS saturated
```

Explanation: the feeding path of a restored zone
([explain.py](backend/gridmend/flisr/explain.py)).

```cypher
MATCH (s:Bus {is_source: true}), (t:Bus {id: $zone})
MATCH p = (s)-[:SEG|SW*0..40]-(t)
WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.status = 'closed')
RETURN [r IN relationships(p) WHERE type(r) = 'SW' | r.id]
ORDER BY length(p) LIMIT 1
```

## Evaluation

All 23 single faults on unswitched segments of the IEEE 33-bus feeder, five methods
(`gridmend eval --llm-only`), on a normal day with standing priorities:

| method | restored kW | % of optimal | critical loads | switch ops | violations | unsafe plans | mean planning ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| isolate-only | 0 | 0.0 | 0 | 0 | 0 | 0 | 0 |
| greedy | 0 | 0.0 | 0 | 16 | 26 | 11 | 10 |
| llm-only | 1170 | 23.6 | 3 | 59 | 26 | 20 | 14883 |
| **gridmend** | **4950** | **100.0** | **11** | **17** | **0** | **0** | **491** |
| optimal | 4950 | 100.0 | 11 | 17 | 0 | 0 | 314 |

![Results page](docs/results.png)

* **isolate-only** restores nothing beyond isolation (lower bound).
* **greedy** closes the first tie into each dead zone without verification, as an operator
  might by reflex.
* **llm-only** gives `qwen/qwen3.8-27b` a text description of the isolated feeder and no tools;
  its switching is executed unchecked and scored by the verifier.
* **optimal** is a brute-force oracle over every switch combination, written with networkx and
  pandapower only and never reading FalkorDB. It also re-checks every configuration GridMend
  executes.

Unsafe switching is scored as restoring nothing, since protection would trip it. On a feeder this
small, GridMend's search over all tie subsets reaches the optimum; the point of the table is
that it does so with zero violations, every plan verified on a fork, in well under a second.
Planning times depend on the machine. The `llm-only` baseline needs a model endpoint and is not
deterministic, so `gridmend eval --llm-only` records its rows in
[`backend/eval/llm_only.csv`](backend/eval/llm_only.csv) and every later `gridmend eval`, including
the image build, replays them. The Results page also shows, as a faded bar, the load each unsafe
plan claimed it would restore before the verifier caught it.

## Architecture

![GridMend architecture: the UI, MCP clients and the LLM reach the grid only through agent tools; a deterministic pipeline verifies every plan on a FalkorDB fork before it runs](docs/architecture.svg)

Where each part lives:

```
gridmend.grid   load, zones, energization (case data -> graph)
gridmend.flisr  locate, isolate, candidates, verify (+ pandapower), execute, explain, trace,
                transfer (max flow), policy, crew reports, what-if
gridmend.agent  14 tools --+-- LLM operator loop and Ask the grid (any OpenAI-compatible endpoint)
                           +-- MCP server (stdio)
gridmend.api    HTTP API --+-- Next.js + Cytoscape UI
eval            scenario runner, baselines, networkx/pandapower oracle
```

## Run it locally

Requirements: Docker, Python 3.11+ ([uv](https://docs.astral.sh/uv/) recommended), Node 20+.

```bash
docker compose up -d                       # FalkorDB on :6379, FalkorDB Browser on :3000
cp .env.example .env

cd backend
uv venv -p 3.12 .venv && source .venv/bin/activate
uv pip install -e '.[test]'
gridmend ping                              # OK
gridmend load                              # writes grid_base and grid_live
gridmend run 4-5 --auto                    # fault -> locate -> isolate -> plan -> execute -> explain
gridmend reset
gridmend whatif 6-7 --fault 4-5            # what a jammed 6-7 would cost, on copies of the grid
pytest                                     # 138 tests, on scratch graphs
gridmend eval                              # writes backend/eval/out/results.{csv,md}
gridmend api                               # HTTP API on :8000

cd ../frontend
npm install
npm run dev                                # UI on http://localhost:3001
```

Other commands: `basecase`, `zones`, `status`, `switch <id> open|close`, `fault <line>`,
`locate`, `isolate`, `verify --close a,b --open c`, `plan`, `agent <line> [--auto]`, `mcp`,
`eval --llm-only`, `serve --ui <dir>`, and with an LLM configured:

```bash
gridmend context "Heat advisory declared: 44 C, the cooling centre on bus 11 is open"
gridmend context --clear                   # back to standing priorities
gridmend crew "Patrol: the sectionalizer between 6 and 7 is jammed shut"
gridmend crew                              # reports filed and switches jammed now
```

### LLM operator agent

Set `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` in `.env` for any OpenAI-compatible endpoint
with tool calling (the default is local Ollama). For example, with Groq:

```bash
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=qwen/qwen3.8-27b
LLM_API_KEY=...
```

Then:

* `gridmend agent 4-5` lets the agent work the incident through the tools and asks for your
  approval before it executes a plan.
* In the web UI, the top of the Feeder page offers **Today's conditions** (normal day, heat
  advisory, storm warning, or your own description) and **Rules / AI operator agent**. In agent
  mode the agent reads the conditions, locates, isolates, asks for candidates, verifies each on a
  fork and calls `recommend_plan`, while every tool call streams into an agent log and replays on
  the diagram. It never gets the execute tool: its pick and its reason are stored on the Plan
  node, and you approve and execute with the usual button. One model call at a time across all
  visitors, with `AGENT_COOLDOWN_S` (default 30) between agent runs.
* The **Field crew report** panel files a crew's note at any time. Before a fault it shapes
  isolation; during an incident it rejects the plans that need a jammed switch and replans.
* Without an LLM configured, the conditions, crew report and agent controls are hidden. The
  what-if and everything else work without one.

### MCP

`gridmend mcp` serves the same fourteen tools over stdio, so any MCP client can locate, plan,
verify and explain on the live twin. To browse and call them interactively:

```bash
npx @modelcontextprotocol/inspector "$(which gridmend)" mcp
```

Or from a script, without a browser:

```bash
npx @modelcontextprotocol/inspector --cli "$(which gridmend)" mcp --method tools/list
npx @modelcontextprotocol/inspector --cli "$(which gridmend)" mcp \
  --method tools/call --tool-name feeding_path --tool-arg facility=hospital
```

To add it to an MCP client, point the client's server configuration at the command (use the full
path from `which gridmend`, and start FalkorDB first):

```json
{
  "mcpServers": {
    "gridmend": { "command": "/path/to/backend/.venv/bin/gridmend", "args": ["mcp"] }
  }
}
```

A good first session: fault a line with `gridmend fault 4-5`, then ask the client to restore
service. It will typically call `locate_fault`, `isolation_plan`, `restoration_candidates`, then
`verify_plan` for each candidate, and the client's own tool approval stands in for the operator's
approval of `execute_plan`.

| Tool | Returns |
|---|---|
| `operating_context` | conditions in force, every site's priority with its policy rule, the policy, crew reports and jammed switches |
| `grid_status` | energized and dark zones, switch states (including jammed), load served |
| `line_dependencies` | per line: buses, load and critical facilities downstream |
| `locate_fault` | faulted zone and indicator evidence chain |
| `isolation_plan` | switch operations performed, zones cut out with the fault |
| `restoration_candidates` | ranked candidate plans |
| `transfer_capacity` | max-flow bound on what any switching could restore, and the lines that limit it |
| `verify_plan` | pass or fail, metrics, violations (by rank or explicit switches) |
| `what_if` | best safe outcome with and without given switches jammed, on copies of the grid |
| `recommend_plan` | records the chosen verified plan and the reason in the trace |
| `execute_plan` | applies a verified plan |
| `fault_impact` | what a fault on a segment would cost (load left dark, critical sites lost), or every segment ranked |
| `feeding_path` | the switches power crosses from the substation to a bus or named facility, right now |
| `explain_plan` | feeding paths, margins, rejected alternatives, critical sites, conditions, crew reports, switching order |

### Single container

The `Dockerfile` builds the UI as static files and runs FalkorDB, the API and the UI in one
container on port 7860 (or `$PORT`). Work that only depends on the fixed feeder is done once,
while the image builds, so a small host starts in seconds:

* the evaluation runs, so the results page and the risk map are ready immediately;
* the loaded feeder is saved as a FalkorDB snapshot, so the container does not rebuild the graph;
* the candidate pre-checks and power flows the evaluation computed for all 23 faults are saved.
  Both depend only on which switches are closed, so a deployed plan reuses them, while every plan
  is still applied to its own fork and checked for isolation, connectivity, radiality and
  capacity in FalkorDB. A configuration the evaluation never saw, such as one around a jammed
  switch, runs a fresh power flow, so the first what-if after a restart takes longer.

```bash
docker build -t gridmend .
docker run --rm -p 7860:7860 gridmend      # http://localhost:7860, API under /api
```

A GitHub Action ([image.yml](.github/workflows/image.yml)) publishes this image for amd64 and
arm64 to `ghcr.io/anish891/gridmend` on every push to `main`.

### Deploy to Render (free)

[render.yaml](render.yaml) describes one free Docker web service. In the Render dashboard choose
**New > Blueprint**, select this repository and apply. The first build takes several minutes;
the health check is `GET /api/state`. FalkorDB runs inside the container and the feeder is
reloaded on every start, so there is no database to provision.

To enable the LLM features on the deployed site, set `LLM_BASE_URL`, `LLM_MODEL` and
`LLM_API_KEY` in the service's **Environment** settings (the blueprint declares them) and redeploy.
Leave them unset and the site runs rule-based only.

## Data and assumptions

* Feeder: IEEE 33-bus (Baran and Wu, 1989) via `pandapower.networks.case33bw()`. The base
  case reproduces the published values: 202.7 kW losses, minimum 0.913 pu at bus 18.
* Synthetic operating assumptions in
  [assumptions.yaml](backend/data/assumptions.yaml): breaker 1-2, eight sectionalizers with
  fault indicators, four critical sites (hospital, fire station, water pumping, telecom; weight
  10), a cooling centre on bus 11 that is ordinary load (weight 1) on a normal day, line ratings
  of 5000 kVA on the main feeder and 1400 kVA on laterals and ties, and a 0.90 pu voltage limit.
* Synthetic restoration policy in [policy.md](backend/data/policy.md): standing priorities,
  conditions that change them (heat advisory, storm warning, water emergency), and rules for
  choosing between safe plans (crew arrival, forecast storms).

## Limitations

Single faults on unswitched segments only. No protection coordination, transients, unbalanced
three-phase modelling, distributed generation, real SCADA, time-varying load or simultaneous
faults. Crew reports only mark switches jammed or repaired: there is no crew dispatch or repair
timing, and a jammed switch is assumed stuck in the position the twin shows. Priorities change
only between incidents. Line loading is checked on summed load kVA; voltage is checked with a
balanced power flow.

## Research basis

* LARA (Findings of EACL 2026): an LLM plans restoration over a pre-validated catalogue of
  feasible actions. GridMend follows the same split: deterministic code builds and verifies,
  the LLM chooses.
* TwinGridShield (2026): every proposed grid action is authorised against a deterministic twin
  before release; GridMend's fork-and-verify step follows this.
* Grid-Agent (2025): LLM agents for grid violations with sandboxed validation.
* Fault location through graph marking (IEEE Transactions on Smart Grid, 2018).
* FLISR on GridAPPS-D (IEEE Access, 2020): open-source, algorithmic FLISR without an agent.

None of these combines an LLM agent, a graph-database twin, verify-before-act on a fork and a
stored, explainable decision trace.
