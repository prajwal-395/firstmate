"""Where the ONE broker for this machine keeps its files.

One Resolve per machine (`resolve_lock`), so ONE broker serves it. The
broker's identity is the MACHINE's, not a checkout's - and it used not
to be: the broker's socket, database and log lived in
`resolve_lock.lock_dir()`, which a lane may point anywhere
(`PIPELINE_RESOLVE_LOCK_DIR`). A qualification run, a benchmark and a
worktree lane each got a PRIVATE broker with a private socket, and
each of those brokers drove the SAME Resolve. Measured 2026-10-20:
three brokers serving at once against the one Resolve, and
`ren resolved status` from the primary checkout reporting "no broker is
serving" while a broker from that checkout's build directory was
running - four writers to one Resolve, and the project-switch and
timeline-count races that came with them.

So the broker's files live in ONE directory per machine, beside the
machine-wide instance lease (`resolve_lock.instance_lock_dir()`): the
socket, the database, the log, the starter lock, the bind lock and the
cancellation markers. A broker's directory IS the directory its socket
lives in, so a deliberately private broker (the qualification harness
names its own socket path) keeps its own queue, its own state and its
own markers.

The test suite keeps the private directory it already had
(`resolve_lock.lock_dir()`), so a test's broker never reaches the
machine's broker.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

#: The socket the broker serves on.
SOCKET_NAME = "ren-resolved.sock"
#: The queue and job state, beside the socket.
DB_NAME = "ren-resolved.sqlite3"
#: The broker's own log, beside the socket.
LOG_NAME = "ren-resolved.log"
#: Serializes concurrent starters, so one broker starts per socket.
STARTER_LOCK_NAME = "ren-resolved.starter.lock"
#: Serializes the bind itself, so two brokers cannot both pass the
#: "nobody is serving" probe and one orphan the other.
BIND_LOCK_NAME = "ren-resolved.bind.lock"
#: Where cooperative cancellation markers live, beside the socket.
CANCEL_DIR_NAME = "ren-resolved-cancel"

#: An explicit override of the machine broker's directory. Nothing in
#: production sets it: the directory is the machine's, derived from the
#: instance lease's home. A test (or a deliberately private broker)
#: sets it to keep its own broker.
BROKER_DIR_ENV = "PIPELINE_RESOLVE_BROKER_DIR"

#: The broker tells the processes it starts where its own directory is,
#: so a worker writes its cancellation markers beside the broker that
#: queued the job rather than beside the machine's default.
BROKER_DIR_TOLD_ENV = "REN_RESOLVED_BROKER_DIR"


def _in_test_suite() -> bool:
    """True where this process runs inside the test suite.

    The same signal `resolve_lock` uses: the suite's own marker in the
    environment, or pytest imported in this process.
    """
    import sys

    if "pytest" in sys.modules:
        return True
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def broker_dir() -> Path:
    """The directory this machine's ONE broker keeps its files in.

    The machine-wide instance directory (`resolve_lock.instance_lock_dir()`),
    so every checkout, worktree and lane on this machine names the SAME
    broker. Inside the test suite that directory is the suite's private
    lock directory, so a test's broker never reaches the machine's.
    """
    override = os.environ.get(BROKER_DIR_ENV)
    if override:
        return Path(override)
    from library.tools import resolve_lock

    return resolve_lock.instance_lock_dir()


def production_dir() -> Path:
    """The machine broker's directory OUTSIDE the test suite.

    This is where the production broker serves, whatever this process
    is: a checkout, a worker worktree or a lane. The guards that
    protect it ("never start it from a worktree", "never stop it from
    a test") are judged against this path, not against whatever the
    calling process happens to have isolated.

    `PIPELINE_RESOLVE_INSTANCE_DIR` is deliberately NOT read here: it
    redirects the instance directory for a test that must not compete
    with the live captain, and a test's redirect is not the
    production socket.
    """
    override = os.environ.get(BROKER_DIR_ENV)
    if override:
        return Path(override)
    return Path(tempfile.gettempdir())


def socket_path() -> Path:
    """The socket the machine's broker serves on."""
    return broker_dir() / SOCKET_NAME


def production_socket_path() -> Path:
    """The production broker's socket, whatever this process isolated."""
    return production_dir() / SOCKET_NAME


def _same_path(left, right) -> bool:
    """Whether two paths name the same file, symlinks resolved."""
    try:
        return os.path.realpath(str(left)) == os.path.realpath(str(right))
    except OSError:  # pragma: no cover - a path that cannot be read
        return False


def is_production_socket(target) -> bool:
    """True where `target` is the production broker's socket."""
    return _same_path(target, production_socket_path())


def is_machine_socket(target) -> bool:
    """True where `target` is the socket this machine's broker serves on.

    The machine's broker is one socket for the machine, so this is the
    socket any client reaches when it does not name one - whatever
    checkout, worktree or lane it runs from.
    """
    return _same_path(target, socket_path())


def dir_for(socket) -> Path:
    """The directory a broker serving on `socket` keeps its files in.

    A broker's directory is the directory its socket lives in: the
    queue, the log and the cancellation markers follow the socket, so a
    deliberately private broker keeps its own state.
    """
    return Path(socket).parent


def db_path(socket=None) -> Path:
    """The broker's queue and job state."""
    return dir_for(socket or socket_path()) / DB_NAME


def cancel_dir(socket=None) -> Path:
    """Where cooperative cancellation markers live."""
    told = os.environ.get(BROKER_DIR_TOLD_ENV)
    if told and socket is None:
        return Path(told) / CANCEL_DIR_NAME
    return dir_for(socket or socket_path()) / CANCEL_DIR_NAME
