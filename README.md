# GridMend: a self-healing grid agent on a FalkorDB twin

GridMend restores power after a fault on a distribution feeder. It locates the fault from
fault-indicator readings, isolates it, searches the graph for ways to re-feed the healthy
areas, checks every candidate on a forked copy of the graph, and only then lets an operator
(or an LLM operator agent) execute a plan. Every decision is written back into the graph, so
any restoration can be explained afterwards, path by path.

**Track:** 01, Agents That Act on Connected Data.

**Try it:**

* **Run the published image** (full speed, needs only Docker), then open http://localhost:7860:

  ```bash
  docker run --rm -p 7860:7860 ghcr.io/anish891/gridmend
  ```

* **Live demo:** _add your Render URL here_. It runs on Render's free tier (0.1 CPU), so it
  sleeps when idle, takes about a minute to wake, and planning takes about a minute instead of
  two seconds.

Click a line to fault it, then approve a plan.

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
expensive distribution-management systems, and its decisions are hard to inspect.

## What GridMend does

| Step | What happens | Where the graph does the work |
|---|---|---|
| Locate | Follow the chain of tripped fault indicators from the substation | Zone whose upstream indicator is set and downstream ones are clear |
| Isolate | Open the faulted zone's boundary switches, reclose the breaker | Switches with exactly one end in the faulted zone |
| Plan | Build candidate restoration plans | What-if energization, radiality and line loading for each tie subset |
| Verify | Apply each candidate to `GRAPH.COPY` of the live graph and check isolation, connectivity, radiality, capacity and voltage | A fork per plan; power flow on the forked configuration |
| Decide | Pick among verified plans: critical loads first, then most load, then fewest operations | `RESTORES` edges from each plan to zones |
| Act | Execute the approved plan on the live graph | Only plans whose status is `verified` |
| Explain | Feeding path of every restored zone, margins, rejected alternatives, why each critical load is lit or dark | Stored decision trace |

The LLM never writes Cypher and never flips a switch. It calls seven tools, chooses among
plans that already passed verification, and explains the choice using only facts the tools
returned. All writes to the graph are deterministic code.

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
* The plan with both critical loads is executed. Water pumping stays dark (no tie reaches a
  live zone) and so does telecom (closing 18-33 from Z14 would overload the path through 2-19).

## Why FalkorDB is load-bearing

Remove FalkorDB and nothing is left: no twin, no forks, no paths, no trace.

* **Zones** are connected components over unswitched segments (`algo.WCC` filtered to `SEG`).
* **Energization** is reachability from the substation over segments and closed switches.
* **Fault location** is the zone at the end of the chain of set indicators.
* **Restoration** is a search between dead and live zones over `BOUNDARY` edges, screened with
  parameterised what-if traversals that never touch the live state.
* **Line loading** is computed in one query: every energized bus has exactly one path from the
  source in a radial feeder, so a line carries the load of the buses whose path uses it.
* **Safety** comes from a forked graph per plan (`GRAPH.COPY`), checked before any switch moves.
* **Explainability** comes from the decision trace stored next to the grid: incidents, plans,
  switch operations, violations and the zones each plan restores.

## Graph data model

Graphs: `grid_base` (pristine feeder, used for reset), `grid_live` (current state and decision
trace) and `fork_<planId>` (temporary, deleted after verification).

```
(:Bus {id, pp_index, p_kw, q_kvar, s_kva, critical, facility, weight, zone, energized, is_source})
(:Zone {id, p_kw, weighted_kw, energized, faulted})

(:Bus)-[:SEG {id, r_ohm, x_ohm, cap_kva, faulted}]->(:Bus)                 unswitched line
(:Bus)-[:SW {id, kind, status, normally, r_ohm, x_ohm, cap_kva, fi}]->(:Bus)
        kind: breaker | sectionalizer | tie     status: open | closed     fi: set | clear | none
(:Bus)-[:IN_ZONE]->(:Zone)
(:Zone)-[:BOUNDARY {switch_id, kind}]->(:Zone)                            upstream -> downstream

(:Incident {id, ts, fault_line, status})-[:FAULT_IN]->(:Zone)
(:Incident)-[:HAS_PLAN]->(:Plan {id, kind, status, switch_ops, restored_kw, restored_weighted,
                                 min_v_pu, max_loading_pct})
        kind: isolation | restoration     status: verified | rejected | executed
(:Plan)-[:STEP]->(:SwitchOp {seq, switch_id, op})
(:Plan)-[:VIOLATES]->(:Violation {type, detail})
(:Plan)-[:RESTORES]->(:Zone)
```

Indexes on `Bus.id`, `Zone.id`, `Plan.id` and `Incident.id`. Separate `SEG` and `SW` types keep
the two core queries simple: zones are components over `SEG` only, and energization is `SEG`
plus closed `SW`.

## The Cypher the product depends on

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

Isolation: every closed switch with exactly one end in the faulted zone
([isolate.py](backend/gridmend/flisr/isolate.py)).

```cypher
MATCH (b:Bus)-[:IN_ZONE]->(:Zone {id: $zone}), (b)-[s:SW]-(o:Bus)
WHERE s.status = 'closed' AND coalesce(o.zone, -1) <> $zone
RETURN s.id, endNode(s) = b AS upstream
```

Restoration options: dead healthy zones a tie connects to a live zone
([candidates.py](backend/gridmend/flisr/candidates.py)).

```cypher
MATCH (d:Zone {energized: false, faulted: false})-[b:BOUNDARY {kind: 'tie'}]-
      (:Zone {energized: true, faulted: false})
RETURN d.id, b.switch_id
ORDER BY d.weighted_kw DESC, b.switch_id
```

What-if screening of a candidate configuration without forking: the proposed closed-switch set
is a parameter.

```cypher
MATCH (s:Bus {is_source: true})
MATCH p = (s)-[:SEG|SW*0..40]-(b:Bus)
WHERE all(r IN relationships(p) WHERE type(r) = 'SEG' OR r.id IN $closed)
RETURN DISTINCT b.id, b.zone
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

Radiality on a fork: the energized part is a tree exactly when it has one conducting edge fewer
than buses.

```cypher
MATCH (a:Bus {energized: true})-[r:SEG|SW]->(b:Bus {energized: true})
WHERE type(r) = 'SEG' OR r.status = 'closed'
RETURN count(r)
```

Forking is `GRAPH.COPY grid_live fork_<planId>`. The checks run on the fork, the outcome is
written to `grid_live` as a `Plan` with `STEP`, `VIOLATES` and `RESTORES` edges, and the fork is
deleted.

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
(`gridmend eval --llm-only`):

| method | restored kW | % of optimal | critical loads | switch ops | violations | unsafe plans | mean planning ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| isolate-only | 0 | 0.0 | 0 | 0 | 0 | 0 | 0 |
| greedy | 0 | 0.0 | 0 | 16 | 26 | 11 | 10 |
| llm-only | 1170 | 23.6 | 3 | 62 | 50 | 20 | 14483 |
| **gridmend** | **4950** | **100.0** | **11** | **17** | **0** | **0** | **491** |
| optimal | 4950 | 100.0 | 11 | 17 | 0 | 0 | 314 |

![results page](docs/results.png)

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
that it does so with zero violations, every plan verified on a fork, in about half a second.

## Architecture

```
            +------------------- FalkorDB ---------------------+
            | grid_base | grid_live (twin + decision trace)     |
            |           | fork_<planId> (one per verification)  |
            +--------------------------------------------------+
                 ^ Cypher / GRAPH.COPY (deterministic code only)
                 |
   gridmend.grid   load, zones, energization (case data -> graph)
   gridmend.flisr  locate, isolate, candidates, verify (+ pandapower), execute, explain, trace
                 |
   gridmend.agent  7 tools  --+-- LLM operator loop (any OpenAI-compatible endpoint)
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
pytest                                     # 89 tests, on scratch graphs
gridmend eval                              # writes backend/eval/out/results.{csv,md}
gridmend api                               # HTTP API on :8000

cd ../frontend
npm install
npm run dev                                # UI on http://localhost:3001
```

Other commands: `basecase`, `zones`, `status`, `switch <id> open|close`, `fault <line>`,
`locate`, `isolate`, `verify --close a,b --open c`, `plan`, `agent <line> [--auto]`, `mcp`,
`eval --llm-only`, `serve --ui <dir>`.

### LLM operator agent

Set `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` in `.env` for any OpenAI-compatible endpoint
with tool calling (the default is local Ollama). For example, with Groq:

```bash
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=qwen/qwen3.8-27b
LLM_API_KEY=...
```

Then `gridmend agent 4-5` lets the agent work the incident through the tools and asks for your
approval before it executes a plan.

### MCP

`gridmend mcp` serves the same seven tools over stdio:

```bash
npx @modelcontextprotocol/inspector "$(which gridmend)" mcp
```

| Tool | Returns |
|---|---|
| `grid_status` | energized and dark zones, switch states, load served |
| `locate_fault` | faulted zone and indicator evidence chain |
| `isolation_plan` | switch operations performed |
| `restoration_candidates` | ranked candidate plans |
| `verify_plan` | pass or fail, metrics, violations (by rank or explicit switches) |
| `execute_plan` | applies a verified plan |
| `explain_plan` | feeding paths, margins, rejected alternatives, critical loads |

### Single container

The `Dockerfile` builds the UI as static files and runs FalkorDB, the API and the UI in one
container on port 7860 (or `$PORT`). The evaluation runs during the image build, so the results
page is ready immediately; on start the container only loads the feeder.

```bash
docker build -t gridmend .
docker run --rm -p 7860:7860 gridmend      # http://localhost:7860, API under /api
```

A GitHub Action ([image.yml](.github/workflows/image.yml)) publishes this image for amd64 and
arm64 to `ghcr.io/anish891/gridmend` on every push to `main`.

### Deploy to Render (free)

[render.yaml](render.yaml) describes one free Docker web service. In the Render dashboard choose
**New > Blueprint**, select this repository and apply. The first build takes several minutes;
the health check is `GET /api/state`. Nothing else is needed: FalkorDB runs inside the
container and the feeder is reloaded on every start, so there is no database to provision and
no secret to set.

## Data and assumptions

* Feeder: IEEE 33-bus (Baran and Wu, 1989) via `pandapower.networks.case33bw()`. The base
  case reproduces the published values: 202.7 kW losses, minimum 0.913 pu at bus 18.
* Synthetic operating assumptions in
  [assumptions.yaml](backend/data/assumptions.yaml): breaker 1-2, eight sectionalizers with
  fault indicators, four critical loads (hospital, fire station, water pumping, telecom; weight
  10), line ratings of 5000 kVA on the main feeder and 1400 kVA on laterals and ties, and a
  0.90 pu voltage limit.

## Limitations

Single faults on unswitched segments only. No protection coordination, transients, unbalanced
three-phase modelling, distributed generation, repair crews, real SCADA, time-varying load or
simultaneous faults. Line loading is checked on summed load kVA; voltage is checked with a
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
