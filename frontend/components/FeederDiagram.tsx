"use client";

import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import { useEffect, useRef, useState } from "react";

import type { GridState, Line, SwitchOp } from "@/lib/api";
import { BUS_POSITIONS, CURVED_TIES } from "@/lib/layout";

const COLORS = {
  live: "#1e8e4e",
  dark: "#b0b6bd",
  faulted: "#d93025",
  switchBorder: "#1f2328",
  label: "#4a5058",
  fi: "#e8710a",
  close: "#1a73e8",
  open: "#9334e6",
};

type Tooltip = { x: number; y: number; title: string; lines: string[] };

// Hover text for a bus, segment or switch, read from the latest grid state.
function describe(state: GridState, kind: string, id: string, clickable: boolean): Omit<Tooltip, "x" | "y"> {
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
    return {
      title: `Line ${line.id}`,
      lines: [
        `unswitched segment, rated ${line.cap_kva} kVA`,
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
        data: { id: `l${line.id}`, source: from, target: to, line: line.id, kind: "line", ref: line.id },
        classes: ["segment", line.faulted ? "faulted" : live ? "live" : "dark"].join(" "),
      });
      continue;
    }

    const [x, y] = midpoint(line);
    const sw = `s${line.id}`;
    elements.push({
      group: "nodes",
      data: { id: sw, label: line.id, line: line.id, kind: "line", ref: line.id },
      position: { x, y },
      classes: `switch ${line.kind} ${line.status} fi-${line.fi}`,
    });
    for (const [bus, side] of [[from, "a"], [to, "b"]] as const) {
      const busId = Number(bus.slice(1));
      const live = line.status === "closed" && energized.get(busId);
      elements.push({
        group: "edges",
        data: { id: `${sw}-${side}`, source: bus, target: sw, line: line.id },
        classes: [line.kind, live ? "live" : "dark"].join(" "),
      });
    }
  }
  return elements;
}

const STYLE: cytoscape.StylesheetJson = [
  {
    selector: "node",
    style: {
      width: 12,
      height: 12,
      "background-color": COLORS.dark,
      label: "data(label)",
      "font-size": 10,
      color: COLORS.label,
      "text-valign": "bottom",
      "text-margin-y": 4,
    },
  },
  { selector: "node.live", style: { "background-color": COLORS.live } },
  { selector: "node.faulted", style: { "background-color": COLORS.faulted } },
  {
    selector: "node.critical",
    style: { shape: "star", width: 24, height: 24, "font-weight": "bold", "font-size": 11 },
  },
  { selector: "node.source", style: { shape: "round-rectangle", width: 34, height: 22 } },
  {
    selector: "node.switch",
    style: {
      shape: "rectangle",
      width: 10,
      height: 10,
      "background-color": COLORS.switchBorder,
      "border-width": 1.5,
      "border-color": COLORS.switchBorder,
      "font-size": 8,
      "text-valign": "top",
      "text-margin-y": -3,
    },
  },
  { selector: "node.switch.open", style: { "background-color": "#ffffff" } },
  { selector: "node.switch.tie", style: { "font-size": 7, color: "#80868b" } },
  { selector: "node.switch.breaker", style: { width: 14, height: 14 } },
  { selector: "node.switch.fi-set", style: { "border-color": COLORS.fi, "border-width": 3 } },
  {
    selector: "edge",
    style: { width: 3, "line-color": COLORS.dark, "curve-style": "straight" },
  },
  { selector: "edge.live", style: { "line-color": COLORS.live } },
  { selector: "edge.faulted", style: { "line-color": COLORS.faulted, width: 5 } },
  { selector: "edge.tie", style: { "line-style": "dashed", width: 2 } },
  // A candidate plan under review: switches it would close or open.
  {
    selector: "node.switch.to-close",
    style: { "border-color": COLORS.close, "border-width": 4, width: 15, height: 15 },
  },
  {
    selector: "node.switch.to-open",
    style: { "border-color": COLORS.open, "border-width": 4, width: 15, height: 15 },
  },
  { selector: "edge.segment.hover", style: { width: 7 } },
];

type Props = {
  state: GridState;
  onSegmentClick?: (lineId: string) => void;
  highlight?: SwitchOp[];
};

export default function FeederDiagram({ state, onSegmentClick, highlight = [] }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  // Elements are rebuilt on every state change, so the tap handler reads the latest callback.
  const onClick = useRef(onSegmentClick);
  onClick.current = onSegmentClick;
  const latest = useRef(state);
  latest.current = state;
  const [tooltip, setTooltip] = useState<Tooltip | null>(null);

  useEffect(() => {
    cy.current = cytoscape({
      container: container.current,
      style: STYLE,
      layout: { name: "preset" },
      autoungrabify: true,
      boxSelectionEnabled: false,
      minZoom: 0.3,
      maxZoom: 3,
    });
    cy.current.on("tap", "edge.segment", (event) => onClick.current?.(event.target.data("line")));
    cy.current.on("mouseover", "edge.segment", (event) => {
      if (!onClick.current) return;
      event.target.addClass("hover");
      container.current!.style.cursor = "pointer";
    });
    cy.current.on("mouseout", "edge.segment", (event) => {
      event.target.removeClass("hover");
      container.current!.style.cursor = "default";
    });
    cy.current.on("mouseover", "node, edge", (event) => {
      const { x, y } = event.renderedPosition ?? event.target.renderedPosition();
      const info = describe(latest.current, event.target.data("kind"), event.target.data("ref"),
        Boolean(onClick.current));
      setTooltip({ x, y, ...info });
    });
    cy.current.on("mouseout", "node, edge", () => setTooltip(null));
    cy.current.on("viewport", () => setTooltip(null));
    // The container's size settles after mount and changes with the window, so refit.
    const observer = new ResizeObserver(() => {
      cy.current?.resize();
      cy.current?.fit(undefined, 30);
    });
    observer.observe(container.current!);
    return () => {
      observer.disconnect();
      cy.current?.destroy();
    };
  }, []);

  useEffect(() => {
    const graph = cy.current;
    if (!graph) return;
    const first = graph.elements().empty();
    graph.elements().remove();
    graph.add(toElements(state));
    if (first) {
      graph.resize();
      graph.fit(undefined, 30);
    }
  }, [state]);

  useEffect(() => {
    const graph = cy.current;
    if (!graph) return;
    graph.nodes(".switch").removeClass("to-close to-open");
    for (const op of highlight) {
      graph.getElementById(`s${op.switch_id}`).addClass(op.op === "close" ? "to-close" : "to-open");
    }
  }, [state, highlight]);

  return (
    <div className="diagram-wrap">
      <div ref={container} className="diagram" />
      {tooltip && (
        <div className="tooltip" style={{ left: tooltip.x, top: tooltip.y }}>
          <b>{tooltip.title}</b>
          {tooltip.lines.map((line) => <div key={line}>{line}</div>)}
        </div>
      )}
    </div>
  );
}
