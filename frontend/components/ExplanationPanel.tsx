"use client";

import { useState } from "react";

import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import type { Explanation, SwitchingStep } from "@/lib/api";

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

function orderText(e: Explanation): string {
  return [
    `Switching order, incident ${e.incident}: fault on ${e.fault_line} in ${e.faulted_zones.map((z) => `Z${z}`).join(", ")}`,
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

// The incident at a glance: what came back, at what cost in switching, after how much trial.
function Outcome({ e }: { e: Explanation }) {
  const total = e.load_served_kw + e.load_unserved_kw;
  const powered = e.critical.filter((c) => c.energized).length;
  const isolating = e.switching_order.filter((s) => s.phase === "isolate").length;
  const restoring = e.switching_order.length - isolating;
  return (
    <div className="outcome" aria-label="Outcome">
      <div className="kpi">
        <div className="kpi-label">Load served</div>
        <div className="kpi-value">{e.load_served_kw} <span className="muted">/ {total} kW</span></div>
        <div className="meter"><div className="meter-fill" style={{ width: `${(100 * e.load_served_kw) / total}%` }} /></div>
      </div>
      <div className="kpi">
        <div className="kpi-label">Critical sites powered</div>
        <div className="kpi-value">{powered} <span className="muted">/ {e.critical.length}</span></div>
        <div className="small muted">{e.critical.filter((c) => c.energized).map((c) => c.facility).join(", ") || "none"}</div>
      </div>
      <div className="kpi">
        <div className="kpi-label">Switch operations</div>
        <div className="kpi-value">{e.switching_order.length}</div>
        <div className="small muted">{isolating} to isolate, {restoring} to restore</div>
      </div>
      <div className="kpi">
        <div className="kpi-label">Designs tested</div>
        <div className="kpi-value">{e.designs.tested}</div>
        <div className="small muted">{e.designs.passed} passed in simulation</div>
      </div>
    </div>
  );
}

type Props = { e: Explanation; onPath?: (zone: number | null) => void };

export default function ExplanationPanel({ e, onPath = () => {} }: Props) {
  return (
    <Card id="explanation" className="explanation" title="Explanation" hint="built from the decision trace stored in the graph">
      <Outcome e={e} />
      <div className="explain-grid">
        <div>
          <h3>What happened</h3>
          <p>
            {e.faulted_zones.length > 1 ? "Faults on lines " : "A fault on line "}<b>{e.fault_line}</b>{" "}
            {e.faulted_zones.length > 1 ? "were" : "was"} located by the agent in{" "}
            <b>{e.faulted_zones.map((z) => `Z${z}`).join(" and ")}</b> and isolated by {e.isolation.join(", ")}.
          </p>
          {e.evidence && <p className="small muted">The indicators it read: {e.evidence}</p>}
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
              <p className="small muted">Hover a path to trace it on the feeder map.</p>
              <ul className="paths">
                {Object.entries(e.plan.paths).map(([zone, path]) => (
                  <li
                    key={zone}
                    tabIndex={0}
                    onMouseEnter={() => onPath(Number(zone))}
                    onMouseLeave={() => onPath(null)}
                    onFocus={() => onPath(Number(zone))}
                    onBlur={() => onPath(null)}
                  >
                    <b>Z{zone}</b> {path.map((s) => <span key={s} className="path-step">{s}</span>)}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p>No restoration design passed simulation, so nothing beyond isolation was executed.</p>
          )}
          {e.switching_order.length > 0 && <SwitchingOrder e={e} />}
          {e.transfer && e.transfer.shortfall_kva > 0 && (
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
          {e.crew_reports.length > 0 && (
            <>
              <h3>Field crew reports</h3>
              <ul className="report-list">
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
                    <b>{r.id}</b>{r.kind === "isolation" && <span className="muted"> (isolation)</span>}{" "}
                    <span className="muted">{r.ops.join(", ")}</span>
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
