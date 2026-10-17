#!/bin/sh
# Single-container start: FalkorDB, the feeder (from the build-time snapshot when present), then
# the API and UI on one port. Evaluation results were produced at image build time.
set -e

/app/deploy/falkordb.sh
if ! redis-cli GRAPH.LIST | grep -q '^grid_base$'; then
    gridmend load
fi
if ! redis-cli GRAPH.LIST | grep -q '^oberrhein_base$'; then
    GRIDMEND_FEEDER=oberrhein gridmend load
fi
exec gridmend serve --ui /app/ui --host 0.0.0.0 --port "${PORT:-7860}"
