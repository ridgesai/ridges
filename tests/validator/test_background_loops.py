import asyncio

import pytest

from validator import background_loops


@pytest.mark.anyio
async def test_set_weights_loop_recovers_on_the_next_tick(monkeypatch) -> None:
    fetch_count = 0
    submitted: list[dict[str, float]] = []
    sleep_count = 0

    async def fetch_weights(_operation):
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 1:
            raise RuntimeError("platform unavailable")
        return {"hotkey-a": 0.6, "hotkey-b": 0.4}

    async def submit_weights(mapping):
        submitted.append(mapping)

    async def stop_after_second_tick(_seconds):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(background_loops, "retry_with_backoff", fetch_weights)
    monkeypatch.setattr(background_loops, "set_weights_from_mapping", submit_weights)
    monkeypatch.setattr(background_loops.asyncio, "sleep", stop_after_second_tick)

    with pytest.raises(asyncio.CancelledError):
        await background_loops.set_weights_loop()

    assert fetch_count == 2
    assert submitted == [{"hotkey-a": 0.6, "hotkey-b": 0.4}]
