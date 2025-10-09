from __future__ import annotations

"""Centralised, extensible code-safety checks for uploaded miner agents.

New checks can be added by simply implementing a method that starts with
`check_` **or** by registering a standalone function via
`AgentCodeChecker.register_check()`.

Raise `CheckError` from a check to fail validation.
"""

from typing import Callable, List
import ast
import sys

from api.src.utils.config import PERMISSABLE_PACKAGES

__all__ = ["CheckError", "AgentCodeChecker"]


class CheckError(Exception):
    """Raised when a code-safety rule is violated."""


class AgentCodeChecker:
    """Runs static analysis on an uploaded *agent.py* file.

    Parameters
    ----------
    raw_code:
        Raw file contents (``bytes``) exactly as uploaded.
    """

    # ---------------------------------------------------------------------
    # House-keeping helpers
    # ---------------------------------------------------------------------
    def __init__(self, raw_code: bytes):
        self.raw_code: bytes = raw_code
        # Try to parse the code and provide detailed error messages if it fails
        try:
            self.tree: ast.AST = ast.parse(raw_code.decode("utf-8"))
        except SyntaxError as e:
            # Provide detailed syntax error information
            error_msg = f"Syntax error in uploaded code at line {e.lineno}"
            if e.offset:
                error_msg += f", column {e.offset}"
            if e.text:
                error_msg += f": {e.text.strip()}"
            if e.msg:
                error_msg += f" ({e.msg})"
            raise CheckError(error_msg)
        except UnicodeDecodeError as e:
            raise CheckError(f"Invalid file encoding: {e}. File must be UTF-8 encoded.")
        except Exception as e:
            raise CheckError(f"Failed to parse Python code: {e}")

        # Collect bound checks (methods with a *check_* prefix)
        self._checks: List[Callable[[AgentCodeChecker], None]] = [
            getattr(self, name)  # type: ignore[arg-type]
            for name in dir(self)
            if name.startswith("check_") and callable(getattr(self, name))
        ]

    # Public API -----------------------------------------------------------
    def register_check(self, func: Callable[["AgentCodeChecker"], None]) -> None:
        """Dynamically add a new rule.

        Example
        -------
        >>> def my_rule(checker: AgentCodeChecker):
        ...     if "secret" in checker.raw_code.decode():
        ...         raise CheckError("nope")
        >>> checker.register_check(my_rule)
        """

        self._checks.append(func)

    def run(self) -> None:
        """Run every rule and report *all* violations at once.

        Instead of failing fast, we collect the message of each ``CheckError``
        and raise a single aggregated exception at the end so the user sees
        every problem in one go.
        """

        errors: list[str] = []

        for rule in self._checks:
            try:
                # Bound method → already has *self* attached.
                if getattr(rule, "__self__", None) is self:
                    rule()
                else:  # Stand-alone function expects the checker argument.
                    rule(self)
            except CheckError as exc:
                errors.append(str(exc))

        if errors:
            # Combine the individual messages; newline keeps them readable.
            raise CheckError("\n".join(errors))

    # ------------------------------------------------------------------
    # Built-in rules (feel free to add more below)
    # ------------------------------------------------------------------
    # Rule helpers --------------------------------------------------------
    def _raise(self, message: str) -> None:  # convenience
        raise CheckError(message)

    # Individual rules ----------------------------------------------------
    def check_agent_main_exists(self) -> None:
        """Ensure there is a top-level ``def agent_main(...):``."""

        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == "agent_main":
                return
        self._raise('File must contain a function named "agent_main".')

    def check_import_whitelist(self) -> None:
        """Only allow stdlib + explicitly permitted third-party packages."""

        stdlib = sys.stdlib_module_names  # Py ≥3.10
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in stdlib and alias.name not in PERMISSABLE_PACKAGES:
                        self._raise(
                            f"Import '{alias.name}' is not allowed."
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod not in stdlib and mod not in PERMISSABLE_PACKAGES and mod.split(".")[0] not in PERMISSABLE_PACKAGES:
                    self._raise(
                        f"Import from '{mod}' is not allowed."
                    )

    def check_no_decoders(self) -> None:
        """Disallow any built-in decode / decompress helpers that are typically
        abused to unpack hidden payloads (base-64, hex, gzip, zlib, …)."""

        forbidden_calls = {
            # base-N
            "base64.b64decode",
            "base64.b32decode",
            "base64.b16decode",
            "b64decode",   # sometimes imported as `from base64 import b64decode`
            # hex / binary <--> text helpers
            "binascii.unhexlify",
            "bytes.fromhex",
            "unhexlify",
            "fromhex",
            # compression wrappers
            # "gzip.decompress",
            # "zlib.decompress",
            # "bz2.decompress",
            # "lzma.decompress",
            # "decompress",  # catch `from zlib import decompress`-style
        }

        violations: set[str] = set()

        # 1. AST-based search
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            full_name = self._resolve_call_name(node.func)
            if any(full_name.endswith(name) for name in forbidden_calls):
                violations.add(full_name)

        # 2. Raw-source heuristic (covers unparsable code / dynamic getattr)
        lower_src = self.raw_code.lower()
        for name in (b"b64decode", b"b32decode", b"b16decode", b"unhexlify", b"fromhex", b"decompress"):
            if name in lower_src:
                violations.add(name.decode())

        if violations:
            pretty = ", ".join(sorted(violations))
            self._raise(
                "Usage of decoder / decompressor functions is prohibited: "
                f"{pretty}."
            )

    # def check_no_binary_execution(self) -> None:
    #     """Disallow launching external binaries (os.system, subprocess, exec…)."""

    #     forbidden = {
    #         "subprocess.run",
    #         "subprocess.Popen",
    #         "os.system",
    #         "os.execv",
    #         "os.execve",
    #         "exec",
    #     }
    #     for node in ast.walk(self.tree):
    #         if not isinstance(node, ast.Call):
    #             continue
    #         full_name = self._resolve_call_name(node.func)
    #         if full_name in forbidden:
    #             self._raise(f"Forbidden call '{full_name}' detected.")

    # Utility -------------------------------------------------------------
    def _resolve_call_name(self, node: ast.AST) -> str:
        """Return dotted name of *node* if it is a simple attribute chain."""

        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = self._resolve_call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return "" 