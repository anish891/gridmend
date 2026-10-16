"use client";

import cytoscape, { type Core, type EdgeSingular, type ElementDefinition } from "cytoscape";
import { useEffect, useRef, useState } from "react";

import type { GridState, Line, RiskLine, SwitchOp } from "@/lib/api";
import { BUS_POSITIONS, CURVED_TIES, LABEL_ABOVE, LABEL_LEFT } from "@/lib/layout";
import type { Problems } from "@/lib/overlay";
import { cssVar, onThemeChange } from "@/lib/theme";

// Diagram colours come from the page's theme tokens so the canvas follows light and dark mode.
const COLOR_VARS = {
  live: "--live",
  fresh: "--fresh",
  dark: "--dark",
  faulted: "--faulted",
  switch: "--switch",
  label: "--diagram-label",
  faint: "--diagram-faint",
  panel: "--panel",
  flow: "--flow",
  fi: "--fi",
  close: "--close",
  open: "--open",
};

type Colors = Record<keyof typeof COLOR_VARS, string>;

function readColors(): Colors {
  return Object.fromEntries(
    Object.entries(COLOR_VARS).map(([key, name]) => [key, cssVar(name)]),
  ) as Colors;
}

export type Pulse = { ids: string[]; kind: "fi" | "open" | "close" | "fault"; key: number };

type Tooltip = { x: number; y: number; title: string; lines: string[] };

export type Risk = Record<string, RiskLine>;

// What a fault on a line would leave dark after the best safe restoration, as a colour.
export const RISK_STEPS: [number, string][] = [
  [0, "#a7d7b5"], [300, "#fcd34d"], [600, "#f59e0b"], [1000, "#ea580c"], [2000, "#b91c1c"],
];

function riskColor(kw: number): string {
  return RISK_STEPS.filter(([from]) => kw >= from).at(-1)![1];
}

function carriesText(line: Line): string[] {
  if (!line.carries) return ["carries no load right now"];
  const { kw, buses, critical } = line.carries;
  return [
    `carries ${Math.round(kw)} kW to ${buses} bus${buses === 1 ? "" : "es"}`,
    ...(critical.length ? [`${critical.join(", ")} depend${critical.length === 1 ? "s" : ""} on it`] : []),
  ];
}

// Hover text for a bus, segment or switch, read from the latest grid state.
function describe(
  state: GridState, kind: string, id: string, clickable: boolean, risk: Risk | null,
): Omit<Tooltip, "x" | "y"> {
  if (kind === "bus") {
    const bus = state.buses.find((b) => `${b.id}` === id)!;
    if (bus.is_source) return { title: "Substation", lines: ["bus 1, feeder source"] };
    return {
      title: bus.facility ? `Bus ${bus.id}: ${bus.facility}` : `Bus ${bus.id}`,
      lines: [`zone Z${bus.zone}, ${Math.round(bus.p_kw)} kW`, bus.energized ? "energized" : "de-energized"],
    };
  }
  const line = state.lines.find((l) => l.id === id)!;
  if (line.type === "segment") {
    const atRisk = risk?.[line.id];
    return {
      title: `Line ${line.id}`,
      lines: [
        `unswitched segment, rated ${line.cap_kva} kVA`,
        ...carriesText(line),
        ...(atRisk
          ? [`if it faults: ${Math.round(atRisk.unserved_kw)} kW stays dark after the best safe restoration`,
             atRisk.critical_dark.length ? `critical loads lost: ${atRisk.critical_dark.join(", ")}` : "no critical load lost"]
          : []),
        ...(line.faulted ? ["FAULTED"] : []),
        ...(clickable ? ["click to fault this line"] : []),
      ],
    };
  }
  return {
    title: `${line.kind[0].toUpperCase()}${line.kind.slice(1)} ${line.id}`,
    lines: [
      `${line.status}${line.status !== line.normally ? ` (normally ${line.normally})` : ""}`,
      ...(line.fi && line.fi !== "none" ? [`fault indicator: ${line.fi}`] : []),
      `rated ${line.cap_kva} kVA`,
      ...carriesText(line),
    ],
  };
}

function midpoint(line: Line): [number, number] {
  const [x1, y1] = BUS_POSITIONS[line.from];
  const [x2, y2] = BUS_POSITIONS[line.to];
  const bow = CURVED_TIES.has(line.id) ? 55 : 0;
  return [(x1 + x2) / 2, (y1 + y2) / 2 + bow];
}

export function toElements(state: GridState): ElementDefinition[] {
  const faultedZones = new Set(state.zones.filter((z) => z.faulted).map((z) => z.id));
  const energized = new Map(state.buses.map((b) => [b.id, b.energized]));
  const busState = (id: number) => {
    const bus = state.buses.find((b) => b.id === id)!;
    if (bus.zone !== null && faultedZones.has(bus.zone)) return "faulted";
    return bus.energized ? "live" : "dark";
  };

  const elements: ElementDefinition[] = state.buses.map((bus) => {
    const [x, y] = BUS_POSITIONS[bus.id];
    const classes = [busState(bus.id)];
    if (bus.critical) classes.push("critical");
    if (bus.is_source) classes.push("source");
    if (LABEL_ABOVE.has(bus.id)) classes.push("label-above");
    if (LABEL_LEFT.has(bus.id)) classes.push("label-left");
    return {
      group: "nodes",
      data: {
        id: `b${bus.id}`,
        kind: "bus",
        ref: `${bus.id}`,
        label: bus.is_source ? "substation" : bus.critical ? `${bus.id} ${bus.facility}` : `${bus.id}`,
      },
      position: { x, y },
      classes: classes.join(" "),
    };
  });

  for (const line of state.lines) {
    const from = `b${line.from}`;
    const to = `b${line.to}`;
    if (line.type === "segment") {
      const live = energized.get(line.from) && energized.get(line.to);
      elements.push({
        group: "edges",
        data: { id: `l${line.id}`, source: from, target: to, kind: "line", ref: line.id },
        classes: ["segment", line.faulted ? "faulted" : live ? "live" : "dark"].join(" "),
      });
      continue;
    }

    const [x, y] = midpoint(line);
    const sw = `s${line.id}`;
    elements.push({
      group: "nodes",
      data: { id: sw, label: line.id, kind: "line", ref: line.id },
      position: { x, y },
      // A switch on a vertical lateral is labelled beside the line instead of on it.
      classes: `switch ${line.kind} ${line.status} fi-${line.fi}`
        + (BUS_POSITIONS[line.from][0] === BUS_POSITIONS[line.to][0] ? " vertical" : ""),
    });
    for (const [bus, side] of [[from, "a"], [to, "b"]] as const) {
      const busId = Number(bus.slice(1));
      const live = line.status === "closed" && energized.get(busId);
      elements.push({
        group: "edges",
        data: { id: `${sw}-${side}`, source: bus, target: sw, kind: "line", ref: line.id },
        classes: [line.kind, live ? "live" : "dark"].join(" "),
      });
    }
  }
  return elements;
}

// Walk outward from the substation over live edges and add a moving-dash overlay to each
// one, oriented the way power actually flows (ties can carry it either way).
function addFlowOverlay(graph: Core): void {
  const visited = new Set(["b1"]);
  const queue = ["b1"];
  const overlays: ElementDefinition[] = [];
  while (queue.length) {
    const id = queue.shift()!;
    graph.getElementById(id).connectedEdges(".live").forEach((edge: EdgeSingular) => {
      const forward = edge.source().id() === id;
      const next = forward ? edge.target().id() : edge.source().id();
      if (visited.has(next)) return;
      visited.add(next);
      queue.push(next);
      overlays.push({
        group: "edges",
        data: { id: `flow-${edge.id()}`, source: id, target: next },
        classes: "flow",
      });
    });
  }
  graph.add(overlays);
}

const styleFor = (COLORS: Colors): cytoscape.StylesheetJson => [
  {
    selector: "node",
    style: {
      width: 14,
      height: 14,
      "background-color": COLORS.dark,
      label: "data(label)",
      "font-size": 13,
      color: COLORS.label,
      "text-valign": "bottom",
      "text-margin-y": 5,
      "transition-property": "width, height, border-width, border-color, background-color",
      "transition-duration": 0.35,
    },
  },
  { selector: "node.live", style: { "background-color": COLORS.live } },
  { selector: "node.faulted", style: { "background-color": COLORS.faulted } },
  {
    selector: "node.critical",
    style: { shape: "star", width: 28, height: 28, "font-weight": "bold", "font-size": 14 },
  },
  { selector: "node.source", style: { shape: "round-rectangle", width: 40, height: 24, "font-weight": "bold" } },
  {
    selector: "node.switch",
    style: {
      shape: "rectangle",
      width: 12,
      height: 12,
      "background-color": COLORS.switch,
      "border-width": 1.5,
      "border-color": COLORS.switch,
      "font-size": 11,
      "text-valign": "top",
      "text-margin-y": -3,
    },
  },
  {
    selector: "node.switch.vertical",
    style: { "text-valign": "center", "text-halign": "right", "text-margin-x": 5, "text-margin-y": 0 },
  },
  { selector: "node.label-above", style: { "text-valign": "top", "text-margin-y": -5 } },
  { selector: "node.label-left", style: { "text-halign": "left", "text-margin-x": -3 } },
  { selector: "node.switch.open", style: { "background-color": COLORS.panel } },
  { selector: "node.switch.tie", style: { "font-size": 10, color: COLORS.faint } },
  { selector: "node.switch.breaker", style: { width: 16, height: 16 } },
  { selector: "node.switch.fi-set", style: { "border-color": COLORS.fi, "border-width": 3 } },
  {
    selector: "edge",
    style: {
      width: 3.5,
      "line-color": COLORS.dark,
      "curve-style": "straight",
      "transition-property": "width, line-color",
      "transition-duration": 0.5,
    },
  },
  { selector: "edge.live", style: { "line-color": COLORS.live } },
  { selector: "edge.faulted", style: { "line-color": COLORS.faulted, width: 5 } },
  { selector: "edge.tie", style: { "line-style": "dashed", width: 2 } },
  { selector: "edge.tie.live", style: { "line-style": "solid", width: 3 } },
  // Moving dashes on top of live lines: power flowing away from the substation.
  {
    selector: "edge.flow",
    style: {
      width: 1.6,
      "line-color": COLORS.flow,
      "line-opacity": 0.7,
      "line-style": "dashed",
      "line-dash-pattern": [5, 9],
      events: "no",
      "transition-duration": 0,
    },
  },
  // Lines that just came back: a brief bright glow.
  { selector: "edge.fresh", style: { width: 8, "line-color": COLORS.fresh } },
  { selector: "node.fresh", style: { width: 20, height: 20 } },
  { selector: "node.critical.fresh", style: { width: 34, height: 34 } },
  // Step-by-step replay.
  { selector: "node.pulse-fi", style: { "border-color": COLORS.fi, "border-width": 6, width: 22, height: 22 } },
  { selector: "node.pulse-open", style: { "border-color": COLORS.open, "border-width": 6, width: 22, height: 22 } },
  { selector: "node.pulse-close", style: { "border-color": COLORS.close, "border-width": 6, width: 22, height: 22 } },
  { selector: "edge.pulse-fault", style: { width: 10, "line-color": COLORS.faulted } },
  // A candidate plan under review: switches it would close or open.
  {
    selector: "node.switch.to-close",
    style: { "border-color": COLORS.close, "border-width": 4, width: 15, height: 15 },
  },
  {
    selector: "node.switch.to-open",
    style: { "border-color": COLORS.open, "border-width": 4, width: 15, height: 15 },
  },
  // Why a candidate was rejected.
  {
    selector: "edge.problem",
    style: {
      width: 7,
      "line-color": COLORS.faulted,
      "line-style": "solid",
      label: "data(problem)",
      "font-size": 11,
      "font-weight": "bold",
      color: COLORS.faulted,
      "text-background-color": COLORS.panel,
      "text-background-opacity": 0.9,
      "text-background-padding": "2px",
    },
  },
  {
    selector: "node.problem",
    style: { "border-color": COLORS.faulted, "border-width": 4, width: 18, height: 18 },
  },
  { selector: "node.low-v", style: { "border-color": COLORS.faulted, "border-width": 3 } },
  { selector: "edge.segment.hover", style: { width: 7 } },
  // Risk map: segments coloured by what a fault there leaves dark; everything else recedes.
  { selector: ".risk-faded", style: { opacity: 0.25 } },
  { selector: "edge.risk", style: { width: 7, "line-color": "data(risk)", "line-style": "solid" } },
];

type Props = {
  state: GridState;
  onSegmentClick?: (lineId: string) => void;
  highlight?: SwitchOp[];
  pulse?: Pulse | null;
  problems?: Problems | null;
  risk?: Risk | null;
};

const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export default function FeederDiagram({ state, onSegmentClick, highlight = [], pulse, problems, risk = null }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  // Elements are rebuilt on every state change, so handlers read the latest values.
  const onClick = useRef(onSegmentClick);
  onClick.current = onSegmentClick;
  const latest = useRef(state);
  latest.current = state;
  const latestRisk = useRef(risk);
  latestRisk.current = risk;
  const liveBefore = useRef<Set<string> | null>(null);
  const [tooltip, setTooltip] = useState<Tooltip | null>(null);

  useEffect(() => {
    const graph = cytoscape({
      container: container.current,
      style: styleFor(readColors()),
      layout: { name: "preset" },
      autoungrabify: true,
      boxSelectionEnabled: false,
      minZoom: 0.3,
      maxZoom: 3,
    });
    cy.current = graph;
    graph.on("tap", "edge.segment", (event) => onClick.current?.(event.target.data("ref")));
    graph.on("mouseover", "edge.segment", (event) => {
      if (!onClick.current) return;
      event.target.addClass("hover");
      container.current!.style.cursor = "pointer";
    });
    graph.on("mouseout", "edge.segment", (event) => {
      event.target.removeClass("hover");
      container.current!.style.cursor = "default";
    });
    graph.on("mouseover", "node, edge[kind]", (event) => {
      const { x, y } = event.renderedPosition ?? event.target.renderedPosition();
      const info = describe(latest.current, event.target.data("kind"), event.target.data("ref"),
        Boolean(onClick.current), latestRisk.current);
      setTooltip({ x, y, ...info });
    });
    graph.on("mouseout", "node, edge", () => setTooltip(null));
    graph.on("viewport", () => setTooltip(null));

    // The container's size settles after mount and changes with the window, so refit.
    const observer = new ResizeObserver(() => {
      graph.resize();
      graph.fit(undefined, 16);
    });
    observer.observe(container.current!);

    let frame = 0;
    let offset = 0;
    const animate = () => {
      offset = (offset + 0.6) % 1400;
      graph.edges(".flow").style("line-dash-offset", -offset);
      frame = requestAnimationFrame(animate);
    };
    if (!reducedMotion()) frame = requestAnimationFrame(animate);

    const stopTheme = onThemeChange(() => graph.style(styleFor(readColors())));

    return () => {
      stopTheme();
      cancelAnimationFrame(frame);
      observer.disconnect();
      graph.destroy();
    };
  }, []);

  useEffect(() => {
    const graph = cy.current;
    if (!graph) return;
    const first = graph.elements().empty();
    graph.elements().remove();
    graph.add(toElements(state));
    addFlowOverlay(graph);
    if (first) {
      graph.resize();
      graph.fit(undefined, 16);
    }

    // Anything live now that was dark before glows briefly as power returns.
    const live = new Set(graph.elements(".live").map((e) => e.id()));
    if (liveBefore.current) {
      const fresh = graph.elements(".live").filter((e) => !liveBefore.current!.has(e.id()));
      fresh.flashClass("fresh", 1600);
    }
    liveBefore.current = live;
  }, [state]);

  useEffect(() => {
    const graph = cy.current;
    if (!graph) return;
    graph.nodes(".switch").removeClass("to-close to-open");
    for (const op of highlight) {
      graph.getElementById(`s${op.switch_id}`).addClass(op.op === "close" ? "to-close" : "to-open");
    }
  }, [state, highlight]);

  useEffect(() => {
    const graph = cy.current;
    if (!graph || !pulse) return;
    for (const id of pulse.ids) {
      const target = pulse.kind === "fault" ? graph.getElementById(`l${id}`) : graph.getElementById(`s${id}`);
      target.flashClass(`pulse-${pulse.kind}`, 900);
    }
  }, [pulse]);

  useEffect(() => {
    const graph = cy.current;
    if (!graph) return;
    graph.elements(".problem, .low-v").removeClass("problem low-v").removeData("problem");
    if (!problems) return;
    for (const line of problems.lines) {
      const parts = graph.elements(`#l${line.id}, #s${line.id}, #s${line.id}-a, #s${line.id}-b`);
      parts.addClass("problem");
      if (line.label) parts.edges().first().data("problem", line.label);
    }
    for (const bus of problems.buses) graph.getElementById(`b${bus}`).addClass("low-v");
  }, [state, problems]);

  useEffect(() => {
    const graph = cy.current;
    if (!graph) return;
    graph.elements(".risk, .risk-faded").removeClass("risk risk-faded");
    if (!risk) return;
    graph.elements().not("edge.segment, node[kind = 'bus']").addClass("risk-faded");
    graph.edges("edge.segment").forEach((edge) => {
      const line = risk[edge.data("ref")];
      if (line) edge.data("risk", riskColor(line.unserved_kw)).addClass("risk");
    });
  }, [state, risk]);

  return (
    <div className="diagram-wrap">
      <div ref={container} className="diagram" />
      {tooltip && (
        // Near the top of the diagram there is no room above the cursor, so show it below.
        <div className={tooltip.y < 150 ? "tooltip below" : "tooltip"} style={{ left: tooltip.x, top: tooltip.y }}>
          <b>{tooltip.title}</b>
          {tooltip.lines.map((line) => <div key={line}>{line}</div>)}
        </div>
      )}
    </div>
  );
}
