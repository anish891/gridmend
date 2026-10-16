"use client";

import { useState } from "react";

import Button from "@/components/ui/Button";
import Segmented from "@/components/ui/Segmented";
import Spinner from "@/components/ui/Spinner";
import Term from "@/components/ui/Term";
import type { OperatingContext } from "@/lib/api";

const PRESETS = {
  normal: { label: "Normal day", text: null },
  heat: {
    label: "Heat advisory",
    text: "Heat advisory declared: 44 °C expected until 7 pm. The community hall cooling centre on "
      + "bus 11 is open to the public. Repair crew ETA to the faulted area: about 3 hours.",
  },
  storm: {
    label: "Storm warning",
    text: "Severe thunderstorm warning for the next 6 hours; more line trips are likely. Emergency "
      + "services are relying on the telecom exchange. Repair crew ETA: about 4 hours.",
  },
  custom: { label: "Describe your own", text: null },
} as const;

type Preset = keyof typeof PRESETS;

function presetOf(conditions: string | null): Preset {
  if (!conditions) return "normal";
  const match = (Object.keys(PRESETS) as Preset[]).find((key) => PRESETS[key].text === conditions);
  return match ?? "custom";
}

type Props = {
  context: OperatingContext | null;
  busy: boolean;
  error: string | null;
  disabled: boolean;
  onApply: (conditions: string | null) => void;
};

// Today's conditions: the LLM reads them with the written policy and proposes each site's
// priority; the backend checks every value before it is used.
export default function Conditions({ context, busy, error, disabled, onApply }: Props) {
  const current = presetOf(context?.conditions ?? null);
  const [choice, setChoice] = useState<Preset>(current);
  const [draft, setDraft] = useState(current === "custom" ? context?.conditions ?? "" : "");
  const shown = busy ? choice : choice === "custom" ? "custom" : current;

  const pick = (preset: Preset) => {
    setChoice(preset);
    if (preset !== "custom") onApply(PRESETS[preset].text);
  };

  const changed = context?.priorities.filter((p) => p.weight !== p.standing) ?? [];

  return (
    <div className="conditions">
      <div className="conditions-head">
        <h2 className="conditions-title">Today&apos;s conditions</h2>
        <span className="card-hint">
          The AI reads them with the written restoration policy and sets each site&apos;s priority
        </span>
      </div>
      <Segmented
        label="Today's conditions"
        value={shown}
        onChange={(preset) => !busy && !disabled && pick(preset)}
        options={(Object.keys(PRESETS) as Preset[]).map((key) => ({ value: key, label: PRESETS[key].label }))}
      />

      {shown === "custom" && (
        <form
          className="conditions-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (draft.trim()) onApply(draft.trim());
          }}
        >
          <label htmlFor="conditions-text">What is happening today?</label>
          <textarea
            id="conditions-text"
            rows={3}
            value={draft}
            placeholder="e.g. Water contamination alert; boil-water notice issued. Crew 45 minutes away."
            onChange={(event) => setDraft(event.target.value)}
          />
          <Button type="submit" variant="secondary" disabled={busy || disabled || !draft.trim()}>
            Apply conditions
          </Button>
        </form>
      )}

      <div className="conditions-result" aria-live="polite">
        {busy ? (
          <p className="muted small conditions-status"><Spinner /> Reading the restoration policy…</p>
        ) : error ? (
          <p className="error" role="alert">{error}</p>
        ) : context?.conditions ? (
          <>
            {changed.length > 0 ? (
              <ul className="priority-changes">
                {changed.map((p) => (
                  <li key={p.facility}>
                    <Term hint={p.rule}>
                      <b>{p.facility}</b> {p.weight > p.standing ? "raised" : "lowered"} to {p.weight}
                    </Term>
                    <span className="muted"> from {p.standing}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted small">The policy keeps every standing priority for these conditions.</p>
            )}
            <p className="muted small">
              Set by {context.model} from the policy, then checked by GridMend. Hover or tap a change for the rule it cites.
            </p>
          </>
        ) : (
          <p className="muted small">Standing priorities: hospital, fire station, water pumping and telecom first.</p>
        )}
      </div>
    </div>
  );
}
