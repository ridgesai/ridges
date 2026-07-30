import json
import logging

from db.models.internal_flag import InternalFlagName
from utils.database import DatabaseConnection, db_operation

logger = logging.getLogger(__name__)

# Set of flags that are expected to have boolean values (stored as "true"/"false" strings in DB)
_BOOL_FLAGS: set[InternalFlagName] = {
    InternalFlagName.VALIDATORS_PAUSED,
}
# Set of flags that are expected to have list values (stored as JSON arrays in DB)
_LIST_FLAGS: set[InternalFlagName] = {
    InternalFlagName.BLACKLISTED_VALIDATORS,
}


def _parse_flag_value(flag: InternalFlagName, raw: str | None) -> bool | list[str] | str | None:
    """Parse a raw DB value into the flag's typed representation.

    Falls back to the flag's default (False / []) if raw is None or parsing fails.
    """
    if flag in _BOOL_FLAGS:
        # "true"/"false" strings in DB, parse to bool
        if raw is None:
            return False
        try:
            return raw.strip().lower() == "true"
        except Exception:
            logger.warning(f"Failed to parse boolean flag {flag} with raw value: {raw}")
            return False
    elif flag in _LIST_FLAGS:
        # JSON array of strings in DB ["screener-1-1"], parse to list[str]
        if raw is None:
            return []
        try:
            result = json.loads(raw)
            return result if isinstance(result, list) else []
        except Exception:
            logger.warning(f"Failed to parse list flag {flag} with raw value: {raw}")
            return []
    else:
        # Unknown flag type, return raw value
        return raw


# Advisory-lock namespace for internal-flag read-modify-write operations
INTERNAL_FLAG_LOCK_NAMESPACE = -1730


async def _lock_internal_flag(conn: DatabaseConnection, flag: InternalFlagName) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock($1, hashtext($2))",
        INTERNAL_FLAG_LOCK_NAMESPACE,
        flag.value,
    )


async def _upsert_internal_flag(conn: DatabaseConnection, flag: InternalFlagName, value: str) -> None:
    await conn.execute(
        """
        INSERT INTO internal_flags (name, value, updated_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (name) DO UPDATE
        SET value = EXCLUDED.value, updated_at = NOW()
        """,
        flag.value,
        value,
    )


@db_operation
async def set_internal_flag(conn: DatabaseConnection, flag: InternalFlagName, value: str) -> None:
    async with conn.conn.transaction():
        await _lock_internal_flag(conn, flag)
        await _upsert_internal_flag(conn, flag, value)


@db_operation
async def add_hotkey_to_blacklist(conn: DatabaseConnection, hotkey: str) -> list[str]:
    flag = InternalFlagName.BLACKLISTED_VALIDATORS
    async with conn.conn.transaction():
        await _lock_internal_flag(conn, flag)
        raw = await conn.fetchval("SELECT value FROM internal_flags WHERE name = $1", flag.value)
        blacklist = _parse_flag_value(flag, raw)
        if hotkey not in blacklist:
            blacklist.append(hotkey)
            await _upsert_internal_flag(conn, flag, json.dumps(blacklist))
    return blacklist


@db_operation
async def remove_hotkey_from_blacklist(conn: DatabaseConnection, hotkey: str) -> list[str]:
    flag = InternalFlagName.BLACKLISTED_VALIDATORS
    async with conn.conn.transaction():
        await _lock_internal_flag(conn, flag)
        raw = await conn.fetchval("SELECT value FROM internal_flags WHERE name = $1", flag.value)
        blacklist = _parse_flag_value(flag, raw)
        if hotkey in blacklist:
            blacklist.remove(hotkey)
            await _upsert_internal_flag(conn, flag, json.dumps(blacklist))
    return blacklist


@db_operation
async def get_internal_flags_parsed(
    conn: DatabaseConnection, flags: list[InternalFlagName]
) -> dict[InternalFlagName, bool | list[str] | str | None]:
    """Retrieve one or more Internal Flags by name and parse their
    values according to their expected type.

    Parameters
    ----------
    conn : DatabaseConnection
        DB connection to use for the query
    flags : list[InternalFlagName]
        List of flag names to retrieve and parse
    Returns
    -------
    dict[InternalFlagName, bool | list[str] | str | None]
        Mapping of flag name to parsed value (bool or list of strings depending on the flag)
    """
    rows = await conn.fetch(
        "SELECT name, value FROM internal_flags WHERE name = ANY($1)",
        [f.value for f in flags],
    )
    raw_by_name = {row["name"]: row["value"] for row in rows}
    return {flag: _parse_flag_value(flag, raw_by_name.get(flag.value)) for flag in flags}
