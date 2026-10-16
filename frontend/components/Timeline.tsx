import Card from "@/components/ui/Card";
import type { Location, Trace } from "@/lib/api";

type Props = {
  fault: string | null;
  location: Location | null;
  trace: Trace | null;
};

const KIND_LABEL: Record<string, string> = { isolation: "Isolation", restoration: "Restoration plan" };

const STATUS_LABEL: Record<string, string> = {
  executed: "carried out",
  verified: "passed safety checks",
  rejected: "failed safety checks",
};

export default function Timeline({ fault, location, trace }: Props) {
  return (
    <Card as="aside" className="timeline" title="Decision trace" hint={trace?.incident.id}>
      <ol>
        {fault && (
          <li className="event fault">
            <b>Fault on {fault}</b>
            <div className="muted">substation breaker 1-2 tripped</div>
          </li>
        )}
        {location && (
          <li className="event">
            <b>Found it in Z{location.zone}</b>
            <div className="muted">tripped indicators {location.chain.join(" → ")}</div>
          </li>
        )}
        {trace?.plans.map((p) => (
          <li key={p.plan} className={`event ${p.status}`}>
            <b>{p.plan}</b>
            <div className="muted">{KIND_LABEL[p.kind] ?? p.kind}, {STATUS_LABEL[p.status] ?? p.status}</div>
            <div className="muted small">{p.ops.join(", ")}</div>
          </li>
        ))}
      </ol>
    </Card>
  );
}
