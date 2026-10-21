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

#: The DIRECT route's own phase.  A touch that writes a property in
#: place never stages, so it never opens the three phases above - it
#: opens this one, and the profile says so: the whole-reel bookkeeping
#: is absent, not cheaper.  It is not one of `PHASES` because the
#: master touch has no direct route and must not report a phase it
#: never opened.
DIRECT_PHASE = "direct"

#: The ledger layer each phase is recorded under.
LAYERS = {phase: f"touchup_{phase}" for phase in PHASES}
LAYERS[DIRECT_PHASE] = "touchup_direct"


class PhaseClock:
    """Records the touch path's phases as leaf spans in the ledger.

    One phase is open at a time: opening a phase closes the one before
    it, so the phases never overlap and never double-charge a second.
    """

    def __init__(self, target: str) -> None:
        self.target = str(target or "touch")
        self._open: dict[str, float] = {}
        #: The steps measured INSIDE each phase.  A step is a named part
        #: of a phase (the conform inside the conform/compose/verify,
        #: the state reconciliation inside the promotion), and its
        #: seconds are the phase's own seconds - so the split is a
        #: reading of the phase, not a second charge against the hold.
        self._steps: dict[str, dict[str, float]] = {}
        #: The step that is open inside the phase that is open: the
        #: named part of the phase the clock is currently in.
        self._step: tuple[str, float] | None = None
        #: A note on a phase, carried onto its row - what the phase
        #: decided, in words the ledger can read back.
        self._notes: dict[str, str] = {}
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
        self._close_step(phase)
        wall = max(0.0, time.time() - started)
        steps = dict(self._steps.pop(phase, {}))
        note = self._notes.pop(phase, "")
        self.rows.append({"phase": phase,
                          "started_at": round(started, 3),
                          "wall_s": round(wall, 3),
                          "steps": steps,
                          "note": note})
        perf_ledger.record(LAYERS[phase], wall, started_at=started,
                           touchup_phase=phase, touchup_target=self.target,
                           touchup_steps=steps or None,
                           touchup_note=note or None)

    def close_all(self) -> None:
        """Close every open phase - a touch that failed still reports
        what it spent."""
        for phase in list(self._open):
            self.close(phase)

    def current_phase(self) -> str:
        """The phase that is open, or "" when none is."""
        for phase in self._open:
            return phase
        return ""

    def note(self, phase: str, text: str) -> None:
        """Put a note on a phase, carried onto its row."""
        if phase not in LAYERS:
            raise ValueError(f"unknown touch phase {phase!r}")
        self._notes[str(phase)] = str(text)

    def add_step(self, phase: str, name: str, seconds: float) -> None:
        """Record one named step's seconds inside a phase.

        A step is a PART of the phase, not a phase of its own: its
        seconds are the phase's seconds, and the phase's total still
        sums to the hold.  Nothing here charges the hold twice - the
        step is a reading of the phase's own interval.
        """
        if phase not in LAYERS:
            raise ValueError(f"unknown touch phase {phase!r}")
        steps = self._steps.setdefault(phase, {})
        steps[str(name)] = round(steps.get(str(name), 0.0)
                                 + max(0.0, float(seconds)), 3)

    def _close_step(self, phase: str) -> None:
        """Close the open step, if any, into the phase it belongs to."""
        if self._step is None:
            return
        name, started = self._step
        self._step = None
        self.add_step(phase, name, time.time() - started)

    def mark(self, name: str) -> None:
        """The named part of the open phase starts here.

        A phase that does the same kind of work in several places is
        split by marks: the seconds between two marks are the step's
        own, and the steps of a phase sum to the phase.  This is how
        the promotion says which of its reads - the state
        reconciliation, the marker fleet, the rename, the journal
        close, the retirement - the seconds went to.
        """
        if self._step is not None:
            phase = self.current_phase()
            if phase:
                self._close_step(phase)
        self._step = (str(name), time.time())


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


def note(phase: str, text: str) -> None:
    """Put a note on the phase that is open.  A no-op with no clock."""
    clock = _ACTIVE.get()
    if clock is not None:
        clock.note(phase, str(text))


def mark(name: str) -> None:
    """The named part of the open phase starts here.  A no-op with no clock.

    A phase that does the same kind of work in several places is split
    by marks: the seconds between two marks are the step's own.  The
    steps of a phase sum to the phase, so the split moves no seconds.
    """
    clock = _ACTIVE.get()
    if clock is not None:
        clock.mark(name)


@contextmanager
def step(name: str) -> Iterator[dict[str, Any]]:
    """Measure one named STEP inside the phase that is open.

    A phase is a stretch of the touch; a step is a named part of it -
    the conform inside the conform/compose/verify, the state
    reconciliation inside the promotion.  The step's seconds are the
    phase's own seconds, so the phase still sums to the hold and the
    hold still sums to the same seconds it always did.

    The punchlist asks for the touch path's cost to be SPLIT - the
    duplication, the media conform, the state reconciliation and the
    final promotion are four different bills, and one number cannot say
    which one to pay.  This is the mechanism that splits them.

    With no clock in progress (a test, a call outside a touch) the
    block still runs and nothing is measured.
    """
    record: dict[str, Any] = {"step": str(name)}
    clock = _ACTIVE.get()
    if clock is None:
        yield record
        return
    phase = clock.current_phase()
    started = time.time()
    try:
        yield record
    finally:
        clock.add_step(phase, str(name), time.time() - started)
        record["phase"] = phase
        record["wall_s"] = round(time.time() - started, 3)


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
    "DIRECT_PHASE",
    "LAYERS",
    "PHASES",
    "PhaseClock",
    "close_phase",
    "current",
    "measured",
    "mark",
    "note",
    "open_phase",
    "phases",
    "step",
]
