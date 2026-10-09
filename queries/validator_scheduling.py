from models.validator_scheduling import (
    CompetitionSchedulingSnapshot,
    CompetitionSchedulingUpdateRequest,
    ValidatorAllowlistSnapshot,
    ValidatorAllowlistUpdateRequest,
)
from queries.competition import _insert_competition_admin_event
from queries.errors import CompetitionNotFoundError
from utils.database import DatabaseConnection, db_operation


async def _lock_validator_cursor(conn: DatabaseConnection) -> None:
    row = await conn.fetchrow("SELECT family FROM competition_work_cursors WHERE family = 'validator' FOR UPDATE")
    if row is None:
        raise RuntimeError("Missing competition work cursor for validator")


@db_operation
async def get_competition_scheduling(conn: DatabaseConnection, *, set_id: int) -> CompetitionSchedulingSnapshot:
    mode = await conn.fetchval("SELECT validator_scheduling_mode FROM competitions WHERE set_id = $1", set_id)
    if mode is None:
        raise CompetitionNotFoundError(set_id)
    return CompetitionSchedulingSnapshot(set_id=set_id, mode=mode)


@db_operation
async def set_competition_scheduling(
    conn: DatabaseConnection, *, set_id: int, target: CompetitionSchedulingUpdateRequest, actor: str
) -> CompetitionSchedulingSnapshot:
    async with conn.conn.transaction():
        await _lock_validator_cursor(conn)
        mode = await conn.fetchval(
            "SELECT validator_scheduling_mode FROM competitions WHERE set_id = $1 FOR UPDATE", set_id
        )
        if mode is None:
            raise CompetitionNotFoundError(set_id)

        before = CompetitionSchedulingSnapshot(set_id=set_id, mode=mode)
        if mode == target.mode:
            return before

        await conn.execute(
            "UPDATE competitions SET validator_scheduling_mode = $2 WHERE set_id = $1", set_id, target.mode
        )
        after = CompetitionSchedulingSnapshot(set_id=set_id, mode=target.mode)
        await _insert_competition_admin_event(
            conn,
            operation="validator_scheduling",
            actor=actor,
            reason=target.reason,
            before_state=before.model_dump(mode="json"),
            after_state=after.model_dump(mode="json"),
        )
        return after


async def _get_validator_allowlist(conn: DatabaseConnection, *, validator_hotkey: str) -> ValidatorAllowlistSnapshot:
    row = await conn.fetchrow(
        """
        SELECT ARRAY(
            SELECT entry.set_id FROM validator_competition_allowlist_entries entry
            WHERE entry.validator_hotkey = allowlist.validator_hotkey ORDER BY entry.set_id
        ) AS allowed_set_ids
        FROM validator_competition_allowlists allowlist WHERE allowlist.validator_hotkey = $1
        """,
        validator_hotkey,
    )
    return ValidatorAllowlistSnapshot(
        validator_hotkey=validator_hotkey, allowed_set_ids=None if row is None else row["allowed_set_ids"]
    )


@db_operation
async def get_validator_allowlist(conn: DatabaseConnection, *, validator_hotkey: str) -> ValidatorAllowlistSnapshot:
    return await _get_validator_allowlist(conn, validator_hotkey=validator_hotkey)


@db_operation
async def set_validator_allowlist(
    conn: DatabaseConnection, *, validator_hotkey: str, target: ValidatorAllowlistUpdateRequest, actor: str
) -> ValidatorAllowlistSnapshot:
    async with conn.conn.transaction():
        await _lock_validator_cursor(conn)
        allowed = None if target.allowed_set_ids is None else sorted(target.allowed_set_ids)
        if allowed:
            rows = await conn.fetch(
                "SELECT set_id FROM competitions WHERE set_id = ANY($1::int[]) ORDER BY set_id FOR KEY SHARE", allowed
            )
            missing = set(allowed) - {row["set_id"] for row in rows}
            if missing:
                raise CompetitionNotFoundError(min(missing))

        before = await _get_validator_allowlist(conn, validator_hotkey=validator_hotkey)
        if before.allowed_set_ids == allowed:
            return before

        await conn.execute("DELETE FROM validator_competition_allowlists WHERE validator_hotkey = $1", validator_hotkey)
        if allowed is not None:
            await conn.execute(
                "INSERT INTO validator_competition_allowlists (validator_hotkey) VALUES ($1)", validator_hotkey
            )
            if allowed:
                await conn.executemany(
                    "INSERT INTO validator_competition_allowlist_entries (validator_hotkey, set_id) VALUES ($1, $2)",
                    [(validator_hotkey, set_id) for set_id in allowed],
                )

        after = ValidatorAllowlistSnapshot(validator_hotkey=validator_hotkey, allowed_set_ids=allowed)
        await _insert_competition_admin_event(
            conn,
            operation="validator_allowlist",
            actor=actor,
            reason=target.reason,
            before_state=before.model_dump(mode="json"),
            after_state=after.model_dump(mode="json"),
        )
        return after
