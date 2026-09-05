import os
import time
import fcntl
from contextlib import contextmanager

LOCK_FILE = "/tmp/resolve_placement.lock"

class ResolveRaceError(RuntimeError):
    pass

@contextmanager
def resolve_placement_lock():
    """Acquire a system-wide lock for Resolve placement operations."""
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

def assert_current_timeline(project, expected_timeline):
    """Verify the current timeline is the expected one immediately before writing."""
    project.SetCurrentTimeline(expected_timeline)
    current = project.GetCurrentTimeline()
    if not current or current.GetUniqueId() != expected_timeline.GetUniqueId():
        raise ResolveRaceError(f"Timeline race: expected {expected_timeline.GetName()}, but got {current.GetName() if current else 'None'}. Mutator changed it!")
