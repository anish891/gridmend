const DEMO_FAULTS = [
  { line: "4-5", note: "Main feeder. Hospital and fire station are at stake; the obvious tie overloads." },
  { line: "10-11", note: "Mid feeder. Only the fire station's zone needs re-feeding." },
  { line: "27-28", note: "Lateral. The only tie sags voltage below 0.90 pu, so nothing is safe." },
];

type Props = { disabled: boolean; onFault: (line: string) => void };

export default function Intro({ disabled, onFault }: Props) {
  return (
    <aside className="card intro">
      <h2>How it works</h2>
      <ol className="how">
        <li><b>Locate</b> the faulted zone from the chain of tripped fault indicators.</li>
        <li><b>Isolate</b> it and reclose the breaker so everything upstream comes back.</li>
        <li><b>Plan</b> re-feeds by searching the graph between dead and live zones.</li>
        <li><b>Verify</b> every plan on a forked copy of the graph: isolation, radiality, line capacity, voltage.</li>
        <li><b>Approve</b> one that passed. Every decision is stored in the graph and explained.</li>
      </ol>
      <h3>Try a fault</h3>
      <div className="demo-faults">
        {DEMO_FAULTS.map((f) => (
          <button key={f.line} className="demo-fault" disabled={disabled} onClick={() => onFault(f.line)}>
            <span className="demo-line">line {f.line}</span>
            <span className="demo-note">{f.note}</span>
          </button>
        ))}
      </div>
      <p className="muted small">Or click any line segment on the diagram.</p>
    </aside>
  );
}
