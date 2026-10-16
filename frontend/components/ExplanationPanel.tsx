import Card from "@/components/ui/Card";
import type { Explanation } from "@/lib/api";

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
