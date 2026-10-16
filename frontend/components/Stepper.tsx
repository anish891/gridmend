import Spinner from "@/components/ui/Spinner";

export const STEPS = ["Fault", "Locate", "Isolate", "Check plans", "Approve", "Restored"] as const;

type Props = {
  // Steps before `done` are complete; `active` is running now (null when idle).
  done: number;
  active: number | null;
  elapsed: number;
  message: React.ReactNode;
  // The next thing the operator can do, shown beside the message.
  action?: React.ReactNode;
};

export default function Stepper({ done, active, elapsed, message, action }: Props) {
  return (
    <section className="stepper" aria-label="Restoration progress">
      <ol>
        {STEPS.map((label, i) => {
          const state = i === active ? "active" : i < done ? "done" : "todo";
          return (
            <li key={label} className={state} aria-current={state === "active" ? "step" : undefined}>
              <span className="step-mark">{state === "done" ? "✓" : i + 1}</span>
              <span className="step-label">{label}</span>
            </li>
          );
        })}
      </ol>
      {(message || action) && (
        <div className="step-message">
          <div className="step-text" aria-live="polite">
            {active !== null && <Spinner />}
            {message}
            {active !== null && elapsed >= 2 && <span className="muted"> · {elapsed} s</span>}
          </div>
          {action}
        </div>
      )}
    </section>
  );
}
