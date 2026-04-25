from types import SimpleNamespace

import pytest

import ridges_harbor.docker_runtime as docker_runtime_module


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
