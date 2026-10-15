import type { Explanation } from "@/lib/api";

export default function ExplanationPanel({ e }: { e: Explanation }) {
  return (
    <section className="card explanation">
      <h2>Explanation</h2>
      <p>
        A fault on line <b>{e.fault_line}</b> was located in <b>Z{e.faulted_zone}</b> and isolated by{" "}
        {e.isolation.join(", ")}.
      </p>
      {e.plan ? (
        <>
          <p>
            Executed <b>{e.plan.id}</b> ({e.plan.ops.join(", ")}), restoring{" "}
            <b>{Math.round(e.plan.restored_kw)} kW</b> with margins of {e.plan.min_v_pu.toFixed(3)} pu
            minimum voltage and {Math.round(e.plan.max_loading_pct)}% maximum line loading.
          </p>
          <ul>
            {Object.entries(e.plan.paths).map(([zone, path]) => (
              <li key={zone}>
                Z{zone} is fed via {path.join(" → ")}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p>No restoration plan was executed.</p>
      )}

      <h3>Critical loads</h3>
      <ul>
        {e.critical.map((c) => (
          <li key={c.bus} className={c.energized ? "restored" : "shed"}>
            <b>{c.facility}</b> (bus {c.bus}, Z{c.zone}): {c.energized ? "restored" : `dark, ${c.reason}`}
          </li>
        ))}
      </ul>

      {e.rejected.length > 0 && (
        <>
          <h3>Rejected alternatives</h3>
          <ul>
            {e.rejected.map((r) => (
              <li key={r.id}>
                {r.id} ({r.ops.join(", ")}): {r.violations.join("; ")}
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="muted">
        Still dark: {e.dark_zones.map((z) => `Z${z}`).join(", ") || "none"} ({e.load_unserved_kw} kW
        unserved).
      </p>
    </section>
  );
}
