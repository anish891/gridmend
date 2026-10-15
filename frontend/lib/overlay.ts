import type { Candidate, GridState, SwitchOp } from "@/lib/api";

export type Problems = { lines: { id: string; label?: string }[]; buses: number[] };

export function withSwitch(state: GridState, op: SwitchOp): GridState {
  const status = op.op === "close" ? "closed" : "open";
  return {
    ...state,
    lines: state.lines.map((l) => (l.id === op.switch_id ? { ...l, status } : l)),
  };
}

// The loop a plan would close: replay its switching, grow a spanning forest over conducting
// lines, and the first line joining two already-connected buses closes the cycle.
function loopLines(state: GridState, ops: SwitchOp[]): string[] {
  const after = ops.reduce(withSwitch, state);
  const conducting = after.lines.filter((l) => l.type === "segment" || l.status === "closed");
  const tree = new Map<number, { bus: number; line: string }[]>();
  const root = new Map<number, number>();
  const find = (b: number): number => {
    while (root.has(b) && root.get(b) !== b) b = root.get(b)!;
    return b;
  };

  for (const line of conducting) {
    const a = find(line.from);
    const b = find(line.to);
    if (a !== b) {
      root.set(a, b);
      root.set(b, b);
      tree.set(line.from, [...(tree.get(line.from) ?? []), { bus: line.to, line: line.id }]);
      tree.set(line.to, [...(tree.get(line.to) ?? []), { bus: line.from, line: line.id }]);
      continue;
    }
    // Path between the two ends through the forest, plus this line, is the loop.
    const previous = new Map<number, { bus: number; line: string }>();
    const queue = [line.from];
    const seen = new Set([line.from]);
    while (queue.length) {
      const bus = queue.shift()!;
      if (bus === line.to) break;
      for (const next of tree.get(bus) ?? []) {
        if (seen.has(next.bus)) continue;
        seen.add(next.bus);
        previous.set(next.bus, { bus, line: next.line });
        queue.push(next.bus);
      }
    }
    const loop = [line.id];
    for (let bus = line.to; previous.has(bus); bus = previous.get(bus)!.bus) {
      loop.push(previous.get(bus)!.line);
    }
    return loop;
  }
  return [];
}

// What to mark on the diagram to show why a candidate failed verification.
export function problemsFor(candidate: Candidate, state: GridState): Problems | null {
  if (candidate.passed) return null;
  const problems: Problems = { lines: [], buses: [] };

  for (const v of candidate.violations) {
    if (v.type === "capacity") {
      // "2-19 carries 1584 kVA of 1400 kVA (113%)"
      const match = v.detail.match(/^(\S+) carries .*\((\d+)%\)/);
      if (match) problems.lines.push({ id: match[1], label: `${match[2]}%` });
    } else if (v.type === "voltage") {
      // "12 buses below 0.90 pu (11, 12, ...); lowest bus 30 at 0.802 pu"
      const list = v.detail.match(/\(([\d, ]+)\)/);
      if (list) problems.buses.push(...list[1].split(",").map((b) => Number(b.trim())));
    } else if (v.type === "radiality") {
      const loop = loopLines(state, candidate.ops);
      problems.lines.push(...loop.map((id, i) => ({ id, label: i === 0 ? "loop" : undefined })));
    } else if (v.type === "isolation") {
      problems.lines.push(
        ...candidate.ops.filter((o) => o.op === "close").map((o) => ({ id: o.switch_id, label: "feeds fault" })),
      );
    } else if (v.type === "connectivity") {
      const zone = Number(v.detail.match(/^Z(\d+)/)?.[1]);
      problems.buses.push(...state.buses.filter((b) => b.zone === zone).map((b) => b.id));
    }
  }
  return problems;
}
