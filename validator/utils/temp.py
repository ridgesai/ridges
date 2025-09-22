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
    
    if os.path.exists(temp_path):
        # Handle permission errors by making files writable before deletion
        def handle_remove_readonly(func, path, exc):
            """Error handler for removing read-only files."""
            if os.path.exists(path):
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                func(path)
        
        shutil.rmtree(temp_path, onerror=handle_remove_readonly)
