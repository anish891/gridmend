"use client";

import { useCallback, useEffect, useState } from "react";

import Cypher from "@/components/Cypher";
import TopBar from "@/components/TopBar";
import { api, type QueryStep } from "@/lib/api";

function cell(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function StepCard({ q }: { q: QueryStep }) {
  return (
    <section className="card query">
      <div className="query-head">
        <span className="step-chip">{q.step}</span>
        <h2>{q.title}</h2>
        {q.ms !== undefined && <span className="muted small">{q.ms} ms</span>}
      </div>
      <p className="muted">{q.purpose}</p>
      <div className="query-body">
        <div>
          <Cypher code={q.cypher} />
          {Object.keys(q.params).length > 0 && (
            <div className="params">
              {Object.entries(q.params).map(([k, v]) => (
                <span key={k} className="param">${k} = {String(v)}</span>
              ))}
            </div>
          )}
        </div>
        <div className="query-result">
          {q.skipped ? (
            <p className="muted">Not run: {q.skipped}.</p>
          ) : q.rows && q.rows.length > 0 ? (
            <>
              <table>
                <thead>
                  <tr>{q.columns!.map((c) => <th key={c}>{c}</th>)}</tr>
                </thead>
                <tbody>
                  {q.rows.map((row, i) => (
                    <tr key={i}>{row.map((v, j) => <td key={j}>{cell(v)}</td>)}</tr>
                  ))}
                </tbody>
              </table>
              <p className="muted small">
                {q.total_rows} row{q.total_rows === 1 ? "" : "s"}
                {q.total_rows! > q.rows.length && `, first ${q.rows.length} shown`}
              </p>
            </>
          ) : (
            <p className="muted">{q.when_empty ?? "No rows right now."}</p>
          )}
        </div>
      </div>
    </section>
  );
}

function ForkCard() {
  return (
    <section className="card query">
      <div className="query-head">
        <span className="step-chip">Verify</span>
        <h2>A fork per plan</h2>
      </div>
      <p className="muted">
        Before any switch moves, each candidate is applied to its own copy of the live graph. The
        checks (isolation, connectivity, radiality, line loading and a pandapower power flow) run on the
        copy, the outcome is written back as a Plan with its steps, violations and the zones it restores,
        and the copy is deleted.
      </p>
      <Cypher code={"GRAPH.COPY grid_live fork_<planId>\n-- apply the plan's switch operations on the fork, run the checks\nGRAPH.DELETE fork_<planId>"} />
    </section>
  );
}

export default function GraphPage() {
  const [steps, setSteps] = useState<QueryStep[] | null>(null);
  const [graph, setGraph] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.queries();
      setSteps(result.steps);
      setGraph(result.graph);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const verifyAt = steps?.findIndex((q) => q.key === "loading") ?? -1;

  return (
    <main>
      <TopBar page="graph">
        <button className="secondary" disabled={loading} onClick={load}>
          {loading ? "Running…" : "Run again"}
        </button>
      </TopBar>
      {error && <p className="error">{error}</p>}
      <section className="card intro-graph">
        <h2>The graph is the agent&apos;s working memory</h2>
        <p className="muted">
          Each step of fault location, isolation and restoration is a Cypher query over the feeder twin in
          FalkorDB. Everything below runs live against <code>{graph || "grid_live"}</code>, read-only or on a
          throwaway copy of it, so it shows the graph exactly as the agent sees it now. Fault a line on the Feeder tab and come back to
          see the incident queries fill in.
        </p>
      </section>
      {steps?.map((q, i) => (
        <div key={q.key}>
          {i === verifyAt && <ForkCard />}
          <StepCard q={q} />
        </div>
      ))}
    </main>
  );
}
