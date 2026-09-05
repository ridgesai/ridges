"""Tests for utils/temp.py — temporary directory management."""

import os
import pytest

from utils.temp import create_temp_dir, delete_temp_dir


class TestCreateTempDir:
    """Tests for create_temp_dir."""

    def test_creates_directory(self):
        temp_dir = create_temp_dir()
        try:
            assert os.path.isdir(temp_dir)
        finally:
            delete_temp_dir(temp_dir)

    def test_directory_is_unique(self):
        dirs = [create_temp_dir() for _ in range(5)]
        try:
            assert len(set(dirs)) == 5
        finally:
            for d in dirs:
                delete_temp_dir(d)

    def test_directory_is_writable(self):
        temp_dir = create_temp_dir()
        try:
            test_file = os.path.join(temp_dir, "test.txt")
            with open(test_file, "w") as f:
                f.write("hello")
            assert os.path.exists(test_file)
        finally:
            delete_temp_dir(temp_dir)


class TestDeleteTempDir:
    """Tests for delete_temp_dir."""

    def test_removes_directory(self):
        temp_dir = create_temp_dir()
        delete_temp_dir(temp_dir)
        assert not os.path.exists(temp_dir)

    def test_removes_directory_with_contents(self):
        temp_dir = create_temp_dir()
        # Create nested structure
        nested = os.path.join(temp_dir, "sub", "deep")
        os.makedirs(nested)
        with open(os.path.join(nested, "file.txt"), "w") as f:
            f.write("content")
        delete_temp_dir(temp_dir)
        assert not os.path.exists(temp_dir)

    def test_nonexistent_directory_does_not_raise(self):
        # Should not raise due to ignore_errors=True
        delete_temp_dir("/nonexistent/temp/dir/12345")
