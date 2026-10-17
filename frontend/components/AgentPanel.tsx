import { VIOLATION_LABEL } from "@/components/CandidateList";
import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import Spinner from "@/components/ui/Spinner";
import type { AgentEvent, SwitchOp, ZoneSwitch } from "@/lib/api";

export type ToolEvent = Extract<AgentEvent, { type: "tool" }>;

// One line per tool call, in the operator's words.
export function describe(e: ToolEvent): { label: string; detail: string; bad?: boolean } {
  const r = e.result as Record<string, any>;
  if (r.error) return { label: e.name, detail: String(r.error), bad: true };
  switch (e.name) {
    case "grid_status":
      return { label: "Checked grid status", detail: `${r.load_served_kw} of ${r.total_load_kw} kW served` };
    case "declare_fault":
      return {
        label: `Located the fault in ${r.faulted_zones.map((z: number) => `Z${z}`).join(" and ")}`,
        detail: String(e.args.evidence ?? ""),
      };
    case "execute_isolation":
    case "execute_restoration":
      if (r.executed === false) return { label: "Asked for approval", detail: String(r.reason) };
      return {
        label: r.breaker_tripped_again ? "Switched, and the breaker tripped again"
          : e.name === "execute_isolation" ? "Isolated the fault" : "Restored power",
        detail: r.executed.join(", "),
        bad: Boolean(r.breaker_tripped_again),
      };
    case "line_dependencies":
      return { label: "Checked what depends on each line", detail: `${r.lines.length} lines` };
    case "transfer_capacity":
      return {
        label: "Computed the transfer limit",
        detail: `${r.deliverable_kva} of ${r.demand_kva} kVA deliverable${r.bottlenecks.length ? `, limited by ${r.bottlenecks.join(", ")}` : ""}`,
      };
    case "zone_map":
      return {
        label: "Read the zone map",
        detail: `${r.zones.filter((z: { state: string }) => z.state === "dark").length} healthy zones dark, `
          + `${r.switches.filter((w: { indicator?: string }) => w.indicator === "set").length} indicators set`,
      };
    case "simulate_plan": {
      const verdict = r.passed ? "passed" : `rejected (${r.violations.map((v: string) => v.split(":")[0]).join(", ")})`;
      const what = r.purpose === "isolation" ? "isolation plan" : "restoration design";
      return { label: `Tested its ${what} ${r.plan_id} on a fork`, detail: `${verdict}, ${r.restored_kw} kW`, bad: !r.passed };
    }
    case "recommend_plan":
      return { label: `Recommended ${r.recommended}`, detail: String(e.args.reason ?? "") };
    case "explain_plan":
      return { label: "Read the decision trace", detail: "" };
    case "field_reports":
      return { label: "Read field crew reports", detail: "" };
    case "feeding_path":
      return {
        label: `Traced the feed to bus ${r.bus}${r.facility ? ` (${r.facility})` : ""}`,
        detail: r.energized ? r.switches.join(" → ") : "no power",
      };
    case "fault_impact":
      return r.segments
        ? { label: "Ranked every line by what a fault would cost", detail: `worst: ${r.segments[0].line}, ${r.segments[0].still_dark_kw} kW dark` }
        : { label: `Looked up a fault on ${r.line}`, detail: `${r.still_dark_kw} kW stays dark` };
    default:
      return { label: e.name, detail: "" };
  }
}

// The model writes light markdown; keep headings, paragraphs, lists, tables, bold, italics and code.
export function Answer({ text }: { text: string }) {
  const inline = (line: string) =>
    line.split(/(\*\*.+?\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g).map((part, i) => {
      if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <b key={i}>{part.slice(2, -2)}</b>;
      if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
      if (part.startsWith("*") && part.endsWith("*") && part.length > 2) return <i key={i}>{part.slice(1, -1)}</i>;
      return part;
    });
  const cells = (line: string) => line.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const blocks: React.ReactNode[] = [];
  let items: string[] = [];
  let ordered = false;
  let rows: string[][] = [];
  const flush = () => {
    if (items.length) {
      const list = items.map((t, i) => <li key={i}>{inline(t)}</li>);
      blocks.push(ordered ? <ol key={blocks.length}>{list}</ol> : <ul key={blocks.length}>{list}</ul>);
    }
    if (rows.length) {
      const [head, ...body] = rows;
      blocks.push(
        <table key={blocks.length}>
          <thead><tr>{head.map((c, i) => <th key={i}>{inline(c)}</th>)}</tr></thead>
          <tbody>{body.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody>
        </table>,
      );
    }
    items = [];
    rows = [];
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (/^\|?[\s:-]+(\|[\s:-]+)+\|?$/.test(line)) continue;
    const bullet = /^[-*•]\s+/.test(line);
    const numbered = /^\d+[.)]\s+/.test(line);
    if (line.startsWith("|")) {
      if (items.length) flush();
      rows.push(cells(line));
    } else if (bullet || numbered) {
      if (rows.length || (items.length && ordered !== numbered)) flush();
      ordered = numbered;
      items.push(line.replace(/^([-*•]|\d+[.)])\s+/, ""));
    } else {
      flush();
      if (/^#+\s/.test(line)) blocks.push(<h3 key={blocks.length}>{inline(line.replace(/^#+\s*/, ""))}</h3>);
      else if (line && !/^-{3,}$/.test(line)) blocks.push(<p key={blocks.length}>{inline(line)}</p>);
    }
  }
  flush();
  return <div className="agent-answer">{blocks}</div>;
}

type Result = Record<string, any>;
type Call = { name: string; args: Record<string, any>; result: Result };
type StageState = "pending" | "active" | "done" | "failed";

function opsOf(args: Record<string, any>): SwitchOp[] {
  return [
    ...((args.open ?? []) as string[]).map((switch_id) => ({ op: "open" as const, switch_id })),
    ...((args.close ?? []) as string[]).map((switch_id) => ({ op: "close" as const, switch_id })),
  ];
}

function Ops({ ops }: { ops: SwitchOp[] }) {
  return (
    <div className="ops" aria-label="Switching">
      {ops.map((o) => <span key={o.switch_id} className={`op ${o.op}`}>{o.op} {o.switch_id}</span>)}
    </div>
  );
}

function Verdict({ r }: { r: Result }) {
  if (r.passed) return <Badge tone="success">passed</Badge>;
  const kinds = [...new Set((r.violations as string[]).map((v) => v.split(":")[0]))];
  return <>{kinds.map((k) => <Badge key={k} tone="danger">{VIOLATION_LABEL[k] ?? k}</Badge>)}</>;
}

function Stage({ n, title, state, summary, children }: {
  n: number; title: string; state: StageState; summary?: string; children?: React.ReactNode;
}) {
  return (
    <li className={`stage ${state}`}>
      <div className="stage-head">
        <span className="stage-dot" aria-hidden="true">
          {state === "active" ? <Spinner /> : state === "done" ? "✓" : state === "failed" ? "!" : n}
        </span>
        <div>
          <b>{title}</b>
          {summary && <div className="muted small">{summary}</div>}
        </div>
      </div>
      {children && <div className="stage-body">{children}</div>}
    </li>
  );
}

type Props = {
  model: string | null;
  events: AgentEvent[];
  running: boolean;
  // Once the run is over, a design can be shown on the map.
  selected?: string | null;
  onPreview?: (planId: string) => void;
};

// The agent's work as the decisions it made, built from its tool calls as they stream in:
// where it located the fault and why, how it isolated it, each restoration design and why it
// passed or failed, and what it chose.
export default function AgentPanel({ model, events, running, selected = null, onPreview }: Props) {
  const tools = events.filter((e): e is ToolEvent => e.type === "tool");
  const calls: Call[] = tools.filter((e) => !(e.result as Result).error)
    .map((e) => ({ name: e.name, args: e.args as Record<string, any>, result: e.result as Result }));
  const final = events.find((e) => e.type === "final");
  const error = events.find((e) => e.type === "error");
  const finished = !running;

  const readings = calls.filter((c) => c.name === "zone_map"
    && (c.result.switches as ZoneSwitch[]).some((s) => s.kind === "breaker" && s.status === "open"));
  const indicators = readings.length
    ? (readings.at(-1)!.result.switches as ZoneSwitch[]).filter((s) => s.indicator === "set").map((s) => s.id)
    : [];
  const declared = calls.filter((c) => c.name === "declare_fault");
  const isolationTests = calls.filter((c) => c.name === "simulate_plan" && c.result.purpose === "isolation");
  const isolations = calls.filter((c) => c.name === "execute_isolation");
  const designs = calls.filter((c) => c.name === "simulate_plan" && c.result.purpose === "restoration");
  const recommended = calls.find((c) => c.name === "recommend_plan");
  const restored = calls.find((c) => c.name === "execute_restoration" && c.result.executed !== false);
  const chosenId = recommended?.result.recommended ?? (restored ? restored.args.plan_id : null);
  const lastIsolation = isolations.at(-1);
  const isolated = Boolean(lastIsolation && !lastIsolation.result.breaker_tripped_again);
  const passed = designs.filter((d) => d.result.passed).length;

  const stateOf = (done: boolean, started: boolean): StageState =>
    done ? "done" : started && running ? "active" : started && finished ? "failed" : "pending";

  return (
    <Card className="agent-panel" title="Agent decisions"
      hint={model ? `${model} · isolates on its own; you approve the restoration` : undefined}>
      <ol className="stages">
        <Stage n={1} title="Locate the fault" state={stateOf(declared.length > 0, running || tools.length > 0)}
          summary={indicators.length ? `${indicators.length} fault indicators read set` : "reading the fault indicators"}>
          {indicators.length > 0 && (
            <div className="ops" aria-label="Indicators set">
              {indicators.map((id) => <span key={id} className="op fi">{id}</span>)}
            </div>
          )}
          {declared.map((d, i) => (
            <div key={i} className="decision-note">
              {i > 0 && <div className="small muted">After the breaker tripped again:</div>}
              <b>Declared {(d.result.faulted_zones as number[]).map((z) => `Z${z}`).join(" and ")}</b>
              <q>{String(d.args.evidence ?? "")}</q>
            </div>
          ))}
        </Stage>

        <Stage n={2} title="Isolate it" state={stateOf(isolated, declared.length > 0)}
          summary={isolated ? "breaker reclosed and held" : isolationTests.length ? "testing isolation on a fork" : undefined}>
          {isolationTests.map((t) => (
            <div key={t.result.plan_id} className="attempt">
              <div className="attempt-head"><code>{t.result.plan_id}</code> <Verdict r={t.result} /></div>
              <Ops ops={opsOf(t.args)} />
            </div>
          ))}
          {isolations.map((x, i) => (
            <div key={i} className={x.result.breaker_tripped_again ? "decision-note bad" : "decision-note"}>
              {x.result.breaker_tripped_again
                ? "Carried out, but power reached a fault and the breaker tripped again: back to locating."
                : "Carried out on the live grid; the breaker held."}
              {((x.result.cut_off_with_fault ?? []) as { zone: number; jammed_switch: string }[]).map((c) => (
                <div key={c.zone} className="small">
                  Z{c.zone} stays dark with the fault: jammed {c.jammed_switch} ties it to the faulted zone.
                </div>
              ))}
            </div>
          ))}
        </Stage>

        <Stage n={3} title="Design the restoration" state={stateOf(chosenId !== null || (finished && designs.length > 0 && passed > 0), isolated)}
          summary={designs.length ? `${designs.length} design${designs.length > 1 ? "s" : ""} tested, ${passed} passed` : undefined}>
          {designs.length > 0 && (
            <ol className="designs">
              {designs.map((d, i) => {
                const r = d.result;
                return (
                  <li key={r.plan_id} className={["design", r.passed ? "pass" : "fail", r.plan_id === chosenId && "chosen",
                    r.plan_id === selected && "previewed"].filter(Boolean).join(" ")}>
                    <div className="attempt-head">
                      <b>Design {i + 1}</b> <code>{r.plan_id}</code> <Verdict r={r} />
                      {r.plan_id === chosenId && <Badge tone="accent" solid>chosen</Badge>}
                    </div>
                    <Ops ops={opsOf(d.args)} />
                    <div className="small muted">
                      {r.restored_kw} kW
                      {(r.critical_loads_restored as string[]).length > 0 && `, ${(r.critical_loads_restored as string[]).join(", ")}`}
                      {r.max_loading_pct != null && ` · busiest line ${r.max_loading_pct}%`}
                      {r.min_voltage_pu != null && ` · lowest ${r.min_voltage_pu} pu`}
                    </div>
                    {!r.passed && <div className="small violation-text">{(r.violations as string[])[0]}</div>}
                    {onPreview && (
                      <button type="button" className="link-button" onClick={() => onPreview(r.plan_id)}>
                        {r.plan_id === selected ? "Shown on the map" : "Show on the map"}
                      </button>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
        </Stage>

        <Stage n={4} title="Decide" state={chosenId ? "done" : final ? "failed" : "pending"}
          summary={chosenId ? undefined : final ? "no design passed, so nothing more was switched" : undefined}>
          {recommended && (
            <div className="decision-note chosen">
              <b>Recommends {recommended.result.recommended}</b>: {String(recommended.args.reason ?? "")}
            </div>
          )}
          {restored && <div className="decision-note chosen"><b>Restored with {restored.args.plan_id}</b></div>}
        </Stage>
      </ol>

      {error && error.type === "error" && <p className="error" role="alert">{error.detail}</p>}
      {final && final.type === "final" && (
        <details className="briefing" open>
          <summary>The agent&apos;s briefing</summary>
          <Answer text={final.text} />
        </details>
      )}
      <details className="tool-log">
        <summary>Every tool call ({tools.length})</summary>
        <ol className="agent-log">
          {tools.map((e, i) => {
            const d = describe(e);
            return (
              <li key={i} className={d.bad ? "bad" : undefined}>
                <code>{e.name}</code> <b>{d.label}</b> <span className="muted">{d.detail}</span>
              </li>
            );
          })}
        </ol>
      </details>
    </Card>
  );
}
