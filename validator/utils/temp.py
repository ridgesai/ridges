"""Utilities for creating temporary directories."""

import os
import shutil
import stat
import tempfile


def create_temp_dir():
    """Create a temporary directory."""
    return tempfile.mkdtemp()


def cleanup_temp_dir(temp_path):
    """Clean up a temporary directory."""
    
    # too many issues with this for now, let it accumulate,
    # will manually have a cleanup on each vali iteration
    pass
