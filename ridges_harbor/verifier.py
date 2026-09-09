from __future__ import annotations

from collections.abc import Callable
from typing import Any

from harbor.environments.base import BaseEnvironment
from harbor.models.task.task import Task
from harbor.models.task.verifier_mode import resolve_effective_verifier_env_config
from harbor.models.trial.paths import TrialPaths
from harbor.models.verifier.result import VerifierResult
from harbor.verifier.verifier import Verifier

RewardParser = Callable[[], dict[str, float | int]]


class _RewardUnreadableError(PermissionError):
    """A reward parser hit PermissionError; carries that parser so verify() can retry it."""

    def __init__(self, parse: RewardParser, cause: PermissionError) -> None:
        super().__init__(*cause.args)
        self.filename = cause.filename
        self.parse = parse


class RidgesVerifier(Verifier):
    def __init__(
        self,
        task: Task,
        trial_paths: TrialPaths,
        environment: BaseEnvironment,
        *,
        skip_tests_upload: bool | None = None,
        **kwargs: Any,
    ) -> None:
        if skip_tests_upload is None:
            skip_tests_upload = resolve_effective_verifier_env_config(task.config, None) is not None
        super().__init__(
            task=task,
            trial_paths=trial_paths,
            environment=environment,
            skip_tests_upload=skip_tests_upload,
            **kwargs,
        )

    async def verify(self) -> VerifierResult:
        try:
            return await super().verify()
        except _RewardUnreadableError as exc:
            self.logger.warning(f"Reward file is unreadable on the host ({exc}); fixing ownership and reading it again")
            await self.environment.prepare_logs_for_host()
            return VerifierResult(rewards=exc.parse())

    def _parse_reward_text(self) -> dict[str, float | int]:
        return _guard_reward_read(super()._parse_reward_text)

    def _parse_reward_json(self) -> dict[str, float | int]:
        return _guard_reward_read(super()._parse_reward_json)


def _guard_reward_read(parse: RewardParser) -> dict[str, float | int]:
    try:
        return parse()
    except PermissionError as exc:
        raise _RewardUnreadableError(parse, exc) from exc
