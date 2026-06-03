import os
import time
from pathlib import Path

from utils.cleanup import prune_dirs_older_than


def _make_dir(parent: Path, name: str, *, age_seconds: float | None = None) -> Path:
    path = parent / name
    path.mkdir(parents=True)
    (path / "marker.txt").write_text("x")
    if age_seconds is not None:
        old = time.time() - age_seconds
        os.utime(path, (old, old))
    return path


def test_removes_only_dirs_older_than_window(tmp_path: Path):
    old = _make_dir(tmp_path, "old", age_seconds=10_000)
    fresh = _make_dir(tmp_path, "fresh", age_seconds=10)

    removed = prune_dirs_older_than(tmp_path, max_age_seconds=3600)

    assert removed == 1
    assert not old.exists()
    assert fresh.exists()


def test_skip_hidden_preserves_dotfiles_by_default(tmp_path: Path):
    tmp_download = _make_dir(tmp_path, ".tmp-abc", age_seconds=10_000)

    removed = prune_dirs_older_than(tmp_path, max_age_seconds=3600)

    assert removed == 0
    assert tmp_download.exists()


def test_skip_hidden_false_reaps_old_temp_dirs_but_not_fresh_ones(tmp_path: Path):
    """Crash-orphaned .tmp-* dirs age out; an in-flight (fresh) one is protected."""
    orphan = _make_dir(tmp_path, ".tmp-orphan", age_seconds=10_000)
    in_flight = _make_dir(tmp_path, ".tmp-inflight", age_seconds=10)

    removed = prune_dirs_older_than(tmp_path, max_age_seconds=3600, skip_hidden=False)

    assert removed == 1
    assert not orphan.exists()
    assert in_flight.exists()


def test_excluded_names_are_never_removed(tmp_path: Path):
    """The core race-safety guarantee: an active dir survives even when stale."""
    active = _make_dir(tmp_path, "problem__run-123", age_seconds=10_000)
    stale = _make_dir(tmp_path, "problem__run-456", age_seconds=10_000)

    removed = prune_dirs_older_than(tmp_path, max_age_seconds=3600, exclude_names={"problem__run-123"})

    assert removed == 1
    assert active.exists()
    assert not stale.exists()


def test_ignores_files_and_missing_parent(tmp_path: Path):
    (tmp_path / "loose.txt").write_text("not a dir")
    assert prune_dirs_older_than(tmp_path, max_age_seconds=0) == 0
    assert prune_dirs_older_than(tmp_path / "does-not-exist", max_age_seconds=0) == 0


def test_one_bad_dir_does_not_abort_sweep(tmp_path: Path, monkeypatch):
    bad = _make_dir(tmp_path, "bad", age_seconds=10_000)
    good = _make_dir(tmp_path, "good", age_seconds=10_000)

    real_rmtree = __import__("shutil").rmtree

    def flaky_rmtree(path, *args, **kwargs):
        if Path(path).name == "bad":
            raise OSError("device busy")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("utils.cleanup.shutil.rmtree", flaky_rmtree)

    removed = prune_dirs_older_than(tmp_path, max_age_seconds=3600)

    assert removed == 1
    assert bad.exists()
    assert not good.exists()
