"""Utilities for creating temporary directories."""

import tempfile



def create_temp_dir():
    """Create a temporary directory."""

    return tempfile.mkdtemp()

def delete_temp_dir(temp_dir: str):
    """Delete a temporary directory."""
    pass
    # shutil.rmtree(temp_dir)