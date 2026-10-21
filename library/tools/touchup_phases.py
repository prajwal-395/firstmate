"""The touch path's three measured phases.

A touch-up's staging cost was ONE number: the `resolve_hold` span the
lease records covered the duplicate, the conform/compose/verify and the
promotion together, so a profile could say the touch took 57 s and could
not say WHICH of the three to attack.  This module owns the three
phases and records each one as its own row in the performance ledger.

The rows are LEAF spans (`perf_ledger.record`), not nested contexts:
the three phases are three DISJOINT intervals of one lease, and a
nested span would charge the promotion to the phase that contains it.
A leaf span is still subtracted from the enclosing `resolve_hold` span,
so the split moves no seconds: the three phases plus the hold's own
remainder is the hold the profile showed before.

The accounting is preserved on purpose.  The lease's hold was already
charged to Resolve (`resolve_hold` is `resolve_api` in the ledger's
external table), so the three phases are charged to the same engine -
the split is a MEASUREMENT of Resolve's own cost, not a re-attribution
of it.  A touch that runs with no ledger attached records nothing,
exactly as every other measurement in the engine does.

Nothing here changes what a touch DOES.  This module only measures.

Where the touch's time goes, measured: `docs/TOUCH_PATH_LATENCY.md`.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from library.tools import perf_ledger

#: The three phases of a touch, in the order they happen.
PHASES = ("duplicate", "conform_compose_verify", "promote")

#: The ledger layer each phase is recorded under.
LAYERS = {phase: f"touchup_{phase}" for phase in PHASES}


class PhaseClock:
    """Records the touch path's phases as leaf spans in the ledger.

    One phase is open at a time: opening a phase closes the one before
    it, so the phases never overlap and never double-charge a second.
    """

    def __init__(self, target: str) -> None:
        self.target = str(target or "touch")
        self._open: dict[str, float] = {}
        self.rows: list[dict[str, Any]] = []

    def open(self, phase: str) -> None:
        """Open a phase, closing whichever phase was open before it."""
        if phase not in LAYERS:
            raise ValueError(f"unknown touch phase {phase!r}")
        for other in list(self._open):
            if other != phase:
                self.close(other)
        self._open.setdefault(phase, time.time())

    def close(self, phase: str) -> None:
        """Close a phase and write its row. A phase never open is a
        no-op - a phase that never happened has no seconds to report."""
        started = self._open.pop(phase, None)
        if started is None:
            return
        wall = max(0.0, time.time() - started)
        self.rows.append({"phase": phase,
                          "started_at": round(started, 3),
                          "wall_s": round(wall, 3)})
        perf_ledger.record(LAYERS[phase], wall, started_at=started,
                           touchup_phase=phase, touchup_target=self.target)

    def close_all(self) -> None:
        """Close every open phase - a touch that failed still reports
        what it spent."""
        for phase in list(self._open):
            self.close(phase)


_ACTIVE: ContextVar[PhaseClock | None] = ContextVar(
    "ren_touchup_phase_clock", default=None)


@contextmanager
def phases(target: str) -> Iterator[PhaseClock]:
    """Measure one touch's phases.

    The clock is context-local, so the promotion - which lives behind
    several calls - can find the clock that measured the duplicate
    without every one of those calls carrying it.
    """
    clock = PhaseClock(target)
    token = _ACTIVE.set(clock)
    try:
        yield clock
    finally:
        clock.close_all()
        _ACTIVE.reset(token)


def current() -> PhaseClock | None:
    """The clock measuring the touch in progress, if there is one."""
    return _ACTIVE.get()


def open_phase(phase: str) -> None:
    """Open a phase of the touch in progress. A no-op with no clock."""
    clock = _ACTIVE.get()
    if clock is not None:
        clock.open(phase)


def close_phase(phase: str) -> None:
    """Close a phase of the touch in progress. A no-op with no clock."""
    clock = _ACTIVE.get()
    if clock is not None:
        clock.close(phase)


def measured(phase: str) -> Callable:
    """Time the decorated call as one phase of the touch path.

    The decorated call is timed whether or not it succeeds: a touch that
    failed in the promotion still spent the promotion's seconds, and a
    profile that hid them would point the fix lane at the wrong phase.
    """

    def decorate(function: Callable) -> Callable:
        @functools.wraps(function)
        def timed(*args: Any, **kwargs: Any) -> Any:
            clock = _ACTIVE.get()
            if clock is None:
                return function(*args, **kwargs)
            clock.open(phase)
            try:
                return function(*args, **kwargs)
            finally:
                clock.close(phase)

        return timed

    return decorate


__all__ = [
    "LAYERS",
    "PHASES",
    "PhaseClock",
    "close_phase",
    "current",
    "measured",
    "open_phase",
    "phases",
]
