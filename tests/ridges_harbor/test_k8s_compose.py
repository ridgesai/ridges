import pytest

from ridges_harbor.k8s_compose import (
    parse_compose_bytes,
    parse_duration_seconds,
    parse_kubernetes_compose_text,
)

PG_NETBOX_COMPOSE = """
services:
  postgres:
    build:
      context: .
      dockerfile: postgres.Dockerfile
    volumes:
      - type: tmpfs
        target: /var/lib/postgresql/data
        tmpfs:
          size: 4294967296
    environment:
      POSTGRES_PASSWORD: miner-postgres-visible-71d301ac
      POSTGRES_DB: postgres
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d postgres && test -f /var/lib/postgresql/data/TASK_READY"]
      interval: 2s
      timeout: 2s
      retries: 300

  redis:
    build:
      context: .
      dockerfile: redis.Dockerfile
    healthcheck:
      test: ["CMD-SHELL", "redis-cli --user task_admin -a redis-admin-miner-visible-b675e04146c1 ping | grep -q PONG"]
      interval: 2s
      timeout: 2s
      retries: 60
"""

CLICKHOUSE_COMPOSE = """
services:
  clickhouse:
    image: clickhouse/clickhouse-server@sha256:35b419db86eed71ab1c41c03b4fd1f39be26f41eb38b0866268e2ca162445105
    environment:
      CLICKHOUSE_DB: default
      CLICKHOUSE_USER: default
      CLICKHOUSE_PASSWORD: ""
      CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: "1"
    volumes:
      - type: tmpfs
        target: /var/lib/clickhouse
        tmpfs:
          size: 1073741824
      - type: tmpfs
        target: /var/log/clickhouse-server
        tmpfs:
          size: 134217728
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8123/ping | grep -q Ok"]
      interval: 2s
      timeout: 2s
      retries: 60
"""


def test_parse_pg_netbox_subset() -> None:
    sidecars = parse_kubernetes_compose_text(PG_NETBOX_COMPOSE)
    assert [sidecar.name for sidecar in sidecars] == ["postgres", "redis"]
    postgres = sidecars[0]
    assert postgres.dockerfile == "postgres.Dockerfile"
    assert postgres.image is None
    assert postgres.tmpfs_mounts[0].size_bytes == 4294967296
    assert postgres.healthcheck is not None
    assert postgres.healthcheck.retries == 300
    assert sidecars[1].dockerfile == "redis.Dockerfile"


def test_parse_clickhouse_subset() -> None:
    sidecars = parse_kubernetes_compose_text(CLICKHOUSE_COMPOSE)
    assert len(sidecars) == 1
    clickhouse = sidecars[0]
    assert clickhouse.name == "clickhouse"
    assert clickhouse.image is not None
    assert clickhouse.image.startswith("clickhouse/clickhouse-server@sha256:")
    assert clickhouse.dockerfile is None
    assert [mount.target for mount in clickhouse.tmpfs_mounts] == [
        "/var/lib/clickhouse",
        "/var/log/clickhouse-server",
    ]


def test_leftover_main_and_proxy_are_skipped() -> None:
    sidecars = parse_kubernetes_compose_text("services:\n  main: {}\n  sandbox-proxy: {}\n")
    assert sidecars == []


def test_leftover_main_allows_command_and_depends_on() -> None:
    sidecars = parse_kubernetes_compose_text(
        "services:\n"
        "  main:\n"
        "    command: [sleep, infinity]\n"
        "    depends_on:\n"
        "      postgres:\n"
        "        condition: service_healthy\n"
        "  postgres:\n"
        "    image: postgres:16\n"
    )
    assert [sidecar.name for sidecar in sidecars] == ["postgres"]


def test_leftover_main_rejects_invalid_healthcheck() -> None:
    with pytest.raises(RuntimeError, match="healthcheck.test must be CMD-SHELL"):
        parse_kubernetes_compose_text('services:\n  main:\n    healthcheck:\n      test: ["CMD", "true"]\n')


def test_leftover_main_with_environment_is_not_a_sidecar() -> None:
    sidecars = parse_kubernetes_compose_text(
        "services:\n  main:\n    environment:\n      FOO: bar\n  postgres:\n    image: postgres:16\n"
    )
    assert [sidecar.name for sidecar in sidecars] == ["postgres"]


def test_rejects_leftover_main_ports() -> None:
    with pytest.raises(RuntimeError, match="unsupported keys"):
        parse_kubernetes_compose_text("services:\n  main:\n    ports:\n      - '8080:8080'\n")


def test_rejects_dockerfile_path_components() -> None:
    with pytest.raises(RuntimeError, match="basename"):
        parse_kubernetes_compose_text("services:\n  postgres:\n    build:\n      context: .\n      dockerfile: ../x\n")


def test_rejects_multiline_image() -> None:
    with pytest.raises(RuntimeError, match="single line"):
        parse_kubernetes_compose_text('services:\n  postgres:\n    image: "nginx\\nRUN evil"\n')


def test_parses_mem_limit_size_string() -> None:
    sidecars = parse_kubernetes_compose_text("services:\n  redis:\n    image: redis:7\n    mem_limit: 1g\n")
    assert sidecars[0].memory_limit_bytes == 1024**3
    assert sidecars[0].memory_request_bytes == 1024**3


def test_parses_deploy_reservations() -> None:
    sidecars = parse_kubernetes_compose_text(
        "services:\n"
        "  redis:\n"
        "    image: redis:7\n"
        "    deploy:\n"
        "      resources:\n"
        "        limits:\n"
        "          memory: 2g\n"
        "        reservations:\n"
        "          memory: 512m\n"
        "          cpus: '0.25'\n"
    )
    assert sidecars[0].memory_request_bytes == 512 * 1024**2
    assert sidecars[0].memory_limit_bytes == 2 * 1024**3
    assert sidecars[0].cpu_request == "250m"


def test_rejects_memory_request_above_limit() -> None:
    with pytest.raises(RuntimeError, match="memory request exceeds"):
        parse_kubernetes_compose_text(
            "services:\n  redis:\n    image: redis:7\n    mem_reservation: 2g\n    mem_limit: 1g\n"
        )


def test_parse_compose_bytes_accepts_int_and_suffix() -> None:
    assert parse_compose_bytes(4294967296, source="t", field="size") == 4294967296
    assert parse_compose_bytes("4g", source="t", field="size") == 4 * 1024**3
    assert parse_compose_bytes("512m", source="t", field="size") == 512 * 1024**2


def test_rejects_ports() -> None:
    with pytest.raises(RuntimeError, match="unsupported keys"):
        parse_kubernetes_compose_text(
            "services:\n  postgres:\n    image: postgres:16\n    ports:\n      - '5432:5432'\n"
        )


def test_rejects_top_level_networks() -> None:
    with pytest.raises(RuntimeError, match="unsupported top-level keys"):
        parse_kubernetes_compose_text("networks:\n  default: {}\nservices:\n  postgres:\n    image: postgres:16\n")


def test_parse_duration_compose_seconds() -> None:
    assert parse_duration_seconds("2s", default=10) == 2
    assert parse_duration_seconds("1m30s", default=10) == 90
    assert parse_duration_seconds(None, default=10) == 10
