"""Talking to ren-resolved: submit, status, result, and a grant.

Every call is bounded. A socket file nobody answers is a dead broker,
and a caller treats it as no broker (`ping` returns None) rather than
waiting on it.

The broker is a STANDALONE service, not a child of any worker:
`ensure` starts it detached (its own session, stdio to a log) so it
outlives the worker that started it, and every client that needs the
broker calls `ensure` first - the lease path (`resolve_lock`),
`resolve-axi`, and `call` itself. A starter lock serializes
concurrent starters, so exactly one broker serves a socket.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

#: Marks the broker's own executor thread: the work it runs must not
#: queue behind itself, and `resolve-axi` there runs rather than
#: forwarding back to the broker.
_executor = threading.local()


def mark_executor() -> None:
    _executor.active = True


def in_broker() -> bool:
    return getattr(_executor, "active", False)

CONNECT_TIMEOUT_SECONDS = 2.0

#: How long `ensure` waits for a broker it started to answer. The
#: broker connects to Resolve LAZILY (only when a job runs), so this
#: is interpreter startup and imports only - a few seconds at most.
START_TIMEOUT_SECONDS = 15.0

LOG_NAME = "ren-resolved.log"
STARTER_LOCK_NAME = "ren-resolved.starter.lock"


class BrokerError(RuntimeError):
    """The broker answered with an error. `reply` is what it said."""

    def __init__(self, reply: dict):
        super().__init__(reply.get("error", "ren-resolved error"))
        self.reply = reply


def _path(path=None) -> Path:
    if path is not None:
        return Path(path)
    from library.tools.resolved.server import socket_path
    return socket_path()


def _open(path=None) -> Optional[socket.socket]:
    target = _path(path)
    if not target.exists():
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT_SECONDS)
    try:
        sock.connect(str(target))
    except OSError:
        sock.close()
        return None
    return sock


def _exchange(sock: socket.socket, stream, request: dict,
              timeout: Optional[float]) -> dict:
    sock.settimeout(timeout)
    sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
    line = stream.readline()
    if not line:
        raise BrokerError({"error": "ren-resolved closed the connection"})
    reply = json.loads(line)
    if "error" in reply:
        raise BrokerError(reply)
    return reply


def call(request: dict, path=None, timeout: Optional[float] = 30.0) -> dict:
    """One request, one reply. Raises `BrokerError`, or `ConnectionError`
    where no broker is serving.

    Ensures a broker is serving first - the one op that must not is
    `shutdown`, which is the stop command and has to be able to say
    "nothing is serving" rather than starting what it was asked to
    stop.
    """
    if request.get("op") != "shutdown":
        ensure(path)
    sock = _open(path)
    if sock is None:
        raise ConnectionError(f"ren-resolved is not serving on {_path(path)}")
    with sock, sock.makefile("r", encoding="utf-8") as stream:
        return _exchange(sock, stream, request, timeout)


def ping(path=None) -> Optional[dict]:
    """The serving broker's answer, or None where none is serving."""
    sock = _open(path)
    if sock is None:
        return None
    try:
        with sock, sock.makefile("r", encoding="utf-8") as stream:
            return _exchange(sock, stream, {"op": "ping"},
                             CONNECT_TIMEOUT_SECONDS)
    except (OSError, ValueError, BrokerError):
        return None


def serving(path=None) -> bool:
    return not in_broker() and ping(path) is not None


def health(path=None) -> dict:
    """Health status: separate `connected` from `responsive`.

    `connected` is True where a non-null Resolve handle exists.
    `responsive` is True where a short, harmless API round trip succeeds.
    """
    return call({"op": "health"}, path)


def post_restart_check(path=None) -> dict:
    """A short read-only project/timeline/count check after a restart."""
    return call({"op": "post_restart_check"}, path)


def watchdog_status(path=None) -> dict:
    """The watchdog's current state, for health reporting."""
    return call({"op": "watchdog_status"}, path)


def _in_pytest() -> bool:
    """True when running under pytest (a test process)."""
    return "pytest" in sys.modules


def _engine_root() -> Optional[Path]:
    """The checkout this package was imported from, or None.

    Found by walking up from this file to the directory that contains
    `library/tools/resolved/__init__.py` - so a packaged install (where
    `library` sits in site-packages) answers None and the subprocess
    relies on the inherited environment instead.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "library" / "tools" / "resolved" / "__init__.py").is_file():
            return parent
    return None


def _start_detached(path: Path) -> subprocess.Popen:
    """Start the broker in its OWN session, stdio to a log.

    `start_new_session` detaches it from the starter's process group,
    so killing the worker that started it (how agent sessions end)
    leaves the broker serving. Stdio goes to `LOG_NAME` beside the
    socket rather than the worker's pipes, which would otherwise stay
    open for the broker's lifetime.
    """
    env = dict(os.environ)
    root = _engine_root()
    if root is not None:
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(root), env.get("PYTHONPATH", "")) if p)
    log = open(path.parent / LOG_NAME, "ab", buffering=0)
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "library.tools.resolved", "serve"],
            cwd=str(root) if root is not None else os.getcwd(),
            env=env, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True)
    finally:
        log.close()          # the child holds its own dup


def ensure(path=None, timeout: float = START_TIMEOUT_SECONDS) -> Optional[dict]:
    """The serving broker's answer, starting one first if none serves.

    This is what makes the broker a standalone service: any client
    that needs it calls `ensure`, and the broker it starts is detached
    and outlives the worker that started it. A no-op where this
    process IS the broker (`in_broker`), and where one is already
    serving it returns that broker's answer at once.

    Single instance is guaranteed twice over: a starter lock
    (`STARTER_LOCK_NAME` beside the socket) serializes concurrent
    starters - the second re-pings under the lock and finds the first's
    broker - and `serve` itself refuses a socket that answers. A
    broker that dies on startup is reported as None (fail fast, no
    waiting out the timeout) and the next client starts a fresh one.
    """
    if in_broker():
        return None
    target = _path(path)
    # Guard: never auto-start a broker on the DEFAULT socket from inside
    # pytest. A test that forgets to isolate its lock dir (measured: a
    # monkeypatch.undo() that unset PIPELINE_RESOLVE_LOCK_DIR) would
    # otherwise leak a broker onto the production socket, which then
    # serves production Resolve calls on unmerged code.
    if _in_pytest():
        from library.tools.resolve_lock import LOCK_DIR_ENV
        if not os.environ.get(LOCK_DIR_ENV):
            return None
    answer = ping(target)
    if answer is not None:
        return answer
    try:
        import fcntl
    except ImportError:  # pragma: no cover - not POSIX
        fcntl = None
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / STARTER_LOCK_NAME
    with open(lock_path, "a+", encoding="utf-8") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            answer = ping(target)
            if answer is not None:
                return answer
            process = _start_detached(target)
            deadline = time.time() + max(0.0, timeout)
            while True:
                answer = ping(target)
                if answer is not None:
                    return answer
                if process.poll() is not None:
                    return None
                if time.time() >= deadline:
                    return None
                time.sleep(0.1)
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return None


def submit(kind: str, params: dict, owner: str = "",
           qualification: bool = False, path=None,
           idempotency_key: str | None = None) -> dict:
    request = {"op": "submit", "kind": kind, "params": params,
               "owner": owner, "qualification": qualification}
    if idempotency_key is not None:
        request["idempotency_key"] = idempotency_key
    return call(request, path)


def result(job_id: str, wait: float = 0.0, path=None) -> dict:
    reply = call({"op": "result", "id": job_id, "wait": wait}, path,
                 timeout=wait + 30.0)
    return reply["job"]


@contextmanager
def grant(params: dict, owner: str = "", qualification: bool = False,
          wait: float = 900.0, path=None):
    """Hold the broker's turn for an in-process critical section.

    Yields the grant's job id. The turn is held exactly as long as this
    connection: leaving the block releases it, and so does dying.
    """
    sock = _open(path)
    if sock is None:
        raise ConnectionError(f"ren-resolved is not serving on {_path(path)}")
    with sock, sock.makefile("r", encoding="utf-8") as stream:
        reply = _exchange(sock, stream,
                          {"op": "acquire", "params": params, "owner": owner,
                           "qualification": qualification, "wait": wait},
                          wait + 30.0)
        try:
            yield reply["granted"]
        finally:
            try:
                _exchange(sock, stream, {"op": "release"}, 30.0)
            except (OSError, ValueError, BrokerError):
                pass        # closing the socket releases it as well
