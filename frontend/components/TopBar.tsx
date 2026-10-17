"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import ThemeToggle from "@/components/ThemeToggle";
import { api } from "@/lib/api";
import { currentFeeder, FEEDERS, withFeeder, type FeederId } from "@/lib/feeders";

type Props = {
  page: "feeder" | "graph" | "results";
  children?: React.ReactNode;
};

const PATHS = { feeder: "/", graph: "/graph/", results: "/results/" };

export default function TopBar({ page, children }: Props) {
  const [feeder, setFeeder] = useState<FeederId>("ieee33");
  // Only feeders the server has loaded are offered; an older deployment has just the 33-bus one.
  const [available, setAvailable] = useState<FeederId[]>([]);

  useEffect(() => {
    setFeeder(currentFeeder());
    api.feeders()
      .then((r) => setAvailable(r.feeders.map((f) => f.id).filter((id): id is FeederId => id in FEEDERS)))
      .catch(() => setAvailable([]));
  }, []);

  // A new network starts from a clean page, so switching reloads it.
  const switchTo = (id: FeederId) => window.location.assign(withFeeder(PATHS[page], id));

  return (
    <header className="topbar">
      <div className="brand">
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
          <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z" fill="currentColor" />
        </svg>
        <div>
          <div className="brand-name">GridMend</div>
          <div className="brand-sub">self-healing feeder on a FalkorDB twin</div>
        </div>
      </div>
      <nav>
        <Link href={withFeeder(PATHS.feeder, feeder)} className={page === "feeder" ? "active" : undefined}>Feeder</Link>
        <Link href={withFeeder(PATHS.graph, feeder)} className={page === "graph" ? "active" : undefined}>Graph</Link>
        <Link href={withFeeder(PATHS.results, feeder)} className={page === "results" ? "active" : undefined}>Results</Link>
      </nav>
      <div className="topbar-actions">
        {available.length > 1 && (
          <label className="feeder-switch">
            <span className="visually-hidden">Network</span>
            <select value={feeder} onChange={(e) => switchTo(e.target.value as FeederId)}>
              {available.map((id) => <option key={id} value={id}>{FEEDERS[id].short}</option>)}
            </select>
          </label>
        )}
        {children}
        <ThemeToggle />
      </div>
    </header>
  );
}
