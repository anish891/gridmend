import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "GridMend",
  description: "Self-healing distribution feeder on a FalkorDB twin",
};

// Applies a saved theme before first paint so the page never flashes the other one.
const THEME_SCRIPT = `try{var t=localStorage.getItem("theme");if(t)document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
