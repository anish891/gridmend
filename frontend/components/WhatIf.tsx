"use client";

import { useState } from "react";

import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Spinner from "@/components/ui/Spinner";
import { api, type GridState, type WhatIf as Result, type WhatIfOutcome } from "@/lib/api";

type Props = { state: GridState; healthy: boolean; disabled: boolean };

function Outcome({ title, o }: { title: string; o: WhatIfOutcome }) {
  return (
    <div className="whatif-side">
      <h3>{title}</h3>
      <dl>
        <dt>Isolation</dt>
        <dd>{o.isolation.join(", ")}</dd>
        {o.cut_off.length > 0 && (
          <>
            <dt>Cut out with the fault</dt>
            <dd>{o.cut_off.map((c) => `Z${c.zone} (${c.via} stuck)`).join(", ")}</dd>
          </>
        )}
        <dt>Best safe plan</dt>
        <dd>{o.plan ? `${o.plan.ops.join(", ")}: ${o.plan.restored_kw} kW` : "none passes the checks"}</dd>
        <dt>Critical sites restored</dt>
        <dd>{o.critical_restored.join(", ") || "none"}</dd>
      </dl>
    </div>
  );
}

function headline(r: Result): string {
  const which = r.switches.join(" and ");
  const lost = r.critical_lost.length ? `, and the ${r.critical_lost.join(" and ")} stays dark` : "";
  if (r.kw_change < 0) return `If ${which} can't be operated, ${-r.kw_change} kW less gets power back${lost}.`;
  if (r.kw_change > 0) return `If ${which} can't be operated, the best safe plan restores ${r.kw_change} kW more${lost}.`;
  return r.critical_lost.length
    ? `If ${which} can't be operated, the same load comes back${lost}.`
    : `If ${which} can't be operated, it makes no difference to what can be restored.`;
}

// Asks the planner a question instead of the grid: everything runs on throwaway copies.
export default function WhatIf({ state, healthy, disabled }: Props) {
  const switches = state.lines.filter((l) => l.type === "switch" && l.kind !== "breaker");
  const segments = state.lines.filter((l) => l.type === "segment");
  const [switchId, setSwitchId] = useState("6-7");
  const [line, setLine] = useState("4-5");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  const check = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api.whatIf([switchId], healthy ? line : null));
    } catch (e) {
      setError((e as Error).message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card as="aside" className="whatif" title="What if a switch won't operate?"
      hint="Planned on copies of the grid; nothing live changes">
      <form className="whatif-form" onSubmit={check}>
        <label>
          <span>Switch that won&apos;t operate</span>
          <select value={switchId} onChange={(e) => setSwitchId(e.target.value)}>
            {switches.map((l) => <option key={l.id} value={l.id}>{l.id} ({l.kind})</option>)}
          </select>
        </label>
        {healthy && (
          <label>
            <span>When this line faults</span>
            <select value={line} onChange={(e) => setLine(e.target.value)}>
              {segments.map((l) => <option key={l.id} value={l.id}>line {l.id}</option>)}
            </select>
          </label>
        )}
        <Button type="submit" variant="secondary" disabled={busy || disabled}>Check</Button>
      </form>

      <div aria-live="polite">
        {busy ? (
          <p className="muted small busy-status"><Spinner /> Planning on copies of the grid…</p>
        ) : error ? (
          <p className="error" role="alert">{error}</p>
        ) : result && (
          <div className="whatif-result">
            <p><b>{headline(result)}</b></p>
            <div className="whatif-compare">
              <Outcome title="As things are" o={result.as_is} />
              <Outcome title={`If ${result.switches.join(", ")} is stuck`} o={result.jammed} />
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
