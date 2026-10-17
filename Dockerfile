# Runs FalkorDB, the API and the UI in one container.
#
# The UI and the evaluation results are the same on every platform, so both are built on the
# build machine's own platform. That also keeps FalkorDB out of emulated multi-arch builds,
# where it does not run reliably.
ARG FALKORDB_IMAGE=falkordb/falkordb:6.0.1

FROM --platform=$BUILDPLATFORM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV NEXT_PUBLIC_API_URL=/api
RUN npm run build

# COPY --from=<image> resolves the image for the target platform, so the natively built
# stage needs its own uv stage.
FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:0.12.23 AS uv-native
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv-target

FROM --platform=$BUILDPLATFORM ${FALKORDB_IMAGE} AS results
COPY --from=uv-native /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH
WORKDIR /app
COPY backend/ backend/
RUN uv venv -p 3.12 /opt/venv && uv pip install -p /opt/venv --no-cache -e ./backend
COPY deploy/ deploy/
# The evaluation needs a running FalkorDB and takes seconds with build-time CPU, so its
# results ship in the image instead of being recomputed on a small host.
# The loaded feeder is saved too, so the container starts without rebuilding the graph.
# Both feeders are loaded and evaluated, so the network switch in the UI works from the start.
RUN deploy/falkordb.sh && gridmend load && gridmend eval > /dev/null \
    && GRIDMEND_FEEDER=oberrhein gridmend load && GRIDMEND_FEEDER=oberrhein gridmend eval > /dev/null \
    && redis-cli save && mkdir -p /app/snapshot && cp /tmp/dump.rdb /app/snapshot/ \
    && redis-cli shutdown nosave

FROM ${FALKORDB_IMAGE}
COPY --from=uv-target /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH PORT=7860
WORKDIR /app
COPY backend/ backend/
RUN uv venv -p 3.12 /opt/venv && uv pip install -p /opt/venv --no-cache -e ./backend
COPY deploy/ deploy/
COPY --from=results /app/backend/eval/out backend/eval/out
COPY --from=results /app/snapshot /app/snapshot
COPY --from=ui /ui/out /app/ui

EXPOSE 7860
ENTRYPOINT ["/app/deploy/start.sh"]
