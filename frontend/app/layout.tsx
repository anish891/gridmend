import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "GridMend",
  description: "Self-healing distribution feeder on a FalkorDB twin",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
