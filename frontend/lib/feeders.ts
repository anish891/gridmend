// The feeders the UI can show. The choice travels in the page URL (?feeder=oberrhein), so a
// link opens the same network and switching reloads the page with a clean state.

export type FeederId = "ieee33" | "oberrhein";

export type DemoFault = { line: string; title: string; note: string };
export type DoubleFault = { lines: string[]; title: string; note: string };
export type CrewExample = { label: string; note: string };

export type FeederConfig = {
  id: FeederId;
  label: string;
  short: string;
  // A large real network drawn from its coordinates, rather than the hand-drawn 33-bus map.
  dense: boolean;
  faults: DemoFault[];
  doubles: DoubleFault[];
  // The fault the guided demo runs: some plans fail on their fork, one passes.
  demoLine: string;
  questions: string[];
  crewExamples: CrewExample[];
  crewPlaceholder: string;
};

export const FEEDERS: Record<FeederId, FeederConfig> = {
  ieee33: {
    id: "ieee33",
    label: "IEEE 33-bus test feeder",
    short: "IEEE 33-bus",
    dense: false,
    faults: [
      { line: "4-5", title: "Main line fault", note: "The hospital and fire station lose power, and the obvious fix would overload a line." },
      { line: "10-11", title: "Mid-feeder fault", note: "Only the fire station's section needs power routed in from elsewhere." },
      { line: "27-28", title: "Branch fault", note: "The only backup route drops voltage too low, so no switching is safe." },
    ],
    // On separate branches both faults show at once; on one path the downstream fault hides the
    // upstream one until the breaker recloses onto it.
    doubles: [
      { lines: ["20-21", "27-28"], title: "Two branches", note: "Both faults are found from one set of indicators." },
      { lines: ["4-5", "16-17"], title: "One behind the other", note: "The upstream fault hides until the breaker recloses onto it." },
    ],
    demoLine: "4-5",
    questions: [
      "Which line is most critical right now, and what would a fault there cost?",
      "How is the hospital fed?",
      "If line 4-5 faults and tie 8-21 is jammed, what changes?",
    ],
    crewExamples: [
      {
        label: "6-7 jammed on patrol",
        note: "Patrol report, 10:40: the sectionalizer between 6 and 7 is jammed shut, operating handle "
          + "seized solid. Also a tree leaning on the line near pole 14.",
      },
      {
        label: "12-22 tie broken",
        note: "Crew at the 12-22 tie: it will not close, the spring mechanism is broken. Parts on order.",
      },
    ],
    crewPlaceholder: "e.g. Switch 9-10 is seized and won't open.",
  },
  oberrhein: {
    id: "oberrhein",
    label: "Oberrhein, a real 20 kV network",
    short: "Oberrhein (real, 177 buses)",
    dense: true,
    faults: [
      { line: "13-19", title: "Re-fed from the other substation", note: "Two dark zones come back through a tie to the second substation." },
      { line: "27-33", title: "Every single tie overloads", note: "Each backup route loads a real cable just past its rating; opening switches first makes one fit." },
      { line: "104-112", title: "No backup route", note: "The zones below the fault have no tie at all, so they stay dark, and the explanation says why." },
    ],
    doubles: [
      { lines: ["13-19", "27-33"], title: "Both substations", note: "A fault on each substation's network, found and isolated together." },
      { lines: ["13-19", "101-108"], title: "One behind the other", note: "The upstream fault hides until the breaker recloses onto it." },
    ],
    demoLine: "27-33",
    questions: [
      "Which line is most critical right now, and what would a fault there cost?",
      "How is the hospital fed?",
      "If line 13-19 faults and tie 157-164 is jammed, what changes?",
    ],
    crewExamples: [
      {
        label: "4-9 jammed on patrol",
        note: "Patrol report, 10:40: the sectionalizer between 4 and 9 is jammed shut, operating handle "
          + "seized solid.",
      },
      {
        label: "157-164 tie broken",
        note: "Crew at the 157-164 tie: it will not close, the spring mechanism is broken. Parts on order.",
      },
    ],
    crewPlaceholder: "e.g. Switch 55-60 is seized and won't open.",
  },
};

export function currentFeeder(): FeederId {
  if (typeof window === "undefined") return "ieee33";
  const id = new URLSearchParams(window.location.search).get("feeder");
  return id === "oberrhein" ? "oberrhein" : "ieee33";
}

// A page path that keeps the current feeder.
export function withFeeder(path: string, feeder: FeederId): string {
  return feeder === "ieee33" ? path : `${path}?feeder=${feeder}`;
}
