"use client";

import { useCallback, useEffect, useState } from "react";

import AgentPanel from "@/components/AgentPanel";
import CandidateList from "@/components/CandidateList";
import ExplanationPanel from "@/components/ExplanationPanel";
import FaultPicker, { type Mode } from "@/components/FaultPicker";
import FeederDiagram, { RISK_STEPS, type Pulse, type Risk } from "@/components/FeederDiagram";
import Intro from "@/components/Intro";
import StatusStrip from "@/components/StatusStrip";
import Stepper from "@/components/Stepper";
import Timeline from "@/components/Timeline";
import TopBar from "@/components/TopBar";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import Segmented from "@/components/ui/Segmented";
import Term from "@/components/ui/Term";
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
    : status === "restored" ? "Power is back wherever it is safe. The explanation below is read from the decision trace."
    : plan?.candidates.length === 0 ? "No backup route reaches the dark sections, so the fault stays isolated."
    : nothingPassed ? "No plan passed the safety checks, so nothing risky was switched. The fault stays isolated."
    : plan ? "Select a plan to preview its switching on the map, then approve one that passed."
    : status === "isolated" ? "The fault is cut out and everything upstream has power. Next, find routes for the rest."
    : fault ? "The substation breaker tripped and the whole feeder is dark."
    : null;

  // The one next step for the operator, when it is theirs to take.
  const action = busy || status === "restored" || plan ? null
    : status === "isolated" ? <Button onClick={() => run("plan")}>Find restoration plans</Button>
    : fault ? <Button onClick={() => run("locate")}>Locate and isolate the fault</Button>
    : null;

  const picking = healthy && !busy;

  return (
    <main>
      <TopBar page="feeder">
        <Button variant="secondary" disabled={busy !== null || healthy} onClick={resetGrid}>
          Reset feeder
        </Button>
      </TopBar>
      {error && <p className="error" role="alert">{error}</p>}
      {picking ? (
        <FaultPicker
          disabled={busy !== null}
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
          action={action}
        />
      )}
      {state && <StatusStrip state={state} trace={trace} />}

      <div className="layout">
        <Card
          className="diagram-card"
          title="Feeder map"
          hint={picking ? "IEEE 33-bus feeder. Click a line to fault it, hover anything for details"
            : "IEEE 33-bus feeder. Hover anything for details"}
          actions={
            <Segmented
              label="Diagram view"
              value={showRisk ? "risk" : "live"}
              onChange={(view) => toggleRisk(view === "risk")}
              options={[{ value: "live", label: "Live" }, { value: "risk", label: "Risk map" }]}
            />
          }
        >
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
          <Timeline fault={fault} location={location} trace={trace} />
        )}
      </div>

      {(agentRunning || agentEvents.length > 0) && (
        <AgentPanel model={agentModel} events={agentEvents} running={agentRunning} />
      )}
      {plan && (
        <Card
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
              busy={busy !== null}
              onSelect={(id) => !executed && setSelected(id)}
              onApprove={approve}
            />
          )}
        </Card>
      )}
      {explanation && <ExplanationPanel e={explanation} />}
    </main>
  );
}
