import { currentFeeder } from "@/lib/feeders";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Bus = {
  id: number;
  zone: number | null;
  energized: boolean;
  critical: boolean;
  facility: string | null;
  weight: number;
  p_kw: number;
  is_source: boolean;
  // Distributed generation at the bus (kW) and whether it is producing right now.
  dg_kw: number;
  dg_online: boolean;
};

export type Line = {
  id: string;
  from: number;
  to: number;
  type: "segment" | "switch";
  kind: "segment" | "breaker" | "sectionalizer" | "tie";
  status: "open" | "closed";
  normally: "open" | "closed" | null;
  fi: "set" | "clear" | "none" | null;
  faulted: boolean;
  cap_kva: number;
  // False when a crew reported the switch jammed; `report` names that crew report.
  operable: boolean;
  report: string | null;
  // What depends on this line in the current switching (null when it carries nothing).
  carries: { buses: number; kw: number; critical: string[] } | null;
};

export type RiskLine = { line: string; unserved_kw: number; restored_kw: number; critical_dark: string[] };

// cut_off: a healthy zone isolated with the fault because a jammed switch joins them.
export type Zone = { id: number; energized: boolean; faulted: boolean; cut_off: boolean; p_kw: number };

export type CrewFinding = { switch_id: string; operable: boolean; evidence: string };

export type CrewReport = { id: string; note: string; other: string; findings: CrewFinding[] };

// A switch as the agent's zone map reports it, with the fault indicator it read.
export type ZoneSwitch = { id: string; kind: string; status: string; indicator?: "set" | "clear" };

export type SwitchingStep = {
  step: number;
  plan: string;
  phase: "isolate" | "restore";
  op: "open" | "close";
  switch_id: string;
  switch_kind: string;
};

export type CrewFiling = { report: string; findings: CrewFinding[]; other: string; rejected_plans: string[] };

export type Incident = { id: string; status: string; zone: number; zones: number[] } | null;

export type GridState = {
  buses: Bus[];
  lines: Line[];
  zones: Zone[];
  served_kw: number;
  total_kw: number;
  incident: Incident;
};

export type SwitchOp = { switch_id: string; op: "open" | "close" };

export type Violation = { type: string; detail: string };

export type Candidate = {
  plan_id: string;
  // Who designed the plan: the agent, or an operator simulating by hand at the CLI.
  origin: "agent" | "manual" | null;
  score: number;
  ops: SwitchOp[];
  passed: boolean;
  restored_zones: number[];
  dropped_zones: number[];
  restored_kw: number;
  critical: string[];
  min_v_pu: number | null;
  max_loading_pct: number | null;
  violations: Violation[];
  recommendation?: string | null;
};

export type PlanResult = { incident: string; recommended: string | null; candidates: Candidate[] };

// One round of fault location: the zones the agent declared faulted and the readings it cited.
export type Location = { zones: number[]; evidence: string };

export type CriticalLoad = {
  bus: number;
  facility: string;
  zone: number;
  energized: boolean;
  reason: string | null;
};

export type Explanation = {
  incident: string;
  fault_line: string;
  status: string;
  faulted_zone: number;
  faulted_zones: number[];
  // The agent's own reading of the indicators that led it to the faulted zones.
  evidence: string | null;
  isolation: string[];
  switching_order: SwitchingStep[];
  plan: {
    id: string;
    ops: string[];
    restored_kw: number;
    min_v_pu: number;
    max_loading_pct: number;
    paths: Record<string, string[]>;
  } | null;
  rejected: { id: string; kind: "isolation" | "restoration"; ops: string[]; violations: string[] }[];
  // Null when FalkorDB's max-flow could not be computed for the configuration.
  transfer: { demand_kva: number; deliverable_kva: number; shortfall_kva: number; bottlenecks: string[] } | null;
  critical: CriticalLoad[];
  crew_reports: CrewReport[];
  cut_off: { zone: number; via: string }[];
  dark_zones: number[];
  load_served_kw: number;
  load_unserved_kw: number;
};

export type Trace = {
  incident: { id: string; status: string; zone: number };
  plans: { plan: string; kind: string; status: string; ops: string[] }[];
};

export type ResultRow = {
  fault: string;
  method: string;
  restored_kw: number;
  critical: number;
  switch_ops: number;
  violations: number;
  plan_ms: number;
  optimal_kw: number;
  // What unchecked switching would have restored had it been safe; it scores 0 when unsafe.
  claimed_kw: number;
};

export type MethodSummary = {
  method: string;
  restored_kw: number;
  pct_optimal: number;
  critical: number;
  switch_ops: number;
  violations: number;
  unsafe_plans: number;
  mean_plan_ms: number;
};

export type Results = { summary: MethodSummary[]; rows: ResultRow[] };

export type QueryStep = {
  key: string;
  step: string;
  title: string;
  purpose: string;
  cypher: string;
  params: Record<string, unknown>;
  skipped?: string;
  when_empty?: string | null;
  ms?: number;
  columns?: string[];
  rows?: unknown[][];
  total_rows?: number;
};

export type AgentEvent =
  | { type: "tool"; name: string; args: Record<string, unknown>; result: Record<string, unknown> }
  | { type: "final"; text: string; model: string }
  | { type: "error"; detail: string };

// The agent streams one server-sent event per tool call, then its final answer.
export function runAgent(onEvent: (event: AgentEvent) => void): Promise<void> {
  return streamAgent("/agent/run", undefined, onEvent);
}

// A question about the grid, answered by the agent with read-only tools.
export function askGrid(question: string, onEvent: (event: AgentEvent) => void): Promise<void> {
  return streamAgent("/ask", { question }, onEvent);
}

// Every request names the feeder the page shows; the API serves each from that feeder's twin.
function url(path: string): string {
  const [route, query] = path.split("?");
  const params = new URLSearchParams(query);
  params.set("feeder", currentFeeder());
  return `${API_URL}${route}?${params}`;
}

async function streamAgent(path: string, body: unknown, onEvent: (event: AgentEvent) => void): Promise<void> {
  const response = await fetch(url(path), {
    method: "POST",
    cache: "no-store",
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    const detail = Array.isArray(data.detail) ? data.detail[0]?.msg : data.detail;
    throw new Error(detail ?? `the agent could not start (${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      if (chunk.startsWith("data: ")) onEvent(JSON.parse(chunk.slice(6)));
    }
  }
}

async function call<T>(method: "GET" | "POST" | "DELETE", path: string, body?: unknown): Promise<T> {
  const response = await fetch(url(path), {
    method,
    cache: "no-store",
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail ?? `${method} ${path} failed: ${response.status}`);
  return data;
}

export const api = {
  state: () => call<GridState>("GET", "/state"),
  fault: (lines: string[]) => call<{ lines: string[]; indicators_set: string[] }>("POST", "/fault", { lines }),
  // tripped_again: the switching fed a fault after all, so the breaker opened again.
  execute: (planId: string) =>
    call<{ plan_id: string; ops: SwitchOp[]; tripped_again: boolean }>("POST", "/execute", { plan_id: planId }),
  explain: () => call<Explanation>("GET", "/explain"),
  trace: () => call<Trace>("GET", "/trace"),
  results: () => call<Results>("GET", "/results"),
  queries: () => call<{ graph: string; steps: QueryStep[] }>("GET", "/queries"),
  plans: () => call<PlanResult>("GET", "/plans"),
  risk: () => call<{ lines: RiskLine[] }>("GET", "/risk"),
  agentInfo: () => call<{ available: boolean; model: string | null }>("GET", "/agent"),
  reset: () => call<{ ok: boolean }>("POST", "/reset"),
  feeders: () => call<{ default: string; feeders: { id: string; label: string }[] }>("GET", "/feeders"),
  // Map positions for a feeder without a hand-drawn layout; null for the 33-bus feeder.
  layout: () => call<{ positions: Record<string, [number, number]> | null }>("GET", "/layout"),
  reconnectGeneration: () => call<{ reconnected: number[]; replan: boolean }>("POST", "/generation/reconnect"),
  crewReports: () => call<{ reports: CrewReport[] }>("GET", "/crew"),
  fileCrewReport: (note: string) => call<CrewFiling>("POST", "/crew", { note }),
};
