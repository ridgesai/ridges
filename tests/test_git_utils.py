"""Tests for utils/git.py — Git repository operations."""

import os
import pytest
import tempfile
import subprocess

from utils.git import (
    clone_local_repo_at_commit,
    verify_commit_exists_in_local_repo,
    init_local_repo_with_initial_commit,
    reset_local_repo,
    get_local_repo_commit_hash,
)


def _create_repo_with_commits(num_commits: int = 2) -> tuple:
    """Create a temporary git repo with multiple commits. Returns (repo_dir, list_of_commit_hashes)."""
    repo_dir = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, capture_output=True)

    hashes = []
    for i in range(num_commits):
        filepath = os.path.join(repo_dir, f"file_{i}.txt")
        with open(filepath, "w") as f:
            f.write(f"commit {i}\n")
        subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", f"commit {i}"], cwd=repo_dir, capture_output=True, check=True)
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True)
        hashes.append(result.stdout.strip())

    return repo_dir, hashes


class TestGetLocalRepoCommitHash:
    """Tests for get_local_repo_commit_hash."""

    def test_returns_correct_hash(self):
        repo_dir, hashes = _create_repo_with_commits(1)
        assert get_local_repo_commit_hash(repo_dir) == hashes[0]

    def test_returns_latest_commit(self):
        repo_dir, hashes = _create_repo_with_commits(3)
        assert get_local_repo_commit_hash(repo_dir) == hashes[-1]

    def test_hash_is_40_char_hex(self):
        repo_dir, _ = _create_repo_with_commits(1)
        commit_hash = get_local_repo_commit_hash(repo_dir)
        assert len(commit_hash) == 40
        assert all(c in "0123456789abcdef" for c in commit_hash)


class TestVerifyCommitExistsInLocalRepo:
    """Tests for verify_commit_exists_in_local_repo."""

    def test_existing_commit_returns_true(self):
        repo_dir, hashes = _create_repo_with_commits(2)
        assert verify_commit_exists_in_local_repo(repo_dir, hashes[0]) is True
        assert verify_commit_exists_in_local_repo(repo_dir, hashes[1]) is True

    def test_nonexistent_commit_returns_false(self):
        repo_dir, _ = _create_repo_with_commits(1)
        assert verify_commit_exists_in_local_repo(repo_dir, "a" * 40) is False

    def test_nonexistent_directory_returns_false(self):
        assert verify_commit_exists_in_local_repo("/nonexistent/dir", "abc123") is False


class TestInitLocalRepoWithInitialCommit:
    """Tests for init_local_repo_with_initial_commit."""

    def test_creates_git_repo(self):
        temp_dir = tempfile.mkdtemp()
        with open(os.path.join(temp_dir, "file.txt"), "w") as f:
            f.write("hello\n")
        init_local_repo_with_initial_commit(temp_dir)
        assert os.path.exists(os.path.join(temp_dir, ".git"))

    def test_initial_commit_exists(self):
        temp_dir = tempfile.mkdtemp()
        with open(os.path.join(temp_dir, "file.txt"), "w") as f:
            f.write("hello\n")
        init_local_repo_with_initial_commit(temp_dir)
        result = subprocess.run(["git", "log", "--oneline"], cwd=temp_dir, capture_output=True, text=True)
        assert "Initial commit" in result.stdout

    def test_custom_commit_message(self):
        temp_dir = tempfile.mkdtemp()
        with open(os.path.join(temp_dir, "file.txt"), "w") as f:
            f.write("hello\n")
        init_local_repo_with_initial_commit(temp_dir, "Custom message")
        result = subprocess.run(["git", "log", "--oneline"], cwd=temp_dir, capture_output=True, text=True)
        assert "Custom message" in result.stdout

    def test_all_files_are_committed(self):
        temp_dir = tempfile.mkdtemp()
        for name in ["a.txt", "b.txt", "c.txt"]:
            with open(os.path.join(temp_dir, name), "w") as f:
                f.write(f"{name}\n")
        init_local_repo_with_initial_commit(temp_dir)
        result = subprocess.run(["git", "status", "--porcelain"], cwd=temp_dir, capture_output=True, text=True)
        assert result.stdout.strip() == ""


class TestCloneLocalRepoAtCommit:
    """Tests for clone_local_repo_at_commit."""

    def test_clone_at_first_commit(self):
        repo_dir, hashes = _create_repo_with_commits(3)
        target = tempfile.mkdtemp()
        os.rmdir(target)
        clone_local_repo_at_commit(repo_dir, hashes[0], target)
        cloned_hash = get_local_repo_commit_hash(target)
        assert cloned_hash == hashes[0]

    def test_clone_at_latest_commit(self):
        repo_dir, hashes = _create_repo_with_commits(2)
        target = tempfile.mkdtemp()
        os.rmdir(target)
        clone_local_repo_at_commit(repo_dir, hashes[-1], target)
        cloned_hash = get_local_repo_commit_hash(target)
        assert cloned_hash == hashes[-1]

    def test_clone_nonexistent_repo_raises(self):
        target = tempfile.mkdtemp()
        os.rmdir(target)
        with pytest.raises(Exception):
            clone_local_repo_at_commit("/nonexistent/repo", "abc123", target)


class TestResetLocalRepo:
    """Tests for reset_local_repo."""

    def test_reset_to_earlier_commit(self):
        repo_dir, hashes = _create_repo_with_commits(3)
        reset_local_repo(repo_dir, hashes[0])
        assert get_local_repo_commit_hash(repo_dir) == hashes[0]

    def test_file_from_later_commit_is_gone(self):
        repo_dir, hashes = _create_repo_with_commits(3)
        reset_local_repo(repo_dir, hashes[0])
        assert not os.path.exists(os.path.join(repo_dir, "file_1.txt"))
        assert not os.path.exists(os.path.join(repo_dir, "file_2.txt"))
        assert os.path.exists(os.path.join(repo_dir, "file_0.txt"))
