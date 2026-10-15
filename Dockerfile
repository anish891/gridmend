# Builds the UI as static files, then runs FalkorDB, the API and the UI in one container.

FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV NEXT_PUBLIC_API_URL=/api
RUN npm run build

FROM falkordb/falkordb:6.0.1
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PORT=7860

WORKDIR /app
COPY backend/ backend/
RUN uv venv -p 3.12 /opt/venv \
    && uv pip install -p /opt/venv --no-cache -e ./backend

# The evaluation needs a running FalkorDB but only takes seconds with build-time CPU,
# so its results ship in the image instead of being recomputed on a small host.
COPY deploy/ deploy/
RUN deploy/falkordb.sh && gridmend load && gridmend eval > /dev/null \
    && redis-cli shutdown nosave

COPY --from=ui /ui/out /app/ui

EXPOSE 7860
ENTRYPOINT ["/app/deploy/start.sh"]
