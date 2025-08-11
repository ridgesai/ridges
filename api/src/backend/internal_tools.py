from __future__ import annotations

import os
import atexit
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

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

    async def get_emission_alpha_for_hotkey(self, miner_hotkey: str, hours: float) -> float:
        query = (
            """
            SELECT COALESCE(SUM(es.emission_alpha)::double precision, 0.0) AS total_alpha
            FROM emission_snapshots AS es
            WHERE es.hotkey = $1
              AND es.occured_at >= NOW() - make_interval(secs => ($2::double precision) * 3600);
            """
        )

        async with self.acquire() as conn:
            value = await conn.fetchval(query, miner_hotkey, float(hours))
            return float(value or 0.0)
