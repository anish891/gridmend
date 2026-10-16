import Link from "next/link";

import ThemeToggle from "@/components/ThemeToggle";

type Props = {
  page: "feeder" | "graph" | "results";
  children?: React.ReactNode;
};

export default function TopBar({ page, children }: Props) {
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
        <Link href="/" className={page === "feeder" ? "active" : undefined}>Feeder</Link>
        <Link href="/graph/" className={page === "graph" ? "active" : undefined}>Graph</Link>
        <Link href="/results/" className={page === "results" ? "active" : undefined}>Results</Link>
      </nav>
      <div className="topbar-actions">
        {children}
        <ThemeToggle />
      </div>
    </header>
  );
}
