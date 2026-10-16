"use client";

import { useState } from "react";

import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Spinner from "@/components/ui/Spinner";
import type { CrewFiling, Line } from "@/lib/api";

const EXAMPLES = [
  {
    label: "6-7 jammed on patrol",
    note: "Patrol report, 10:40: the sectionalizer between 6 and 7 is jammed shut, operating handle "
      + "seized solid. Also a tree leaning on the line near pole 14.",
  },
  {
    label: "12-22 tie broken",
    note: "Crew at the 12-22 tie: it will not close, the spring mechanism is broken. Parts on order.",
  },
];

type Props = {
  jammed: Line[];
  busy: boolean;
  error: string | null;
  result: CrewFiling | null;
  disabled: boolean;
  onFile: (note: string) => void;
};

// A field crew's note in their own words. The AI picks out which switches it says are jammed
// or repaired; GridMend only accepts a finding that names a real switch and quotes the note.
export default function CrewReport({ jammed, busy, error, result, disabled, onFile }: Props) {
  const [note, setNote] = useState("");

  return (
    <Card as="aside" className="crew" title="Field crew report"
      hint="Jammed switches are worked around in isolation and planning">
      <form
        className="crew-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (note.trim()) onFile(note.trim());
        }}
      >
        <label htmlFor="crew-note">What did the crew find?</label>
        <textarea
          id="crew-note"
          rows={3}
          value={note}
          placeholder="e.g. Switch 9-10 is seized and won't open."
          onChange={(event) => setNote(event.target.value)}
        />
        <div className="crew-examples">
          <span className="muted small">Examples:</span>
          {EXAMPLES.map((e) => (
            <button key={e.label} type="button" className="crew-example" onClick={() => setNote(e.note)}>
              {e.label}
            </button>
          ))}
        </div>
        <Button type="submit" variant="secondary" disabled={busy || disabled || !note.trim()}>
          File report
        </Button>
      </form>

      <div className="crew-result" aria-live="polite">
        {busy ? (
          <p className="muted small conditions-status"><Spinner /> Reading the crew&apos;s note…</p>
        ) : error ? (
          <p className="error" role="alert">{error}</p>
        ) : result && (
          <>
            <p className="small">
              <b>Filed as {result.report}.</b>{" "}
              {result.findings.length === 0 && "It mentions no switch, so nothing changed."}
            </p>
            {result.findings.length > 0 && (
              <ul className="crew-findings">
                {result.findings.map((f) => (
                  <li key={f.switch_id}>
                    <b>{f.switch_id}</b> {f.operable ? "works again" : "jammed"}
                    <span className="muted"> · &ldquo;{f.evidence}&rdquo;</span>
                  </li>
                ))}
              </ul>
            )}
            {result.rejected_plans.length > 0 && (
              <p className="small">Plans that can no longer run: {result.rejected_plans.join(", ")}. Replanning around it.</p>
            )}
            {result.other && <p className="muted small">Also reported: {result.other}</p>}
          </>
        )}
      </div>

      {jammed.length > 0 && (
        <p className="crew-jammed small">
          <b>Jammed now:</b>{" "}
          {jammed.map((l) => `${l.id} (stuck ${l.status}${l.report ? `, ${l.report}` : ""})`).join(", ")}
        </p>
      )}
    </Card>
  );
}
