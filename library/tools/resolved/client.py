"""Talking to ren-resolved: submit, status, result, and a grant.

Every call is bounded. A socket file nobody answers is a dead broker,
and a caller treats it as no broker (`ping` returns None) rather than
waiting on it.
"""

from __future__ import annotations

import json
import socket
import threading
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
    where no broker is serving."""
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
