import asyncio
from types import SimpleNamespace

import pytest

from ridges_harbor.docker_runtime import DockerStartGate
from validator import background_loops


def _configure_cleanup(monkeypatch, tmp_path) -> DockerStartGate:
    gate = DockerStartGate()
    monkeypatch.setattr(background_loops, "docker_start_gate", gate)
    monkeypatch.setattr(background_loops.config, "CLEANUP_TASK_CACHE_RETENTION_HOURS", 168)
    monkeypatch.setattr(background_loops.config, "CLEANUP_ARTIFACT_RETENTION_HOURS", 48)
    monkeypatch.setattr(background_loops.config, "RIDGES_HARBOR_RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(background_loops.config, "CLEANUP_DOCKER_ENABLED", True)
    monkeypatch.setattr(background_loops.config, "RIDGES_ENVIRONMENT_TYPE", "docker")
    monkeypatch.setattr(background_loops.config, "CLEANUP_DOCKER_DRY_RUN", False)
    monkeypatch.setattr(background_loops.config, "CLEANUP_STOPPED_GRACE_MINUTES", 45)
    monkeypatch.setattr(background_loops.config, "CLEANUP_RUNNING_TTL_HOURS", 4)
    monkeypatch.setattr(background_loops.config, "CLEANUP_IMAGE_TAG_GRACE_HOURS", 6)
    monkeypatch.setattr(background_loops.config, "CLEANUP_BUILD_CACHE_RETENTION_HOURS", 48)
    monkeypatch.setattr(background_loops.config, "CLEANUP_DISK_PRESSURE_PERCENT", 75)
    monkeypatch.setattr(background_loops.config, "CLEANUP_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(background_loops, "prune_task_cache", lambda **_kwargs: 0)
    monkeypatch.setattr(background_loops, "prune_dirs_older_than", lambda *_args: 0)
    monkeypatch.setattr(
        background_loops, "prune_build_cache_older_than", lambda **_kwargs: {"build_bytes": 0, "errors": 0}
    )

    async def stop_after_tick(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(background_loops.asyncio, "sleep", stop_after_tick)
    return gate


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
async def test_cleanup_loop_ages_out_build_cache_and_logs_summary(monkeypatch, tmp_path) -> None:
    _configure_cleanup(monkeypatch, tmp_path)
    seen_image_kwargs = {}
    seen_build_cache_kwargs = {}
    messages = []

    def capture_info(message, *args):
        messages.append(message % args if args else message)

    async def metrics():
        return SimpleNamespace(disk_percent=82.0)

    def sweep_images(**kwargs):
        seen_image_kwargs.update(kwargs)
        return {"count": 1, "names": ["hb__example:latest"], "errors": 0}

    def prune_build_cache(**kwargs):
        seen_build_cache_kwargs.update(kwargs)
        return {"build_bytes": 789, "errors": 0}

    monkeypatch.setattr(background_loops, "get_system_metrics", metrics)
    monkeypatch.setattr(background_loops.logger, "info", capture_info)
    monkeypatch.setattr(background_loops, "prune_build_cache_older_than", prune_build_cache)
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

    assert seen_image_kwargs == {"tag_grace_sec": 6 * 3600, "dry_run": False}
    assert seen_build_cache_kwargs == {"hours": 48, "dry_run": False}
    assert any(
        message.startswith(
            "Janitor: containers=1 images=1 prune_bytes=123 build_cache_bytes=789 disk_percent=82 errors=0 dry_run=false"
        )
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
        "Janitor: containers=0 images=0 prune_bytes=0 build_cache_bytes=0 disk_percent=unknown errors=3 "
        "dry_run=false names=-" in messages
    )


def _patch_docker_sweeps(monkeypatch, *, disk_samples, seen, gate=None) -> None:
    async def metrics():
        return SimpleNamespace(disk_percent=disk_samples.pop(0))

    def sweep_images(**kwargs):
        seen["image_sweep"] = True
        return {"count": 0, "names": [], "errors": 0}

    def pressure_prune(**kwargs):
        seen["pressure_prune_disk"] = kwargs["disk_used_percent"]
        seen["pressure_prune_active_digests"] = kwargs["active_task_digests"]
        seen["pressure_prune_exclusive"] = gate._exclusive if gate is not None else None
        return {"fired": True, "image_bytes": 0, "build_bytes": 0, "errors": 0}

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


@pytest.mark.anyio
async def test_cleanup_loop_evicts_only_while_holding_the_start_gate(monkeypatch, tmp_path) -> None:
    gate = _configure_cleanup(monkeypatch, tmp_path)
    seen = {}
    _patch_docker_sweeps(monkeypatch, disk_samples=[82.0, 80.0], seen=seen, gate=gate)

    with pytest.raises(asyncio.CancelledError):
        await background_loops.cleanup_loop({"sha256:running"})

    assert seen == {
        "image_sweep": True,
        "pressure_prune_disk": 80.0,
        "pressure_prune_active_digests": frozenset({"sha256:running"}),
        "pressure_prune_exclusive": True,
    }
    assert not gate._exclusive


@pytest.mark.anyio
async def test_cleanup_loop_skips_eviction_when_starts_do_not_drain(monkeypatch, tmp_path) -> None:
    gate = _configure_cleanup(monkeypatch, tmp_path)
    monkeypatch.setattr(background_loops, "START_DRAIN_TIMEOUT_SECONDS", 0.05)
    seen = {}
    warnings = []
    _patch_docker_sweeps(monkeypatch, disk_samples=[82.0, 80.0], seen=seen)
    monkeypatch.setattr(background_loops.logger, "warning", warnings.append)

    entered = asyncio.Event()
    release = asyncio.Event()

    async def in_flight_start():
        async with gate.start():
            entered.set()
            await release.wait()

    start_task = asyncio.create_task(in_flight_start())
    await entered.wait()

    with pytest.raises(asyncio.CancelledError):
        await background_loops.cleanup_loop(set())

    assert seen == {"image_sweep": True}
    assert any("did not drain" in message for message in warnings)
    async with asyncio.timeout(1):
        async with gate.start():
            pass
    release.set()
    await start_task


@pytest.mark.anyio
async def test_cleanup_loop_never_takes_the_gate_below_the_pressure_threshold(monkeypatch, tmp_path) -> None:
    """A resample that crosses the threshold mid-sweep must not prune without the gate."""
    gate = _configure_cleanup(monkeypatch, tmp_path)
    seen = {}
    _patch_docker_sweeps(monkeypatch, disk_samples=[60.0, 90.0], seen=seen)
    gate_entries = []
    original_exclusive = gate.exclusive
    monkeypatch.setattr(gate, "exclusive", lambda **kwargs: gate_entries.append(kwargs) or original_exclusive(**kwargs))

    with pytest.raises(asyncio.CancelledError):
        await background_loops.cleanup_loop(set())

    assert gate_entries == []
    assert seen == {"image_sweep": True}
