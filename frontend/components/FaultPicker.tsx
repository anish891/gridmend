import Link from "next/link";

import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import type { Results } from "@/lib/api";
import { currentFeeder, withFeeder, type FeederConfig } from "@/lib/feeders";
import Segmented from "@/components/ui/Segmented";

export type Mode = "rules" | "agent";

type Props = {
  disabled: boolean;
  onFault: (line: string | string[]) => void;
  agentModel: string | null;
  mode: Mode;
  onMode: (mode: Mode) => void;
  onDemo: () => void;
  feeder: FeederConfig;
  // The evaluation's headline numbers, when results are available.
  results?: Results | null;
};

function Headline({ results }: { results: Results }) {
  const ours = results.summary.find((s) => s.method === "gridmend");
  const optimum = results.summary.some((s) => s.method === "optimal");
  // The strongest unchecked baseline the evaluation has: the AI alone, else greedy switching.
  const rival = results.summary.find((s) => s.method === "llm-only")
    ?? results.summary.find((s) => s.method === "greedy");
  const faults = new Set(results.rows.map((r) => r.fault)).size;
  if (!ours) return null;
  return (
    <Link href={withFeeder("/results/", currentFeeder())} className="hero-stats" aria-label="Evaluation results">
      {optimum ? (
        <span><b>{ours.pct_optimal.toFixed(0)}%</b> of the best possible restoration</span>
      ) : (
        <span><b>{(ours.restored_kw / 1000).toFixed(0)} MW</b> restored over every fault</span>
      )}
      <span><b>{ours.unsafe_plans}</b> unsafe plans across all {faults} faults</span>
      {rival && (
        <span>
          {rival.method === "llm-only" ? "AI alone, without the graph" : "Greedy, unchecked"}:{" "}
          <b className="bad">{rival.unsafe_plans} of {faults}</b> unsafe
        </span>
      )}
    </Link>
  );
}

export default function FaultPicker(
  { disabled, onFault, agentModel, mode, onMode, onDemo, results, feeder }: Props,
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
          About 30 seconds: a fault, the plans that fail and why, then the restoration.
        </span>
      </div>
      <div className="demo-faults">
        {feeder.faults.map((f) => (
          <button key={f.line} type="button" className="demo-fault" disabled={disabled} onClick={() => onFault(f.line)}>
            <span className="demo-title">{f.title}</span>
            <span className="demo-line">line {f.line}</span>
            <span className="demo-note">{f.note}</span>
          </button>
        ))}
      </div>
      <h3 className="double-title">Or two faults at once</h3>
      <div className="double-faults">
        {feeder.doubles.map((f) => (
          <button key={f.title} type="button" className="demo-fault double" disabled={disabled} onClick={() => onFault(f.lines)}>
            <span className="demo-title">{f.title}</span>
            <span className="demo-line">lines {f.lines.join(" + ")}</span>
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
