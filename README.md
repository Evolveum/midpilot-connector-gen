# Connector Generator AI Service

Smart Integration Microservice for scraping, digester and CodeGen built with FastAPI.

## Project structure

Directory structure:

- [`src`](src) - production source code
- [`src/modules`](src/modules) - domain oriented modules with API routes and implementation
- [`src/common`](src/common) - common code and utils
- [`test/unit`](test/unit) - unit tests
- [`test/integration`](test/integration) - integration tests

Important files:

- [`server.py`](server.py) - uvicorn server entry point
- [`src/app.py`](src/app.py) - FastAPI entry point
- [`src/config.py`](src/config.py) - project configuration
- [`pyproject.toml`](pyproject.toml) - dependencies, tools, tasks
- [`Dockerfile`](Dockerfile) - docker file

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

## Running with Docker

### Requirements

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)

### Run using docker compose

```bash
# build image
docker compose build

# start container
docker compose up
```

## Local Development

### Prerequisites
- [Python 3.12+](https://www.python.org/downloads/)
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
- `DATABASE__PORT` - Database port (default: 5432)
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
DATABASE__PORT=5432
DATABASE__USER=user
DATABASE__PASSWORD=password
DATABASE__NAME=db

# Full connection string for Alembic and SQLAlchemy
DATABASE_URL=postgresql+asyncpg://${DATABASE__USER}:${DATABASE__PASSWORD}@${DATABASE__HOST}:${DATABASE__PORT}/${DATABASE__NAME}
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
uv run poe start
# access the service at http://localhost:8090
# e.g. `curl http://0.0.0.0:8090/health`
```

### Installing dependencies

`dev` dependency group is used to distinguish from production ones.

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
[Default langfuse host](https://langfuse-vaia.k8s.evolveum.com/) is behind Evolveum's VPN therefore you need to connect to it first.
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
