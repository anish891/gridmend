export const STEPS = ["Fault", "Locate", "Isolate", "Verify plans", "Approve", "Restored"] as const;

type Props = {
  // Steps before `done` are complete; `active` is running now (null when idle).
  done: number;
  active: number | null;
  elapsed: number;
  message: React.ReactNode;
};

export default function Stepper({ done, active, elapsed, message }: Props) {
  return (
    <section className="stepper" aria-label="Restoration progress">
      <ol>
        {STEPS.map((label, i) => {
          const state = i === active ? "active" : i < done ? "done" : "todo";
          return (
            <li key={label} className={state}>
              <span className="step-mark">{state === "done" ? "✓" : i + 1}</span>
              <span className="step-label">{label}</span>
            </li>
          );
        })}
      </ol>
      {message && (
        <div className="step-message">
          {active !== null && <span className="spinner" />}
          {message}
          {active !== null && elapsed >= 2 && <span className="muted"> · {elapsed} s</span>}
        </div>
      )}
    </section>
  );
}
