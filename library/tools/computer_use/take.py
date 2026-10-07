"""One exclusive lease for a whole UI take, model think time included.

A UI take is establish-act-verify-restore: the project borrowed, the
timeline current, steps driven and verified, the captain's project
restored. Anything that runs between two of its steps against Resolve
- a broker snapshot making its timeline current, a foreign reset -
sees different state than the take established (measured live
2026-10-06/07: a lost Q Seed, disagreeing pixels). The lease is the
fence, so the take holds one EXCLUSIVE lease for the whole sequence,
including model inference: releasing during think time to let other
agents interleave invites exactly the interleaving that corrupts
takes. The broker schedules the take like every other job (its grant
carries the take purpose, so `ren resolved list` shows it busy), and
the shadow watcher and watchdog skip on contention instead of marking
Resolve degraded.

The lease is bounded: a take that never ends wedges the fleet behind
it (measured 2026-10-04: an 818 s hold piled every later lease up).
`TAKE_LENGTH_SECONDS` caps wall-clock from take entry; the loops check
the deadline before each model call or step and stop on it. The bound
covers the longest announced take window tonight (180 s, with an order
of magnitude for establish plus local-model think) while staying well
under the 900 s fleet acquisition timeout, so one take can never wedge
every waiter to their own timeout. `$COMPUTER_USE_TAKE_SECONDS`
overrides it.

Every computer-use verb and recipe runner enters through `ui_take` -
never a hand-rolled lease plus borrowed plus connect. A second
`ui_take` inside one is a reentrant no-op with its own deadline
ignored (the outer take's bound rules - one take, one bound).
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from library.tools.ren_refusal import RenRefusal

#: Override for the take length bound, in seconds.
TAKE_ENV = "COMPUTER_USE_TAKE_SECONDS"

#: Wall-clock bound on one UI take. Provenance is the module docstring:
#: longest announced window plus think headroom, under the fleet timeout.
TAKE_LENGTH_SECONDS = 600.0


class TakeExpired(RenRefusal):
    """The take's wall-clock bound ran out - aborted and released."""


@dataclass(frozen=True)
class TakeContext:
    """What a take body runs with: the connected Resolve and the take's
    monotonic deadline, which loops and scripts check before each step."""

    resolve: Any
    deadline: float


def take_length_seconds() -> float:
    """The take bound: `$COMPUTER_USE_TAKE_SECONDS` or the default."""
    raw = os.environ.get(TAKE_ENV, "")
    if raw:
        try:
            if float(raw) > 0:
                return float(raw)
        except ValueError:
            pass
    return TAKE_LENGTH_SECONDS


@contextmanager
def ui_take(purpose: str,
            take_seconds: float | None = None) -> Iterator[TakeContext]:
    """Hold one exclusive lease for a whole UI take.

    Lease, handshake, borrow, run, restore: the yielded `TakeContext`
    carries the connected Resolve and the monotonic deadline the body
    passes to `run_loop`/`run_script`. The purpose names the take and
    its bound, so the broker grant and the lease file show what is
    busy and for how long. A take whose bound already passed refuses
    with `TakeExpired` before touching Resolve.
    """
    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import borrowed
    from library.tools.resolve_lock import resolve_lease

    bound = take_length_seconds() if take_seconds is None else take_seconds
    deadline = time.monotonic() + bound
    if bound <= 0 or time.monotonic() >= deadline:
        raise TakeExpired(
            f"the take {purpose!r} is already past its {bound:.0f}s bound",
            "a take that cannot fit its bound never starts",
            "raise the bound or split the take, then retry",
            code="TAKE_EXPIRED",
        )
    with resolve_lease(f"{purpose} (take, {bound:.0f}s bound)",
                       exclusive=True):
        resolve = connect_resolve()
        with borrowed(resolve):
            yield TakeContext(resolve=resolve, deadline=deadline)
