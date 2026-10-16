import Card from "@/components/ui/Card";
import Segmented from "@/components/ui/Segmented";

const DEMO_FAULTS = [
  {
    line: "4-5",
    title: "Main line fault",
    note: "The hospital and fire station lose power, and the obvious fix would overload a line.",
  },
  {
    line: "10-11",
    title: "Mid-feeder fault",
    note: "Only the fire station's section needs power routed in from elsewhere.",
  },
  {
    line: "27-28",
    title: "Branch fault",
    note: "The only backup route drops voltage too low, so no switching is safe.",
  },
];

export type Mode = "rules" | "agent";

type Props = {
  disabled: boolean;
  onFault: (line: string) => void;
  agentModel: string | null;
  mode: Mode;
  onMode: (mode: Mode) => void;
  // Today's conditions, shown before the faults because they set the priorities.
  conditions?: React.ReactNode;
};

export default function FaultPicker({ disabled, onFault, agentModel, mode, onMode, conditions }: Props) {
  return (
    <Card className="hero">
      <h1>Simulate a fault and watch the feeder heal itself</h1>
      <p className="hero-lead">
        GridMend finds the damaged section, cuts it out and re-routes power around it. Every plan is
        safety-checked on a copy of the grid before a single switch moves.
      </p>
      {conditions}
      {conditions && <h2 className="conditions-title">Pick a fault</h2>}
      <div className="demo-faults">
        {DEMO_FAULTS.map((f) => (
          <button key={f.line} type="button" className="demo-fault" disabled={disabled} onClick={() => onFault(f.line)}>
            <span className="demo-title">{f.title}</span>
            <span className="demo-line">line {f.line}</span>
            <span className="demo-note">{f.note}</span>
          </button>
        ))}
      </div>
      <div className="hero-foot">
        <p className="muted small">Or click any line on the feeder map below.</p>
        {agentModel && (
          <Segmented
            label="Who handles the incident"
            value={mode}
            onChange={onMode}
            options={[
              { value: "rules", label: "Rules", hint: "instant and deterministic" },
              { value: "agent", label: "AI operator agent", hint: `${agentModel}, takes 1 to 2 minutes` },
            ]}
          />
        )}
      </div>
    </Card>
  );
}
