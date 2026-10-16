"use client";

import { useRef } from "react";

type Option<T extends string> = { value: T; label: string; hint?: string };

type Props<T extends string> = {
  label: string;
  options: Option<T>[];
  value: T;
  onChange: (value: T) => void;
};

// A radio group drawn as joined buttons. Arrow keys move the choice, as in a native radio group.
export default function Segmented<T extends string>({ label, options, value, onChange }: Props<T>) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const withHints = options.some((o) => o.hint);

  const move = (from: number, step: number) => {
    const next = (from + step + options.length) % options.length;
    onChange(options[next].value);
    buttons.current[next]?.focus();
  };

  const onKeyDown = (index: number) => (event: React.KeyboardEvent) => {
    if (event.key === "ArrowRight" || event.key === "ArrowDown") move(index, 1);
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") move(index, -1);
    else return;
    event.preventDefault();
  };

  return (
    <div role="radiogroup" aria-label={label} className={withHints ? "segmented segmented-cards" : "segmented"}>
      {options.map((o, i) => (
        <button
          key={o.value}
          ref={(el) => { buttons.current[i] = el; }}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          tabIndex={o.value === value ? 0 : -1}
          onClick={() => onChange(o.value)}
          onKeyDown={onKeyDown(i)}
        >
          {o.label}
          {o.hint && <span>{o.hint}</span>}
        </button>
      ))}
    </div>
  );
}
