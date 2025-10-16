"""Utilities for creating temporary directories."""

import shutil
import tempfile



def create_temp_dir():
    """Create a temporary directory."""

    return tempfile.mkdtemp()



def delete_temp_dir(temp_dir: str):
    """Delete a temporary directory."""
    
    shutil.rmtree(temp_dir, ignore_errors=True)