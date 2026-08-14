import asyncio
from types import SimpleNamespace

import pytest

from validator import background_loops


def _configure_cleanup(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(background_loops.config, "CLEANUP_TASK_CACHE_RETENTION_HOURS", 168)
    monkeypatch.setattr(background_loops.config, "CLEANUP_ARTIFACT_RETENTION_HOURS", 48)
    monkeypatch.setattr(background_loops.config, "RIDGES_HARBOR_RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(background_loops.config, "CLEANUP_DOCKER_ENABLED", True)
    monkeypatch.setattr(background_loops.config, "RIDGES_ENVIRONMENT_TYPE", "docker")
    monkeypatch.setattr(background_loops.config, "CLEANUP_DOCKER_DRY_RUN", False)
    monkeypatch.setattr(background_loops.config, "CLEANUP_STOPPED_GRACE_MINUTES", 45)
    monkeypatch.setattr(background_loops.config, "CLEANUP_RUNNING_TTL_HOURS", 4)
    monkeypatch.setattr(background_loops.config, "CLEANUP_IMAGE_TAG_GRACE_HOURS", 6)
    monkeypatch.setattr(background_loops.config, "CLEANUP_PULLED_IMAGE_DISK_PERCENT", 50)
    monkeypatch.setattr(background_loops.config, "CLEANUP_DISK_PRESSURE_PERCENT", 75)
    monkeypatch.setattr(background_loops.config, "CLEANUP_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(background_loops, "prune_task_cache", lambda **_kwargs: 0)
    monkeypatch.setattr(background_loops, "prune_dirs_older_than", lambda *_args: 0)

    async def stop_after_tick(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(background_loops.asyncio, "sleep", stop_after_tick)


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


@pytest.mark.anyio
async def test_cleanup_loop_passes_pressure_and_logs_summary(monkeypatch, tmp_path) -> None:
    _configure_cleanup(monkeypatch, tmp_path)
    seen_image_kwargs = {}
    messages = []

    def capture_info(message, *args):
        messages.append(message % args if args else message)

    async def metrics():
        return SimpleNamespace(disk_percent=82.0)

    def sweep_images(**kwargs):
        seen_image_kwargs.update(kwargs)
        return {"count": 1, "names": ["swebench/sweb.eval.example:latest"], "errors": 0}

    monkeypatch.setattr(background_loops, "get_system_metrics", metrics)
    monkeypatch.setattr(background_loops.logger, "info", capture_info)
    monkeypatch.setattr(
        background_loops,
        "sweep_stale_harbor_containers",
        lambda **_kwargs: {"count": 1, "names": ["stale_builder"], "errors": 0},
    )
    monkeypatch.setattr(background_loops, "sweep_leaked_harbor_images", sweep_images)
    monkeypatch.setattr(
        background_loops,
        "prune_docker_disk_resources",
        lambda **_kwargs: {"image_bytes": 123, "build_bytes": 0, "errors": 0},
    )
    monkeypatch.setattr(
        background_loops,
        "prune_caches_under_disk_pressure",
        lambda **_kwargs: {"fired": False, "image_bytes": 0, "build_bytes": 0, "errors": 0},
    )

    with pytest.raises(asyncio.CancelledError):
        await background_loops.cleanup_loop(set())

    assert seen_image_kwargs["disk_used_percent"] == 82.0
    assert seen_image_kwargs["pulled_image_pressure_percent"] == 50
    assert any(
        message.startswith("Janitor: containers=1 images=1 prune_bytes=123 disk_percent=82 errors=0 dry_run=false")
        for message in messages
    )


@pytest.mark.anyio
async def test_cleanup_loop_resamples_disk_before_pressure_prune(monkeypatch, tmp_path) -> None:
    """The sweeps can free space; a stale >=threshold sample must not wipe the build cache."""
    _configure_cleanup(monkeypatch, tmp_path)
    samples = [82.0, 60.0]
    seen = {}

    async def metrics():
        return SimpleNamespace(disk_percent=samples.pop(0))

    def sweep_images(**kwargs):
        seen["image_sweep_disk"] = kwargs["disk_used_percent"]
        return {"count": 0, "names": [], "errors": 0}

    def pressure_prune(**kwargs):
        seen["pressure_prune_disk"] = kwargs["disk_used_percent"]
        return {"fired": False, "image_bytes": 0, "build_bytes": 0, "errors": 0}

    monkeypatch.setattr(background_loops, "get_system_metrics", metrics)
    monkeypatch.setattr(
        background_loops,
        "sweep_stale_harbor_containers",
        lambda **_kwargs: {"count": 0, "names": [], "errors": 0},
    )
    monkeypatch.setattr(background_loops, "sweep_leaked_harbor_images", sweep_images)
    monkeypatch.setattr(
        background_loops,
        "prune_docker_disk_resources",
        lambda **_kwargs: {"image_bytes": 0, "build_bytes": 0, "errors": 0},
    )
    monkeypatch.setattr(background_loops, "prune_caches_under_disk_pressure", pressure_prune)

    with pytest.raises(asyncio.CancelledError):
        await background_loops.cleanup_loop(set())

    assert seen["image_sweep_disk"] == 82.0
    assert seen["pressure_prune_disk"] == 60.0


@pytest.mark.anyio
async def test_cleanup_loop_logs_zero_summary_when_sweeps_fail(monkeypatch, tmp_path) -> None:
    _configure_cleanup(monkeypatch, tmp_path)
    messages = []

    def capture_info(message, *args):
        messages.append(message % args if args else message)

    async def metrics():
        return SimpleNamespace(disk_percent=None)

    def fail(**_kwargs):
        raise RuntimeError("daemon unavailable")

    monkeypatch.setattr(background_loops, "get_system_metrics", metrics)
    monkeypatch.setattr(background_loops.logger, "info", capture_info)
    monkeypatch.setattr(background_loops, "sweep_stale_harbor_containers", fail)
    monkeypatch.setattr(background_loops, "sweep_leaked_harbor_images", fail)
    monkeypatch.setattr(background_loops, "prune_docker_disk_resources", fail)

    with pytest.raises(asyncio.CancelledError):
        await background_loops.cleanup_loop(set())

    assert (
        "Janitor: containers=0 images=0 prune_bytes=0 disk_percent=unknown errors=3 dry_run=false names=-" in messages
    )
