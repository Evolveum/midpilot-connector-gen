# Connector Generator AI Service

Midpilot Connector Generator for discovery, scraping, digester and Codegen built with FastAPI.

## Project structure

The source tree is layered by purpose and import direction: an item may import
anything below it in the list, never above. The layering is enforced by
import-linter (`uv run poe importcheck`, configured in `pyproject.toml`).

- [`docs`](docs/index.adoc) - documentation
- [`src/app.py`](src/app.py), [`src/router.py`](src/router.py) - composition root (FastAPI app, route aggregation)
- [`src/modules`](src/modules) - feature pipelines (discovery, scrape, digester, codegen)
- [`src/session`](src/session) - session context: routes, documentation upload/processing, ownership check
- [`src/auth`](src/auth) - API key authentication and key management endpoints
- [`src/api`](src/api) - shared HTTP edge: exception handlers, job status response builders
- [`src/jobs`](src/jobs) - durable PostgreSQL job queue (claiming worker, runner,
  lifecycle, caching, fencing, persistence)
- [`src/documents`](src/documents) - documentation toolkit: chunking, LLM processing, filtering, relevance
- [`src/integrations`](src/integrations) - adapters for external services (web fetch and search)
- [`src/database`](src/database) - SQLAlchemy models and repositories (single shared schema)
- [`src/core`](src/core) - technical foundations: LLM client, observability, DB engine, base errors/schema
- [`src/shared`](src/shared) - pure helpers and product-wide vocabulary (no imports from other src packages)
- [`src/config`](src/config) - settings loaded from environment
- [`test/unit`](test/unit), [`test/integration`](test/integration) - tests, mirroring the src layout

Important files:

- [`server.py`](server.py) - Hypercorn server entry point
- [`src/app.py`](src/app.py) - FastAPI entry point
- [`src/config`](src/config) - project configuration
- [`pyproject.toml`](pyproject.toml) - dependencies, tools, tasks, import-linter contracts
- [`Dockerfile`](Dockerfile) - docker file
- [`Dockerfile.base`](Dockerfile.base) - reusable Python + Playwright base image

## API Documentation

App exposes API documentation on the URLs below:

- **OpenAPI UI:** [http://localhost:8090/docs](http://localhost:8090/docs)
- **ReDoc:** [http://localhost:8090/redoc](http://localhost:8090/redoc)

## Configuration

App is configured using environment variables, see the default configuration in [src/config.py](src/config.py) and samples in [.env-example](.env-example).

For local development you can take advantage of dotenv plugins that read `.env` file from the project root.

```bash
# copy and use example dev configuration
cp .env-example .env

# copy and use configuration for unit/integration tests
cp .env.test-example .env.test
```

### API key authentication

The service supports two operating modes controlled by `.env`:

- `AUTH__API_KEY_REQUIRED=false` (default) - no authentication, for development.
- `AUTH__API_KEY_REQUIRED=true` - every request must send a valid key in the
  `X-API-Key` header. `AUTH__MASTER_API_KEY` must be configured in this mode.

The master key accesses all sessions and is the only key allowed to manage API
keys via the `/api/v1/apiKeys` endpoints (issue with `POST`, list with `GET`,
revoke with `DELETE /{apiKeyId}`). The full key value is returned only once, in
the issue response - only its SHA-256 hash is stored.

Each session is owned by the API key that created it and is accessible only
with that key (or the master key). Sessions without an owner (created while
auth was disabled, or by the master key) are accessible only with the master
key.

## Running with Docker

### Requirements

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)

### Run using docker compose

```bash
# build base image when Python, uv.lock, pyproject.toml, or Playwright changes
docker build -f Dockerfile.base -t midpilot-connector-gen-base:python3.13-playwright1.61.0 .

# build image
docker compose build

# start container
docker compose up
```

## Local Development

### Prerequisites
- [Python 3.13+](https://www.python.org/downloads/)
- [UV dependency manager](https://github.com/astral-sh/uv)
- [PostgreSQL 15](https://www.postgresql.org/download/)

NOTE: tasks are run with a [poethepoet](https://github.com/nat-n/poethepoet) tool and configured in [pyproject.toml](pyproject.toml)

### Database Setup

The application requires PostgreSQL database. The database is automatically configured when using Docker Compose.

#### 1. Using Docker Compose (Recommended)

The `docker-compose.yaml` includes a PostgreSQL service that is automatically configured with the environment variables from your `.env` file:

```bash
# Start all services (app + database)
docker compose up

# Start only the database
docker compose up db
```

The database service uses the following environment variables from `.env`:
- `DATABASE__HOST` - Database host (default: localhost)
- `DATABASE__INT_PORT` - Internal database port used inside Docker/networked deployments (default: 5432)
- `DATABASE__EXT_PORT` - External database port exposed to the host (default: 5433)
- `DATABASE__USER` - Database user
- `DATABASE__PASSWORD` - Database password
- `DATABASE__NAME` - Database name

#### 2. Manual PostgreSQL Setup (Alternative)

If you prefer to run PostgreSQL outside Docker Compose:

```bash
docker run --name postgres \
  -e POSTGRES_USER=user \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=db \
  -p 5432:5432 \
  -d postgres:15-alpine
```

#### 3. Configure Database Connection

Ensure your `.env` file has the correct database configuration:

```bash
DATABASE__HOST=localhost
DATABASE__INT_PORT=5432
DATABASE__EXT_PORT=5433
DATABASE__USER=user
DATABASE__PASSWORD=password
DATABASE__NAME=db

# Full connection string for Alembic and SQLAlchemy
DATABASE__URL=postgresql+asyncpg://${DATABASE__USER}:${DATABASE__PASSWORD}@${DATABASE__HOST}:${DATABASE__EXT_PORT}/${DATABASE__NAME}
```

#### 4. Run Database Migrations

The application uses [Alembic](https://alembic.sqlalchemy.org/) for database migrations.

```bash
# Run all pending migrations
uv run alembic upgrade head

# Check current migration version
uv run alembic current

# View migration history
uv run alembic history
```

#### 5. Common Alembic Commands

```bash
# Create a new migration (auto-generate from models)
uv run alembic revision --autogenerate -m "description of changes"

# Upgrade to a specific revision
uv run alembic upgrade <revision_id>

# Downgrade one revision
uv run alembic downgrade -1

# Downgrade to base (drop all tables)
uv run alembic downgrade base

# Show current revision
uv run alembic current

# Show SQL without executing
uv run alembic upgrade head --sql
```

### Run the app

```bash
# after pulling a version with schema changes
uv run alembic upgrade head

# APP__WORKERS can be raised when live reload is disabled; all processes
# coordinate through the same PostgreSQL job queue
uv run poe start
# access the service at http://localhost:8090
# e.g. `curl http://0.0.0.0:8090/health`
```

#### Running the API and the job workers separately

`poe start` runs both roles in one process. To reproduce the split deployment
locally — one API that only serves requests, plus several queue consumers —
start them as separate processes. The consumers use
`python -m src.jobs.runtime`, which starts no HTTP server:

```bash
# 1x API, consuming nothing
JOBS__ENABLED=false APP__WORKERS=1 APP__LIVE_RELOAD=false \
  uv run python server.py > /tmp/api.log 2>&1 &

# 10x worker; lower pools because each process opens its own
for i in $(seq 1 10); do
  DATABASE__POOL_SIZE=3 DATABASE__MAX_OVERFLOW=3 \
    uv run python -m src.jobs.runtime > /tmp/worker-$i.log 2>&1 &
done
```

Verify the split took effect — the first count must be the number of workers,
the second must be zero:

```bash
grep -h "Started database job worker" /tmp/worker-*.log | wc -l
grep -c "Started database job worker" /tmp/api.log
```

Stop everything with `pkill -f "src.jobs.runtime"; pkill -f "server.py"`.

> The lowered pool settings are not optional at this scale: PostgreSQL defaults
> to `max_connections=100`, while 11 processes on the default pool of
> `10 + 20` can ask for up to 330 connections.

### Installing dependencies

`dev` dependency group is used to distinguish from production ones.

For first-time local setup:

```bash
# install project dependencies (including dev group)
uv sync --dev

# NOTE: browser binaries are not installed automatically by `uv sync`
uv run playwright install
```

```bash
# install production dependency
uv add mydep

# install dev dependency
uv add --dev mydep
```

## Quality assurance

All code should adhere to the quality checks below.
It is highly recommended to instal pre-commit hook and also integrate these tools below in the IDE.

### Linting and formatting

Quality checks using [ruff](https://github.com/astral-sh/ruff) and [mypy](https://github.com/python/mypy).

```bash
uv run poe typecheck
uv run poe lint
uv run poe stylecheck

# optionally run all quality checks (including unit tests)
uv run poe qa

# attempt to fix formatting and lint errors
uv run poe fix
```

### Tests

Running tests using [pytest](https://github.com/pytest-dev/pytest/).

```bash
# run all tests
uv run poe test

# run in watch mode
uv run poe test-watch

# run unit tests only
uv run poe test test/unit

# run integration tests only
uv run poe test test/integration
```

### Pre-commit hooks

This project uses [`pre-commit`](https://pre-commit.com/) to ensure consistent code style and other quality checks.

```bash
# install the hooks from .pre-commit-config.yaml
# this needs to be done just once when setting up project
uv run pre-commit install
```

Once installed, the hooks will automatically run every time you commit changes.
If any issues are found or files are modified, the commit will be aborted until fixed.

### Langfuse configuration

For development and testing purposes is every api request and llm call traced with [Langfuse](https://langfuse.com/).
By default is langfuse tracing disabled, you can enable it by configuration:

```
# configure correct langfuse project keys
LANGFUSE__SECRET_KEY=project-secret-key
LANGFUSE__PUBLIC_KEY=project-public-key
# when using for development define your own environment
LANGFUSE__ENVIRONMENT=dev-myname
# enable langfuse
LANGFUSE__TRACING_ENABLED=true
```

## Technical notes

- API endpoints and parameters have to follow camel case convention
- LLM model can be configured via any OpenAI-compatible chat API
