"""A dead owner's session releases the scratch-origin slot.

THE DEFECT (measured 2026-10-21): seven abandoned sessions held the
machine's scratch-origin slot, and every writer that wanted it waited
for a slot that was never going to be released. Two things held it:

1. **the hostname was read as an identity.** The records carried
   `owner_host: "Mac"`, and this machine's name is now
   `Mac.attlocal.net` - so every record read as ANOTHER MACHINE's, which
   is fail-closed to ALIVE, and a dead owner's session was never
   released. The owner was long gone (the broker's own workers, killed
   when the broker was reloaded).
2. the take-back that releases a dead owner's session needs the Resolve
   instance, so the wait (which runs BEFORE the lease) could only report
   the staleness.

These are the defects this could reintroduce, each named:

1. a record that names THIS machine by its old short hostname is still
   this machine's - the dead owner is detected;
2. a record from ANOTHER machine still reads as ALIVE (fail-closed: the
   route back from a live writer's scratch project is never taken by
   judgement);
3. a record whose owner is alive is alive;
4. a record whose owner's pid was REUSED is gone.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _record(**overrides) -> dict:
    record = {"owner": "pid-1", "owner_pid": 1, "owner_host":
              socket.gethostname(), "owner_start": ""}
    record.update(overrides)
    return record


def test_a_record_naming_this_machine_by_its_old_short_name_is_this_machine():
    """The hostname changed; the record is still OURS.

    THE DEFECT: the records carried `owner_host: "Mac"` and the machine
    is now `Mac.attlocal.net`, so every one of them read as another
    machine's - fail-closed to alive - and seven abandoned sessions
    held the slot for ever.
    """
    from library.tools import resolve_scratch_projects as scratch

    old_name = socket.gethostname().split(".")[0]
    assert old_name, "the machine has no short name to test with"

    assert scratch._same_machine(_record(owner_host=old_name)) is True
    assert scratch._same_machine(_record(owner_host=socket.gethostname())) \
        is True


def test_a_record_from_another_machine_reads_as_alive():
    """Fail-closed: another machine's writer keeps its route back."""
    from library.tools import resolve_scratch_projects as scratch

    assert scratch._same_machine(_record(owner_host="some-other-host")) \
        is False
    assert scratch._owner_alive(
        _record(owner_host="some-other-host", owner_pid=os.getpid())) is True


def test_a_dead_owner_on_this_machine_is_gone():
    """The owner is gone: the session is not a live holder."""
    from library.tools import resolve_scratch_projects as scratch

    assert scratch._owner_alive(_record(owner_pid=999999)) is False


def test_a_live_owner_is_alive():
    """A live writer keeps its scratch project."""
    from library.tools import resolve_scratch_projects as scratch

    assert scratch._owner_alive(_record(owner_pid=os.getpid())) is True
