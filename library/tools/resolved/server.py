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
    {"op": "cancel", "id": ID}          -> {"job": <receipt>}; running
                                            jobs must declare safe Stop support
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

import datetime
import json
import os
import socketserver
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from library.tools.resolved import jobs as job_kinds
from library.tools.resolved import scheduler
from library.tools.resolved.cancellation import CancelRequested, clear
from library.tools.resolved import cancellation
from library.tools.resolved.store import (
    TERMINAL,
    IdempotencyConflict,
    JobStore,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None

SOCKET_NAME = "ren-resolved.sock"
DB_NAME = "ren-resolved.sqlite3"
BIND_LOCK_NAME = "ren-resolved.bind.lock"


def _log_broker(message: str) -> None:
    """One timestamped line on stderr: the broker's stdio is its log
    file, so this is what makes the next "already stopped, no known
    cause" answerable - every start and every stop lands here with
    when and which tree. Logging never breaks the broker."""
    try:
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        print(f"{stamp} {message}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 - the log must not break serving
        pass


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
                 connect: Callable[[], object] | None = None,
                 watchdog: object | None = None):
        self.store = store
        self._connect = connect or _default_connect
        self._resolve = None
        self._watchdog = watchdog
        self._cond = threading.Condition()
        self._queued: dict = {}       # id -> scheduler.Pending
        self._running: dict = {}      # id -> scheduler.Pending
        self._started: dict = {}      # id -> threading.Event (grants)
        self._coalesce: dict = {}     # key -> id, while queued or running
        self._cursor = ("", "")
        self._executor_queue: list = []
        self._stopping = False
        self._resolve_lock = threading.Lock()
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
        if self._watchdog is not None and self._watchdog.is_degraded():
            status = self._watchdog.status()
            raise job_kinds.JobRefused(
                f"Resolve is unresponsive: {status['degraded_reason']} "
                f"(holder: {status['holder'] or 'none'}, "
                f"job: {status['job_id'] or 'none'}). "
                "Restart Resolve, then retry.")
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
                "qualification": int(shape["qualification"]),
                "coalesce_key": key, "owner": owner, "state": "queued",
                "description": job_kinds.activity_description(kind, params),
                "progress": str(params.get("progress") or ""),
                "interrupt_policy": shape.get("interrupt_policy", ""),
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
                qualification=shape["qualification"],
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

    def result(self, job_id: str, wait: float = 0.0) -> dict | None:
        deadline = time.time() + max(0.0, wait)
        with self._cond:
            while True:
                job = self.store.get(job_id)
                if job is None or job["state"] in TERMINAL or self._stopping:
                    return job
                remaining = deadline - time.time()
                if remaining <= 0:
                    return job
                self._cond.wait(min(remaining, 1.0))

    def cancel(self, job_id: str, reason: str = "cancelled") -> dict | None:
        with self._cond:
            if job_id in self._queued:
                del self._queued[job_id]
                self._forget(job_id)
                self._started.pop(job_id, None)
                self.store.update(job_id, state="cancelled",
                                  finished=time.time(), error=reason)
                self._cond.notify_all()
        return self.store.get(job_id)

    def request_cancel(self, job_id: str) -> dict | None:
        """Cancel queued work, or request a declared safe running stop."""
        with self._cond:
            if job_id in self._queued:
                return self.cancel(job_id)
            if job_id not in self._running:
                return self.store.get(job_id)
            job = self.store.get(job_id)
            if job is None:
                return None
            if not self.store.interrupt_policy(job_id):
                raise job_kinds.JobRefused(
                    f"{job['kind']} has no safe running cancellation path; "
                    "Resolve remains shielded until it finishes")
            from library.tools.resolved import cancellation
            cancellation.request(job_id)
            self._cond.notify_all()
            return job

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

    def activity(self) -> dict:
        """Compact display records for the native activity overlay."""
        with self._cond:
            records = [self.store.get(job.id)
                       for job in self._running.values()]
        active = [job for job in records if job is not None]
        return {
            "count": len(active),
            "jobs": [{
                "id": job["id"], "kind": job["kind"], "mode": job["mode"],
                "description": job["description"], "progress": job["progress"],
                "owner": job["owner"][:40], "params": {
                    "purpose": str(job["params"].get("purpose", ""))[:80]},
                "state": job["state"], "started": job["started"],
                "submitted": job["submitted"],
            } for job in active[:4]],
        }

    def report_progress(self, job_id: str, progress: str) -> dict:
        if (not isinstance(progress, str) or len(progress) > 20
                or "\n" in progress or "\r" in progress):
            raise job_kinds.JobRefused(
                "progress must be one line of at most 20 characters")
        with self._cond:
            if job_id not in self._running:
                raise job_kinds.JobRefused(
                    "progress can only be reported for a running job")
            job = self.store.get(job_id)
            if len(job["description"]) + len(progress) + 3 > 80:
                raise job_kinds.JobRefused(
                    "description and progress must fit in 80 characters")
            self.store.update(job_id, progress=progress)
            return self.store.get(job_id)

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
                    if self._running:
                        from library.tools.resolve_lock import (
                            _start_activity_overlay)
                        _start_activity_overlay()
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
        from library.tools import resolve_lock
        cancellation.begin(job["id"])
        activity_token = resolve_lock._activity_job_id.set(job["id"])
        try:
            with resolve_lock.resolve_lease(
                    f"ren-resolved {job['kind']} {job['id']}",
                    exclusive=job["mode"] == "exclusive"):
                if job["mode"] == "shared":
                    from library.tools.resolve_lock import _start_activity_overlay
                    _start_activity_overlay()
                if (job["qualification"]
                        and job["kind"] in
                        job_kinds.BORROWING_QUALIFICATION_KINDS):
                    # These fixed Qualification jobs borrow and restore
                    # the currently open project inside their whole-take
                    # lease. Their runners enforce the exact target.
                    job_kinds.refuse_qualification(job.get("project", ""))
                resolve = (self._resolve_handle()
                           if job["qualification"] or job["executed"]
                           else None)
                if job["executed"] and resolve is not None:
                    from library.tools.resolve_deadline import (
                        DeadlineProxy, deadline_proxy)
                    if not isinstance(resolve, DeadlineProxy):
                        resolve = deadline_proxy(
                            resolve, label=f"{job['kind']} {job['id']}")
                if (job["qualification"]
                        and job["kind"] not in
                        job_kinds.BORROWING_QUALIFICATION_KINDS):
                    found = job_kinds.open_project_name(resolve)
                    if found != job_kinds.QUALIFICATION_PROJECT:
                        raise job_kinds.JobRefused(
                            f"a qualification job needs "
                            f"{job_kinds.QUALIFICATION_PROJECT!r} open; "
                            f"Resolve has {found or 'no project'!r}")
                if not job["executed"]:
                    return "granted", None, ""
                return "done", job_kinds.run(job, resolve), ""
        except CancelRequested as exc:
            clear(job["id"])
            return "cancelled", exc.result, str(exc)
        except job_kinds.JobRefused as exc:
            return "rejected", exc.result, str(exc)
        except Exception as exc:                              # noqa: BLE001
            # The job failed; the broker does not. A dead connection is
            # reopened on the next job rather than trusted - EXCEPT inside
            # a project-switch window, where dropping and reconnecting
            # races Resolve's scripting-thread shutdown.
            from library.tools import resolve_switch_guard
            if resolve_switch_guard.connection_may_drop():
                self._resolve = None
            return "failed", None, f"{type(exc).__name__}: {exc}"
        finally:
            resolve_lock._activity_job_id.reset(activity_token)
            clear(job["id"])
            cancellation.end(job["id"])

    def _resolve_handle(self):
        from library.tools import resolve_switch_guard
        # Never handshake mid-switch: a fresh scripting client while
        # Resolve is loading a project is the crash site. Wait out the
        # settle first, then connect (or reuse the kept handle).
        resolve_switch_guard.wait_for_settle()
        with self._resolve_lock:
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
            from dataclasses import asdict

            from library.tools.resolve_lock import LEASE_FILENAME, holder
            socket_path = Path(self.server.server_address)
            lease = holder(socket_path.parent / LEASE_FILENAME)
            return {
                "ok": True,
                "pid": os.getpid(),
                "serving": True,
                "responsive": True,
                "busy": lease is not None,
                "lease": asdict(lease) if lease is not None else None,
            }
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
        if op == "activity":
            return broker.activity()
        if op == "progress":
            return {"job": broker.report_progress(
                request["id"], request.get("progress", ""))}
        if op == "cancel":
            return {"job": broker.request_cancel(request["id"])}
        if op == "list":
            return {"jobs": broker.store.recent(int(request.get("limit", 20))),
                    "running": broker.running()}
        if op == "health":
            from library.tools import resolve_watchdog
            resolve = broker._resolve_handle()
            return resolve_watchdog.health_status(resolve)
        if op == "post_restart_check":
            from library.tools import resolve_watchdog
            resolve = broker._resolve_handle()
            return resolve_watchdog.post_restart_check(resolve)
        if op == "watchdog_status":
            if broker._watchdog is None:
                return {"watchdog": False}
            return {"watchdog": True, **broker._watchdog.status()}
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
            _log_broker("ren-resolved: shutdown requested - stopping")
            broker.stop()
            return {"ok": True}
        return {"error": f"unknown op {op!r}"}


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(path: Path | None = None, connect=None) -> None:
    """Run the broker in the foreground until `shutdown` or a signal.

    The ping-check and the bind are atomic under a bind lock, so a
    second broker starting while the first is still binding finds it
    serving and refuses rather than orphaning it: two brokers on one
    socket path left the earlier one holding a socket nobody could
    reach (measured 2026-10-05, when a watcher respawned a branch
    broker within a second of a deliberate restart).
    """
    from library.tools.resolved import client
    path = Path(path or socket_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    bind_lock_path = path.parent / BIND_LOCK_NAME
    with open(bind_lock_path, "a+", encoding="utf-8") as bind_lock:
        if fcntl is not None:
            fcntl.flock(bind_lock.fileno(), fcntl.LOCK_EX)
        try:
            if client.ping(path) is not None:
                raise SystemExit(f"ren-resolved is already serving on {path}")
            if path.exists():
                path.unlink()           # a dead broker's socket: nobody answers it
            store = JobStore(path.parent / DB_NAME)
            broker = Broker(store, connect=connect)
            from library.tools import resolve_watchdog
            watchdog = resolve_watchdog.ResolveWatchdog(connect=broker._connect)
            broker._watchdog = watchdog
            watchdog.start()
            server = _Server(str(path), _Handler)
            server.broker = broker
            os.chmod(path, 0o600)
            _log_broker(
                "ren-resolved: serving on "
                f"{path} (pid {os.getpid()}, "
                f"engine {client._engine_root()})")

            # The shadow watcher lives inside the broker: it starts when the
            # broker starts, stops when the broker stops, and follows whatever
            # project is open. No one runs a command to start it.
            from library.tools import shadow_watcher
            from library.tools.resolve_lock import resolve_lease
            from library.tools.resolved import jobs as broker_jobs

            def project_name():
                # A read of Resolve, so under a shared instance lease like the
                # watchdog's probe: without one the handshake is refused, and a
                # handle another job opened would be read outside any lease.
                try:
                    with resolve_lease("shadow watcher: open project",
                                       exclusive=False, honor_captain=False,
                                       timeout=10.0):
                        return broker_jobs.open_project_name(broker._resolve_handle())
                except Exception:  # noqa: BLE001 - watcher fails closed to unknown
                    return None

            def project_folder(name):
                if not name:
                    return ""
                # The project's absolute root - where its project.yaml was found -
                # because the watcher files hand edits under it.
                from library.tools.project_registry import (
                    load_project_config,
                    project_config_paths,
                )
                for yaml_path in project_config_paths():
                    try:
                        config = load_project_config(yaml_path, migrate_format=False)
                    except (ValueError, FileNotFoundError):
                        continue
                    if config.resolve.project_name == name:
                        return str(yaml_path.parent.absolute())
                return ""

            # Jobs go straight onto this broker's queue, never through the socket
            # client: its `ensure` would start a broker from inside this one.
            watcher = shadow_watcher.ShadowWatcher(
                submit=lambda kind, params, owner="": broker.submit(
                    kind, params, owner=owner),
                result=broker.result)
            if shadow_watcher.hosting_enabled():
                watcher.start(project_name, project_folder)
        finally:
            if fcntl is not None:
                fcntl.flock(bind_lock.fileno(), fcntl.LOCK_UN)
    try:
        # A short poll interval: `shutdown` is answered at once, but the
        # serve loop notices on its next tick - and a client that was
        # asked to stop should be gone by the time `stop` returns.
        server.serve_forever(poll_interval=0.05)
    finally:
        _log_broker(f"ren-resolved: stopping (pid {os.getpid()})")
        watcher.stop()
        watchdog.stop()
        broker.stop()
        server.server_close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        store.close()
