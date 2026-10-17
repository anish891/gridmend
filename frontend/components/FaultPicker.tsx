import Link from "next/link";

import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import type { Results } from "@/lib/api";
import Segmented from "@/components/ui/Segmented";

const DEMO_FAULTS = [
  {
    line: "4-5",
    title: "Main line fault",
    note: "The hospital and fire station lose power, and the obvious fix would overload a line.",
  },
  {
    line: "10-11",
    title: "Mid-feeder fault",
    note: "Only the fire station's section needs power routed in from elsewhere.",
  },
  {
    line: "27-28",
    title: "Branch fault",
    note: "The only backup route drops voltage too low, so no switching is safe.",
  },
];

export type Mode = "rules" | "agent";

type Props = {
  disabled: boolean;
  onFault: (line: string) => void;
  agentModel: string | null;
  mode: Mode;
  onMode: (mode: Mode) => void;
  // Today's conditions, shown before the faults because they set the priorities.
  conditions?: React.ReactNode;
  onDemo: () => void;
  // The evaluation's headline numbers, when results are available.
  results?: Results | null;
};

function Headline({ results }: { results: Results }) {
  const ours = results.summary.find((s) => s.method === "gridmend");
  const ai = results.summary.find((s) => s.method === "llm-only");
  const faults = new Set(results.rows.map((r) => r.fault)).size;
  if (!ours) return null;
  return (
    <Link href="/results/" className="hero-stats" aria-label="Evaluation results">
      <span><b>{ours.pct_optimal.toFixed(0)}%</b> of the best possible restoration</span>
      <span><b>{ours.unsafe_plans}</b> unsafe plans across all {faults} faults</span>
      {ai && <span>AI alone, without the graph: <b className="bad">{ai.unsafe_plans} of {faults}</b> unsafe</span>}
    </Link>
  );
}

export default function FaultPicker(
  { disabled, onFault, agentModel, mode, onMode, conditions, onDemo, results }: Props,
) {
  return (
    <Card className="hero">
      <h1>Simulate a fault and watch the feeder heal itself</h1>
      <p className="hero-lead">
        GridMend finds the damaged section, cuts it out and re-routes power around it. Every plan is
        safety-checked on a copy of the grid before a single switch moves.
      </p>
      {results && <Headline results={results} />}
      <div className="hero-demo">
        <Button disabled={disabled} onClick={onDemo}>
          <span aria-hidden="true">▶</span> Play the demo
        </Button>
        <span className="muted small">
          About 30 seconds: a main-line fault, the plans that fail and why, then the restoration.
        </span>
      </div>
      {conditions}
      {conditions && <h2 className="conditions-title">Pick a fault</h2>}
      <div className="demo-faults">
        {DEMO_FAULTS.map((f) => (
          <button key={f.line} type="button" className="demo-fault" disabled={disabled} onClick={() => onFault(f.line)}>
            <span className="demo-title">{f.title}</span>
            <span className="demo-line">line {f.line}</span>
            <span className="demo-note">{f.note}</span>
          </button>
        ))}
      </div>
      <div className="hero-foot">
        <p className="muted small">Or click any line on the feeder map below.</p>
        {agentModel && (
          <Segmented
            label="Who handles the incident"
            value={mode}
            onChange={onMode}
            options={[
              { value: "rules", label: "Rules", hint: "instant and deterministic" },
              { value: "agent", label: "AI operator agent", hint: `${agentModel}, takes 1 to 2 minutes` },
            ]}
          />
        )}
      </div>
    </Card>
  );
}
