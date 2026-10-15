"use client";

import { useEffect, useState } from "react";

import FeederDiagram from "@/components/FeederDiagram";
import { getState, type GridState } from "@/lib/api";

export default function Home() {
  const [state, setState] = useState<GridState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getState().then(setState).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main>
      <header>
        <h1>GridMend</h1>
        {state && (
          <span className="served">
            {Math.round(state.served_kw)} / {Math.round(state.total_kw)} kW served
          </span>
        )}
      </header>
      {error && <p className="error">Cannot reach the API: {error}</p>}
      {state && <FeederDiagram state={state} />}
      <ul className="legend">
        <li><span className="swatch live" /> energized</li>
        <li><span className="swatch dark" /> de-energized</li>
        <li><span className="swatch faulted" /> faulted zone</li>
        <li><span className="box closed" /> switch closed</li>
        <li><span className="box open" /> switch open</li>
        <li><span className="dash" /> tie (normally open)</li>
        <li><span className="star">★</span> critical load</li>
      </ul>
    </main>
  );
}
