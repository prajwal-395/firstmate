"""Resolve the captain's typed timeline notes, and clear only what is proven.

`marker_feedback.py` READS the captain's notes off a Resolve timeline and
deliberately does not answer them. `marker_routing.py` routes each note to
the step that owns the decision it is about, and reports an ambiguous one
as ambiguous rather than guessing. This module is the other end: recording
that a note was ANSWERED, and clearing its marker - but only from evidence.

The captain's rule (2026-09-09): "if you resolve and verify that you have
addressed a marker in which i gave you feedback, then remove the marker to
clean up the timeline when you are done."

The three words that make this hard are "resolve AND VERIFY". A marker the
captain left is their statement that something was wrong. Removing it is a
claim that it no longer is, and a marker cleared on a worker's say-so is
worse than no cleanup: it erases the captain's own words and their record
that something was wrong, on an assertion. So:

* Every note gets a durable RESOLUTION RECORD holding the captain's
  original text verbatim, what was done, and the evidence. It lives in
  `<project>/marker_feedback/resolutions/`, beside the pull files - in the
  `Kind.CAPTURED` area a re-render cannot reach, so a rebuild neither
  erases it nor needs it.
* A marker is removed ONLY when a deterministic check proves the fix.
  A declined note is acknowledged, never addressed, and NEVER clears its
  marker. A note with no mechanical proof available is recorded as
  unverifiable and its marker stays.
* The captain's words are written to the record BEFORE the marker is
  removed. If removal fails, the record still stands and says the marker
  is still there.

The shape follows what this repo already established: PR 808's skills
contract (receipts on disk are the read-back; a self-reported check writes
no receipt and cannot pass), PR 834's `treatment_verify` (a deterministic
half carries the verdict; the model's reading is recorded rather than
enforced, AGENTS.md 10.4), and `run_pipeline`'s `note_acknowledgements`
(a reasoned DECLINE is a valid acknowledgement - of an acknowledgement,
not of a fix).

── What counts as evidence ──────────────────────────────────────────

`CHECKS` is the whole vocabulary of deterministic verification, one entry
per check. A check reads a MEASUREMENT - a plain dict counted off the
rebuilt timeline - never the worker's assertion that the fix worked. A
check that cannot fail is refused here the same way a gate that cannot
fail is refused in AGENTS.md 10.4: each check below names what failing
input looks like, and the tests prove it fails on one.

Two checks are wired, because two structural facts about a built timeline
can be counted mechanically:

* `a_roll_two_rows` - the rebuilt timeline carries two a-roll video rows.
  Fails on one row, on zero, and on a measurement that never counted.
* `motion_graphics_present` - the rebuilt timeline carries at least one
  motion-graphics item. Fails on an empty list, and on a measurement that
  never listed.

Everything else is honestly unwired. `SUGGESTED_CHECKS` maps the routed
steps those two checks can prove anything about; a note routed anywhere
else, an unrouted or ambiguous note, and above all a TASTE note ("this
segment makes it look choppy") has no mechanical proof available, and
`verifiability_of` says so plainly rather than building a placeholder.
A taste note's marker stays until the captain says otherwise.

── The removal itself ───────────────────────────────────────────────

Resolve's marker API is `DeleteMarkerAtFrame` / `DeleteMarkerByCustomData`
alongside `GetMarkers`. `marker_feedback.py`'s own docstring records that
`hasattr` is useless on Resolve proxies and that four assumptions in this
area were wrong until they were run against a real Resolve - so nothing
here guards on `hasattr`, and every deletion is judged by what the call
RETURNS, then confirmed by re-reading `GetMarkers`:

* `DeleteMarkerAtFrame` returning True removed the marker at that frame;
  anything falsy means it did not - absent, or refused - and is recorded
  as not removed, never retried blindly.
* `DeleteMarkerByCustomData` is the fallback where a record id exists,
  judged the same way. KNOWN UNKNOWN, stated plainly: which of the two
  calls actually works on a timeline marker versus a clip marker has not
  been measured against a live Resolve in this module - the captain's
  notes include both kinds and they may not delete the same way. The code
  tries the frame call first and records which call was used and what it
  returned, so either outcome is evidence rather than an assumption.
* A rebuilt timeline is a different timeline: the frame a note sat on may
  no longer mean the same thing. Clearing therefore always re-resolves
  the note's CURRENT marker key off the open timeline before deleting,
  and refuses when the note's text is no longer found there - deleting a
  marker by stale frame alone could clear a note the captain typed since.

A second known unknown, also stated: whether a marker can be identified
stably across a rebuild. The record keys on the routed `note_id`
(timeline, source, frame), which is stable across re-routings of the same
pull but NOT across a rebuild that moves the frame. The record carries
the captain's text verbatim precisely so a moved note is still findable
by its words.

Rules relocated from AGENTS.md 15
---------------------------------
These are the engine's rules for this module. They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 15 keeps the headline and
points here.

**A marker the captain left is removed only when a real check proves the
fix, never on a worker's assertion and never on a reasoned decline.**
One enumeration, `library/tools/marker_resolution.py`.
- **Every collected note gets a durable resolution record** in
  `<project>/marker_feedback/resolutions/`, holding the captain's
  original text verbatim, what was done, and the evidence. The record is
  written BEFORE the marker is removed; if removal fails the record
  still stands and says the marker is still there.
- **A declined note is acknowledged, not addressed**, and never clears
  its marker - even when a check that would pass is named alongside it.
- **A note with no mechanical proof available is recorded as
  unverifiable**, with the reason, and its marker stays. No check is
  invented so that a marker can be cleared.
- **The Resolve removal call is judged by what it actually returns**,
  confirmed by re-reading `GetMarkers`. Nothing here guards on
  `hasattr`.
- `tests/test_marker_resolution.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools.project_layout import Area, ProjectLayout  # noqa: E402
from library.tools.resolve_lock import under_lease  # noqa: E402

RESOLUTIONS_SUBDIR = "resolutions"
RESOLUTION_FORMAT = "marker_resolution/1"


# ── Statuses: what happened to a note ─────────────────────────────

STATUS_RESOLVED_VERIFIED = "resolved_verified"
"""A deterministic check proved the fix. Only this status may clear."""

STATUS_ADDRESSED_UNVERIFIED = "addressed_unverified"
"""A fix is claimed but the check failed or no measurement was supplied.
The marker stays."""

STATUS_DECLINED = "declined"
"""A reasoned decline: acknowledged, not addressed. The marker stays,
always - even beside a check that would pass."""

STATUS_UNVERIFIABLE = "unverifiable"
"""No mechanical proof exists for this kind of note. The marker stays
until the captain says otherwise."""


# ── The checks: what evidence proves a fix ────────────────────────

CHECK_A_ROLL_ROWS = "a_roll_two_rows"
CHECK_MOTION_GRAPHICS = "motion_graphics_present"


class UnknownCheck(ValueError):
    """A check name nothing in `CHECKS` provides. Refused by name, the
    same way `marker_routing` refuses a step name it does not know -
    a note verified against a misspelled check would read as verified."""


class UnknownNote(ValueError):
    """A `note_id` no pull file in this project collected. A resolution
    for a note nobody collected would be a record about nothing."""


def check_a_roll_rows(measured: dict) -> tuple:
    """(passed, evidence). The rebuilt timeline carries two a-roll rows.

    `measured` must carry `a_roll_video_rows`, counted off the rebuilt
    timeline - not asserted by whoever did the fix. Passes on two or
    more; fails on one, on zero, and on a measurement that never counted
    (a missing count is not a zero, and is said so).
    """
    measured = measured or {}
    if "a_roll_video_rows" not in measured:
        return False, {
            "reason": "no measurement supplied: `a_roll_video_rows` was "
                      "never counted off the rebuilt timeline",
        }
    try:
        rows = int(measured["a_roll_video_rows"])
    except (TypeError, ValueError):
        return False, {
            "reason": f"`a_roll_video_rows` is not a count: "
                      f"{measured['a_roll_video_rows']!r}",
        }
    return (rows >= 2, {
        "a_roll_video_rows": rows,
        "required": 2,
        "reason": ("the rebuilt timeline carries two a-roll video rows"
                   if rows >= 2 else
                   f"the rebuilt timeline carries {rows} a-roll video "
                   f"row(s); two are required"),
    })


def check_motion_graphics_present(measured: dict) -> tuple:
    """(passed, evidence). The rebuilt timeline carries motion graphics.

    `measured` must carry `motion_graphics_items`, the item names listed
    off the rebuilt timeline. Passes on a non-empty list; fails on an
    empty one, and on a measurement that never listed.
    """
    measured = measured or {}
    if "motion_graphics_items" not in measured:
        return False, {
            "reason": "no measurement supplied: `motion_graphics_items` "
                      "was never listed off the rebuilt timeline",
        }
    items = measured["motion_graphics_items"] or []
    if not isinstance(items, list):
        return False, {
            "reason": f"`motion_graphics_items` is not a list: {items!r}",
        }
    return (len(items) > 0, {
        "motion_graphics_items": list(items),
        "reason": (f"the rebuilt timeline carries {len(items)} "
                   f"motion-graphics item(s)"
                   if items else
                   "the rebuilt timeline carries no motion-graphics items"),
    })


def check_declaration_reaches_reels(measured: dict) -> tuple:
    """(passed, evidence). Every named reel CARRIES the declared element.

    The check for the commonest instruction the captain gives - *"this
    animation here is something i want applied to all of the reels being
    made"* - and the one that failed on 2026-09-11: the mechanism landed,
    the engine's capability was reported as the project's state, and six
    of eight reels did not have it.

    `measured` must carry a `divergence` survey
    (`reel_divergence.survey`) and the `declaration` key it is being
    verified for; `reels` optionally narrows it to a named set.  The
    verdict is `reel_divergence.assert_reaches`, so this check and the
    build's own report read one measurement rather than two opinions.

    Fails on a survey that read no reel, on any reel reading ABSENT, and
    on any reel reading UNDETERMINED - "we did not look" has never been
    evidence that something is there, and a marker cleared on it would
    erase the captain's words on exactly the claim that was wrong.
    """
    measured = measured or {}
    for required in ("divergence", "declaration"):
        if required not in measured:
            return False, {
                "reason": f"no measurement supplied: `{required}` was "
                          f"never read off the built reels",
            }
    from library.tools import reel_divergence

    try:
        reached = reel_divergence.assert_reaches(
            measured["divergence"], str(measured["declaration"]),
            measured.get("reels"))
    except reel_divergence.ClaimNotBackedByArtefacts as refused:
        return False, {"declaration": measured["declaration"],
                       "reason": str(refused)}
    return True, {
        "declaration": measured["declaration"],
        "reels": list(reached),
        "reason": (f"all {len(reached)} surveyed reel(s) carry "
                   f"{measured['declaration']!r}"),
    }


CHECK_DECLARATION_REACHES = "declared_element_reaches_reels"

CHECKS = {
    CHECK_A_ROLL_ROWS: check_a_roll_rows,
    CHECK_MOTION_GRAPHICS: check_motion_graphics_present,
    CHECK_DECLARATION_REACHES: check_declaration_reaches_reels,
}
"""The whole vocabulary of deterministic verification. A name outside it
is refused by `verify` rather than treated as a pass."""


SUGGESTED_CHECKS = {
    "render": (CHECK_A_ROLL_ROWS, CHECK_MOTION_GRAPHICS,
               CHECK_DECLARATION_REACHES),
    "render_motion_graphics": (CHECK_MOTION_GRAPHICS,
                               CHECK_DECLARATION_REACHES),
}
"""Where a check proves anything. `render` (6.01) builds the timeline -
its rows and what is placed on them - and `render_motion_graphics`
(4.06) decides the overlays. A note routed anywhere else has no wired
check, and `verifiability_of` says so. A suggestion is not a decision:
the worker still names the check and supplies the measurement."""


UNVERIFIABLE_STEPS_REASON = (
    "no deterministic check is wired for this step's decisions yet - "
    "taste notes especially have no mechanical proof, and the honest "
    "answer is that the marker stays until the captain says otherwise"
)


def verify(check_name: str, measured: dict) -> tuple:
    """Run the named check against the measurement. (passed, evidence).

    Raises `UnknownCheck` for a name outside `CHECKS` - an unknown check
    is refused, never treated as a pass.
    """
    try:
        check = CHECKS[check_name]
    except KeyError:
        raise UnknownCheck(
            f"check {check_name!r} is not one this pipeline can verify "
            f"with. Known checks: {sorted(CHECKS)}. A fix verified "
            f"against a check nobody provides is a worker's assertion "
            f"with a name on it."
        ) from None
    return check(measured or {})


def verifiability_of(note: dict) -> tuple:
    """(verifiable, check_or_reason) for one routed note.

    Verifiable means a deterministic check EXISTS for the step this note
    was routed to - not that the fix passes it. Only a `routed` note to
    a step in `SUGGESTED_CHECKS` qualifies; ambiguous, unrouted and
    unknown-step notes name no single decision to prove, and taste notes
    routed anywhere else have no mechanical proof at all. Both come back
    as unverifiable with the reason stated.
    """
    from library.tools import marker_routing as routing

    outcome = (note or {}).get("outcome", "")
    if outcome != routing.OUTCOME_ROUTED:
        return False, (
            f"the note is {outcome or 'unrouted'} - it names no single "
            f"step's decision, so no check can prove it addressed"
        )
    steps = (note or {}).get("steps") or []
    if len(steps) != 1:
        return False, (
            "the note names no single step's decision, so no check can "
            "prove it addressed"
        )
    suggested = SUGGESTED_CHECKS.get(steps[0])
    if not suggested:
        return False, (
            f"the note is routed to `{steps[0]}`, and "
            f"{UNVERIFIABLE_STEPS_REASON}"
        )
    return True, ",".join(suggested)


# ── Declines: acknowledged, never addressed ───────────────────────

def is_decline(action: str) -> bool:
    """Is this acknowledgement a reasoned decline?

    `run_pipeline` asks the model for `note_id`, `action` and
    `rationale`, with a reasoned DECLINE as a valid acknowledgement.
    The convention, stated in the schema description it renders: an
    action reading "decline ..." / "declined ..." is a decline. Anything
    else claims a fix. The prefix match is deliberate - "declined: the
    passage is fine as cut" is a decline, while "fixed the framing and
    declined further tweaks" is a fix claim and must face a check.
    """
    return (action or "").strip().lower().startswith("declin")


# ── The durable record ────────────────────────────────────────────

@dataclass
class ResolutionRecord:
    """What happened to one of the captain's notes.

    `name`, `note` and `text` are the captain's own words, verbatim -
    the same fields `marker_feedback` keeps. They are written BEFORE any
    marker is removed, so a failed removal still leaves the words.
    """

    note_id: str
    status: str
    action: str = ""
    rationale: str = ""
    check: str = ""
    evidence: dict = field(default_factory=dict)
    verifier: str = ""

    name: str = ""
    note: str = ""
    text: str = ""
    source: str = ""
    frame: object = None
    frame_in_timeline_space: object = None
    timecode: object = None
    timeline: str = ""
    pull_file: str = ""
    collected_at: str = ""

    marker_removed: bool = False
    marker_still_present: bool = True
    removal: dict = field(default_factory=dict)

    resolved_at: str = ""
    format: str = RESOLUTION_FORMAT


def _safe_filename(note_id: str) -> str:
    stem = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in (note_id or "note")
    ) or "note"
    return f"{stem}.resolution.json"


def resolution_path(project_folder, note_id: str) -> Path:
    """The file this note's resolution record lives in."""
    layout = ProjectLayout(project_folder)
    return layout.write_path(Area.MARKER_FEEDBACK, RESOLUTIONS_SUBDIR,
                             _safe_filename(note_id))


def read_resolution(project_folder, note_id: str):
    """This note's resolution record, or None when it has none."""
    layout = ProjectLayout(project_folder)
    path = layout.read_path(Area.MARKER_FEEDBACK, RESOLUTIONS_SUBDIR,
                            _safe_filename(note_id))
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def all_resolutions(project_folder) -> list:
    """Every resolution record in this project, oldest first."""
    layout = ProjectLayout(project_folder)
    directory = layout.read_dir(Area.MARKER_FEEDBACK) / RESOLUTIONS_SUBDIR
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.resolution.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def _note_text_fields(note: dict) -> dict:
    return {
        "name": note.get("name", ""),
        "note": note.get("note", ""),
        "text": note.get("text") or "\n\n".join(
            p for p in (note.get("name") or "", note.get("note") or "")
            if p),
        "source": note.get("source", ""),
        "frame": note.get("frame"),
        "frame_in_timeline_space": note.get("frame_in_timeline_space"),
        "timecode": note.get("timecode"),
        "timeline": note.get("timeline", ""),
        "pull_file": note.get("pull_file", ""),
        "collected_at": note.get("collected_at", ""),
    }


def record_resolution(project_folder, note: dict, status: str, action: str = "",
                      rationale: str = "", check: str = "",
                      evidence=None, verifier: str = "",
                      marker_removed: bool = False,
                      marker_still_present: bool = True,
                      removal=None) -> dict:
    """Write this note's resolution record. Returns the record dict.

    The captain's words go in first, unconditionally: whatever happens
    after - a passed check, a failed deletion - the words survive it.
    """
    if status not in (STATUS_RESOLVED_VERIFIED,
                      STATUS_ADDRESSED_UNVERIFIED,
                      STATUS_DECLINED, STATUS_UNVERIFIABLE):
        raise ValueError(
            f"status {status!r} is not a resolution status this module "
            f"records - inventing one would let a reader misread what "
            f"happened to the marker")
    record = ResolutionRecord(
        note_id=note.get("note_id", ""),
        status=status,
        action=action or "",
        rationale=rationale or "",
        check=check or "",
        evidence=dict(evidence or {}),
        verifier=verifier or "",
        marker_removed=bool(marker_removed),
        marker_still_present=bool(marker_still_present),
        removal=dict(removal or {}),
        resolved_at=datetime.now(timezone.utc).isoformat(),
        **_note_text_fields(note),
    )
    payload = asdict(record)
    resolution_path(project_folder, record.note_id).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def find_note(project_folder, note_id: str):
    """The collected note `note_id` names, or None.

    Read off the project's pull files through the router, so the record
    carries the same id the captain's report uses. A resolution for a
    note nobody collected is refused by `resolve_note` - see
    `UnknownNote`.
    """
    from library.tools import marker_routing as routing

    for routed in routing.route_project(project_folder):
        if routed.note_id == note_id:
            return asdict(routed)
    return None


# ── The removal: judged by what Resolve returns ───────────────────
#
# Nothing here guards on `hasattr` - it is always True on Resolve's
# proxies, including invented names (`marker_feedback`). The calls are
# made and their returns are read. A missing method (a plain object
# with no such call) raises AttributeError, which is recorded as "not
# removed" with the reason - never as a pass.

def _confirm_absent(get_markers, key) -> bool:
    """Is `key` gone from `get_markers()` now? A re-read that raises is
    not a confirmation: absence unconfirmed is still present."""
    try:
        markers = get_markers() or {}
    except Exception:
        return False
    return key not in markers and int(key) not in {
        int(k) for k in markers
        if str(k).lstrip("-").isdigit()
    }


@under_lease("delete a timeline marker on resolution")
def delete_timeline_marker(timeline, frame_key) -> dict:
    """Delete the timeline marker at `frame_key`. Judged, then confirmed.

    Calls `DeleteMarkerAtFrame` and reads what it returned: True means
    Resolve removed it, anything falsy means it did not. A True that
    leaves the key still readable in `GetMarkers` is recorded as not
    removed - the return is trusted only as far as the re-read confirms.
    """
    outcome = {"called": "DeleteMarkerAtFrame", "frame": int(frame_key),
               "returned": None, "still_present": True, "removed": False}
    try:
        returned = timeline.DeleteMarkerAtFrame(int(frame_key))
    except Exception as exc:
        outcome["reason"] = (
            f"DeleteMarkerAtFrame raised {type(exc).__name__}: {exc}")
        return outcome
    outcome["returned"] = bool(returned)
    if not returned:
        outcome["reason"] = (
            "DeleteMarkerAtFrame returned falsy - the marker is absent "
            "at that frame, or Resolve refused the deletion")
        return outcome
    try:
        absent = _confirm_absent(timeline.GetMarkers, int(frame_key))
    except Exception as exc:
        outcome["reason"] = (
            f"DeleteMarkerAtFrame returned True but GetMarkers raised "
            f"{type(exc).__name__}: {exc} - removal unconfirmed")
        return outcome
    outcome["still_present"] = not absent
    outcome["removed"] = bool(absent)
    if not absent:
        outcome["reason"] = (
            "DeleteMarkerAtFrame returned True but the marker still "
            "reads back in GetMarkers")
    return outcome


@under_lease("delete a clip marker on resolution")
def delete_clip_marker(item, source_frame) -> dict:
    """Delete the clip marker at `source_frame`. Same contract as
    `delete_timeline_marker`: the return is read, then the re-read
    confirms. Clip marker keys are SOURCE frames - the same space as
    `GetLeftOffset()` (`marker_feedback`) - so no conversion happens
    here; the caller passes the note's `frame_in_timeline_space`."""
    outcome = {"called": "DeleteMarkerAtFrame", "frame": int(source_frame),
               "returned": None, "still_present": True, "removed": False,
               "marker_kind": "clip_marker"}
    try:
        returned = item.DeleteMarkerAtFrame(int(source_frame))
    except Exception as exc:
        outcome["reason"] = (
            f"DeleteMarkerAtFrame raised {type(exc).__name__}: {exc}")
        return outcome
    outcome["returned"] = bool(returned)
    if not returned:
        outcome["reason"] = (
            "DeleteMarkerAtFrame returned falsy - the marker is absent "
            "at that source frame, or Resolve refused the deletion")
        return outcome
    try:
        absent = _confirm_absent(item.GetMarkers, int(source_frame))
    except Exception as exc:
        outcome["reason"] = (
            f"DeleteMarkerAtFrame returned True but GetMarkers raised "
            f"{type(exc).__name__}: {exc} - removal unconfirmed")
        return outcome
    outcome["still_present"] = not absent
    outcome["removed"] = bool(absent)
    if not absent:
        outcome["reason"] = (
            "DeleteMarkerAtFrame returned True but the marker still "
            "reads back in GetMarkers")
    return outcome


@under_lease("delete a marker by customData on resolution")
def delete_marker_by_custom_data(timeline_or_item, custom_data: str) -> dict:
    """Fallback deletion by `customData`. Judged the same way.

    Only called where a record id exists to delete by - never with an
    empty string, which Resolve would be free to read as "everything".
    Whether this call works on timeline versus clip markers has not been
    measured against a live Resolve in this module (see the docstring):
    the outcome records which object it was called on and what came
    back, so the answer accumulates instead of being assumed.
    """
    outcome = {"called": "DeleteMarkerByCustomData",
               "custom_data": custom_data or "",
               "returned": None, "still_present": True, "removed": False}
    if not custom_data:
        outcome["reason"] = (
            "no customData to delete by - refusing an unscoped deletion")
        return outcome
    try:
        returned = timeline_or_item.DeleteMarkerByCustomData(custom_data)
    except Exception as exc:
        outcome["reason"] = (
            f"DeleteMarkerByCustomData raised {type(exc).__name__}: {exc}")
        return outcome
    outcome["returned"] = bool(returned)
    outcome["removed"] = bool(returned)
    outcome["still_present"] = not bool(returned)
    if not returned:
        outcome["reason"] = (
            "DeleteMarkerByCustomData returned falsy - nothing carrying "
            "that customData was removed")
    return outcome


# ── Putting it together ───────────────────────────────────────────

def _current_marker_key(note: dict, timeline=None, item=None):
    """The marker key to delete, re-resolved off the OPEN timeline.

    A rebuilt timeline is a different timeline: the frame a note sat on
    when it was pulled may no longer mean the same thing. So the key is
    confirmed, not assumed - the note's text must still read back at the
    candidate key, by name+note, before anything is deleted. A note whose
    text is no longer found comes back as `(None, reason)` and the
    marker is left alone: deleting by stale frame could clear a note the
    captain typed since.
    """
    source = (note or {}).get("source", "")
    if source == "timeline_marker":
        if timeline is None:
            return None, "no open timeline to re-resolve the marker against"
        try:
            markers = timeline.GetMarkers() or {}
            start = int(timeline.GetStartFrame())
        except Exception as exc:
            return None, (
                f"could not read the open timeline: "
                f"{type(exc).__name__}: {exc}")
        key = (note or {}).get("frame_in_timeline_space")
        if key is None:
            return None, "the note has no timeline-space frame to delete by"
        marker = None
        for raw_key, candidate in markers.items():
            try:
                if int(raw_key) == int(key):
                    marker = candidate
                    break
            except (TypeError, ValueError):
                continue
        if marker is None:
            return None, (
                f"no timeline marker reads back at frame {key} - the "
                f"timeline was rebuilt since, or the marker is already gone")
        for field in ("name", "note"):
            want = (note or {}).get(field) or ""
            got = (marker.get(field) if isinstance(marker, dict) else "") or ""
            if want and want != got:
                return None, (
                    f"the marker at frame {key} no longer carries this "
                    f"note's {field} - refusing a deletion by stale frame")
        return int(key), ""
    if source in ("clip_marker", "media_pool_marker", "clip_comment"):
        if item is None:
            return None, "no open clip item to re-resolve the marker against"
        key = (note or {}).get("frame_in_timeline_space")
        if key is None:
            return None, "the note has no source frame to delete by"
        try:
            markers = item.GetMarkers() or {}
        except Exception as exc:
            return None, (
                f"could not read the clip's markers: "
                f"{type(exc).__name__}: {exc}")
        found = False
        for raw_key in markers:
            try:
                if int(raw_key) == int(key):
                    found = True
                    break
            except (TypeError, ValueError):
                continue
        if not found:
            return None, (
                f"no clip marker reads back at source frame {key} - the "
                f"timeline was rebuilt since, or the marker is already gone")
        return int(key), ""
    return None, f"unknown marker source {source!r} - refusing the deletion"


def resolve_note(project_folder, note: dict, action: str, rationale: str,
                 check: str = "", measured=None, verifier: str = "",
                 timeline=None, item=None) -> dict:
    """Answer one collected note, clearing its marker only from proof.

    * A decline (`is_decline(action)`) is recorded as `declined` and the
      timeline is never touched - even beside a named check that would
      pass. A decline must never clear the marker.
    * With no check named, or a check no measurement can feed, the note
      is recorded as `addressed_unverified` (a fix was claimed) or
      `unverifiable` (none was claimed and none exists for its kind) -
      and the marker stays.
    * With a check named, the check runs against `measured`. Unknown
      check names raise `UnknownCheck` and touch nothing. A failed check
      records `addressed_unverified` and the marker stays. A passed
      check records `resolved_verified` FIRST - the words safe - then
      deletes, re-reads, and updates the record with the removal
      outcome. A failed deletion leaves the words, the evidence, and
      `marker_still_present: True`.

    Returns `{"record": ..., "marker_touched": bool}`.
    """
    action = action or ""
    rationale = rationale or ""

    if is_decline(action):
        record = record_resolution(
            project_folder, note, STATUS_DECLINED, action, rationale,
            check=check or "", evidence={},
            verifier=verifier or "marker_resolution.is_decline",
            marker_removed=False, marker_still_present=True,
            removal={"marker_touched": False,
                     "reason": "a declined note is acknowledged, not "
                               "addressed - its marker stays"})
        return {"record": record, "marker_touched": False}

    if not check:
        verifiable, why = verifiability_of(note)
        if verifiable:
            record = record_resolution(
                project_folder, note, STATUS_ADDRESSED_UNVERIFIED,
                action, rationale, check="", evidence={},
                verifier=verifier or "marker_resolution.verifiability_of",
                marker_removed=False, marker_still_present=True,
                removal={"marker_touched": False,
                         "reason": f"a fix was claimed but no check was "
                                   f"named to prove it ({why}); the marker "
                                   f"stays until one does"})
        else:
            record = record_resolution(
                project_folder, note, STATUS_UNVERIFIABLE,
                action, rationale, check="", evidence={},
                verifier=verifier or "marker_resolution.verifiability_of",
                marker_removed=False, marker_still_present=True,
                removal={"marker_touched": False, "reason": why})
        return {"record": record, "marker_touched": False}

    passed, evidence = verify(check, measured or {})
    if not passed:
        record = record_resolution(
            project_folder, note, STATUS_ADDRESSED_UNVERIFIED,
            action, rationale, check=check, evidence=evidence,
            verifier=verifier or f"marker_resolution.CHECKS[{check}]",
            marker_removed=False, marker_still_present=True,
            removal={"marker_touched": False,
                     "reason": "the named check did not pass on the "
                               "supplied measurement - the marker stays"})
        return {"record": record, "marker_touched": False}

    # PASSED. The words go down first - before anything is deleted.
    record = record_resolution(
        project_folder, note, STATUS_RESOLVED_VERIFIED,
        action, rationale, check=check, evidence=evidence,
        verifier=verifier or f"marker_resolution.CHECKS[{check}]",
        marker_removed=False, marker_still_present=True,
        removal={"marker_touched": False,
                 "reason": "check passed; removal pending"})
    key, refusal = _current_marker_key(note, timeline=timeline, item=item)
    if key is None:
        record = record_resolution(
            project_folder, note, STATUS_RESOLVED_VERIFIED,
            action, rationale, check=check, evidence=evidence,
            verifier=verifier or f"marker_resolution.CHECKS[{check}]",
            marker_removed=False, marker_still_present=True,
            removal={"marker_touched": False, "reason": refusal})
        return {"record": record, "marker_touched": False}
    source = (note or {}).get("source", "")
    if source == "timeline_marker":
        outcome = delete_timeline_marker(timeline, key)
    else:
        outcome = delete_clip_marker(item, key)
    record = record_resolution(
        project_folder, note, STATUS_RESOLVED_VERIFIED,
        action, rationale, check=check, evidence=evidence,
        verifier=verifier or f"marker_resolution.CHECKS[{check}]",
        marker_removed=bool(outcome.get("removed")),
        marker_still_present=bool(outcome.get("still_present", True)),
        removal={**outcome, "marker_touched": True})
    return {"record": record, "marker_touched": True}


# ── CLI ───────────────────────────────────────────────────────────

def _render_record(record: dict) -> str:
    lines = [
        f"  {record.get('note_id')} [{record.get('status')}]",
        f"    captain : {(record.get('text') or '(no text)').splitlines()[0][:80]}",
        f"    action  : {(record.get('action') or '-')[:80]}",
    ]
    if record.get("check"):
        lines.append(
            f"    check   : {record['check']} -> "
            f"{json.dumps(record.get('evidence') or {})[:160]}")
    lines.append(
        f"    marker  : {'REMOVED' if record.get('marker_removed') else 'still present'}")
    if (record.get("removal") or {}).get("reason"):
        lines.append(f"              {record['removal']['reason'][:120]}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.marker_resolution",
        description="Record what happened to the captain's timeline notes, "
                    "and clear only the markers a real check proves fixed.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_show = sub.add_parser(
        "show", help="print every resolution record, writing nothing")
    p_show.add_argument("--project", required=True)

    p_record = sub.add_parser(
        "record", help="record a decline or an unverifiable note (never "
                       "touches the timeline)")
    p_record.add_argument("--project", required=True)
    p_record.add_argument("--note", required=True,
                          help="the routed note_id to record against")
    p_record.add_argument("--action", required=True)
    p_record.add_argument("--rationale", default="")
    p_record.add_argument("--verifier", default="")

    p_clear = sub.add_parser(
        "clear", help="verify a fix against a named check and, only on a "
                      "pass, remove the marker from the open timeline")
    p_clear.add_argument("--project", required=True)
    p_clear.add_argument("--note", required=True)
    p_clear.add_argument("--action", required=True)
    p_clear.add_argument("--rationale", default="")
    p_clear.add_argument("--check", required=True)
    p_clear.add_argument("--measured", default="{}",
                         help="JSON measurement the check reads")
    p_clear.add_argument("--verifier", default="")

    args = parser.parse_args(argv)

    if args.command == "show":
        records = all_resolutions(args.project)
        if not records:
            print(f"  (no resolutions recorded in {args.project})")
            return 0
        for record in records:
            print(_render_record(record))
        return 0

    note = find_note(args.project, args.note)
    if note is None:
        print(f"✗ no collected note {args.note!r} in {args.project} - "
              f"a resolution for a note nobody collected is refused",
              file=sys.stderr)
        return 2

    if args.command == "record":
        result = resolve_note(args.project, note, args.action,
                              args.rationale, verifier=args.verifier)
        print(_render_record(result["record"]))
        return 0

    from library.tools import marker_feedback

    try:
        timeline, _project = marker_feedback.current_timeline()
    except marker_feedback.ResolveUnavailable as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 3
    try:
        measured = json.loads(args.measured)
    except Exception as exc:
        print(f"✗ --measured is not JSON: {exc}", file=sys.stderr)
        return 2
    # A clip note's marker lives on its item, not on the timeline. The
    # item is found the way the reader finds context: the placement the
    # note's own frame lands on.
    item = None
    if (note.get("source") or "") != "timeline_marker":
        frame = note.get("frame")
        if frame is not None:
            for track_type in ("video", "audio"):
                try:
                    count = timeline.GetTrackCount(track_type) or 0
                except Exception:
                    continue
                for index in range(1, count + 1):
                    try:
                        items = timeline.GetItemListInTrack(
                            track_type, index) or []
                    except Exception:
                        continue
                    for candidate in items:
                        try:
                            if (candidate.GetStart() <= frame
                                    < candidate.GetEnd()):
                                item = candidate
                                break
                        except Exception:
                            continue
                    if item is not None:
                        break
                if item is not None:
                    break
    try:
        result = resolve_note(args.project, note, args.action,
                              args.rationale, check=args.check,
                              measured=measured,
                              verifier=args.verifier or "cli:clear",
                              timeline=timeline, item=item)
    except UnknownCheck as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    print(_render_record(result["record"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
