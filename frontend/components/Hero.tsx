import Link from "next/link";

import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import type { Results } from "@/lib/api";
import { currentFeeder, withFeeder } from "@/lib/feeders";

type Props = {
  disabled: boolean;
  agentModel: string | null;
  onDemo: () => void;
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

export default function Hero({ disabled, agentModel, onDemo, results }: Props) {
  return (
    <Card className="hero">
      <div className="hero-main">
        <div>
          <h1>An AI agent that heals the feeder</h1>
          <p className="hero-lead">
            It reads the fault indicators, finds the damaged section, cuts it out and designs how to
            re-route power. Every plan it writes is tested on a copy of the grid in FalkorDB, and only
            plans that pass move a real switch.
          </p>
        </div>
        <div className="hero-demo">
          <Button disabled={disabled || !agentModel} onClick={onDemo}>
            <span aria-hidden="true">▶</span> Play the demo
          </Button>
          <span className="muted small">
            {agentModel ? `About a minute, with ${agentModel}` : "No language model is configured on this server."}
          </span>
        </div>
      </div>
      {results && <Headline results={results} />}
    </Card>
  );
}
