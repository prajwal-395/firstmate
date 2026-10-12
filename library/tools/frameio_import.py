"""`ren notes import`: Frame.io comment exports into the shared note store.

Plan item T18 (file import only, no API or OAuth): a Frame.io comment
export - ``.csv``, ``.fiojson`` or ``.edl`` - becomes notes in the same
note store Resolve markers and ``ren revise --at`` chat notes share
(one pull file under the marker-feedback area), so ``ren revise`` can
consume them by note id and ``feedback_ledger`` folds a re-imported
comment onto its first asking.

What is kept per comment: the author, the Frame.io comment id, the
timestamp or range, and the annotation. A file time maps to timeline
time through the delivery record (``.deliver.json``), exactly the way
``delivered_note_anchor.resolve`` maps a chat note - a comment whose
time cannot be mapped is LISTED in the result, never guessed
(vep-frameio-workflow rec. 3). A comment with no timestamp becomes a
general (unplaced) note, the same shape a marker with no frame already
has in ``marker_routing``.

The three formats, and why each parses the way it does:

* ``.fiojson`` - the JSON shape this module documents (see
  :data:`FIOJSON_FORMAT`): ``{"comments": [{id, author, text,
  timestamp, timestamp_end, annotation, resolved}]``. A bare list is
  accepted too. Timestamps are file seconds (a number) or a clock
  (``M:SS``, ``H:M:SS``); anything else is unmapped, not coerced.
* ``.csv`` - a comment export as rows. Headers are matched
  case-insensitively against synonym sets (:data:`CSV_FIELDS`) because
  no single export header spelling is guaranteed; an unknown column is
  ignored, a missing text column fails the import (a comment with no
  words asks nothing).
* ``.edl`` - Frame.io's EDL export, the lowest-friction Resolve bridge
  (vep-frameio-workflow rec. 2): CMX3600-style events whose source-in
  timecode is the comment's file time, with ``* COMMENT:`` / ``* LOC:``
  lines carrying the words. Timecodes convert through the delivery
  record's own frame rate, reconciled at import time; an event with no
  parsable timecode is unmapped, never placed at zero (a note at
  0.0..0.0 would read as the first frame of the cut).

A note's stable id is its Frame.io comment id when the export carried
one (``fio:<id>``) - the stable half, the way a marker's ``note_id``
is - and a digest of reel, words and moment otherwise, the same shape
``delivered_note_anchor.anchored_identity`` takes. Re-importing the
same export therefore re-asks the same notes instead of filing
duplicates: the ledger folds them by id.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools.ren_refusal import RenRefusal

FIOJSON_FORMAT = "frameio_comments/1"
IMPORT_FORMAT = "frameio_import/1"

SUPPORTED_SUFFIXES = (".csv", ".fiojson", ".edl")


class FrameioImportRefused(RenRefusal):
    """The export cannot become notes; nothing was stored."""


# ── The parsed comment ──────────────────────────────────────────────
#
# One comment as the mapping below reads it: who wrote it, what they
# said, where in the file they meant (or None for a general note), and
# the annotation the reviewer drew. `file_end_seconds` is the range end
# when the comment spans one; otherwise it equals `file_seconds`.


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _comment_id(value: Any, fallback_parts: list) -> str:
    """The stable id: the export's own comment id, else a digest.

    The export's id is the stable half - re-importing the same export
    re-asks the same note. Without one the digest of reel, words and
    moment is the fallback, the same shape `anchored_identity` takes so
    the ledger folds a re-ask onto its first asking.
    """
    if not _blank(value):
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
        return f"fio:{stem or 'comment'}"
    from library.tools.feedback_ledger import base_reel_name, normalise_text

    reel = base_reel_name(str(fallback_parts[0]) if fallback_parts else "")
    words = str(fallback_parts[1]) if len(fallback_parts) > 1 else ""
    moment = str(fallback_parts[2]) if len(fallback_parts) > 2 else ""
    payload = (f"{reel}\n{normalise_text(words)}\n{moment}").encode()
    digest = hashlib.sha256(payload).hexdigest()[:16]
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in reel)
    return f"{stem or 'timeline'}:{digest}"


# ── Clocks ──────────────────────────────────────────────────────────
#
# A file time may arrive as seconds, a clock (`M:SS`, `H:M:SS`), or an
# EDL timecode (`HH:MM:SS:FF`). All three convert here, at import time,
# against the delivery record's own frame rate - and a value in none of
# those shapes is unmapped, never placed at zero.


def to_seconds(value: Any, fps: float = 0.0) -> float | None:
    """File seconds for one export timestamp, or None when unmappable.

    Numbers and numeric strings are seconds. `M:SS[.f]` and `H:M:SS[.f]`
    go through the same shapes `delivered_note_anchor.parse_at` takes.
    `HH:MM:SS:FF` (or `HH:MM:SS;FF`) is an EDL timecode and needs the
    delivery rate; without one it is unmapped rather than divided by a
    guessed rate. Anything else - including a negative time - is None.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if float(value) >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        seconds = float(text)
        return seconds if seconds >= 0 else None
    clock = re.fullmatch(r"(\d+):(\d{1,2}(?:\.\d+)?)", text)
    if clock:
        total = float(clock.group(1)) * 60.0 + float(clock.group(2))
        return total if total >= 0 else None
    long_clock = re.fullmatch(
        r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)", text)
    if long_clock:
        total = (float(long_clock.group(1)) * 3600.0
                 + float(long_clock.group(2)) * 60.0
                 + float(long_clock.group(3)))
        return total if total >= 0 else None
    timecode = re.fullmatch(r"(\d+):(\d{2}):(\d{2})[:;](\d{2})", text)
    if timecode and fps > 0:
        hours, minutes, seconds, frames = (
            int(group) for group in timecode.groups())
        if minutes >= 60 or seconds >= 60 or frames >= round(fps):
            return None
        return hours * 3600.0 + minutes * 60.0 + seconds + frames / fps
    return None


# ── .fiojson ─────────────────────────────────────────────────────────


def parse_fiojson(document: Any) -> list:
    """The JSON export: `{"comments": [...]}` or a bare list.

    Per comment: `id` (or `comment_id`/`uuid`), `author` (a name or
    `{name, email}`), `text` (or `body`/`comment`), `timestamp` and
    optional `timestamp_end` (seconds or a clock), `annotation` (kept
    verbatim), `resolved` (or `completed`). A comment with no words is
    skipped - it asks nothing - and one with no id gets its stable id
    from the digest fallback at map time.
    """
    if isinstance(document, Mapping):
        for key in ("comments", "items", "rows"):
            if isinstance(document.get(key), list):
                document = document[key]
                break
        else:
            raise FrameioImportRefused(
                "the JSON export names no comment list",
                "a Frame.io JSON export carries its comments under "
                "`comments`",
                "export the comments as JSON with a `comments` list, "
                "or pass the .csv or .edl export")
    if not isinstance(document, list):
        raise FrameioImportRefused(
            "the JSON export is neither a comment list nor a document "
            "carrying one",
            "a Frame.io JSON export is `{\"comments\": [...]}`",
            "export the comments as JSON with a `comments` list, "
            "or pass the .csv or .edl export")
    comments = []
    for entry in document:
        if not isinstance(entry, Mapping):
            continue
        author = entry.get("author")
        if isinstance(author, Mapping):
            author = author.get("name") or author.get("email") or ""
        text = entry.get("text")
        if _blank(text):
            text = entry.get("body")
        if _blank(text):
            text = entry.get("comment")
        if _blank(text):
            continue  # a comment with no words asks nothing
        comments.append({
            "comment_id": str(entry.get("id")
                              or entry.get("comment_id")
                              or entry.get("uuid") or ""),
            "author": str(author or "").strip(),
            "text": str(text).strip(),
            "timestamp": entry.get("timestamp",
                                   entry.get("time", entry.get("file_time"))),
            "timestamp_end": entry.get("timestamp_end",
                                       entry.get("end_time",
                                                 entry.get("range_end"))),
            "annotation": entry.get("annotation",
                                    entry.get("anchor", "")),
            "resolved": bool(entry.get("resolved")
                             or entry.get("completed")
                             or entry.get("done")),
            "replies": entry.get("replies") or [],
        })
    return comments


# ── .csv ─────────────────────────────────────────────────────────────


#: Header synonyms, matched case-insensitively after stripping. The
#: first spelling is what the import report names.
CSV_FIELDS = {
    "comment_id": ("comment id", "comment_id", "id", "uuid"),
    "author": ("author", "user", "reviewer", "created by", "created_by",
               "email"),
    "text": ("text", "comment", "body", "note", "message"),
    "timestamp": ("timestamp", "time", "timecode", "file time",
                  "file_time", "start", "start time", "start_time",
                  "at", "position"),
    "timestamp_end": ("end", "end time", "end_time", "timestamp end",
                      "timestamp_end", "range end", "range_end", "to"),
    "duration": ("duration", "length", "range length"),
    "annotation": ("annotation", "anchor", "drawing", "attachment",
                   "markup"),
    "resolved": ("resolved", "completed", "done", "status"),
}


def _csv_columns(header: list) -> dict:
    """Each known field's column index, from the header's own words."""
    normalized = [re.sub(r"\s+", " ", (cell or "").strip().lower())
                  for cell in header]
    columns: dict = {}
    for field, synonyms in CSV_FIELDS.items():
        for index, cell in enumerate(normalized):
            if cell in synonyms and field not in columns:
                columns[field] = index
    return columns


def parse_csv_rows(rows: list) -> list:
    """A CSV export's rows under a synonym-matched header.

    Refuses when no text column is found - without words there is no
    note to file - and skips wordless rows. An unknown column is
    ignored; a missing optional column leaves its field empty.
    """
    if not rows:
        raise FrameioImportRefused(
            "the CSV export is empty",
            "a Frame.io CSV export carries one comment per row under a "
            "header",
            "export the comments as CSV with a header row")
    columns = _csv_columns(list(rows[0]))
    if "text" not in columns:
        raise FrameioImportRefused(
            "the CSV export names no comment-text column",
            "a comment's words arrive under `text`, `comment`, `body`, "
            "`note` or `message`",
            "export the comments with a text column under one of those "
            "headers")
    comments = []
    for row in rows[1:]:
        def cell(field: str, _row: list = row) -> str:
            index = columns.get(field)
            if index is None or index >= len(_row):
                return ""
            return (_row[index] or "").strip()

        if _blank(cell("text")):
            continue
        comments.append({
            "comment_id": cell("comment_id"),
            "author": cell("author"),
            "text": cell("text"),
            "timestamp": cell("timestamp") or None,
            "timestamp_end": cell("timestamp_end") or None,
            "timestamp_duration": cell("duration") or None,
            "annotation": cell("annotation"),
            "resolved": cell("resolved").lower() in (
                "true", "yes", "1", "resolved", "completed", "done",
                "closed"),
            "replies": [],
        })
    return comments


# ── .edl ──────────────────────────────────────────────────────────────
#
# CMX3600-style events: `001  CLIP  V  C  00:00:12:00 00:00:14:00 ...`
# with `* COMMENT:` / `* LOC:` lines carrying the words. The source-in
# timecode is the comment's file time; the source-out end is its range
# end when the two differ. Comment lines of one event join into one
# note; an event with no comment line is not a comment and is skipped.


_EDL_EVENT = re.compile(
    r"^(\d{3,})\s+\S+\s+\S+\s+\S+\s+"
    r"(\d{2}:\d{2}:\d{2}[:;]\d{2})\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})")


def parse_edl_text(text: str) -> list:
    """An EDL export's events as comments.

    `AUTHOR | words` on a comment line splits the author off; anything
    else is all words with no author. The `TITLE:` header is ignored -
    it names the sequence, not a comment. An EDL with no comment lines
    fails the import rather than filing an empty pull.
    """
    events: list = []
    current: dict | None = None

    def flush() -> None:
        if current is not None and current["lines"]:
            words = " ".join(current["lines"]).strip()
            if words:
                author, text_value = "", words
                split = re.split(r"\s+[|:]\s+", words, maxsplit=1)
                if len(split) == 2 and len(split[0]) <= 64:
                    author, text_value = split
                events.append({
                    "comment_id": "",
                    "author": author.strip(),
                    "text": text_value.strip(),
                    "timestamp": current["start"],
                    "timestamp_end": current["end"],
                    "annotation": "",
                    "resolved": False,
                    "replies": [],
                })

    for line in (text or "").splitlines():
        event = _EDL_EVENT.match(line.strip())
        if event:
            flush()
            current = {"start": event.group(2), "end": event.group(3),
                       "lines": []}
            continue
        comment = re.match(
            r"^\*\s+(?:COMMENT|LOC|NOTE)\s*:\s*(.*)$", line.strip(),
            re.IGNORECASE)
        if comment and current is not None:
            current["lines"].append(comment.group(1).strip())
    flush()
    if not events:
        raise FrameioImportRefused(
            "the EDL export carries no comment lines",
            "a Frame.io EDL export places timecoded comments as "
            "`* COMMENT:` lines on events",
            "export the comments with timecoded markers, or pass the "
            ".csv or .fiojson export")
    return events


# ── Reading one export ───────────────────────────────────────────────


def read_export(path: str) -> list:
    """Parse one export file by its suffix: `.csv`, `.fiojson`, `.edl`.

    `.json` reads as the fiojson shape. Anything else is refused with
    the three accepted suffixes, so a media file never parses as a
    comment list by accident.
    """
    source = Path(path)
    if not source.is_file():
        raise FrameioImportRefused(
            f"no export file at {path!r}",
            "a Frame.io comment export is a file on disk",
            "pass the exported .csv, .fiojson or .edl path")
    suffix = source.suffix.lower()
    if suffix == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            return parse_csv_rows(list(csv.reader(handle)))
    if suffix in (".fiojson", ".json"):
        try:
            document = json.loads(source.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise FrameioImportRefused(
                f"the JSON export does not parse: {exc}",
                "a Frame.io JSON export is JSON",
                "export the comments again as JSON") from exc
        return parse_fiojson(document)
    if suffix == ".edl":
        return parse_edl_text(source.read_text(encoding="utf-8"))
    raise FrameioImportRefused(
        f"the export {source.name!r} is not a comment export this "
        "import reads",
        "a Frame.io comment export ends in .csv, .fiojson or .edl",
        "export the comments in one of those shapes and pass that path")


# ── Mapping file time to timeline time ───────────────────────────────
#
# Through the delivery record, the way `ren revise --at` maps a chat
# note: `delivered_note_anchor.resolve` is the one mapping, so a
# Frame.io timestamp and a chat `--at` at the same file second land on
# the same frame. Anything that mapping refuses - no delivery, a time
# past the end, no recorded rate - is listed as unmapped, never placed
# by guess.


def _range_end_seconds(comment: Mapping, start: float, fps: float
                       ) -> float:
    """The range end in file seconds: explicit end, else duration, else
    the start (a point comment, one frame long on the timeline)."""
    explicit = to_seconds(comment.get("timestamp_end"), fps)
    if explicit is not None and explicit >= start:
        return explicit
    duration = to_seconds(comment.get("timestamp_duration"), fps)
    if duration is not None and duration > 0:
        return start + duration
    return start


def import_comments(project_folder: str, export_path: str, reel: str = ""
                    ) -> dict:
    """Ingest one export into the shared note store.

    Returns the import record: how many comments parsed, how many
    became anchored notes, how many general notes, and the unmapped
    list - each naming the comment id, its words and why it placed
    nowhere. The notes land in one pull file under the marker-feedback
    area, carrying the timeline frame, the delivery rate and start, the
    author, the comment id and the annotation - so `route_project`
    places them, `revise` translates them by note id, and the ledger
    folds a re-import onto the first import. With nothing placeable
    and nothing general, no pull file is written.
    """
    from library.tools import delivered_note_anchor as anchor
    from library.tools import marker_feedback
    from library.tools.project_layout import Area, ProjectLayout

    project = str(Path(project_folder).expanduser().resolve())
    comments = read_export(export_path)
    if not comments:
        raise FrameioImportRefused(
            f"the export {Path(export_path).name!r} carries no comments",
            "an import files what reviewers wrote",
            "export the comments with at least one worded comment")

    row = anchor.find_delivered(project, reel)
    timeline = str(row.get("timeline_name") or "")
    _fps, _start = anchor.delivery_timing(project, row)
    fps = float(_fps)

    anchored, general, unmapped = [], [], []
    for comment in comments:
        words = str(comment.get("text") or "").strip()
        start = (to_seconds(comment.get("timestamp"), fps)
                 if not _blank(comment.get("timestamp")) else None)
        if start is None:
            if _blank(comment.get("timestamp")):
                general.append((comment, words))
            else:
                unmapped.append({
                    "comment_id": str(comment.get("comment_id") or ""),
                    "text": words,
                    "timestamp": str(comment.get("timestamp")),
                    "reason": "the timestamp is not a file moment "
                              "(seconds, M:SS, H:M:SS or HH:MM:SS:FF), "
                              "so it maps to no timeline frame",
                })
            continue
        try:
            resolved = anchor.resolve(
                project, reel or str(row.get("reel") or ""), start)
        except anchor.AnchorRefused as exc:
            unmapped.append({
                "comment_id": str(comment.get("comment_id") or ""),
                "text": words,
                "timestamp": str(comment.get("timestamp")),
                "reason": exc.what,
            })
            continue
        end = _range_end_seconds(comment, start, fps)
        duration_frames = max(
            1, round((end - start) * resolved["timeline_fps"]))
        anchored.append((comment, words, resolved, duration_frames))

    if not anchored and not general:
        return {"format": IMPORT_FORMAT,
                "project": project,
                "export": str(export_path),
                "timeline": timeline,
                "parsed": len(comments),
                "anchored": 0,
                "general": 0,
                "unmapped": unmapped,
                "pull_path": None,
                "note_ids": []}

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    short = hashlib.sha256(
        f"{timeline}\n{Path(export_path).name}\n{stamp}".encode()
    ).hexdigest()[:10]
    notes, note_ids = [], []
    for comment, words, resolved, duration_frames in anchored:
        note_id = _comment_id(
            comment.get("comment_id"),
            [resolved["timeline_name"], words,
             f"{resolved['file_seconds']:.3f}"])
        note_ids.append(note_id)
        notes.append(_raw_note(
            comment, words, note_id, timeline=resolved["timeline_name"],
            frame=resolved["timeline_frame"],
            timecode=marker_feedback.frames_to_timecode(
                resolved["timeline_frame"], resolved["timeline_fps"]),
            frame_in_space=(resolved["timeline_frame"]
                            - resolved["timeline_start_frame"]),
            duration_frames=duration_frames,
            file_clock=resolved["file_clock"],
            timeline_seconds=resolved["timeline_seconds"],
            source_clip=resolved["source_clip"]))
    for comment, words in general:
        note_id = _comment_id(
            comment.get("comment_id"), [timeline, words, "general"])
        note_ids.append(note_id)
        notes.append(_raw_note(
            comment, words, note_id, timeline=timeline,
            frame=None, timecode=None, frame_in_space=None,
            duration_frames=1, file_clock="", timeline_seconds=None,
            source_clip=None,
            unplaced_reason="the Frame.io comment carries no timestamp, "
                            "so it is a general note on the reel rather "
                            "than a moment in it"))
    path = ProjectLayout(project).write_path(
        Area.MARKER_FEEDBACK,
        f"frameio.{stamp}.{short}{marker_feedback.PULL_FILE_SUFFIX}")
    payload = {
        "format": marker_feedback.PULL_FORMAT,
        "pulled_at": dt.datetime.now(dt.UTC).isoformat(),
        "resolve_project": f"frame.io import {Path(export_path).name}",
        "timeline": timeline,
        "timeline_start_frame": int(_start),
        "timeline_fps": fps,
        "note_count": len(notes),
        "notes": notes,
        "frameio_import": {
            "format": IMPORT_FORMAT,
            "source": Path(export_path).name,
            "reel": row.get("reel"),
            "unmapped": unmapped,
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return {"format": IMPORT_FORMAT,
            "project": project,
            "export": str(export_path),
            "timeline": timeline,
            "parsed": len(comments),
            "anchored": len(anchored),
            "general": len(general),
            "unmapped": unmapped,
            "pull_path": str(path),
            "note_ids": note_ids}


def _raw_note(comment: Mapping, words: str, note_id: str, *,
              timeline: str, frame: int | None, timecode: str | None,
              frame_in_space: int | None, duration_frames: int,
              file_clock: str, timeline_seconds: float | None,
              source_clip: dict | None,
              unplaced_reason: str = "") -> dict:
    """One stored note: the marker fields the router reads, plus the
    Frame.io record (author, comment id, annotation) in `custom_data`
    where no existing reader mistakes it for its own."""
    author = str(comment.get("author") or "").strip()
    name = (f"Frame.io @ {file_clock} ({author})" if file_clock and author
            else f"Frame.io @ {file_clock}" if file_clock
            else f"Frame.io note ({author})" if author
            else "Frame.io note")
    record = {
        "comment_id": str(comment.get("comment_id") or ""),
        "author": author,
        "file_clock": file_clock,
        "timeline_seconds": timeline_seconds,
        "annotation": comment.get("annotation") or "",
        "resolved": bool(comment.get("resolved")),
        "replies": comment.get("replies") or [],
        "source_clip": source_clip,
    }
    raw: dict = {
        "source": "frameio_comment",
        "name": name,
        "note": words,
        "text": words,
        "author": author,
        "frame": frame,
        "timecode": timecode,
        "frame_in_timeline_space": frame_in_space,
        "duration_frames": duration_frames,
        "custom_data": {"frameio": record},
        "custom_data_raw": json.dumps({"frameio": record}),
        "attachments": ([{"annotation": record["annotation"]}]
                        if record["annotation"] else []),
        "note_id": note_id,
        "read_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    if unplaced_reason:
        raw["unplaced_reason"] = unplaced_reason
    return raw


# ── Reporting ───────────────────────────────────────────────────────


def render(result: Mapping) -> str:
    """The printable import: what landed, and what placed nowhere."""
    lines = [f"── Frame.io import ({result.get('parsed', 0)} comment(s) "
             f"from {Path(str(result.get('export', ''))).name}) ──",
             f"  anchored: {result.get('anchored', 0)} "
             f"on {result.get('timeline', '') or '(no reel)'}",
             f"  general: {result.get('general', 0)} (no timestamp)"]
    unmapped = list(result.get("unmapped") or ())
    if unmapped:
        lines.append(f"  unmapped: {len(unmapped)} (listed, never guessed)")
        for entry in unmapped:
            identity = (entry.get("comment_id") or "(no id)")
            lines.append(f"      {identity}: "
                         f"\"{(entry.get('text') or '')[:60]}\"")
            lines.append(f"        {entry.get('reason', '')}")
    if result.get("pull_path"):
        lines.append(f"  written: {result['pull_path']}")
    for note_id in result.get("note_ids") or ():
        lines.append(f"      note {note_id}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren notes import",
        description="Ingest a Frame.io comment export (.csv, .fiojson, "
                    ".edl) into the shared note store.")
    parser.add_argument("project", help="project folder path")
    parser.add_argument("export", help="the exported comment file")
    parser.add_argument("--reel", default="",
                        help="the reel number or exact timeline name "
                             "the comments review")
    parser.add_argument("--json", action="store_true",
                        help="print the import record as JSON")
    args = parser.parse_args(argv)
    try:
        result = import_comments(args.project, args.export, reel=args.reel)
    except FrameioImportRefused as refused:
        print(refused.render(), file=sys.stderr)
        return refused.exit_code
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json
          else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
