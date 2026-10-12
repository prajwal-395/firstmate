"""A chat note anchored to a delivered file's own clock, without Resolve.

`ren revise <p> --at 0:12 [--reel N] "<note>"` is the chat half of the
review loop: the editor watches the delivered file and types what they
saw at a file time. This module maps that file time onto the timeline
through the delivery record (`.deliver.json`) `reel_deliver` wrote, and
stores the note in the same note store Resolve markers use - one pull
file per note under the marker-feedback area - with a stable note id,
so markers and chat notes share one ledger (`feedback_ledger.py`).

The mapping is deliberately narrow: a delivered file starts at the
timeline's own start frame and plays straight through, so

    timeline_frame = timeline_start_frame + round(file_seconds * fps)

`timeline_fps` and `timeline_start_frame` come off the delivery record
itself. Anything older than those fields falls back to the project's
own pull files and then to the recorded shadow snapshot for the same
timeline - both are the project's own measurements of the same
timeline, never a guess. With no rate to map through, this refuses.

The same clock serves a PREVIEW (`ren preview`, T12): the round's
review page (`round_page`, T13) plays the reel's newest preview, which
also starts at the timeline's first frame, and its "note at this time"
control stores the note through `ren notes add <p> --file <preview>`.
A preview the reel has moved past is refused - its seconds belong to a
cut that is no longer on the timeline.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from library.tools.ren_refusal import RenRefusal


class AnchorRefused(RenRefusal):
    """The file time cannot become a timeline anchor; nothing was stored."""


WATCHED_DELIVERED = "delivered"
WATCHED_PREVIEW = "preview"
"""What kind of file the editor watched when they typed the note."""


# ── The file clock ────────────────────────────────────────────────


_AT_PART = r"(?:\d+(?:\.\d+)?)"


def parse_at(value: str) -> float:
    """File seconds for `--at 0:12`: `SS`, `M:SS[.f]` or `H:M:SS[.f]`.

    Refuses rather than guessing: a negative time, an empty string or
    anything that is not one of those three shapes is not a moment in
    the file.
    """
    text = (value or "").strip()
    if not text:
        raise AnchorRefused(
            "no file time was given",
            "a chat note anchors to a moment in the delivered file",
            "pass `--at 0:12` (seconds, M:SS or H:M:SS) with the note")
    plain = re.fullmatch(_AT_PART, text)
    if plain:
        seconds = float(text)
        if seconds < 0:
            raise AnchorRefused(
                f"file time {text!r} is before the file starts",
                "a chat note anchors to a moment in the delivered file",
                "pass a time at or after 0:00 with `--at`")
        return seconds
    parts = text.split(":")
    if len(parts) in (2, 3) and all(
            re.fullmatch(r"\d+(?:\.\d+)?", part) for part in parts):
        numbers = [float(part) for part in parts]
        if len(numbers) == 3:
            hours, minutes, seconds = numbers
        else:
            hours, minutes, seconds = 0.0, numbers[0], numbers[1]
        if minutes >= 60 or seconds >= 60 or hours < 0:
            raise AnchorRefused(
                f"file time {text!r} is not a clock reading",
                "a chat note anchors to a moment in the delivered file",
                "pass `--at M:SS` (or H:M:SS), e.g. `--at 0:12`")
        total = hours * 3600.0 + minutes * 60.0 + seconds
        if total < 0:
            raise AnchorRefused(
                f"file time {text!r} is before the file starts",
                "a chat note anchors to a moment in the delivered file",
                "pass a time at or after 0:00 with `--at`")
        return total
    raise AnchorRefused(
        f"file time {text!r} is not a moment in the file",
        "a chat note anchors to a moment in the delivered file",
        "pass `--at 0:12` (seconds, M:SS or H:M:SS) with the note")


def format_at(seconds: float) -> str:
    """`12.0` -> `0:12`; the name a stored chat note carries."""
    total = max(0.0, float(seconds))
    minutes, rest = divmod(total, 60.0)
    hours, minutes = divmod(minutes, 60.0)
    if hours:
        return f"{int(hours)}:{int(minutes):02d}:{rest:04.1f}"
    return f"{int(minutes)}:{rest:04.1f}"


# ── The delivered file ────────────────────────────────────────────


def _rows(project_folder: str) -> list:
    from library.tools import render_watch

    return render_watch.delivered_reels(project_folder)


def find_delivered(project_folder: str, reel: str = "") -> dict:
    """The delivery record for one reel: the row `render_watch` reads.

    `reel` is a reel number (`--reel 2`) or an exact timeline name.
    Empty means the project's only delivered reel; with more than one
    delivered and none named, this refuses and lists them rather than
    anchoring to the wrong file.
    """
    rows = [row for row in _rows(project_folder) if row.get("delivered")]
    if not rows:
        raise AnchorRefused(
            "no reel of this project has been delivered yet",
            "a chat note anchors to a moment in a delivered file",
            "run `ren deliver <project> <reel>` first, then revise the note")
    wanted = (reel or "").strip()
    if not wanted:
        if len(rows) > 1:
            known = sorted({str(row.get("reel")) for row in rows
                            if row.get("reel") is not None})
            raise AnchorRefused(
                f"{len(rows)} reels are delivered ({', '.join(known)})",
                "a chat note anchors to one delivered file",
                "pass `--reel N` naming the reel the note is about")
        return rows[0]
    try:
        number = int(wanted)
    except (TypeError, ValueError):
        number = None
    if number is not None:
        mine = [row for row in rows if row.get("reel") == number]
        if not mine:
            known = sorted({str(row.get("reel")) for row in rows
                            if row.get("reel") is not None})
            raise AnchorRefused(
                f"reel {number} has no delivered file in this project",
                "a chat note anchors to a moment in a delivered file "
                f"(delivered so far: {known or '(none)'})",
                f"run `ren deliver <project> {number}` first, then revise")
        return mine[0]
    mine = [row for row in rows if row.get("timeline_name") == wanted]
    if not mine:
        known = sorted({str(row.get("timeline_name")) for row in rows})
        raise AnchorRefused(
            f"no delivered file is named exactly {wanted!r}",
            "a reel is addressed by its exact timeline name",
            f"delivered timelines: {known or '(none)'}; "
            "pass `--reel` exactly as Resolve lists it")
    return mine[0]


def find_watched(project_folder: str, file: str) -> dict:
    """The record row for the exact file the editor watched.

    `file` is a delivered file or a preview (`ren preview`). A preview
    must still be its reel's newest one: an older preview shows a cut
    the timeline has moved past, so its seconds would land on the wrong
    frames and this refuses rather than anchoring there.
    """
    from library.tools import reel_preview

    path = Path(file).expanduser()
    if not path.is_absolute():
        path = Path(project_folder) / path
    path = path.resolve()
    for row in _rows(project_folder):
        video = row.get("video_path") or ""
        if video and Path(video).expanduser().resolve() == path:
            if not row.get("delivered"):
                break
            return {**row, "watched": WATCHED_DELIVERED}
    sidecar = reel_preview._sidecar(path)
    try:
        record = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AnchorRefused(
            f"{path} is neither a delivered reel nor a preview Ren made",
            "a note anchors to a moment in a file whose record maps it "
            "onto the timeline",
            "pass `--file` naming a preview (`ren preview`) or a delivered "
            "file (`ren deliver`), or leave it out to use the delivered "
            "file") from None
    timeline = str(record.get("timeline") or "")
    index = reel_preview.previews_dir(project_folder) / reel_preview.INDEX_FILE
    try:
        latest = (json.loads(index.read_text(encoding="utf-8"))
                  .get("latest") or {}).get(timeline) or {}
    except (OSError, ValueError):
        latest = {}
    newest = latest.get("path") or ""
    if not newest or Path(newest).expanduser().resolve() != path:
        raise AnchorRefused(
            f"{path.name} is not the newest preview of {timeline!r}",
            "the reel changed after this preview was made, so its seconds "
            "would land on a cut that is no longer on the timeline",
            "regenerate the review page (`ren round-page <project>`), "
            "watch the newest preview and note it again")
    return {"timeline_name": timeline, "reel": record.get("reel"),
            "video_path": str(path), "sidecar": str(sidecar),
            "delivered": False, "watched": WATCHED_PREVIEW}


def _sidecar_record(row: dict) -> dict:
    try:
        return json.loads(Path(row["sidecar"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return {}


def _recorded_clock(project_folder: str, timeline: str):
    """`(fps, start_frame)` the project itself recorded for a timeline.

    The pull files the project already holds for the same timeline come
    first, then the recorded shadow snapshot: both are the project's own
    measurements of the same timeline. None when neither carries a rate.
    """
    if not timeline:
        return None
    from library.tools import marker_feedback

    for _path, payload in reversed(
            marker_feedback.pulled_files(project_folder)):
        if payload.get("timeline") != timeline:
            continue
        try:
            pull_fps = float(payload.get("timeline_fps") or 0.0)
        except (TypeError, ValueError):
            pull_fps = 0.0
        if pull_fps > 0:
            try:
                pull_start = int(payload.get("timeline_start_frame") or 0)
            except (TypeError, ValueError):
                pull_start = 0
            return pull_fps, pull_start
    try:
        from library.tools import edit_spec, timeline_shadow

        project_name = edit_spec._resolve_project_name(project_folder)
        store = timeline_shadow.ShadowStore()
        timeline_id = store.resolve_timeline(project_name, timeline)
        snapshot = store.snapshot(store.head(project_name, timeline_id))
        shadow_fps = float(snapshot.get("fps") or 0.0)
        if shadow_fps > 0:
            return shadow_fps, int(snapshot.get("start_frame") or 0)
    except Exception:  # noqa: BLE001 - the caller refuses on None
        pass
    return None


def delivery_timing(project_folder: str, row: dict) -> tuple:
    """`(fps, start_frame)` for a watched reel file, off its own records.

    A delivered file's record (`.deliver.json`) is read first - it is
    the mapping this note is anchored through. A preview's record
    (`.preview.json`) carries the build's frame rate but not where the
    timeline's numbering starts, so its start comes from the project's
    own clock for the same timeline (`_recorded_clock`). With no rate or
    no start anywhere, this refuses rather than guessing one.
    """
    record = _sidecar_record(row)
    timeline = str(row.get("timeline_name") or "")
    if row.get("watched") == WATCHED_PREVIEW:
        try:
            fps_value = float((record.get("expected") or {}).get("fps") or 0)
        except (TypeError, ValueError):
            fps_value = 0.0
        clock = _recorded_clock(project_folder, timeline)
        start = record.get("timeline_start_frame")
        if fps_value > 0 and start is not None:
            return fps_value, int(start)
        if fps_value > 0 and clock is not None:
            return fps_value, int(clock[1])
        raise AnchorRefused(
            f"the preview of {timeline!r} cannot be mapped onto its "
            "timeline",
            "a preview second becomes a timeline frame through the "
            "build's frame rate and the timeline's start frame, and "
            + ("no start frame was recorded for it" if fps_value > 0
               else "its record carries no frame rate"),
            "render the preview again with `ren preview <project>`, or "
            "deliver the reel and note against the delivered file")
    fps = record.get("timeline_fps")
    start = record.get("timeline_start_frame")
    try:
        fps_value = float(fps) if fps is not None else 0.0
    except (TypeError, ValueError):
        fps_value = 0.0
    try:
        start_value = int(start) if start is not None else 0
    except (TypeError, ValueError):
        start_value = 0
    if fps_value > 0:
        return fps_value, start_value
    clock = _recorded_clock(project_folder, timeline)
    if clock is not None:
        return clock
    raise AnchorRefused(
        f"the delivery record for {row.get('timeline_name')!r} carries no "
        "timeline rate to map the file time through",
        "a file second becomes a timeline frame through the timeline's "
        "own frame rate, and none was recorded",
        "deliver the reel again so its `.deliver.json` records the rate, "
        "then revise the note")


def _delivered_seconds(row: dict) -> float:
    """How long the delivered file plays, when the record says."""
    record = _sidecar_record(row)
    for carrier in (record.get("timeline_seconds"),
                    ((record.get("verification") or {}).get("duration") or {})
                    .get("value"),
                    ((record.get("render") or {}).get("duration_seconds")),
                    # A preview's record: the reel's length it was
                    # checked against, then what was measured.
                    record.get("duration_seconds"),
                    (record.get("verification") or {}).get(
                        "measured_seconds")):
        try:
            value = float(carrier) if carrier is not None else 0.0
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _clips_at_frame(project_folder: str, timeline_name: str,
                    frame: int) -> tuple:
    """`(primary, all)` clips playing at one absolute timeline frame.

    Read off the reel's recorded shadow generation - the same picture
    `ren revise` patches against - so the note resolves to the source
    clip the timeline actually plays there. `primary` prefers the V1
    picture; empty with the reason when nothing was ever read.
    """
    try:
        from library.tools import edit_spec, timeline_shadow

        project_name = edit_spec._resolve_project_name(project_folder)
        store = timeline_shadow.ShadowStore()
        timeline_id = store.resolve_timeline(project_name, timeline_name)
        snapshot = store.snapshot(store.head(project_name, timeline_id))
    except Exception as exc:  # noqa: BLE001 - the anchor survives this
        return None, f"the reel has no recorded generation ({exc})"
    hits = []
    for track in snapshot.get("tracks") or []:
        row = (f"{str(track.get('type') or 'v')[0].upper()}"
               f"{int(track['index'])}")
        for position, clip in enumerate(track.get("clips") or []):
            try:
                start = int(clip["record_in"])
                end = int(clip["record_out"])
            except (KeyError, TypeError, ValueError):
                continue
            if start <= int(frame) < end:
                hits.append({
                    "row": row,
                    "position": position,
                    "unique_id": str(clip.get("unique_id") or ""),
                    "name": str(clip.get("name") or ""),
                    "record_in": start,
                    "record_out": end,
                })
    if not hits:
        return None, "no recorded clip plays that frame"
    primary = next((hit for hit in hits if hit["row"] == "V1"), hits[0])
    return primary, hits


def resolve(project_folder: str, reel: str, file_seconds: float, *,
            file: str = "") -> dict:
    """Anchor one file time: watched file -> timeline frame + clip.

    The watched file is the reel's delivered file, or the exact file
    `file` names (a delivered file or the reel's newest preview).
    """
    if file:
        row = find_watched(project_folder, file)
        wanted = (reel or "").strip()
        if wanted and wanted not in (str(row.get("reel")),
                                     row["timeline_name"]):
            raise AnchorRefused(
                f"{Path(file).name} is {row['timeline_name']!r}, not "
                f"reel {wanted!r}",
                "a note anchors to the reel whose file was watched",
                "pass the `--reel` the file belongs to, or leave it out")
    else:
        row = {**find_delivered(project_folder, reel),
               "watched": WATCHED_DELIVERED}
    fps, start_frame = delivery_timing(project_folder, row)
    duration = _delivered_seconds(row)
    if duration > 0 and file_seconds > duration + 1e-6:
        raise AnchorRefused(
            f"{format_at(file_seconds)} is past the end of "
            f"{row.get('timeline_name')!r} ({format_at(duration)} long)",
            "a chat note anchors to a moment in the delivered file",
            f"pass `--at` within 0:00..{format_at(duration)}")
    timeline_frame = int(start_frame) + round(float(file_seconds) * fps)
    timeline_seconds = round((timeline_frame - int(start_frame)) / fps, 3)
    timeline_name = str(row.get("timeline_name") or "")
    primary, clips = _clips_at_frame(
        project_folder, timeline_name, timeline_frame)
    return {
        "timeline_name": timeline_name,
        "reel": row.get("reel"),
        "video_path": row.get("video_path") or "",
        "sidecar": row.get("sidecar") or "",
        "watched": row.get("watched") or WATCHED_DELIVERED,
        "file_seconds": round(float(file_seconds), 3),
        "file_clock": format_at(file_seconds),
        "timeline_fps": fps,
        "timeline_start_frame": int(start_frame),
        "timeline_frame": timeline_frame,
        "timeline_seconds": timeline_seconds,
        "source_clip": primary,
        "clips_at_frame": clips if isinstance(clips, list) else [],
        "clips_reason": "" if isinstance(clips, list) else str(clips),
    }


# ── The shared note store ─────────────────────────────────────────


def anchored_identity(timeline: str, text: str, file_seconds: float) -> str:
    """The stable id for one anchored chat note: reel + words + moment.

    The same shape `feedback_ledger.durable_identity` takes - a readable
    reel stem, one colon, sixteen hex digest characters - so the ledger
    folds a re-asked note onto its first asking the way it folds a
    re-typed marker. The file second joins the digest (rounded to the
    millisecond) because the same words at two moments are two notes
    about two places; the same words at the same moment are one note
    asked again.
    """
    from library.tools.feedback_ledger import base_reel_name, normalise_text

    reel = base_reel_name(timeline)
    payload = (f"{reel}\n{normalise_text(text)}\n"
               f"{round(float(file_seconds), 3):.3f}").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in reel)
    return f"{stem or 'timeline'}:{digest}"


def write_anchored_note(project_folder: str, text: str,
                        anchor: dict) -> tuple:
    """Store one anchored chat note where markers already live.

    One pull file under the marker-feedback area, carrying the timeline
    frame the file time mapped to, the timeline's own rate and start,
    and the stable note id - so `route_project` places it, `revise`
    translates it by its edit id, and `feedback_ledger` folds a re-ask
    of the same words onto the same entry. Returns
    `(edit_link_id, pull_path)`.
    """
    from library.tools import marker_feedback, marker_routing
    from library.tools.project_layout import Area, ProjectLayout

    words = (text or "").strip()
    if not words:
        raise AnchorRefused(
            "no note was given",
            "a revision starts from what the editor wants changed",
            "pass the note in words with `--at 0:12`")
    timeline = str(anchor["timeline_name"] or "")
    frame = int(anchor["timeline_frame"])
    fps = float(anchor["timeline_fps"])
    start_frame = int(anchor["timeline_start_frame"])
    note_id = anchored_identity(timeline, words, anchor["file_seconds"])
    raw = {
        "source": "timeline_marker",
        "name": f"Chat note @ {anchor['file_clock']}",
        "note": words,
        "text": words,
        "frame": frame,
        "timecode": marker_feedback.frames_to_timecode(frame, fps),
        "frame_in_timeline_space": frame - start_frame,
        "duration_frames": 1,
        "custom_data": {"chat_time_anchor": {
            "file_seconds": anchor["file_seconds"],
            "file_clock": anchor["file_clock"],
            "timeline_seconds": anchor["timeline_seconds"],
            "video_path": anchor["video_path"],
            "sidecar": anchor["sidecar"],
            "watched": anchor.get("watched") or WATCHED_DELIVERED,
            "source_clip": anchor["source_clip"],
        }},
        "custom_data_raw": json.dumps({"chat_time_anchor": {
            "file_seconds": anchor["file_seconds"],
            "file_clock": anchor["file_clock"]}}),
        "attachments": [],
        "note_id": note_id,
        "read_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    short = hashlib.sha256(
        f"{timeline}\n{words}\n{anchor['file_seconds']:.3f}".encode("utf-8")
    ).hexdigest()[:10]
    path = ProjectLayout(project_folder).write_path(
        Area.MARKER_FEEDBACK, f"chat-at.{stamp}.{short}"
        f"{marker_feedback.PULL_FILE_SUFFIX}")
    payload = {
        "format": marker_feedback.PULL_FORMAT,
        "pulled_at": dt.datetime.now(dt.UTC).isoformat(),
        "resolve_project": "ren revise --at",
        "timeline": timeline,
        "timeline_start_frame": start_frame,
        "timeline_fps": fps,
        "note_count": 1,
        "notes": [raw],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return marker_routing.edit_link_id(raw, timeline, str(path)), str(path)


def add_note(project_folder: str, text: str, *, at: str, reel: str = "",
             file: str = "") -> dict:
    """Store one note at a watched-file time, without revising it yet.

    `ren notes add` - the write behind the review page's "note at this
    time" control. The note lands in the same store `ren revise --at`
    writes, so `ren notes <p>` lists it and `ren revise <p> --note-id
    <id>` turns it into a change. Returns the anchor plus the `note_id`
    revise takes and the pull file it was written to.
    """
    anchor = resolve(project_folder, reel, parse_at(at), file=file)
    note_id, pull_path = write_anchored_note(project_folder, text, anchor)
    return {**anchor, "note_id": note_id, "note": text.strip(),
            "pull_file": pull_path}
