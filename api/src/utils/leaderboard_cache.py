"""One shared, cached fetch of a set's leaderboard rows.

`/evaluation-sets/{set_id}/leaderboard` and `/retrieval/agents-by-coldkey` both need
the same rows for a set, so they share one cache here rather than each holding their
own. Ended (and grandfathered) competitions cache for 24 hours because their
leaderboard can no longer change.

Live sets cache for 15 seconds. Short enough that an active
competition still reads as live, long enough to collapse bursts onto one query.

Filtering, search and sorting run in memory over the cached rows: rank is a window over
the whole competition, so the SQL has to compute every row regardless of the page asked for.
"""

from operator import itemgetter
from typing import Any, Callable

import asyncpg

from models.agent import AgentCompetitionStatus, derive_agent_competition_status
from models.leaderboard import LeaderboardQuery, LeaderboardSortField
from queries.evaluation_set import get_evaluation_set_leaderboard_agents
from utils.ttl import ttl_cache
from utils.validator_hotkeys import validator_name_to_hotkey

CACHE_LIVE_LEADERBOARD_TTL_SECONDS = 15
CACHE_PAST_LEADERBOARD_TTL_SECONDS = 24 * 60 * 60

_APPROVED_STATUSES = {AgentCompetitionStatus.approved, AgentCompetitionStatus.baseline}

# Pipeline progression first, then the outcomes of a finished agent
_STATUS_SORT_PRIORITY = {
    status: priority
    for priority, status in enumerate(
        [
            AgentCompetitionStatus.pre_screening,
            AgentCompetitionStatus.failed_pre_screening,
            AgentCompetitionStatus.pre_screening_needs_review,
            AgentCompetitionStatus.screening_1,
            AgentCompetitionStatus.failed_screening_1,
            AgentCompetitionStatus.screening_2,
            AgentCompetitionStatus.failed_screening_2,
            AgentCompetitionStatus.evaluating,
            AgentCompetitionStatus.finished,
            AgentCompetitionStatus.cancelled,
            AgentCompetitionStatus.rejected,
            AgentCompetitionStatus.didnt_qualify,
            AgentCompetitionStatus.under_review,
            AgentCompetitionStatus.baseline,
            AgentCompetitionStatus.approved,
        ]
    )
}

_SORT_KEYS: dict[LeaderboardSortField, Callable[[asyncpg.Record, AgentCompetitionStatus], Any]] = {
    LeaderboardSortField.rank: lambda row, _: row["rank"],
    LeaderboardSortField.name: lambda row, _: (row["name"].casefold(), row["version_num"]),
    LeaderboardSortField.score: lambda row, _: row["final_score"],
    LeaderboardSortField.cost: lambda row, _: row["average_cost_usd"],
    LeaderboardSortField.runtime: lambda row, _: row["average_runtime_seconds"],
    LeaderboardSortField.status: lambda _, status: _STATUS_SORT_PRIORITY[status],
    LeaderboardSortField.submitted_at: lambda row, _: row["created_at"],
}


async def _fetch_leaderboard_rows(set_id: int, required_validator_count: int | None) -> list[asyncpg.Record]:
    return await get_evaluation_set_leaderboard_agents(set_id, required_validator_count)


_cached_live_leaderboard_rows = ttl_cache(ttl_seconds=CACHE_LIVE_LEADERBOARD_TTL_SECONDS)(_fetch_leaderboard_rows)
_cached_past_leaderboard_rows = ttl_cache(ttl_seconds=CACHE_PAST_LEADERBOARD_TTL_SECONDS)(_fetch_leaderboard_rows)


def _apply_query(rows: list[asyncpg.Record], set_id: int, query: LeaderboardQuery) -> list[asyncpg.Record]:
    """Filter and sort into a new list. Nulls sort last in both directions; ties keep the cached rank order."""
    search = (query.search or "").strip().casefold()
    statuses = set(query.status)
    validator_hotkeys = {validator_name_to_hotkey(validator.value) for validator in query.validator}

    matching: list[tuple[asyncpg.Record, AgentCompetitionStatus]] = []
    for row in rows:
        status = derive_agent_competition_status(
            status=row["status"],
            set_id=set_id,
            approved=row["approved"] is True,
            approval_review_status=row["approval_review_status"],
            performance_delta=row["performance_delta"],
            cost_delta=row["cost_delta"],
            relative_improvement_units=row["relative_improvement_units"],
            approved_at=row["approved_at"],
            baseline_agent_id=row["baseline_agent_id"],
        )
        if query.ranked and row["rank"] is None:
            continue
        if query.approved and status not in _APPROVED_STATUSES:
            continue
        if statuses and status not in statuses:
            continue
        if validator_hotkeys and validator_hotkeys.isdisjoint(row["validator_hotkeys"] or ()):
            continue
        if search and search not in row["name"].casefold():
            continue
        matching.append((row, status))

    sort_key = _SORT_KEYS[query.sort_field]
    keyed = [(sort_key(row, status), row) for row, status in matching]
    present = [(key, row) for key, row in keyed if key is not None]
    missing = [row for key, row in keyed if key is None]
    # sorted() is stable with reverse=True too, so equal keys stay in cached order
    present.sort(key=itemgetter(0), reverse=query.sort_descending)
    return [row for _, row in present] + missing


async def get_cached_leaderboard_rows(
    set_id: int,
    required_validator_count: int | None,
    use_historical_cache: bool,
    query: LeaderboardQuery | None = None,
) -> list[asyncpg.Record]:
    """The set's leaderboard rows, cached for 24h once the competition can no longer change.

    Without a `query`, returns the shared cached list in rank order; callers must treat it as
    read-only. With a `query`, returns a new list filtered and sorted by it.
    """
    fetch = _cached_past_leaderboard_rows if use_historical_cache else _cached_live_leaderboard_rows
    rows = await fetch(set_id, required_validator_count)
    if query is None:
        return rows
    return _apply_query(rows, set_id, query)
