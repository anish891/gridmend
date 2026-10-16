// Evaluation methods in plain words, for the results table and chart.
export const METHODS: Record<string, { label: string; hint: string }> = {
  "isolate-only": {
    label: "Isolate only",
    hint: "Cuts out the fault and restores nothing else. The lower bound.",
  },
  greedy: {
    label: "Greedy, unchecked",
    hint: "Closes the first backup switch into every dark section, without any safety check.",
  },
  "llm-only": {
    label: "AI alone",
    hint: "A language model given a text description of the feeder and no tools. Its switching is checked afterwards.",
  },
  gridmend: {
    label: "GridMend",
    hint: "Plans from the graph, checks every plan on a copy of the grid, then acts.",
  },
  optimal: {
    label: "Best possible",
    hint: "Brute-force search over every switching option. Used only as the yardstick.",
  },
};

export const methodLabel = (method: string) => METHODS[method]?.label ?? method;
