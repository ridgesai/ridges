from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config

import utils.database as _db
from alembic import command

BASE_REVISION = "622a36d5146f"
HEAD_REVISION = "6f59f4e0c487"
REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_COLUMNS = (
    "scoring_mode",
    "screener_1_threshold",
    "screener_2_threshold",
    "prune_threshold",
    "required_validator_count",
    "pre_screening_enabled",
    "auto_approval_enabled",
    "hardcoding_policy_version",
    "incentive_enabled",
    "incentive_performance_threshold",
    "incentive_cost_threshold",
    "incentive_reward_half_life_hours",
    "incentive_time_multiplier_scale_hours",
)


async def _migrate(revision: str) -> None:
    config = Config(REPO_ROOT / "alembic.ini")
    await asyncio.to_thread(command.upgrade if revision == HEAD_REVISION else command.downgrade, config, revision)


@pytest.mark.anyio
async def test_competition_foundation_migration_and_constraints(postgres_db) -> None:
    try:
        await _migrate(BASE_REVISION)

        async with _db.pool.acquire() as conn:
            await conn.execute("TRUNCATE evaluation_sets, competitions RESTART IDENTITY CASCADE")
            await conn.execute(
                """
                INSERT INTO competitions (set_id, start_date, end_date)
                VALUES
                    (10, '2026-01-01T00:00:00Z', '2026-02-01T00:00:00Z'),
                    (20, '2026-03-01T00:00:00Z', NULL),
                    (30, '2026-04-01T00:00:00Z', NULL)
                """
            )

        await _migrate(HEAD_REVISION)

        async with _db.pool.acquire() as conn:
            columns = {
                row["column_name"]: row
                for row in await conn.fetch(
                    """
                    SELECT column_name, is_nullable, column_default, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'competitions'
                    """
                )
            }
            assert {
                "submissions_closed_at",
                "is_paused",
                "emissions_end_at",
                "raw_emission_weight",
                *POLICY_COLUMNS,
            } <= columns.keys()
            assert "policy_schema_version" not in columns
            assert "policy_snapshot" not in columns
            assert columns["is_paused"]["is_nullable"] == "NO"
            assert columns["is_paused"]["column_default"] == "false"
            assert columns["raw_emission_weight"]["is_nullable"] == "NO"
            assert columns["raw_emission_weight"]["column_default"] == "0"
            assert columns["scoring_mode"]["data_type"] == "text"
            assert columns["screener_1_threshold"]["data_type"] == "numeric"
            assert columns["required_validator_count"]["data_type"] == "integer"
            assert columns["pre_screening_enabled"]["data_type"] == "boolean"

            constraint_names = {
                row["conname"]
                for row in await conn.fetch(
                    """
                    SELECT conname
                    FROM pg_constraint
                    WHERE conrelid = 'competitions'::regclass AND contype = 'c'
                    """
                )
            }
            assert {
                "ck_competitions_raw_emission_weight_finite_range",
                "ck_competitions_policy_complete",
                "ck_competitions_scoring_mode",
                "ck_competitions_screening_thresholds",
                "ck_competitions_required_validator_count",
                "ck_competitions_hardcoding_policy_version",
                "ck_competitions_incentive_thresholds",
                "ck_competitions_incentive_durations",
                "ck_competitions_end_not_before_start",
                "ck_competitions_submission_emissions_window",
            } <= constraint_names

            weights = await conn.fetch("SELECT set_id, raw_emission_weight FROM competitions ORDER BY set_id")
            assert [(row["set_id"], row["raw_emission_weight"]) for row in weights] == [
                (10, 0),
                (20, 0),
                (30, 1),
            ]

            await conn.execute("INSERT INTO competitions (set_id) VALUES (40)")
            defaults = await conn.fetchrow(
                """
                SELECT
                    is_paused,
                    raw_emission_weight,
                    scoring_mode,
                    screener_1_threshold,
                    screener_2_threshold,
                    prune_threshold,
                    required_validator_count,
                    pre_screening_enabled,
                    auto_approval_enabled,
                    hardcoding_policy_version,
                    incentive_enabled,
                    incentive_performance_threshold,
                    incentive_cost_threshold,
                    incentive_reward_half_life_hours,
                    incentive_time_multiplier_scale_hours
                FROM competitions
                WHERE set_id = 40
                """
            )
            assert dict(defaults) == {
                "is_paused": False,
                "raw_emission_weight": 0,
                **dict.fromkeys(POLICY_COLUMNS),
            }

            await conn.execute(
                """
                INSERT INTO competitions (
                    set_id,
                    start_date,
                    submissions_closed_at,
                    emissions_end_at,
                    raw_emission_weight,
                    scoring_mode,
                    screener_1_threshold,
                    screener_2_threshold,
                    prune_threshold,
                    required_validator_count,
                    pre_screening_enabled,
                    auto_approval_enabled,
                    hardcoding_policy_version,
                    incentive_enabled,
                    incentive_performance_threshold,
                    incentive_cost_threshold,
                    incentive_reward_half_life_hours,
                    incentive_time_multiplier_scale_hours
                )
                VALUES (
                    41,
                    '2026-01-01T00:00:00Z',
                    '2026-02-01T00:00:00Z',
                    '2026-03-01T00:00:00Z',
                    0.25,
                    'legacy',
                    0.4,
                    0.5,
                    0.9,
                    3,
                    true,
                    false,
                    'hardcoding-v1',
                    true,
                    0.03,
                    0.06,
                    336,
                    12
                )
                """
            )

            stored_policy = await conn.fetchrow(
                """
                SELECT
                    scoring_mode,
                    screener_1_threshold,
                    screener_2_threshold,
                    prune_threshold,
                    required_validator_count,
                    pre_screening_enabled,
                    auto_approval_enabled,
                    hardcoding_policy_version,
                    incentive_enabled,
                    incentive_performance_threshold,
                    incentive_cost_threshold,
                    incentive_reward_half_life_hours,
                    incentive_time_multiplier_scale_hours
                FROM competitions
                WHERE set_id = 41
                """
            )
            assert dict(stored_policy) == {
                "scoring_mode": "legacy",
                "screener_1_threshold": Decimal("0.4"),
                "screener_2_threshold": Decimal("0.5"),
                "prune_threshold": Decimal("0.9"),
                "required_validator_count": 3,
                "pre_screening_enabled": True,
                "auto_approval_enabled": False,
                "hardcoding_policy_version": "hardcoding-v1",
                "incentive_enabled": True,
                "incentive_performance_threshold": Decimal("0.03"),
                "incentive_cost_threshold": Decimal("0.06"),
                "incentive_reward_half_life_hours": Decimal("336"),
                "incentive_time_multiplier_scale_hours": Decimal("12"),
            }

            invalid_rows = [
                "UPDATE competitions SET raw_emission_weight = -0.1 WHERE set_id = 41",
                "UPDATE competitions SET raw_emission_weight = 1.1 WHERE set_id = 41",
                "UPDATE competitions SET raw_emission_weight = 'NaN' WHERE set_id = 41",
                "UPDATE competitions SET raw_emission_weight = 'Infinity' WHERE set_id = 41",
                "UPDATE competitions SET raw_emission_weight = '-Infinity' WHERE set_id = 41",
                "INSERT INTO competitions (set_id, scoring_mode) VALUES (42, 'legacy')",
                "UPDATE competitions SET scoring_mode = 'weighted' WHERE set_id = 41",
                "UPDATE competitions SET screener_1_threshold = -0.1 WHERE set_id = 41",
                "UPDATE competitions SET screener_2_threshold = 1.1 WHERE set_id = 41",
                "UPDATE competitions SET prune_threshold = 'NaN' WHERE set_id = 41",
                "UPDATE competitions SET required_validator_count = 0 WHERE set_id = 41",
                "UPDATE competitions SET hardcoding_policy_version = '   ' WHERE set_id = 41",
                "UPDATE competitions SET incentive_performance_threshold = 0 WHERE set_id = 41",
                "UPDATE competitions SET incentive_cost_threshold = 1 WHERE set_id = 41",
                "UPDATE competitions SET incentive_reward_half_life_hours = 0 WHERE set_id = 41",
                "UPDATE competitions SET incentive_time_multiplier_scale_hours = 'Infinity' WHERE set_id = 41",
                "INSERT INTO competitions (set_id, start_date, end_date) "
                "VALUES (42, '2026-02-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                "INSERT INTO competitions (set_id, submissions_closed_at) VALUES (42, NOW())",
                "INSERT INTO competitions (set_id, emissions_end_at) VALUES (42, NOW())",
                "INSERT INTO competitions (set_id, submissions_closed_at, emissions_end_at) "
                "VALUES (42, '2026-02-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            ]
            for statement in invalid_rows:
                with pytest.raises(asyncpg.CheckViolationError):
                    await conn.execute(statement)

            await conn.execute("UPDATE competitions SET screener_1_threshold = 0.87 WHERE set_id = 41")
            updated_threshold = await conn.fetchval("SELECT screener_1_threshold FROM competitions WHERE set_id = 41")
            assert updated_threshold == Decimal("0.87")

            await conn.execute(
                "INSERT INTO evaluation_sets (set_id, set_group, problem_name) VALUES (50, 'screener_1', 'problem-a')"
            )
            triggered = await conn.fetchrow(
                "SELECT start_date, is_paused, raw_emission_weight, scoring_mode FROM competitions WHERE set_id = 50"
            )
            assert triggered is not None
            assert triggered["start_date"] is not None
            assert triggered["is_paused"] is False
            assert triggered["raw_emission_weight"] == 0
            assert triggered["scoring_mode"] is None

        await _migrate(BASE_REVISION)
        async with _db.pool.acquire() as conn:
            remaining_columns = {
                row["column_name"]
                for row in await conn.fetch(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'competitions'
                    """
                )
            }
            assert (
                not {
                    "submissions_closed_at",
                    "is_paused",
                    "emissions_end_at",
                    "raw_emission_weight",
                    *POLICY_COLUMNS,
                }
                & remaining_columns
            )

            await conn.execute(
                "INSERT INTO evaluation_sets (set_id, set_group, problem_name) VALUES (60, 'screener_1', 'problem-b')"
            )
            assert await conn.fetchval("SELECT start_date FROM competitions WHERE set_id = 60") is not None
    finally:
        await _migrate(HEAD_REVISION)
