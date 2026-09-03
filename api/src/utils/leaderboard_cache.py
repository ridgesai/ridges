"""One shared, cached fetch of a set's leaderboard rows.

`/evaluation-sets/{set_id}/leaderboard` and `/retrieval/agents-by-coldkey` both need
the same rows for a set, so they share one cache here rather than each holding their
own. Ended (and grandfathered) competitions cache for 24 hours because their
leaderboard can no longer change.

Live sets cache for 15 seconds. Short enough that an active
competition still reads as live, long enough to collapse bursts onto one query.
"""

import asyncpg

from queries.evaluation_set import get_evaluation_set_leaderboard_agents
from utils.ttl import ttl_cache

CACHE_LIVE_LEADERBOARD_TTL_SECONDS = 15
CACHE_PAST_LEADERBOARD_TTL_SECONDS = 24 * 60 * 60


async def _fetch_leaderboard_rows(set_id: int, required_validator_count: int | None) -> list[asyncpg.Record]:
    return await get_evaluation_set_leaderboard_agents(set_id, required_validator_count)


_cached_live_leaderboard_rows = ttl_cache(ttl_seconds=CACHE_LIVE_LEADERBOARD_TTL_SECONDS)(_fetch_leaderboard_rows)
_cached_past_leaderboard_rows = ttl_cache(ttl_seconds=CACHE_PAST_LEADERBOARD_TTL_SECONDS)(_fetch_leaderboard_rows)


async def get_cached_leaderboard_rows(
    set_id: int,
    required_validator_count: int | None,
    use_historical_cache: bool,
) -> list[asyncpg.Record]:
    """The set's leaderboard rows, cached for 24h once the competition can no longer change.

    Callers must treat the returned rows as read-only: every caller shares one cached list.
    """
    fetch = _cached_past_leaderboard_rows if use_historical_cache else _cached_live_leaderboard_rows
    return await fetch(set_id, required_validator_count)
