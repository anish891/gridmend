import type { Location, Trace } from "@/lib/api";

type Props = {
  fault: string | null;
  location: Location | null;
  trace: Trace | null;
};

const STATUS_LABEL: Record<string, string> = {
  executed: "executed",
  verified: "passed on its fork",
  rejected: "rejected on its fork",
};

export default function Timeline({ fault, location, trace }: Props) {
  return (
    <aside className="card timeline">
      <div className="card-head">
        <h2>Decision trace</h2>
        {trace && <span className="muted small">{trace.incident.id}</span>}
      </div>
      <ol>
        {fault && (
          <li className="event fault">
            <b>Fault on {fault}</b>
            <div className="muted">breaker 1-2 tripped</div>
          </li>
        )}
        {location && (
          <li className="event">
            <b>Located Z{location.zone}</b>
            <div className="muted">indicators {location.chain.join(" → ")}</div>
          </li>
        )}
        {trace?.plans.map((p) => (
          <li key={p.plan} className={`event ${p.status}`}>
            <b>{p.plan}</b>
            <div className="muted">{p.kind}, {STATUS_LABEL[p.status] ?? p.status}</div>
            <div className="muted small">{p.ops.join(", ")}</div>
          </li>
        ))}
      </ol>
    </aside>
  );
}
