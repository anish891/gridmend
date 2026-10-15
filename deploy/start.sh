#!/bin/sh
# Single-container start: FalkorDB on localhost, the feeder loaded into it, then the
# API and UI on one port. The evaluation runs in the background for the results page.
set -e

redis-server --bind 127.0.0.1 --port 6379 --daemonize yes \
    --dir /tmp --save "" --appendonly no \
    --loadmodule "${FALKORDB_BIN_PATH}/falkordb.so" ${FALKORDB_ARGS}

until redis-cli ping >/dev/null 2>&1; do sleep 0.2; done

gridmend load
(gridmend eval > /tmp/eval.log 2>&1 &)

exec gridmend serve --ui /app/ui --host 0.0.0.0 --port "${PORT:-7860}"
