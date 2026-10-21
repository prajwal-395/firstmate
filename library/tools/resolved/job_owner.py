"""Who owns a broker job, and whether that owner is still there.

The broker records a job's `owner` as a name a waiter can act on: the
lane that asked, and the process that asked. The pid is in the name
because it is the only part a broker can CHECK - a name is prose, a
pid is a process.

A grant (`lease` job) is the case that matters: the client that asked
for it is the connection that holds it, so a client that dies while
waiting leaves a grant queued with nobody waiting for it. The broker
reaps those (see `server.Broker._reap_dead_grants`), and the queue
report names them (`library.tools.resolved.queue_status`).

An EXECUTED job is never reaped on a dead owner: its submitter may
legitimately exit after submitting (`ren resolved submit` prints the id
and returns), and the job is still wanted.
"""

from __future__ import annotations

import os
import re

#: The owner's pid, as the broker records it: a process is `pid-1234`,
#: and a lane that names itself carries the pid beside its name
#: (`lane-3 (pid-1234)`).
_PID_PATTERN = re.compile(r"pid[-= ](\d+)")


def owner_pid(owner: str) -> int | None:
    """The pid the owner names, or None where it names none.

    A name that is not a pid at all (a lane's task name on its own)
    yields None, and the job is left alone by anything that reaps a
    dead owner: absence is not death.
    """
    if not owner:
        return None
    found = _PID_PATTERN.search(str(owner))
    if found is None:
        return None
    try:
        pid = int(found.group(1))
    except ValueError:
        return None
    return pid if pid > 0 else None


def process_alive(pid: int) -> bool:
    """Whether the process is still there.

    A pid this process cannot signal belongs to somebody else and is
    alive; a pid nobody answers for is gone.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def owner_alive(owner: str) -> bool | None:
    """Whether the owner's process is alive, or None where it names none."""
    pid = owner_pid(owner)
    if pid is None:
        return None
    return process_alive(pid)
