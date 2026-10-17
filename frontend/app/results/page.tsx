"use client";

import { useEffect, useState } from "react";

import ResultsChart, { MAX_FAULTS } from "@/components/ResultsChart";
import TopBar from "@/components/TopBar";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import Spinner from "@/components/ui/Spinner";
import Term from "@/components/ui/Term";
import { api, type Results } from "@/lib/api";
import { currentFeeder, FEEDERS, type FeederConfig } from "@/lib/feeders";
import { METHODS, methodLabel } from "@/lib/methods";

// Baselines plan in milliseconds; the agent works an incident in tens of seconds.
const duration = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);

export default function ResultsPage() {
  const [results, setResults] = useState<Results | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showFaults, setShowFaults] = useState(false);
  const [feeder, setFeeder] = useState<FeederConfig>(FEEDERS.ieee33);

  useEffect(() => {
    setFeeder(FEEDERS[currentFeeder()]);
    api.results().then(setResults).catch((e: Error) => setError(e.message));
  }, []);

  const methods = results?.summary.map((s) => s.method) ?? [];
  const ours = results?.summary.find((s) => s.method === "agent");
  const best = results?.summary.find((s) => s.method === "optimal");
  // Every baseline that switches without verification, for the safety comparison.
  const unverified = results?.summary.filter((s) => s.method === "greedy" || s.method === "llm-only") ?? [];
  const faults = [...new Set(results?.rows.map((r) => r.fault) ?? [])];

  return (
    <main>
      <TopBar page="results" />

      <header className="page-intro">
        <h1>How GridMend compares</h1>
        <p className="muted">
          On the {feeder.label}, each of the {faults.length || "its"} line segments was faulted in turn, and{" "}
          {methods.length || "several"} methods tried to restore power. Unsafe switching counts as restoring nothing, because protection
          would trip it.
        </p>
      </header>

      {error ? (
        <Card>
          {error.includes("no evaluation results") ? (
            <EmptyState title="No results yet">
              Run <code>gridmend eval</code> to fault every segment and score each method, then reload this page.
            </EmptyState>
          ) : (
            <EmptyState title="Could not load the results">
              {error}. Check that the API is running on port 8000, then reload.
            </EmptyState>
          )}
        </Card>
      ) : !results ? (
        <Card>
          <div className="loading" role="status">
            <Spinner /> Loading the evaluation…
          </div>
        </Card>
      ) : (
        <>
          {ours && (
            <section className="headline" aria-label="GridMend at a glance">
              <div className="kpi">
                <div className="kpi-label">Load restored</div>
                {best ? (
                  <>
                    <div className="kpi-big">{ours.pct_optimal.toFixed(0)}%</div>
                    <div className="muted small">
                      of the best possible: {Math.round(ours.restored_kw)} of {Math.round(best.restored_kw)} kW
                      across all faults
                    </div>
                  </>
                ) : (
                  <>
                    <div className="kpi-big">{(ours.restored_kw / 1000).toFixed(0)} <span className="muted">MW</span></div>
                    <div className="muted small">summed over every fault, each plan checked on a fork</div>
                  </>
                )}
              </div>
              <div className="kpi">
                <div className="kpi-label">Unsafe plans carried out</div>
                <div className="kpi-big">{ours.unsafe_plans}</div>
                <div className="muted small">
                  {unverified.length > 0
                    ? `vs. ${unverified.map((s) => `${s.unsafe_plans} for ${methodLabel(s.method)}`).join(", ")}`
                    : "every plan is checked first"}
                </div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Critical sites restored</div>
                <div className="kpi-big">
                  {ours.critical}
                  {best && <span className="muted"> / {best.critical}</span>}
                </div>
                <div className="muted small">hospital, fire station, water pumping, telecom</div>
              </div>
              <div className="kpi">
                <div className="kpi-label">Time per fault</div>
                <div className="kpi-big">{Math.round(ours.mean_plan_ms / 1000)} <span className="muted">s</span></div>
                <div className="muted small">on average for the agent to locate, isolate, design and check</div>
              </div>
            </section>
          )}

          <Card title="All methods" hint="Totals across every fault. Hover or tap a method to see how it works">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Method</th>
                    <th scope="col">Restored kW</th>
                    <th scope="col">% of best</th>
                    <th scope="col">Critical sites</th>
                    <th scope="col">Switch moves</th>
                    <th scope="col">
                      <Term hint="Safety rules broken: a loop, an overloaded line, low voltage, or power into the fault.">
                        Violations
                      </Term>
                    </th>
                    <th scope="col">Unsafe plans</th>
                    <th scope="col">Avg. time</th>
                  </tr>
                </thead>
                <tbody>
                  {results.summary.map((s) => (
                    <tr key={s.method} className={s.method === "agent" ? "highlight" : undefined}>
                      <th scope="row">
                        {METHODS[s.method] ? <Term hint={METHODS[s.method].hint}>{methodLabel(s.method)}</Term> : s.method}
                      </th>
                      <td>{Math.round(s.restored_kw)}</td>
                      <td>{best ? s.pct_optimal.toFixed(1) : "-"}</td>
                      <td>{s.critical}</td>
                      <td>{s.switch_ops}</td>
                      <td className={s.violations ? "bad" : undefined}>{s.violations}</td>
                      <td className={s.unsafe_plans ? "bad" : undefined}>{s.unsafe_plans}</td>
                      <td>{duration(s.mean_plan_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted small">
              {best
                ? "GridMend's switching is re-checked by an independent networkx and pandapower oracle."
                : "There is no optimum for this network: the brute-force oracle handles one substation, so GridMend's plans rest on the fork checks alone."}
            </p>
          </Card>

          <Card
            title="Restored load per fault"
            hint={best ? "Faults that leave some load restorable. Hover a bar for its value"
              : `The ${MAX_FAULTS} faults where GridMend restored the most. Hover a bar for its value`}
          >
            <ResultsChart rows={results.rows} />
          </Card>

          <Card
            title="Every fault in detail"
            hint="Restored kW per fault and method"
            actions={
              <Button
                variant="secondary"
                aria-expanded={showFaults}
                aria-controls="fault-table"
                onClick={() => setShowFaults((open) => !open)}
              >
                {showFaults ? "Hide faults" : `Show all ${faults.length} faults`}
              </Button>
            }
          >
            {showFaults && (
              <div id="fault-table">
                <p className="muted small">
                  Broken safety rules are in brackets.{" "}
                  {best ? "Greyed rows leave nothing restorable, so even the best possible restores 0 kW."
                    : "Greyed rows are faults where no method restored anything."}
                </p>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Faulted line</th>
                        {methods.map((m) => <th key={m} scope="col">{methodLabel(m)}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {faults.map((fault) => (
                        <tr key={fault}
                          className={results.rows.some((r) => r.fault === fault && (r.optimal_kw > 0 || r.restored_kw > 0))
                            ? undefined : "quiet"}>
                          <th scope="row">{fault}</th>
                          {methods.map((m) => {
                            const r = results.rows.find((row) => row.fault === fault && row.method === m);
                            if (!r) return <td key={m}>-</td>;
                            return (
                              <td key={m} className={r.violations ? "bad" : undefined}>
                                {Math.round(r.restored_kw)}
                                {r.violations > 0 && ` [${r.violations}]`}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </Card>
        </>
      )}
    </main>
  );
}
