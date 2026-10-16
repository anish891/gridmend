import type { ResultRow } from "@/lib/api";

// Series colours are theme tokens, applied through style so CSS variables resolve.
const METHOD_COLORS: Record<string, string> = {
  optimal: "var(--optimal)",
  gridmend: "var(--live)",
  "llm-only": "var(--llm)",
  greedy: "var(--greedy)",
};

const WIDTH = 1100;
const HEIGHT = 300;
const PAD = { left: 52, right: 8, top: 14, bottom: 30 };

// A round tick step (1, 2 or 5 times a power of ten) giving about four intervals.
function tickStep(max: number): number {
  const rough = max / 4;
  const power = 10 ** Math.floor(Math.log10(rough));
  return [1, 2, 5, 10].map((m) => m * power).find((step) => step >= rough)!;
}

// Grouped bars of restored kW per fault. An unsafe plan restores nothing and is marked with a red x.
export default function ResultsChart({ rows }: { rows: ResultRow[] }) {
  const methods = Object.keys(METHOD_COLORS).filter((m) => rows.some((r) => r.method === m));
  const faults = [...new Set(rows.filter((r) => r.optimal_kw > 0).map((r) => r.fault))];
  if (faults.length === 0) return <p className="muted">No fault leaves restorable load.</p>;

  const step = tickStep(Math.max(...rows.map((r) => r.optimal_kw)));
  const top = Math.ceil(Math.max(...rows.map((r) => r.optimal_kw)) / step) * step;
  const ticks = Array.from({ length: top / step + 1 }, (_, i) => i * step);
  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const groupW = plotW / faults.length;
  const barW = (groupW * 0.7) / methods.length;
  const y = (kw: number) => PAD.top + plotH - (kw / top) * plotH;

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="Restored kW per fault by method">
        {ticks.map((kw) => (
          <g key={kw}>
            <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(kw)} y2={y(kw)} style={{ stroke: "var(--chart-grid)" }} />
            <text x={PAD.left - 8} y={y(kw) + 4} textAnchor="end" fontSize="12" style={{ fill: "var(--muted)" }}>
              {kw}
            </text>
          </g>
        ))}
        {faults.map((fault, i) => {
          const x0 = PAD.left + i * groupW + groupW * 0.15;
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
                      width={barW - 2}
                      height={y(0) - y(row.restored_kw)}
                      rx={2}
                      style={{ fill: METHOD_COLORS[method] }}
                    >
                      <title>{`${method}: ${Math.round(row.restored_kw)} kW, ${row.violations} violations`}</title>
                    </rect>
                    {row.violations > 0 && (
                      <text x={x + barW / 2} y={y(0) - 4} textAnchor="middle" fontSize="14" fontWeight="700"
                        style={{ fill: "var(--faulted)" }}>
                        ×
                      </text>
                    )}
                  </g>
                );
              })}
              <text x={x0 + (groupW * 0.7) / 2} y={HEIGHT - 8} textAnchor="middle" fontSize="12"
                style={{ fill: "var(--text)" }}>
                {fault}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="legend">
        {methods.map((m) => (
          <span key={m}>
            <span className="swatch" style={{ background: METHOD_COLORS[m], borderRadius: 2 }} /> {m}
          </span>
        ))}
        <span>
          <span style={{ color: "var(--faulted)", fontWeight: 700 }}>×</span> unsafe plan (restores nothing)
        </span>
        <span className="chart-note">Restored kW, faults that leave restorable load</span>
      </figcaption>
    </figure>
  );
}
