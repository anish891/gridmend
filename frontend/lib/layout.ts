// Fixed single-line layout: main feeder 1-18 left to right, laterals as rows that
// start straight below or above their branch bus: 2 (below), 3 (above), 6 (further below).
const STEP = 80;
const ROW = 110;

function row(first: number, last: number, startX: number, y: number): Record<number, [number, number]> {
  const positions: Record<number, [number, number]> = {};
  for (let bus = first; bus <= last; bus++) {
    positions[bus] = [startX + (bus - first) * STEP, y];
  }
  return positions;
}

const MAIN = row(1, 18, 0, 0);

export const BUS_POSITIONS: Record<number, [number, number]> = {
  ...MAIN,
  ...row(19, 22, MAIN[2][0], ROW),
  ...row(23, 25, MAIN[3][0], -ROW),
  ...row(26, 33, MAIN[6][0], 2 * ROW),
};

// Ties between two buses on the same row would sit on top of the feeder, so they bow out.
export const CURVED_TIES = new Set(["9-15"]);

// Bus labels normally sit below the bus. The upper lateral is labelled above it, and buses
// where a lateral drops straight down are labelled to the left so the line does not cross it.
export const LABEL_ABOVE = new Set([23, 24, 25]);
export const LABEL_LEFT = new Set([2, 6]);
