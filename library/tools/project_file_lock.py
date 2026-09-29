"""Short cross-process locks for project files updated by read/merge/write."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


@contextmanager
def lock_project_file(path: str | os.PathLike):
    """Serialize a project's read/merge/write for the named file.

    The lock is separate from the data file, so replacing the data file
    atomically does not replace the lock other writers are waiting on.
    """
    data_path = Path(path)
    lock_path = data_path.with_name(data_path.name + ".lock")
    lock_key = str(lock_path.resolve())
    with _thread_locks_guard:
        local_lock = _thread_locks.setdefault(lock_key, threading.Lock())

    data_path.parent.mkdir(parents=True, exist_ok=True)
    with local_lock:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - the pipeline runs on POSIX
            yield
            return
        with open(lock_path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
