"use client";

import { useCallback, useEffect, useState } from "react";

import AgentPanel from "@/components/AgentPanel";
import AskPanel, { type MapFocus } from "@/components/AskPanel";
import CandidateList from "@/components/CandidateList";
import CrewReport from "@/components/CrewReport";
import ExplanationPanel from "@/components/ExplanationPanel";
import FaultPicker, { type Mode } from "@/components/FaultPicker";
import FeederDiagram, { RISK_STEPS, type Positions, type Pulse, type Risk } from "@/components/FeederDiagram";
import Intro from "@/components/Intro";
import StatusStrip from "@/components/StatusStrip";
import Stepper from "@/components/Stepper";
import Timeline from "@/components/Timeline";
import TopBar from "@/components/TopBar";
import WhatIf from "@/components/WhatIf";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import Segmented from "@/components/ui/Segmented";
import Spinner from "@/components/ui/Spinner";
import Term from "@/components/ui/Term";
import {
  api, runAgent, type AgentEvent, type CrewFiling, type Explanation, type GridState, type Location,
  type PlanResult, type Results, type SwitchOp, type Trace,
} from "@/lib/api";
import { currentFeeder, FEEDERS, type FeederConfig } from "@/lib/feeders";
import { problemsFor, withSwitch } from "@/lib/overlay";

// Short pauses between automatic steps so each one is visible on the diagram.
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// Brings a section into view once React has rendered it.
function reveal(id: string) {
  setTimeout(() => {
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById(id)?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
  }, 80);
}

// Where each stepper step's result is shown: the map for the first three, then plans, then the explanation.
const STEP_TARGETS = ["map", "map", "map", "plans", "plans", "explanation"];

type Step = "fault" | "locate" | "plan";

// Stepper indices: Fault, Locate, Isolate, Verify plans, Approve, Restored.
const STEP = { fault: 0, locate: 1, isolate: 2, verify: 3, approve: 4, restored: 5 };

export default function Home() {
  const [state, setState] = useState<GridState | null>(null);
  const [busy, setBusy] = useState<{ step: number; message: string } | null>(null);
  const [startedAt, setStartedAt] = useState(0);
  const [now, setNow] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [fault, setFault] = useState<string | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [plan, setPlan] = useState<PlanResult | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [executed, setExecuted] = useState<string | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [pulse, setPulse] = useState<Pulse | null>(null);
  const [mode, setMode] = useState<Mode>("rules");
  const [agentModel, setAgentModel] = useState<string | null>(null);
  const [agentEvents, setAgentEvents] = useState<AgentEvent[]>([]);
  const [agentRunning, setAgentRunning] = useState(false);
  const [crewBusy, setCrewBusy] = useState(false);
  const [crewError, setCrewError] = useState<string | null>(null);
  const [crewResult, setCrewResult] = useState<CrewFiling | null>(null);
  const [risk, setRisk] = useState<Risk | null>(null);
  const [showRisk, setShowRisk] = useState(false);
  const [focus, setFocus] = useState<MapFocus | null>(null);
  const [demo, setDemo] = useState(false);
  const [results, setResults] = useState<Results | null>(null);
  const [feeder, setFeeder] = useState<FeederConfig>(FEEDERS.ieee33);
  // Positions for a feeder drawn from its coordinates; the 33-bus map needs none.
  const [positions, setPositions] = useState<Positions | null>(null);

  const toggleRisk = useCallback(async (on: boolean) => {
    setShowRisk(on);
    if (on && !risk) {
      try {
        const { lines } = await api.risk();
        setRisk(Object.fromEntries(lines.map((l) => [l.line, l])));
      } catch (e) {
        setError((e as Error).message);
        setShowRisk(false);
      }
    }
  }, [risk]);

  const flash = useCallback((ids: string[], kind: Pulse["kind"]) => {
    setPulse({ ids, kind, key: Math.random() });
  }, []);

  // Show switching one operation at a time on the diagram before reading the real state back.
  const replay = useCallback(async (ops: SwitchOp[]) => {
    for (const op of ops) {
      setState((current) => (current ? withSwitch(current, op) : current));
      flash([op.switch_id], op.op);
      await pause(550);
    }
  }, [flash]);

  const working = useCallback((step: number, message: string) => {
    setBusy({ step, message });
    setStartedAt(Date.now());
    setNow(Date.now());
  }, []);

  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  const refresh = useCallback(async () => {
    const next = await api.state();
    setState(next);
    return next;
  }, []);

  // The LLM agent works the incident through its tools; each call it makes is shown on the
  // diagram as it happens. It only recommends: the operator still approves and executes.
  const handleAgentEvent = useCallback(async (event: AgentEvent) => {
    setAgentEvents((events) => [...events, event]);
    if (event.type !== "tool" || "error" in event.result) return;
    const result = event.result as Record<string, any>;
    if (event.name === "locate_fault") {
      working(STEP.locate, "The agent is following the chain of tripped fault indicators");
      const chains: Record<string, string[]> = Object.fromEntries(
        Object.entries(result.indicator_chains as Record<string, string[]>).map(([z, c]) => [z.slice(1), c]));
      setLocations((rounds) => [...rounds, {
        zone: result.faulted_zone, chain: result.indicator_chain, zones: result.faulted_zones, chains,
      }]);
      for (const id of new Set(Object.values(chains).flat())) {
        flash([id], "fi");
        await pause(600);
      }
      await refresh();
    } else if (event.name === "isolation_plan") {
      working(STEP.isolate, result.breaker_tripped_again
        ? "The breaker tripped again on reclosing: the agent is looking for a hidden second fault"
        : "The agent isolated the faulted zone");
      const ops = (result.executed as string[]).map((step) => {
        const [op, switch_id] = step.split(" ");
        return { op, switch_id } as SwitchOp;
      });
      await replay(ops);
      await refresh();
      setTrace(await api.trace());
    } else if (event.name === "restoration_candidates") {
      working(STEP.verify, `The agent got ${result.candidates.length} candidates and is verifying them on forks`);
    } else if (event.name === "verify_plan") {
      working(STEP.verify, `The agent verified ${result.plan_id} on a fork: ${result.passed ? "passed" : "rejected"}`);
      setTrace(await api.trace());
    } else if (event.name === "recommend_plan") {
      working(STEP.verify, `The agent recommends ${result.recommended} and is writing its explanation`);
    }
  }, [working, flash, refresh, replay]);

  const runWithAgent = useCallback(async () => {
    setAgentEvents([]);
    setAgentRunning(true);
    working(STEP.locate, "The operator agent is starting on the incident");
    let chain = Promise.resolve();
    try {
      await runAgent((event) => {
        chain = chain.then(() => handleAgentEvent(event));
      });
      await chain;
      const result = await api.plans();
      setPlan(result);
      setSelected(result.recommended);
      setTrace(await api.trace());
      reveal("plans");
    } finally {
      setAgentRunning(false);
    }
  }, [working, handleAgentEvent]);

  const run = useCallback(async (from: Step, line?: string | string[], as: Mode = mode): Promise<PlanResult | null> => {
    setError(null);
    try {
      const lines = typeof line === "string" ? [line] : line ?? [];
      if (from === "fault" && lines.length) {
        const named = lines.join(" and ");
        working(STEP.fault, `Fault on ${lines.length > 1 ? "lines" : "line"} ${named}: the breaker trips and the feeder goes dark`);
        await api.fault(lines);
        setFault(named);
        await refresh();
        flash(lines, "fault");
        await pause(1100);
        if (as === "agent") {
          await runWithAgent();
          return null;
        }
      }
      // Each round isolates the faults the indicators show. If reclosing trips the breaker again,
      // a fault was hidden upstream of one already found, so locate and isolate once more.
      for (let round = 0; from !== "plan" && round < 5; round++) {
        working(STEP.locate, round === 0 ? "Following the chain of tripped fault indicators"
          : "The breaker tripped again on reclosing: a second fault was hidden upstream. Following the new indicators");
        const found = await api.locate();
        setLocations((rounds) => [...rounds, found]);
        for (const id of new Set(Object.values(found.chains).flat())) {
          flash([id], "fi");
          await pause(600);
        }
        await refresh();
        await pause(400);
        const zones = found.zones.map((z) => `Z${z}`).join(" and ");
        working(STEP.isolate, `Cutting out ${zones} and reclosing the substation breaker`);
        const isolation = await api.isolate();
        await replay(isolation.ops);
        await refresh();
        setTrace(await api.trace());
        await pause(900);
        if (!isolation.tripped_again) break;
      }
      working(STEP.verify, "Generating restoration plans and verifying each one on a forked graph");
      const result = await api.plan();
      setPlan(result);
      setSelected(result.recommended);
      setTrace(await api.trace());
      reveal("plans");
      return result;
    } catch (e) {
      setError((e as Error).message);
      return null;
    } finally {
      setBusy(null);
    }
  }, [refresh, working, flash, replay, mode, runWithAgent]);

  const approve = useCallback(async (planId: string, scroll = true): Promise<Explanation | null> => {
    setError(null);
    working(STEP.approve, `Executing ${planId} on the live graph`);
    try {
      const execution = await api.execute(planId);
      setExecuted(planId);
      setSelected(null);
      await replay(execution.ops);
      await refresh();
      setTrace(await api.trace());
      const explained = await api.explain();
      setExplanation(explained);
      if (scroll) reveal("explanation");
      return explained;
    } catch (e) {
      setError((e as Error).message);
      return null;
    } finally {
      setBusy(null);
    }
  }, [refresh, working, replay]);

  // Solar tripped with the fault; once its zone has had power for a while it can reconnect,
  // and its output relieves the lines feeding the restored area, so planning runs again.
  const reconnectSolar = useCallback(async () => {
    setError(null);
    working(STEP.verify, "The solar plant reconnects after its delay; checking what more can be restored");
    try {
      const { replan } = await api.reconnectGeneration();
      await refresh();
      if (!replan) return;
      setExecuted(null);
      setExplanation(null);
      setSelected(null);
      await run("plan");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [refresh, run, working]);

  // A guided run of the main-line fault: each rejected plan is shown with its reason on the map,
  // then the recommended one is approved and its feeding paths traced.
  const playDemo = useCallback(async () => {
    setDemo(true);
    try {
      reveal("progress");
      const result = await run("fault", feeder.demoLine, "rules");
      if (!result) return;
      reveal("progress");
      for (const c of result.candidates.filter((c) => !c.passed)) {
        const why = c.violations[0] ? `${c.violations[0].type}: ${c.violations[0].detail}` : "it broke a safety rule";
        working(STEP.verify, `${c.plan_id} was rejected on its copy of the graph. ${why}`);
        setSelected(c.plan_id);
        await pause(3200);
      }
      if (!result.recommended) return;
      working(STEP.approve, `${result.recommended} passed every check, so the operator approves it`);
      setSelected(result.recommended);
      await pause(2600);
      const explained = await approve(result.recommended, false);
      if (explained?.plan) {
        for (const zone of Object.keys(explained.plan.paths).map(Number)) {
          working(STEP.restored, `Z${zone} is fed again through ${explained.plan.paths[zone].join(" → ")}`);
          setFocus({ bus: zone, lines: [] });
          await pause(2400);
        }
        setFocus(null);
      }
      reveal("explanation");
    } finally {
      setBusy(null);
      setDemo(false);
    }
  }, [run, working, approve, feeder]);

  const fileCrewReport = useCallback(async (note: string) => {
    setCrewBusy(true);
    setCrewError(null);
    try {
      const filed = await api.fileCrewReport(note);
      setCrewResult(filed);
      const next = await refresh();
      if (next.incident) setTrace(await api.trace());
      // A jam found after plans were checked: plan again around it.
      if (next.incident?.status === "isolated" && plan) await run("plan");
    } catch (e) {
      setCrewError((e as Error).message);
    } finally {
      setCrewBusy(false);
    }
  }, [refresh, plan, run]);

  const resetGrid = useCallback(async () => {
    setError(null);
    working(STEP.fault, "Restoring the healthy feeder");
    try {
      await api.reset();
      setFault(null);
      setLocations([]);
      setPlan(null);
      setSelected(null);
      setExecuted(null);
      setTrace(null);
      setExplanation(null);
      setAgentEvents([]);
      setFocus(null);
      setCrewResult(null);
      setCrewError(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [refresh, working]);

  useEffect(() => {
    const chosen = FEEDERS[currentFeeder()];
    setFeeder(chosen);
    if (chosen.dense) {
      api.layout()
        .then(({ positions: p }) => p && setPositions(
          Object.fromEntries(Object.entries(p).map(([bus, xy]) => [Number(bus), xy])) as Positions))
        .catch((e: Error) => setError(`Cannot load the map: ${e.message}`));
    }
  }, []);

  useEffect(() => {
    // Only for the headline numbers; the page works without them.
    api.results().then(setResults).catch(() => setResults(null));
  }, []);

  useEffect(() => {
    api.agentInfo()
      .then((info) => setAgentModel(info.available ? info.model : null))
      .catch(() => setAgentModel(null));
  }, []);

  // Pick up an incident already in progress, e.g. after a page reload.
  useEffect(() => {
    refresh()
      .then(async (s) => {
        const faulted = s.lines.filter((l) => l.faulted).map((l) => l.id);
        if (faulted.length) setFault(faulted.join(" and "));
        if (s.incident) setTrace(await api.trace());
        if (s.incident?.status === "restored") setExplanation(await api.explain());
      })
      .catch((e: Error) => setError(`Cannot reach the API: ${e.message}`));
  }, [refresh]);

  const healthy = state !== null && !state.incident && !state.lines.some((l) => l.faulted);
  const status = state?.incident?.status;
  const reviewed = plan?.candidates.find((c) => c.plan_id === selected);
  const highlight = reviewed?.ops ?? [];
  const problems = reviewed && state ? problemsFor(reviewed, state) : null;
  const nothingPassed = plan !== null && !plan.candidates.some((c) => c.passed);

  // How far the incident has got, for the stepper.
  const done = healthy ? 0
    : status === "restored" ? STEP.restored + 1
    : plan ? STEP.approve
    : status === "isolated" ? STEP.verify
    : locations.length ? STEP.isolate
    : STEP.locate;

  // Solar on a bus that has power again but is still off, after a restoration.
  const solarWaiting = status === "restored" && state !== null
    && state.buses.some((b) => b.dg_kw > 0 && b.energized && !b.dg_online);

  const message = busy ? busy.message
    : solarWaiting ? "Power is back wherever it is safe. The solar plant tripped with the fault; its zone has power again, so it can reconnect and may let more load back on."
    : status === "restored" ? "Power is back wherever it is safe. The explanation below is read from the decision trace."
    : plan?.candidates.length === 0 ? "No backup route reaches the dark sections, so the fault stays isolated."
    : nothingPassed ? "No plan passed the safety checks, so nothing risky was switched. The fault stays isolated."
    : plan ? "Select a plan to preview its switching on the map, then approve one that passed."
    : status === "isolated" ? "The fault is cut out and everything upstream has power. Next, find routes for the rest."
    : fault ? "The substation breaker tripped and the whole feeder is dark."
    : null;

  // The one next step for the operator, when it is theirs to take.
  const action = solarWaiting && !busy ? <Button onClick={reconnectSolar}>Reconnect the solar plant and plan again</Button>
    : busy || status === "restored" || plan ? null
    : status === "isolated" ? <Button onClick={() => run("plan")}>Find restoration plans</Button>
    : fault ? <Button onClick={() => run("locate")}>Locate and isolate the fault</Button>
    : null;

  const picking = healthy && !busy && !demo;

  return (
    <main>
      <TopBar page="feeder">
        <Button variant="secondary" disabled={busy !== null || demo || healthy} onClick={resetGrid}>
          Reset feeder
        </Button>
      </TopBar>
      {error && <p className="error" role="alert">{error}</p>}
      {!state && !error ? (
        <Card className="loading-card">
          <div className="loading" role="status">
            <Spinner /> Connecting to the grid twin in FalkorDB…
          </div>
          <p className="muted small">Loading the feeder, its switches and the current incident.</p>
        </Card>
      ) : picking ? (
        <FaultPicker
          onDemo={playDemo}
          feeder={feeder}
          results={results}
          disabled={busy !== null || crewBusy}
          onFault={(line) => run("fault", line)}
          agentModel={agentModel}
          mode={mode}
          onMode={setMode}
        />
      ) : (
        <Stepper
          done={done}
          active={busy?.step ?? null}
          elapsed={Math.round((now - startedAt) / 1000)}
          message={message}
          action={demo ? null : action}
          targets={STEP_TARGETS}
        />
      )}
      {state && <StatusStrip state={state} trace={trace} />}

      <div className="layout">
        <Card
          id="map"
          className="diagram-card"
          title="Feeder map"
          hint={picking ? `${feeder.label}. Click a line to fault it, hover anything for details`
            : `${feeder.label}. Hover anything for details`}
          actions={
            <Segmented
              label="Diagram view"
              value={showRisk ? "risk" : "live"}
              onChange={(view) => toggleRisk(view === "risk")}
              options={[{ value: "live", label: "Live" }, { value: "risk", label: "Risk map" }]}
            />
          }
        >
          {state && (!feeder.dense || positions) && (
            <FeederDiagram
              positions={feeder.dense ? positions : null}
              state={state}
              highlight={highlight}
              pulse={pulse}
              problems={problems}
              risk={showRisk ? risk : null}
              focus={focus}
              onSegmentClick={healthy && !busy && !demo && !crewBusy ? (line) => run("fault", line) : undefined}
            />
          )}
          {showRisk && (
            <div className="risk-legend">
              <span className="muted small">How much load stays dark if a line fails, even after the best safe re-routing:</span>
              <div className="risk-scale">
                {RISK_STEPS.map(([from, color], i) => (
                  <span key={from}>
                    <i style={{ background: color }} />
                    {i + 1 < RISK_STEPS.length ? `${from}-${RISK_STEPS[i + 1][0]}` : `${from}+`} kW
                  </span>
                ))}
              </div>
              <span className="muted small">From the 23-fault evaluation. Hover a line for the critical sites it would cost.</span>
            </div>
          )}
          <ul className="legend">
            <li><span className="swatch live" /> has power</li>
            <li><span className="swatch dark" /> no power</li>
            <li><span className="box closed" /> closed switch</li>
            <li><span className="box open" /> open switch</li>
            <li>
              <span className="dash" />
              <Term hint="A backup switch, normally open, that can feed a dark section from another route.">
                tie switch
              </Term>
            </li>
            <li><span className="star">★</span> critical site</li>
            {state?.buses.some((b) => b.dg_kw > 0) && (
              <li>
                <span className="swatch solar" />
                <Term hint="Solar trips whenever its bus loses power and comes back only after a reconnection delay. Dashed while off.">
                  solar plant
                </Term>
              </li>
            )}
            {state?.lines.some((l) => !l.operable) && <li><span className="box jammed" /> jammed switch</li>}
            {state?.zones.some((z) => z.cut_off) && (
              <li><span className="swatch cut-off" /> cut out with the fault</li>
            )}
            {!healthy && (
              <>
                <li><span className="swatch faulted" /> faulted zone</li>
                <li>
                  <span className="box fi" />
                  <Term hint="Fault current passed through this switch. The chain of these leads to the fault.">
                    indicator tripped
                  </Term>
                </li>
              </>
            )}
            {plan && plan.candidates.length > 0 && (
              <>
                <li><span className="box to-close" /> plan closes</li>
                <li><span className="box to-open" /> plan opens</li>
                <li><span className="problem-mark" /> why a plan failed</li>
              </>
            )}
          </ul>
        </Card>

        {healthy ? (
          <Intro />
        ) : (
          <Timeline fault={fault} locations={locations} trace={trace} />
        )}
      </div>

      {agentModel && state && <AskPanel incident={!healthy} questions={feeder.questions} onFocus={setFocus} />}
      {(agentRunning || agentEvents.length > 0) && (
        <AgentPanel model={agentModel} events={agentEvents} running={agentRunning} />
      )}
      {plan && (
        <Card
          id="plans"
          className="plans-card"
          title="Restoration plans"
          hint="Ranked by load restored, critical sites first. Select a plan to see it, or why it failed, on the map"
        >
          {plan.candidates.length === 0 ? (
            <EmptyState title="Nothing to restore">
              No dead zone can be reached from a live one.
            </EmptyState>
          ) : (
            <CandidateList
              candidates={plan.candidates}
              recommended={plan.recommended}
              selected={selected}
              executed={executed}
              busy={busy !== null || demo}
              onSelect={(id) => !executed && !demo && setSelected(id)}
              onApprove={approve}
            />
          )}
        </Card>
      )}
      {state && status !== "restored" && (
        <div className="tools">
          {agentModel && (
            <CrewReport
              jammed={state.lines.filter((l) => !l.operable)}
              examples={feeder.crewExamples}
              placeholder={feeder.crewPlaceholder}
              busy={crewBusy}
              error={crewError}
              result={crewResult}
              disabled={busy !== null}
              onFile={fileCrewReport}
            />
          )}
          <WhatIf state={state} healthy={healthy} disabled={busy !== null} />
        </div>
      )}
      {explanation && (
        <ExplanationPanel e={explanation} onPath={(zone) => setFocus(zone === null ? null : { bus: zone, lines: [] })} />
      )}
    </main>
  );
}
