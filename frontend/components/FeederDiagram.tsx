"use client";

import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import { useEffect, useRef } from "react";

import type { GridState, Line, SwitchOp } from "@/lib/api";
import { BUS_POSITIONS, CURVED_TIES } from "@/lib/layout";

const COLORS = {
  live: "#2e9d57",
  dark: "#9aa0a6",
  faulted: "#d93025",
  switchBorder: "#202124",
  label: "#3c4043",
};

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
        data: { id: `l${line.id}`, source: from, target: to, line: line.id },
        classes: ["segment", line.faulted ? "faulted" : live ? "live" : "dark"].join(" "),
      });
      continue;
    }

    const [x, y] = midpoint(line);
    const sw = `s${line.id}`;
    elements.push({
      group: "nodes",
      data: { id: sw, label: line.id, line: line.id },
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
      "font-size": 9,
      color: COLORS.label,
      "text-valign": "bottom",
      "text-margin-y": 3,
    },
  },
  { selector: "node.live", style: { "background-color": COLORS.live } },
  { selector: "node.faulted", style: { "background-color": COLORS.faulted } },
  {
    selector: "node.critical",
    style: { shape: "star", width: 22, height: 22, "font-weight": "bold", "font-size": 10 },
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
      "font-size": 7,
      "text-valign": "top",
      "text-margin-y": -3,
    },
  },
  { selector: "node.switch.open", style: { "background-color": "#ffffff" } },
  { selector: "node.switch.breaker", style: { width: 14, height: 14 } },
  { selector: "node.switch.fi-set", style: { "border-color": "#f29900", "border-width": 3 } },
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
    style: { "border-color": "#1a73e8", "border-width": 4, width: 14, height: 14 },
  },
  {
    selector: "node.switch.to-open",
    style: { "border-color": "#a142f4", "border-width": 4, width: 14, height: 14 },
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
    <div
      ref={container}
      className="diagram"
      title={onSegmentClick ? "Click a line segment to fault it" : undefined}
    />
  );
}
