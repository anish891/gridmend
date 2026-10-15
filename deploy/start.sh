#!/bin/sh
# Single-container start: FalkorDB, the feeder loaded into it, then the API and UI on one
# port. Evaluation results were produced at image build time.
set -e

/app/deploy/falkordb.sh
gridmend load
exec gridmend serve --ui /app/ui --host 0.0.0.0 --port "${PORT:-7860}"
