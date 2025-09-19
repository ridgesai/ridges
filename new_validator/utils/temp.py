"""Utilities for creating temporary directories."""

import os
import shutil
import tempfile


def create_temp_dir():
    """Create a temporary directory."""
    return tempfile.mkdtemp()


def cleanup_temp_dir(temp_path):
    """Clean up a temporary directory."""
    
    if os.path.exists(temp_path):
        shutil.rmtree(temp_path)
