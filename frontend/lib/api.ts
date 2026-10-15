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

export async function getState(): Promise<GridState> {
  const response = await fetch(`${API_URL}/state`, { cache: "no-store" });
  if (!response.ok) throw new Error(`GET /state failed: ${response.status}`);
  return response.json();
}
