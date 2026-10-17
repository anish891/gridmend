import Card from "@/components/ui/Card";
import Spinner from "@/components/ui/Spinner";
import type { AgentEvent } from "@/lib/api";

export type ToolEvent = Extract<AgentEvent, { type: "tool" }>;

// One line per tool call, in the operator's words.
export function describe(e: ToolEvent): { label: string; detail: string; bad?: boolean } {
  const r = e.result as Record<string, any>;
  if (r.error) return { label: e.name, detail: String(r.error), bad: true };
  switch (e.name) {
    case "grid_status":
      return { label: "Checked grid status", detail: `${r.load_served_kw} of ${r.total_load_kw} kW served` };
    case "locate_fault":
      return { label: "Located the fault", detail: `Z${r.faulted_zone} via ${r.indicator_chain.join(" → ")}` };
    case "isolation_plan":
      return { label: "Isolated it", detail: r.executed.join(", ") };
    case "line_dependencies":
      return { label: "Checked what depends on each line", detail: `${r.lines.length} lines` };
    case "transfer_capacity":
      return {
        label: "Computed the transfer limit",
        detail: `${r.deliverable_kva} of ${r.demand_kva} kVA deliverable${r.bottlenecks.length ? `, limited by ${r.bottlenecks.join(", ")}` : ""}`,
      };
    case "restoration_candidates":
      return { label: "Asked for candidates", detail: `${r.candidates.length} ranked plans` };
    case "verify_plan": {
      const verdict = r.passed ? "passed" : `rejected (${r.violations.map((v: string) => v.split(":")[0]).join(", ")})`;
      return { label: `Verified ${r.plan_id} on a fork`, detail: `${verdict}, ${r.restored_kw} kW`, bad: !r.passed };
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
    case "what_if":
      return {
        label: `Planned as if ${r.switches.join(", ")} could not move`,
        detail: `${r.jammed.restored_kw} kW restorable, ${r.kw_change >= 0 ? "+" : ""}${r.kw_change} kW vs. as is`,
      };
    default:
      return { label: e.name, detail: "" };
  }
}

// The model writes light markdown; keep headings, paragraphs, bullets, tables and bold.
export function Answer({ text }: { text: string }) {
  const bold = (line: string) =>
    line.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? <b key={i}>{part}</b> : part));
  const cells = (line: string) => line.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const blocks: React.ReactNode[] = [];
  let items: string[] = [];
  let rows: string[][] = [];
  const flush = () => {
    if (items.length) blocks.push(<ul key={blocks.length}>{items.map((t, i) => <li key={i}>{bold(t)}</li>)}</ul>);
    if (rows.length) {
      const [head, ...body] = rows;
      blocks.push(
        <table key={blocks.length}>
          <thead><tr>{head.map((c, i) => <th key={i}>{bold(c)}</th>)}</tr></thead>
          <tbody>{body.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{bold(c)}</td>)}</tr>)}</tbody>
        </table>,
      );
    }
    items = [];
    rows = [];
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (/^\|?[\s:-]+(\|[\s:-]+)+\|?$/.test(line)) continue;
    if (line.startsWith("|")) {
      if (items.length) flush();
      rows.push(cells(line));
    } else if (/^[-*•]\s+/.test(line)) {
      if (rows.length) flush();
      items.push(line.replace(/^[-*•]\s+/, ""));
    } else {
      flush();
      if (/^#+\s/.test(line)) blocks.push(<h3 key={blocks.length}>{bold(line.replace(/^#+\s*/, ""))}</h3>);
      else if (line && !/^-{3,}$/.test(line)) blocks.push(<p key={blocks.length}>{bold(line)}</p>);
    }
  }
  flush();
  return <div className="agent-answer">{blocks}</div>;
}

type Props = { model: string | null; events: AgentEvent[]; running: boolean };

export default function AgentPanel({ model, events, running }: Props) {
  const tools = events.filter((e): e is ToolEvent => e.type === "tool");
  const final = events.find((e) => e.type === "final");
  const error = events.find((e) => e.type === "error");
  return (
    <Card
      className="agent-panel"
      title="Operator agent"
      hint={`${model} · calls the same tools, never executes; you approve`}
    >
      <ol className="agent-log">
        {tools.map((e, i) => {
          const d = describe(e);
          return (
            <li key={i} className={d.bad ? "bad" : undefined}>
              <code>{e.name}</code> <b>{d.label}</b> <span className="muted">{d.detail}</span>
            </li>
          );
        })}
        {running && (
          <li className="thinking">
            <Spinner /> thinking…
          </li>
        )}
      </ol>
      {error && error.type === "error" && <p className="error" role="alert">{error.detail}</p>}
      {final && final.type === "final" && <Answer text={final.text} />}
    </Card>
  );
}
