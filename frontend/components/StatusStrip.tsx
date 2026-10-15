import type { GridState, Trace } from "@/lib/api";

type Props = { state: GridState; trace: Trace | null };

export default function StatusStrip({ state, trace }: Props) {
  const share = state.total_kw ? state.served_kw / state.total_kw : 0;
  const critical = state.buses.filter((b) => b.critical);
  // Read from the decision trace so the count survives a page reload.
  const verified = trace?.plans.filter((p) => p.kind === "restoration") ?? [];
  const passed = verified.filter((p) => p.status !== "rejected").length;

  return (
    <section className="strip">
      <div className="kpi">
        <div className="kpi-label">Load served</div>
        <div className="kpi-value">
          {Math.round(state.served_kw)} <span className="muted">/ {Math.round(state.total_kw)} kW</span>
        </div>
        <div className="meter">
          <div className="meter-fill" style={{ width: `${share * 100}%` }} />
        </div>
      </div>

      <div className="kpi">
        <div className="kpi-label">Critical loads</div>
        <div className="chips">
          {critical.map((b) => (
            <span key={b.id} className={b.energized ? "chip on" : "chip off"} title={`bus ${b.id}, Z${b.zone}`}>
              <span className="dot" /> {b.facility}
            </span>
          ))}
        </div>
      </div>

      <div className="kpi">
        <div className="kpi-label">Incident</div>
        <div className="kpi-value">
          {state.incident ? (
            <>
              <span className={`state-pill ${state.incident.status}`}>{state.incident.status}</span>{" "}
              <span className="muted">
                {state.incident.id} in Z{state.incident.zone}
              </span>
            </>
          ) : state.lines.some((l) => l.faulted) ? (
            <span className="state-pill faulted">breaker tripped</span>
          ) : (
            <span className="state-pill healthy">healthy</span>
          )}
        </div>
      </div>

      <div className="kpi">
        <div className="kpi-label">Plans verified on forks</div>
        <div className="kpi-value">
          {verified.length > 0 ? (
            <>
              {passed} <span className="muted">of {verified.length} passed</span>
            </>
          ) : (
            <span className="muted">none yet</span>
          )}
        </div>
      </div>
    </section>
  );
}
