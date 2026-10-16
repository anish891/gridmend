import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "GridMend",
  description: "Self-healing distribution feeder on a FalkorDB twin",
};

// Sets the theme before first paint so the page never flashes the other one: the saved choice if
// there is one, otherwise the system setting, which it keeps following until the user picks.
const THEME_SCRIPT = `(function(){var m=matchMedia("(prefers-color-scheme: dark)");function a(){var t=null;try{t=localStorage.getItem("theme")}catch(e){}document.documentElement.dataset.theme=t||(m.matches?"dark":"light")}a();m.addEventListener("change",a)})()`;

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
