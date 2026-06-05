"""Harbor agent adapter that wraps a Ridges 'agent_main(input) -> patch' miner.

See ridges_harbor/README.md for the host/container flow and file map.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

from ridges_harbor._stdlib_contract import (
    INSTRUCTION_FILENAME,
    PATCH_APPLY_LOG_FILENAME,
    PATCH_CHECK_LOG_FILENAME,
    PATCH_FILENAME,
    RUN_LOG_FILENAME,
    RUNTIME_FILENAME,
    RUNTIME_LOG_FILENAME,
    RUNTIME_PAYLOAD_FILENAME,
    SETUP_LOG_FILENAME,
)
from ridges_harbor.runtime_contract import (
    MinerInvalidPatchError,
    MinerPatchApplyError,
    MinerRuntimeError,
)

if TYPE_CHECKING:
    from harbor.environments.base import BaseEnvironment, ExecResult
    from harbor.models.task.config import MCPServerConfig

RUNTIME_BOOTSTRAP_PROBE_LOG_FILENAME = "runtime-bootstrap-probe.log"
STDLIB_CONTRACT_FILENAME = "_stdlib_contract.py"


class RidgesMinerAgent(BaseInstalledAgent):
    """Harbor agent wrapper for a 'agent_main' miner."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        logger: logging.Logger | None = None,
        mcp_servers: list[MCPServerConfig] | None = None,
        skills_dir: str | None = None,
        *,
        agent_path: str | Path,
        extra_env: dict[str, str] | None = None,
        workdir: str | None = None,
        runtime_dir: str = "/installed-agent",
        **kwargs,
    ):
        super().__init__(
            logs_dir=logs_dir,
            model_name=model_name,
            logger=logger,
            mcp_servers=mcp_servers,
            skills_dir=skills_dir,
            extra_env=extra_env,
            version="0.2.0",
            **kwargs,
        )
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.agent_path = Path(agent_path).expanduser().resolve()
        self.workdir = workdir
        self.runtime_dir = runtime_dir.rstrip("/")
        self.runtime_script_path = Path(__file__).with_name(RUNTIME_FILENAME)
        self.stdlib_contract_path = Path(__file__).with_name(STDLIB_CONTRACT_FILENAME)

    @staticmethod
    def name() -> str:
        return "ridges-miner"

    @property
    def _env_agent_path(self) -> str:
        # The miner source is uploaded into the install/runtime directory,
        # not Harbor's /logs/agent artifact directory.
        return f"{self.runtime_dir}/agent.py"

    @property
    def _env_runtime_path(self) -> str:
        return f"{self.runtime_dir}/{RUNTIME_FILENAME}"

    @property
    def _env_stdlib_contract_path(self) -> str:
        return f"{self.runtime_dir}/{STDLIB_CONTRACT_FILENAME}"

    @property
    def _env_instruction_path(self) -> str:
        return f"{self.runtime_dir}/{INSTRUCTION_FILENAME}"

    @property
    def _env_patch_path(self) -> str:
        return (EnvironmentPaths.agent_dir / PATCH_FILENAME).as_posix()

    @property
    def _env_runtime_payload_path(self) -> str:
        return (EnvironmentPaths.agent_dir / RUNTIME_PAYLOAD_FILENAME).as_posix()

    @property
    def _env_runtime_log_path(self) -> str:
        return (EnvironmentPaths.agent_dir / RUNTIME_LOG_FILENAME).as_posix()

    def _write_log(self, filename: str, content: str) -> None:
        """Write 'content' to 'logs_dir/filename', truncating any prior file."""
        (self.logs_dir / filename).write_text(content)

    def _append_log(self, filename: str, content: str) -> None:
        """Append 'content' to 'logs_dir/filename'."""
        with (self.logs_dir / filename).open("a") as handle:
            handle.write(content)

    @staticmethod
    def _format_exec_log_start(command: str) -> str:
        """Render the per-command header written before the executor runs."""
        return f"$ {command}\n[state] started\n"

    @staticmethod
    def _format_exec_log_result(
        result: "ExecResult",
        *,
        include_output_body: bool,
    ) -> str:
        """Render the per-command tail; includes [stdout]/[stderr] when requested."""
        stdout = result.stdout or ""
        stderr = result.stderr or ""

        rendered = f"[return_code] {result.return_code}\n"
        if include_output_body:
            rendered += f"[stdout]\n{stdout}\n[stderr]\n{stderr}\n"

        return rendered

    async def _bootstrap_runtime_dependencies(self, environment: "BaseEnvironment") -> None:
        """Verify 'python3' is available before uploading the runtime script."""
        await self._exec_with_log(
            environment,
            executor=self.exec_as_root,
            command="python3 -c " + shlex.quote("print('ok')"),
            log_filename=RUNTIME_BOOTSTRAP_PROBE_LOG_FILENAME,
            cancelled_detail="command execution was cancelled",
            error_summary="Unsupported task environment: python3 is required to run the Ridges miner runtime",
            error_type=RuntimeError,
        )

    async def _exec_with_log(
        self,
        environment: "BaseEnvironment",
        *,
        executor: Callable[..., Awaitable["ExecResult"]],
        command: str,
        log_filename: str,
        cancelled_detail: str,
        cwd: str | None = None,
        error_summary: str | None = None,
        error_type: type[Exception] | None = None,
        include_output_body: bool = True,
    ) -> "ExecResult":
        """Run a command via Harbor and record its framing + output to a log file.

        Args:
            cancelled_detail: Human-readable detail recorded under
                '[state] cancelled' when the executor is cancelled.
            error_summary: Optional prefix used when wrapping executor
                failures into 'error_type'.
            error_type: When set, executor exceptions are translated to this
                class (chained via 'from'); otherwise they propagate unchanged.
            include_output_body: When True, the post-run log entry includes
                full '[stdout]' / '[stderr]' sections.

        Raises:
            asyncio.CancelledError: Re-raised after writing a cancelled marker.
        """
        self._write_log(log_filename, self._format_exec_log_start(command))
        try:
            result = await executor(environment, command=command, cwd=cwd)
        except asyncio.CancelledError:
            self._append_log(
                log_filename,
                f"[state] cancelled\n[detail] {cancelled_detail}\n",
            )
            raise

        except Exception as exception:
            self._append_log(log_filename, f"[exception]\n{exception}\n")
            if error_type is not None:
                summary = error_summary or error_type.__name__
                raise error_type(f"{summary}:\n{exception}") from exception
            raise

        self._append_log(
            log_filename,
            self._format_exec_log_result(result, include_output_body=include_output_body),
        )
        return result

    async def install(self, environment: "BaseEnvironment") -> None:
        """Upload the miner and compatibility runtime into the environment.

        Called once by Harbor during 'BaseInstalledAgent.setup()'. Verifies
        the miner exists, ensures the install/log dirs are writable, probes
        python3, then uploads 'agent.py', the runtime script, and its stdlib
        contract sibling.
        """
        if not self.agent_path.exists():
            raise FileNotFoundError(f"Miner agent file not found: {self.agent_path}")

        await self._exec_with_log(
            environment,
            executor=self.exec_as_root,
            command=f"mkdir -p {shlex.quote(EnvironmentPaths.agent_dir.as_posix())} {shlex.quote(self.runtime_dir)}",
            log_filename=SETUP_LOG_FILENAME,
            cancelled_detail="command execution was cancelled",
        )

        await self._bootstrap_runtime_dependencies(environment)
        await environment.upload_file(self.agent_path, self._env_agent_path)
        await environment.upload_file(self.stdlib_contract_path, self._env_stdlib_contract_path)
        await environment.upload_file(self.runtime_script_path, self._env_runtime_path)

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: "BaseEnvironment",
        context: AgentContext,
    ) -> None:
        """Run the miner and apply the patch it writes.

        Called once per trial by Harbor after 'install()'. Uploads the
        instruction, executes the runtime with the canonical transcript captured to
        'runtime.log', then runs 'git apply --check' followed by 'git apply'.

        Raises MinerRuntimeError, MinerInvalidPatchError, or MinerPatchApplyError
        so Harbor's outer except clause catches them and skips the verifier.
        """
        with tempfile.TemporaryDirectory() as host_tmp_dir:
            instruction_host_path = Path(host_tmp_dir) / INSTRUCTION_FILENAME
            instruction_host_path.write_text(instruction)
            await environment.upload_file(instruction_host_path, self._env_instruction_path)
            command = (
                f"python3 -u {shlex.quote(self._env_runtime_path)} "
                f"--agent {shlex.quote(self._env_agent_path)} "
                f"--instruction {shlex.quote(self._env_instruction_path)} "
                f"--patch {shlex.quote(self._env_patch_path)} "
                f"--runtime {shlex.quote(self._env_runtime_payload_path)} "
                f"2>&1 | tee {shlex.quote(self._env_runtime_log_path)}"
            )

            await self._exec_with_log(
                environment,
                executor=self.exec_as_agent,
                command=command,
                cwd=self.workdir,
                log_filename=RUN_LOG_FILENAME,
                cancelled_detail="agent execution was cancelled, likely due to timeout",
                error_summary="Miner runtime failed",
                error_type=MinerRuntimeError,
                include_output_body=False,
            )

            patch_check_command = f"git apply --check {shlex.quote(self._env_patch_path)}"
            await self._exec_with_log(
                environment,
                executor=self.exec_as_agent,
                command=patch_check_command,
                cwd=self.workdir,
                log_filename=PATCH_CHECK_LOG_FILENAME,
                cancelled_detail="agent execution was cancelled, likely due to timeout",
                error_summary="Miner returned an invalid patch",
                error_type=MinerInvalidPatchError,
            )

            patch_apply_command = f"git apply {shlex.quote(self._env_patch_path)}"
            await self._exec_with_log(
                environment,
                executor=self.exec_as_agent,
                command=patch_apply_command,
                cwd=self.workdir,
                log_filename=PATCH_APPLY_LOG_FILENAME,
                cancelled_detail="agent execution was cancelled, likely due to timeout",
                error_summary="Failed to apply miner patch",
                error_type=MinerPatchApplyError,
            )

    def populate_context_post_run(self, context: AgentContext) -> None:
        """Mark Harbor's post-run agent context as handled.

        Note: Harbor may reach this hook from multiple Trial.run() termination
        paths. An empty dict marks the context as handled without copying
        ridges_runtime.json into result.json when the file is already on disk.
        """
        context.metadata = {}
