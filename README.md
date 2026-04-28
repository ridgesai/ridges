# Ridges

## Development Guide

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
