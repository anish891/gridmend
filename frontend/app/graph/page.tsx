"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import Cypher from "@/components/Cypher";
import TopBar from "@/components/TopBar";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import Spinner from "@/components/ui/Spinner";
import Term from "@/components/ui/Term";
import { api, type QueryStep } from "@/lib/api";

// The fork card is not a query from the API; it sits in the list just before the first check.
const FORK = { key: "fork", step: "Verify", title: "A fork per plan" };

function cell(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function algorithms(cypher: string): string[] {
  return [...new Set(cypher.match(/algo\.\w+/g) ?? [])];
}

// "needs a located fault: fault a line first" reads as two short sentences.
function sentences(text: string): string {
  return text.split(": ").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(". ") + ".";
}

function QueryResult({ q }: { q: QueryStep }) {
  if (q.skipped) return <p className="query-empty">Not run yet. {sentences(q.skipped)}</p>;
  if (!q.rows || q.rows.length === 0) {
    return <p className="query-empty">{q.when_empty ?? "No rows right now."}</p>;
  }
  return (
    <>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>{q.columns!.map((c) => <th key={c} scope="col">{c}</th>)}</tr>
          </thead>
          <tbody>
            {q.rows.map((row, i) => (
              <tr key={i}>{row.map((v, j) => <td key={j}>{cell(v)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        {q.total_rows} row{q.total_rows === 1 ? "" : "s"}
        {q.total_rows! > q.rows.length && `, first ${q.rows.length} shown`}
      </p>
    </>
  );
}

function StepCard({ q }: { q: QueryStep }) {
  return (
    <Card className="query" id={`q-${q.key}`}>
      <div className="query-head">
        <Badge tone="accent">{q.step}</Badge>
        <h2>{q.title}</h2>
        {algorithms(q.cypher).map((a) => (
          <span key={a} className="algo-badge">
            <Term hint="A graph algorithm built into FalkorDB, called from Cypher.">{a}</Term>
          </span>
        ))}
        {q.ms !== undefined && <span className="muted small query-ms">ran in {q.ms} ms</span>}
      </div>
      <p className="query-purpose">{q.purpose}</p>
      <div className="query-body">
        <div>
          <h3>Query</h3>
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
          <h3>Live result</h3>
          <QueryResult q={q} />
        </div>
      </div>
    </Card>
  );
}

function ForkCard() {
  return (
    <Card className="query" id={`q-${FORK.key}`}>
      <div className="query-head">
        <Badge tone="accent">{FORK.step}</Badge>
        <h2>{FORK.title}</h2>
      </div>
      <p className="query-purpose">
        Before any switch moves, each plan is applied to its own copy of the live graph. The checks
        (isolation, connectivity, radiality, line loading and a pandapower power flow) run on the copy. The
        outcome is written back as a Plan with its steps, violations and the zones it restores, and the copy
        is deleted.
      </p>
      <Cypher code={"GRAPH.COPY grid_live fork_<planId>\n-- apply the plan's switch operations on the fork, run the checks\nGRAPH.DELETE fork_<planId>"} />
    </Card>
  );
}

type NavItem = { key: string; step: string; title: string; algo: boolean };

// Lists the steps grouped by stage, marking the one currently on screen.
function StepNav({ items, active }: { items: NavItem[]; active: string | null }) {
  const nav = useRef<HTMLElement>(null);

  // On narrow screens the list is a sideways-scrolling row; keep the current step in view.
  useEffect(() => {
    const row = nav.current;
    const link = row?.querySelector<HTMLElement>("a[aria-current]");
    if (!row || !link || row.scrollWidth <= row.clientWidth) return;
    row.scrollTo({ left: link.offsetLeft - 16, behavior: "smooth" });
  }, [active]);

  const groups = items.reduce<{ step: string; items: NavItem[] }[]>((all, item) => {
    const last = all.at(-1);
    if (last?.step === item.step) last.items.push(item);
    else all.push({ step: item.step, items: [item] });
    return all;
  }, []);
  return (
    <nav ref={nav} className="query-nav" aria-label="Query steps">
      <ol>
        {groups.map((g) => (
          <li key={g.step}>
            <span className="query-nav-group">{g.step}</span>
            <ol>
              {g.items.map((item) => (
                <li key={item.key}>
                  <a
                    href={`#q-${item.key}`}
                    aria-current={active === item.key ? "location" : undefined}
                    className={item.algo ? "has-algo" : undefined}
                  >
                    {item.title}
                  </a>
                </li>
              ))}
            </ol>
          </li>
        ))}
      </ol>
    </nav>
  );
}

export default function GraphPage() {
  const [steps, setSteps] = useState<QueryStep[] | null>(null);
  const [graph, setGraph] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState<string | null>(null);

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

  // Track which card sits near the top of the screen so the step list can mark it.
  useEffect(() => {
    if (!steps) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting);
        if (visible.length) setActive(visible[0].target.id.slice(2));
      },
      { rootMargin: "-15% 0px -75% 0px" },
    );
    document.querySelectorAll(".query[id^='q-']").forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [steps]);

  const verifyAt = steps?.findIndex((q) => q.key === "loading") ?? -1;
  const nav: NavItem[] = (steps ?? []).flatMap((q, i) => [
    ...(i === verifyAt ? [{ ...FORK, algo: false }] : []),
    { key: q.key, step: q.step, title: q.title, algo: algorithms(q.cypher).length > 0 },
  ]);

  return (
    <main>
      <TopBar page="graph">
        <Button variant="secondary" disabled={loading} onClick={load}>
          {loading ? "Running…" : "Run again"}
        </Button>
      </TopBar>

      <header className="page-intro">
        <h1>The graph is the agent&apos;s working memory</h1>
        <p className="muted">
          Every step GridMend takes is a Cypher query on the FalkorDB graph that mirrors the feeder. Each one
          below runs live against <code>{graph || "grid_live"}</code>, read-only or on a throwaway copy. To
          see the incident queries fill in, fault a line on the <Link href="/">Feeder page</Link> and come back.
        </p>
      </header>

      {error && steps && <p className="error" role="alert">Could not run the queries again: {error}</p>}

      {!steps ? (
        <Card>
          {error ? (
            <EmptyState title="Could not run the queries">
              {error}. Check that the API and FalkorDB are running, then use Run again.
            </EmptyState>
          ) : (
            <div className="loading" role="status">
              <Spinner /> Running the queries against the graph…
            </div>
          )}
        </Card>
      ) : (
        <div className="graph-layout">
          <StepNav items={nav} active={active ?? nav[0]?.key ?? null} />
          <div className="graph-steps">
            {steps.map((q, i) => (
              <div key={q.key}>
                {i === verifyAt && <ForkCard />}
                <StepCard q={q} />
              </div>
            ))}
          </div>
        </div>
      )}
    </main>
  );
}
