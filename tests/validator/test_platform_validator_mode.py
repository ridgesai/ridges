from __future__ import annotations

import asyncio

import pytest

import validator.main as validator_main
from tests.validator.test_docker_plugin_startup import StopAtIntake, _fake_startup


@pytest.mark.anyio
async def test_platform_validator_mode_registers_with_password_and_never_sets_weights(monkeypatch):
    events: list[str] = []
    _fake_startup(monkeypatch, environment="kubernetes", simulate=False, events=events)
    monkeypatch.setattr(validator_main.config, "MODE", "platform")
    monkeypatch.setattr(validator_main.config, "PLATFORM_VALIDATOR_NAME", "platform-validator-1", raising=False)
    monkeypatch.setattr(validator_main.config, "PLATFORM_VALIDATOR_PASSWORD", "platform-password", raising=False)
    registrations = []

    async def post(path: str, *args, **kwargs):
        if path == "/validator/register-as-platform-validator":
            registrations.append((args[0], kwargs.get("quiet", 0)))
            return {
                "session_id": "00000000-0000-0000-0000-000000000001",
                "running_agent_timeout_seconds": 1,
                "running_eval_timeout_seconds": 1,
                "max_evaluation_run_log_size_bytes": 1,
            }
        if path == "/validator/request-evaluation":
            raise StopAtIntake
        raise AssertionError(f"unexpected platform call: {path}")

    def set_weights_loop():
        events.append("set_weights")
        return asyncio.sleep(0)

    monkeypatch.setattr(validator_main, "post_ridges_platform", post)
    monkeypatch.setattr(validator_main, "set_weights_loop", set_weights_loop)

    with pytest.raises(StopAtIntake):
        await validator_main.main()

    # quiet >= 1 keeps the request body, and so the password, out of debug logs
    assert [(r.name, r.password, quiet) for r, quiet in registrations] == [
        ("platform-validator-1", "platform-password", 1)
    ]
    assert "set_weights" not in events
