"""Per-binary exclusion between native builds and live use of that binary.

The resource scheduler limits build CPU, memory and I/O. This file lock
adds the narrower safety rule the scheduler cannot express: a native
binary is never replaced while one of its render invocations is running.
"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def native_part_lock(root: str | Path, part: str, *, shared: bool
                     ) -> Iterator[None]:
    """Hold a shared use lock or exclusive rebuild lock for one native part."""
    lock_dir = Path(root) / "native" / "target" / ".ren-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / part
    with open(path, "a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
