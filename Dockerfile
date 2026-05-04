# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

ARG BASE_IMAGE=registry.evolveum.com/public/midpilot-connector-gen-base:python3.13-playwright1.58.0
FROM ${BASE_IMAGE}

WORKDIR /app

# ---- Project code + install ----
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
RUN python -c "from pathlib import Path; import importlib.metadata; base = Path('/opt/playwright-python-version').read_text().strip(); actual = importlib.metadata.version('playwright'); assert actual == base, f'Playwright version mismatch: base image has {base}, app installs {actual}. Rebuild Dockerfile.base.'"

# Expose only the FastAPI port
EXPOSE 8090

# ---- Run FastAPI ----
CMD ["python", "server.py"]
