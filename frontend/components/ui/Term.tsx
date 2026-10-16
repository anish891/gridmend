"use client";

import { useId, useState } from "react";

const TIP_WIDTH = 260;

// A word with a plain-language explanation, shown on hover, focus or tap.
export default function Term({ children, hint }: { children: React.ReactNode; hint: string }) {
  const id = useId();
  const [alignEnd, setAlignEnd] = useState(false);
  // Open towards whichever side has room, so the tip never runs off the page.
  const place = (event: React.SyntheticEvent<HTMLElement>) => {
    const { left } = event.currentTarget.getBoundingClientRect();
    setAlignEnd(left + TIP_WIDTH > window.innerWidth - 16);
  };
  return (
    <span className="term" tabIndex={0} aria-describedby={id} onMouseEnter={place} onFocus={place}>
      {children}
      <span role="tooltip" id={id} className={alignEnd ? "term-tip end" : "term-tip"}>{hint}</span>
    </span>
  );
}
