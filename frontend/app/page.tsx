"use client";

import { useCallback, useEffect, useState } from "react";

import AgentPanel from "@/components/AgentPanel";
import CandidateList from "@/components/CandidateList";
import ExplanationPanel from "@/components/ExplanationPanel";
import FeederDiagram, { RISK_STEPS, type Pulse, type Risk } from "@/components/FeederDiagram";
import Intro, { type Mode } from "@/components/Intro";
import StatusStrip from "@/components/StatusStrip";
import Stepper from "@/components/Stepper";
import Timeline from "@/components/Timeline";
import TopBar from "@/components/TopBar";
import {
  api, runAgent, type AgentEvent, type Explanation, type GridState, type Location, type PlanResult,
  type SwitchOp, type Trace,
} from "@/lib/api";
import { problemsFor, withSwitch } from "@/lib/overlay";

// Short pauses between automatic steps so each one is visible on the diagram.
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

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
  const [location, setLocation] = useState<Location | null>(null);
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
  const [risk, setRisk] = useState<Risk | null>(null);
  const [showRisk, setShowRisk] = useState(false);

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
      setLocation({ zone: result.faulted_zone, chain: result.indicator_chain });
      for (const id of result.indicator_chain) {
        flash([id], "fi");
        await pause(600);
      }
      await refresh();
    } else if (event.name === "isolation_plan") {
      working(STEP.isolate, "The agent isolated the faulted zone");
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
    } finally {
      setAgentRunning(false);
    }
  }, [working, handleAgentEvent]);

  const run = useCallback(async (from: Step, line?: string) => {
    setError(null);
    try {
      if (from === "fault" && line) {
        working(STEP.fault, `Fault on line ${line}: the breaker trips and the feeder goes dark`);
        await api.fault(line);
        setFault(line);
        await refresh();
        flash([line], "fault");
        await pause(1100);
        if (mode === "agent") {
          await runWithAgent();
          return;
        }
      }
      if (from !== "plan") {
        working(STEP.locate, "Following the chain of tripped fault indicators");
        const found = await api.locate();
        setLocation(found);
        for (const id of found.chain) {
          flash([id], "fi");
          await pause(600);
        }
        await refresh();
        await pause(400);
        working(STEP.isolate, "Opening the faulted zone's boundary switches and reclosing the breaker");
        const isolation = await api.isolate();
        await replay(isolation.ops);
        await refresh();
        setTrace(await api.trace());
        await pause(900);
      }
      working(STEP.verify, "Generating restoration plans and verifying each one on a forked graph");
      const result = await api.plan();
      setPlan(result);
      setSelected(result.recommended);
      setTrace(await api.trace());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [refresh, working, flash, replay, mode, runWithAgent]);

  const approve = useCallback(async (planId: string) => {
    setError(null);
    working(STEP.approve, `Executing ${planId} on the live graph`);
    try {
      const execution = await api.execute(planId);
      setExecuted(planId);
      setSelected(null);
      await replay(execution.ops);
      await refresh();
      setTrace(await api.trace());
      setExplanation(await api.explain());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [refresh, working, replay]);

  const resetGrid = useCallback(async () => {
    setError(null);
    working(STEP.fault, "Restoring the healthy feeder");
    try {
      await api.reset();
      setFault(null);
      setLocation(null);
      setPlan(null);
      setSelected(null);
      setExecuted(null);
      setTrace(null);
      setExplanation(null);
      setAgentEvents([]);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [refresh, working]);

  useEffect(() => {
    api.agentInfo()
      .then((info) => setAgentModel(info.available ? info.model : null))
      .catch(() => setAgentModel(null));
  }, []);

  // Pick up an incident already in progress, e.g. after a page reload.
  useEffect(() => {
    refresh()
      .then(async (s) => {
        const faulted = s.lines.find((l) => l.faulted);
        if (faulted) setFault(faulted.id);
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
    : location ? STEP.isolate
    : STEP.locate;

  const message = busy ? busy.message
    : healthy ? "The feeder is healthy. Pick a demo fault or click any line segment on the diagram."
    : status === "restored" ? "Service restored. The explanation below is read back from the decision trace."
    : nothingPassed ? "No candidate passed verification, so nothing unsafe is executed; the fault stays isolated."
    : plan ? "Hover a candidate to preview its switching on the diagram, then approve one that passed."
    : status === "isolated" ? <button onClick={() => run("plan")}>Generate and verify plans</button>
    : fault ? <button onClick={() => run("locate")}>Locate and isolate</button>
    : null;

  return (
    <main>
      <TopBar page="feeder">
        <button className="secondary" disabled={busy !== null || healthy} onClick={resetGrid}>
          Reset feeder
        </button>
      </TopBar>
      {error && <p className="error">{error}</p>}
      {state && <StatusStrip state={state} trace={trace} />}
      <Stepper
        done={done}
        active={busy?.step ?? null}
        elapsed={Math.round((now - startedAt) / 1000)}
        message={message}
      />

      <div className="layout">
        <section className="card diagram-card">
          <div className="card-head">
            <h2>IEEE 33-bus feeder</h2>
            <span className="muted small">hover any bus, line or switch for details</span>
            <div className="view-switch" role="radiogroup" aria-label="Diagram view">
              <button role="radio" aria-checked={!showRisk} className={!showRisk ? "on" : undefined}
                onClick={() => toggleRisk(false)}>Live</button>
              <button role="radio" aria-checked={showRisk} className={showRisk ? "on" : undefined}
                onClick={() => toggleRisk(true)}>Risk map</button>
            </div>
          </div>
          {state && (
            <FeederDiagram
              state={state}
              highlight={highlight}
              pulse={pulse}
              problems={problems}
              risk={showRisk ? risk : null}
              onSegmentClick={healthy && !busy ? (line) => run("fault", line) : undefined}
            />
          )}
          {showRisk && (
            <div className="risk-legend">
              <span className="muted small">If this line faults, load still dark after the best safe restoration:</span>
              <div className="risk-scale">
                {RISK_STEPS.map(([from, color], i) => (
                  <span key={from}>
                    <i style={{ background: color }} />
                    {i + 1 < RISK_STEPS.length ? `${from}-${RISK_STEPS[i + 1][0]}` : `${from}+`} kW
                  </span>
                ))}
              </div>
              <span className="muted small">From the 23-fault evaluation. Hover a line for the critical loads it would cost.</span>
            </div>
          )}
          <ul className="legend">
            <li><span className="swatch live" /> energized</li>
            <li><span className="swatch dark" /> de-energized</li>
            <li><span className="swatch faulted" /> faulted zone</li>
            <li><span className="box closed" /> closed switch</li>
            <li><span className="box open" /> open switch</li>
            <li><span className="dash" /> tie</li>
            <li><span className="box fi" /> indicator set</li>
            <li><span className="box to-close" /> plan closes</li>
            <li><span className="box to-open" /> plan opens</li>
            <li><span className="problem-mark" /> why rejected</li>
            <li><span className="star">★</span> critical load</li>
          </ul>
        </section>

        {healthy ? (
          <Intro
            disabled={busy !== null}
            onFault={(line) => run("fault", line)}
            agentModel={agentModel}
            mode={mode}
            onMode={setMode}
          />
        ) : (
          <Timeline fault={fault} location={location} trace={trace} />
        )}
      </div>

      {(agentRunning || agentEvents.length > 0) && (
        <AgentPanel model={agentModel} events={agentEvents} running={agentRunning} />
      )}
      {plan && (
        <section className="card">
          <div className="card-head">
            <h2>Restoration candidates</h2>
            <span className="muted small">
              ranked by weighted restored load; each checked on its own copy of the graph.
              Hover a rejected plan to see the problem on the diagram
            </span>
          </div>
          {plan.candidates.length === 0 ? (
            <p className="muted">No dead zone can be reached from a live one, so there is nothing to restore.</p>
          ) : (
            <CandidateList
              candidates={plan.candidates}
              recommended={plan.recommended}
              selected={selected}
              executed={executed}
              busy={busy !== null}
              onSelect={(id) => !executed && setSelected(id)}
              onApprove={approve}
            />
          )}
        </section>
      )}
      {explanation && <ExplanationPanel e={explanation} />}
    </main>
  );
}
