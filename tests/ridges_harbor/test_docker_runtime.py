import asyncio
from types import SimpleNamespace

import pytest

import ridges_harbor.docker_runtime as docker_runtime_module


def test_docker_environment_env_includes_inference_seed() -> None:
    env = docker_runtime_module.docker_environment_env(
        ridges_trial_id="trial-1",
        evaluation_run_id="eval-run-1",
        max_cost_usd="9",
        proxy_data_dir="/tmp/proxy",
        openrouter_config=None,
        inference_seed=123,
    )

    assert env["RIDGES_INFERENCE_SEED"] == "123"


def test_docker_environment_env_defaults_inference_seed_to_empty_string() -> None:
    env = docker_runtime_module.docker_environment_env(
        ridges_trial_id="trial-1",
        evaluation_run_id="eval-run-1",
        max_cost_usd="9",
        proxy_data_dir="/tmp/proxy",
        openrouter_config=None,
    )

    assert env["RIDGES_INFERENCE_SEED"] == ""


def test_resolve_single_match_requires_exactly_one_result() -> None:
    assert (
        docker_runtime_module._resolve_single_match(
            "abc\n",
            resource="container",
            trial_id="trial-1",
        )
        == "abc"
    )

    with pytest.raises(RuntimeError) as no_match:
        docker_runtime_module._resolve_single_match(
            "",
            resource="container",
            trial_id="trial-1",
        )
    assert "found 0" in str(no_match.value)

    with pytest.raises(RuntimeError) as too_many:
        docker_runtime_module._resolve_single_match(
            "abc\ndef\n",
            resource="container",
            trial_id="trial-1",
        )
    assert "found 2" in str(too_many.value)


@pytest.mark.anyio
async def test_enable_verifier_egress_hook_prefers_ridges_labels(monkeypatch) -> None:
    commands: list[list[str]] = []

    async def fake_run_host_command(args: list[str], *, check: bool = True) -> tuple[int, str]:
        commands.append(args)
        if args[:3] == ["docker", "ps", "-q"]:
            assert "label=ridges.trial_id=ridges-trial-1" in args
            return 0, "container-1\n"
        if args[:4] == ["docker", "network", "ls", "-q"]:
            assert "label=ridges.trial_id=ridges-trial-1" in args
            return 0, "network-1\n"
        if args[:3] == ["docker", "network", "connect"]:
            assert args[3:] == ["network-1", "container-1"]
            assert check is False
            return 0, ""
        raise AssertionError(f"Unexpected Docker command: {args}")

    monkeypatch.setattr(docker_runtime_module, "_run_host_command", fake_run_host_command)

    enable_verifier_egress = docker_runtime_module.build_enable_verifier_egress_hook(ridges_trial_id="ridges-trial-1")
    event = SimpleNamespace(trial_id="trial-1")
    await enable_verifier_egress(event)

    assert commands[-1] == ["docker", "network", "connect", "network-1", "container-1"]


@pytest.mark.anyio
async def test_enable_verifier_egress_hook_requires_labeled_scaffold(monkeypatch) -> None:
    async def fake_run_host_command(args: list[str], *, check: bool = True) -> tuple[int, str]:
        if args[:3] == ["docker", "ps", "-q"]:
            return 0, "container-1\n"
        if args[:4] == ["docker", "network", "ls", "-q"]:
            return 0, ""
        raise AssertionError(f"Unexpected Docker command: {args}")

    monkeypatch.setattr(docker_runtime_module, "_run_host_command", fake_run_host_command)

    enable_verifier_egress = docker_runtime_module.build_enable_verifier_egress_hook(ridges_trial_id="ridges-trial-1")
    with pytest.raises(RuntimeError) as exc_info:
        await enable_verifier_egress(SimpleNamespace(trial_id="trial-1"))

    assert "sandbox_egress network labeled ridges.trial_id=ridges-trial-1" in str(exc_info.value)


@pytest.mark.anyio
async def test_enable_verifier_egress_hook_raises_dedicated_error_on_connect_failure(
    monkeypatch,
) -> None:
    async def fake_run_host_command(args: list[str], *, check: bool = True) -> tuple[int, str]:
        if args[:3] == ["docker", "ps", "-q"]:
            return 0, "container-1\n"
        if args[:4] == ["docker", "network", "ls", "-q"]:
            return 0, "network-1\n"
        if args[:3] == ["docker", "network", "connect"]:
            return 1, "permission denied"
        raise AssertionError(f"Unexpected Docker command: {args}")

    monkeypatch.setattr(docker_runtime_module, "_run_host_command", fake_run_host_command)

    enable_verifier_egress = docker_runtime_module.build_enable_verifier_egress_hook(ridges_trial_id="ridges-trial-1")
    event = SimpleNamespace(trial_id="trial-1")

    with pytest.raises(docker_runtime_module.VerifierEgressSetupError) as exc_info:
        await enable_verifier_egress(event)

    assert "Failed to connect verifier egress" in str(exc_info.value)


@pytest.mark.anyio
async def test_start_gate_exclusive_waits_for_in_flight_starts_and_holds_new_ones() -> None:
    gate = docker_runtime_module.DockerStartGate()
    events = []
    release_first = asyncio.Event()
    first_entered = asyncio.Event()

    async def first_start():
        async with gate.start():
            first_entered.set()
            await release_first.wait()
            events.append("first start done")

    async def second_start():
        async with gate.start():
            events.append("second start entered")

    async def evict():
        async with gate.exclusive(drain_timeout_sec=5) as drained:
            events.append(f"evicting drained={drained}")
            second_task = asyncio.create_task(second_start())
            await asyncio.sleep(0.01)
            events.append("eviction done")
        await second_task

    first_task = asyncio.create_task(first_start())
    await first_entered.wait()
    evict_task = asyncio.create_task(evict())
    await asyncio.sleep(0.01)
    assert events == []

    release_first.set()
    await asyncio.gather(first_task, evict_task)

    assert events == ["first start done", "evicting drained=True", "eviction done", "second start entered"]


@pytest.mark.anyio
async def test_start_gate_releases_on_drain_timeout() -> None:
    gate = docker_runtime_module.DockerStartGate()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def stuck_start():
        async with gate.start():
            entered.set()
            await release.wait()

    stuck_task = asyncio.create_task(stuck_start())
    await entered.wait()

    async with gate.exclusive(drain_timeout_sec=0.01) as drained:
        assert drained is False
        async with asyncio.timeout(1):
            async with gate.start():
                pass

    release.set()
    await stuck_task


@pytest.mark.anyio
async def test_start_gate_counts_a_failed_start_as_finished() -> None:
    gate = docker_runtime_module.DockerStartGate()

    with pytest.raises(RuntimeError):
        async with gate.start():
            raise RuntimeError("compose up failed")

    async with gate.exclusive(drain_timeout_sec=0.01) as drained:
        assert drained is True


@pytest.mark.anyio
async def test_ridges_docker_environment_starts_inside_the_gate(monkeypatch) -> None:
    gate = docker_runtime_module.DockerStartGate()
    monkeypatch.setattr(docker_runtime_module, "docker_start_gate", gate)
    seen = []

    async def harbor_start(_self, force_build):
        seen.append((force_build, gate._active_starts))

    monkeypatch.setattr(docker_runtime_module.DockerEnvironment, "start", harbor_start)
    environment = docker_runtime_module.RidgesDockerEnvironment.__new__(docker_runtime_module.RidgesDockerEnvironment)

    await environment.start(force_build=False)

    assert seen == [(False, 1)]
    assert gate._active_starts == 0
