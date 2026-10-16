"use client";

import { useState } from "react";

import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import type { Explanation, SwitchingStep } from "@/lib/api";

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

function orderText(e: Explanation): string {
  return [
    `Switching order, incident ${e.incident}: fault on line ${e.fault_line} in Z${e.faulted_zone}`,
    ...e.switching_order.map((s) => `${s.step}. ${capitalize(s.op)} ${s.switch_kind} ${s.switch_id}`
      + ` (${s.phase === "isolate" ? "isolation" : `restoration, ${s.plan}`})`),
  ].join("\n");
}

// The steps exactly as carried out, numbered for the field crew, with a copy for their log.
function SwitchingOrder({ e }: { e: Explanation }) {
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  const phases: [string, SwitchingStep[]][] = [
    ["Isolate the fault", e.switching_order.filter((s) => s.phase === "isolate")],
    ["Restore", e.switching_order.filter((s) => s.phase === "restore")],
  ];
  const copyOrder = async () => {
    try {
      await navigator.clipboard.writeText(orderText(e));
      setCopy("copied");
    } catch {
      // Clipboard access can be refused, e.g. outside a secure context.
      setCopy("failed");
    }
    setTimeout(() => setCopy("idle"), 2500);
  };
  return (
    <>
      <div className="order-head">
        <h3>Switching order</h3>
        <Button variant="secondary" onClick={copyOrder}>
          {copy === "copied" ? "Copied" : copy === "failed" ? "Copy failed" : "Copy"}
        </Button>
      </div>
      {phases.filter(([, steps]) => steps.length).map(([label, steps]) => (
        <div key={label}>
          <div className="small muted">{label}</div>
          <ol className="switching-order" start={steps[0].step}>
            {steps.map((s) => (
              <li key={s.step}>
                <b>{capitalize(s.op)}</b> {s.switch_kind} <b>{s.switch_id}</b>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </>
  );
}

export default function ExplanationPanel({ e }: { e: Explanation }) {
  return (
    <Card className="explanation" title="Explanation" hint="built from the decision trace stored in the graph">
      <div className="explain-grid">
        <div>
          <h3>What happened</h3>
          <p>
            A fault on line <b>{e.fault_line}</b> was located in <b>Z{e.faulted_zone}</b> and isolated by{" "}
            {e.isolation.join(", ")}.
          </p>
          {e.cut_off.map((c) => (
            <p key={c.zone} className="callout warn">
              <span>
                Switch <b>{c.via}</b> was reported jammed closed, so <b>Z{c.zone}</b> could not be separated
                from the fault and was isolated with it.
              </span>
            </p>
          ))}
          {e.plan ? (
            <>
              <p>
                Executed <b>{e.plan.id}</b> ({e.plan.ops.join(", ")}), restoring{" "}
                <b>{Math.round(e.plan.restored_kw)} kW</b>. Margins: {e.plan.min_v_pu.toFixed(3)} pu minimum
                voltage, {Math.round(e.plan.max_loading_pct)}% maximum line loading.
              </p>
              <h3>Feeding paths</h3>
              <ul className="paths">
                {Object.entries(e.plan.paths).map(([zone, path]) => (
                  <li key={zone}>
                    <b>Z{zone}</b> {path.map((s) => <span key={s} className="path-step">{s}</span>)}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p>No restoration plan passed verification, so nothing beyond isolation was executed.</p>
          )}
          {e.switching_order.length > 0 && <SwitchingOrder e={e} />}
          {e.transfer.shortfall_kva > 0 && (
            <div className="callout">
              <span className="callout-tag">algo.maxFlow</span>
              <span>
                At most <b>{e.transfer.deliverable_kva}</b> of {e.transfer.demand_kva} kVA of healthy load can
                be carried by any switching, limited by <b>{e.transfer.bottlenecks.join(", ")}</b>. That is why
                the rest stays dark.
              </span>
            </div>
          )}
          <p className="muted">
            Still dark: {e.dark_zones.map((z) => `Z${z}`).join(", ") || "none"} ({e.load_unserved_kw} kW unserved).
          </p>
        </div>

        <div>
          {e.context && (
            <>
              <h3>Conditions and priorities</h3>
              <p className="muted">{e.context.conditions}</p>
              <ul className="priority-list">
                {e.context.priorities.map((p) => (
                  <li key={p.facility}>
                    <b>{p.facility}</b> {p.weight}
                    {p.weight !== p.standing && <span className="muted"> (normally {p.standing})</span>}
                    <div className="small muted">{p.rule}</div>
                  </li>
                ))}
              </ul>
            </>
          )}
          {e.crew_reports.length > 0 && (
            <>
              <h3>Field crew reports</h3>
              <ul className="priority-list">
                {e.crew_reports.map((r) => (
                  <li key={r.id}>
                    <b>{r.id}</b> <span className="muted">&ldquo;{r.note}&rdquo;</span>
                    {r.findings.map((f) => (
                      <div key={f.switch_id} className="small">
                        {f.switch_id}: {f.operable ? "works again" : "jammed"}
                      </div>
                    ))}
                  </li>
                ))}
              </ul>
            </>
          )}
          <h3>Critical sites</h3>
          <ul className="critical-list">
            {e.critical.map((c) => (
              <li key={c.bus} className={c.energized ? "on" : "off"}>
                <span className="dot" />
                <div>
                  <b>{c.facility}</b> <span className="muted">bus {c.bus}, Z{c.zone}</span>
                  <div className="small">{c.energized ? "restored" : c.reason}</div>
                </div>
              </li>
            ))}
          </ul>
          {e.rejected.length > 0 && (
            <>
              <h3>Rejected alternatives</h3>
              <ul className="rejected-list">
                {e.rejected.map((r) => (
                  <li key={r.id}>
                    <b>{r.id}</b> <span className="muted">{r.ops.join(", ")}</span>
                    {r.violations.map((v) => <div key={v} className="small violation">{v}</div>)}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </Card>
  );
}
