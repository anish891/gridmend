import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Term from "@/components/ui/Term";
import type { Candidate } from "@/lib/api";

type Props = {
  candidates: Candidate[];
  recommended: string | null;
  selected: string | null;
  executed: string | null;
  busy: boolean;
  onSelect: (planId: string) => void;
  onApprove: (planId: string) => void;
};

const ORIGIN = {
  agent: "Designed by the AI operator agent",
  manual: "Entered by hand",
};

// The verifier's check names, in the operator's words.
export const VIOLATION_LABEL: Record<string, string> = {
  isolation: "Re-energizes the fault",
  connectivity: "Leaves a zone dark",
  radiality: "Creates a loop",
  capacity: "Overloads a line",
  voltage: "Voltage too low",
  operability: "Uses a jammed switch",
};

const V_MIN = 0.9;

function zones(ids: number[]): string {
  return ids.map((z) => `Z${z}`).join(", ") || "none";
}

// A bar from 0.85 to 1.00 pu with the 0.90 limit marked.
function VoltageBar({ value }: { value: number }) {
  const at = (v: number) => `${Math.min(100, Math.max(0, ((v - 0.85) / 0.15) * 100))}%`;
  return (
    <div className="gauge" title={`lowest voltage ${value.toFixed(3)} pu, limit ${V_MIN}`}>
      <div className={value < V_MIN ? "gauge-fill bad" : "gauge-fill"} style={{ width: at(value) }} />
      <div className="gauge-limit" style={{ left: at(V_MIN) }} />
    </div>
  );
}

// A bar from 0 to 120 % loading with the 100 % rating marked.
function LoadingBar({ value }: { value: number }) {
  const at = (v: number) => `${Math.min(100, (v / 120) * 100)}%`;
  return (
    <div className="gauge" title={`busiest line at ${Math.round(value)}% of its rating`}>
      <div className={value > 100 ? "gauge-fill bad" : "gauge-fill"} style={{ width: at(value) }} />
      <div className="gauge-limit" style={{ left: at(100) }} />
    </div>
  );
}

function Recommendation({ plan, rank, busy, onApprove }: {
  plan: Candidate; rank: number; busy: boolean; onApprove: (planId: string) => void;
}) {
  return (
    <div className="recommend">
      <p>
        <b>The agent recommends plan #{rank}.</b> It restores {Math.round(plan.restored_kw)} kW
        {plan.critical.length > 0 && <>, including the {plan.critical.join(" and ")}</>}, and passed every
        check in simulation.
      </p>
      <Button disabled={busy} onClick={() => onApprove(plan.plan_id)}>Approve recommended plan</Button>
    </div>
  );
}

export default function CandidateList(props: Props) {
  const { candidates, recommended, selected, executed, busy, onSelect, onApprove } = props;
  const best = candidates.findIndex((c) => c.plan_id === recommended);
  return (
    <>
      {best >= 0 && !executed && (
        <Recommendation plan={candidates[best]} rank={best + 1} busy={busy} onApprove={onApprove} />
      )}
      <ol className="candidates">
        {candidates.map((c, i) => (
          <li
            key={c.plan_id}
            className={["candidate", c.passed ? "pass" : "fail", selected === c.plan_id && "selected",
              c.plan_id === recommended && "recommended",
              executed === c.plan_id && "executed"].filter(Boolean).join(" ")}
            onMouseEnter={() => onSelect(c.plan_id)}
            onClick={() => onSelect(c.plan_id)}
          >
            <div className="candidate-head">
              <button
                type="button"
                className="candidate-select"
                aria-pressed={selected === c.plan_id}
                aria-label={`Preview plan ${i + 1} on the map`}
                onFocus={() => onSelect(c.plan_id)}
              >
                <Badge tone={c.passed ? "success" : "danger"}>{c.passed ? "Passed" : "Failed"}</Badge>
                <span className="rank">Plan #{i + 1}</span>
              </button>
              {c.plan_id === recommended && <Badge tone="success" solid>recommended</Badge>}
              {executed === c.plan_id && <Badge tone="neutral" solid>carried out</Badge>}
              <span className="fork-id">
                <Term hint="The copy of the grid this plan was tested on before anything real moved.">
                  fork_{c.plan_id}
                </Term>
              </span>
            </div>
            <div className="muted small">{c.origin && ORIGIN[c.origin]}</div>
            {c.recommendation && (
              <div className="agent-reason">
                <Badge tone="accent" solid>agent</Badge> {c.recommendation}
              </div>
            )}

            <div className="ops" aria-label="Switching">
              {c.ops.map((o) => (
                <span key={o.switch_id} className={`op ${o.op}`}>
                  {o.op} {o.switch_id}
                </span>
              ))}
            </div>

            <div className="candidate-metrics">
              <div>
                <div className="metric-label">
                  <Term hint="Zones are sections between switches, named by their first bus.">Restores</Term>
                </div>
                <div className="metric-value">{Math.round(c.restored_kw)} kW</div>
                <div className="muted small">{zones(c.restored_zones)}</div>
              </div>
              <div>
                <div className="metric-label">Critical sites</div>
                <div className="metric-value">{c.critical.length || "none"}</div>
                <div className="muted small">{c.critical.join(", ")}</div>
              </div>
              {c.min_v_pu !== null && (
                <div>
                  <div className="metric-label">
                    <Term hint="Lowest voltage on the feeder as a fraction of normal (pu). Must stay at 0.90 or above.">
                      Lowest voltage
                    </Term>
                  </div>
                  <div className="metric-value">{c.min_v_pu.toFixed(3)} pu</div>
                  <VoltageBar value={c.min_v_pu} />
                </div>
              )}
              {c.max_loading_pct !== null && (
                <div>
                  <div className="metric-label">
                    <Term hint="The most heavily used line, as a share of what it is rated to carry. Must stay at 100% or below.">
                      Busiest line
                    </Term>
                  </div>
                  <div className="metric-value">{Math.round(c.max_loading_pct)}%</div>
                  <LoadingBar value={c.max_loading_pct} />
                </div>
              )}
            </div>

            {c.dropped_zones.length > 0 && (
              <div className="violation">
                <span className="violation-type">Cuts power</span>
                <span>to zones that are on now: {zones(c.dropped_zones)}</span>
              </div>
            )}
            {c.violations.map((v, j) => (
              <div key={j} className="violation">
                <span className="violation-type">{VIOLATION_LABEL[v.type] ?? v.type}</span>
                <span>{v.detail}</span>
              </div>
            ))}

            {c.passed && !executed && (
              <Button variant="secondary" className="approve" disabled={busy} onClick={() => onApprove(c.plan_id)}>
                Approve this plan
              </Button>
            )}
          </li>
        ))}
      </ol>
    </>
  );
}
