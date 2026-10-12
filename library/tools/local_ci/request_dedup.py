"""Coalesce identical in-flight Tier-1 requests inside the CI daemon.

The identity is supplied by the caller and must include every verification
key and the selected test path. Consumers are retained with the shared result
so the daemon can account for every check served by one execution.
"""

from __future__ import annotations

import threading
from collections import OrderedDict


class RequestDeduplicator:
    """Single-flight execution for concurrent requests with the same key."""

    def __init__(self, keep: int = 1024) -> None:
        if keep < 1:
            raise ValueError("keep must be positive")
        self._keep = keep
        self._lock = threading.Condition()
        self._requests: OrderedDict[str, dict] = OrderedDict()

    def run(self, key: str, consumer: str, execute) -> dict:
        """Run once per key and return its result plus all consumers.

        Exceptions are shared as results too: every consumer sees the same
        failure and a later request may retry after the failed row is evicted.
        """
        with self._lock:
            request = self._requests.get(key)
            if request is None:
                request = {"running": True, "consumers": [], "result": None,
                           "error": None}
                self._requests[key] = request
                owner = True
            else:
                owner = False
            if consumer not in request["consumers"]:
                request["consumers"].append(consumer)
                self._lock.notify_all()
            if not owner:
                while request["running"]:
                    self._lock.wait()
                if request["error"] is not None:
                    raise request["error"]
                result = request["result"]
                return {"result": result, "consumers": list(request["consumers"]),
                        "shared": True}

        try:
            result = execute()
        except BaseException as exc:
            with self._lock:
                request["error"] = exc
                request["running"] = False
                self._trim()
                self._lock.notify_all()
            raise
        with self._lock:
            request["result"] = result
            request["running"] = False
            self._trim()
            self._lock.notify_all()
            return {"result": result, "consumers": list(request["consumers"]),
                    "shared": False}

    def _trim(self) -> None:
        completed = [key for key, value in self._requests.items()
                     if not value["running"]]
        while len(completed) > self._keep:
            self._requests.pop(completed.pop(0), None)


_daemon_requests = RequestDeduplicator()


def run(key: str, consumer: str, execute) -> dict:
    """Daemon-wide coordinator; separate checkouts share only equal keys."""
    return _daemon_requests.run(key, consumer, execute)
