import re

import pytest

from ridges_harbor.k8s_environment import sanitize_kubernetes_resource_name


def test_short_names_pass_through() -> None:
    assert sanitize_kubernetes_resource_name("my-task-v2-abc123") == "my-task-v2-abc123"


def test_long_session_ids_stay_distinct() -> None:
    long_prefix = "task-" * 20
    first = sanitize_kubernetes_resource_name(f"{long_prefix}first")
    second = sanitize_kubernetes_resource_name(f"{long_prefix}second")

    assert first != second
    assert len(first) <= 63 and len(second) <= 63


@pytest.mark.parametrize(
    "session_id",
    [
        "task__abc123",
        "My_Task/v2__ABC123",
        "trailing-underscore_",
        "x" * 80 + "_",
        "___",
    ],
)
def test_sanitized_names_are_rfc1123(session_id: str) -> None:
    name = sanitize_kubernetes_resource_name(session_id)
    assert 0 < len(name) <= 63
    assert re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", name), name


def test_empty_illegal_input_falls_back() -> None:
    assert sanitize_kubernetes_resource_name("___") == "ridges"
    assert sanitize_kubernetes_resource_name("...") == "ridges"
