"""Utilities for creating temporary directories."""
import utils.logger as logger

from pathlib import Path
import shutil
import tempfile
import time




def create_temp_dir():
    """Create a temporary directory."""

    return tempfile.mkdtemp()



def delete_temp_dir(temp_dir: str):
    """Delete a temporary directory."""
    shutil.rmtree(temp_dir, ignore_errors=True)



def clean_tmp(tmp_clean_age_seconds: int = 3600):
    for path in Path(tempfile.gettempdir()).glob("tmp*"):
        runner_file = path / "AGENT_RUNNER.py"
        if not runner_file.exists():
            continue

        now = time.time()
        modified_time = path.stat().st_mtime
        age = now - modified_time

        if age > tmp_clean_age_seconds:
            logger.debug(f"Deleting {path}")
            shutil.rmtree(path, ignore_errors=True)
