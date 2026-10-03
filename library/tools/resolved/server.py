"""The ren-resolved daemon: one socket, one queue, one Resolve connection.

Protocol: one JSON object per line over the Unix socket, one reply line
per request.

    {"op": "ping"}
    {"op": "submit", "kind": K, "params": {...}, "owner": O,
     "qualification": false}            -> {"id": ..., "coalesced": bool}
    {"op": "status", "id": ID}          -> {"job": <receipt>}
    {"op": "result", "id": ID, "wait": SECONDS}
                                        -> {"job": <receipt>} once terminal,
                                           or as it stands at the deadline
    {"op": "cancel", "id": ID}          -> {"job": <receipt>} (queued only)
    {"op": "list", "limit": N}          -> {"jobs": [...], "running": [...]}
    {"op": "acquire", "params": {...}, "owner": O, "qualification": false,
     "wait": SECONDS}                   -> {"granted": ID} | {"error": ...}
        then, on the SAME connection:
    {"op": "release"}                   -> {"released": ID}
    {"op": "shutdown"}

A grant is bound to its connection: a client that dies mid-section
releases its grant by closing the socket, so the queue never wedges on a
dead holder.
"""

from __future__ import annotations

import json
import os
import socketserver
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from library.tools.resolved import jobs as job_kinds
from library.tools.resolved import scheduler
from library.tools.resolved.store import (
    TERMINAL,
    IdempotencyConflict,
    JobStore,
)

SOCKET_NAME = "ren-resolved.sock"
DB_NAME = "ren-resolved.sqlite3"


def socket_path() -> Path:
    from library.tools.resolve_lock import lock_dir
    return lock_dir() / SOCKET_NAME


def db_path() -> Path:
    from library.tools.resolve_lock import lock_dir
    return lock_dir() / DB_NAME


class Broker:
    """The queue, the scheduler loop and the executor, minus the socket.

    `connect` returns a Resolve handle; it is called lazily, once, and
    again only after a job finds the connection dead.
    """

    def __init__(self, store: JobStore,
                 connect: Optional[Callable[[], object]] = None):
        self.store = store
        self._connect = connect or _default_connect
        self._resolve = None
        self._cond = threading.Condition()
        self._queued: dict = {}       # id -> scheduler.Pending
        self._running: dict = {}      # id -> scheduler.Pending
        self._started: dict = {}      # id -> threading.Event (grants)
        self._coalesce: dict = {}     # key -> id, while queued or running
        self._cursor = ("", "")
        self._executor_queue: list = []
        self._stopping = False
        self._threads = [
            threading.Thread(target=self._schedule_loop, daemon=True,
                             name="ren-resolved-scheduler"),
            threading.Thread(target=self._execute_loop, daemon=True,
                             name="ren-resolved-executor"),
        ]
        for thread in self._threads:
            thread.start()

    # ── submission ───────────────────────────────────────────────
    def submit(self, kind: str, params: dict, owner: str = "",
               qualification: bool = False,
               idempotency_key: str | None = None) -> dict:
        shape = job_kinds.prepare(kind, params, qualification=qualification)
        idempotency = job_kinds.idempotency_request(
            kind, params, idempotency_key)
        with self._cond:
            key = shape["coalesce_key"]
            if idempotency is None:
                joined = self._coalesce.get(key) if key else None
                if joined is not None:
                    self.store.add_subscriber(joined)
                    return {"id": joined, "coalesced": True}
            job_id = uuid.uuid4().hex[:16]
            now = time.time()
            record = {
                "id": job_id, "kind": kind, "priority": shape["priority"],
                "mode": shape["mode"], "executed": int(shape["executed"]),
                "params": params, "project": shape["project"],
                "timeline": shape["timeline"],
                "qualification": int(qualification),
                "coalesce_key": key, "owner": owner, "state": "queued",
                "submitted": now}
            if idempotency is None:
                self.store.insert(record)
            else:
                durable_key, digest = idempotency
                try:
                    previous = self.store.insert_idempotent(
                        record, kind=kind, key=durable_key, digest=digest)
                except IdempotencyConflict as exc:
                    raise job_kinds.JobRefused(str(exc)) from exc
                if previous is not None:
                    if previous["state"] not in TERMINAL:
                        self.store.add_subscriber(previous["id"])
                    return {"id": previous["id"], "coalesced": True}
            self._queued[job_id] = scheduler.Pending(
                id=job_id, priority=shape["priority"], mode=shape["mode"],
                submitted=now, project=shape["project"],
                timeline=shape["timeline"], executed=shape["executed"],
                qualification=qualification,
                locality=shape["locality"])
            if key:
                self._coalesce[key] = job_id
            if not shape["executed"]:
                self._started[job_id] = threading.Event()
            self._cond.notify_all()
        return {"id": job_id, "coalesced": False}

    def wait_started(self, job_id: str, timeout: float) -> bool:
        event = self._started.get(job_id)
        return bool(event and event.wait(timeout))

    def result(self, job_id: str, wait: float = 0.0) -> Optional[dict]:
        deadline = time.time() + max(0.0, wait)
        with self._cond:
            while True:
                job = self.store.get(job_id)
                if job is None or job["state"] in TERMINAL:
                    return job
                remaining = deadline - time.time()
                if remaining <= 0:
                    return job
                self._cond.wait(min(remaining, 1.0))

    def cancel(self, job_id: str, reason: str = "cancelled") -> Optional[dict]:
        with self._cond:
            if job_id in self._queued:
                del self._queued[job_id]
                self._forget(job_id)
                self._started.pop(job_id, None)
                self.store.update(job_id, state="cancelled",
                                  finished=time.time(), error=reason)
                self._cond.notify_all()
        return self.store.get(job_id)

    def release(self, job_id: str, state: str = "done",
                error: str = "") -> None:
        """End a grant (or cancel it, if it was never started)."""
        with self._cond:
            if job_id in self._running:
                self._finish(job_id, state=state, error=error)
                return
        self.cancel(job_id, error or "the client left before its turn")

    def running(self) -> list:
        with self._cond:
            return [dict(vars(p)) for p in self._running.values()]

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()

    # ── internals ────────────────────────────────────────────────
    def _forget(self, job_id: str) -> None:
        job = self.store.get(job_id)
        key = job and job["coalesce_key"]
        if key and self._coalesce.get(key) == job_id:
            del self._coalesce[key]

    def _finish(self, job_id: str, state: str, result=None,
                error: str = "") -> None:
        """Caller holds `self._cond`."""
        del self._running[job_id]
        self._forget(job_id)
        event = self._started.pop(job_id, None)
        if event is not None:
            event.set()         # a grant refused before it started
        self.store.update(job_id, state=state, finished=time.time(),
                          result=result, error=error)
        self._cond.notify_all()

    def _schedule_loop(self) -> None:
        with self._cond:
            while not self._stopping:
                job = scheduler.next_job(self._queued.values(),
                                         self._running.values(),
                                         self._cursor, time.time())
                if job is None:
                    # Re-evaluated on every submit and finish, and at
                    # least every second for aging.
                    self._cond.wait(1.0)
                    continue
                del self._queued[job.id]
                self._running[job.id] = job
                if job.project:
                    self._cursor = (job.project, job.timeline)
                self.store.update(job.id, state="running",
                                  started=time.time())
                if job.executed or job.qualification:
                    # A qualification GRANT is checked on the broker's own
                    # connection before the caller gets Resolve.
                    self._executor_queue.append(job.id)
                else:
                    self._started[job.id].set()
                self._cond.notify_all()

    def _execute_loop(self) -> None:
        from library.tools.resolved import client
        client.mark_executor()
        while True:
            with self._cond:
                while not self._executor_queue and not self._stopping:
                    self._cond.wait(1.0)
                if self._stopping:
                    return
                job_id = self._executor_queue.pop(0)
            job = self.store.get(job_id)
            state, result, error = self._run(job)
            if state == "granted":
                # Checked; the caller takes Resolve now that the broker
                # has let go of it.
                self._started[job_id].set()
                continue
            with self._cond:
                self._finish(job_id, state=state, result=result, error=error)

    def _run(self, job: dict) -> tuple:
        """(state, result, error) for one job, under the instance lease.

        Held in the JOB's mode for the whole job, connection included:
        the scripting handshake is refused outside a lease
        (`resolve_locale`), and a connection opened between leases is a
        client nobody scheduled.
        """
        from library.tools.resolve_lock import resolve_lease
        try:
            with resolve_lease(f"ren-resolved {job['kind']} {job['id']}",
                               exclusive=job["mode"] == "exclusive"):
                if job["qualification"]:
                    found = job_kinds.open_project_name(self._resolve_handle())
                    if found != job_kinds.QUALIFICATION_PROJECT:
                        raise job_kinds.JobRefused(
                            f"a qualification job needs "
                            f"{job_kinds.QUALIFICATION_PROJECT!r} open; "
                            f"Resolve has {found or 'no project'!r}")
                if not job["executed"]:
                    return "granted", None, ""
                return "done", job_kinds.run(job, self._resolve_handle()), ""
        except job_kinds.JobRefused as exc:
            return "rejected", exc.result, str(exc)
        except Exception as exc:                              # noqa: BLE001
            # The job failed; the broker does not. A dead connection is
            # reopened on the next job rather than trusted.
            self._resolve = None
            return "failed", None, f"{type(exc).__name__}: {exc}"

    def _resolve_handle(self):
        if self._resolve is None:
            self._resolve = self._connect()
        return self._resolve


def _default_connect():
    from library.tools.marker_feedback import connect_resolve
    return connect_resolve()


# ── The socket ──────────────────────────────────────────────────────

class _Handler(socketserver.StreamRequestHandler):
    def _send(self, payload: dict) -> None:
        self.wfile.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.wfile.flush()

    def handle(self) -> None:
        broker: Broker = self.server.broker
        grant = None
        try:
            for line in self.rfile:
                try:
                    request = json.loads(line)
                    reply = self._dispatch(broker, request, grant)
                except job_kinds.JobRefused as exc:
                    reply = {"error": str(exc), "refused": True}
                except Exception as exc:                      # noqa: BLE001
                    reply = {"error": f"{type(exc).__name__}: {exc}"}
                if "granted" in reply:
                    grant = reply["granted"]
                elif "released" in reply:
                    grant = None
                self._send(reply)
                if request.get("op") == "shutdown":
                    threading.Thread(target=self.server.shutdown,
                                     daemon=True).start()
                    return
        finally:
            if grant is not None:
                broker.release(grant, state="done",
                               error="the client disconnected while holding")

    def _dispatch(self, broker: Broker, request: dict, grant) -> dict:
        op = request.get("op")
        if op == "ping":
            return {"ok": True, "pid": os.getpid()}
        if op == "submit":
            return broker.submit(request["kind"], request.get("params", {}),
                                 owner=request.get("owner", ""),
                                 qualification=bool(
                                     request.get("qualification")),
                                 idempotency_key=request.get(
                                     "idempotency_key"))
        if op in ("status", "result"):
            wait = float(request.get("wait", 0)) if op == "result" else 0.0
            return {"job": broker.result(request["id"], wait)}
        if op == "cancel":
            return {"job": broker.cancel(request["id"])}
        if op == "list":
            return {"jobs": broker.store.recent(int(request.get("limit", 20))),
                    "running": broker.running()}
        if op == "acquire":
            submitted = broker.submit("lease", request.get("params", {}),
                                      owner=request.get("owner", ""),
                                      qualification=bool(
                                          request.get("qualification")))
            job_id = submitted["id"]
            broker.wait_started(job_id, float(request.get("wait", 900)))
            job = broker.result(job_id)
            if job["state"] == "running":
                return {"granted": job_id}
            if job["state"] == "rejected":
                return {"error": job["error"], "refused": True}
            broker.release(job_id, error="the client's wait ran out")
            return {"error": "timed out waiting for Resolve", "busy": True,
                    "running": broker.running()}
        if op == "release":
            if grant is None:
                return {"error": "nothing is held on this connection"}
            broker.release(grant)
            return {"released": grant}
        if op == "shutdown":
            broker.stop()
            return {"ok": True}
        return {"error": f"unknown op {op!r}"}


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(path: Optional[Path] = None, connect=None) -> None:
    """Run the broker in the foreground until `shutdown` or a signal."""
    from library.tools.resolved import client
    path = Path(path or socket_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    if client.ping(path) is not None:
        raise SystemExit(f"ren-resolved is already serving on {path}")
    if path.exists():
        path.unlink()           # a dead broker's socket: nobody answers it
    store = JobStore(path.parent / DB_NAME)
    broker = Broker(store, connect=connect)
    server = _Server(str(path), _Handler)
    server.broker = broker
    os.chmod(path, 0o600)
    try:
        server.serve_forever()
    finally:
        broker.stop()
        server.server_close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        store.close()
