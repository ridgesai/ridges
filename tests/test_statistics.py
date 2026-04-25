from __future__ import annotations

from datetime import datetime, timezone

import pytest

from queries import statistics as statistics_module


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.query: str | None = None

    async def fetch(self, query: str):
        self.query = query
        return self.rows


@pytest.mark.anyio
async def test_get_perfectly_solved_over_time_returns_dynamic_family_counts() -> None:
    hour_1 = datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc)
    hour_2 = datetime(2026, 4, 20, 6, 0, tzinfo=timezone.utc)
    conn = _FakeConn(
        [
            {"hour": hour_1, "benchmark_family": None, "solved_count": 0},
            {"hour": hour_2, "benchmark_family": "scale-ai", "solved_count": 1},
            {"hour": hour_2, "benchmark_family": "swe-bench", "solved_count": 2},
        ]
    )

    result = await statistics_module.get_perfectly_solved_over_time.__wrapped__(conn)

    assert conn.query is not None
    assert "erh.benchmark_family <> 'custom'" in conn.query
    assert "GROUP BY erh.benchmark_family, erh.problem_name" in conn.query
    assert result == [
        statistics_module.PerfectlySolvedOverTime(hour=hour_1, total_solved=0, by_family={}),
        statistics_module.PerfectlySolvedOverTime(
            hour=hour_2,
            total_solved=3,
            by_family={"scale-ai": 1, "swe-bench": 2},
        ),
    ]
