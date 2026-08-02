# Errand backend — slim, uv-based Python image for Azure Container Apps.
#
# The backend is STATEFUL and long-running (Deepgram voice-relay WebSocket,
# multi-minute errand tasks, SSE), so it runs as a container, not serverless.
#
# The shopper broker drives a REAL browser over Cloudflare Browser Rendering
# (remote, via Playwright `connect_over_cdp`). That path needs only the
# Playwright *driver* that ships inside the pip package — NOT a downloaded
# Chromium binary — so we deliberately do NOT run `playwright install`, keeping
# the image small. (The local-Chromium path is dev-only, for the localhost demo
# store, which a cloud container never reaches anyway.)

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# uv: install into the system environment, copy (not link) from cache, and don't
# spend time compiling bytecode during the image build.
ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1. Dependency layer — cached unless the lockfile changes. --no-install-project
#    installs only third-party deps so app-code edits don't bust this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# 2. App code + the project itself.
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Azure Container Apps routes external traffic to this port (ingress targetPort).
EXPOSE 8787

# Bind 0.0.0.0 so the container is reachable; single worker keeps the in-process
# approval-gate + voice-session state coherent (no cross-worker shared state).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
