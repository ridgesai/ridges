#!/usr/bin/env python3
"""
Populate PostgreSQL database with dummy data for testing.
Usage: python populate_dummy_data.py [--num-agents N] [--num-evals N] [--num-runs N]
"""

import argparse
import asyncio
import asyncpg
import uuid
from datetime import datetime, timedelta
import random
import json
import os
from dotenv import load_dotenv

# Load environment variables from current directory
load_dotenv()

# Default dummy data configuration
DEFAULT_NUM_AGENTS = 12  # Ensures good distribution across quartiles (12 % 4 = 0)
DEFAULT_NUM_EVALUATIONS_PER_AGENT = 3
DEFAULT_NUM_RUNS_PER_EVALUATION = 10

AGENT_STATUSES = ['cancelled', 'screening_1', 'failed_screening_1', 'screening_2', 'failed_screening_2', 'evaluating', 'finished']
RUN_STATUSES = ['pending', 'initializing_agent', 'running_agent', 'initializing_eval', 'running_eval', 'finished', 'error']
PROBLEM_NAMES = [
    'django__django-12308',
    'django__django-12453',
    'django__django-12589',
    'sympy__sympy-15599',
    'matplotlib__matplotlib-23476',
    'scikit-learn__scikit-learn-13142',
    'requests__requests-3362',
    'flask__flask-4169',
    'pytest__pytest-5692',
    'numpy__numpy-19107'
]

def generate_test_results(passed=True):
    """Generate dummy test results JSON"""
    num_tests = random.randint(3, 8)
    results = []
    for i in range(num_tests):
        results.append({
            "name": f"test_{i+1}",
            "status": "pass" if (passed or random.random() > 0.3) else "fail",
            "duration": round(random.uniform(0.1, 2.0), 2)
        })
    return json.dumps(results)

def generate_logs(log_type='agent'):
    """Generate dummy logs"""
    if log_type == 'agent':
        return """Agent started
Analyzing problem...
Running tests...
Generated patch
Agent completed successfully"""
    else:
        return """Evaluation started
Setting up environment...
Running test suite...
Evaluation completed"""

async def get_db_connection():
    """Get database connection using environment variables"""
    db_user = os.getenv("AWS_MASTER_USERNAME")
    db_pass = os.getenv("AWS_MASTER_PASSWORD")
    db_host = os.getenv("AWS_RDS_PLATFORM_ENDPOINT")
    db_name = os.getenv("AWS_RDS_PLATFORM_DB_NAME")
    db_port = os.getenv("PGPORT", "5432")

    if not all([db_user, db_pass, db_host, db_name]):
        missing = [
            name
            for name, value in [
                ("AWS_MASTER_USERNAME", db_user),
                ("AWS_MASTER_PASSWORD", db_pass),
                ("AWS_RDS_PLATFORM_ENDPOINT", db_host),
                ("AWS_RDS_PLATFORM_DB_NAME", db_name),
            ]
            if not value
        ]
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return await asyncpg.connect(
        user=db_user,
        password=db_pass,
        host=db_host,
        port=int(db_port),
        database=db_name,
    )

async def recreate_database(conn):
    """Drop and recreate the database schema"""
    print("\n💥 Recreating database schema...")

    from pathlib import Path
    schema_file = Path(__file__).parent / "postgres_schema.sql"

    if not schema_file.exists():
        raise Exception(f"Schema file not found: {schema_file}")

    # Drop all materialized views and tables
    print("  Dropping materialized views...")
    await conn.execute("DROP MATERIALIZED VIEW IF EXISTS agent_scores CASCADE")
    await conn.execute("DROP MATERIALIZED VIEW IF EXISTS evaluations_hydrated CASCADE")
    await conn.execute("DROP MATERIALIZED VIEW IF EXISTS evaluation_runs_hydrated CASCADE")

    print("  Dropping tables...")
    tables = [
        'evaluation_run_logs',
        'evaluation_runs',
        'evaluations',
        'agents',
        'evaluation_sets',
        'treasury_transactions',
        'approved_agents',
        'top_agents',
        'approved_top_agents_history',
        'upload_attempts',
        'inferences',
        'embeddings',
        'banned_hotkeys',
        'threshold_config',
        'open_user_bittensor_hotkeys',
        'open_users',
        'treasury_wallets',
        'platform_status_checks',
    ]

    for table in tables:
        try:
            await conn.execute(f'DROP TABLE IF EXISTS {table} CASCADE')
            print(f"    Dropped {table}")
        except Exception as e:
            print(f"    Warning: Could not drop {table}: {e}")

    # Drop types
    print("  Dropping types...")
    await conn.execute("DROP TYPE IF EXISTS AgentStatus CASCADE")
    await conn.execute("DROP TYPE IF EXISTS EvaluationRunStatus CASCADE")
    await conn.execute("DROP TYPE IF EXISTS EvaluationRunLogType CASCADE")
    await conn.execute("DROP TYPE IF EXISTS EvaluationStatus CASCADE")

    # Recreate schema
    print("  Recreating schema from postgres_schema.sql...")
    sql_text = schema_file.read_text()
    await conn.execute(sql_text)

    print("✅ Database schema recreated")

async def wipe_database(conn):
    """Wipe all data from the database tables"""
    print("\n🗑️  Wiping database data...")

    # Order matters due to foreign key constraints
    tables = [
        'evaluation_run_logs',
        'evaluation_runs',
        'evaluations',
        'agents',
        'evaluation_sets',
        'treasury_transactions',
        'approved_agents',
        'top_agents',
        'approved_top_agents_history',
        'upload_attempts',
        'inferences',
        'embeddings',
    ]

    for table in tables:
        try:
            result = await conn.execute(f'DELETE FROM {table}')
            print(f"  Deleted from {table}")
        except Exception as e:
            print(f"  Warning: Could not delete from {table}: {e}")

    print("✅ Database data wiped")

async def populate_data(num_agents: int, num_evaluations_per_agent: int, num_runs_per_evaluation: int, wipe: bool = False, recreate: bool = False):
    print("Connecting to database...")
    conn = await get_db_connection()

    try:
        if recreate:
            await recreate_database(conn)
        elif wipe:
            await wipe_database(conn)

        print("\nStarting to populate database with dummy data...")
        print(f"  Agents: {num_agents}")
        print(f"  Evaluations per agent: {num_evaluations_per_agent}")
        print(f"  Runs per evaluation: {num_runs_per_evaluation}")

        # Create evaluation set if not exists
        await conn.execute("""
            INSERT INTO evaluation_sets (set_id, type, problem_name)
            VALUES (1, 'validator', $1)
            ON CONFLICT (set_id, type, problem_name) DO NOTHING
        """, PROBLEM_NAMES[0])

        for problem in PROBLEM_NAMES[:5]:
            await conn.execute("""
                INSERT INTO evaluation_sets (set_id, type, problem_name)
                VALUES (1, 'validator', $1)
                ON CONFLICT (set_id, type, problem_name) DO NOTHING
            """, problem)

        # Create agents - ensure at least one agent in each status
        agent_ids = []

        # First, create one agent for each status
        for i, status in enumerate(AGENT_STATUSES):
            agent_id = str(uuid.uuid4())
            agent_ids.append(agent_id)

            miner_hotkey = f"5{''.join([str(random.randint(0, 9)) for _ in range(47)])}"
            agent_name = f"TestAgent_{status}"
            version_num = 1
            created_at = datetime.now() - timedelta(days=random.randint(1, 30))

            await conn.execute("""
                INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status,
                                   agent_summary, created_at, ip_address, innovation)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (agent_id) DO NOTHING
            """,
                agent_id,
                miner_hotkey,
                agent_name,
                version_num,
                status,
                f"Test agent in {status} status",
                created_at,
                f"192.168.1.{random.randint(1, 255)}",
                random.uniform(0.1, 1.0)
            )

            print(f"Created agent: {agent_name} (status={status}, {agent_id})")

        # Then create remaining agents with random statuses
        for i in range(len(AGENT_STATUSES), num_agents):
            agent_id = str(uuid.uuid4())
            agent_ids.append(agent_id)

            miner_hotkey = f"5{''.join([str(random.randint(0, 9)) for _ in range(47)])}"
            agent_name = f"TestAgent_{i+1}"
            version_num = random.randint(1, 5)
            status = random.choice(AGENT_STATUSES)
            created_at = datetime.now() - timedelta(days=random.randint(1, 30))

            await conn.execute("""
                INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status,
                                   agent_summary, created_at, ip_address, innovation)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (agent_id) DO NOTHING
            """,
                agent_id,
                miner_hotkey,
                agent_name,
                version_num,
                status,
                f"Test agent {i+1} for evaluation",
                created_at,
                f"192.168.1.{random.randint(1, 255)}",
                random.uniform(0.1, 1.0)
            )

            print(f"Created agent: {agent_name} ({agent_id})")

        # Create evaluations and evaluation runs following the state machine flow
        # Agent flow: screening_1 → (pass) → screening_2 → (pass) → evaluating → (3 evals) → finished
        #          or screening_1 → (fail) → failed_screening_1
        #          or screening_2 → (fail) → failed_screening_2

        for idx, agent_id in enumerate(agent_ids):
            agent_status = await conn.fetchval("SELECT status FROM agents WHERE agent_id = $1", agent_id)

            evaluations_to_create = []

            if agent_status == 'cancelled':
                # Cancelled agents may have no evaluations or partial evaluations
                pass

            elif agent_status == 'screening_1':
                # Currently in screening_1, may have 0 or more in-progress screener-1 evaluations
                num_sc1_evals = random.choice([0, 1, 2])  # 0 = waiting, 1-2 = in progress
                for _ in range(num_sc1_evals):
                    evaluations_to_create.append(('screener-1-1', 'screener_1', 'running'))

            elif agent_status == 'failed_screening_1':
                # Failed screening_1, must have at least 1 failed screener-1 evaluation
                num_failed = random.randint(1, 3)
                for _ in range(num_failed):
                    evaluations_to_create.append(('screener-1-1', 'screener_1', 'failure'))

            elif agent_status == 'screening_2':
                # Passed screening_1, now in screening_2
                # Must have 1 successful screener-1 evaluation
                evaluations_to_create.append(('screener-1-1', 'screener_1', 'success'))
                # May have 0 or more in-progress screener-2 evaluations
                num_sc2_evals = random.choice([0, 1, 2])
                for _ in range(num_sc2_evals):
                    evaluations_to_create.append(('screener-2-1', 'screener_2', 'running'))

            elif agent_status == 'failed_screening_2':
                # Passed screening_1 but failed screening_2
                evaluations_to_create.append(('screener-1-1', 'screener_1', 'success'))
                num_failed = random.randint(1, 3)
                for _ in range(num_failed):
                    evaluations_to_create.append(('screener-2-1', 'screener_2', 'failure'))

            elif agent_status == 'evaluating':
                # Passed both screeners, now getting validator evaluations
                evaluations_to_create.append(('screener-1-1', 'screener_1', 'success'))
                evaluations_to_create.append(('screener-2-1', 'screener_2', 'success'))
                # Has 1-2 validator evaluations (not yet 3)
                num_val_evals = random.randint(1, 2)
                for i in range(num_val_evals):
                    validator_hotkey = f'validator_{random.randint(1, 5)}'
                    outcome = random.choice(['success', 'running'])
                    evaluations_to_create.append((validator_hotkey, 'validator', outcome))

            elif agent_status == 'finished':
                # Completed all stages, has 3+ successful validator evaluations
                evaluations_to_create.append(('screener-1-1', 'screener_1', 'success'))
                evaluations_to_create.append(('screener-2-1', 'screener_2', 'success'))
                num_val_evals = random.randint(3, 5)
                for i in range(num_val_evals):
                    validator_hotkey = f'validator_{random.randint(1, 5)}'
                    evaluations_to_create.append((validator_hotkey, 'validator', 'success'))

            for validator_hotkey, eval_type, outcome in evaluations_to_create:
                evaluation_id = str(uuid.uuid4())
                set_id = 1
                created_at = datetime.now() - timedelta(days=random.randint(0, 10))

                # Determine evaluation outcome based on the state machine flow
                if outcome == 'success':
                    # Success: all runs finished successfully
                    finished_at = created_at + timedelta(hours=random.randint(1, 48))
                    all_runs_status = 'finished'
                elif outcome == 'failure':
                    # Failure: finished but some/all runs errored
                    finished_at = created_at + timedelta(hours=random.randint(1, 48))
                    all_runs_status = 'mixed'  # Will randomly assign finished/error
                else:  # outcome == 'running'
                    # Running: not finished yet
                    finished_at = None
                    all_runs_status = 'running'

                await conn.execute("""
                    INSERT INTO evaluations (evaluation_id, agent_id, validator_hotkey,
                                            set_id, created_at, finished_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (evaluation_id) DO NOTHING
                """, evaluation_id, agent_id, validator_hotkey, set_id, created_at, finished_at)

                print(f"  Created {eval_type} evaluation: {evaluation_id} (validator={validator_hotkey})")

                # Create evaluation runs for this evaluation
                for run_num in range(num_runs_per_evaluation):
                    evaluation_run_id = str(uuid.uuid4())
                    problem_name = random.choice(PROBLEM_NAMES)

                    # Determine run status based on evaluation outcome
                    if all_runs_status == 'finished':
                        status = 'finished'
                    elif all_runs_status == 'mixed':
                        status = random.choice(['finished', 'error'])
                    else:
                        status = random.choice(RUN_STATUSES)

                    # Generate test results if finished
                    test_results = None
                    if status in ['finished', 'error']:
                        test_results = generate_test_results(passed=(status == 'finished' and random.random() > 0.2))

                    # Generate patch if finished successfully
                    patch = None
                    if status == 'finished':
                        patch = f"""diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,3 +1,4 @@
 def test_function():
-    return False
+    # Fixed the bug
+    return True
"""

                    # Generate realistic error codes
                    # 1xxx: agent errors (import issues, syntax errors, timeouts)
                    # 2xxx: validator errors (docker issues, evaluation failures)
                    # 3xxx: platform errors (network issues, database errors)
                    error_code = None
                    error_message = None
                    if status == 'error':
                        error_type = random.choice(['agent', 'validator', 'platform'])
                        if error_type == 'agent':
                            error_code = random.choice([1001, 1002, 1003, 1004, 1005])
                            error_messages = [
                                "Agent import error: module not found",
                                "Agent syntax error in generated code",
                                "Agent timeout: exceeded 120 second limit",
                                "Agent runtime error: division by zero",
                                "Agent invalid output: missing required fields"
                            ]
                            error_message = error_messages[error_code - 1001]
                        elif error_type == 'validator':
                            error_code = random.choice([2001, 2002, 2003, 2004])
                            error_messages = [
                                "Validator docker error: container failed to start",
                                "Validator evaluation error: test suite not found",
                                "Validator timeout: exceeded maximum evaluation time",
                                "Validator resource error: out of memory"
                            ]
                            error_message = error_messages[error_code - 2001]
                        else:  # platform
                            error_code = random.choice([3001, 3002, 3003])
                            error_messages = [
                                "Platform network error: connection timeout",
                                "Platform database error: query failed",
                                "Platform storage error: S3 upload failed"
                            ]
                            error_message = error_messages[error_code - 3001]

                    run_created_at = created_at + timedelta(minutes=run_num * 5)
                    finished_or_errored_at = run_created_at + timedelta(minutes=random.randint(5, 30)) if status in ['finished', 'error'] else None

                    await conn.execute("""
                        INSERT INTO evaluation_runs (
                            evaluation_run_id, evaluation_id, problem_name, status,
                            patch, test_results, error_code, error_message,
                            created_at, started_initializing_agent_at, started_running_agent_at,
                            started_initializing_eval_at, started_running_eval_at,
                            finished_or_errored_at
                        ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12, $13, $14)
                        ON CONFLICT (evaluation_run_id) DO NOTHING
                    """,
                        evaluation_run_id,
                        evaluation_id,
                        problem_name,
                        status,
                        patch,
                        test_results,
                        error_code,
                        error_message,
                        run_created_at,
                        run_created_at + timedelta(seconds=10) if status != 'pending' else None,
                        run_created_at + timedelta(seconds=30) if status not in ['pending', 'initializing_agent'] else None,
                        run_created_at + timedelta(minutes=2) if status not in ['pending', 'initializing_agent', 'running_agent'] else None,
                        run_created_at + timedelta(minutes=3) if status not in ['pending', 'initializing_agent', 'running_agent', 'initializing_eval'] else None,
                        finished_or_errored_at
                    )

                    # Create evaluation run logs
                    log_type = random.choice(['agent', 'eval'])
                    await conn.execute("""
                        INSERT INTO evaluation_run_logs (evaluation_run_id, logs, type)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (evaluation_run_id) DO NOTHING
                    """, evaluation_run_id, generate_logs(log_type), log_type)

                    print(f"    Created run: {problem_name} ({status})")

        print("\n✅ Successfully populated database with dummy data!")
        print(f"   - {num_agents} agents")
        print(f"   - {num_agents * num_evaluations_per_agent} evaluations")
        print(f"   - {num_agents * num_evaluations_per_agent * num_runs_per_evaluation} evaluation runs")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        raise
    finally:
        await conn.close()

def main():
    parser = argparse.ArgumentParser(description='Populate database with dummy data')
    parser.add_argument(
        '--num-agents',
        type=int,
        default=DEFAULT_NUM_AGENTS,
        help=f'Number of agents to create (default: {DEFAULT_NUM_AGENTS})'
    )
    parser.add_argument(
        '--num-evals',
        type=int,
        default=DEFAULT_NUM_EVALUATIONS_PER_AGENT,
        help=f'Number of evaluations per agent (default: {DEFAULT_NUM_EVALUATIONS_PER_AGENT})'
    )
    parser.add_argument(
        '--num-runs',
        type=int,
        default=DEFAULT_NUM_RUNS_PER_EVALUATION,
        help=f'Number of runs per evaluation (default: {DEFAULT_NUM_RUNS_PER_EVALUATION})'
    )
    parser.add_argument(
        '--wipe',
        action='store_true',
        help='Wipe all data from database before populating'
    )
    parser.add_argument(
        '--recreate',
        action='store_true',
        help='Drop and recreate entire database schema before populating (DESTRUCTIVE)'
    )

    args = parser.parse_args()

    # Validate arguments
    if args.recreate and args.wipe:
        print("Error: Cannot use both --recreate and --wipe. Use --recreate alone.")
        return

    print("Database Population Script")
    print("=" * 50)
    if args.recreate:
        print("\n🔥 🔥 🔥  WARNING: This will DROP AND RECREATE the entire database schema! 🔥 🔥 🔥")
        print("All data, tables, views, and types will be destroyed and recreated.\n")
    elif args.wipe:
        print("\n⚠️  ⚠️  ⚠️  WARNING: This will WIPE ALL DATA from the database! ⚠️  ⚠️  ⚠️\n")
    else:
        print("\n⚠️  This will add dummy data to the database!\n")

    response = input("Continue? (y/n): ")
    if response.lower() == 'y':
        asyncio.run(populate_data(args.num_agents, args.num_evals, args.num_runs, args.wipe, args.recreate))
    else:
        print("Cancelled.")

if __name__ == "__main__":
    main()
