import Button from "@/components/ui/Button";
import type { FeederConfig } from "@/lib/feeders";

// What goes wrong: segments that fault, and switches that will not operate when the agent needs them.
export type Scenario = { lines: string[]; switches: string[] };

export const MAX_FAULTS = 3;

type Props = {
  feeder: FeederConfig;
  scenario: Scenario;
  onChange: (scenario: Scenario) => void;
  onRun: (scenario: Scenario) => void;
  disabled: boolean;
};

function Picks({ label, ids, kind, empty, onRemove }: {
  label: string; ids: string[]; kind: "fault" | "jam"; empty: string; onRemove: (id: string) => void;
}) {
  return (
    <div className="pick-row">
      <span className="pick-label">{label}</span>
      <div className="pick-chips">
        {ids.length === 0 && <span className="muted small">{empty}</span>}
        {ids.map((id) => (
          <button key={id} type="button" className={`pick-chip ${kind}`} onClick={() => onRemove(id)}
            aria-label={`Remove ${id}`}>
            {id} <span aria-hidden="true">×</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export default function ScenarioBuilder({ feeder, scenario, onChange, onRun, disabled }: Props) {
  const presets = [
    ...feeder.faults.map((f) => ({ title: f.title, lines: [f.line], note: f.note })),
    ...feeder.doubles.map((f) => ({ title: f.title, lines: f.lines, note: f.note })),
  ];
  const picked = scenario.lines.length + scenario.switches.length > 0;
  return (
    <div className="scenario">
      <section>
        <h3 className="panel-heading">Build what goes wrong</h3>
        <p className="muted small">
          Click lines on the map to fault them (up to {MAX_FAULTS}), and switches to make them jam, as
          a field crew might find them. Then trip the feeder and let the agent handle it.
        </p>
        <Picks label="Faults" kind="fault" ids={scenario.lines} empty="click a line on the map"
          onRemove={(id) => onChange({ ...scenario, lines: scenario.lines.filter((l) => l !== id) })} />
        <Picks label="Jammed" kind="jam" ids={scenario.switches} empty="optional: click a switch"
          onRemove={(id) => onChange({ ...scenario, switches: scenario.switches.filter((s) => s !== id) })} />
        <div className="scenario-actions">
          <Button disabled={disabled || scenario.lines.length === 0} onClick={() => onRun(scenario)}>
            Trip the feeder
          </Button>
          {picked && (
            <Button variant="secondary" disabled={disabled} onClick={() => onChange({ lines: [], switches: [] })}>
              Clear
            </Button>
          )}
        </div>
      </section>
      <section>
        <h3 className="panel-heading">Or start from a known incident</h3>
        <ul className="presets">
          {presets.map((p) => (
            <li key={p.title}>
              <button type="button" className="preset" disabled={disabled}
                onClick={() => onRun({ lines: p.lines, switches: [] })}>
                <span className="preset-title">{p.title}</span>
                <span className="preset-lines">{p.lines.join(" + ")}</span>
                <span className="preset-note">{p.note}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
