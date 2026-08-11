"""Tests for utils/diff.py — file diff computation, validation, and application."""

import os
import pytest
import tempfile
import subprocess

from unittest.mock import patch, MagicMock
from utils.diff import get_file_diff, validate_diff_for_local_repo, apply_diff_to_local_repo


class TestGetFileDiff:
    """Tests for the get_file_diff function."""

    def _write_temp(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        return path

    def test_identical_files_returns_empty_diff(self):
        path_a = self._write_temp("hello\nworld\n")
        path_b = self._write_temp("hello\nworld\n")
        try:
            diff = get_file_diff(path_a, path_b)
            assert diff.strip() == ""
        finally:
            os.unlink(path_a)
            os.unlink(path_b)

    def test_different_files_returns_unified_diff(self):
        path_a = self._write_temp("line1\nline2\n")
        path_b = self._write_temp("line1\nmodified\n")
        try:
            diff = get_file_diff(path_a, path_b)
            assert "-line2" in diff
            assert "+modified" in diff
        finally:
            os.unlink(path_a)
            os.unlink(path_b)

    def test_diff_header_uses_basename(self):
        path_a = self._write_temp("a\n")
        path_b = self._write_temp("b\n")
        try:
            diff = get_file_diff(path_a, path_b)
            basename = os.path.basename(path_a)
            assert f"--- {basename}" in diff
            assert f"+++ {basename}" in diff
        finally:
            os.unlink(path_a)
            os.unlink(path_b)

    def test_missing_file_raises_exception(self):
        existing = self._write_temp("content\n")
        try:
            with pytest.raises(Exception):
                get_file_diff(existing, "/nonexistent/file.txt")
        finally:
            os.unlink(existing)

    def test_both_files_missing_raises_exception(self):
        with pytest.raises(Exception):
            get_file_diff("/nonexistent/a.txt", "/nonexistent/b.txt")

    def test_added_lines_in_diff(self):
        path_a = self._write_temp("line1\n")
        path_b = self._write_temp("line1\nline2\nline3\n")
        try:
            diff = get_file_diff(path_a, path_b)
            assert "+line2" in diff
            assert "+line3" in diff
        finally:
            os.unlink(path_a)
            os.unlink(path_b)

    def test_removed_lines_in_diff(self):
        path_a = self._write_temp("line1\nline2\nline3\n")
        path_b = self._write_temp("line1\n")
        try:
            diff = get_file_diff(path_a, path_b)
            assert "-line2" in diff
            assert "-line3" in diff
        finally:
            os.unlink(path_a)
            os.unlink(path_b)


class TestValidateDiffForLocalRepo:
    """Tests for the validate_diff_for_local_repo function."""

    def _create_git_repo(self, files: dict) -> str:
        repo_dir = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, capture_output=True)
        for name, content in files.items():
            filepath = os.path.join(repo_dir, name)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w") as f:
                f.write(content)
        subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, capture_output=True, check=True)
        return repo_dir

    def test_valid_diff_returns_true(self):
        repo = self._create_git_repo({"hello.txt": "line1\nline2\n"})
        diff = "--- a/hello.txt\n+++ b/hello.txt\n@@ -1,2 +1,2 @@\n line1\n-line2\n+modified\n"
        is_valid, error = validate_diff_for_local_repo(diff, repo)
        assert is_valid is True
        assert error is None

    def test_invalid_diff_returns_false(self):
        repo = self._create_git_repo({"hello.txt": "line1\n"})
        diff = "--- a/nonexistent.txt\n+++ b/nonexistent.txt\n@@ -1 +1 @@\n-old\n+new\n"
        is_valid, error = validate_diff_for_local_repo(diff, repo)
        assert is_valid is False
        assert error is not None

    def test_empty_diff_is_valid(self):
        repo = self._create_git_repo({"hello.txt": "content\n"})
        is_valid, error = validate_diff_for_local_repo("", repo)
        assert is_valid is True


class TestApplyDiffToLocalRepo:
    """Tests for the apply_diff_to_local_repo function."""

    def _create_git_repo(self, files: dict) -> str:
        repo_dir = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, capture_output=True)
        for name, content in files.items():
            filepath = os.path.join(repo_dir, name)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w") as f:
                f.write(content)
        subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, capture_output=True, check=True)
        return repo_dir

    def test_apply_valid_diff_modifies_file(self):
        repo = self._create_git_repo({"hello.txt": "line1\nline2\n"})
        diff = "--- a/hello.txt\n+++ b/hello.txt\n@@ -1,2 +1,2 @@\n line1\n-line2\n+modified\n"
        apply_diff_to_local_repo(diff, repo)
        with open(os.path.join(repo, "hello.txt")) as f:
            assert f.read() == "line1\nmodified\n"

    def test_apply_invalid_diff_raises(self):
        repo = self._create_git_repo({"hello.txt": "content\n"})
        diff = "--- a/nonexistent.txt\n+++ b/nonexistent.txt\n@@ -1 +1 @@\n-old\n+new\n"
        with pytest.raises(Exception):
            apply_diff_to_local_repo(diff, repo)
