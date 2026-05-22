from datetime import datetime, timezone

import pytest

import api.endpoints.evaluation_sets as evaluation_sets_endpoint
from models.evaluation_set import EvaluationSet


@pytest.mark.anyio
async def test_evaluation_sets_list_returns_all_sets(monkeypatch):
    fake = [
        EvaluationSet(set_id=1, created_at=datetime(2026, 5, 1, tzinfo=timezone.utc)),
        EvaluationSet(set_id=2, created_at=datetime(2026, 5, 22, tzinfo=timezone.utc)),
    ]

    async def fake_get_all_evaluation_sets():
        return fake

    monkeypatch.setattr(evaluation_sets_endpoint, "get_all_evaluation_sets", fake_get_all_evaluation_sets)
    result = await evaluation_sets_endpoint.evaluation_sets_list()
    assert len(result) == 2
    assert result[0].id == 1
    assert result[1].id == 2


@pytest.mark.anyio
async def test_evaluation_sets_list_returns_empty_when_no_sets(monkeypatch):
    async def fake_get_all_evaluation_sets():
        return []

    monkeypatch.setattr(evaluation_sets_endpoint, "get_all_evaluation_sets", fake_get_all_evaluation_sets)
    result = await evaluation_sets_endpoint.evaluation_sets_list()
    assert result == []
