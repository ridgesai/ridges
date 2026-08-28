"""Separate-mode verifier: apply the miner patch, then run Harbor's stock tests."""

from __future__ import annotations

import logging
import shlex
from typing import Any

from harbor.environments.base import BaseEnvironment
from harbor.models.task.task import Task
from harbor.models.trial.paths import EnvironmentPaths, TrialPaths
from harbor.models.verifier.result import VerifierResult
from harbor.verifier.verifier import Verifier

from ridges_harbor._stdlib_contract import GRADED_PATCH_FILENAME, PATCH_FILENAME


class RidgesVerifier(Verifier):
    """Apply `/logs/agent/patch.diff` before Harbor runs `/tests/test.sh`.

    Only registered on separate-mode jobs. Harbor 0.20's VerifierFactory does not
    forward the call-site ``skip_tests_upload=True`` into ``import_path`` classes,
    so this subclass always enables skip (tests are baked into the verifier image).
    """

    def __init__(
        self,
        task: Task,
        trial_paths: TrialPaths,
        environment: BaseEnvironment,
        override_env: dict[str, str] | None = None,
        logger: logging.Logger | None = None,
        skip_tests_upload: bool = True,
        verifier_env: dict[str, str] | None = None,
        step_name: str | None = None,
        include_logs: list[str] | None = None,
        exclude_logs: list[str] | None = None,
        **_: Any,
    ):
        super().__init__(
            task=task,
            trial_paths=trial_paths,
            environment=environment,
            override_env=override_env,
            logger=logger,
            skip_tests_upload=True,
            verifier_env=verifier_env,
            step_name=step_name,
            include_logs=include_logs,
            exclude_logs=exclude_logs,
        )

    async def verify(self) -> VerifierResult:
        patch_path = (EnvironmentPaths.agent_dir / PATCH_FILENAME).as_posix()
        graded_path = (EnvironmentPaths.verifier_dir / GRADED_PATCH_FILENAME).as_posix()
        workdir = self.environment.task_env_config.workdir
        quoted_patch = shlex.quote(patch_path)
        quoted_graded = shlex.quote(graded_path)

        check_result = await self.environment.exec(
            command=f"git apply --check {quoted_patch}",
            cwd=workdir,
            user="root",
        )
        if check_result.return_code != 0:
            raise RuntimeError(
                "Separate verifier rejected miner patch during git apply --check: "
                f"stdout={check_result.stdout!r} stderr={check_result.stderr!r}"
            )

        apply_result = await self.environment.exec(
            command=f"git apply {quoted_patch}",
            cwd=workdir,
            user="root",
        )
        if apply_result.return_code != 0:
            raise RuntimeError(
                "Separate verifier failed to apply miner patch: "
                f"stdout={apply_result.stdout!r} stderr={apply_result.stderr!r}"
            )

        copy_result = await self.environment.exec(
            command=(
                f"mkdir -p {shlex.quote(EnvironmentPaths.verifier_dir.as_posix())} && cp {quoted_patch} {quoted_graded}"
            ),
            cwd="/",
            user="root",
        )
        if copy_result.return_code != 0:
            raise RuntimeError(
                "Separate verifier failed to write graded.patch: "
                f"stdout={copy_result.stdout!r} stderr={copy_result.stderr!r}"
            )

        return await super().verify()
