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
"""

from __future__ import annotations

import json
import sys
from typing import Optional


def _reply(request_id: str, ok: bool, result=None, error: str = "",
           refused: bool = False, extra: Optional[dict] = None) -> dict:
    reply = {"id": request_id, "ok": ok}
    if ok:
        reply["result"] = result
    else:
        reply["error"] = error
        if refused:
            reply["refused"] = True
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
        return _reply(submit.get("id", ""), False, error=str(exc), refused=True)
    return _reply(submit.get("id", ""), True, result=shape)


# ── job: one broker job under the instance lease ─────────────────────

# The worker's Resolve connection, kept across requests as the Python
# broker keeps one. A dead connection is dropped on the next job rather
# than trusted - the broker does the same when a job raises.
_resolve_cache = None


def _connect_cached():
    global _resolve_cache
    if _resolve_cache is None:
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
    from library.tools.resolved import jobs as job_kinds

    kind = job["kind"]
    purpose = f"ren-resolved {kind} {job['id']}"
    exclusive = job["mode"] == "exclusive"
    try:
        with resolve_lock.resolve_lease(purpose, exclusive=exclusive):
            resolve = _connect_cached()
            if job["qualification"]:
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
    except job_kinds.JobRefused as exc:
        return _reply(job["id"], False, error=str(exc), refused=True,
                      result=exc.result)
    except Exception as exc:                              # noqa: BLE001
        # The job failed; the worker does not. A dead connection is
        # dropped rather than trusted - the next job reconnects.
        _drop_cached_connection()
        return _reply(job["id"], False,
                      error=f"{type(exc).__name__}: {exc}")


# ── probe / health / post-restart check ──────────────────────────────


def _execute_probe() -> dict:
    """The watchdog's bounded probe: connect and round trip, under a shared
    lease. Contention is not a hang - the daemon skips a probe it could not
    take the lease for."""
    from library.tools import resolve_lock, resolve_watchdog
    from library.tools.marker_feedback import connect_resolve

    try:
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
    captain's action.
    """
    from library.tools import resolve_lock, resolve_watchdog
    from library.tools.marker_feedback import connect_resolve

    with resolve_lock.resolve_lease(
            "resolve-post-restart-check", exclusive=True,
            honor_captain=False,
            timeout=resolve_watchdog.CONNECT_BUDGET_S):
        resolve = connect_resolve()
        return resolve_watchdog.post_restart_check(resolve)


# ── the loop ─────────────────────────────────────────────────────────


def execute(request: dict) -> dict:
    """Execute one request; never raises - the observation IS the answer."""
    request_id = request.get("id", "")
    kind = request.get("kind")
    try:
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
    """
    from library.tools.resolved import client
    client.mark_executor()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError as exc:
            response = {"id": "", "ok": False,
                        "error": f"bad request: {exc}"}
        else:
            response = execute(request)
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
