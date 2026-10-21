"""Resolve's timeline handle does not catch up with a write the instant it lands.

`AppendToTimeline` returns the placed item, and the timeline still reads
the state from BEFORE the write: the clip is on the row, but the handle
has not caught up.  A path that verifies a write by reading the
timeline straight after it therefore reads a timeline that never moved,
and reports a failure for a write that landed.

Measured 2026-10-21 on a Ren-owned scratch copy of a real reel
(portrait 1080x1920, 23.976fps, 65 clips over eight video rows and two
audio rows):

* `resolve-axi edit place` reported "append reported success but the
  item count did not move (67 -> 67) - nothing landed" while the clip
  WAS on the cover row at the requested frame - the read was taken
  before Resolve had refreshed the row;
* the EditPatch `clip.insert` wrote the placed item's identity onto the
  operation as the string "None", because a freshly appended item has
  no readable unique id until the handle has caught up, and the
  verification then looked for a clip called "None" and found nothing.

The synthetic qualification fixture never saw this: its timeline holds
one or two clips, and Resolve refreshes that handle fast enough to
read the write back on the first try.  A real reel is dense, and the
refresh takes longer - which is exactly why the qualification earned on
the fixture says nothing about the reel.

So the ONE reading of "the write landed" WAITS for the handle, with a
bound, and reports what it waited for.  The wait is bounded on purpose:
a write that genuinely did not land must still be reported as a
failure, and a wait that never ends would hide it.

`library/tools/edit_patch.py` (the post-write snapshot),
`library/tools/edit_algebra.py` (the placed item's identity) and
`library/tools/resolve_axi_core_commands.py` (the place verb's item
count, and the re-place read-back) all read through this module, so
there is ONE reading of a settled timeline.

`tests/unit/resolve/test_resolve_write_settle.py`.
"""

from __future__ import annotations

import time
from collections.abc import Callable

#: How long a write waits for Resolve's handle to catch up.
#:
#: A real reel's timeline is dense, and its refresh is slower than a
#: fixture's.  Three seconds is the measured bound on this machine: the
#: placements that read back as "not landed" were on the row well inside
#: it.  A write that has not landed by then did not land, and saying so
#: is the point of the bound.
SETTLE_TIMEOUT_SECONDS = 3.0

#: How often the handle is re-read while it catches up.
SETTLE_INTERVAL_SECONDS = 0.05


def wait_for(predicate: Callable[[], object], *,
             timeout_seconds: float = SETTLE_TIMEOUT_SECONDS,
             interval_seconds: float = SETTLE_INTERVAL_SECONDS
             ) -> tuple[bool, float]:
    """Wait for `predicate` to answer true; report whether it did.

    Returns `(settled, waited_seconds)`.  `settled` is False when the
    bound passed without the predicate answering true - a write that did
    not land is reported as such, never waited for forever.

    An exception RAISES: a read that FAILED is uncertainty, not
    staleness, and the caller's own failure path (the recovery
    requirement) owns it.  A predicate that must not raise catches its
    own.
    """
    started = time.monotonic()
    while True:
        if predicate():
            return True, time.monotonic() - started
        if time.monotonic() - started >= timeout_seconds:
            return False, time.monotonic() - started
        time.sleep(max(0.0, interval_seconds))


def settled_snapshot(read_live: Callable[[], dict], *,
                     before_hash: str = "",
                     timeout_seconds: float = SETTLE_TIMEOUT_SECONDS,
                     interval_seconds: float = SETTLE_INTERVAL_SECONDS
                     ) -> tuple[dict, bool, float]:
    """The post-write snapshot, once the handle has caught up.

    `read_live` reads the timeline.  The snapshot is returned once it
    DIFFERS from `before_hash` - the hash of the state from before the
    write - because a write that changed nothing has nothing to wait
    for.  When the bound passes, the last read is returned with
    `settled=False`, and the caller judges that read as it always did.
    """
    from library.tools import timeline_shadow

    last: dict = {}
    settled = False

    def changed() -> bool:
        nonlocal last
        last = read_live()
        if not before_hash:
            return True
        return timeline_shadow.snapshot_hash(last) != before_hash

    ok, waited = wait_for(changed, timeout_seconds=timeout_seconds,
                          interval_seconds=interval_seconds)
    settled = ok
    if not last:
        last = read_live()
    return last, settled, waited


def settled_placed_identity(item, *,
                            timeout_seconds: float = SETTLE_TIMEOUT_SECONDS,
                            interval_seconds: float = SETTLE_INTERVAL_SECONDS
                            ) -> tuple[str, bool, float]:
    """The unique id of a freshly placed item, once it reads back.

    A placed item has no readable identity until Resolve's handle has
    caught up: `GetUniqueId()` answers None, and `str(None)` is the
    string "None" - which is what a placement wrote onto the operation
    and what the verification then looked for.
    """
    def identity() -> str:
        try:
            value = item.GetUniqueId()
        except Exception:  # noqa: BLE001 - an unreadable item is not settled
            return ""
        text = str(value or "").strip()
        return "" if text in ("", "None") else text

    ok, waited = wait_for(identity, timeout_seconds=timeout_seconds,
                          interval_seconds=interval_seconds)
    return identity(), ok, waited


def settled_item_count(count_items: Callable[[], int], before: int, *,
                       timeout_seconds: float = SETTLE_TIMEOUT_SECONDS,
                       interval_seconds: float = SETTLE_INTERVAL_SECONDS
                       ) -> tuple[int, bool, float]:
    """The item count, once the handle has caught up with a placement.

    `count_items` counts the items on the timeline.  The count is
    returned once it DIFFERS from `before`; a placement that did not
    land leaves it where it was, and the caller says so.
    """
    last = before

    def moved() -> bool:
        nonlocal last
        last = int(count_items())
        return last != before

    ok, waited = wait_for(moved, timeout_seconds=timeout_seconds,
                          interval_seconds=interval_seconds)
    return last, ok, waited
