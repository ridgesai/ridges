import os
from pathlib import Path

import pytest

from ridges_harbor.digest import compute_task_digest
from ridges_harbor.runner import run_task

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_HARBOR_DOCKER_INTEGRATION") != "1",
    reason="set RUN_HARBOR_DOCKER_INTEGRATION=1 to run native Harbor Docker integration tests",
)


def _write_database_context(context_dir: Path, *, verifier: bool) -> None:
    context_dir.mkdir()
    app_dir = context_dir / "app"
    app_dir.mkdir()
    (app_dir / "README.md").write_text("database migration pending\n")

    db_dir = context_dir / "db"
    db_dir.mkdir()
    (db_dir / "Dockerfile").write_text(
        "FROM postgres:17-alpine\nCOPY init.sql /docker-entrypoint-initdb.d/001-init.sql\n"
    )
    (db_dir / "init.sql").write_text(
        "CREATE TABLE items (id integer PRIMARY KEY, value text NOT NULL);\n"
        "INSERT INTO items VALUES (1, 'alpha'), (2, 'beta');\n"
    )

    service_name = "verifier-db" if verifier else "agent-db"
    password = "verifier" if verifier else "agent"
    (context_dir / "docker-compose.yaml").write_text(
        "services:\n"
        f"  {service_name}:\n"
        "    build: ./db\n"
        "    environment:\n"
        f"      POSTGRES_PASSWORD: {password}\n"
        "    healthcheck:\n"
        f'      test: ["CMD-SHELL", "pg_isready -U postgres -d postgres"]\n'
        "      interval: 1s\n"
        "      timeout: 1s\n"
        "      retries: 30\n"
        "    volumes:\n"
        "      - database-data:/var/lib/postgresql/data\n"
        "volumes:\n"
        "  database-data: {}\n"
    )

    dockerfile_lines = [
        "FROM python:3.13-slim",
        "RUN apt-get update && apt-get install -y --no-install-recommends "
        "bash git postgresql-client && rm -rf /var/lib/apt/lists/*",
        "WORKDIR /app",
        "COPY app/README.md /app/README.md",
    ]
    if verifier:
        dockerfile_lines.extend(
            [
                "COPY test.sh /tests/test.sh",
                "RUN chmod 755 /tests/test.sh",
            ]
        )
    (context_dir / "Dockerfile").write_text("\n".join(dockerfile_lines) + "\n")


def _write_database_task(task_dir: Path) -> None:
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text(
        "Add a rerunnable migration.sql that creates idx_items_value on items(value).\n"
    )
    (task_dir / "task.toml").write_text(
        'schema_version = "1.3"\n'
        'artifacts = ["/logs/agent/patch.diff"]\n'
        "\n"
        "[environment]\n"
        'workdir = "/app"\n'
        "cpus = 1\n"
        "memory_mb = 512\n"
        "storage_mb = 1024\n"
        "\n"
        "[environment.env]\n"
        'PGHOST = "agent-db"\n'
        'PGPASSWORD = "agent"\n'
        'PGUSER = "postgres"\n'
        'PGDATABASE = "postgres"\n'
        "\n"
        "[verifier]\n"
        'environment_mode = "separate"\n'
        "\n"
        "[verifier.environment]\n"
        'workdir = "/app"\n'
        "cpus = 1\n"
        "memory_mb = 512\n"
        "storage_mb = 1024\n"
        "\n"
        "[verifier.environment.env]\n"
        'PGHOST = "verifier-db"\n'
        'PGPASSWORD = "verifier"\n'
        'PGUSER = "postgres"\n'
        'PGDATABASE = "postgres"\n'
    )

    _write_database_context(task_dir / "environment", verifier=False)
    tests_dir = task_dir / "tests"
    _write_database_context(tests_dir, verifier=True)
    (tests_dir / "test.sh").write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "mkdir -p /logs/verifier /logs/artifacts\n"
        "rm -rf /logs/artifacts/*\n"
        "cd /app\n"
        'cluster_id=$(psql -Atqc "SELECT system_identifier FROM pg_control_system()")\n'
        'base_rows=$(psql -Atqc "SELECT count(*) FROM items")\n'
        "marker_before=$(psql -Atqc \"SELECT to_regclass('public.miner_marker') IS NULL\")\n"
        "index_before=$(psql -Atqc \"SELECT to_regclass('public.idx_items_value') IS NULL\")\n"
        "agent_log_leaked=false\n"
        "if [ -e /logs/agent/miner-db-state.txt ]; then agent_log_leaked=true; fi\n"
        "migration_present=false\n"
        "index_after=false\n"
        "if [ -f migration.sql ]; then\n"
        "  migration_present=true\n"
        "  psql -v ON_ERROR_STOP=1 -f migration.sql >/dev/null\n"
        "  psql -v ON_ERROR_STOP=1 -f migration.sql >/dev/null\n"
        "  index_after=$(psql -Atqc \"SELECT to_regclass('public.idx_items_value') IS NOT NULL\")\n"
        "fi\n"
        "{\n"
        '  echo "cluster_id=$cluster_id"\n'
        '  echo "base_rows=$base_rows"\n'
        '  echo "marker_absent_before=$marker_before"\n'
        '  echo "index_absent_before=$index_before"\n'
        '  echo "agent_log_leaked=$agent_log_leaked"\n'
        '  echo "migration_present=$migration_present"\n'
        '  echo "index_present_after=$index_after"\n'
        "} > /logs/verifier/database-state.txt\n"
        "reward=0\n"
        'if [ "$base_rows" = 2 ] && [ "$marker_before" = t ] && '
        '[ "$index_before" = t ] && [ "$agent_log_leaked" = false ] && '
        '[ "$migration_present" = true ] && [ "$index_after" = t ]; then\n'
        "  reward=1\n"
        "fi\n"
        "printf '%s\\n' \"$reward\" > /logs/verifier/reward.txt\n"
    )


def _write_database_agent(agent_path: Path, *, oracle: bool) -> None:
    if oracle:
        patch = (
            "diff --git a/migration.sql b/migration.sql\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/migration.sql\n"
            "@@ -0,0 +1 @@\n"
            "+CREATE INDEX IF NOT EXISTS idx_items_value ON items(value);\n"
        )
    else:
        patch = (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "-database migration pending\n"
            "+database migration attempted only in the miner database\n"
        )

    agent_path.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "import subprocess\n"
        "\n"
        "def agent_main(_input):\n"
        "    subprocess.run(\n"
        "        [\n"
        "            'psql', '-v', 'ON_ERROR_STOP=1', '-c',\n"
        "            'CREATE TABLE miner_marker (value text); ',\n"
        "            '-c', 'CREATE INDEX idx_items_value ON items(value);',\n"
        "        ],\n"
        "        check=True,\n"
        "        env=os.environ,\n"
        "    )\n"
        "    cluster_id = subprocess.check_output(\n"
        "        ['psql', '-Atqc', 'SELECT system_identifier FROM pg_control_system()'],\n"
        "        env=os.environ,\n"
        "        text=True,\n"
        "    ).strip()\n"
        "    Path('/logs/agent/miner-db-state.txt').write_text(\n"
        "        f'cluster_id={cluster_id}\\nmarker_created=true\\nindex_created=true\\n'\n"
        "    )\n"
        f"    return {patch!r}\n"
    )


async def _run_database_trial(
    task_dir: Path,
    tmp_path: Path,
    *,
    label: str,
    oracle: bool,
) -> tuple[float, str, str]:
    agent_path = tmp_path / f"agent-{label}.py"
    _write_database_agent(agent_path, oracle=oracle)
    summary = await run_task(
        task_dir,
        task_name="native-separate-postgres",
        task_digest=compute_task_digest(task_dir),
        evaluation_run_id=f"native-postgres-{label}",
        agent_path=agent_path,
        agent_timeout_sec=180,
        verifier_timeout_sec=180,
        results_dir=tmp_path / "results",
        job_name=f"native-postgres-{label}",
    )

    assert summary.trial_result.exception_info is None
    assert summary.trial_result.verifier_result is not None
    reward = summary.trial_result.verifier_result.rewards["reward"]
    miner_state = (summary.trial_dir / "agent" / "miner-db-state.txt").read_text()
    verifier_state = (summary.trial_dir / "verifier" / "database-state.txt").read_text()
    assert "marker_created=true" in miner_state
    assert "index_created=true" in miner_state
    assert "base_rows=2" in verifier_state
    assert "marker_absent_before=t" in verifier_state
    assert "index_absent_before=t" in verifier_state
    assert "agent_log_leaked=false" in verifier_state
    miner_cluster = miner_state.splitlines()[0].split("=", 1)[1]
    verifier_cluster = verifier_state.splitlines()[0].split("=", 1)[1]
    assert miner_cluster != verifier_cluster
    return reward, miner_cluster, verifier_cluster


@pytest.mark.anyio
async def test_native_separate_postgres_rebuilds_from_base_and_applies_only_patch(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "native-separate-postgres"
    _write_database_task(task_dir)

    nop_reward, nop_miner_cluster, nop_verifier_cluster = await _run_database_trial(
        task_dir, tmp_path, label="nop", oracle=False
    )
    oracle_one_reward, oracle_one_miner_cluster, oracle_one_verifier_cluster = await _run_database_trial(
        task_dir, tmp_path, label="oracle-one", oracle=True
    )
    oracle_two_reward, oracle_two_miner_cluster, oracle_two_verifier_cluster = await _run_database_trial(
        task_dir, tmp_path, label="oracle-two", oracle=True
    )

    assert nop_reward == 0
    assert oracle_one_reward == 1
    assert oracle_two_reward == 1
    assert (
        len(
            {
                nop_miner_cluster,
                nop_verifier_cluster,
                oracle_one_miner_cluster,
                oracle_one_verifier_cluster,
                oracle_two_miner_cluster,
                oracle_two_verifier_cluster,
            }
        )
        == 6
    )
