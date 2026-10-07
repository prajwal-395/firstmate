"""Project-switch settle: the quiet window around a Resolve LoadProject.

On 2026-10-06 computer-use ran ~6 LoadProjects in ~10 minutes and on the
third its scripting handles died (`GetProjectManager` -> None) seconds
before Resolve crashed in scripting-thread shutdown (EXC_GUARD,
fusionscript.so `Fusion::Platform::WaitAllThreads`). A project load is
asynchronous: the API returns while Resolve is still switching, so a
scripting call - or a fresh scripting handshake, or dropping the old
connection - inside the window races the switch.

The discipline: every in-repo `LoadProject` goes through
`qualification_project.switch_project`, which checks the project-manager
handle is live before and after, refuses on a dead handle instead of
proceeding, sleeps at least `SETTLE_SECONDS`, and stamps this guard.
The broker worker and the driver consult `connection_may_drop` before
dropping a cached scripting connection: inside the window the answer is
no - reconnecting mid-switch is the crash site.

`SETTLE_SECONDS` is a floor, not a guarantee: 2.0s exceeds the measured
scripting handshake (0.02-0.03s, `resolve_watchdog.CONNECT_BUDGET_S`)
and sits below the measured page-repaint wait (4s in
`computer_use/window_delete.py`) and the health round trip (5s in
`resolve_watchdog.HEALTH_ROUND_TRIP_S`). A switch that has not settled
by then is refused by the live-handle readback, not by a longer sleep.
"""

from __future__ import annotations

import threading
import time

#: Minimum quiet seconds after a LoadProject before scripting resumes.
SETTLE_SECONDS = 2.0

_lock = threading.Lock()
_last_switch_monotonic: float | None = None


def note_project_switch() -> None:
    """Stamp now as the last LoadProject return. Called by the switch
    helper only."""
    global _last_switch_monotonic
    with _lock:
        _last_switch_monotonic = time.monotonic()


def seconds_since_switch() -> float:
    """Seconds since the last stamped switch; inf where none happened."""
    with _lock:
        last = _last_switch_monotonic
    if last is None:
        return float("inf")
    return max(0.0, time.monotonic() - last)


def in_switch_window(settle: float | None = None) -> bool:
    """True while scripting must stay quiet after a switch."""
    if settle is None:
        settle = SETTLE_SECONDS
    return seconds_since_switch() < settle


def wait_for_settle(settle: float | None = None) -> None:
    """Sleep out the remainder of the switch window, if any."""
    if settle is None:
        settle = SETTLE_SECONDS
    remaining = settle - seconds_since_switch()
    if remaining > 0:
        time.sleep(remaining)


def connection_may_drop() -> bool:
    """May the broker/worker drop its cached scripting connection now?

    False inside the switch window: dropping (and the reconnect that
    follows) mid-switch races Resolve's scripting-thread shutdown -
    the 2026-10-06 crash site. The caller keeps its handle and retries
    after `wait_for_settle` instead.
    """
    return not in_switch_window()


def _reset_for_tests() -> None:
    """Clear the stamp. Tests only."""
    global _last_switch_monotonic
    with _lock:
        _last_switch_monotonic = None
