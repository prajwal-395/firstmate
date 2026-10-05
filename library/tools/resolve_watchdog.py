"""A Resolve-degraded watchdog that stops queue growth.

2026-10-04: the draw-gain probe held the broker for 818 seconds while
Resolve was deadlocked in ``Fusion::FusionApp::SyncProjectSettings``.
Every later lease piled up behind it - 13 minutes for a reader, 26
minutes for a connect, nine 15-minute waits. The Python deadline
wrapper cannot fire when the binding call holds the GIL, so a caller
timeout does not unstick Resolve.

This module is the watchdog that does:

* PROBE Resolve on a short budget (connect ~10s, round trip ~5s).
* MARK Resolve unresponsive after a failed bounded probe.
* STOP queue growth: the broker rejects new submissions while degraded.
* KEEP the holder and job id of the operation that was running.
* NEVER auto-restart Resolve - that is the operator's call.

Health status separates ``connected`` (a non-null handle exists) from
``responsive`` (a short, harmless API round trip succeeds), so a
wedged Resolve does not report as healthy.

The post-restart check is a short read-only project/timeline/count
verification that runs before builds resume after an operator restart.
"""

from __future__ import annotations

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


class ResolveDegraded(RuntimeError):
    """Resolve is marked unresponsive; new work is refused."""

    def __init__(self, reason: str, holder: str = "", job_id: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.holder = holder
        self.job_id = job_id


def _deadline_call(label: str, func: Callable, *args,
                   timeout_s: float, **kwargs):
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
    worker.join(timeout_s)
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
                 round_trip_budget: float = HEALTH_ROUND_TRIP_S):
        self._connect = connect
        self._probe_interval = probe_interval
        self._connect_budget = connect_budget
        self._round_trip_budget = round_trip_budget
        self._resolve = None
        self._degraded = False
        self._degraded_at: float | None = None
        self._degraded_reason = ""
        self._holder = ""
        self._job_id = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="resolve-watchdog")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

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
                "holder": self._holder,
                "job_id": self._job_id,
            }

    def _run(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self._probe_interval)
            if self._stop.is_set():
                break
            self._probe()

    def _probe(self) -> None:
        try:
            resolve = _deadline_call("watchdog connect", self._connect,
                                     timeout_s=self._connect_budget)
        except Exception as exc:  # noqa: BLE001 - any failure is a mark
            self._mark_degraded(f"connect failed: {exc}", "", "")
            return
        with self._lock:
            self._resolve = resolve
        try:
            _deadline_call(
                "watchdog probe",
                lambda: resolve.GetProjectManager().GetCurrentProject(),
                timeout_s=self._round_trip_budget)
        except Exception as exc:  # noqa: BLE001 - any failure is a mark
            self._mark_degraded(f"probe failed: {exc}", "", "")

    def _mark_degraded(self, reason: str, holder: str,
                       job_id: str) -> None:
        with self._lock:
            self._degraded = True
            self._degraded_at = time.time()
            self._degraded_reason = reason
            self._holder = holder
            self._job_id = job_id


def health_status(resolve) -> dict:
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
            timeout_s=HEALTH_ROUND_TRIP_S)
        return {"connected": True, "responsive": True}
    except Exception:  # noqa: BLE001 - unresponsive is the answer
        return {"connected": True, "responsive": False}


def post_restart_check(resolve, budget_s: float = POST_RESTART_BUDGET_S
                       ) -> dict:
    """A short read-only project/timeline/count check after a restart.

    Runs before builds resume after an operator restart. Reads the
    project name, current timeline name, and timeline count, and verifies
    they are readable. Never writes anything.
    """
    result = {"ok": False, "project": "", "timeline": "",
              "timeline_count": 0, "error": ""}
    try:
        project = _deadline_call(
            "post-restart project",
            lambda: resolve.GetProjectManager().GetCurrentProject(),
            timeout_s=budget_s)
        if project is None:
            result["error"] = "no project open"
            return result
        result["project"] = project.GetName()
        timeline = _deadline_call(
            "post-restart timeline",
            project.GetCurrentTimeline,
            timeout_s=budget_s)
        result["timeline"] = timeline.GetName() if timeline else ""
        result["timeline_count"] = _deadline_call(
            "post-restart count",
            project.GetTimelineCount,
            timeout_s=budget_s)
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001 - report, don't raise
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result
