from __future__ import annotations

import os
import atexit
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, Any
from datetime import datetime, timezone

import asyncpg

class InternalTools:

    def __init__(self, *, min_con: int = 1, max_con: int = 8) -> None:
        db_user = os.getenv("DB_USER_INT")
        db_pass = os.getenv("DB_PASS_INT")
        db_host = os.getenv("DB_HOST_INT")
        db_port = os.getenv("DB_PORT_INT", "5432")
        db_name = os.getenv("DB_NAME_INT")

        if not all([db_user, db_pass, db_host, db_name]):
            missing = [
                name
                for name, value in [
                    ("DB_USER_INT", db_user),
                    ("DB_PASS_INT", db_pass),
                    ("DB_HOST_INT", db_host),
                    ("DB_NAME_INT", db_name),
                ]
                if not value
            ]
            raise RuntimeError(
                f"Missing required INT DB environment variables: {', '.join(missing)}"
            )

        self._conn_args: dict[str, object] = {
            "user": db_user,
            "password": db_pass,
            "host": db_host,
            "port": int(db_port),
            "database": db_name,
        }
        self._min_con = min_con
        self._max_con = max_con
        self._pool: Optional[asyncpg.Pool] = None

    async def open(self) -> None:
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(
            **self._conn_args,
            min_size=self._min_con,
            max_size=self._max_con,
            max_inactive_connection_lifetime=300,
            statement_cache_size=512,
            command_timeout=30,
            server_settings={
                "idle_in_transaction_session_timeout": "300000",
                "statement_timeout": "30000",
                "lock_timeout": "30000",
            },
        )
        self._register_atexit_cleanup()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _register_atexit_cleanup(self) -> None:
        if getattr(self, "_atexit_registered", False):
            return

        def _cleanup() -> None:
            try:
                if self._pool is None:
                    return
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    loop.create_task(self.close())
                else:
                    asyncio.run(self.close())
            except Exception:
                pass

        atexit.register(_cleanup)
        self._atexit_registered = True

    @asynccontextmanager
    async def acquire(self):
        if self._pool is None:
            await self.open()
        assert self._pool is not None
        async with self._pool.acquire() as con:
            yield con

    async def get_emission_alpha_for_hotkeys(self, miner_hotkeys: list[str]) -> int:
        if not miner_hotkeys:
            return 0

        query = (
            """
            SELECT COALESCE(SUM(es.emission_alpha_rao), 0) AS total_alpha
            FROM emission_snapshots AS es
            WHERE es.hotkey = ANY($1::text[]);
            """
        )

        async with self.acquire() as conn:
            value = await conn.fetchval(query, miner_hotkeys)
            return int(value or 0)

    async def get_emission_alpha_for_hotkeys_during_periods(
        self,
        periods: list[tuple[datetime, datetime]],
        miner_hotkeys: list[str],
    ) -> int:
        if not miner_hotkeys or not periods:
            return 0

        valid_periods: list[tuple[datetime, datetime]] = [
            (start, end) for start, end in periods if start is not None and end is not None and start < end
        ]
        if not valid_periods:
            return 0

        def _to_naive_utc(dt: datetime) -> datetime:
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt

        starts = [_to_naive_utc(p[0]) for p in valid_periods]
        ends = [_to_naive_utc(p[1]) for p in valid_periods]

        query = (
            """
            WITH period_bounds AS (
                SELECT s AS start_at, e AS end_at
                FROM unnest($2::timestamp[], $3::timestamp[]) AS t(s, e)
            )
            SELECT COALESCE(SUM(es.emission_alpha_rao), 0) AS total_alpha
            FROM emission_snapshots AS es
            WHERE es.hotkey = ANY($1::text[])
              AND EXISTS (
                SELECT 1
                FROM period_bounds pb
                WHERE es.occured_at >= pb.start_at
                  AND es.occured_at < pb.end_at
              );
            """
        )

        async with self.acquire() as conn:
            value = await conn.fetchval(query, miner_hotkeys, starts, ends)
            return int(value or 0)

    async def get_transfer_stake_extrinsic_details(self, event_code: str) -> Optional[dict[str, Any]]:
        query = (
            """
            SELECT
              occured_at,
              raw_extrinsic->>'address' AS sender_coldkey,
              (jsonb_path_query_first(raw_extrinsic, '$.call.call_args[*] ? (@.name == "alpha_amount").value') #>> '{}')::numeric AS alpha_amount,
              jsonb_path_query_first(raw_extrinsic, '$.call.call_args[*] ? (@.name == "destination_coldkey").value') #>> '{}' AS destination_coldkey,
              jsonb_path_query_first(raw_extrinsic, '$.call.call_args[*] ? (@.name == "hotkey").value') #>> '{}' AS staker_hotkey
            FROM extrinsics
            WHERE extrinsic_code = $1
            ORDER BY occured_at DESC
            LIMIT 1;
            """
        )

        async with self.acquire() as conn:
            row = await conn.fetchrow(query, event_code)
            if not row:
                return None

            alpha_val = row["alpha_amount"]
            return {
                "occurred_at": datetime.fromisoformat(str(row["occured_at"])),
                "sender_coldkey": str(row["sender_coldkey"]),
                "alpha_amount": int(alpha_val),
                "destination_coldkey": str(row["destination_coldkey"]),
                "staker_hotkey": str(row["staker_hotkey"]),
            }
