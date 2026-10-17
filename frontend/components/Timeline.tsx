import Card from "@/components/ui/Card";
import type { Location, Trace } from "@/lib/api";

type Props = {
  fault: string | null;
  // One entry per round of location; a second round means a fault was hidden upstream.
  locations: Location[];
  trace: Trace | null;
  conditions: string | null;
};

const KIND_LABEL: Record<string, string> = { isolation: "Isolation", restoration: "Restoration plan" };

const STATUS_LABEL: Record<string, string> = {
  executed: "carried out",
  verified: "passed safety checks",
  rejected: "failed safety checks",
};

export default function Timeline({ fault, locations, trace, conditions }: Props) {
  const breakers = [...new Set((locations[0] ? Object.values(locations[0].chains) : []).map((chain) => chain[0]))];
  return (
    <Card as="aside" className="timeline" title="Decision trace" hint={trace?.incident.id}>
      <ol>
        {conditions && (
          <li className="event">
            <b>Conditions in force</b>
            <div className="muted">{conditions}</div>
          </li>
        )}
        {fault && (
          <li className="event fault">
            <b>Fault on {fault}</b>
            {/* Fault current passes the feeder's breaker first, so it heads every indicator chain. */}
            <div className="muted">
              {breakers.length ? `substation breaker ${breakers.join(" and ")} tripped` : "the substation breaker tripped"}
            </div>
          </li>
        )}
        {locations.map((location, round) => (
          <li key={round} className="event">
            {round > 0 && <div className="small muted">Breaker tripped again on reclosing</div>}
            <b>Found {location.zones.length > 1 ? "faults" : "it"} in {location.zones.map((z) => `Z${z}`).join(" and ")}</b>
            {location.zones.map((z) => (
              <div key={z} className="muted">tripped indicators {location.chains[z].join(" → ")}</div>
            ))}
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
