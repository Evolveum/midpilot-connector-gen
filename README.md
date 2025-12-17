# Smart Integration Microservice

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

NOTE: tasks are run with a [poethepoet](https://github.com/nat-n/poethepoet) tool and configured in [pyproject.toml](pyproject.toml)

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
