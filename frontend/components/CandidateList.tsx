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
  greedy: "operator reflex",
  "tie-only": "single tie",
  repaired: "repaired",
};

function zones(ids: number[]): string {
  return ids.map((z) => `Z${z}`).join(", ") || "none";
}

export default function CandidateList(props: Props) {
  const { candidates, recommended, selected, executed, busy, onSelect, onApprove } = props;
  return (
    <ol className="candidates">
      {candidates.map((c, i) => (
        <li
          key={c.plan_id}
          className={["candidate", c.passed ? "pass" : "fail", selected === c.plan_id && "selected"]
            .filter(Boolean)
            .join(" ")}
          onMouseEnter={() => onSelect(c.plan_id)}
        >
          <div className="candidate-head">
            <span className="rank">#{i + 1}</span>
            <span className={c.passed ? "badge pass" : "badge fail"}>
              {c.passed ? "PASS" : "REJECTED"}
            </span>
            <span className="muted">{ORIGIN[c.origin]}</span>
            {c.plan_id === recommended && <span className="badge recommended">recommended</span>}
            <span className="badge fork">verified on fork_{c.plan_id}</span>
          </div>
          <div className="ops">
            {c.ops.map((o) => (
              <span key={o.switch_id} className={`op ${o.op}`}>
                {o.op} {o.switch_id}
              </span>
            ))}
          </div>
          <div className="muted">
            restores {zones(c.restored_zones)} ({Math.round(c.restored_kw)} kW)
            {c.critical.length > 0 && <> &middot; {c.critical.join(", ")}</>}
            {c.dropped_zones.length > 0 && <> &middot; drops {zones(c.dropped_zones)}</>}
            {c.min_v_pu !== null && <> &middot; min {c.min_v_pu.toFixed(3)} pu</>}
            {c.max_loading_pct !== null && <> &middot; max loading {Math.round(c.max_loading_pct)}%</>}
          </div>
          {c.violations.map((v, j) => (
            <div key={j} className="violation">
              {v.type}: {v.detail}
            </div>
          ))}
          {c.passed && !executed && (
            <button disabled={busy} onClick={() => onApprove(c.plan_id)}>
              Approve and execute
            </button>
          )}
          {executed === c.plan_id && <span className="badge pass">executed</span>}
        </li>
      ))}
    </ol>
  );
}
