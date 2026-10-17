import Card from "@/components/ui/Card";
import type { Location, Trace } from "@/lib/api";

type Props = {
  fault: string | null;
  // One entry per round of location; a second round means a fault was hidden upstream.
  locations: Location[];
  trace: Trace | null;
};

const KIND_LABEL: Record<string, string> = { isolation: "Isolation plan", restoration: "Restoration plan" };

const STATUS_LABEL: Record<string, string> = {
  executed: "carried out",
  verified: "passed simulation",
  rejected: "failed simulation",
};

export default function Timeline({ fault, locations, trace }: Props) {
  return (
    <Card as="aside" className="timeline" title="Decision trace" hint={trace?.incident.id}>
      <ol>
        {fault && (
          <li className="event fault">
            <b>Fault on {fault}</b>
            <div className="muted">the substation breaker tripped</div>
          </li>
        )}
        {locations.map((location, round) => (
          <li key={round} className="event">
            {round > 0 && <div className="small muted">Breaker tripped again on reclosing</div>}
            <b>The agent located {location.zones.length > 1 ? "faults" : "it"} in {location.zones.map((z) => `Z${z}`).join(" and ")}</b>
            <div className="muted">{location.evidence}</div>
          </li>
        ))}
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
