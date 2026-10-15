import type { Location, Trace } from "@/lib/api";

type Props = {
  fault: string | null;
  location: Location | null;
  trace: Trace | null;
  servedKw: number;
  totalKw: number;
};

const STATUS_LABEL: Record<string, string> = {
  executed: "executed",
  verified: "passed on fork",
  rejected: "rejected on fork",
};

export default function Timeline({ fault, location, trace, servedKw, totalKw }: Props) {
  return (
    <aside className="card timeline">
      <h2>Incident</h2>
      <div className="metric">
        <span className="muted">Load served</span>
        <b>
          {Math.round(servedKw)} / {Math.round(totalKw)} kW
        </b>
      </div>
      {trace && (
        <div className="metric">
          <span className="muted">{trace.incident.id}</span>
          <b>{trace.incident.status}</b>
        </div>
      )}
      <ol>
        {fault && <li>Fault on {fault}; breaker tripped</li>}
        {location && (
          <li>
            Located Z{location.zone} from indicators {location.chain.join(" → ")}
          </li>
        )}
        {trace?.plans.map((p) => (
          <li key={p.plan} className={p.status}>
            <b>{p.plan}</b> {p.kind}, {STATUS_LABEL[p.status] ?? p.status}
            <div className="muted">{p.ops.join(", ")}</div>
          </li>
        ))}
      </ol>
      {!fault && !trace && <p className="muted">Click any line segment to fault it.</p>}
    </aside>
  );
}
