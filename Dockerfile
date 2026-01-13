#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.
# Python + uv base
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# ---- uv settings ----
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# ---- Python deps (without project) ----
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# ---- Project code + install ----
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:$PATH"

# ---- Playwright: install browsers during build ----
RUN python -m playwright install --with-deps chromium

ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

# Expose only the FastAPI port
EXPOSE 8090

# ---- Run FastAPI ----
CMD ["python", "server.py"]
