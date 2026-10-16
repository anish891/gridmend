import Badge, { type Tone } from "@/components/ui/Badge";
import Term from "@/components/ui/Term";
import type { GridState, Trace } from "@/lib/api";

const STATUS_TONE: Record<string, Tone> = { restored: "success", isolated: "warning" };

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

type Props = { state: GridState; trace: Trace | null };

export default function StatusStrip({ state, trace }: Props) {
  const share = state.total_kw ? state.served_kw / state.total_kw : 0;
  const critical = state.buses.filter((b) => b.critical);
  // Read from the decision trace so the count survives a page reload.
  const verified = trace?.plans.filter((p) => p.kind === "restoration") ?? [];
  const passed = verified.filter((p) => p.status !== "rejected").length;

  return (
    <section className="status-bar" aria-label="Feeder status">
      <div className="stat">
        <div className="stat-label">Load served</div>
        <div className="stat-value">
          {Math.round(state.served_kw)} <span className="muted">/ {Math.round(state.total_kw)} kW</span>
        </div>
        <div className="meter" role="meter" aria-label="Share of load served" aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={Math.round(share * 100)}>
          <div className="meter-fill" style={{ width: `${share * 100}%` }} />
        </div>
      </div>

      <div className="stat stat-wide">
        <div className="stat-label">
          <Term hint="Sites that get power back first, weighted by the restoration policy for today's conditions.">
            Critical sites
          </Term>
        </div>
        <div className="chips">
          {critical.map((b) => (
            <span key={b.id} className={b.energized ? "chip on" : "chip off"} title={`bus ${b.id}, Z${b.zone}`}>
              <span className="dot" /> {b.facility}
              <span className="visually-hidden">{b.energized ? "has power" : "without power"}</span>
            </span>
          ))}
        </div>
      </div>

      <div className="stat">
        <div className="stat-label">Incident</div>
        <div className="stat-value">
          {state.incident ? (
            <>
              <Badge tone={STATUS_TONE[state.incident.status] ?? "danger"}>
                {capitalize(state.incident.status)}
              </Badge>{" "}
              <span className="muted">in Z{state.incident.zone}</span>
            </>
          ) : state.lines.some((l) => l.faulted) ? (
            <Badge tone="danger">Breaker tripped</Badge>
          ) : (
            <Badge tone="success">None, feeder healthy</Badge>
          )}
        </div>
      </div>

      <div className="stat">
        <div className="stat-label">
          <Term hint="Every plan is tried on a throwaway copy of the grid before any real switch moves.">
            Plans checked
          </Term>
        </div>
        <div className="stat-value">
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
