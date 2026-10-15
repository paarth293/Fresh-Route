# Build the browser interface separately from the Python runtime.
FROM node:24-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRESHROUTE_DB_PATH=/data/freshroute.sqlite3 \
    FRESHROUTE_REQUIRE_AUTH=1 \
    FRESHROUTE_SECURE_COOKIES=1
WORKDIR /app
# OR-Tools/SCIP and numerical wheels require the GNU C++ runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.lock /app/backend/requirements.lock
RUN python -m pip install -r backend/requirements.lock
COPY backend/app/ /app/backend/app/
COPY configs/ /app/configs/
COPY scenarios/ /app/scenarios/
COPY scripts/serve.py /app/scripts/serve.py
COPY --from=frontend-build /build/frontend/dist/ /app/frontend/dist/
RUN mkdir -p /data
EXPOSE 8000
# Railway supplies PORT. SQLite and the local job queue require one worker.
CMD ["python", "scripts/serve.py"]
