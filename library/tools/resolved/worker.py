"""The Resolve worker: typed requests in, observations out, over stdio.

The Rust ren-resolved daemon keeps ONE worker process and speaks this
protocol to it. The worker is deliberately thin - it takes the instance
lease, connects to Resolve through the existing Python code, runs the
request, and serializes the observation back. Scheduling, coalescing,
persistence and the watchdog's decision all live in the daemon; this
process only executes.

The wire format matches the daemon's socket protocol: one JSON object
per line. The request names what to do; the response carries the
observation:

    request   {"id": I, "kind": "prepare", "submit": {"kind": K,
               "params": {...}, "qualification": false}}
    request   {"id": I, "kind": "job", "job": {...}}
    request   {"id": I, "kind": "probe" | "health" | "post_restart_check"}
    request   {"id": I, "kind": "version"}   (the worker's code version;
                answers without touching Resolve or the shadow DB)
    response  {"id": I, "ok": true, "result": {...}}
    response  {"id": I, "ok": false, "error": "...", "refused": true,
               "result": {...}}

`prepare` validates a submission and returns its schedulable shape, or
refuses it - the daemon never queues a job `prepare` would refuse.
`job` executes one broker job under the instance lease. `probe` is the
watchdog's bounded connect-and-round-trip; it answers `ok: true` where
Resolve is responsive and `ok: false` naming the error where it is not,
with `contention: true` where another writer holds the lease (the daemon
skips that probe - contention is not a hang). `health` separates
`connected` from `responsive`; `post_restart_check` verifies the open
project's timelines after a restart.

The worker keeps its Resolve connection across requests, as the Python
broker does; a `probe` reconnects, because the watchdog must not trust a
connection a restart has invalidated.

THE WORKER DOES NOT OUTLIVE ITS BROKER. The worker holds the instance
lease while it works, and the instance is the one Resolve: a worker
left running after its broker stopped holds the one Resolve against
every later writer, which then times out waiting for an instance nobody
is using (measured 2026-10-20: an orphan worker held the instance flock
for 3h10m after `ren resolved stop`, and every writer refused with
"timed out waiting for Resolve"). The daemon names its own pid in the
worker's environment, and the worker ends when that pid is gone - giving
the instance back first, and leaving the lease record behind as the
stale record the status reports.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from typing import Optional

from library.tools import refusal_taxonomy


#: The broker's own pid, in the environment of every worker it starts.
#: The daemon is the worker's owner: it schedules the work, and it is
#: the thing that dies when the broker stops. A worker that outlives it
#: keeps the one Resolve's lock with it - the daemon's own shutdown
#: races its detached threads, so the worker's process outlived the
#: broker that started it and every later writer timed out waiting for
#: an instance nobody was using (measured 2026-10-20: an orphan worker
#: held the flock for 3h10m after `ren resolved stop`).
BROKER_PID_ENV = "REN_RESOLVED_BROKER_PID"

#: How often the worker checks that its broker is still there. A second
#: is short enough that the lock is not held long after the broker goes,
#: and long enough that the check costs nothing.
BROKER_WATCH_SECONDS = 1.0


def broker_pid() -> int:
    """The pid of the broker this worker belongs to, or 0 where none.

    0 means the worker was not started by a broker (an operator running
    it by hand), and then there is no broker to lose: the worker stays.
    """
    raw = (os.environ.get(BROKER_PID_ENV) or "").strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def broker_alive(pid: int) -> bool:
    """Whether the broker is still running.

    A pid this process cannot signal is still alive (it belongs to
    somebody else); a pid nobody answers for is gone.
    """
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _release_the_instance() -> None:
    """Give the one Resolve back: the lock this process holds.

    The worker holds the instance lease while it works, so a worker
    that outlives its broker holds the one Resolve against every later
    writer - and every later writer times out waiting for an instance
    nobody is using (measured 2026-10-20: an orphan worker held the
    instance flock for 3h10m after `ren resolved stop`).

    The lease RECORD stays: the worker did not get to clean up, and a
    record with no flock behind it is what the status reports as stale.
    """
    from library.tools import resolve_lock
    try:
        resolve_lock.release_instance()
    except Exception:  # noqa: BLE001 - the exit is the point
        pass


def _release_and_exit() -> None:
    """Give the instance back and leave.

    The kernel drops the flock when this process goes, so the instance
    is free the moment the worker is gone. The lease RECORD stays: the
    worker did not get to clean up, and a record with no flock behind it
    is exactly what the status reports as stale. Nothing here unlinks
    it - another writer may already be publishing its own.
    """
    _release_the_instance()
    try:
        sys.stderr.write(
            "ren-resolved worker: the broker is gone - releasing the "
            "Resolve instance and exiting\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 - the exit is the point
        pass
    os._exit(0)


def _watch_the_broker(pid: int) -> None:
    """Exit when the broker is gone: a worker must not outlive its broker.

    The worker holds the instance lease while it works, so a worker that
    outlives its broker holds the one Resolve against everyone else -
    and the broker is exactly what died. This thread is the other half
    of the daemon's own shutdown: the daemon kills an idle worker, and
    this catches the worker that was inside a job when the broker went.
    """
    while True:
        time.sleep(BROKER_WATCH_SECONDS)
        if broker_alive(pid):
            continue
        _release_and_exit()


def _reply(request_id: str, ok: bool, result=None, error: str = "",
           refused: bool = False, extra: Optional[dict] = None,
           blocker: str = "", next_action: str = "") -> dict:
    reply = {"id": request_id, "ok": ok}
    if ok:
        reply["result"] = result
    else:
        reply["error"] = error
        if refused:
            reply["refused"] = True
            # PROD-003: a refusal says what KIND of block it was and
            # what the caller can do about it, not only that it happened.
            if blocker:
                reply["blocker"] = blocker
                reply["blocking_reason"] = refusal_taxonomy.plain_reason(
                    blocker)
            if next_action:
                reply["next_action"] = next_action
        if result is not None:
            reply["result"] = result
    if extra:
        reply.update(extra)
    return reply


# ── prepare: the submission gate ─────────────────────────────────────


def _execute_prepare(submit: dict) -> dict:
    """Validate one submission; return its schedulable shape or refuse.

    This is `jobs.prepare` - the ONE validation a submission meets, so a
    non-conforming job is never queued. It stays in Python: the capability
    registry and the resolve-axi parser it consults are Python data, and
    porting them would drift the gate.
    """
    from library.tools.resolved import jobs as job_kinds

    try:
        shape = job_kinds.prepare(
            submit["kind"], submit["params"],
            qualification=submit.get("qualification", False))
    except job_kinds.JobRefused as exc:
        # The refusal's message, without the class name - the Python
        # daemon's dispatch answers `{"error": str(exc), "refused": true}`.
        return _reply(submit.get("id", ""), False, error=str(exc),
                      refused=True, blocker=exc.blocker,
                      next_action=exc.next_action)
    shape["description"] = job_kinds.activity_description(
        submit["kind"], submit["params"])
    shape["progress"] = str(submit["params"].get("progress") or "")
    return _reply(submit.get("id", ""), True, result=shape)


# ── job: one broker job under the instance lease ─────────────────────

# The worker's Resolve connection, kept across requests as the Python
# broker keeps one. A dead connection is dropped on the next job rather
# than trusted - the broker does the same when a job raises.
_resolve_cache = None


def _connect_cached():
    global _resolve_cache
    if _resolve_cache is None:
        from library.tools import resolve_switch_guard
        # Same crash site as the broker: never handshake mid-switch.
        resolve_switch_guard.wait_for_settle()
        from library.tools.marker_feedback import connect_resolve
        _resolve_cache = connect_resolve()
    return _resolve_cache


def _drop_cached_connection():
    global _resolve_cache
    _resolve_cache = None


def _execute_job(job: dict) -> dict:
    """Run one broker job under the instance lease; return the observation.

    The lease is the daemon's, held for the whole job - connection
    included: the scripting handshake is refused outside a lease, and a
    connection opened between leases is a client nobody scheduled. The
    worker takes it here because the daemon is a Rust process and the
    flock lives in Python.
    """
    from library.tools import resolve_lock
    from library.tools.resolved import cancellation
    from library.tools.resolved import jobs as job_kinds

    kind = job["kind"]
    purpose = f"ren-resolved {kind} {job['id']}"
    exclusive = job["mode"] == "exclusive"
    cancellation.begin(job["id"])
    try:
        with resolve_lock.resolve_lease(purpose, exclusive=exclusive):
            resolve = _connect_cached()
            if job["executed"]:
                from library.tools.resolve_deadline import (
                    DeadlineProxy, deadline_proxy)
                if not isinstance(resolve, DeadlineProxy):
                    resolve = deadline_proxy(
                        resolve, label=f"{kind} {job['id']}")
            if job["qualification"]:
                if kind in job_kinds.BORROWING_QUALIFICATION_KINDS:
                    # These fixed Qualification jobs borrow and restore
                    # the currently open project inside their whole-take
                    # lease, mirroring the Python daemon path in
                    # server.py: their runners enforce the exact target.
                    job_kinds.refuse_qualification(job.get("project", ""))
                else:
                    found = job_kinds.open_project_name(resolve)
                    if found != job_kinds.QUALIFICATION_PROJECT:
                        return _reply(job["id"], False, error=(
                            f"a qualification job needs "
                            f"{job_kinds.QUALIFICATION_PROJECT!r} open; Resolve "
                            f"has {found or 'no project'!r}"), refused=True)
            if not job["executed"]:
                # A grant: checked, and the caller takes Resolve now that
                # the broker has let go of its turn.
                return _reply(job["id"], True)
            result = job_kinds.run(job, resolve)
            return _reply(job["id"], True, result=result)
    except cancellation.CancelRequested as exc:
        return _reply(job["id"], False, error=str(exc),
                      result=exc.result,
                      extra={"cancelled": True})
    except job_kinds.JobRefused as exc:
        return _reply(job["id"], False, error=str(exc), refused=True,
                      result=exc.result, blocker=exc.blocker,
                      next_action=exc.next_action)
    except Exception as exc:                              # noqa: BLE001
        # The job failed; the worker does not. A dead connection is
        # dropped rather than trusted - except inside a project-switch
        # window, where dropping mid-switch races scripting-thread
        # shutdown (2026-10-06): keep it for the post-settle retry.
        from library.tools import resolve_switch_guard
        if resolve_switch_guard.connection_may_drop():
            _drop_cached_connection()
        return _reply(job["id"], False,
                      error=f"{type(exc).__name__}: {exc}")
    finally:
        cancellation.clear(job["id"])
        cancellation.end(job["id"])


# ── probe / health / post-restart check ──────────────────────────────


def _execute_probe() -> dict:
    """The watchdog's bounded probe: connect and round trip, under a shared
    lease. Contention is not a hang - the daemon skips a probe it could not
    take the lease for."""
    from library.tools import resolve_lock, resolve_watchdog
    from library.tools.marker_feedback import connect_resolve

    try:
        from library.tools import resolve_switch_guard
        resolve_switch_guard.wait_for_settle()
        with resolve_lock.resolve_lease(
                "resolve-watchdog probe", exclusive=False,
                honor_captain=False,
                timeout=resolve_watchdog.CONNECT_BUDGET_S):
            resolve = resolve_watchdog._deadline_call(
                "watchdog connect", connect_resolve,
                timeout_s=resolve_watchdog.CONNECT_BUDGET_S)
            resolve_watchdog._deadline_call(
                "watchdog probe",
                lambda: resolve.GetProjectManager().GetCurrentProject(),
                timeout_s=resolve_watchdog.HEALTH_ROUND_TRIP_S)
            return {"connected": True, "responsive": True}
    except resolve_lock.ResolveBusy as exc:
        # Contention, not a hang: the daemon skips this probe.
        return {"error": f"ResolveBusy: {exc}", "contention": True}
    except Exception as exc:                              # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _execute_health() -> dict:
    """Separate `connected` from `responsive` with a short round trip.

    Takes a shared instance lease, as the watchdog probe does: a health
    check is a read, and the scripting handshake refuses outside a lease.
    `honor_captain=False` - a health check is not the captain's action.
    """
    from library.tools import resolve_lock, resolve_watchdog
    from library.tools.marker_feedback import connect_resolve

    try:
        from library.tools import resolve_switch_guard
        resolve_switch_guard.wait_for_settle()
        with resolve_lock.resolve_lease(
                "resolve-health probe", exclusive=False,
                honor_captain=False,
                timeout=resolve_watchdog.CONNECT_BUDGET_S):
            try:
                resolve = resolve_watchdog._deadline_call(
                    "health connect", connect_resolve,
                    timeout_s=resolve_watchdog.CONNECT_BUDGET_S)
            except Exception:                             # noqa: BLE001
                return {"connected": False, "responsive": False}
            try:
                resolve_watchdog._deadline_call(
                    "health round trip",
                    lambda: resolve.GetProjectManager().GetCurrentProject(),
                    timeout_s=resolve_watchdog.HEALTH_ROUND_TRIP_S)
                return {"connected": True, "responsive": True}
            except Exception:                             # noqa: BLE001
                return {"connected": True, "responsive": False}
    except resolve_lock.ResolveBusy:
        # Contention, not a hang: report as not connected this instant.
        return {"connected": False, "responsive": False}


def _execute_post_restart_check() -> dict:
    """A short read-only project/timeline/count check after a restart.

    Takes an EXCLUSIVE instance lease: the check moves the cursor through
    every known timeline (`cursor_excursion`), which is a write even though
    it only reads. `honor_captain=False` - a post-restart check is not the
    captain's action. The connect is bounded, as the health check's is:
    an unbounded connect hung this check past the client's timeout while
    `health` answered (2026-10-11); the check itself bounds every call
    after the connect.
    """
    from library.tools import resolve_lock, resolve_watchdog
    from library.tools.marker_feedback import connect_resolve

    with resolve_lock.resolve_lease(
            "resolve-post-restart-check", exclusive=True,
            honor_captain=False,
            timeout=resolve_watchdog.CONNECT_BUDGET_S):
        try:
            resolve = resolve_watchdog._deadline_call(
                "post-restart connect", connect_resolve,
                timeout_s=resolve_watchdog.CONNECT_BUDGET_S)
        except Exception as exc:                           # noqa: BLE001
            return {"ok": False, "project": "", "timeline": "",
                    "timeline_count": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                    "timelines_intact": [], "timelines_stale": []}
        return resolve_watchdog.post_restart_check(resolve)


# ── version: the worker's report ───────────────────────────────────


def _execute_version() -> dict:
    """This worker's code version, without touching Resolve or the DB.

    The broker (or a supervisor) compares `shadow_schema_version`
    against the shadow DB's version and refuses - or restarts on
    current code - a worker that predates the schema it is about to
    write, instead of failing its writes (2026-10-11: a pre-A3 worker
    failed every first-record shadow write machine-wide while
    re-reads looked healthy).
    """
    from library.tools import timeline_shadow

    return {"shadow_schema_version": timeline_shadow.worker_schema_version()}


# ── the loop ─────────────────────────────────────────────────────────


def execute(request: dict) -> dict:
    """Execute one request; never raises - the observation IS the answer."""
    request_id = request.get("id", "")
    kind = request.get("kind")
    try:
        if kind == "version":
            return _reply(request_id, True, result=_execute_version())
        if kind == "prepare":
            return _execute_prepare(request["submit"])
        if kind == "job":
            return _execute_job(request["job"])
        if kind == "probe":
            return _reply(request_id, True, result=_execute_probe())
        if kind == "health":
            return _reply(request_id, True, result=_execute_health())
        if kind == "post_restart_check":
            return _reply(request_id, True,
                          result=_execute_post_restart_check())
        return _reply(request_id, False, error=f"unknown worker kind {kind!r}")
    except Exception as exc:                              # noqa: BLE001
        return _reply(request_id, False,
                      error=f"{type(exc).__name__}: {exc}")


def main() -> int:
    """Requests on stdin, responses on stdout, until the daemon closes.

    The daemon keeps this process alive and speaks one request-response
    exchange at a time; a request that kills the worker (a segfault in the
    scripting bridge, say) is restarted by the daemon for the next one.

    The worker marks itself the broker's executor, as the Python broker's
    execute loop does: `resolve_axi.main` asks `client.serving()` whether
    a broker is serving, and a worker that does not mark itself would
    route every `resolve_axi` job back through the broker - a loop. With
    the mark, `serving()` is False and the command runs here.

    STDOUT IS THE PROTOCOL. The daemon reads ONE LINE and parses it as
    the answer, so a job that prints - the touch-up path says its gate
    class and its cost out loud - put a non-JSON line where the answer
    belonged and the job failed with "the worker's answer was not JSON".
    A job's prints belong on stderr; the receipt already carries what
    they said.

    THE WORKER DOES NOT OUTLIVE ITS BROKER. The daemon says which pid it
    is (`BROKER_PID_ENV`), and a watcher thread ends the worker when that
    pid is gone - the worker holds the instance lease while it works, so
    one that outlives its broker holds the one Resolve against everyone
    else. The check also runs before every request, so a worker whose
    broker died between two jobs does not start another one.
    """
    from library.tools.resolved import client
    client.mark_executor()
    pid = broker_pid()
    if pid:
        threading.Thread(target=_watch_the_broker, args=(pid,),
                         daemon=True).start()
    protocol_out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if pid and not broker_alive(pid):
            # The broker is gone: taking this lease would hold the one
            # Resolve against everyone else, for a broker that will
            # never answer for it.
            _release_and_exit()
        try:
            request = json.loads(line)
        except ValueError as exc:
            response = {"id": "", "ok": False,
                        "error": f"bad request: {exc}"}
        else:
            with contextlib.redirect_stdout(sys.stderr):
                response = execute(request)
        protocol_out.write(json.dumps(response) + "\n")
        protocol_out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
