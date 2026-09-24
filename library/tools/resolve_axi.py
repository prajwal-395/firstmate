"""resolve-axi: agent-ergonomic reads (and guarded writes) over the live Resolve session.

The captain's ask: unify the hand-written-script-through-MCP paths that
drive DaVinci Resolve every day into one command surface, in TOON rather
than escaped JSON, so LLM-driven Resolve control costs fewer tokens and
fewer turns.

The command surface comes from six measured incidents, not from the API
surface: `timeline list` (staging siblings visible), `timeline get`
(exact names win, unique prefixes resolve, ambiguous ones are
refused), `markers` (both marker planes in one call),
`markers snapshot`/`restore`/`reply` (content round-trip, dry-run
default), `items` (with `--transforms`), `captions`, `fusion` (comp
coverage per clip), `cursor` (the global cursor made visible
and assertable), `frames` (frame counts for drift checks),
`pool` (every clip in the media pool with its bin, plus the offline
count), `renders` (the queue with locale-independent states, failures
with their errors), `api` (the native MCP's knowledge tools - stubs,
search, docs, whats-new - wrapped as TOON), `luts` (the shared LUT
shelf listed locally; DCTL/LUT writes wrapped with compile check,
dry-run default), `launch` (idempotent app start, never opens a
project), `project` (identity plus the delivery-relevant settings),
`audio` (audio tracks with enable state and clip counts), `run`
(the cheap escape hatch: a caller script with ready Resolve names
in scope, its `result` rendered as TOON rows).

Safety shape, stated once:

- Every command here is READ-ONLY except `markers restore --apply`,
  `markers reply --apply`, and `run --unsafe`.
- Nothing here opens or creates a project or timeline, and nothing
  moves the current-timeline cursor: listing and reading go through
  `GetTimelineByIndex`, never `SetCurrentTimeline`. Opening something
  is a write to the captain's session.
- Reads hold the Resolve lease SHARED (`exclusive=False`); the three
  writes hold it EXCLUSIVE. `restore --apply` and `reply --apply`
  refuse unless the cursor already sits on the reel; `run --unsafe`
  reports the cursor before and after the script instead (an arbitrary
  script owns its own cursor, so there is nothing to assert it against).
- `run` without `--unsafe` refuses scripts that call Resolve writers
  (a prefix rule over mutator verbs, stated at `_RUN_WRITE_PREFIXES`).
  Reads must still never move the cursor, and the AST test still holds
  for everything that is not a declared write.
- `run --unsafe` still refuses `CopyGrades` unless
  `--acknowledge-copy-grades` names it out loud: the call replaces
  the target's whole grade, reports success, and versions nothing
  to go back to (`_COPYGRADES_TRAP`).
- `markers restore` defaults to a dry-run diff. `--apply` is the
  explicit flag, and it restores the TIMELINE plane only; a snapshot
  holding clip/pool-plane rows is REFUSED unless `--allow-partial`
  acknowledges the gap (the refusal names the count, the planes, and
  how to re-apply the clip half by hand - see `RESTORE_SCOPE`).

Output contract (axi.md principles 1-6, 9-10):

- stdout carries TOON tables (`name[N]{cols}:` plus backtick-quoted
  CSV rows - the same quote character `toon_serializer` uses, because
  prose here holds apostrophes and embedded JSON holds double quotes)
  followed by `help[]` next-step hints.
- Errors go to stdout as `error: ...` plus a `help: ...` fix line.
  Exit 0 is success (including no-ops), 1 is an error, 2 is a usage
  error such as an unknown flag - which is always rejected loudly,
  never ignored.
- Large text (marker notes) is truncated with a size hint and a
  `--full` escape hatch.
- `-v` / `-V` / `--version` answer from this leaf module before any
  heavy import runs.

Command-shape rule (the round-2 lesson, stated as a rule after round 5
broke it again): any command taking one obvious primary argument
accepts it positionally. A bare timeline name routes onto `--timeline`
for the read commands (`_BARE_TIMELINE_COMMANDS`, via `_normalize`);
a bare script IS the script for `run`. `--timeline`/`--script`/`--file`
keep working as the explicit forms. `test_positional_primary_args`
enumerates the commands and asserts the shape, so the next command
built without a positional fails here instead of on first real use.
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import os
import sys
from datetime import datetime, timezone

VERSION = "0.4.0"

DESCRIPTION = "Read the live DaVinci Resolve session in token-cheap TOON rows"

TOOL = "resolve-axi"

#: Marker-note text shown inline before the truncation hint fires.
NOTE_PREVIEW_CHARS = 500

#: What `markers restore --apply` writes. Timeline-plane markers round
#: trip exactly through `AddMarker(frame, colour, name, note, duration,
#: customData)`; clip/pool-plane rows carry no verified write path in
#: this repo (no caller writes them today), so they are compared in the
#: dry run and reported, never guessed at. A refused restore tells the
#: caller to re-apply that half by hand from the snapshot file (each
#: skipped row carries its plane, frame, name and words) - the refusal
#: names no machine-local path, because the file lives wherever the
#: caller wrote the snapshot.
RESTORE_SCOPE = "timeline-plane"

#: Snapshot fields that name each frame number for what it IS.
#: `frame_in_timeline_space` is the inverted legacy name: for a clip
#: note it holds the clip-local SOURCE frame, not a timeline position.
#: It stays in the payload (restore and older readers key on it) but
#: new callers must read `timeline_frame` / `source_frame` instead.
TIMELINE_FRAME_FIELD = "timeline_frame"
SOURCE_FRAME_FIELD = "source_frame"
DEPRECATED_FRAME_FIELD = "frame_in_timeline_space"
DEPRECATED_FRAME_NOTE = (
    f"{DEPRECATED_FRAME_FIELD} is misnamed: for clip notes it holds "
    f"the clip-local source frame - read {SOURCE_FRAME_FIELD} for that "
    f"and {TIMELINE_FRAME_FIELD} for the timeline position"
)

# NOTE: there is deliberately no allowlist of restorable planes beside
# `timeline_marker` itself. The restore filter below refuses anything
# whose source is not the timeline plane, so a future plane fails
# closed rather than slipping through an enumeration nobody updated.


# ── TOON output ──────────────────────────────────────────────────────
#
# Named-table headers (`timelines[32]{name,frames}:`) in the axi
# catalog shape; row quoting follows `toon_serializer`'s rule (a
# BACKTICK, doubled when it occurs in content).


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return " ".join(text.split())


def _format_row(vals: list) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quotechar="`", doublequote=True,
                        quoting=csv.QUOTE_MINIMAL, lineterminator="")
    writer.writerow([_cell(v) for v in vals])
    return buffer.getvalue()


def table(name: str, rows: list, cols: list) -> str:
    """One named TOON table. Empty collections say zero explicitly."""
    if not rows:
        return f"{name}: 0 rows"
    colspec = ",".join(cols)
    lines = [name + "[" + str(len(rows)) + "]{" + colspec + "}:"]
    for row in rows:
        if isinstance(row, dict):
            lines.append("  " + _format_row([row.get(c) for c in cols]))
        else:
            lines.append("  " + _format_row(list(row)))
    return "\n".join(lines)


def kv_block(name: str, pairs: dict) -> str:
    lines = [f"{name}:"]
    for key, value in pairs.items():
        lines.append(f"  {key}: {_cell(value)}")
    return "\n".join(lines)


def help_block(suggestions: list) -> str:
    lines = [f"help[{len(suggestions)}]:"]
    lines.extend(f"  Run `{s}`" for s in suggestions)
    return "\n".join(lines)


def emit(parts: list) -> None:
    sys.stdout.write("\n".join(p for p in parts if p) + "\n")


def preview(text: str, full: bool) -> str:
    text = text or ""
    if full or len(text) <= NOTE_PREVIEW_CHARS:
        return text
    return (f"{text[:NOTE_PREVIEW_CHARS]}... "
            f"(truncated, {len(text)} chars total - use --full)")


# ── Errors ───────────────────────────────────────────────────────────


class AxiError(RuntimeError):
    """A structured failure: what went wrong plus the fixing command."""

    def __init__(self, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.fix = fix


def fail(message: str, fix: str = "") -> int:
    parts = [f"error: {message}"]
    if fix:
        parts.append(f"help: {fix}")
    emit(parts)
    return 1


class Parser(argparse.ArgumentParser):
    """argparse that fails loud onto stdout with exit 2 (axi principle 6).

    The error TEACHES the working invocation (axi principle 9),
    because the predictable wrong guess is known: timelines are
    addressed with `--timeline` (or a bare name), and an agent
    guessing `--reel` should land on the right flag in one turn,
    not after a `--help` round trip.
    """

    def error(self, message):
        lines = [f"error: {message}"]
        if "--reel" in message or (
                self.prog.endswith("markers")
                and "invalid choice" in message):
            # The only positionals `markers` owns are snapshot/restore,
            # so anything else in that slot is a reel-name attempt
            # (`markers --reel "Reel 29"`, or a bare name that missed
            # the rewrite). Name the working shape, not the manual.
            lines.append(
                f"help: timelines take --timeline \"<name>\" (exact or "
                f"unique prefix), or a bare name: {TOOL} markers "
                f"\"Reel 29\"")
        else:
            lines.append(f"help: {self.prog} --help lists the valid flags")
        emit(lines)
        raise SystemExit(2)


# ── Resolve access (all lazy: nothing here imports at module load) ───


def _connect():
    from library.tools.marker_feedback import connect_resolve
    try:
        return connect_resolve()
    except Exception as exc:
        raise AxiError(
            f"cannot reach Resolve: {exc}",
            f"{TOOL} --help") from exc


def _lease(exclusive: bool):
    from library.tools.resolve_lock import resolve_lease
    return resolve_lease(f"resolve-axi ({TOOL})", exclusive=exclusive)


def _project(resolve, project_name: str):
    """The open project, optionally checked against an expected name."""
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject()
    if project is None:
        raise AxiError("Resolve has no project open - open one and re-run.",
                       f"{TOOL} cursor")
    if project_name and project.GetName() != project_name:
        raise AxiError(
            f"the open project is {project.GetName()!r}, not "
            f"{project_name!r} - exact names only, refusing rather than "
            f"reading the wrong project.",
            f"{TOOL} cursor")
    return project


def _timeline_names(project) -> list:
    names = []
    for index in range(1, (project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is None:
            continue
        names.append(timeline.GetName())
    return names


def _target_timeline(project, name):
    """The named timeline, or the open one when no name is given.

    Returns `(timeline, is_current, note)`. Exact names win; a prefix
    matching exactly ONE timeline resolves to it (with a `note` saying
    so, so the caller learns the full name); a prefix matching several
    is refused loudly - that refusal is what makes incident 2's
    `startswith("Reel 16")` trap impossible.

    `timeline_named`'s own refusal lists every timeline on the project
    (~2 KB on the error path, the token cost this tool removes), so a
    miss is rephrased compactly: the colliding candidates when that is
    what it is, near matches otherwise, plus the one command that
    lists names by choice.
    """
    from library.tools.marker_feedback import current_timeline
    from library.tools.timeline_ingest import (
        TimelineIngestError,
        timeline_named,
    )
    if not name:
        timeline, _ = current_timeline()
        if timeline.GetName() not in _timeline_names(project):
            raise AxiError(
                "the open timeline is not on the open project - "
                "re-open it and re-run.",
                f"{TOOL} cursor")
        return timeline, True, ""
    try:
        return timeline_named(project, name), False, ""
    except TimelineIngestError:
        listed = _timeline_names(project)
        hits = [other for other in listed if other.startswith(name)]
        if len(hits) == 1:
            return (timeline_named(project, hits[0]), False,
                    f"resolved: {name!r} is a unique prefix of "
                    f"{hits[0]!r}")
        if hits:
            # The case that bites is a (rebuild staging) sibling: the
            # caller asked for the reel and the unpromoted rebuild
            # answered the prefix. Name the cause, not just the
            # collision, so the next action is obvious.
            staged = [h for h in hits
                      if h.endswith(_staging_suffix())]
            cause = (f" One of them is an unpromoted (rebuild staging) "
                     f"sibling - promote or discard it, or pass the "
                     f"full name." if staged else "")
            detail = (f"no timeline named exactly {name!r}; it is a "
                      f"prefix of {len(hits)} timelines: {hits} - pass "
                      f"the full name.{cause}")
        else:
            near = [other for other in listed if name.lower() in
                    other.lower()][:5]
            detail = (f"no timeline named exactly {name!r}."
                      + (f" Resembling: {near}." if near else ""))
        raise AxiError(detail,
                       f"{TOOL} timeline list") from None


def _staging_suffix() -> str:
    from library.tools.resolve_bin_layout import STAGING_TIMELINE_SUFFIX
    return STAGING_TIMELINE_SUFFIX


def _frames_of(timeline) -> tuple:
    try:
        start = int(timeline.GetStartFrame())
        end = int(timeline.GetEndFrame())
    except (TypeError, ValueError) as exc:
        raise AxiError(
            f"timeline {timeline.GetName()!r} would not report its "
            f"start/end frames ({exc}) - refusing rather than "
            f"reporting half a row.",
            f"{TOOL} timeline list") from exc
    return start, end, end - start + 1


def _timeline_marker_summary(timeline) -> tuple:
    """(count, sorted colours) off the timeline plane only - the cheap half."""
    try:
        markers = timeline.GetMarkers() or {}
    except Exception as exc:
        raise AxiError(
            f"timeline {timeline.GetName()!r} would not report its "
            f"markers ({exc}).",
            f"{TOOL} timeline list") from exc
    colours = sorted({(m.get("color") or "") for m in markers.values()
                      if isinstance(m, dict)} - {""})
    return len(markers), colours


# ── Commands ─────────────────────────────────────────────────────────


def cmd_home(_args) -> int:
    try:
        resolve = _connect()
    except AxiError as exc:
        emit([f"bin: {_bin_path()}",
              f"description: {DESCRIPTION}",
              f"error: {exc}",
              help_block([f"{TOOL} --help"])])
        return 1
    with _lease(exclusive=False):
        project = _project(resolve, "")
        from library.tools.marker_feedback import current_timeline
        try:
            open_timeline, _ = current_timeline(resolve)
            cursor = open_timeline.GetName()
        except Exception:
            cursor = "(none open)"
        names = _timeline_names(project)
        suffix = _staging_suffix()
        staged = [n for n in names if n.endswith(suffix)]
    emit([f"bin: {_bin_path()}",
          f"description: {DESCRIPTION}",
          kv_block("resolve", {
              "project": project.GetName(),
              "cursor": cursor,
              "timelines": len(names),
              "staging": len(staged),
          }),
          table("staging", [{"timeline": n} for n in staged],
                ["timeline"]) if staged else "staging: 0 unpromoted siblings",
          help_block([f"{TOOL} timeline list",
                      f"{TOOL} timeline get \"<exact-name>\"",
                      f"{TOOL} markers --timeline \"<exact-name>\""])])
    return 0


def _bin_path() -> str:
    path = os.path.abspath(sys.argv[0] or TOOL)
    home = os.path.expanduser("~")
    return f"~{path[len(home):]}" if path.startswith(home) else path


def cmd_timeline_list(args) -> int:
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        names = _timeline_names(project)
        suffix = _staging_suffix()
        timelines = [project.GetTimelineByIndex(index + 1)
                     for index in range(len(names))]
        rows = []
        for name, timeline in zip(names, timelines):
            start, end, frames = _frames_of(timeline)
            count, colours = _timeline_marker_summary(timeline)
            base = (name[:-len(suffix)]
                    if name.endswith(suffix) else None)
            rows.append({
                "name": name,
                "frames": frames,
                "markers": count,
                "colors": ",".join(colours),
                "staging": "yes" if base is not None else "no",
                "promotion_pending": ("yes" if base in names else "no")
                if base is not None else "no",
            })
    emit([kv_block("project", {"name": project.GetName(),
                               "timelines": len(rows)}),
          table("timelines", rows,
                ["name", "frames", "markers", "colors",
                 "staging", "promotion_pending"]),
          help_block([f"{TOOL} timeline get \"<exact-name>\"",
                      f"{TOOL} markers --timeline \"<exact-name>\""])])
    return 0


def cmd_timeline_get(args) -> int:
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            timeline, _, note = _target_timeline(project, args.name)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        from library.tools import marker_feedback
        start, end, frames = _frames_of(timeline)
        try:
            fps = timeline.GetSetting("timelineFrameRate")
        except Exception:
            fps = "unknown"
        notes = marker_feedback.read_notes(timeline)
        planes: dict = {}
        colours: dict = {}
        for entry in notes:
            planes[entry.source] = planes.get(entry.source, 0) + 1
            if entry.color:
                colours[entry.color] = colours.get(entry.color, 0) + 1
        try:
            tracks = []
            for track_type in ("video", "audio"):
                count = timeline.GetTrackCount(track_type) or 0
                for index in range(1, count + 1):
                    items = (timeline.GetItemListInTrack(track_type,
                                                         index) or [])
                    tracks.append({"track": f"{track_type}{index}",
                                   "name": timeline.GetTrackName(
                                       track_type, index) or "",
                                   "clips": len(items)})
        except Exception as exc:
            return fail(f"timeline {args.name!r} would not report its "
                        f"tracks ({exc}).",
                        f"{TOOL} timeline list")
    emit([kv_block("timeline", {
              "name": timeline.GetName(),
              "start": start, "end": end, "frames": frames, "fps": fps,
          }),
          note,
          table("tracks", tracks, ["track", "name", "clips"]),
          table("marker_planes",
                [{"plane": k, "notes": v} for k, v in sorted(planes.items())],
                ["plane", "notes"]),
          table("marker_colors",
                [{"color": k, "notes": v} for k, v in sorted(colours.items())],
                ["color", "notes"]) if colours else "marker_colors: 0 colors",
          help_block([f"{TOOL} markers --timeline \"{timeline.GetName()}\"",
                      f"{TOOL} frames --timeline \"{timeline.GetName()}\""])])
    return 0


def _note_rows(notes, full: bool, plane: str) -> list:
    rows = []
    for note in notes:
        if plane and note.source != plane:
            continue
        rows.append({
            "frame": note.frame if note.frame is not None else "",
            "timecode": note.timecode or "",
            "color": note.color or "",
            "plane": note.source,
            "name": note.name or "",
            "note": preview(note.note or note.text or "", full),
        })
    return rows


def cmd_markers(args) -> int:
    if args.plane and args.plane not in ("timeline_marker", "clip_marker",
                                         "media_pool_marker",
                                         "clip_comment"):
        return fail(f"unknown plane {args.plane!r}.",
                    f"{TOOL} markers --timeline \"{args.timeline or '<name>'}\""
                    f" [--plane timeline_marker|clip_marker|"
                    f"media_pool_marker|clip_comment]")
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        from library.tools import marker_feedback
        notes = marker_feedback.read_notes(timeline)
    rows = _note_rows(notes, args.full, args.plane or "")
    truncated = sum(1 for r in rows if "(truncated," in r["note"])
    emit([kv_block("markers", {
              "timeline": timeline.GetName() +
              (" (current)" if is_current else ""),
              "notes": len(rows),
              "truncated": truncated,
          }),
          note,
          table("notes", rows,
                ["frame", "timecode", "color", "plane", "name", "note"]),
          help_block([f"{TOOL} markers --timeline \"{timeline.GetName()}\""
                      f" --full"]) if truncated and not args.full else ""])
    return 0


def _snapshot_payload(project_name: str, timeline_name: str,
                       notes) -> dict:
    payload_notes = []
    for note in notes:
        raw_key = note.frame_in_timeline_space
        payload_notes.append({
            "source": note.source,
            "frame": note.frame,
            "frame_in_timeline_space": raw_key,
            "timeline_frame": note.frame,
            "source_frame": (raw_key if note.source in
                             ("clip_marker", "media_pool_marker")
                             else None),
            "timecode": note.timecode,
            "color": note.color,
            "name": note.name,
            "note": note.note,
            "duration_frames": note.duration_frames,
            "custom_data_raw": note.custom_data_raw,
        })
    return {
        "tool": TOOL,
        "tool_version": VERSION,
        "scope": RESTORE_SCOPE,
        "project": project_name,
        "timeline": timeline_name,
        "read_at": datetime.now(timezone.utc).isoformat(),
        "deprecated_fields": {
            DEPRECATED_FRAME_FIELD: DEPRECATED_FRAME_NOTE,
        },
        "notes": payload_notes,
    }


def cmd_markers_snapshot(args) -> int:
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            timeline, _, note = _target_timeline(project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        from library.tools import marker_feedback
        notes = marker_feedback.read_notes(timeline)
        payload = _snapshot_payload(project.GetName(), timeline.GetName(),
                                    notes)
    try:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True,
                      default=str)
            handle.write("\n")
    except OSError as exc:
        return fail(f"cannot write {args.out!r} ({exc}).",
                    f"{TOOL} markers snapshot --timeline "
                    f"\"{timeline.GetName()}\" --out <path>")
    scoped = [n for n in payload["notes"] if n["source"] == "timeline_marker"]
    emit([kv_block("snapshot", {
              "timeline": timeline.GetName(),
              "notes": len(payload["notes"]),
              "restorable": len(scoped),
              "out": args.out,
          }),
          note,
          f"deprecated: {DEPRECATED_FRAME_NOTE}",
          help_block([f"{TOOL} markers restore --in {args.out} "
                      f"--timeline \"{timeline.GetName()}\""])])
    return 0


def _skipped_planes(notes: list) -> dict:
    """Count of snapshot notes restore cannot write, by plane.

    Anything outside the timeline plane is unrestorable (see
    RESTORE_SCOPE): the check is `!= "timeline_marker"`, never a
    membership list, so a future plane fails closed.
    """
    counts: dict = {}
    for row in notes or []:
        if (row.get("source") or "") != "timeline_marker":
            plane = row.get("source") or "(unknown plane)"
            counts[plane] = counts.get(plane, 0) + 1
    return counts


def _refuse_partial_snapshot(in_file: str, timeline_name, skipped: dict,
                             apply: bool) -> int:
    """Refuse a snapshot holding notes restore cannot write.

    The refusal IS the safety: "snapshot" reads as a backup, and a
    caller using it as a pre-rebuild safety net must learn before the
    rebuild - not after - that the clip half is not in this file. It
    names how many notes and in which plane, prints where the clip
    half actually lives, and teaches the acknowledgement flag, the
    way the ambiguous-prefix refusal teaches the full name.
    """
    total = sum(skipped.values())
    planes = ", ".join(f"{plane}: {count}"
                       for plane, count in sorted(skipped.items()))
    verb = "would leave" if not apply else "leaves"
    emit([f"error: snapshot {in_file!r} holds {total} note(s) restore "
          f"cannot write ({planes}) - restore covers the timeline "
          f"plane ({RESTORE_SCOPE}) only, so restoring it "
          f"{verb} the clip half behind",
          f"recovery: re-apply the clip-plane note(s) by hand from the "
          f"snapshot file - each skipped row carries its plane, frame, "
          f"name and words",
          f"help: {TOOL} markers restore --in {in_file} --timeline "
          f"\"{timeline_name}\""
          f"{' --apply' if apply else ''} --allow-partial to proceed "
          f"with the timeline plane only"])
    return 1


def _snapshot_key(row: dict):
    return (row.get("frame_in_timeline_space"), row.get("color") or "",
            row.get("name") or "", row.get("note") or "",
            row.get("duration_frames") or 0,
            row.get("custom_data_raw") or "")


def cmd_markers_restore(args) -> int:
    try:
        with open(args.in_file, encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, ValueError) as exc:
        return fail(f"cannot read snapshot {args.in_file!r} ({exc}).",
                    f"{TOOL} markers snapshot --timeline \"<name>\" "
                    f"--out {args.in_file}")
    if not isinstance(snapshot, dict) or snapshot.get("tool") != TOOL:
        return fail(f"{args.in_file!r} is not a {TOOL} marker snapshot.",
                    f"{TOOL} markers snapshot --timeline \"<name>\" "
                    f"--out {args.in_file}")
    wanted_name = args.timeline or snapshot.get("timeline")
    skipped = _skipped_planes(snapshot.get("notes", []))
    if skipped and not args.allow_partial:
        # Before any Resolve contact: no lease, no cursor assertion,
        # no write. The file's content alone decides this.
        return _refuse_partial_snapshot(args.in_file, wanted_name,
                                        skipped, args.apply)
    wanted = [n for n in snapshot.get("notes", [])
              if n.get("source") == "timeline_marker"]
    skipped_rows = [n for n in snapshot.get("notes", [])
                    if (n.get("source") or "") != "timeline_marker"]
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, _, note = _target_timeline(project, wanted_name)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        if timeline.GetName() != snapshot.get("timeline"):
            return fail(
                f"snapshot is for {snapshot.get('timeline')!r} but the "
                f"target is {timeline.GetName()!r} - refusing to restore "
                f"one reel's words onto another.",
                f"{TOOL} markers restore --in {args.in_file} "
                f"--timeline \"{snapshot.get('timeline')}\"")
        from library.tools.marker_feedback import current_timeline
        open_timeline, _ = current_timeline(resolve)
        if open_timeline.GetName() != timeline.GetName():
            return fail(
                f"the cursor sits on {open_timeline.GetName()!r}, not "
                f"{timeline.GetName()!r} - a restore asserts the cursor "
                f"rather than depending on it. Open the reel and re-run.",
                f"{TOOL} cursor --expect \"{timeline.GetName()}\"")
        live = timeline.GetMarkers() or {}
        live_keys = {(key, (m.get("color") or ""),
                      (m.get("name") or ""), (m.get("note") or ""),
                      int(m.get("duration") or 1),
                      (m.get("customData") or ""))
                     for key, m in live.items() if isinstance(m, dict)}
        missing = [row for row in wanted
                   if _snapshot_key(row) not in live_keys]
        if not args.apply:
            parts = [kv_block("restore_plan", {
                      "timeline": timeline.GetName(),
                      "snapshot_notes": len(wanted),
                      "already_present": len(wanted) - len(missing),
                      "to_restore": len(missing),
                  }),
                  table("missing", [{
                      "frame": r.get("frame_in_timeline_space"),
                      "color": r.get("color") or "",
                      "name": r.get("name") or "",
                      "note": preview(r.get("note") or "", False),
                  } for r in missing],
                  ["frame", "color", "name", "note"])]
            if skipped_rows:
                parts.append(table("skipped", [{
                    "plane": r.get("source") or "",
                    "frame": r.get(TIMELINE_FRAME_FIELD),
                    "name": r.get("name") or "",
                    "note": preview(r.get("note") or "", False),
                } for r in skipped_rows],
                ["plane", "frame", "name", "note"]))
                parts.append(f"recovery: re-apply the skipped note(s) "
                             f"by hand from the snapshot file - each row "
                             f"above carries its plane, frame, name and "
                             f"words")
            parts.extend([
                  f"dry run - pass --apply to write under the Resolve lease",
                  help_block([f"{TOOL} markers restore --in {args.in_file} "
                              f"--timeline \"{timeline.GetName()}\" "
                              f"--apply"
                              f"{' --allow-partial' if skipped_rows else ''}"])])
            emit(parts)
            return 0
        restored, refused = 0, []
        for row in missing:
            try:
                placed = timeline.AddMarker(
                    int(row["frame_in_timeline_space"]),
                    row.get("color") or "", row.get("name") or "",
                    row.get("note") or "",
                    int(row.get("duration_frames") or 1),
                    row.get("custom_data_raw") or "")
            except Exception as exc:
                refused.append((row, str(exc)))
                continue
            if placed:
                restored += 1
            else:
                refused.append((row, "occupied frame - one marker per "
                                     "frame"))
    summary = {
        "timeline": timeline.GetName(),
        "restored": restored,
        "refused": len(refused),
    }
    if skipped_rows:
        summary["skipped"] = len(skipped_rows)
    out = [kv_block("restore", summary), note]
    if refused:
        out.append(table("refused", [{
            "frame": r.get("frame_in_timeline_space"),
            "name": r.get("name") or "",
            "reason": reason,
        } for r, reason in refused], ["frame", "name", "reason"]))
    if skipped_rows:
        out.append(f"skipped: {len(skipped_rows)} clip-plane note(s) left "
                   f"behind by --allow-partial - re-apply them by hand "
                   f"from the snapshot file")
    out.append(help_block(
        [f"{TOOL} markers --timeline \"{timeline.GetName()}\""]))
    emit(out)
    return 0 if not refused else 1


def cmd_markers_reply(args) -> int:
    """Record our answer on a reel as a marker, linked to what it answers.

    The write half of the reply workflow `marker_feedback` owns:
    `place_reply_marker` (AddMarker + read-back verification) carries
    a `reply_custom_data` payload built with `reply_custom_data`, so
    the marker states mechanically that it is OURS and what it
    answers. Nothing here invents that path - this command exposes
    it the way `markers restore` exposes the snapshot path.

    `--frame` is ABSOLUTE (the space `markers` reports). `--color` is
    required: colour carries meaning and is never defaulted (AGENTS.md
    10.5). `--answers-frame` names the note being answered; its text
    is looked up live for the payload and shown in the dry run, so a
    reply cannot silently answer the wrong words. Default is a
    dry-run diff; `--apply` writes under the exclusive lease with the
    cursor asserted on the reel first.
    """
    from library.tools.marker_feedback import (
        MarkerWriteError,
        place_reply_marker,
        reply_custom_data,
    )
    if not args.name or not args.note or not args.color:
        return fail("a reply needs --name, --note and --color.",
                    f"{TOOL} markers reply --timeline \"<name>\" --frame "
                    f"<n> --color <c> --name \"<n>\" --note \"<text>\"")
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, _, note = _target_timeline(project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        from library.tools import marker_feedback
        notes = marker_feedback.read_notes(timeline)
        answered = [n for n in notes
                    if args.answers_frame is not None
                    and n.frame == args.answers_frame]
        answered.sort(key=lambda n: (n.source != "timeline_marker",))
        answers_text = answered[0].note if answered else ""
        # The pairing the reply carries: the answered note's durable
        # identity plus the picture it sat on (`marker_carry` re-pairs
        # by this after a rebuild, never by frame). `--answers` is an
        # explicit identity, validated, never prose: it must BE the
        # note's own identity when both are given.
        from library.tools.feedback_ledger import (
            durable_identity as _durable_identity,
            is_identity as _is_identity,
        )
        derived_answers, derived_anchor = "", None
        if answered:
            derived_answers = _durable_identity(
                timeline.GetName(), answered[0].text)
            # The SAME anchor `marker_carry` will read for this note:
            # the picture under its frame, not the frame number. A
            # mirror computed off the note's clip list would disagree
            # wherever an overlay row sits on top (carry anchors to
            # picture rows only), so this calls carry's own function.
            from library.tools import marker_carry as _carry
            try:
                start_frame = int(timeline.GetStartFrame())
            except (TypeError, ValueError):
                start_frame = None
            if answered[0].frame is not None and start_frame is not None:
                pictured = _carry.picture_at(
                    timeline, answered[0].frame - start_frame)
                if pictured is not None:
                    derived_anchor = {"source_file": pictured[0],
                                      "source_frame": pictured[1]}
        if args.answers and not _is_identity(args.answers):
            return fail(
                f"--answers {args.answers!r} is not a note identity - "
                f"prose never joins to a note and a frame is invalidated "
                f"by the next rebuild.",
                f"omit --answers and keep --answers-frame "
                f"{args.answers_frame} to take the note's own identity "
                f"({derived_answers or 'none - no note at that frame'}), "
                f"or pass that identity explicitly")
        if (args.answers and derived_answers
                and args.answers != derived_answers):
            return fail(
                f"--answers {args.answers!r} is not the note at frame "
                f"{args.answers_frame} ({derived_answers}) - a reply "
                f"cannot silently answer the wrong words.",
                f"omit --answers to take the note's own identity, or "
                f"check the frame with {TOOL} markers --timeline "
                f"\"{timeline.GetName()}\"")
        effective_answers = args.answers or derived_answers
        effective_anchor = derived_anchor if answered else None
        try:
            start = int(timeline.GetStartFrame())
            span = int(timeline.GetEndFrame()) - start
        except (TypeError, ValueError) as exc:
            return fail(f"timeline {timeline.GetName()!r} would not "
                        f"report its span ({exc}).",
                        f"{TOOL} timeline list")
        key = int(args.frame) - start
        live = timeline.GetMarkers() or {}
        problem = ("off the timeline "
                   f"(0..{span - 1} in timeline space)" if not
                   0 <= key < span else
                   ("occupied - one marker per frame" if key in live
                    else ""))
        if not args.apply:
            emit([kv_block("reply_plan", {
                      "timeline": timeline.GetName(),
                      "frame": args.frame,
                      "color": args.color,
                      "name": args.name,
                      "answers_frame": (args.answers_frame
                                        if args.answers_frame is not None
                                        else ""),
                      "answers": effective_answers or "(none - words only)",
                      "answers_anchor": (
                          f"{effective_anchor['source_file']}@"
                          f"{effective_anchor['source_frame']}"
                          if effective_anchor else "(no picture under "
                          "the note - pairs by words alone)"),
                      "answers_text": preview(answers_text, False),
                      "blocked": problem or "no",
                  }),
                  note,
                  f"dry run - pass --apply to write under the Resolve "
                  f"lease",
                  help_block([f"{TOOL} markers reply --timeline "
                              f"\"{timeline.GetName()}\" --frame "
                              f"{args.frame} --color \"{args.color}\" "
                              f"--name \"{args.name}\" --note "
                              f"\"{preview(args.note, False)}\" "
                              f"--apply"])])
            return 0
        if problem:
            return fail(f"reply at frame {args.frame} refused: {problem}.",
                        f"{TOOL} markers --timeline "
                        f"\"{timeline.GetName()}\"")
        from library.tools.marker_feedback import current_timeline
        open_timeline, _ = current_timeline(resolve)
        if open_timeline.GetName() != timeline.GetName():
            return fail(
                f"the cursor sits on {open_timeline.GetName()!r}, not "
                f"{timeline.GetName()!r} - a reply asserts the cursor "
                f"rather than depending on it. Open the reel and re-run.",
                f"{TOOL} cursor --expect \"{timeline.GetName()}\"")
        payload = reply_custom_data(
            "", effective_answers, answers_text, args.summary or "",
            effective_anchor)
        try:
            place_reply_marker(timeline, int(args.frame), args.color,
                               args.name, args.note,
                               int(args.duration or 1), payload)
        except MarkerWriteError as exc:
            return fail(str(exc), f"{TOOL} markers --timeline "
                                  f"\"{timeline.GetName()}\"")
        except ValueError as exc:
            return fail(str(exc), f"{TOOL} markers --timeline "
                                  f"\"{timeline.GetName()}\"")
    emit([kv_block("reply", {
              "timeline": timeline.GetName(),
              "frame": args.frame,
              "placed": "yes (read back)",
          }),
          note,
          help_block(
              [f"{TOOL} markers --timeline \"{timeline.GetName()}\""])])
    return 0


def cmd_markers_audit_replies(args) -> int:
    """Report which replies still sit beside the notes they answer.

    Read-only: the sweep the replies-decay task asks for before
    anything is changed. Each green reply is bound to its note by
    identity (`marker_carry.audit_replies`); `paired-drifted` is the
    Reel 14 shape - the note moved on under a rebuild and the reply
    stayed. This command reports and writes nothing: re-pairing
    happens in the next promotion carry, or by a directed write that
    firstmate routes, never here.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        from library.tools import marker_carry as _carry
        from library.tools import marker_feedback
        try:
            notes = _carry.read_markers(timeline, timeline.GetName())
        except _carry.MarkerCarryUnreadable as exc:
            return fail(str(exc), f"{TOOL} markers --timeline "
                                  f"\"{args.timeline or '<name>'}\"")
        rows = _carry.audit_replies(notes, timeline.GetName())
        live = marker_feedback.read_notes(timeline)
        clip_replies = [
            n for n in live if n.source != "timeline_marker"
            and marker_feedback.reply_records_in(n.custom_data_raw)]
    table_rows = [{
        "frame": r["frame"],
        "color": r["color"] or "",
        "name": preview(r["name"] or "", args.full),
        "status": r["status"],
        "ask_frame": (r["ask_frame"] if r["ask_frame"] is not None
                      else ""),
        "distance": (r["distance"] if r["distance"] is not None else ""),
        "answers": preview(r["answers"] or "", args.full),
    } for r in rows]
    drifted = sum(1 for r in rows if r["status"] == "paired-drifted")
    emit([kv_block("reply_audit", {
              "timeline": timeline.GetName() +
              (" (current)" if is_current else ""),
              "replies": len(rows),
              "drifted": drifted,
              "clip_plane_replies": len(clip_replies),
          }),
          note,
          table("replies", table_rows,
                ["frame", "color", "name", "status", "ask_frame",
                 "distance", "answers"]),
          (f"note: {len(clip_replies)} replie(s) of ours sit on the "
           f"clip plane, which marker carry does not read - this table "
           f"covers the timeline plane only.")
          if clip_replies else "",
          help_block([f"{TOOL} markers --timeline "
                      f"\"{timeline.GetName()}\" --full"])])
    return 0


def cmd_items(args) -> int:
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        from library.tools.reel_read import read_tracks
        tracks = read_tracks(timeline)
    rows = []
    for track in tracks:
        for clip in track.get("clips", []):
            row = {
                "track": f"{clip['track_type']}{clip['track_index']}",
                "name": clip["name"],
                "record_in": clip["record_in"],
                "record_out": clip["record_out"],
                "duration": clip["duration"],
                "source_in": clip["source_in_frame"],
                "source_out": clip["source_out_frame"],
                "source_file": clip["source_file"],
            }
            if args.transforms:
                # The stored transform, read back verbatim - the same
                # `GetProperty()` (no argument) reading
                # `timeline_ingest` trusts (AGENTS.md 5). No units are
                # converted here: Pan/Tilt is one model and a unit is
                # not a pixel (`resolve_transform`), so the numbers
                # travel as Resolve stores them.
                #
                # CURRENCY WARNING (not enforced here): through a
                # non-current handle Pan/Tilt come back scaled by the
                # current timeline's dimensions
                # (`reel_read.assert_timeline_current`). The
                # "(current)" marker above only covers the opened-via-
                # current path - a named timeline that happens to be
                # current reads true but unmarked, and one that is not
                # reads scaled with no warning. Treat these numbers as
                # the timeline's own only when it is current.
                stored = clip.get("transform") or {}
                for key in ("Pan", "Tilt", "ZoomX", "ZoomY", "Opacity"):
                    row[key.lower()] = stored.get(key, "")
            rows.append(row)
    placed = sum((r["duration"] or 0) for r in rows)
    cols = ["track", "name", "record_in", "record_out", "duration",
            "source_in", "source_out", "source_file"]
    if args.transforms:
        cols += ["pan", "tilt", "zoomx", "zoomy", "opacity"]
    emit([kv_block("items", {
              "timeline": timeline.GetName() +
              (" (current)" if is_current else ""),
              "clips": len(rows),
              "placed_frames": placed,
          }),
          table("clips", rows, cols),
          note,
          "source_out is inclusive (GetSourceEndFrame); record_out is "
          "exclusive - the mix-up that cost a frame is visible here",
          help_block([f"{TOOL} frames --timeline \"{timeline.GetName()}\""])])
    return 0


def cmd_fusion(args) -> int:
    """Every clip carrying Fusion comps, and whether each comp COVERS it.

    Surfaces `reel_read`'s per-item fusion detail: comp count and
    names plus each comp's media window verdict from
    `comp_media_window`. A comp whose MediaIn misses a frame its item
    PLAYS fails the whole render at that frame (six of the captain's
    eight reels, 2026-09-12) - `uncovered` names the reason per comp,
    or `no` when every comp covers every played frame. Read-only.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        from library.tools.reel_read import read_tracks
        tracks = read_tracks(timeline)
    rows = []
    for track in tracks:
        for clip in track.get("clips", []):
            fusion = clip.get("fusion") or {}
            if not fusion.get("comp_count"):
                continue
            windows = fusion.get("media_windows") or []
            uncovered = "; ".join(
                w.get("uncovered_reason") or ""
                for w in windows if w.get("uncovered_reason")) or "no"
            rows.append({
                "clip": clip["name"],
                "track": f"{clip['track_type']}{clip['track_index']}",
                "record_in": clip["record_in"],
                "record_out": clip["record_out"],
                "comps": fusion["comp_count"],
                "comp_names": ",".join(fusion.get("comp_names") or []),
                "uncovered": uncovered,
            })
    emit([kv_block("fusion", {
              "timeline": timeline.GetName() +
              (" (current)" if is_current else ""),
              "clips_with_comps": len(rows),
          }),
          note,
          table("comps", rows,
                ["clip", "track", "record_in", "record_out", "comps",
                 "comp_names", "uncovered"]),
          help_block([f"{TOOL} items --timeline \"{timeline.GetName()}\""])])
    return 0


def cmd_captions(args) -> int:
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        from library.tools.caption_swap import subtitle_track_index
        track = subtitle_track_index(timeline)
        rows = []
        if track is not None:
            for item in timeline.GetItemListInTrack("video", track) or []:
                try:
                    pool_item = item.GetMediaPoolItem()
                except Exception:
                    pool_item = None
                path = ""
                if pool_item is not None:
                    try:
                        path = (pool_item.GetClipProperty("File Path")
                                or "")
                    except Exception:
                        path = ""
                try:
                    start, end = item.GetStart(), item.GetEnd()
                except Exception:
                    continue
                try:
                    name = item.GetName()
                except Exception:
                    name = ""
                rows.append({"start": start, "end": end, "name": name,
                             "file": path})
    emit([kv_block("captions", {
              "timeline": timeline.GetName() +
              (" (current)" if is_current else ""),
              "cards": len(rows),
          }),
          note,
          table("cards", rows, ["start", "end", "name", "file"])])
    return 0


def _pool_clip_rows(folder, bin_path: str) -> tuple:
    """One row per media-pool item under `folder`, recursively.

    Returns `(rows, bins, skipped)`. A row that would not report its name
    or properties is SKIPPED and counted, never half-reported: the
    caller says the count, so "12 clips, 1 unreadable" cannot read as
    "11 clips".
    """
    rows, skipped, bins = [], 0, 0
    stack = [(folder, bin_path)]
    while stack:
        current, path = stack.pop()
        bins += 1
        try:
            clips = current.GetClipList() or []
        except Exception:
            clips = []
        for clip in clips:
            try:
                name = clip.GetName()
            except Exception:
                skipped += 1
                continue
            try:
                props = clip.GetClipProperty() or {}
            except Exception:
                props = {}
            if not isinstance(props, dict):
                props = {}
            rows.append({
                "name": name,
                "bin": path,
                "kind": props.get("Type") or "",
                "file": props.get("File Path") or "",
            })
        try:
            subs = current.GetSubFolderList() or []
        except Exception:
            subs = []
        for sub in subs:
            try:
                sub_name = sub.GetName()
            except Exception:
                skipped += 1
                continue
            stack.append((sub, f"{path}/{sub_name}" if path else sub_name))
    rows.sort(key=lambda r: (r["bin"], r["name"]))
    return rows, bins, skipped


def _resolve_bin(root, bin_path: str):
    """The pool folder at `bin_path` (`A/B/C` from the root), or None."""
    if not bin_path:
        return root, ""
    current, walked = root, []
    for part in [p for p in bin_path.split("/") if p]:
        try:
            subs = current.GetSubFolderList() or []
        except Exception:
            return None, "/".join(walked)
        names = {}
        for sub in subs:
            try:
                names[sub.GetName()] = sub
            except Exception:
                continue
        if part not in names:
            return None, "/".join(walked)
        current, walked = names[part], walked + [part]
    return current, "/".join(walked)


def cmd_pool(args) -> int:
    """Every clip in the media pool, with the bin it sits in.

    The ingest/catalog check `run` covers only at arbitrary token
    cost: which footage is in the pool, where it lives, and whether
    its file is still on disk (the offline aggregate). Read-only:
    pool/folder/clip getters only, shared lease, nothing here moves
    the cursor or opens anything.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            pool = project.GetMediaPool()
            root = pool.GetRootFolder()
        except Exception as exc:
            return fail(f"project {project.GetName()!r} would not open "
                        f"its media pool ({exc}).",
                        f"{TOOL} cursor")
        folder, walked = _resolve_bin(root, args.bin or "")
        if folder is None:
            return fail(
                f"no bin {args.bin!r} under "
                f"{('/' + walked) if walked else 'the pool root'} - "
                f"bins are addressed A/B/C from the root.",
                f"{TOOL} pool")
        rows, bins, skipped = _pool_clip_rows(folder, walked)
    import os as _os
    offline = 0
    for row in rows:
        if not row["file"]:
            continue
        try:
            if not _os.path.exists(row["file"]):
                offline += 1
        except Exception:
            offline += 1
    emit([kv_block("pool", {
              "project": project.GetName(),
              "bin": ("/" + walked) if walked else "/ (root)",
              "bins": bins,
              "clips": len(rows),
              "offline": offline,
              "unreadable": skipped,
          }),
          table("clips", rows, ["name", "bin", "kind", "file"]),
          (f"note: {skipped} item(s) would not report their "
           f"properties and are counted, not listed.")
          if skipped else "",
          help_block([f"{TOOL} items --timeline \"<exact-name>\"",
                      f"{TOOL} pool \"<bin/A/B>\""])])
    return 0


def _render_state(status: dict) -> str:
    """The job's state without reading English.

    `JobStatus` is a LOCALIZED display string ("Complete" on an
    English install, "Concluso" on an Italian one) - comparing it to
    the English literal fails every non-English Resolve with an error
    saying the opposite of what happened. The locale-independent
    signals decide: a populated `Error` is failed, 100% is complete.
    The raw string travels untouched in its own column, never parsed.
    """
    status = status or {}
    if status.get("Error"):
        return "failed"
    try:
        if float(status.get("CompletionPercentage")) >= 100:
            return "complete"
    except (TypeError, ValueError):
        pass
    if str(status.get("JobStatus") or "") == "Complete":
        return "complete"
    return "queued"


def cmd_renders(args) -> int:
    """The render queue, as rows: which timeline, how far, what failed.

    The delivery loop's missing read: `render_qa` measures the FILE,
    nothing shows the QUEUE cheaply. Read-only (`GetRenderJobList` /
    `GetRenderJobStatus` / `IsRenderingInProgress`), shared lease.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            jobs = project.GetRenderJobList() or []
        except Exception as exc:
            return fail(f"project {project.GetName()!r} would not report "
                        f"its render queue ({exc}).",
                        f"{TOOL} cursor")
        rows = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = job.get("JobId") or ""
            try:
                status = project.GetRenderJobStatus(job_id) or {}
            except Exception:
                status = {}
            if not isinstance(status, dict):
                status = {}
            try:
                percent = status.get("CompletionPercentage")
                percent = ("" if percent is None else int(percent))
            except (TypeError, ValueError):
                percent = ""
            rows.append({
                "job": job.get("RenderJobName") or job_id,
                "timeline": job.get("TimelineName") or "",
                "percent": percent,
                "state": _render_state(status),
                "status": status.get("JobStatus") or "",
                "error": status.get("Error") or "",
                "output": "/".join([
                    str(job.get("TargetDir") or "").rstrip("/"),
                    str(job.get("OutputFilename") or "")]).strip("/"),
            })
        try:
            rendering = bool(project.IsRenderingInProgress())
        except Exception:
            rendering = False
    states: dict = {}
    for row in rows:
        states[row["state"]] = states.get(row["state"], 0) + 1
    queue_cols = ["job", "timeline", "percent", "state"]
    if args.full:
        queue_cols += ["status", "output"]
    parts = [kv_block("renders", {
              "project": project.GetName(),
              "jobs": len(rows),
              "rendering": "yes" if rendering else "no",
              "complete": states.get("complete", 0),
              "failed": states.get("failed", 0),
          }),
          table("queue", [{c: r[c] for c in queue_cols} for r in rows],
                queue_cols)]
    failed = [r for r in rows if r["state"] == "failed"]
    if failed:
        parts.append(table("failed", [{
            "job": r["job"],
            "error": preview(r["error"], args.full),
            "output": r["output"],
        } for r in failed], ["job", "error", "output"]))
    hints = [f"{TOOL} frames --timeline \"<exact-name>\""]
    if failed and not args.full:
        hints = [f"{TOOL} renders --full"] + hints
    parts.append(help_block(hints))
    emit(parts)
    return 0


# ── api: Blackmagic's knowledge tools, wrapped as TOON ─────────
#
# `search_scripting_api`, `get_scripting_api`, `get_scripting_docs`
# and `get_whats_new` are documentation, not session state: they never
# touch the captain's project, so they wrap the native MCP server
# (`library/tools/native_mcp.py`) instead of being reimplemented. What
# this layer adds is the axi shape - minimal tables, truncation with
# `--full`, structured errors naming the fix.


def _native(tool: str, args: dict, fix: str):
    """One native MCP call, or a structured failure. Never raises."""
    from library.tools.native_mcp import NativeMcpError, call
    try:
        return call(tool, args), ""
    except NativeMcpError as exc:
        return None, fail(str(exc), fix)


def _unwrap_text(text: str) -> str:
    """The text inside the native envelope, when there is one.

    `get_scripting_docs` answers with a JSON-stringified
    `{content: [{text}]}` envelope AS its text block; plain tools
    answer with bare text. Unwrap the former, pass the latter through
    untouched - a stub that happens to parse as JSON must survive.
    """
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if isinstance(parsed, dict):
        blocks = parsed.get("content")
        if isinstance(blocks, list):
            return "\n".join(b.get("text", "") for b in blocks
                             if isinstance(b, dict))
    return text


#: Search hits shown before the `--full` escape hatch fires.
API_SEARCH_ROWS = 50


def cmd_api_search(args) -> int:
    text, _ = _native("search_scripting_api", {"pattern": args.pattern},
                      f"{TOOL} api search --help")
    if text is None:
        return 1
    lines = [line for line in text.splitlines() if line.strip()]
    shown = lines if args.full else lines[:API_SEARCH_ROWS]
    rest = len(lines) - len(shown)
    emit([kv_block("api_search", {
              "pattern": args.pattern,
              "matches": len(lines),
          }),
          table("matches", [{"line": line} for line in shown], ["line"]),
          (f"+{rest} more line(s) - re-run with --full")
          if rest else "",
          help_block([f"{TOOL} api stubs <Type>",
                      f"{TOOL} api docs --section \"<heading>\""])])
    return 0


def cmd_api_stubs(args) -> int:
    text, _ = _native("get_scripting_api", {"types": list(args.types)},
                      f"{TOOL} api stubs --help")
    if text is None:
        return 1
    emit([kv_block("api_stubs", {
              "types": ",".join(args.types),
              "chars": len(text),
          }),
          text,
          help_block([f"{TOOL} api search \"<pattern>\"",
                      f"{TOOL} api docs --section \"<heading>\""])])
    return 0


def cmd_api_docs(args) -> int:
    text, _ = _native(
        "get_scripting_docs",
        {"document": args.document, "section": args.section},
        f"{TOOL} api docs --help")
    if text is None:
        return 1
    body = _unwrap_text(text)
    emit([kv_block("api_docs", {
              "document": args.document,
              "section": args.section,
              "chars": len(body),
          }),
          body,
          help_block([f"{TOOL} api search \"<pattern>\"",
                      f"{TOOL} luts update --help" if args.document == "DCTLReadme.txt"
                      else f"{TOOL} api docs --section \"<heading>\""])])
    return 0


def cmd_api_whats_new(args) -> int:
    text, _ = _native("get_whats_new", {"since": args.since},
                      f"{TOOL} api whats-new --since <version>")
    if text is None:
        return 1
    try:
        entries = json.loads(text).get("entries", [])
    except ValueError:
        return fail(f"the native changelog came back unparseable.",
                    f"{TOOL} api whats-new --since {args.since}")
    if not isinstance(entries, list):
        entries = []
    cols = ["version", "date"] + (["changelog"] if args.full else [])
    rows = [{
        "version": e.get("version", ""),
        "date": e.get("date", ""),
        "changelog": e.get("changelog", ""),
    } for e in entries if isinstance(e, dict)]
    emit([kv_block("whats_new", {
              "since": args.since,
              "entries": len(rows),
          }),
          table("releases", rows, cols),
          (f"changelogs hidden - re-run with --full")
          if rows and not args.full else "",
          help_block([f"{TOOL} api whats-new --since {args.since} --full"]
                     if rows and not args.full else [])])
    return 0


# ── luts: the shared shelf, listed locally, written wrapped ──
#
# Listing walks the on-disk LUT directory (no Resolve contact at
# all). Writes go through the native MCP's `update_dctl` /
# `delete_dctl` / `delete_lut` / `generate_lut`, which compile-check
# before writing - this layer adds the axi discipline those tools
# lack: a dry-run plan by default, `--apply` to write, and a read-back
# verification. All of it lands in the shared `LUT/MCP` shelf, never
# in the captain's project.

#: Where Resolve keeps third-party transforms, and our shelf in it.
LUT_DIR = ("/Library/Application Support/Blackmagic Design/"
           "DaVinci Resolve/LUT")
LUT_MCP_SHELF = "MCP"

LUT_EXTS = {".dctl": "dctl", ".3dl": "lut", ".cube": "lut",
            ".dat": "lut", ".lut": "lut", ".olut": "lut"}


def _lut_rows() -> list:
    rows = []
    for base, _dirs, files in os.walk(LUT_DIR):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in LUT_EXTS:
                continue
            full = os.path.join(base, name)
            rows.append({
                "name": os.path.relpath(full, LUT_DIR),
                "kind": LUT_EXTS[ext],
                "file": full,
            })
    rows.sort(key=lambda r: r["name"])
    return rows


def cmd_luts_list(_args) -> int:
    if not os.path.isdir(LUT_DIR):
        emit(["luts: 0 rows (no shared LUT directory on this machine)",
              help_block([f"{TOOL} api docs --document DCTLReadme.txt"])])
        return 0
    rows = _lut_rows()
    kinds: dict = {}
    for row in rows:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
    emit([kv_block("luts", {
              "dir": LUT_DIR,
              "files": len(rows),
              "dctl": kinds.get("dctl", 0),
              "lut": kinds.get("lut", 0),
          }),
          table("files", rows, ["name", "kind", "file"]),
          help_block([f"{TOOL} luts update --name \"<file>\" --file <path>",
                      f"{TOOL} api docs --document DCTLReadme.txt "
                      f"--section TOC"])])
    return 0


def _lut_name(args, tool: str):
    """The shelf-relative target, or a structured refusal (None)."""
    name = args.name or args.name_pos or ""
    if not name:
        fail(f"{tool} needs a shelf file name.",
             f"{TOOL} luts {tool} --name \"<file>\"")
        return None
    if os.path.isabs(name) or ".." in name.split("/"):
        fail(f"{name!r} escapes the {LUT_MCP_SHELF} shelf - pass a "
             f"shelf-relative name like \"cool.dctl\".",
             f"{TOOL} luts {tool} --name \"cool.dctl\"")
        return None
    return name


def _lut_target(name: str) -> str:
    return os.path.join(LUT_DIR, LUT_MCP_SHELF, name)


def cmd_luts_update(args) -> int:
    if args.text and args.file:
        return fail("pass --text or --file, not both.",
                    f"{TOOL} luts update --help")
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as handle:
                content = handle.read()
        except OSError as exc:
            return fail(f"cannot read {args.file!r} ({exc}).",
                        f"{TOOL} luts update --name \"{args.name}\" "
                        f"--text \"...\"")
    elif args.text:
        content = args.text
    else:
        return fail("update needs the DCTL source: --text or --file.",
                    f"{TOOL} api docs --document DCTLReadme.txt")
    name = _lut_name(args, "update")
    if name is None:
        return 1
    target = _lut_target(name)
    if not args.apply:
        emit([kv_block("lut_plan", {
                  "name": name,
                  "target": target,
                  "bytes": len(content.encode("utf-8")),
                  "exists": "yes" if os.path.exists(target) else "no",
              }),
              preview(content, False),
              f"dry run - pass --apply to compile-check and write",
              help_block([f"{TOOL} luts update --name \"{name}\" "
                          f"--file <path> --apply"])])
        return 0
    text, _ = _native("update_dctl", {"path": name, "content": content},
                      f"{TOOL} luts list")
    if text is None:
        return 1
    placed = os.path.exists(target)
    emit([kv_block("lut_update", {
              "name": name,
              "target": target,
              "placed": "yes (read back)" if placed else
              "NO - the server said written but the file is missing",
          }),
          preview(text, args.full),
          help_block([f"{TOOL} luts list"])])
    return 0 if placed else 1


def cmd_luts_delete(args) -> int:
    name = _lut_name(args, "delete")
    if name is None:
        return 1
    target = _lut_target(name)
    if not args.apply:
        emit([kv_block("lut_plan", {
                  "name": name,
                  "target": target,
                  "exists": "yes" if os.path.exists(target) else "no",
              }),
              f"dry run - pass --apply to delete",
              help_block([f"{TOOL} luts delete --name \"{name}\" "
                          f"--apply"])])
        return 0
    tool = ("delete_dctl" if name.lower().endswith(".dctl")
            else "delete_lut")
    text, _ = _native(tool, {"path": name}, f"{TOOL} luts list")
    if text is None:
        return 1
    gone = not os.path.exists(target)
    emit([kv_block("lut_delete", {
              "name": name,
              "deleted": "yes (read back)" if gone else
              "NO - the file is still there",
          }),
          preview(text, args.full),
          help_block([f"{TOOL} luts list"])])
    return 0 if gone else 1


def cmd_luts_generate(args) -> int:
    if args.transform and args.transform_file:
        return fail("pass --transform or --transform-file, not both.",
                    f"{TOOL} luts generate --help")
    if args.transform_file:
        try:
            with open(args.transform_file,
                       encoding="utf-8") as handle:
                transform = handle.read()
        except OSError as exc:
            return fail(f"cannot read {args.transform_file!r} ({exc}).",
                        f"{TOOL} luts generate --help")
    elif args.transform:
        transform = args.transform
    else:
        return fail("generate needs the transform function body.",
                    f"{TOOL} luts generate --help")
    name = _lut_name(args, "generate")
    if name is None:
        return 1
    target = _lut_target(name)
    if not args.apply:
        emit([kv_block("lut_plan", {
                  "name": name,
                  "target": target,
                  "size": args.size,
                  "exists": "yes" if os.path.exists(target) else "no",
              }),
              preview(transform, False),
              f"dry run - pass --apply to evaluate and write",
              help_block([f"{TOOL} luts generate --name \"{name}\" "
                          f"--size {args.size} "
                          f"--transform \"...\" --apply"])])
        return 0
    text, _ = _native("generate_lut",
                      {"path": name, "size": args.size,
                       "transform": transform},
                      f"{TOOL} luts list")
    if text is None:
        return 1
    placed = os.path.exists(target)
    emit([kv_block("lut_generate", {
              "name": name,
              "target": target,
              "placed": "yes (read back)" if placed else
              "NO - the server said written but the file is missing",
          }),
          preview(text, args.full),
          help_block([f"{TOOL} luts list"])])
    return 0 if placed else 1


# ── launch / project / audio ─────────────────────────────────────


#: How the app is started. One place, so a renamed bundle fails in
#: tests instead of opening the wrong app (or nothing) live.
#: Verified against the installed bundle id
#: `com.blackmagic-design.DaVinciResolve` via
#: `osascript -e 'id of app "DaVinci Resolve"'` (resolves the name
#: without launching anything).
_LAUNCH_ARGV = ("open", "-a", "DaVinci Resolve")


def _launch_app() -> tuple:
    """Ask macOS to open Resolve. Returns `(ok, detail)`.

    A module-level seam so tests never shell out: they monkeypatch
    this, not the caller.
    """
    import subprocess as _subprocess
    try:
        started = _subprocess.run(
            list(_LAUNCH_ARGV),
            capture_output=True, text=True, encoding="utf-8",
            timeout=60, check=False)
    except Exception as exc:
        return False, f"could not ask macOS to open Resolve ({exc})"
    if started.returncode != 0:
        detail = (started.stderr or "").strip() or "no reason given"
        return False, f"macOS would not open Resolve ({detail})"
    return True, ""


def cmd_launch(_args) -> int:
    """Start the Resolve app when it is not running. Nothing else.

    Idempotent: when the scripting API already answers, nothing is
    launched and the session is reported. This never opens or creates
    a project or timeline - if Resolve starts with no project open,
    that is reported, not fixed.
    """
    try:
        resolve = _connect()
    except AxiError:
        resolve = None
    if resolve is not None:
        with _lease(exclusive=False):
            project = _project(resolve, "")
            from library.tools.marker_feedback import current_timeline
            try:
                cursor, _ = current_timeline(resolve)
                cursor_name = cursor.GetName()
            except Exception:
                cursor_name = "(none open)"
        emit([kv_block("launch", {
                  "running": "yes (already - nothing launched)",
                  "project": project.GetName(),
                  "cursor": cursor_name,
              }),
              help_block([f"{TOOL} timeline list"])])
        return 0
    ok, detail = _launch_app()
    if not ok:
        return fail(detail, f"{TOOL} launch")
    import time as _time
    launched = None
    for _ in range(30):
        _time.sleep(2)
        try:
            launched = _connect()
            break
        except AxiError:
            continue
    if launched is None:
        return fail("Resolve did not answer within 60s of launching.",
                    f"{TOOL} launch")
    with _lease(exclusive=False):
        try:
            project = _project(launched, "")
            project_name = project.GetName()
        except AxiError:
            project_name = "(no project open)"
    emit([kv_block("launch", {
              "running": "yes (launched now)",
              "project": project_name,
          }),
          help_block([f"{TOOL} timeline list"])])
    return 0


def _named_list(entries) -> list:
    rows = []
    for entry in entries or []:
        if isinstance(entry, str):
            rows.append({"name": entry})
        elif isinstance(entry, dict):
            rows.append({"name": entry.get("PresetName")
                         or entry.get("name") or str(entry)})
        else:
            rows.append({"name": str(entry)})
    return rows


def cmd_project(args) -> int:
    """The open project: identity plus the delivery-relevant settings.

    Read-only. The settings are the ones a render decision needs
    (frame rate, delivery frame) plus the preset inventory; the full
    settings dict stays one `run` script away.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        from library.tools.marker_feedback import current_timeline
        try:
            cursor, _ = current_timeline(resolve)
            cursor_name = cursor.GetName()
        except Exception:
            cursor_name = "(none open)"
        settings = {}
        for key in ("timelineFrameRate", "timelineResolutionWidth",
                    "timelineResolutionHeight"):
            try:
                settings[key] = project.GetSetting(key)
            except Exception:
                settings[key] = ""
        try:
            presets = _named_list(project.GetPresetList())
        except Exception:
            presets = []
        try:
            render_presets = _named_list(project.GetRenderPresetList())
        except Exception:
            render_presets = []
        names = _timeline_names(project)
    emit([kv_block("project", {
              "name": project.GetName(),
              "timelines": len(names),
              "cursor": cursor_name,
              "fps": settings["timelineFrameRate"],
              "resolution": (f"{settings['timelineResolutionWidth']}x"
                             f"{settings['timelineResolutionHeight']}"),
          }),
          table("render_presets", render_presets, ["name"]),
          table("presets", presets, ["name"]),
          help_block([f"{TOOL} timeline list",
                      f"{TOOL} renders"])])
    return 0


def _voice_state(value) -> str:
    if isinstance(value, dict):
        if value.get("isEnabled"):
            return f"on {value.get('amount', '')}".strip()
        return "off"
    return _cell(value)


def cmd_audio(args) -> int:
    """Audio tracks: which rows carry sound, enabled or not, how much.

    Read-only. Voice isolation and lock state ride along behind
    `--full`; nothing here changes a mix - the mix goes through OTIO
    at placement time.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        try:
            count = timeline.GetTrackCount("audio") or 0
        except Exception as exc:
            return fail(f"timeline {timeline.GetName()!r} would not "
                        f"report its audio tracks ({exc}).",
                        f"{TOOL} timeline list")
        rows = []
        for index in range(1, count + 1):
            try:
                items = (timeline.GetItemListInTrack("audio", index)
                         or [])
            except Exception:
                items = []
            row: dict = {
                "track": f"audio{index}",
                "clips": len(items),
            }
            try:
                row["name"] = timeline.GetTrackName("audio", index) or ""
            except Exception:
                row["name"] = ""
            try:
                row["enabled"] = ("yes" if timeline.GetIsTrackEnabled(
                    "audio", index) else "no")
            except Exception:
                row["enabled"] = ""
            full: dict = {}
            for key, method in (("sub_type", "GetTrackSubType"),
                                ("locked", "GetIsTrackLocked")):
                try:
                    value = getattr(timeline, method)("audio", index)
                    full[key] = ("yes" if value is True else
                                 "no" if value is False else _cell(value))
                except Exception:
                    full[key] = ""
            try:
                full["voice_isolation"] = _voice_state(
                    timeline.GetVoiceIsolationState(index))
            except Exception:
                full["voice_isolation"] = ""
            row.update(full if args.full else {})
            rows.append(row)
    cols = ["track", "name", "enabled", "clips"]
    if args.full:
        cols += ["sub_type", "locked", "voice_isolation"]
    emit([kv_block("audio", {
              "timeline": timeline.GetName() +
              (" (current)" if is_current else ""),
              "tracks": len(rows),
          }),
          note,
          table("tracks", rows, cols),
          help_block([f"{TOOL} items --timeline \"{timeline.GetName()}\""])])
    return 0


def cmd_cursor(args) -> int:
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, "")
        from library.tools.marker_feedback import current_timeline
        try:
            timeline, _ = current_timeline(resolve)
            cursor = timeline.GetName()
        except Exception as exc:
            return fail(f"no timeline open ({exc}) - open one and re-run.",
                        f"{TOOL} timeline list")
    if args.expect and cursor != args.expect:
        return fail(f"cursor is on {cursor!r}, expected {args.expect!r} - "
                    f"a sibling lane may have moved it.",
                    f"{TOOL} timeline get \"{args.expect}\"")
    emit([kv_block("cursor", {"project": project.GetName(),
                              "timeline": cursor})] +
         ([f"asserted: cursor is on {args.expect!r}"] if args.expect
          else []))
    return 0


def cmd_frames(args) -> int:
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            timeline, _, note = _target_timeline(project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        start, end, frames = _frames_of(timeline)
        try:
            fps = timeline.GetSetting("timelineFrameRate")
        except Exception:
            fps = "unknown"
        rows = []
        try:
            for track_type in ("video", "audio"):
                count = timeline.GetTrackCount(track_type) or 0
                for index in range(1, count + 1):
                    items = (timeline.GetItemListInTrack(track_type,
                                                         index) or [])
                    placed = sum((i.GetDuration() or 0) for i in items
                                 if _readable(i))
                    rows.append({"track": f"{track_type}{index}",
                                 "clips": len(items),
                                 "placed_frames": placed})
        except Exception as exc:
            return fail(f"timeline {timeline.GetName()!r} would not report "
                        f"its tracks ({exc}).",
                        f"{TOOL} timeline list")
    emit([kv_block("frames", {
              "timeline": timeline.GetName(),
              "start": start, "end": end, "frames": frames, "fps": fps,
          }),
          note,
          table("tracks", rows, ["track", "clips", "placed_frames"])])
    return 0


def _readable(item) -> bool:
    try:
        item.GetDuration()
        return True
    except Exception:
        return False


# ── run: the cheap escape hatch ──────────────────────────────────


#: Resolve mutators refused by `run` unless `--unsafe` is passed.
#: Reads in this API are `Get*`; every mutator starts with one of
#: these prefixes, so the rule is a prefix match rather than an
#: enumeration of an open-ended API: owning the serialization cost
#: does not require listing what the scripting API can do.
_RUN_WRITE_PREFIXES = ("Set", "Add", "Create", "Delete", "Import",
                       "Move", "Update", "Apply", "Replace", "Remove",
                       "Clear", "Load", "Close", "Open", "Duplicate",
                       "Start", "Stop", "Copy", "Paste", "Undo", "Save",
                       "Export", "Render", "Grab")

#: What a `run` script may assume in scope. This list is exact: the
#: script is `exec`d with exactly these names (plus what Python puts
#: there itself), so a script that is only the `result = ...` line
#: needs no boilerplate at all.
RUN_SCOPE = ("resolve", "manager", "project", "project_name",
             "timeline", "timeline_name", "is_current",
             "timeline_names", "by_index", "read_notes")

#: Columns kept when a script returns wide dicts. Past this the table
#: names the overflow and points at `--json`: a TOON table with fifty
#: columns is not cheaper than the JSON it replaces.
RUN_MAX_COLUMNS = 12


def _refused_resolve_writes(script: str) -> list:
    """Mutator attribute names in a `run` script, in first-seen order.

    A `SyntaxError` is an `AxiError` (the script never runs); anything
    else returns the offending names, possibly empty. String literals
    never match: only attribute calls (`timeline.AddMarker(...)`) do,
    so a script that merely MENTIONS a writer in a comment still runs.
    """
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        raise AxiError(f"script would not parse ({exc}).",
                       f"{TOOL} run --help") from exc
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            name = node.attr
            if name.startswith(_RUN_WRITE_PREFIXES):
                if name not in hits:
                    hits.append(name)
    return hits


#: Resolve calls that report success while destroying work that cannot
#: be recovered. `TimelineItem.CopyGrades` replaces the target's whole
#: grade, returns True while doing it, and versions nothing to go back
#: to (measured by baking each state to a 33-point LUT and comparing
#: bytes). `--unsafe` declares A write, not THIS one: the script must
#: name it out loud with `--acknowledge-copy-grades` first.
_COPYGRADES_TRAP = "CopyGrades"


def _names_copygrades(script: str) -> bool:
    """Whether the script reaches for the grade-destroying call."""
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return False
    return any(isinstance(node, ast.Attribute)
               and node.attr == _COPYGRADES_TRAP
               for node in ast.walk(tree))


def _run_cell(value, full: bool) -> tuple:
    """One result cell under the same truncation discipline as markers.

    Returns `(text, truncated)`: `text` is already truncated unless
    `full`, so `table()` quoting is the only further shaping.
    """
    text = _cell(value)
    if full or len(text) <= NOTE_PREVIEW_CHARS:
        return text, False
    return (f"{text[:NOTE_PREVIEW_CHARS]}... "
            f"(truncated, {len(text)} chars total - use --full)"), True


def _run_parts(result, full: bool, as_json: bool) -> list:
    """Render a `run` script's `result` as TOON (or JSON on request)."""
    if as_json:
        try:
            return [json.dumps(result, indent=2, default=str,
                               ensure_ascii=False)]
        except (TypeError, ValueError) as exc:
            raise AxiError(f"result would not serialize to JSON ({exc}).",
                           f"{TOOL} run --help") from exc
    if result is None:
        return ["result: (no result - set `result = ...` in the script)"]
    if isinstance(result, dict):
        pairs, truncated = {}, 0
        for key, value in result.items():
            text, cut = _run_cell(value, full)
            pairs[key] = text
            truncated += cut
        meta = (f"{truncated} cells truncated - re-run with --full"
                if truncated and not full else "")
        return [kv_block("result", pairs), meta]
    if isinstance(result, (list, tuple)):
        rows = list(result)
        if not rows:
            return ["result: 0 rows"]
        if all(isinstance(row, dict) for row in rows):
            cols = []
            for row in rows:
                for key in row:
                    if key not in cols:
                        cols.append(key)
            overflow = ""
            if len(cols) > RUN_MAX_COLUMNS:
                overflow = (f"+{len(cols) - RUN_MAX_COLUMNS} more "
                            f"columns ({cols[RUN_MAX_COLUMNS:]}) - "
                            f"use --json for the wide shape")
                cols = cols[:RUN_MAX_COLUMNS]
            shaped, truncated = [], 0
            for row in rows:
                shaped_row = {}
                for col in cols:
                    text, cut = _run_cell(row.get(col), full)
                    shaped_row[col] = text
                    truncated += cut
                shaped.append(shaped_row)
            meta = (f"{truncated} cells truncated - re-run with --full"
                    if truncated and not full else "")
            return [table("result", shaped, cols), overflow, meta]
        shaped, truncated = [], 0
        for value in rows:
            text, cut = _run_cell(value, full)
            shaped.append({"value": text})
            truncated += cut
        meta = (f"{truncated} cells truncated - re-run with --full"
                if truncated and not full else "")
        return [table("result", shaped, ["value"]), meta]
    text, cut = _run_cell(result, full)
    return [kv_block("result", {"value": text}),
            ("re-run with --full for the whole value"
             if cut and not full else "")]


def cmd_run(args) -> int:
    """Execute a caller script with ready Resolve names, render `result`.

    The point is NOT to enumerate the scripting API: it is that an
    arbitrary script stops costing arbitrary tokens. The script runs
    with exactly `RUN_SCOPE` in scope (documented above and in
    `--help`), and whatever it leaves in `result` renders as TOON rows
    under the same truncation discipline as every other command
    (`--full` and `--json` escape it).

    Safety, stated plainly:

    - Default is a READ: the script is AST-scanned for Resolve writers
      (`_RUN_WRITE_PREFIXES`) and refused loudly when one appears.
    - `--unsafe` is the declared write path: it skips the refusal,
      holds the Resolve lease EXCLUSIVE, and reports the cursor before
      and after (an arbitrary script owns its own cursor, so there is
      nothing to assert it against). Reads must still never move the
      cursor, and nothing in this module moves it either way.
    - `--unsafe` still refuses `CopyGrades` without
      `--acknowledge-copy-grades`: that call destroys the target's
      grade while reporting success.
    """
    if args.script and args.file:
        return fail("pass --script or --file, not both.",
                    f"{TOOL} run --help")
    if args.script_pos and (args.script or args.file):
        return fail("pass the script positionally or with --script/--file,"
                    " not both.",
                    f"{TOOL} run --help")
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as handle:
                script = handle.read()
        except OSError as exc:
            return fail(f"cannot read script file {args.file!r} ({exc}).",
                        f"{TOOL} run --script \"result = ...\"")
        unsafe_form = f"--file {args.file}"
    elif args.script:
        script = args.script
        unsafe_form = "--script \"...\""
    elif args.script_pos:
        script = args.script_pos
        unsafe_form = "\"...\""
    else:
        return fail("run needs a script: `run \"result = ...\"`, --script "
                    "\"result = ...\", or --file <path>.",
                    f"{TOOL} run --help")
    try:
        refused = _refused_resolve_writes(script)
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    if refused and not args.unsafe:
        timeline_flag = (f"--timeline \"{args.timeline}\" "
                         if args.timeline else "")
        return fail(
            f"script calls Resolve writers ({', '.join(refused)}) - "
            f"run is read-only by default.",
            f"{TOOL} run {timeline_flag}{unsafe_form} --unsafe "
            f"to declare the write")
    if (args.unsafe and not args.acknowledge_copy_grades
            and _names_copygrades(script)):
        timeline_flag = (f"--timeline \"{args.timeline}\" "
                         if args.timeline else "")
        return fail(
            f"script calls {_COPYGRADES_TRAP}, which replaces the "
            f"target's whole grade, reports success, and versions "
            f"nothing to go back to - `--unsafe` declares a write, "
            f"not this one.",
            f"{TOOL} run {timeline_flag}{unsafe_form} --unsafe "
            f"--acknowledge-copy-grades to say you know what it does")
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=args.unsafe):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        from library.tools import marker_feedback
        from library.tools.marker_feedback import current_timeline
        try:
            open_timeline, _ = current_timeline(resolve)
            cursor_before = open_timeline.GetName()
        except Exception:
            cursor_before = "(none open)"
        scope = {
            "resolve": resolve,
            "manager": resolve.GetProjectManager(),
            "project": project,
            "project_name": project.GetName(),
            "timeline": timeline,
            "timeline_name": timeline.GetName(),
            "is_current": is_current,
            "timeline_names": _timeline_names(project),
            "by_index": project.GetTimelineByIndex,
            "read_notes": marker_feedback.read_notes,
        }
        sandbox = dict(scope)
        try:
            exec(compile(script, "<resolve-axi run>", "exec"), sandbox)  # noqa: S102
        except Exception as exc:
            return fail(f"script raised {type(exc).__name__}: {exc}.",
                        f"{TOOL} run --help")
        result = sandbox.get("result")
        try:
            open_after, _ = current_timeline(resolve)
            cursor_after = open_after.GetName()
        except Exception:
            cursor_after = "(none open)"
        try:
            rendered = _run_parts(result, args.full, args.json)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
    meta = {"timeline": timeline.GetName() +
            (" (current)" if is_current else ""),
            "unsafe": "yes" if args.unsafe else "no"}
    if args.unsafe:
        meta["cursor_before"] = cursor_before
        meta["cursor_after"] = cursor_after
        meta["cursor_moved"] = ("yes" if cursor_before != cursor_after
                                else "no")
    out = [kv_block("run", meta), note] + rendered
    if args.unsafe and cursor_before != cursor_after:
        out.append(f"cursor moved: {cursor_before!r} -> "
                   f"{cursor_after!r}")
    out.append(help_block([f"{TOOL} timeline list",
                           f"{TOOL} markers --timeline "
                           f"\"{timeline.GetName()}\""]))
    emit(out)
    return 0


# ── setup / update ───────────────────────────────────────────────────


def cmd_setup_hooks(args) -> int:
    """Opt-in session hook: a static routing hint, never a live Resolve read.

    A hook that calls Resolve at every session start would probe a
    possibly-closed app on every session for every lane; the live state
    belongs in explicit invocations. What the hook carries is the way
    to reach it.
    """
    app = (args.app or "").lower()
    if app not in ("opencode", "claude-code", "codex"):
        return fail(f"unknown app {args.app!r}.",
                    f"{TOOL} setup hooks --app "
                    f"opencode|claude-code|codex")
    homes = {
        "opencode": os.path.join(os.path.expanduser("~"), ".config",
                                 "opencode", "plugins", "resolve-axi.js"),
        "claude-code": os.path.join(os.path.expanduser("~"), ".claude",
                                    "settings.json"),
        "codex": os.path.join(os.path.expanduser("~"), ".codex",
                              "hooks.json"),
    }
    emit([kv_block("setup", {
              "app": app,
              "path": homes[app],
              "note": "review the path, then re-run with --write to "
                      "install the session hook",
          }),
          help_block([f"{TOOL} setup hooks --app {app} --write"])])
    if not args.write:
        return 0
    return fail("hook installation is not implemented in this preview - "
                "the live state stays one explicit call away.",
                f"{TOOL} timeline list")


def cmd_update(_args) -> int:
    import subprocess
    sha = "unknown"
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=here,
            capture_output=True, text=True, encoding="utf-8",
            timeout=10, check=False)
        if out.returncode == 0:
            sha = out.stdout.strip()
    except Exception:
        pass
    emit([kv_block("update", {
              "package": TOOL,
              "version": VERSION,
              "installed": "repository checkout - pull to update",
              "sha": sha,
          })])
    return 0


# ── Argument wiring ──────────────────────────────────────────────────


def _add_scope(parser, what: str) -> None:
    parser.add_argument("--project", default="",
                        help=f"expect this Resolve project open ({what})")
    parser.add_argument("--timeline", default="",
                        help="which timeline: the exact name, a UNIQUE "
                             "prefix, or a bare positional "
                             "(markers \"Reel 29\"); omit for the open one. "
                             "An ambiguous prefix is refused.")


def build_parser() -> Parser:
    parser = Parser(prog=TOOL, description=DESCRIPTION,
                    formatter_class=argparse.RawDescriptionHelpFormatter,
                    epilog=f"""examples:
  {TOOL}
  {TOOL} timeline list
  {TOOL} timeline get "Reel 13 - moment"
  {TOOL} markers "Reel 29"
  {TOOL} markers --timeline "Reel 13 - moment"
  {TOOL} markers snapshot --timeline "Reel 13 - moment" --out /tmp/m.json
  {TOOL} cursor --expect "Reel 13 - moment"
  {TOOL} frames --timeline "Reel 13 - moment"
  {TOOL} pool
  {TOOL} pool "Footage/Day 1"
  {TOOL} renders
  {TOOL} api search "marker"
  {TOOL} api docs --section "Audio Mapping"
  {TOOL} luts list
  {TOOL} project
  {TOOL} audio --timeline "Reel 29"
  {TOOL} run --timeline "Reel 29" --script "result = timeline_names"
  {TOOL} run --timeline "Reel 29" "result = timeline_names\"""")
    subs = parser.add_subparsers(dest="command")

    p = subs.add_parser("timeline", help="list timelines or get one by "
                                         "exact name")
    tsubs = p.add_subparsers(dest="timeline_command", required=True)
    pl = tsubs.add_parser("list", help="every timeline with frame counts, "
                                       "marker colours and staging flags")
    pl.add_argument("--project", default="",
                    help="expect this Resolve project open")
    pl.set_defaults(func=cmd_timeline_list)
    pg = tsubs.add_parser("get", help="one timeline by name - exact or "
                                      "unique prefix; collisions refused")
    pg.add_argument("name", help="the timeline's name (exact or unique "
                                 "prefix)")
    pg.add_argument("--project", default="",
                    help="expect this Resolve project open")
    pg.set_defaults(func=cmd_timeline_get)

    p = subs.add_parser("markers", help="every note on a reel, both "
                                        "marker planes in one call; "
                                        "snapshot/restore round-trip "
                                        "marker content")
    _add_scope(p, "markers")
    p.add_argument("--plane", default="",
                   help="timeline_marker|clip_marker|media_pool_marker|"
                        "clip_comment")
    p.add_argument("--full", action="store_true",
                   help="show complete note text instead of the preview")
    p.set_defaults(func=cmd_markers)
    msubs = p.add_subparsers(dest="markers_command")

    p = msubs.add_parser("snapshot", help="write a reel's marker "
                                          "CONTENT to a file")
    _add_scope(p, "snapshot")
    p.add_argument("--out", required=True, help="snapshot file to write")
    p.set_defaults(func=cmd_markers_snapshot)

    p = msubs.add_parser("restore", help="dry-run a snapshot "
                                         "against live markers; "
                                         "--apply writes")
    _add_scope(p, "restore")
    p.add_argument("--in", dest="in_file", required=True,
                   help="snapshot file to restore from")
    p.add_argument("--apply", action="store_true",
                   help="write the missing timeline-plane markers under "
                        "the Resolve lease (default is a dry-run diff)")
    p.add_argument("--allow-partial", action="store_true",
                   help="acknowledge that the snapshot holds clip-plane "
                        "notes restore cannot write, and proceed with "
                        "the timeline plane only (without it, such a "
                        "snapshot is refused before any Resolve contact)")
    p.set_defaults(func=cmd_markers_restore)

    p = msubs.add_parser("reply", help="record our answer as a marker, "
                                       "linked to the note it answers; "
                                       "--apply writes")
    _add_scope(p, "reply")
    p.add_argument("--frame", required=True, type=int,
                   help="ABSOLUTE frame where the reply lands (the space "
                        "`markers` reports)")
    p.add_argument("--color", default="",
                   help="marker colour - required, never defaulted")
    p.add_argument("--name", default="",
                   help="reply marker name - required (Resolve refuses "
                        "empty names)")
    p.add_argument("--note", default="",
                   help="the reply text - required")
    p.add_argument("--duration", default=1, type=int,
                   help="marker duration in frames (default 1)")
    p.add_argument("--answers-frame", default=None, type=int,
                   help="frame of the note being answered: its text is "
                        "looked up live into the payload and shown")
    p.add_argument("--answers", default="",
                   help="explicit identity of the answered note, "
                        "validated against the durable-identity grammar "
                        "(else the note's own identity is taken from "
                        "--answers-frame)")
    p.add_argument("--summary", default="",
                   help="one-line summary recorded into the payload")
    p.add_argument("--apply", action="store_true",
                   help="place the marker under the Resolve lease with "
                        "the cursor asserted (default is a dry-run plan)")
    p.set_defaults(func=cmd_markers_reply)

    p = msubs.add_parser("audit-replies", help="report which replies "
                                               "still sit beside the "
                                               "notes they answer; "
                                               "read-only, writes nothing")
    _add_scope(p, "audit-replies")
    p.add_argument("--full", action="store_true",
                   help="show complete names and identities instead of "
                        "the preview")
    p.set_defaults(func=cmd_markers_audit_replies)

    p = subs.add_parser("fusion", help="clips carrying Fusion comps and "
                                       "whether each comp covers its item")
    _add_scope(p, "fusion")
    p.set_defaults(func=cmd_fusion)

    p = subs.add_parser("items", help="placed clips with record/source "
                                      "spans and source paths")
    _add_scope(p, "items")
    p.add_argument("--transforms", action="store_true",
                   help="add the stored Pan/Tilt/Zoom/Opacity columns, "
                        "read back verbatim (units unconverted)")
    p.set_defaults(func=cmd_items)

    p = subs.add_parser("captions", help="Subtitles-row cards with file "
                                         "paths")
    _add_scope(p, "captions")
    p.set_defaults(func=cmd_captions)

    p = subs.add_parser("cursor", help="show (and optionally assert) the "
                                       "current-timeline cursor")
    p.add_argument("--expect", default="",
                   help="refuse unless the cursor sits on this timeline")
    p.set_defaults(func=cmd_cursor)

    p = subs.add_parser("frames", help="frame counts for drift checks")
    _add_scope(p, "frames")
    p.set_defaults(func=cmd_frames)

    p = subs.add_parser("api", help="the native MCP's knowledge tools "
                                   "as TOON: stubs, search, docs, "
                                   "whats-new")
    asubs = p.add_subparsers(dest="api_command", required=True)
    q = asubs.add_parser("search", help="search the scripting API "
                                        "stubs by pattern")
    q.add_argument("pattern", help="regex matched against stub names "
                                    "and descriptions, e.g. \"marker\"")
    q.add_argument("--full", action="store_true",
                   help="all matching lines instead of the first 50")
    q.set_defaults(func=cmd_api_search)
    q = asubs.add_parser("stubs", help="the .pyi declarations for "
                                        "named API types")
    q.add_argument("types", nargs="+",
                   help="type names, e.g. RenderJobInfo RenderJobStatus")
    q.set_defaults(func=cmd_api_stubs)
    q = asubs.add_parser("docs", help="the developer documents, whole "
                                       "or by section")
    q.add_argument("--document", default="README.md",
                   help="README.md (scripting guide) or DCTLReadme.txt "
                        "(DCTL reference)")
    q.add_argument("--section", default="TOC",
                   help="heading to return (default TOC); \"\" returns "
                        "the whole document")
    q.set_defaults(func=cmd_api_docs)
    q = asubs.add_parser("whats-new", help="the Resolve changelog since "
                                            "a version or date")
    q.add_argument("--since", required=True,
                   help="version (e.g. \"21.0\") or ISO date "
                        "(e.g. \"2025-01-25\")")
    q.add_argument("--full", action="store_true",
                   help="include each release's changelog text")
    q.set_defaults(func=cmd_api_whats_new)

    p = subs.add_parser("luts", help="the shared LUT shelf: list "
                                     "locally, write wrapped with "
                                     "compile check (dry-run default)")
    lsubs = p.add_subparsers(dest="luts_command", required=True)
    q = lsubs.add_parser("list", help="every DCTL and LUT file on the "
                                       "shared shelf")
    q.set_defaults(func=cmd_luts_list)
    q = lsubs.add_parser("update", help="write a .dctl file "
                                         "(compile-checked); --apply "
                                         "writes")
    q.add_argument("name_pos", nargs="?", default="",
                   help="shelf file name, e.g. \"cool.dctl\" (--name "
                        "is the explicit form)")
    q.add_argument("--name", default="",
                   help="shelf file name relative to the MCP shelf")
    q.add_argument("--text", default="",
                   help="the DCTL source")
    q.add_argument("--file", default="",
                   help="read the DCTL source from this file")
    q.add_argument("--apply", action="store_true",
                   help="compile-check and write (default is a dry-run "
                        "plan)")
    q.set_defaults(func=cmd_luts_update)
    q = lsubs.add_parser("delete", help="delete a shelf file; --apply "
                                         "deletes")
    q.add_argument("name_pos", nargs="?", default="",
                   help="shelf file name (--name is the explicit form)")
    q.add_argument("--name", default="",
                   help="shelf file name relative to the MCP shelf")
    q.add_argument("--apply", action="store_true",
                   help="delete (default is a dry-run plan)")
    q.set_defaults(func=cmd_luts_delete)
    q = lsubs.add_parser("generate", help="evaluate a Python transform "
                                           "into a .cube LUT; --apply "
                                           "writes")
    q.add_argument("name_pos", nargs="?", default="",
                   help="shelf file name, e.g. \"warm.cube\" (--name is "
                        "the explicit form)")
    q.add_argument("--name", default="",
                   help="shelf file name relative to the MCP shelf")
    q.add_argument("--size", default=33, type=int,
                   help="cube size per axis (17, 33, 65)")
    q.add_argument("--transform", default="",
                   help="Python body: receives r, g, b in [0,1], "
                        "returns an (r, g, b) tuple")
    q.add_argument("--transform-file", default="",
                   help="read the transform body from this file")
    q.add_argument("--apply", action="store_true",
                   help="evaluate and write (default is a dry-run plan)")
    q.set_defaults(func=cmd_luts_generate)

    p = subs.add_parser("launch", help="start Resolve when it is not "
                                       "running; never opens a project")
    p.set_defaults(func=cmd_launch)

    p = subs.add_parser("project", help="the open project: identity, "
                                        "delivery settings, presets")
    p.add_argument("--project", default="",
                   help="expect this Resolve project open")
    p.set_defaults(func=cmd_project)

    p = subs.add_parser("audio", help="audio tracks: enable state and "
                                      "clip counts (read-only)")
    _add_scope(p, "audio")
    p.add_argument("--full", action="store_true",
                   help="add sub-type, lock and voice-isolation columns")
    p.set_defaults(func=cmd_audio)

    p = subs.add_parser("pool", help="every clip in the media pool, "
                                    "with the bin it sits in")
    p.add_argument("--project", default="",
                   help="expect this Resolve project open")
    p.add_argument("--bin", default="",
                   help="bin path A/B/C from the pool root (or a bare "
                        "positional); omit for the whole pool")
    p.set_defaults(func=cmd_pool)

    p = subs.add_parser("renders", help="the render queue: which "
                                       "timeline, how far, what failed")
    p.add_argument("--project", default="",
                   help="expect this Resolve project open")
    p.add_argument("--full", action="store_true",
                   help="add the raw localized status and output "
                        "path columns")
    p.set_defaults(func=cmd_renders)

    p = subs.add_parser(
        "run",
        help="execute a caller script with ready Resolve names; "
             "`result` renders as TOON (read-only unless --unsafe)",
        description=(
            "The cheap escape hatch: the script runs with exactly "
            "these names in scope - resolve, manager, project, "
            "project_name, timeline, timeline_name, is_current, "
            "timeline_names, by_index, read_notes - and whatever it "
            "leaves in `result` renders as TOON rows under the same "
            "truncation discipline as every other command. A list of "
            "dicts renders as a typed table; --full and --json escape "
            "the truncation. Without --unsafe the script is AST-scanned "
            "for Resolve writers and refused when one appears; --unsafe "
            "declares the write, holds the exclusive lease, and reports "
            "the cursor before and after."))
    _add_scope(p, "run")
    p.add_argument("script_pos", nargs="?", default="",
                   help="the script itself (positional); sets `result = ...`")
    p.add_argument("--script", default="",
                   help="inline script; set `result = ...`")
    p.add_argument("--file", default="",
                   help="read the script from this file")
    p.add_argument("--full", action="store_true",
                   help="no per-cell truncation")
    p.add_argument("--json", action="store_true",
                   help="emit result as JSON instead of TOON")
    p.add_argument("--unsafe", action="store_true",
                   help="allow Resolve writers; holds the exclusive "
                        "lease and reports cursor before/after")
    p.add_argument("--acknowledge-copy-grades", action="store_true",
                   help="the script names CopyGrades out loud: it "
                        "replaces the target's whole grade with no "
                        "version to go back to")
    p.set_defaults(func=cmd_run)

    p = subs.add_parser("setup", help="session integrations")
    ssubs = p.add_subparsers(dest="setup_command", required=True)
    ph = ssubs.add_parser("hooks", help="install the opt-in session hook")
    ph.add_argument("--app", default="",
                    help="opencode|claude-code|codex")
    ph.add_argument("--write", action="store_true",
                    help="write the hook (default only shows the plan)")
    ph.set_defaults(func=cmd_setup_hooks)

    p = subs.add_parser("update", help="report the installed version")
    p.set_defaults(func=cmd_update)
    return parser


#: Commands whose first job is one timeline: a bare name there is the
#: obvious shape (`markers "Reel 29"`), so it is accepted.
_BARE_TIMELINE_COMMANDS = ("markers", "items", "captions", "frames",
                           "fusion")

#: Commands whose first job is one pool bin: a bare path there is the
#: obvious shape (`pool "Footage/Day 1"`), so it is accepted onto
#: `--bin` the same way a bare reel routes onto `--timeline`.
_BARE_BIN_COMMANDS = ("pool",)

#: First-position tokens these commands own: `markers snapshot ...`,
#: `markers restore ...` and `markers reply ...` must keep routing to
#: their subcommand.
_OWN_SUBCOMMANDS = ("snapshot", "restore", "reply")


def _normalize(argv: list) -> list:
    """Rewrite a bare timeline name onto `--timeline`, before parsing.

    `markers "Reel 29 - x"` fails at argparse's subcommand dispatch
    (the name collides with the `snapshot`/`restore` position), and
    `items "Reel 29 - x"` fails as an unrecognized argument - three
    attempts plus a `--help` round trip before the first real read,
    which is the cost this tool removes. The rewrite is deliberately
    narrow: only the token right after the command, only when it is
    not a flag and not one of the command's own subcommands. Flags
    (`--timeline`, `--plane`, `--full`) keep working exactly as before.
    """
    if (len(argv) >= 2 and argv[0] in _BARE_TIMELINE_COMMANDS
            and not argv[1].startswith("-")
            and argv[1] not in _OWN_SUBCOMMANDS):
        return [argv[0], "--timeline", argv[1]] + argv[2:]
    if (len(argv) >= 2 and argv[0] in _BARE_BIN_COMMANDS
            and not argv[1].startswith("-")):
        return [argv[0], "--bin", argv[1]] + argv[2:]
    return argv


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 1 and argv[0] in ("-v", "-V", "--version"):
        sys.stdout.write(f"{VERSION}\n")
        return 0
    parser = build_parser()
    if not argv:
        return cmd_home(argparse.Namespace())
    args = parser.parse_args(_normalize(argv))
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stdout)
        return 2
    try:
        return func(args)
    except AxiError as exc:
        return fail(str(exc), exc.fix)


if __name__ == "__main__":
    _HERE = os.path.abspath(__file__)
    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    raise SystemExit(main())
