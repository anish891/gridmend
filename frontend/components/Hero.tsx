import Link from "next/link";

import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import type { Results } from "@/lib/api";
import { currentFeeder, withFeeder, type FeederConfig } from "@/lib/feeders";

type Props = {
  disabled: boolean;
  onFault: (line: string | string[]) => void;
  agentModel: string | null;
  onDemo: () => void;
  feeder: FeederConfig;
  // The evaluation's headline numbers, when results are available.
  results?: Results | null;
};

function Headline({ results }: { results: Results }) {
  const ours = results.summary.find((s) => s.method === "agent");
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
  { disabled, onFault, agentModel, onDemo, results, feeder }: Props,
) {
  // Every incident is worked by the agent, so without a model there is nothing to run.
  const off = disabled || !agentModel;
  return (
    <Card className="hero">
      <h1>Simulate a fault and watch an AI agent heal the feeder</h1>
      <p className="hero-lead">
        The agent reads the fault indicators, finds the damaged section, cuts it out and designs how
        to re-route power around it. Every plan it designs is tested on a copy of the grid, and only
        plans that pass move a real switch.
      </p>
      {results && <Headline results={results} />}
      {!agentModel && (
        <p className="muted">No language model is configured on this server, so the agent cannot run.</p>
      )}
      <div className="hero-demo">
        <Button disabled={off} onClick={onDemo}>
          <span aria-hidden="true">▶</span> Play the demo
        </Button>
        <span className="muted small">
          About a minute: the agent locates and isolates the fault, tries designs, learns why some
          fail, then restores.
        </span>
      </div>
      <div className="demo-faults">
        {feeder.faults.map((f) => (
          <button key={f.line} type="button" className="demo-fault" disabled={off} onClick={() => onFault(f.line)}>
            <span className="demo-title">{f.title}</span>
            <span className="demo-line">line {f.line}</span>
            <span className="demo-note">{f.note}</span>
          </button>
        ))}
      </div>
      <h3 className="double-title">Or two faults at once</h3>
      <div className="double-faults">
        {feeder.doubles.map((f) => (
          <button key={f.title} type="button" className="demo-fault double" disabled={off} onClick={() => onFault(f.lines)}>
            <span className="demo-title">{f.title}</span>
            <span className="demo-line">lines {f.lines.join(" + ")}</span>
            <span className="demo-note">{f.note}</span>
          </button>
        ))}
      </div>
      <div className="hero-foot">
        <p className="muted small">Or click any line on the feeder map below.</p>
        {agentModel && <p className="muted small">Agent: {agentModel}</p>}
      </div>
    </Card>
  );
}
