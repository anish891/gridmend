#!/bin/sh
# FalkorDB on localhost only, in memory, with the image's default module arguments.
# Nothing is ever saved to disk, so Redis's ARM64 copy-on-write check (which fails under
# the emulated arm64 kernel used for multi-arch builds) guards nothing here.
set -e

redis-server --bind 127.0.0.1 --port 6379 --daemonize yes \
    --dir /tmp --save "" --appendonly no \
    --ignore-warnings ARM64-COW-BUG \
    --loadmodule "${FALKORDB_BIN_PATH}/falkordb.so" ${FALKORDB_ARGS}

for _ in $(seq 1 100); do
    redis-cli ping >/dev/null 2>&1 && exit 0
    sleep 0.2
done
echo "FalkorDB did not start" >&2
exit 1
