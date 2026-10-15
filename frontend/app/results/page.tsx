"use client";

import { useEffect, useState } from "react";

import ResultsChart from "@/components/ResultsChart";
import TopBar from "@/components/TopBar";
import { api, type Results } from "@/lib/api";

export default function ResultsPage() {
  const [results, setResults] = useState<Results | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.results().then(setResults).catch((e: Error) => setError(e.message));
  }, []);

  const methods = results?.summary.map((s) => s.method) ?? [];
  const faults = [...new Set(results?.rows.map((r) => r.fault) ?? [])];

  return (
    <main>
      <TopBar page="results" />
      {error && <p className="error">{error}</p>}

      {results && (
        <>
          <section className="card">
            <div className="card-head">
              <h2>All 23 single faults</h2>
              <span className="muted small">IEEE 33-bus feeder, every unswitched segment faulted in turn</span>
            </div>
            <table>
              <thead>
                <tr>
                  <th>method</th>
                  <th>restored kW</th>
                  <th>% of optimal</th>
                  <th>critical loads</th>
                  <th>switch ops</th>
                  <th>violations</th>
                  <th>unsafe plans</th>
                  <th>mean planning ms</th>
                </tr>
              </thead>
              <tbody>
                {results.summary.map((s) => (
                  <tr key={s.method} className={s.method === "gridmend" ? "highlight" : undefined}>
                    <td>{s.method}</td>
                    <td>{Math.round(s.restored_kw)}</td>
                    <td>{s.pct_optimal.toFixed(1)}</td>
                    <td>{s.critical}</td>
                    <td>{s.switch_ops}</td>
                    <td className={s.violations ? "bad" : undefined}>{s.violations}</td>
                    <td className={s.unsafe_plans ? "bad" : undefined}>{s.unsafe_plans}</td>
                    <td>{Math.round(s.mean_plan_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted">
              Unsafe switching is scored as restoring nothing: protection would trip it. GridMend&apos;s
              executed configurations are re-checked by an independent networkx and pandapower oracle.
            </p>
          </section>

          <section className="card">
            <h2>Restored load against the optimum</h2>
            <ResultsChart rows={results.rows} />
          </section>

          <section className="card">
            <h2>Restored kW per fault (violations in brackets)</h2>
            <table>
              <thead>
                <tr>
                  <th>fault</th>
                  {methods.map((m) => <th key={m}>{m}</th>)}
                </tr>
              </thead>
              <tbody>
                {faults.map((fault) => (
                  <tr key={fault}>
                    <td>{fault}</td>
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
          </section>
        </>
      )}
    </main>
  );
}
