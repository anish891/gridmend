"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import CandidateList from "@/components/CandidateList";
import ExplanationPanel from "@/components/ExplanationPanel";
import FeederDiagram from "@/components/FeederDiagram";
import Timeline from "@/components/Timeline";
import { api, type Explanation, type GridState, type Location, type PlanResult, type Trace } from "@/lib/api";

// Short pauses between automatic steps so each one is visible on the diagram.
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

type Step = "fault" | "locate" | "plan";

export default function Home() {
  const [state, setState] = useState<GridState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fault, setFault] = useState<string | null>(null);
  const [location, setLocation] = useState<Location | null>(null);
  const [plan, setPlan] = useState<PlanResult | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [executed, setExecuted] = useState<string | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [explanation, setExplanation] = useState<Explanation | null>(null);

  const refresh = useCallback(async () => {
    const next = await api.state();
    setState(next);
    return next;
  }, []);

  const run = useCallback(async (from: Step, line?: string) => {
    setError(null);
    try {
      if (from === "fault" && line) {
        setBusy(`Fault on ${line}: breaker tripped`);
        await api.fault(line);
        setFault(line);
        await refresh();
        await pause(900);
      }
      if (from !== "plan") {
        setBusy("Locating the fault from the indicator chain");
        setLocation(await api.locate());
        await refresh();
        await pause(900);
        setBusy("Isolating the faulted zone");
        await api.isolate();
        await refresh();
        setTrace(await api.trace());
        await pause(600);
      }
      setBusy("Generating candidates and verifying each one on a forked graph");
      const result = await api.plan();
      setPlan(result);
      setSelected(result.recommended);
      setTrace(await api.trace());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [refresh]);

  const approve = useCallback(async (planId: string) => {
    setError(null);
    setBusy(`Executing ${planId}`);
    try {
      await api.execute(planId);
      setExecuted(planId);
      setSelected(null);
      await refresh();
      setTrace(await api.trace());
      setExplanation(await api.explain());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [refresh]);

  const resetGrid = useCallback(async () => {
    setError(null);
    setBusy("Resetting the feeder");
    try {
      await api.reset();
      setFault(null);
      setLocation(null);
      setPlan(null);
      setSelected(null);
      setExecuted(null);
      setTrace(null);
      setExplanation(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [refresh]);

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
  const highlight = plan?.candidates.find((c) => c.plan_id === selected)?.ops ?? [];
  const status = state?.incident?.status;

  return (
    <main>
      <header>
        <h1>GridMend</h1>
        <nav>
          <Link href="/" className="active">Feeder</Link>
          <Link href="/results">Results</Link>
        </nav>
        <button className="secondary" disabled={busy !== null || healthy} onClick={resetGrid}>
          Reset feeder
        </button>
      </header>
      {error && <p className="error">{error}</p>}

      <div className="layout">
        <div className="main-column">
          {state && (
            <FeederDiagram
              state={state}
              highlight={highlight}
              onSegmentClick={healthy && !busy ? (line) => run("fault", line) : undefined}
            />
          )}
          <ul className="legend">
            <li><span className="swatch live" /> energized</li>
            <li><span className="swatch dark" /> de-energized</li>
            <li><span className="swatch faulted" /> faulted zone</li>
            <li><span className="box closed" /> switch closed</li>
            <li><span className="box open" /> switch open</li>
            <li><span className="box fi" /> indicator set</li>
            <li><span className="dash" /> tie</li>
            <li><span className="box to-close" /> plan closes</li>
            <li><span className="box to-open" /> plan opens</li>
            <li><span className="star">★</span> critical load</li>
          </ul>

          <div className="status-bar">
            {busy && <span className="spinner" />}
            {busy ??
              (healthy
                ? "Healthy. Click any line segment to fault it."
                : fault && !status && !plan
                  ? <button onClick={() => run("locate")}>Locate and isolate</button>
                  : status === "isolated" && !plan
                    ? <button onClick={() => run("plan")}>Generate restoration plans</button>
                    : plan && !executed
                      ? "Hover a candidate to preview its switching; approve one that passed."
                      : status === "restored"
                        ? "Restored. The explanation below is built from the decision trace."
                        : null)}
          </div>

          {plan && (
            <section className="card">
              <h2>Restoration candidates</h2>
              {plan.candidates.length === 0 ? (
                <p className="muted">No dead zone can be reached from a live one.</p>
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
        </div>

        {state && (
          <Timeline
            fault={fault}
            location={location}
            trace={trace}
            servedKw={state.served_kw}
            totalKw={state.total_kw}
          />
        )}
      </div>
    </main>
  );
}
