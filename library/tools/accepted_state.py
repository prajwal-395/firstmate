"""VC-003: the accepted state of a reel, and the invariant that guards it.

THE DEFECT THIS CLOSES
----------------------
A reel's accepted work is its GRADE, its FUSION comps, its TRANSFORM,
its KEYFRAMES and its SOUND.  The engine compared a reel's CUT (its
rows, its spans, its names) and said nothing about the rest, in three
places that all agreed to be silent:

- the VERSION comparison.  The committed version record
  (`.timeline.json`, `library/tools/timeline_serializer.py`) reduced a
  clip's Fusion comps to a COUNT and a list of NAMES, dropped the
  comp's CANVAS entirely, dropped the keyframes, and kept only the
  CDL of the grade - so a version whose comp had been re-authored on
  another canvas, or whose keyframes had gone, or whose nodes had
  been replaced, diffed as the same reel.  A grade is not a row.
- the RECORDED-STATE comparison.  `timeline_shadow.diff` compared two
  recorded generations by their rows and their markers, so a
  generation that arrived with a different grade on a shot read as
  the same timeline with a different cut.
- the UNDO's own verification.  The journal's projection
  (`undo_journal.item_projection`) carried the transform, the comp
  COUNT and the CDL, and carried no keyframes, no comp canvas and no
  sound - so a captain who hand-moved a keyframe or a level after a
  touch was read as "the timeline has not moved", and the undo
  reversed straight over the work.

WHAT THIS MODULE IS
-------------------
ONE reading of the accepted state, ONE comparison of it, and ONE
refusal for a change nobody authorized:

- `capture` reads the accepted state out of a recorded state - a live
  snapshot (`reel_read`) or a VERSION RECORD (the committed
  `.timeline.json`), which now carry the same fields, so one reading
  serves both.
- `verify` compares two accepted states and says what moved, in the
  engine's own vocabulary (`look_preservation.LOOK_PARTS` and the
  conflict domains), with the DENOMINATOR: how many accepted items
  were compared, how many changed, how many of those changes nobody
  authorized.  A report that says "one thing moved" and a report that
  says "one of forty accepted items moved" are different claims.
- `require_preserved` is the INVARIANT: it refuses when an accepted
  item moved and no authorized scope says it may.  It fails CLOSED -
  a difference nobody can judge is unprovable, and an unprovable
  difference is not a pass.

The CANVAS is part of the accepted state.  A comp authored on the
source canvas lands its vignette and glow on a different picture than
one authored on the delivery canvas (Reel 26, 2026-10-20), and a reel
whose delivery canvas changed re-frames every comp in it.  Both are
named here: a comp whose canvas CHANGED between two states, and a
reel whose DELIVERY canvas changed between two states.

WHAT THIS MODULE DOES NOT OWN
------------------------------
- the look comparison itself, which is QA-005's (`look_preservation`),
  read here and reported with its counts;
- the read-back judgement on one mutation
  (`preservation_invariant`, `mutation_verification`), which already
  refuses an unauthorized change on the domain the operation declared;
- the pre-write model of what a request may cause
  (`collateral_scope`, PLAN-006);
- the RESTORE's own verification of the look it brought back
  (VC-004, `reel_replace_guard.preservation_record` and
  `reel_replace_guard.preservation_losses`).

THAT LAST ONE IS A DIFFERENT QUESTION, NOT A SECOND SPELLING
-------------------------------------------------------------
VC-004's record answers "did the restore REPRODUCE the look the
restored version recorded?" - it is read after the import, against the
live timeline, and it is addressed by the media each item plays.  This
module answers "WHAT DOES THE RESTORE REPLACE in the approved work?" -
it is read between two VERSIONS, before the import, and it is
addressed by the shot's editorial identity.  The two records overlap in
the grade and in the Fusion comps, and they are not interchangeable:
this one also carries the TRANSFORM, the KEYFRAMES, the SOUND and the
CANVAS, and it is what a version comparison, a round diff and a branch
comparison read.  A version that carries one and not the other says so
rather than filling the gap.

READ-ONLY.  Nothing here writes to Resolve or to a timeline.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from library.tools import look_preservation
from library.tools.look_preservation import (
    LOOK_PARTS,
    PART_DOMAINS,
    PRESERVED,
    UNPROVABLE,
    VIOLATED,
)
from library.tools.ren_refusal import RenRefusal

SCHEMA = "ren-accepted-state/1"

#: The parts of a shot the captain accepted.  The vocabulary is
#: QA-005's (`look_preservation.LOOK_PARTS`) and is not restated here:
#: one enumeration, so a look finding and an accepted-state finding
#: mean the same thing when they name the same part.
ACCEPTED_PARTS: tuple[str, ...] = LOOK_PARTS

#: The domain a comp's CANVAS belongs to.  A comp's canvas is part of
#: the comp, and the comp is Fusion.
CANVAS_DOMAIN = PART_DOMAINS["fusion"]

#: The domain a change of the reel's DELIVERY canvas belongs to.  The
#: reel's own shape changed, and every comp in it is drawn against
#: that shape.
DELIVERY_CANVAS_DOMAIN = "timeline_structure"


class AcceptedStateMoved(RenRefusal):
    """An accepted item changed and no authorized scope says it may."""

    USER_MESSAGE = ("I can't make this change, because it would move work "
                    "that was already approved.")
    RECOVERY_CLASS = "needs_question"
    ASK_REASON = "missing_authorization"
    """The change is the person's to approve, and nobody has said yes."""


class AcceptedStateUnprovable(RenRefusal):
    """An accepted item changed and nothing can say whether it may.

    A difference nobody can judge is not a pass.  The invariant fails
    closed on it, and the fix is to say what the change was for.
    """

    USER_MESSAGE = ("I can't make this change, because I cannot tell "
                    "whether it moves work that was already approved.")
    RECOVERY_CLASS = "blocked"


# ── the reading ──────────────────────────────────────────────────────────

def _compact_tools(comps) -> list:
    """Each comp's tools as a COUNT and a digest, not as the list.

    A reel's comps carry dozens of tools each, and a version ledger
    that carried every tool of every clip of every version would grow
    by hundreds of kilobytes per version.  The COUNT and the DIGEST
    answer the question the comparison asks - did the comp's tool set
    change - and the full list stays in the live reading for a reader
    who wants to see it.
    """
    import hashlib

    out = []
    for comp in comps or ():
        if not isinstance(comp, Mapping):
            continue
        row = {key: comp[key] for key in ("name", "canvas")
               if key in comp}
        tools = comp.get("tools")
        if tools is not None:
            names = sorted(str(tool) for tool in tools)
            row["tools"] = {
                "count": len(names),
                "digest": hashlib.sha256(
                    "\n".join(names).encode("utf-8")).hexdigest(),
            }
        out.append(row)
    return out


def capture(state: Mapping[str, Any]) -> dict[str, Any]:
    """The accepted state of a reel, out of one recorded state.

    `state` is a live snapshot (`reel_read.read_reel`) or a VERSION
    RECORD (the committed `.timeline.json`) - the two carry the same
    clip fields, so one reading serves both.  A state that names no
    timeline is still read: the shots are what the accepted state IS,
    and a state nobody can name is a state nobody can compare.
    """
    if not isinstance(state, Mapping):
        raise look_preservation.LookPreservationError(
            "an accepted state can only be read from a recorded state")
    look = look_preservation.capture_look(state)
    shots = []
    for shot in look.get("shots") or ():
        row = dict(shot)
        # The parts stay at the top level of the shot, so a captured
        # accepted state is READ by QA-005's own comparison without a
        # second shape and a second comparison.
        fusion = row.get("fusion")
        if isinstance(fusion, Mapping) and "comps" in fusion:
            fusion = dict(fusion)
            fusion["comps"] = _compact_tools(fusion.get("comps"))
            row["fusion"] = fusion
        shots.append(row)
    return {
        "schema": SCHEMA,
        "timeline": look.get("timeline"),
        "delivery": look.get("delivery"),
        "shots": shots,
        "counts": {
            "shots": len(shots),
            "accepted_items": sum(
                1 for shot in shots
                for part in ACCEPTED_PARTS if shot.get(part)),
        },
    }


def capture_tracks(tracks) -> dict[str, Any]:
    """The accepted state of a recorded set of tracks.

    `reel_read.read_tracks` returns the rows of a timeline; the journal
    and the adoption path hold exactly that.  One reading, so the
    accepted state of a live reel and the accepted state of a recorded
    one are the same shape.
    """
    return capture({"tracks": list(tracks or ())})


# ── the comparison ───────────────────────────────────────────────────────

def _canvas_of(shot: Mapping[str, Any]) -> list:
    """The canvases a shot's comps were authored on, as (comp, w, h)."""
    out = []
    fusion = shot.get("fusion") or {}
    for comp in fusion.get("comps") or ():
        if not isinstance(comp, Mapping):
            continue
        canvas = comp.get("canvas")
        if not isinstance(canvas, Mapping):
            continue
        try:
            width = int(canvas.get("width"))
            height = int(canvas.get("height"))
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        out.append((str(comp.get("name") or ""), width, height))
    return out


def _canvas_findings(before: Mapping[str, Any],
                     after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The canvas differences between two accepted states.

    Two of them, and they are different defects:

    - `comp_canvas_changed` - the shot's comp was AUTHORED on another
      canvas.  Reel 26's closing shot: the comp was drawn on the
      source canvas while every other comp in the reel was drawn on
      the delivery canvas, so its vignette and glow landed on a
      different picture than they were drawn for.
    - `delivery_canvas_changed` - the reel now DELIVERS another
      canvas.  Every comp in it is drawn against the old one, so the
      whole reel's look moves without one comp changing.

    The static report - a comp that is not on the delivery canvas at
    all - is QA-005's (`look_preservation.canvas_report`) and is
    carried through unchanged: it names a state, not a difference.
    """
    findings: list[dict[str, Any]] = []
    before_shots = {str(shot.get("shot_id") or ""): shot
                    for shot in before.get("shots") or ()}
    after_shots = {str(shot.get("shot_id") or ""): shot
                   for shot in after.get("shots") or ()}
    for shot_id, old in before_shots.items():
        new = after_shots.get(shot_id)
        if new is None:
            continue
        was = _canvas_of(old)
        now = _canvas_of(new)
        if was != now:
            findings.append({
                "kind": "comp_canvas_changed",
                "shot": shot_id,
                "unique_id": old.get("unique_id"),
                "row": old.get("row"),
                "name": old.get("name"),
                "domain": CANVAS_DOMAIN,
                "before": [{"comp": name, "width": width, "height": height}
                           for name, width, height in was],
                "after": [{"comp": name, "width": width, "height": height}
                          for name, width, height in now],
            })
    old_delivery = before.get("delivery") or {}
    new_delivery = after.get("delivery") or {}
    if (old_delivery.get("width"), old_delivery.get("height")) != \
            (new_delivery.get("width"), new_delivery.get("height")):
        findings.append({
            "kind": "delivery_canvas_changed",
            "shot": "",
            "row": "",
            "name": str(before.get("timeline") or ""),
            "domain": DELIVERY_CANVAS_DOMAIN,
            "before": dict(old_delivery),
            "after": dict(new_delivery),
        })
    return findings


def verify(before: Mapping[str, Any], after: Mapping[str, Any], *,
           scope: Any = None) -> dict[str, Any]:
    """Compare two accepted states, and say what moved.

    `before` and `after` are recorded states (a live snapshot, a
    version record) or already-captured accepted states.  `scope` is
    the authorized delta - a `preservation_invariant.MutationScope` -
    and without it every difference is UNPROVABLE rather than a pass.

    The report carries the DENOMINATOR: `accepted_items` is how many
    accepted items were compared, and `changed` is how many of them
    moved.  `unauthorized` is how many of those changes no authorized
    scope permits.
    """
    left = before if _is_captured(before) else capture(before)
    right = after if _is_captured(after) else capture(after)
    look = look_preservation.compare_look(left, right, scope=scope)
    canvas = _canvas_findings(left, right)
    for row in canvas:
        row["authorized"] = _authorized(scope, row)
    static = look_preservation.canvas_report(
        right, delivery=right.get("delivery"))
    counts = dict(look.get("counts") or {})
    counts["canvas_differences"] = len(canvas)
    unauthorized = counts.get("unauthorized", 0) + sum(
        1 for row in canvas if row.get("authorized") is False)
    unjudged = counts.get("unjudged", 0) + sum(
        1 for row in canvas if row.get("authorized") is None)
    if unauthorized:
        status = VIOLATED
    elif unjudged:
        status = UNPROVABLE
    else:
        status = PRESERVED
    return {
        "schema": SCHEMA,
        "status": status,
        "findings": list(look.get("findings") or ()),
        "canvas": canvas,
        "canvas_report": static,
        "counts": counts,
        "shots_compared": look.get("shots_compared"),
    }


def _is_captured(state: Mapping[str, Any]) -> bool:
    """Whether this is already a captured accepted state."""
    return isinstance(state, Mapping) and state.get("schema") == SCHEMA


def _authorized(scope: Any, row: Mapping[str, Any]) -> bool | None:
    """Whether the authorized scope permits this canvas difference.

    None where there is no scope to judge against: a difference nobody
    can judge is not a pass, and it is not a violation either.
    """
    if scope is None:
        return None
    allows = getattr(scope, "allows", None)
    if not callable(allows):
        return None
    unique_id = str(row.get("unique_id") or "")
    if not unique_id:
        return None
    return bool(allows(f"clip:{unique_id}", str(row.get("domain") or "")))


def moved_accepted_items(before: Mapping[str, Any],
                         after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every accepted item that moved between two states, named.

    The same reading as `verify`, without a verdict: the differences
    themselves.  A caller that asks "has the accepted state moved?"
    - the undo, before it reverses over work it did not make - wants
    the list, and judges it against its own question.  The verdict
    needs an authorized scope, and this caller has none: it is asking
    whether anything moved at all.
    """
    left = before if _is_captured(before) else capture(before)
    right = after if _is_captured(after) else capture(after)
    look = look_preservation.compare_look(left, right)
    moved = list(look.get("findings") or ())
    for row in _canvas_findings(left, right):
        moved.append({
            "shot": row.get("shot"),
            "row": row.get("row"),
            "name": row.get("name"),
            "part": "fusion",
            "field": row.get("kind"),
            "domain": row.get("domain"),
            "before": row.get("before"),
            "after": row.get("after"),
        })
    return moved


def require_preserved(before: Mapping[str, Any], after: Mapping[str, Any], *,
                      scope: Any = None, what: str = "the accepted state",
                      fail_closed: bool = True) -> dict[str, Any]:
    """The INVARIANT: an accepted item may only change when authorized.

    Raises `AcceptedStateMoved` when an accepted item moved and no
    authorized scope permits it, and `AcceptedStateUnprovable` when
    the difference cannot be judged at all (which is not a pass).
    `fail_closed=False` downgrades the second to a reported verdict
    for a caller that only wants to know.
    """
    report = verify(before, after, scope=scope)
    status = report.get("status")
    if status == VIOLATED:
        moved = [row for row in report.get("findings") or ()
                 if row.get("authorized") is False]
        named = ", ".join(
            f"{row.get('row')} {row.get('name') or row.get('shot')}: "
            f"{row.get('field')}" for row in moved[:5])
        canvas = ", ".join(
            f"{row.get('kind')}" for row in report.get("canvas") or ())
        detail = "; ".join(part for part in (named, canvas) if part)
        raise AcceptedStateMoved(
            f"{what} would change and no operation authorized it: {detail}",
            "the change is not part of the request, so it cannot be made "
            "on the accepted state",
            "say what the change is for, and it will be authorized; "
            "otherwise leave the accepted state alone")
    if status == UNPROVABLE and fail_closed:
        raise AcceptedStateUnprovable(
            f"{what} would change and no authorized scope says whether it "
            "may: a difference nobody can judge is not a pass",
            "the change is not part of the request, so it cannot be made "
            "on the accepted state",
            "say what the change is for, and it will be authorized; "
            "otherwise leave the accepted state alone")
    return report


def render(report: Mapping[str, Any]) -> str:
    """The report, in the sentences a reader has to read."""
    counts = report.get("counts") or {}
    lines = [
        f"accepted state: {report.get('status')}",
        (f"  {counts.get('changed', 0)} of "
         f"{counts.get('accepted_items', 0)} accepted item(s) moved, "
         f"{counts.get('unauthorized', 0)} of them unauthorized"),
    ]
    for row in report.get("findings") or ():
        lines.append(
            f"  {row.get('row')} {row.get('name') or row.get('shot')}: "
            f"{row.get('part')} {row.get('field')} "
            f"({row.get('authorized')})")
    for row in report.get("canvas") or ():
        lines.append(f"  {row.get('kind')}: {row.get('shot') or row.get('name')}")
    return "\n".join(lines)
