"""A Resolve-degraded watchdog that stops queue growth.

2026-10-04: the draw-gain probe held the broker for 818 seconds while
Resolve was deadlocked in ``Fusion::FusionApp::SyncProjectSettings``.
Every later lease piled up behind it - 13 minutes for a reader, 26
minutes for a connect, nine 15-minute waits. The Python deadline
wrapper cannot fire when the binding call holds the GIL, so a caller
timeout does not unstick Resolve.

This module is the watchdog that does:

* PROBE Resolve on a short budget (connect ~10s, round trip ~5s),
  under a shared instance lease - the scripting handshake refuses
  outside one, and a probe that cannot connect is not evidence that
  Resolve is down.
* MARK Resolve unresponsive after a failed bounded probe.
* STOP queue growth: the broker rejects new submissions while degraded.
* KEEP the holder and job id of the operation that was running.
* RECOVER on the next successful bounded probe, so a transient hang does
  not latch the broker shut until an operator restarts it.
* NEVER auto-restart Resolve - that is the operator's call.

Health status separates ``connected`` (a non-null handle exists) from
``responsive`` (a short, harmless API round trip succeeds), so a
wedged Resolve does not report as healthy.

The post-restart check is a short read-only project/timeline/count
verification that runs before builds resume after an operator restart.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable

#: How long a connect may take before it is a hang. Successful connects
#: took 0.02-0.03s in the 2026-10-04 incident; 10s is generous.
CONNECT_BUDGET_S = 10.0

#: How long a draw-gain probe may take. Successful probes took 1-2.5s;
#: the 2026-10-04 hang held for 818s.
DRAW_GAIN_BUDGET_S = 10.0

#: How long a health round trip may take. A live return is sub-second.
HEALTH_ROUND_TRIP_S = 5.0

#: How often the watchdog probes Resolve while idle.
PROBE_INTERVAL_S = 30.0

#: How long the post-restart check may take.
POST_RESTART_BUDGET_S = 10.0

#: How long each timeline content check may take.
POST_RESTART_PER_TIMELINE_S = 1.0


class ResolveDegraded(RuntimeError):
    """Resolve is marked unresponsive; new work is refused."""

    def __init__(self, reason: str, holder: str = "", job_id: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.holder = holder
        self.job_id = job_id


def _deadline_call(label: str, func: Callable, *args,
                   timeout_s: float,
                   deadline_waiter: Callable[[threading.Thread, float], None]
                   | None = None,
                   **kwargs):
    """Run `func(*args, **kwargs)` with a deadline.

    Returns the value, or raises `ResolveCallTimeout` naming the label.
    The worker is a daemon thread, so it never blocks interpreter exit.
    """
    box: dict = {}

    def _run():
        try:
            box["value"] = func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - cross the thread boundary
            box["error"] = exc

    worker = threading.Thread(target=_run,
                              name=f"resolve-watchdog:{label}",
                              daemon=True)
    worker.start()
    (deadline_waiter or (lambda thread, timeout: thread.join(timeout)))(
        worker, timeout_s)
    if worker.is_alive():
        from library.tools.resolve_deadline import ResolveCallTimeout
        raise ResolveCallTimeout(
            f"{label} did not return within {timeout_s:g}s - Resolve is "
            f"not answering scripting calls")
    if "error" in box:
        raise box["error"]
    return box.get("value")


class ResolveWatchdog:
    """Marks Resolve unresponsive after a failed bounded probe.

    Runs a background thread that probes Resolve on a short budget. When
    a probe fails, the watchdog marks Resolve degraded and the broker
    rejects new submissions. The watchdog never auto-restartes Resolve.
    """

    def __init__(self, connect: Callable[[], object],
                 probe_interval: float = PROBE_INTERVAL_S,
                 connect_budget: float = CONNECT_BUDGET_S,
                 round_trip_budget: float = HEALTH_ROUND_TRIP_S,
                 *, clock: Callable[[], float] = time.monotonic,
                 probe_scheduler: Callable[[threading.Event, float], bool]
                 | None = None,
                 deadline_waiter: Callable[[threading.Thread, float], None]
                 | None = None):
        self._connect = connect
        self._probe_interval = probe_interval
        self._connect_budget = connect_budget
        self._round_trip_budget = round_trip_budget
        self._clock = clock
        self._probe_scheduler = probe_scheduler or (
            lambda stop, interval: stop.wait(interval))
        self._deadline_waiter = deadline_waiter
        self._resolve = None
        self._degraded = False
        self._degraded_at: float | None = None
        self._degraded_reason = ""
        self._recovered_at: float | None = None
        self._holder = ""
        self._job_id = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="resolve-watchdog")

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        """Signal the probe loop to exit and wait for it to finish.

        The probe holds a SHARED instance lease while it connects, and
        the holder accounting is process-wide: a probe thread still
        inside its lease when this returns leaves `_depth` non-zero, so
        a later EXCLUSIVE acquisition in the same process refuses with
        `ResolveBusy` ("holds SHARED and is asking for EXCLUSIVE").
        Joining here releases the lease in teardown at its source -
        the next test or broker shutdown never inherits it. An
        in-flight probe is bounded by its connect and round-trip
        budgets, so the join is bounded too; an idle probe wakes from
        its wait at once and exits.
        """
        self._stop.set()
        thread = self._thread
        if (thread is not None and thread.ident is not None
                and threading.current_thread() is not thread):
            thread.join(timeout=timeout)

    def is_degraded(self) -> bool:
        with self._lock:
            return self._degraded

    def status(self) -> dict:
        """The watchdog's current state, for health reporting."""
        with self._lock:
            return {
                "connected": self._resolve is not None,
                "responsive": not self._degraded,
                "degraded": self._degraded,
                "degraded_at": self._degraded_at,
                "degraded_reason": self._degraded_reason,
                "recovered_at": self._recovered_at,
                "holder": self._holder,
                "job_id": self._job_id,
            }

    def _run(self) -> None:
        while not self._stop.is_set():
            self._probe_scheduler(self._stop, self._probe_interval)
            if self._stop.is_set():
                break
            self._probe()

    def _probe(self) -> None:
        """Connect and round-trip under a shared instance lease.

        `connect_resolve` reaches Resolve through the guarded scripting
        handshake in `resolve_locale`, which refuses outside the instance
        lease. The watchdog probes from its own thread, so it takes the
        lease itself: without it an idle broker's first probe fails and
        marks Resolve degraded permanently, refusing every submission. A
        shared lease - the probe only reads - and `honor_captain=False` -
        a health check is not the captain's action and must not wait on
        his signal. A lease another writer holds is not a hang: the
        refusal is skipped and the next probe retries.
        """
        from library.tools.resolve_lock import ResolveBusy, resolve_lease
        try:
            with resolve_lease("resolve-watchdog probe", exclusive=False,
                               honor_captain=False,
                               timeout=self._connect_budget):
                self._probe_connected()
        except ResolveBusy:
            pass      # contention is not a hang; the next probe retries
        except Exception as exc:  # noqa: BLE001 - any failure is a mark
            self._mark_degraded(f"probe failed: {exc}", "", "")

    def _probe_connected(self) -> None:
        try:
            resolve = _deadline_call("watchdog connect", self._connect,
                                     timeout_s=self._connect_budget,
                                     deadline_waiter=self._deadline_waiter)
        except Exception as exc:  # noqa: BLE001 - any failure is a mark
            self._mark_degraded(f"connect failed: {exc}", "", "")
            return
        with self._lock:
            self._resolve = resolve
        try:
            _deadline_call(
                "watchdog probe",
                lambda: resolve.GetProjectManager().GetCurrentProject(),
                timeout_s=self._round_trip_budget,
                deadline_waiter=self._deadline_waiter)
        except Exception as exc:  # noqa: BLE001 - any failure is a mark
            self._mark_degraded(f"probe failed: {exc}", "", "")
            return
        if self.is_degraded():
            self._clear_degraded()

    def _mark_degraded(self, reason: str, holder: str,
                       job_id: str) -> None:
        with self._lock:
            self._degraded = True
            self._degraded_at = self._clock()
            self._degraded_reason = reason
            self._holder = holder
            self._job_id = job_id

    def _clear_degraded(self) -> None:
        """A bounded probe succeeded while degraded: Resolve has recovered.

        Clears the degraded mark so the broker resumes accepting
        submissions. The holder and job id of the original failure are
        kept for post-mortem; nothing about Resolve is restarted,
        killed or reconfigured.
        """
        with self._lock:
            self._degraded = False
            self._degraded_at = None
            self._degraded_reason = ""
            self._recovered_at = self._clock()
            holder = self._holder
            job_id = self._job_id
        print(f"resolve-watchdog: recovered - Resolve is responsive again "
              f"(failed holder={holder!r}, job_id={job_id!r})",
              file=sys.stderr)


def health_status(
        resolve,
        deadline_waiter: Callable[[threading.Thread, float], None]
        | None = None) -> dict:
    """Separate `connected` from `responsive` with a short round trip.

    `connected` is True where a non-null handle exists. `responsive` is
    True where a short, harmless Resolve API round trip succeeds. A
    wedged Resolve is connected but not responsive.
    """
    if resolve is None:
        return {"connected": False, "responsive": False}
    try:
        _deadline_call(
            "health round trip",
            lambda: resolve.GetProjectManager().GetCurrentProject(),
            timeout_s=HEALTH_ROUND_TRIP_S,
            deadline_waiter=deadline_waiter)
        return {"connected": True, "responsive": True}
    except Exception:  # noqa: BLE001 - unresponsive is the answer
        return {"connected": True, "responsive": False}


def post_restart_check(resolve, budget_s: float = POST_RESTART_BUDGET_S,
                       per_timeline_s: float = POST_RESTART_PER_TIMELINE_S,
                       store=None,
                       deadline_waiter: Callable[[threading.Thread, float], None]
                       | None = None) -> dict:
    """A short read-only project/timeline/count check after a restart.

    Runs before builds resume after an operator restart. Reads the
    project name, current timeline name, and timeline count, and verifies
    they are readable. Then, for each timeline the shadow store knows,
    does a quick live read and compares the hash against the recorded
    head hash. A mismatch is reported as a stale shadow, not a failure -
    the shadow is re-observed.

    Never writes anything to Resolve.
    """
    result = {"ok": False, "project": "", "timeline": "",
              "timeline_count": 0, "error": "",
              "timelines_intact": [], "timelines_stale": []}
    try:
        project = _deadline_call(
            "post-restart project",
            lambda: resolve.GetProjectManager().GetCurrentProject(),
            timeout_s=budget_s, deadline_waiter=deadline_waiter)
        if project is None:
            result["error"] = "no project open"
            return result
        result["project"] = project.GetName()
        timeline = _deadline_call(
            "post-restart timeline",
            project.GetCurrentTimeline,
            timeout_s=budget_s, deadline_waiter=deadline_waiter)
        result["timeline"] = timeline.GetName() if timeline else ""
        result["timeline_count"] = _deadline_call(
            "post-restart count",
            project.GetTimelineCount,
            timeout_s=budget_s, deadline_waiter=deadline_waiter)
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001 - report, don't raise
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    _verify_timeline_contents(project, result, per_timeline_s, store)
    return result


def _verify_timeline_contents(project, result: dict,
                              per_timeline_s: float, store) -> None:
    """Compare each known timeline's live contents against the shadow store.

    For each timeline the shadow store knows about in this project,
    do a quick live read and compare the hash against the recorded head
    hash. A mismatch is reported as a stale shadow and the shadow is
    re-observed. Read-only with respect to Resolve.
    """
    from library.tools.timeline_shadow import (
        OBSERVED,
        ShadowStore,
        read_live,
        snapshot_hash,
    )

    store = store or ShadowStore()
    project_name = result["project"]

    try:
        heads = store.heads(project_name)
    except Exception:  # noqa: BLE001 - shadow store unavailable
        return

    if not heads:
        return

    from library.tools.resolve_lock import cursor_excursion

    for head in heads:
        timeline = _find_timeline_by_id(project, head.timeline_id)
        if timeline is None:
            result["timelines_stale"].append({
                "timeline": head.timeline_name,
                "error": "timeline not found",
            })
            continue

        try:
            with cursor_excursion(project, timeline, "post-restart check",
                                  timeout=per_timeline_s,
                                  honor_captain=False):
                snapshot = read_live(project, timeline)
            live_hash = snapshot_hash(snapshot)
            if live_hash == head.hash:
                result["timelines_intact"].append(head.timeline_name)
            else:
                result["timelines_stale"].append({
                    "timeline": head.timeline_name,
                    "expected_hash": head.hash,
                    "actual_hash": live_hash,
                })
                store.record(
                    project=project_name,
                    timeline_id=head.timeline_id,
                    timeline_name=head.timeline_name,
                    snapshot=snapshot,
                    source=OBSERVED,
                    expected_head=head.generation,
                )
        except Exception as exc:  # noqa: BLE001 - report, don't raise
            result["timelines_stale"].append({
                "timeline": head.timeline_name,
                "error": f"{type(exc).__name__}: {exc}",
            })


def _find_timeline_by_id(project, timeline_id: str):
    """Find a timeline by its unique ID."""
    try:
        count = project.GetTimelineCount()
    except Exception:  # noqa: BLE001
        return None
    for i in range(1, count + 1):
        try:
            timeline = project.GetTimelineByIndex(i)
        except Exception:  # noqa: BLE001, S112 - one missing timeline is isolated
            continue
        if timeline and timeline.GetUniqueId() == timeline_id:
            return timeline
    return None
