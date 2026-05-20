import logging

import pytest


def test_handler_formats_message_with_extra_fields(capsys):
    from utils.logger import RidgesLogHandler

    log = logging.getLogger("test.structured")
    log.handlers.clear()
    log.propagate = False
    log.setLevel(logging.DEBUG)
    log.addHandler(RidgesLogHandler())

    log.info("hello world", extra={"hotkey": "5Grk", "num_runs": 3})

    captured = capsys.readouterr().out
    assert "hello world" in captured
    assert "hotkey=5Grk" in captured
    assert "num_runs=3" in captured


def test_handler_includes_correlation_id_when_present(capsys):
    from utils.logger import RidgesLogHandler

    log = logging.getLogger("test.correlation")
    log.handlers.clear()
    log.propagate = False
    log.setLevel(logging.DEBUG)
    log.addHandler(RidgesLogHandler())

    log.info("request", extra={"correlation_id": "abc-123"})

    captured = capsys.readouterr().out
    assert "correlation_id=abc-123" in captured


def test_handler_omits_correlation_id_when_absent(capsys):
    from utils.logger import RidgesLogHandler

    log = logging.getLogger("test.no_corr")
    log.handlers.clear()
    log.propagate = False
    log.setLevel(logging.DEBUG)
    log.addHandler(RidgesLogHandler())

    log.info("plain message")

    captured = capsys.readouterr().out
    assert "plain message" in captured
    assert "correlation_id" not in captured


def test_setup_logging_respects_debug_env(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    import importlib

    import utils.logger as ul

    importlib.reload(ul)
    ul.setup_logging()

    root = logging.getLogger()
    assert root.level == logging.DEBUG


def test_fatal_logs_and_raises():
    import utils.logger as ul

    with pytest.raises(SystemExit):
        ul.fatal("boom")


def test_setup_logging_attaches_ridges_handler(monkeypatch):
    monkeypatch.setenv("DEBUG", "false")
    import importlib

    import utils.logger as ul

    importlib.reload(ul)
    ul.setup_logging()

    root = logging.getLogger()
    from utils.logger import RidgesLogHandler

    assert any(isinstance(h, RidgesLogHandler) for h in root.handlers)


def test_setup_logging_suppresses_third_party_loggers(monkeypatch):
    monkeypatch.setenv("DEBUG", "false")
    import importlib

    import utils.logger as ul

    importlib.reload(ul)
    ul.setup_logging()

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("chain_utils").level == logging.WARNING
    assert logging.getLogger("uvicorn.access").level == logging.WARNING


def test_old_convenience_functions_removed():
    import utils.logger as ul

    assert not hasattr(ul, "info"), "info() shim should be removed"
    assert not hasattr(ul, "warning"), "warning() shim should be removed"
    assert not hasattr(ul, "error"), "error() shim should be removed"
    assert not hasattr(ul, "debug"), "debug() shim should be removed"
