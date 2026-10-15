export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Bus = {
  id: number;
  zone: number | null;
  energized: boolean;
  critical: boolean;
  facility: string | null;
  p_kw: number;
  is_source: boolean;
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
};

export type Zone = { id: number; energized: boolean; faulted: boolean; p_kw: number };

export type Incident = { id: string; status: string; zone: number } | null;

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
  origin: "greedy" | "tie-only" | "repaired";
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
};

export type PlanResult = { incident: string; recommended: string | null; candidates: Candidate[] };

export type Location = { zone: number; chain: string[] };

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
  isolation: string[];
  plan: {
    id: string;
    ops: string[];
    restored_kw: number;
    min_v_pu: number;
    max_loading_pct: number;
    paths: Record<string, string[]>;
  } | null;
  rejected: { id: string; ops: string[]; violations: string[] }[];
  critical: CriticalLoad[];
  dark_zones: number[];
  load_served_kw: number;
  load_unserved_kw: number;
};

export type Trace = {
  incident: { id: string; status: string; zone: number };
  plans: { plan: string; kind: string; status: string; ops: string[] }[];
};

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
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
  fault: (line: string) => call<{ line: string; indicators_set: string[] }>("POST", "/fault", { line }),
  locate: () => call<Location>("POST", "/locate"),
  isolate: () => call<{ incident: string; ops: SwitchOp[] }>("POST", "/isolate"),
  plan: () => call<PlanResult>("POST", "/plan"),
  execute: (planId: string) => call<{ plan_id: string; ops: SwitchOp[] }>("POST", "/execute", { plan_id: planId }),
  explain: () => call<Explanation>("GET", "/explain"),
  trace: () => call<Trace>("GET", "/trace"),
};
