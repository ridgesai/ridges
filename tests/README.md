# Tests

## Running Tests

```bash
# Run all tests
uv run pytest

# Run a specific suite
uv run pytest tests/api/
uv run pytest tests/execution/
uv run pytest tests/miners/
uv run pytest tests/ridges_harbor/
uv run pytest tests/validator/

# Run a single file
uv run pytest tests/api/test_upload.py

# Run with verbose output
uv run pytest -v
```

## Structure

```
tests/
├── conftest.py                          # Root fixture: sets anyio_backend = "asyncio"
├── test_task_cache.py                   # Task download/caching logic
│
├── api/                                 # FastAPI endpoint tests
│   ├── test_upload.py                   # Idempotent agent upload / payment flow
│   └── test_validator_update_evaluation_run.py  # Validator evaluation run status updates
│
├── execution/                           # Execution engine tests
│   ├── helpers.py                       # Shared fixtures: make_summary, valid_execution_spec, etc.
│   ├── test_artifacts.py                # Result parsing from Harbor summaries
│   ├── test_engine.py                   # ExecutionEngine end-to-end scenarios
│   └── test_failure_classifier.py       # Failure shape → typed error code mapping
│
├── miners/                              # Miner CLI tests
│   ├── test_agent_file.py               # Agent file discovery and validation
│   ├── test_cli_config.py               # CLI config loading and defaults
│   ├── test_commands.py                 # CLI command wiring (setup, run-local, upload)
│   ├── test_inference_client.py         # Inference client against SANDBOX_PROXY_URL
│   ├── test_local_agent.py              # Local agent execution helpers
│   ├── test_local_harbor.py             # Local Harbor task runner integration
│   ├── test_provider_env.py             # Provider environment variable resolution
│   └── test_registry.py                # Agent registry operations
│
├── ridges_harbor/                       # Harbor runner tests
│   ├── test_docker_runtime.py           # Docker container runtime behaviour
│   └── test_runner.py                   # _run_task_dir and RidgesMinerAgent lifecycle
│
└── validator/
    └── test_main_status_flow.py         # Validator main loop: evaluation run status transitions
```

## Conventions

**Async tests** — all async tests are marked `@pytest.mark.anyio` and run on the `asyncio` backend. `anyio_backend` is module-scoped in `conftest.py`, meaning all async tests within a module share one event loop.

**Mostly no live services** — the majority of tests monkeypatch every external boundary (DB, S3, blockchain, Docker, HTTP). The exception is `tests/api/test_upload.py`, which uses a real Postgres instance via testcontainers (requires Docker). The `postgres_db` fixture (module-scoped, starts a Postgres container and runs Alembic migrations) lives in `conftest.py` and is available to any test file that needs a real database.

**Direct endpoint calls** — API tests call endpoint handler functions directly (bypassing FastAPI request parsing) using `monkeypatch` to stub blockchain and S3 helpers. See `test_upload.py` and `test_validator_update_evaluation_run.py` for the pattern.
