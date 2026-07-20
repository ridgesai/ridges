"""Tests for utils/logger.py — logging utilities."""

import os
import pytest
from unittest.mock import patch
from io import StringIO

import utils.logger as logger


class TestLogLevels:
    """Tests for different log levels."""

    def test_info_prints_output(self, capsys):
        logger.info("test info message")
        captured = capsys.readouterr()
        assert "INFO" in captured.out
        assert "test info message" in captured.out

    def test_warning_prints_output(self, capsys):
        logger.warning("test warning")
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "test warning" in captured.out

    def test_error_prints_output(self, capsys):
        logger.error("test error")
        captured = capsys.readouterr()
        assert "ERROR" in captured.out
        assert "test error" in captured.out

    def test_fatal_raises_exception(self):
        with pytest.raises(Exception, match="fatal message"):
            logger.fatal("fatal message")

    def test_fatal_prints_before_raising(self, capsys):
        with pytest.raises(Exception):
            logger.fatal("fatal msg")
        captured = capsys.readouterr()
        assert "FATAL" in captured.out

    @patch.dict(os.environ, {"DEBUG": "true"})
    def test_debug_prints_when_enabled(self, capsys):
        logger.debug("debug message")
        captured = capsys.readouterr()
        assert "debug message" in captured.out

    @patch.dict(os.environ, {"DEBUG": "false"})
    def test_debug_silent_when_disabled(self, capsys):
        logger.debug("should not appear")
        captured = capsys.readouterr()
        assert "should not appear" not in captured.out


class TestLogFormat:
    """Tests for log message formatting."""

    def test_log_contains_timestamp(self, capsys):
        logger.info("timestamp test")
        captured = capsys.readouterr()
        # Should contain date-like pattern
        assert "-" in captured.out  # YYYY-MM-DD
        assert ":" in captured.out  # HH:MM:SS

    def test_log_contains_file_and_line(self, capsys):
        logger.info("location test")
        captured = capsys.readouterr()
        # Should contain the test file name
        assert "test_logger.py" in captured.out
