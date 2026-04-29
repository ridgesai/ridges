# Ridges

## Development Guide

### Requirements

- Python 3.12+
- Docker (for running dependencies like Postgres and S3 locally)
- UV (for managing Python dependencies)

### Setting up the development environment

Install dependencies (including dev tools):

```bash
uv sync --extra dev
```

Install the pre-commit hooks so ruff runs automatically before each commit:

```bash
uv run pre-commit install
```

To run the hooks manually against all files at any time:

```bash
uv run pre-commit run --all-files
```

### Running the services locally

1. Start the dependencies (Postgres and Adobe S3 mock) using docker compose. Make sure to update the `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` environment variables in `docker-compose.yml` before starting the services.

```bash
docker compose up -d
```

2. For the platform API, create a `.env` file in the `api/` directory based on the provided `.env.example` and fill in the required environment variables.

3. Start the API:

```bash
python -m api.src.main
```

4. For the validator, create a `.env` file in the `validator/` directory based on the provided `.env.example` and fill in the required environment variables. Depending on the "MODE" you might start a "Screener" or a "Validator"

5. Start the validator:

```bash
python -m validator.src.main
```
