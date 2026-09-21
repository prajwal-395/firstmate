"""A staging timeline awaiting promotion may not be swept (issue #971).

Measured 2026-09-11: Reel 13's marker fix was built into a scratch
timeline at 04:22Z and verified correct at 1921 frames. At 04:46Z the
cleanup sweep deleted it as scratch. It had never been promoted, so
the fix existed, was proven, and was thrown away by tidying.

The defect: the sweep knows "disposable scratch" but has no notion of
"a scratch that is a PENDING PROMOTION". Both look identical - a
timeline with a scratch-shaped name that no plan claims.

This module is the distinction: a durable HOLD a build takes out on
its own staging timeline and releases at promotion.

A staged timeline awaiting promotion is HELD: the sweep refuses a held
name, loudly.  (Moved from AGENTS.md 5 verbatim, where the rule keeps
its index row.)

Why a hold, not a verification-state check
------------------------------------------
No existing record marks a staging timeline as verified -
`verify_built_reels` overwrites one `conformance_report.json` per run,
so asking "whose verification passed" has nothing durable to read.
That state would have to be created, and a check that only protects
post-verify stagings leaves the build-to-verify window open. A hold
taken at STAGE time covers the whole pending window, needs no new
verdict plumbing, and is released by the same lifecycle that created
it.

Lifecycle (owned by `library/tools/reel_build.py`)
--------------------------------------------------
- TAKE: `rebuild_reels_in_project` takes a hold for every staging
  container it stages, plus - on suffix builds only - the suffixed
  final it stages toward (e.g. `... (baseline scratch)`), which sits
  pending a human promotion decision after the build promotes into
  it. Approved finals of ordinary builds are never held: no sweep
  may take them anyway.
- RELEASE: `promote_staged_reels` releases each staging it promotes
  (after the renames, so a failed promotion keeps its holds);
  `discard_staged_reels` releases each staging it discards. A
  verified staging that is never promoted stays held until an
  operator releases it explicitly with `release_hold` - see below.

Abandonment: holds NEVER expire. A timeout would reintroduce timed
deletion by another name. A hold that outlives its build is clutter,
and the sweep's refusal names the hold's age so the operator can
chase the owning lane; the release is then one explicit call:

    python3 -c "from library.tools.staging_holds import release_hold;
    print(release_hold('<project>', '<staging timeline name>'))"

What the sweep does with a hold
-------------------------------
`proof_cleanup._plan_removal` refuses a held timeline at plan time
and `execution/remove_proof.remove_proof` re-checks live at execution
(the plan is a claim about an earlier moment; a hold taken between
plan and apply must still refuse). Both raise naming the timeline,
the promotion it awaits and the hold's age - a sweep that declined
to remove something says what and why, never skips silently.

How this composes with the promote row-diff guard (issue #925)
--------------------------------------------------------------
The row-diff guard protects a promotion that HAPPENS: it diffs the
incoming staging against the retiring timeline and refuses a lossy
replace. Holds protect a promotion that has NOT happened yet: they
keep the verified staging alive until promotion runs. In sequence:
the hold ensures the staging SURVIVES to be promoted; the guard
ensures the promotion itself loses nothing. Promotion releases the
hold as a side effect; the guard itself stays a pure diff and knows
nothing about holds - a held staging with fewer rows is still
refused by the guard, and the hold is retained, which is the safe
direction on both halves.

`tests/test_pending_promotion_hold.py`.
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone

HOLDS_FILENAME = "staging_holds.json"
LOCK_FILENAME = "staging_holds.lock"

VERSION = 1


class HoldsUnreadable(RuntimeError):
    """The holds file exists but cannot be parsed, and this says so."""


def holds_path_for(project_folder: str) -> str:
    """Where this project's pending-promotion holds live.

    Under `pipeline_output/review/` - on the per-project version
    control allow-list, beside the declaration the build read, so a
    later diff answers what was pending and when.
    """
    return os.path.join(project_folder, "pipeline_output", "review",
                        HOLDS_FILENAME)


def lock_path_for(project_folder: str) -> str:
    """Where the holds file's lock lives: beside the file it guards."""
    return os.path.join(project_folder, "pipeline_output", "review",
                        LOCK_FILENAME)


@contextmanager
def _holds_lock(project_folder: str, *, shared: bool = False):
    """Hold the holds file's lock while the caller reads or writes.

    The atomic rename in `_write_holds` keeps a reader from seeing a
    torn file, but two lanes staging at once interleave read-modify-
    write: each reads the same set, each writes back only its own
    entry, and one lane's hold silently vanishes (measured 2026-09-20
    as a hold dropped from the file between two writers, unprotecting
    a live staging from a future prune). The lock closes that: every
    take and release runs its read-modify-write under an EXCLUSIVE
    lock, and readers take it SHARED. Same shape as the render
    ledger's lock (`library/tools/caption_asset_gc.py`), and likewise
    a no-op where `fcntl` is unavailable rather than a refusal.
    """
    try:
        import fcntl  # noqa: PLC0415 - platform seam, not a dependency
    except ImportError:
        yield
        return
    path = lock_path_for(project_folder)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(),
                    fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _parse_taken_at(raw: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(raw or ""))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _now() -> datetime:
    return datetime.now(timezone.utc)


def read_holds(project_folder: str) -> dict:
    """Every pending-promotion hold, by staging timeline name.

    A missing file is no holds - a project that never staged has
    nothing pending. A corrupt file REFUSES rather than reading as
    empty: an unreadable hold set reads exactly like "nothing is
    protected" and would condemn every held staging, so the sweep
    keeps refusing until an operator inspects or clears it.

    Reads under the SHARED lock, so a take or release mid-read cannot
    hand back a set that is already stale.
    """
    with _holds_lock(project_folder, shared=True):
        return _read_holds_unlocked(project_folder)


def _read_holds_unlocked(project_folder: str) -> dict:
    """`read_holds` without the lock: for callers already holding it.

    `take_hold` and the releases run read-modify-write under one
    EXCLUSIVE lock, so they must not re-acquire it mid-cycle (a second
    `flock` on another descriptor of the same file contends with the
    first, even in the same process).
    """
    path = holds_path_for(project_folder)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise HoldsUnreadable(
            f"the staging-holds file at {path} cannot be read "
            f"({unreadable}); refusing to judge any scratch timeline "
            f"until it is inspected or removed. Nothing was removed."
        ) from unreadable
    if not isinstance(data, dict):
        raise HoldsUnreadable(
            f"the staging-holds file at {path} holds "
            f"{type(data).__name__}, not an object; refusing to judge "
            f"any scratch timeline until it is inspected or removed. "
            f"Nothing was removed.")
    holds = data.get("holds", {})
    if not isinstance(holds, dict):
        raise HoldsUnreadable(
            f"the staging-holds file at {path} holds no 'holds' mapping; "
            f"refusing to judge any scratch timeline until it is "
            f"inspected or removed. Nothing was removed.")
    return {name: entry for name, entry in holds.items()
            if isinstance(entry, dict)}


def _write_holds(project_folder: str, holds: dict) -> None:
    """Write the hold set atomically: temp file plus rename.

    Two lanes stage at once often enough (Reel 13 and Reel 28 ran in
    parallel the day this was written) that a torn write must not be
    expressible. A reader never sees a half-written file.

    The rename alone does not stop two interleaved writers losing
    each other's entries - callers run read-modify-write under
    `_holds_lock`, and this stays the write half of that cycle.
    """
    path = holds_path_for(project_folder)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    doc = {"version": VERSION, "holds": holds,
           "updated_at": _now().isoformat()}
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".staging_holds_",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(doc, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def take_hold(project_folder: str, staging_name: str, *,
              awaiting: str | None = None, reason: str = "",
              taken_by: str = "") -> dict:
    """Hold `staging_name` against the sweep until it is promoted.

    `awaiting` names the final timeline this staging promotes into,
    or None where the promotion decision itself is still open (a
    suffix verification build, which a human promotes explicitly).
    Taking is an upsert stamped NOW: a rebuild re-takes the same
    name and the pending window restarts, so a stale entry from a
    crashed run cannot outlive the run that replaced it.

    The read-modify-write runs under the EXCLUSIVE holds lock, so a
    concurrent take or release cannot interleave between the read and
    the write and silently drop this entry.
    """
    if not staging_name:
        raise ValueError("take_hold needs a staging timeline name.")
    with _holds_lock(project_folder):
        holds = _read_holds_unlocked(project_folder)
        entry = {"awaiting": awaiting,
                 "taken_at": _now().isoformat(),
                 "reason": reason,
                 "taken_by": taken_by}
        holds[staging_name] = entry
        _write_holds(project_folder, holds)
    return dict(entry)


def release_hold(project_folder: str, staging_name: str) -> bool:
    """Release one hold. True when an entry was removed."""
    with _holds_lock(project_folder):
        holds = _read_holds_unlocked(project_folder)
        if staging_name not in holds:
            return False
        del holds[staging_name]
        _write_holds(project_folder, holds)
    return True


def release_holds(project_folder: str, staging_names) -> dict:
    """Release every named hold. Returns the names released."""
    with _holds_lock(project_folder):
        holds = _read_holds_unlocked(project_folder)
        released = [name for name in (staging_names or ())
                    if name in holds]
        if not released:
            return {"released": []}
        for name in released:
            del holds[name]
        _write_holds(project_folder, holds)
    return {"released": released}


def held_names(project_folder: str) -> set:
    """The staging timeline names under hold, for membership checks."""
    return set(read_holds(project_folder))


def hold_age(entry: dict, now: datetime | None = None) -> str:
    """How long this hold has been pending, in the refusal's words."""
    taken = _parse_taken_at((entry or {}).get("taken_at", ""))
    if taken is None:
        return "unknown age"
    seconds = max(0, int(((now or _now()) - taken).total_seconds()))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def refusal_message(staging_name: str, entry: dict) -> str:
    """The loud refusal a sweep raises for one held timeline.

    Names the timeline, the promotion it awaits, who took the hold
    and how long it has been pending, and the exact release that
    proceeds deliberately. Shared so the plan-time and execution-time
    refusals cannot drift apart.
    """
    awaiting = (entry or {}).get("awaiting")
    if awaiting:
        waits = f"awaiting promotion to {awaiting!r}"
    else:
        waits = ("awaiting an explicit promotion decision "
                 "(a suffix verification build - no automatic "
                 "promotion will take it)")
    reason = (entry or {}).get("reason") or ""
    taken_by = (entry or {}).get("taken_by") or ""
    lines = [
        f"REFUSING to sweep {staging_name!r}: it is a STAGED build "
        f"{waits} - hold taken "
        f"{(entry or {}).get('taken_at', 'at an unknown time')} "
        f"({hold_age(entry)} ago).",
    ]
    if taken_by or reason:
        lines.append(f"  Hold: {taken_by} {reason}".rstrip())
    lines.append(
        "  Promote it, or release the hold explicitly and re-run - "
        "the sweep never takes a pending promotion:")
    lines.append(
        f"    python3 -c \"from library.tools.staging_holds import "
        f"release_hold; print(release_hold('.', {staging_name!r}))\" "
        f"(run in the project folder)")
    lines.append("  Nothing was removed.")
    return "\n".join(lines)


def pending_promotions(project_folder: str) -> list:
    """Every staging still awaiting promotion, oldest first.

    The holds file IS the pending-promotion record (a hold is taken
    at STAGE time and released at promotion or discard), but until
    2026-09-20 nothing ever READ it that way: the sweep reads holds
    only to refuse deletion, so a build that stages but never
    promotes sits protected and invisible - Reel 16's 2026-09-19
    staging sat a day while two lanes stepped around it as debris
    and the captain found it himself. A run that ends with entries
    here says so (see `report_pending`); an empty list is no pending
    work, never an unreadable file (which raises `HoldsUnreadable`
    rather than reading as empty).
    """
    holds = read_holds(project_folder)
    out = []
    for name, entry in holds.items():
        entry = entry if isinstance(entry, dict) else {}
        out.append({
            "staging": name,
            "awaiting": entry.get("awaiting"),
            "taken_at": entry.get("taken_at", ""),
            "age": hold_age(entry),
            "taken_by": entry.get("taken_by", ""),
            "reason": entry.get("reason", ""),
        })
    out.sort(key=lambda row: row["taken_at"])
    return out


def report_pending(project_folder: str) -> str:
    """Pending promotions as a run-end warning, or "" when none.

    Loud about WHAT is pending and HOW LONG, because a staging that
    is never promoted is finished work the captain never sees: name
    the staging, the final it awaits, and the hold's age. Quiet (not
    silent - the empty string, which callers print only when
    non-empty) when nothing is pending.
    """
    pending = pending_promotions(project_folder)
    if not pending:
        return ""
    lines = [f"UNPROMOTED STAGING: {len(pending)} staged timeline(s) "
             f"still awaiting promotion - finished work no timeline "
             f"carries yet:"]
    for row in pending:
        awaiting = row["awaiting"]
        waits = (f"-> {awaiting!r}" if awaiting
                 else "(no automatic promotion will take it - a human "
                      "promotes it explicitly or releases the hold)")
        lines.append(f"  {row['staging']!r} {waits} "
                     f"(held {row['age']}, by {row['taken_by'] or '?'})")
    lines.append("  Promote it, discard it, or release the hold "
                 "explicitly - a staging that sits is a fix the "
                 "captain cannot watch.")
    return "\n".join(lines)
