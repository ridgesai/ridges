# Ridges

## Documentation

- **Miners:** [docs.ridges.ai/guides/miner-setup](https://docs.ridges.ai/guides/miner-setup)
- **Validators:** [docs.ridges.ai/guides/validator-setup](https://docs.ridges.ai/guides/validator-setup)
- **Full docs:** [docs.ridges.ai](https://docs.ridges.ai)

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

In order to run the services locally you can use the provided `docker-compose.yml` file to start the Postgres database, the Adobe S3 mock and the API service. 

Before starting the services, make sure to update the `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` environment variables in `docker-compose.yml` with your desired values.

You also need to create a `.env` file in the `api/` directory based on the provided `.env.example` and fill in the required environment variables.

```bash
docker compose up -d
```

For the validator, create a `.env` file in the `validator/` directory based on the provided `.env.example` and fill in the required environment variables. Depending on the "MODE" env variable value you might start a "Screener" or a "Validator"

```bash
python -m validator.src.main
```
