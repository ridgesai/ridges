"""The live upload price of each competition.

One row per competition stores the price as of its last bump; readers decay it to now.
Only confirmed burns, settings changes and resets change the price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from utils.database import DatabaseConnection, db_operation
from utils.upload_pricing import DEFAULT_FLOOR_USD, PricingSettings, current_price, multiplier

_SELECT = """
    SELECT set_id,
           floor_usd::float8 AS floor_usd,
           target_per_hour::float8 AS target_per_hour,
           half_life_minutes::float8 AS half_life_minutes,
           price_usd::float8 AS price_usd,
           price_updated_at
    FROM competition_upload_prices
    WHERE set_id = $1
"""


@dataclass(frozen=True, slots=True)
class CompetitionPrice:
    set_id: int
    settings: PricingSettings
    price_usd: float
    price_updated_at: datetime
    as_of: datetime


def _from_row(row, now: datetime) -> CompetitionPrice:
    settings = PricingSettings(
        floor_usd=row["floor_usd"],
        target_per_hour=row["target_per_hour"],
        half_life_minutes=row["half_life_minutes"],
    )
    price = current_price(
        price_usd=row["price_usd"], price_updated_at=row["price_updated_at"], settings=settings, now=now
    )
    return CompetitionPrice(
        set_id=row["set_id"], settings=settings, price_usd=price, price_updated_at=row["price_updated_at"], as_of=now
    )


async def lock_competition_price(conn: DatabaseConnection, set_id: int) -> CompetitionPrice:
    """Lock the competition's price row, creating it with default settings on first use. Call inside a transaction."""
    await conn.execute(
        "INSERT INTO competition_upload_prices (set_id, price_usd, price_updated_at) "
        "VALUES ($1, $2::float8, clock_timestamp()) ON CONFLICT (set_id) DO NOTHING",
        set_id,
        DEFAULT_FLOOR_USD,
    )
    row = await conn.fetchrow(_SELECT + " FOR UPDATE", set_id)
    now = await conn.fetchval("SELECT clock_timestamp()")
    return _from_row(row, now)


async def apply_bump(conn: DatabaseConnection, set_id: int) -> None:
    """Raise the competition's price by one confirmed burn. Call inside a transaction."""
    locked = await lock_competition_price(conn, set_id)
    await conn.execute(
        "UPDATE competition_upload_prices SET price_usd = $2::float8, price_updated_at = $3 WHERE set_id = $1",
        set_id,
        locked.price_usd * multiplier(locked.settings),
        locked.as_of,
    )


@db_operation
async def get_competition_price(conn: DatabaseConnection, set_id: int) -> Optional[CompetitionPrice]:
    """Fetch current price. None when the competition does not exist."""
    if await conn.fetchval("SELECT 1 FROM competitions WHERE set_id = $1", set_id) is None:
        return None

    now = await conn.fetchval("SELECT clock_timestamp()")
    row = await conn.fetchrow(_SELECT, set_id)
    if row is None:
        settings = PricingSettings()
        return CompetitionPrice(
            set_id=set_id, settings=settings, price_usd=settings.floor_usd, price_updated_at=now, as_of=now
        )
    return _from_row(row, now)


@db_operation
async def update_competition_pricing(
    conn: DatabaseConnection, set_id: int, settings: PricingSettings
) -> Optional[CompetitionPrice]:
    """Save new settings by admin changes."""
    async with conn.conn.transaction():
        if await conn.fetchval("SELECT 1 FROM competitions WHERE set_id = $1", set_id) is None:
            return None

        locked = await lock_competition_price(conn, set_id)
        price = max(settings.floor_usd, locked.price_usd)
        await conn.execute(
            """
            UPDATE competition_upload_prices
            SET floor_usd = $2::float8,
                target_per_hour = $3::float8,
                half_life_minutes = $4::float8,
                price_usd = $5::float8,
                price_updated_at = $6
            WHERE set_id = $1
            """,
            set_id,
            settings.floor_usd,
            settings.target_per_hour,
            settings.half_life_minutes,
            price,
            locked.as_of,
        )
        return CompetitionPrice(
            set_id=set_id, settings=settings, price_usd=price, price_updated_at=locked.as_of, as_of=locked.as_of
        )
