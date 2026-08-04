# syntax=docker/dockerfile:1

# Domain Email Intelligence
#
# Two stages sharing one base. The builder compiles wheels (lxml, pymupdf and
# cryptography all build from source on slim) so the final image never carries a
# compiler; the runtime stage installs those wheels and the Chromium build that
# matches the resolved Playwright version.

ARG PYTHON_VERSION=3.12


# --------------------------------------------------------------------------- #
# Stage 1 — build wheels
# --------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


# --------------------------------------------------------------------------- #
# Stage 2 — runtime
# --------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    # Shared browser location, readable by the unprivileged runtime user. Playwright
    # otherwise installs into the *installing* user's home, which root's is.
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    # Scan log, saved results and checkpoints. Mount a volume here.
    DATA_DIR=/data

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        tini \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels requirements.txt

# Installed after the Python packages so the browser always matches the Playwright
# version pip actually resolved, rather than one pinned to a base-image tag.
# --with-deps pulls the Chromium system libraries; it needs root, which is why this
# runs before the USER switch.
RUN playwright install --with-deps chromium \
    && chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH" \
    && rm -rf /var/lib/apt/lists/*

# Crawling untrusted pages with a headless browser is exactly the workload that
# should not be root.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p "$DATA_DIR" \
    && chown -R appuser:appuser "$DATA_DIR"

WORKDIR /app
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser web/ ./web/
COPY --chown=appuser:appuser run_server.py ./

USER appuser

EXPOSE 8000
VOLUME ["/data"]

# The registry, SSE fan-out and live feed are per-process state, so the app is
# correct with exactly one worker. Scale by running more containers behind a proxy
# with sticky sessions, not by raising --workers.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# tini reaps the Chromium processes Playwright leaves behind on a killed scan.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
