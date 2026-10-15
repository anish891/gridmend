import type { ResultRow } from "@/lib/api";

const METHOD_COLORS: Record<string, string> = {
  optimal: "#bdc1c6",
  gridmend: "#2e9d57",
  "llm-only": "#1a73e8",
  greedy: "#f29900",
};

const WIDTH = 760;
const HEIGHT = 260;
const PAD = { left: 48, right: 8, top: 12, bottom: 36 };

// Grouped bars of restored kW per fault. An unsafe plan restores nothing and is marked with a red x.
export default function ResultsChart({ rows }: { rows: ResultRow[] }) {
  const methods = Object.keys(METHOD_COLORS).filter((m) => rows.some((r) => r.method === m));
  const faults = [...new Set(rows.filter((r) => r.optimal_kw > 0).map((r) => r.fault))];
  if (faults.length === 0) return <p className="muted">No fault leaves restorable load.</p>;

  const maxKw = Math.max(...rows.map((r) => r.optimal_kw));
  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const groupW = plotW / faults.length;
  const barW = (groupW * 0.8) / methods.length;
  const y = (kw: number) => PAD.top + plotH - (kw / maxKw) * plotH;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * maxKw));

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="Restored kW per fault by method">
        {ticks.map((kw) => (
          <g key={kw}>
            <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(kw)} y2={y(kw)} stroke="#e8eaed" />
            <text x={PAD.left - 6} y={y(kw) + 3} textAnchor="end" fontSize="10" fill="#5f6368">
              {kw}
            </text>
          </g>
        ))}
        {faults.map((fault, i) => {
          const x0 = PAD.left + i * groupW + groupW * 0.1;
          return (
            <g key={fault}>
              {methods.map((method, j) => {
                const row = rows.find((r) => r.fault === fault && r.method === method);
                if (!row) return null;
                const x = x0 + j * barW;
                return (
                  <g key={method}>
                    <rect
                      x={x}
                      y={y(row.restored_kw)}
                      width={barW - 1}
                      height={y(0) - y(row.restored_kw)}
                      fill={METHOD_COLORS[method]}
                    >
                      <title>{`${method}: ${Math.round(row.restored_kw)} kW, ${row.violations} violations`}</title>
                    </rect>
                    {row.violations > 0 && (
                      <text x={x + barW / 2} y={y(0) - 3} textAnchor="middle" fontSize="11" fill="#d93025">
                        ×
                      </text>
                    )}
                  </g>
                );
              })}
              <text x={x0 + (groupW * 0.8) / 2} y={HEIGHT - 18} textAnchor="middle" fontSize="10" fill="#3c4043">
                {fault}
              </text>
            </g>
          );
        })}
        <text x={PAD.left} y={HEIGHT - 4} fontSize="10" fill="#5f6368">
          fault (only faults with restorable load); y: restored kW
        </text>
      </svg>
      <figcaption className="legend">
        {methods.map((m) => (
          <span key={m}>
            <span className="swatch" style={{ background: METHOD_COLORS[m], borderRadius: 2 }} /> {m}
          </span>
        ))}
        <span>
          <span style={{ color: "#d93025" }}>×</span> unsafe plan (restores nothing)
        </span>
      </figcaption>
    </figure>
  );
}
