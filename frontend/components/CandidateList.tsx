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
  greedy: "operator reflex: a tie into every dead zone",
  "tie-only": "single tie, nothing opened",
  repaired: "ties plus openings that keep it radial and within ratings",
  agent: "checked at the operator agent's request",
};

const V_MIN = 0.9;

function zones(ids: number[]): string {
  return ids.map((z) => `Z${z}`).join(", ") || "none";
}

// A bar from 0.85 to 1.00 pu with the 0.90 limit marked.
function VoltageBar({ value }: { value: number }) {
  const at = (v: number) => `${Math.min(100, Math.max(0, ((v - 0.85) / 0.15) * 100))}%`;
  return (
    <div className="gauge" title={`minimum voltage ${value.toFixed(3)} pu, limit ${V_MIN}`}>
      <div className={value < V_MIN ? "gauge-fill bad" : "gauge-fill"} style={{ width: at(value) }} />
      <div className="gauge-limit" style={{ left: at(V_MIN) }} />
    </div>
  );
}

// A bar from 0 to 120 % loading with the 100 % rating marked.
function LoadingBar({ value }: { value: number }) {
  const at = (v: number) => `${Math.min(100, (v / 120) * 100)}%`;
  return (
    <div className="gauge" title={`maximum line loading ${Math.round(value)}%, rating 100%`}>
      <div className={value > 100 ? "gauge-fill bad" : "gauge-fill"} style={{ width: at(value) }} />
      <div className="gauge-limit" style={{ left: at(100) }} />
    </div>
  );
}

export default function CandidateList(props: Props) {
  const { candidates, recommended, selected, executed, busy, onSelect, onApprove } = props;
  return (
    <ol className="candidates">
      {candidates.map((c, i) => (
        <li
          key={c.plan_id}
          className={["candidate", c.passed ? "pass" : "fail", selected === c.plan_id && "selected",
            executed === c.plan_id && "executed"].filter(Boolean).join(" ")}
          onMouseEnter={() => onSelect(c.plan_id)}
        >
          <div className="candidate-head">
            <span className={c.passed ? "verdict pass" : "verdict fail"}>
              {c.passed ? "Passed" : "Rejected"}
            </span>
            <span className="rank">#{i + 1}</span>
            {c.plan_id === recommended && <span className="tag recommended">recommended</span>}
            {executed === c.plan_id && <span className="tag executed">executed</span>}
            <span className="fork-id" title="checked on its own copy of the graph">fork_{c.plan_id}</span>
          </div>
          <div className="muted small">{ORIGIN[c.origin]}</div>
          {c.recommendation && (
            <div className="agent-reason">
              <span className="tag agent">agent</span> {c.recommendation}
            </div>
          )}

          <div className="ops">
            {c.ops.map((o) => (
              <span key={o.switch_id} className={`op ${o.op}`}>
                {o.op} {o.switch_id}
              </span>
            ))}
          </div>

          <div className="candidate-metrics">
            <div>
              <div className="metric-label">Restores</div>
              <div className="metric-value">{Math.round(c.restored_kw)} kW</div>
              <div className="muted small">{zones(c.restored_zones)}</div>
            </div>
            <div>
              <div className="metric-label">Critical loads</div>
              <div className="metric-value">{c.critical.length || "none"}</div>
              <div className="muted small">{c.critical.join(", ")}</div>
            </div>
            {c.min_v_pu !== null && (
              <div>
                <div className="metric-label">Min voltage</div>
                <div className="metric-value">{c.min_v_pu.toFixed(3)} pu</div>
                <VoltageBar value={c.min_v_pu} />
              </div>
            )}
            {c.max_loading_pct !== null && (
              <div>
                <div className="metric-label">Max loading</div>
                <div className="metric-value">{Math.round(c.max_loading_pct)}%</div>
                <LoadingBar value={c.max_loading_pct} />
              </div>
            )}
          </div>

          {c.dropped_zones.length > 0 && (
            <div className="violation">drops live zones {zones(c.dropped_zones)}</div>
          )}
          {c.violations.map((v, j) => (
            <div key={j} className="violation">
              <span className="violation-type">{v.type}</span> {v.detail}
            </div>
          ))}

          {c.passed && !executed && (
            <button className="approve" disabled={busy} onClick={() => onApprove(c.plan_id)}>
              Approve and execute {c.plan_id}
            </button>
          )}
        </li>
      ))}
    </ol>
  );
}
