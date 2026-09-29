# ChainLens - Linux container image.
#
# This image is the Linux deliverable: one process serving the API and the compiled
# frontend, with no outbound network calls at runtime. Everything it needs - Python
# dependencies, the built frontend, the trained model bundle - is baked in at build
# time, so the running container works with networking disabled.
#
# Build:  docker build -t chainlens .
# Run:    docker run -p 8000:8000 -e CHAINLENS_AUTH_PASSWORD=... -v chainlens:/data chainlens

# ---------------------------------------------------------------- frontend build stage
FROM node:20-bookworm-slim AS frontend

WORKDIR /build
# Copy manifests first so the dependency layer is cached across source edits.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# ----------------------------------------------------------------- python build stage
FROM python:3.12-slim-bookworm AS backend

WORKDIR /build
COPY backend/requirements.txt ./
# Build wheels once so the runtime stage needs no compiler toolchain.
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


# --------------------------------------------------------------------- runtime stage
FROM python:3.12-slim-bookworm AS runtime

# PYTHONDONTWRITEBYTECODE keeps the read-only layers clean; PYTHONUNBUFFERED makes
# container logs appear immediately rather than being held in a buffer.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Run as a non-root user: a compromise in the app should not own the container.
RUN useradd --create-home --uid 10001 chainlens

WORKDIR /app

COPY --from=backend /wheels /wheels
COPY backend/requirements.txt ./
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Application code, then the compiled frontend from the node stage.
COPY backend/chainlens ./backend/chainlens
COPY --from=frontend /build/dist ./frontend/dist

# The trained model bundle ships inside the image. Training needs the dataset and is a
# deliberate, versioned operation - it does not belong in a container start-up path.
COPY backend/model_artifacts ./backend/model_artifacts

# A sample dataset, so a reviewer can try the application without hunting for a file.
COPY data/samples/demo_mixed_patterns.csv ./data/samples/demo_mixed_patterns.csv

# /data is the only writable location: cases, the source archive, predictions and
# exports all live here. Mount a volume on it to keep them across restarts.
ENV CHAINLENS_DATA_DIR=/data \
    CHAINLENS_MODEL_DIR=/app/backend/model_artifacts \
    CHAINLENS_FRONTEND_DIST=/app/frontend/dist \
    PYTHONPATH=/app/backend \
    HOST=0.0.0.0 \
    PORT=8000

RUN mkdir -p /data && chown -R chainlens:chainlens /data /app

USER chainlens
EXPOSE 8000

# Probe the one endpoint that stays reachable without a session, so the platform can
# tell "starting" from "broken" even on a password-protected instance.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/api/health', timeout=4).status==200 else 1)"

CMD ["python", "-m", "chainlens"]
