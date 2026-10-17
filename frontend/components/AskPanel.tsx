"use client";

import { useState } from "react";

import { Answer, describe, type ToolEvent } from "@/components/AgentPanel";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Spinner from "@/components/ui/Spinner";
import { askGrid, type AgentEvent } from "@/lib/api";

// What the answer leaned on that can be shown on the map: a feeding path or particular lines.
export type MapFocus = { bus: number | null; lines: string[] };

const INCIDENT_QUESTIONS = [
  "Why is the telecom site still dark?",
  "What limits restoring more load?",
  "How is the fire station fed now?",
];

// The last feeding path or lines a tool returned, for the map.
function focusOf(tools: ToolEvent[]): MapFocus | null {
  for (const e of [...tools].reverse()) {
    const r = e.result as Record<string, any>;
    if (r.error) continue;
    if (e.name === "feeding_path" && r.energized) return { bus: r.bus, lines: [] };
    if (e.name === "transfer_capacity" && r.bottlenecks.length) return { bus: null, lines: r.bottlenecks };
    if (e.name === "fault_impact") return { bus: null, lines: [r.segments ? r.segments[0].line : r.line] };
  }
  return null;
}

type Props = {
  incident: boolean;
  // Questions to suggest while the feeder is healthy; they name lines of the network shown.
  questions: string[];
  onFocus: (focus: MapFocus | null) => void;
};

export default function AskPanel({ incident, questions, onFocus }: Props) {
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mapped, setMapped] = useState(false);

  const ask = async (text: string) => {
    const q = text.trim();
    if (!q || running) return;
    setAsked(q);
    setQuestion("");
    setEvents([]);
    setError(null);
    setRunning(true);
    setMapped(false);
    onFocus(null);
    const seen: AgentEvent[] = [];
    try {
      await askGrid(q, (event) => {
        seen.push(event);
        setEvents([...seen]);
      });
      const focus = focusOf(seen.filter((e): e is ToolEvent => e.type === "tool"));
      onFocus(focus);
      setMapped(focus !== null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const tools = events.filter((e): e is ToolEvent => e.type === "tool");
  const final = events.find((e) => e.type === "final");
  const failed = events.find((e) => e.type === "error");
  const suggestions = incident ? INCIDENT_QUESTIONS : questions;

  return (
    <Card
      id="ask"
      className="ask-panel"
      title="Ask the grid"
      hint="Answered by the AI from graph tools only. It can read the grid and plan on copies, never switch anything"
    >
      <form className="ask-form" onSubmit={(e) => { e.preventDefault(); ask(question); }}>
        <input
          type="text"
          value={question}
          maxLength={300}
          placeholder="Ask about lines, zones, critical sites or what-ifs"
          aria-label="Question about the grid"
          onChange={(e) => setQuestion(e.target.value)}
          disabled={running}
        />
        <Button type="submit" disabled={running || !question.trim()}>Ask</Button>
      </form>
      <div className="ask-suggestions">
        {suggestions.map((s) => (
          <button key={s} type="button" className="chip-button" disabled={running} onClick={() => ask(s)}>
            {s}
          </button>
        ))}
      </div>

      {asked && (
        <div className="ask-result" aria-live="polite">
          <p className="ask-question">{asked}</p>
          {tools.length > 0 && (
            <ol className="agent-log">
              {tools.map((e, i) => {
                const d = describe(e);
                return (
                  <li key={i} className={d.bad ? "bad" : undefined}>
                    <code>{e.name}</code> <b>{d.label}</b> <span className="muted">{d.detail}</span>
                  </li>
                );
              })}
            </ol>
          )}
          {running && (
            <div className="loading" role="status">
              <Spinner /> {tools.length ? "Following the graph…" : "Choosing tools…"}
            </div>
          )}
          {(error || (failed && failed.type === "error")) && (
            <p className="error" role="alert">{error ?? (failed as Extract<AgentEvent, { type: "error" }>).detail}</p>
          )}
          {final && final.type === "final" && <Answer text={final.text} />}
          {mapped && !running && (
            <div className="ask-map">
              <Button variant="secondary" onClick={() => document.getElementById("map")?.scrollIntoView({ block: "start" })}>
                See it on the feeder map
              </Button>
              <Button variant="secondary" onClick={() => { onFocus(null); setMapped(false); }}>Clear</Button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
