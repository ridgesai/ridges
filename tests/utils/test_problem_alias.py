import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from utils import problem_alias


@pytest.mark.parametrize("salt", [None, ""])
def test_missing_salt_uses_stable_keyed_digest(monkeypatch, salt):
    warning = Mock()
    monkeypatch.setattr(problem_alias.logger, "warning", warning)
    if salt is None:
        monkeypatch.delenv("PROBLEM_ALIAS_SALT", raising=False)
    else:
        monkeypatch.setenv("PROBLEM_ALIAS_SALT", salt)
    problem_alias._runtime_alias_salt.cache_clear()
    raw = "ridges:hidden-task"

    first = problem_alias._make_digest(raw)
    assert problem_alias._make_digest(raw) == first
    assert first != hashlib.sha256(raw.encode()).digest()
    assert len(problem_alias._RUNTIME_ALIAS_SALT) == 32
    warning.assert_called_once()
    assert "random process-local salt" in warning.call_args.args[0]
    assert problem_alias._RUNTIME_ALIAS_SALT.hex() not in str(warning.call_args)


def test_configured_salt_preserves_hmac_mapping(monkeypatch):
    warning = Mock()
    monkeypatch.setattr(problem_alias.logger, "warning", warning)
    monkeypatch.setenv("PROBLEM_ALIAS_SALT", "configured-test-secret")
    raw = "ridges:hidden-task"

    assert problem_alias._make_digest(raw) == hmac.new(b"configured-test-secret", raw.encode(), hashlib.sha256).digest()
    warning.assert_not_called()


def _aliases_in_fresh_process(salt):
    env = os.environ.copy()
    env.pop("PROBLEM_ALIAS_SALT", None)
    if salt is not None:
        env["PROBLEM_ALIAS_SALT"] = salt
    script = """
import json
from utils.problem_alias import make_problem_alias, make_test_alias
def aliases():
    return [make_problem_alias('hidden-task', 'ridges'),
            make_test_alias(benchmark_family='ridges', problem_name='hidden-task',
                            test_name='hidden-test', test_category='default')]
print(json.dumps([aliases(), aliases()]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(result.stdout)


def test_runtime_aliases_are_stable_within_process_and_change_after_restart():
    first = _aliases_in_fresh_process(None)
    second = _aliases_in_fresh_process(None)

    assert first[0] == first[1]
    assert second[0] == second[1]
    assert first[0] != second[0]


def test_configured_aliases_are_stable_across_processes():
    first = _aliases_in_fresh_process("shared-test-secret")
    second = _aliases_in_fresh_process("shared-test-secret")

    assert first[0] == first[1] == second[0] == second[1]
    assert first[0] != _aliases_in_fresh_process("different-test-secret")[0]
