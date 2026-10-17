import Card from "@/components/ui/Card";

const TERMS = [
  ["Zone", "A section of the feeder between switches; the smallest piece that can be cut out. Named by its first bus, so Z7 starts at bus 7."],
  ["Tie switch", "A backup switch that is normally open. Closing it feeds a dark section from another route."],
  ["Fault indicator", "Flags when fault current passes through it. Following the flags from the substation leads to the fault."],
  ["Fork", "A throwaway copy of the live graph. Each plan is tried on its own fork, so an unsafe plan never touches the grid."],
  ["pu (per unit)", "Voltage as a fraction of normal (12.66 kV). Every bus must stay at 0.90 or above."],
];

export default function Intro() {
  return (
    <Card as="aside" className="intro" title="How it works">
      <ol className="how">
        <li><b>Locate.</b> The agent reads the tripped fault indicators and decides where the fault is.</li>
        <li><b>Isolate.</b> It writes the switching that cuts that section out and turns the breaker back on.</li>
        <li><b>Design.</b> It plans backup routes into the sections still dark, and revises when one fails.</li>
        <li><b>Check.</b> Every plan runs on a copy of the grid first: no loops, no overloads, voltage within limits.</li>
        <li><b>Approve.</b> You approve the plan it recommends. Every decision is recorded and explained.</li>
      </ol>
      <details className="glossary">
        <summary>Key terms</summary>
        <dl>
          {TERMS.map(([term, meaning]) => (
            <div key={term}>
              <dt>{term}</dt>
              <dd>{meaning}</dd>
            </div>
          ))}
        </dl>
      </details>
    </Card>
  );
}
