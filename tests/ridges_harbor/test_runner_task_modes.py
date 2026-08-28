from pathlib import Path

import pytest

from ridges_harbor.runner import (
    _resolve_separate_verifier,
    _uses_separate_verifier,
    _validate_kubernetes_task_services,
)

SEPARATE_TOML = '\n[verifier]\nenvironment_mode = "separate"\n'


def _write_task(path: Path, verifier_toml: str = "") -> Path:
    path.mkdir(parents=True)
    (path / "instruction.md").write_text("Fix the task.\n")
    (path / "environment").mkdir()
    (path / "environment" / "Dockerfile").write_text("FROM alpine:3.20\n")
    (path / "tests").mkdir()
    (path / "tests" / "test.sh").write_text("#!/bin/sh\nexit 0\n")
    (path / "task.toml").write_text(f'schema_version = "1.0"\n{verifier_toml}')
    return path


@pytest.mark.parametrize(
    ("verifier_toml", "expected"),
    [
        ("", False),
        ('\n[verifier]\nenvironment_mode = "shared"\n', False),
        ('\n[verifier]\nenvironment_mode = "separate"\n', True),
        (
            '\n[verifier]\n[verifier.environment]\ndocker_image = "alpine:3.20"\n',
            True,
        ),
    ],
)
def test_uses_separate_verifier_follows_harbor_resolution(
    tmp_path: Path,
    verifier_toml: str,
    expected: bool,
) -> None:
    task_dir = _write_task(tmp_path / "task", verifier_toml)

    assert _uses_separate_verifier(task_dir) is expected


@pytest.mark.parametrize(
    "step_verifier",
    [
        "",
        '[steps.verifier]\nenvironment_mode = "separate"\n',
    ],
)
def test_multistep_task_is_rejected(tmp_path: Path, step_verifier: str) -> None:
    task_dir = _write_task(
        tmp_path / "task",
        f'\n[[steps]]\nname = "one"\n{step_verifier}',
    )
    step_dir = task_dir / "steps" / "one"
    step_dir.mkdir(parents=True)
    (step_dir / "instruction.md").write_text("Fix step one.\n")

    with pytest.raises(RuntimeError, match="only single-step tasks"):
        _uses_separate_verifier(task_dir)


def test_preflight_accepts_a_separate_task_that_ships_a_tests_dockerfile(tmp_path: Path) -> None:
    task_dir = _write_task(tmp_path / "task", SEPARATE_TOML)
    (task_dir / "tests" / "Dockerfile").write_text("FROM alpine:3.20\nCOPY . /tests\n")

    assert _resolve_separate_verifier(task_dir) is True


def test_preflight_accepts_a_separate_task_with_a_prebuilt_verifier_image(tmp_path: Path) -> None:
    task_dir = _write_task(
        tmp_path / "task",
        '\n[verifier]\n[verifier.environment]\ndocker_image = "alpine:3.20"\n',
    )

    assert _resolve_separate_verifier(task_dir) is True


def test_preflight_rejects_a_separate_task_without_a_verifier_environment(tmp_path: Path) -> None:
    task_dir = _write_task(tmp_path / "task", SEPARATE_TOML)

    with pytest.raises(RuntimeError, match="no verifier environment definition"):
        _resolve_separate_verifier(task_dir)


def test_preflight_rejects_a_separate_task_without_a_test_script(tmp_path: Path) -> None:
    task_dir = _write_task(tmp_path / "task", SEPARATE_TOML)
    (task_dir / "tests" / "Dockerfile").write_text("FROM alpine:3.20\nCOPY . /tests\n")
    (task_dir / "tests" / "test.sh").unlink()

    with pytest.raises(RuntimeError, match="missing tests/test.sh"):
        _resolve_separate_verifier(task_dir)


def test_preflight_leaves_shared_tasks_alone(tmp_path: Path) -> None:
    task_dir = _write_task(tmp_path / "task")

    assert _resolve_separate_verifier(task_dir) is False


def test_kubernetes_allows_only_the_materialized_proxy_scaffold(tmp_path: Path) -> None:
    task_dir = _write_task(tmp_path / "task")
    (task_dir / "environment" / "docker-compose.yaml").write_text("services:\n  main: {}\n  sandbox-proxy: {}\n")

    _validate_kubernetes_task_services(task_dir)


def test_kubernetes_allows_shared_leftover_only_tests_compose(tmp_path: Path) -> None:
    task_dir = _write_task(tmp_path / "task")
    (task_dir / "tests" / "docker-compose.yaml").write_text("services:\n  main: {}\n")

    _validate_kubernetes_task_services(task_dir)


def test_kubernetes_rejects_shared_tests_sidecars(tmp_path: Path) -> None:
    task_dir = _write_task(tmp_path / "task")
    (task_dir / "environment" / "docker-compose.yaml").write_text(
        "services:\n"
        "  postgres:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: postgres.Dockerfile\n"
        "    volumes:\n"
        "      - type: tmpfs\n"
        "        target: /var/lib/postgresql/data\n"
        "        tmpfs:\n"
        "          size: 4294967296\n"
        "    environment:\n"
        "      POSTGRES_PASSWORD: secret\n"
        "    healthcheck:\n"
        '      test: ["CMD-SHELL", "pg_isready"]\n'
        "      interval: 2s\n"
        "      timeout: 2s\n"
        "      retries: 300\n"
        "  redis:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: redis.Dockerfile\n"
        "    healthcheck:\n"
        '      test: ["CMD-SHELL", "redis-cli ping"]\n'
        "      interval: 2s\n"
        "      timeout: 2s\n"
        "      retries: 60\n"
    )
    (task_dir / "tests" / "docker-compose.yaml").write_text(
        "services:\n"
        "  clickhouse:\n"
        "    image: clickhouse/clickhouse-server@sha256:deadbeef\n"
        "    healthcheck:\n"
        '      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8123/ping"]\n'
        "      interval: 2s\n"
        "      timeout: 2s\n"
        "      retries: 60\n"
    )

    with pytest.raises(RuntimeError, match="shared mode"):
        _validate_kubernetes_task_services(task_dir)


def test_kubernetes_allows_separate_tests_sidecars(tmp_path: Path) -> None:
    task_dir = _write_task(tmp_path / "task", SEPARATE_TOML)
    (task_dir / "tests" / "Dockerfile").write_text("FROM alpine:3.20\nCOPY . /tests\n")
    (task_dir / "environment" / "docker-compose.yaml").write_text(
        "services:\n"
        "  postgres:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: postgres.Dockerfile\n"
        "    healthcheck:\n"
        '      test: ["CMD-SHELL", "pg_isready"]\n'
        "      interval: 2s\n"
        "      timeout: 2s\n"
        "      retries: 300\n"
    )
    (task_dir / "tests" / "docker-compose.yaml").write_text(
        "services:\n"
        "  clickhouse:\n"
        "    image: clickhouse/clickhouse-server@sha256:deadbeef\n"
        "    healthcheck:\n"
        '      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8123/ping"]\n'
        "      interval: 2s\n"
        "      timeout: 2s\n"
        "      retries: 60\n"
    )

    _validate_kubernetes_task_services(task_dir, separate_verifier=True)


@pytest.mark.parametrize(
    ("relative_path", "compose", "expected"),
    [
        (
            "environment/docker-compose.yaml",
            "services:\n  postgres:\n    image: postgres:16\n    ports:\n      - '5432:5432'\n",
            "unsupported keys",
        ),
        (
            "tests/docker-compose.yaml",
            "networks:\n  default: {}\nservices:\n  postgres:\n    image: postgres:16\n",
            "unsupported top-level keys",
        ),
    ],
)
def test_kubernetes_rejects_unsupported_compose(
    tmp_path: Path,
    relative_path: str,
    compose: str,
    expected: str,
) -> None:
    task_dir = _write_task(tmp_path / "task")
    compose_path = task_dir / relative_path
    compose_path.parent.mkdir(parents=True, exist_ok=True)
    compose_path.write_text(compose)

    with pytest.raises(RuntimeError, match=expected):
        _validate_kubernetes_task_services(task_dir)
