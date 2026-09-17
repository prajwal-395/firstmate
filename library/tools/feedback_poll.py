"""Poll DaVinci Resolve for feedback markers nobody has handled yet.

The captain summons the agent by naming a marker "feedback" - the Name
field of Resolve's Add Marker dialog, verbatim.  That convention is his,
established without being asked: his review notes are BLUE CLIP markers
whose name is exactly the word "feedback" and whose note body carries
the request.  This module notices a new one.  It does not interpret it
and does not act on it; turning a note into a structured edit is a
separate, deliberately unshaped task.

── Why the trigger is the marker's name ───────────────────────────────

The captain raised a "keyword section" as one possible home for the
trigger.  Measured against the live API (Resolve Studio, `GetMarkers()`
on four real markers across two timelines): a marker exposes EXACTLY
`color`, `duration`, `name`, `note` and `customData` - there is NO
keywords field on a marker.  Keywords are a MEDIA POOL clip property
(`GetClipProperty("Keywords")`, the same field `resolve_organization`
stamps reel state onto).  So there is no second channel to build on,
and none is invented: his own naming convention is the answer.  A
marker whose name, stripped and case-folded, is `feedback` is a
summons; anything else is not.

── What this module does ─────────────────────────────────────────────

One entry point, `python3 -m library.tools.feedback_poll --project DIR`,
that prints a JSON report of every UNHANDLED feedback marker and
records them handed over, so the next run stays silent.  Firstmate
registers this command as a poll on its own side; the report shape is
that poll's contract and is versioned (`feedback_poll/1`).

Two details decide whether it works at all:

* BOTH MARKER STORES ARE READ.  A timeline's own `GetMarkers()` does
  NOT return clip markers, and both of the captain's notes are clip
  markers.  Every timeline's items are walked and each item's markers
  read too, through `marker_feedback.read_notes`, which already knows
  both stores.  A version reading only the timeline level finds
  nothing and reports "no feedback" - exactly the wrong failure.
* THE CAPTAIN'S SESSION IS NOT DISTURBED.  Marker text is not a
  transform, so no timeline needs to be current: every timeline is
  read through its own handle, `SetCurrentTimeline` is never called,
  and the current timeline and playhead are left exactly as found.
  The scan takes a SHARED lease only, so it never excludes another
  reader, and it honours no captain hold - it writes nothing into
  Resolve, so the hold's guarantee ("no agent writes") is untouched
  by it.  It does not open Resolve when it is closed; that state is
  reported rather than launched through.

── Where handled-ness lives, and what counts as new ──────────────────

In `<project>/marker_feedback/feedback_handover.json` (`Kind.CAPTURED`,
beside the pull files a re-run cannot reach), never in the marker's
own `customData` slot.  Two reasons: writing `customData` means
writing into the captain's live project while he may be working, and
a rebuild retires the timeline the marker sits on, taking the slot
with it - while the handover file survives both, which is what
"prefer whichever survives a rebuild" decides.

The identity is the reel (by base name, so a staging rename does not
re-arm it), which store the marker sits in, where in that store, the
clip it sits on for a clip marker, and the EXACT name, note and
written attachments.  An edited note - any byte of name or note
changed - is a new identity and is reported again; a captured still
landing on an already-handed note re-arms it too, because the pull
file no longer says everything the marker does.  Colour is not part
of it: this module has no colour vocabulary by design, and recolouring
a marker must not look like a new question.

Our own replies never trigger: a marker whose name starts with
`reply:` (the writer's stamp, `feedback_ledger.REPLY_NAME_PREFIX`) or
whose `customData` carries a `marker_feedback` reply record is ours,
and an unmarked marker is the captain's - filing an unmarked note as
ours would LOSE their question.

── The contract firstmate polls on ───────────────────────────────────

* Exit 0, stdout EMPTY (zero bytes): looked, nothing new.  That
  silence is what makes it usable as a poll.
* Exit 0, stdout a single `feedback_poll/1` JSON document: looked,
  these markers are new.  Printing IS the handover: every printed
  marker is recorded handled in the same run, unless `--peek`.
* Exit 3, stderr names the reason: could not look (Resolve not
  running, no project open, the open project is not this project's
  declared one, or the instance stayed busy past `--timeout`).
* Exit 2: usage error (argparse).

`--peek` prints what would be reported without recording anything -
the mode the live proof runs in, so proving the scan never consumes
live work items.  `--forget IDENTITY` drops one handled row (a
handover firstmate discarded and wants re-reported); it reports to
stderr so stdout stays JSON-or-empty.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools import marker_feedback  # noqa: E402
from library.tools.feedback_ledger import (  # noqa: E402
    REPLY_NAME_PREFIX,
    base_reel_name,
)
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402
from library.tools.resolve_lock import resolve_lease  # noqa: E402

POLL_FORMAT = "feedback_poll/1"
HANDOVER_FILENAME = "feedback_handover.json"
HANDOVER_FORMAT = "feedback_handover/1"

TRIGGER_WORD = "feedback"
"""The marker NAME that summons the agent, stripped and case-folded.

The captain's own convention, read rather than designed.  Matched on
the name alone: colour carries no meaning by design, and markers
expose no keywords field to match on.
"""

EXIT_CANNOT_LOOK = 3
DEFAULT_LEASE_TIMEOUT = 30.0


class CannotLook(RuntimeError):
    """Resolve could not be read.  "No markers" and "I could not look"
    are different answers (see `marker_feedback.ResolveUnavailable`)."""


# ── The trigger ─────────────────────────────────────────────────────


def is_feedback_name(name: str) -> bool:
    """Is this marker name the summons?  Exact word, any case, padded
    or not.  "feedback please" is a sentence, not the word, and does
    not trigger - the convention is one word and the match says so."""
    return (name or "").strip().casefold() == TRIGGER_WORD


def _carries_our_reply(note) -> bool:
    """Did WE write this marker back?  The name shape first (cheap),
    then the machine record in `customData` (exact)."""
    first = (note.name or "").splitlines()[0] if note.name else ""
    if first.strip().lower().startswith(REPLY_NAME_PREFIX):
        return True
    try:
        if marker_feedback.reply_records_in(note.custom_data):
            return True
    except Exception:  # noqa: BLE001 - a hostile customData never hides a note
        pass
    try:
        if marker_feedback.reply_records_in(note.custom_data_raw):
            return True
    except Exception:  # noqa: BLE001 - same: read trouble reads as "not ours"
        pass
    return False


def is_summons(note) -> bool:
    """A feedback marker the agent should be called for: the trigger
    word for a name, and not a reply of ours wearing it."""
    return is_feedback_name(note.name) and not _carries_our_reply(note)


# ── The identity ────────────────────────────────────────────────────


def _clip_key(note) -> str:
    """Which clip a clip marker sits on, stably across a rebuild: the
    clip's name, its source file's base name, and the source-frame key
    the marker sits at.  A rebuild re-places the same file, so all
    three survive it; the timeline frame does not and is not keyed."""
    clip = note.attached_clip or {}
    source_file = str(clip.get("source_file") or "")
    return "|".join((
        str(clip.get("name") or ""),
        Path(source_file).name if source_file else "",
        str(note.frame_in_timeline_space),
    ))


def marker_identity(timeline_name: str, note) -> str:
    """The handover identity: reel + store + position + exact words.

    Readable prefix, then a digest - the same shape
    `feedback_ledger.durable_identity` takes.  An edited note is a new
    identity; a recoloured one is not; a rebuilt one still sitting on
    the same clip source frame is not."""
    reel = base_reel_name(timeline_name)
    if note.source in ("clip_marker", "media_pool_marker", "clip_comment"):
        where = f"{note.source}:{_clip_key(note)}"
    elif note.frame_in_timeline_space is None:
        where = "timeline_marker:unplaced"
    else:
        where = f"timeline_marker:{note.frame_in_timeline_space}"
    attachments = tuple(sorted(
        str(a.get("path") or "") for a in (note.attachments or [])
        if isinstance(a, dict) and a.get("origin") == "custom_data"
        and a.get("path")
    ))
    payload = "\n".join((note.source, where, note.name or "",
                         note.note or "", *attachments)).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in reel)
    return f"{stem or 'timeline'}:{digest}"


# ── The handover file ───────────────────────────────────────────────


def handover_path(project_folder) -> Path:
    return ProjectLayout(project_folder).write_path(
        Area.MARKER_FEEDBACK, HANDOVER_FILENAME)


def read_handover(project_folder) -> dict:
    """`{identity: row}` handed over so far.  `{}` for none, and for a
    file that will not parse - a corrupt handover re-reports rather
    than losing the captain's questions."""
    path = ProjectLayout(project_folder).read_path(
        Area.MARKER_FEEDBACK, HANDOVER_FILENAME)
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - see docstring: fail open, never lose
        return {}
    if not isinstance(document, dict):
        return {}
    handed = document.get("handed")
    return handed if isinstance(handed, dict) else {}


def write_handover(project_folder, handed: dict) -> Path:
    path = handover_path(project_folder)
    document = {
        "format": HANDOVER_FORMAT,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "handed": handed,
    }
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


# ── The scan ────────────────────────────────────────────────────────


def _expected_resolve_project(project_folder) -> str:
    """The Resolve project name this pipeline project declares, or ""
    when it declares none.  Compared EXACT: AGENTS.md 5 refuses prefix
    matches for project names."""
    try:
        from library.schemas.project_config import load_project_config
    except Exception:  # noqa: BLE001 - schemas stay optional for the poll
        return ""
    try:
        config = load_project_config(Path(project_folder) / "project.yaml")
    except Exception:  # noqa: BLE001 - no config, no expectation
        return ""
    return str((config.resolve.project_name if config.resolve else "") or "")


def iter_timelines(project):
    """Every timeline in the project, oldest index first.  Handles
    only - nothing here makes any timeline current."""
    count = project.GetTimelineCount() or 0
    for index in range(1, count + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is not None:
            yield timeline


def scan(resolve, project_folder, verbose: bool = False) -> tuple:
    """`(summons, resolve_project_name)`: every feedback marker on
    every timeline, oldest timeline first, in timeline order within.

    Read-only end to end: no cursor move, no marker write, no pull
    file.  Raises `CannotLook` when there is nothing safe to read."""
    manager = resolve.GetProjectManager()
    if manager is None:
        raise CannotLook("Resolve returned no project manager.")
    project = manager.GetCurrentProject()
    if project is None:
        raise CannotLook("Resolve has no project open.")
    resolve_name = project.GetName() or ""
    expected = _expected_resolve_project(project_folder)
    if expected and resolve_name != expected:
        raise CannotLook(
            f"Resolve has project {resolve_name!r} open, but this "
            f"pipeline project declares {expected!r}.  Refusing rather "
            f"than scanning another project's timelines and filing "
            f"their markers here.")
    before = None
    try:
        current = project.GetCurrentTimeline()
        before = current.GetName() if current else None
    except Exception:  # noqa: BLE001 - the name is a courtesy, not control
        before = None
    summons = []
    for timeline in iter_timelines(project):
        name = timeline.GetName()
        try:
            notes = marker_feedback.read_notes(timeline, project_folder)
        except Exception as exc:  # noqa: BLE001 - one unreadable timeline
            raise CannotLook(
                f"Timeline {name!r} could not be read: {exc}") from exc
        hits = [n for n in notes if is_summons(n)]
        if verbose:
            print(f"  {name}: {len(notes)} note(s), "
                  f"{len(hits)} feedback summons",
                  file=sys.stderr)
        for note in hits:
            summons.append((name, note))
    if verbose:
        try:
            current = project.GetCurrentTimeline()
            after = current.GetName() if current else None
        except Exception:  # noqa: BLE001 - same courtesy as above
            after = None
        print(f"  current timeline before {before!r}, after {after!r}",
              file=sys.stderr)
    return summons, resolve_name


def _still_of(note) -> tuple:
    """`(resolved path, exists)`: the captured frame, if one is on
    this marker and on disk.  `("", False)` otherwise - an absent
    capture is reported, never a path to nothing."""
    for attachment in note.attachments or []:
        if not isinstance(attachment, dict):
            continue
        if attachment.get("origin") != "custom_data":
            continue
        resolved = str(attachment.get("resolved_path") or "")
        if resolved:
            return resolved, bool(attachment.get("exists"))
    return "", False


def report_row(timeline_name: str, note, identity: str) -> dict:
    """One marker, as the poll prints it.  Fixed keys: the poll is
    bound to these bytes and a later rename breaks firstmate's poll
    silently, so a key changes here only with a format bump."""
    clip = note.attached_clip or {}
    still, still_exists = _still_of(note)
    track = ""
    if clip.get("track_type") is not None:
        track = f"{clip.get('track_type')}{clip.get('track_index')}"
    return {
        "identity": identity,
        "timeline": timeline_name,
        "reel": base_reel_name(timeline_name),
        "source": note.source,
        "clip": str(clip.get("name") or ""),
        "clip_track": track,
        "clip_source_file": str(clip.get("source_file") or ""),
        "timeline_frame": note.frame,
        "timecode": note.timecode,
        "source_frame": note.frame_in_timeline_space,
        "name": note.name,
        "note": note.note,
        "color": note.color,
        "still": still if still_exists else "",
        "still_exists": still_exists,
    }


def handover_row(timeline_name: str, note, identity: str) -> dict:
    """What the handover file remembers: enough to audit, keyed by
    the identity.  The full words are kept - a digest alone cannot
    say what was handed over a month later."""
    return {
        "identity": identity,
        "handed_at": datetime.now(timezone.utc).isoformat(),
        "timeline": timeline_name,
        "reel": base_reel_name(timeline_name),
        "source": note.source,
        "clip": str((note.attached_clip or {}).get("name") or ""),
        "frame": note.frame,
        "frame_in_timeline_space": note.frame_in_timeline_space,
        "name": note.name,
        "note": note.note,
    }


# ── The command ─────────────────────────────────────────────────────


def run_poll(project_folder, peek: bool = False,
             lease_timeout: float = DEFAULT_LEASE_TIMEOUT,
             verbose: bool = False) -> tuple:
    """`(rows, resolve_name)`: unhandled feedback markers, in scan
    order.  Records them handed over unless `peek`."""
    started = time.time()
    try:
        with resolve_lease("poll feedback markers", exclusive=False,
                           timeout=lease_timeout, honor_captain=False):
            resolve = marker_feedback.connect_resolve()
            summons, resolve_name = scan(resolve, project_folder,
                                         verbose=verbose)
    except marker_feedback.ResolveUnavailable as exc:
        raise CannotLook(str(exc)) from exc
    handed = read_handover(project_folder)
    fresh = [(name, note, marker_identity(name, note))
             for name, note in summons
             if marker_identity(name, note) not in handed]
    if verbose:
        print(f"  {len(summons)} summons, {len(handed)} handed over, "
              f"{len(fresh)} new, "
              f"{time.time() - started:.1f}s", file=sys.stderr)
    if fresh and not peek:
        for name, note, identity in fresh:
            handed[identity] = handover_row(name, note, identity)
        write_handover(project_folder, handed)
    return ([report_row(name, note, identity)
             for name, note, identity in fresh], resolve_name)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.feedback_poll",
        description="Report feedback markers nobody has handled yet; "
                    "print nothing when there is nothing new.",
    )
    parser.add_argument("--project", required=True,
                        help="the pipeline project folder whose handover "
                             "file records what was reported")
    parser.add_argument("--peek", action="store_true",
                        help="print what would be reported without "
                             "recording anything as handed over")
    parser.add_argument("--forget", default="",
                        help="drop one handled IDENTITY so it reports "
                             "again, then exit")
    parser.add_argument("--timeout", type=float,
                        default=DEFAULT_LEASE_TIMEOUT,
                        help="seconds to wait for the Resolve instance "
                             f"(default {DEFAULT_LEASE_TIMEOUT:g})")
    parser.add_argument("--verbose", action="store_true",
                        help="per-timeline counts and timing to stderr; "
                             "stdout stays JSON-or-empty")
    args = parser.parse_args(argv)

    if args.forget:
        handed = read_handover(args.project)
        if args.forget in handed:
            del handed[args.forget]
            write_handover(args.project, handed)
            print(f"forgot {args.forget} - it will report again",
                  file=sys.stderr)
        else:
            print(f"no handover row {args.forget!r} - nothing to forget",
                  file=sys.stderr)
        return 0

    from library.tools.resolve_lock import ResolveBusy

    try:
        rows, resolve_name = run_poll(
            args.project, peek=args.peek,
            lease_timeout=args.timeout, verbose=args.verbose)
    except CannotLook as exc:
        print(f"feedback_poll: cannot look: {exc}", file=sys.stderr)
        return EXIT_CANNOT_LOOK
    except ResolveBusy as exc:
        print(f"feedback_poll: Resolve is busy: {exc}", file=sys.stderr)
        return EXIT_CANNOT_LOOK
    if not rows:
        return 0
    document = {
        "format": POLL_FORMAT,
        "polled_at": datetime.now(timezone.utc).isoformat(),
        "resolve_project": resolve_name,
        "count": len(rows),
        "markers": rows,
    }
    print(json.dumps(document, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
