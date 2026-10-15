#!/bin/sh
# FalkorDB on localhost only, in memory, with the image's default module arguments.
set -e

redis-server --bind 127.0.0.1 --port 6379 --daemonize yes \
    --dir /tmp --save "" --appendonly no \
    --loadmodule "${FALKORDB_BIN_PATH}/falkordb.so" ${FALKORDB_ARGS}

until redis-cli ping >/dev/null 2>&1; do sleep 0.2; done
