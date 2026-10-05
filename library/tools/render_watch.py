"""Frames of the FINISHED picture, in front of a model.

This module draws a strip of the RENDERED FILE and hands a model the strip
plus a list of questions a still can answer.  Every structural verifier
judges the plan or the timeline; this is the one surface that judges the
composite, where picture defects (text too big, graphics off the frame, a
card on the wrong row, a shot of nothing) actually live.  `window_frames`
shows the SOURCE window a step is choosing; this shows the OUTPUT that
choosing produced.

The current contract
--------------------
**Watching requires a render, and this module never makes one.**  On the
`edit_video` path step 6.01 already rendered the file 6.02 judges, so the
watch runs on that render.  On the `reels` path a reel becomes a file only
when the captain runs `deliver-reel`, so `manage_project.py watch-reel`
reads the file that verb already wrote.  This module imports no render
path.

**Nothing here is a DAG node and nothing here runs on a build.**  The
cheap structural checks (`verify_reels`, `reel_conformance_verifier`) run
always; the watch is the expensive check, on request.  No `build-reels`,
no `run`, no DAG and no operation reaches it;
`library/processes/reels/dag.json` stays two nodes
(`tests/contracts/test_reel_deliver_is_explicit.py`).  The cost is the model reading
the strips, not the decode (one ffmpeg call per strip).

**Report, not gate.**  The watch does not fail a build or a render: every
defect it can see is a judgement, and a judgement that blocks a build is
the pipeline inventing taste (AGENTS.md 10.4, 10.5).

**One thing IS hard, and it is mechanical.**  A watch that was ASKED FOR
and drew no picture must not read as a watch that passed.
`NothingWasWatched` is that refusal, and it is about the instrument, never
about the answer.

**What a still can be asked is enumerated, and so is what it cannot.**
`WATCH_QUESTIONS` is the enumeration and `NOT_ANSWERABLE_FROM_STILLS` its
boundary - most recorded defects are where speech is cut, which is not in
any frame.

The audit that found no step had seen the picture, the measured strip
cost, and the accounting of which recorded interventions a watcher would
have caught: docs/evidence/render_watch.md and
`docs/WATCHING_THE_BUILT_REEL.md`.  `tests/unit/resolve/test_render_qa.py`.
"""

from __future__ import annotations

import json
import math
import os
import subprocess

from library.tools import window_frames as wf
from library.tools.ren_refusal import RenRefusal


class NothingWasWatched(RenRefusal):
    """A watch was asked for and no picture reached anybody.

    Raised by `assert_watched`.  It is about the INSTRUMENT: the file
    could not be decoded, ffmpeg is not on the path, or every strip
    failed.  It is never raised because of what a model answered.
    """


class NoPictureChanged(RenRefusal):
    """A scoped watch was asked for and the touch changed no picture."""


# ── The sampling rule ─────────────────────────────────────────────────
#
# Not a second number.  `window_frames.SECONDS_UNSEEN_BETWEEN_SAMPLES`
# is already this repository's law for how finely a strip may tell two
# moments apart, and a finished cut is sampled by the same law as a
# candidate window - one rule, one place to change it, one number to
# state in a prompt.

#: How much of the file one strip covers.  DERIVED: the sampling
#: resolution times the frames one image may carry.  Nothing chooses it.
STRIP_SECONDS = (wf.MAX_FRAMES_PER_STRIP
                 * wf.SECONDS_UNSEEN_BETWEEN_SAMPLES)

#: Beyond this many strips the watch stops and SAYS which spans it did
#: not draw.  A reel is tens of seconds; this binds at ~32 minutes of
#: video, far past anything the reels path produces, and whatever it
#: drops is named.  Same shape as `window_frames.MAX_STRIPS`.
MAX_WATCH_STRIPS = 240


def strip_spans(duration_seconds: float) -> list:
    """The `(start, end)` spans of the file the strips cover.

    Contiguous, in order, covering the WHOLE file: nothing is ranked,
    filtered or shortlisted, because whatever selects a shortlist
    becomes the chooser (AGENTS.md 10.5).  A watcher shown the
    "interesting" seconds is a watcher whose finding is the shortlist's.
    """
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        return []
    count = int(math.ceil(duration / STRIP_SECONDS))
    count = max(1, min(count, MAX_WATCH_STRIPS))
    spans = []
    for i in range(count):
        start = i * STRIP_SECONDS
        spans.append((round(start, 3), round(min(duration,
                                                 start + STRIP_SECONDS), 3)))
    return spans


def strips_over(ranges, duration_seconds: float) -> list:
    """The strips of `strip_spans` that overlap any `(start, end)` range.

    What a touch's dirty spans become (`dirty_regions`).  The SAME
    strips a whole watch draws, so a scoped watch is a subset of the
    whole one, frame for frame, and never a second sampling grid.  A
    range is a statement of what CHANGED, not a shortlist of what is
    interesting: the rest of the file was watched before the touch and
    has not changed since.
    """
    ranges = [(float(start), float(end)) for start, end in ranges]
    return [(start, end) for start, end in strip_spans(duration_seconds)
            if any(lo < end and start < hi for lo, hi in ranges)]


def file_label(video_path: str) -> str:
    """The label a file's strips are drawn under: its stem AND its bytes.

    `deliver-reel` writes a reel to the same name every time, and a
    strip already on disk is reused (`window_frames.draw_strip`). Keyed
    by the stem alone, a reel re-delivered after a touch was shown the
    PREVIOUS render's strips. Size and modification time change on
    every render, so a new file is a new label.
    """
    import hashlib
    stat = os.stat(video_path)
    digest = hashlib.sha1(
        f"{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:8]
    return f"{os.path.splitext(os.path.basename(video_path))[0]}__{digest}"


def strip_filename(label: str, start: float, times) -> str:
    """The strip's name, which IS its label AND its cache key.

    `window_frames.strip_filename`'s reasoning, unchanged and for the
    same defect: the name carries WHAT WAS DRAWN - the frame count, the
    exact instants and the sampling scale - so a strip drawn under an
    older rule is a different file and is never read as this one.
    """
    return wf.strip_filename(f"{label}__watch", start, times)


def probe_duration(video_path: str) -> float:
    """The file's own measured duration, or 0.0.

    Read off the file rather than taken from the plan: the watch is of
    what was RENDERED, and a strip planned past the end of the file is
    a strip of nothing.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, encoding="utf-8", check=False,
            timeout=wf.FFMPEG_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    try:
        return float((out.stdout or "").strip())
    except (TypeError, ValueError):
        return 0.0


def probe_fps(video_path: str) -> float:
    """The file's frame rate, or 0.0 - `sample_times` needs it to put
    the last sample one frame before the span's end."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of",
             "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, encoding="utf-8", check=False,
            timeout=wf.FFMPEG_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    raw = (out.stdout or "").strip()
    if "/" in raw:
        num, _, den = raw.partition("/")
        try:
            return float(num) / float(den) if float(den) else 0.0
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def draw_watch_strips(video_path: str, directory: str,
                      duration_seconds: float = 0.0,
                      fps: float = 0.0, label: str = "",
                      ranges=None) -> dict:
    """Draw a strip per span of the rendered file.

    Returns `{directory, rows, missing, duration, fps}`.  A span that
    could not be drawn is NAMED in `missing` rather than left out: an
    unseen second is a second the watch cannot judge, and the watcher
    must know which ones those are.

    Drawing uses `window_frames`' own ffmpeg command and its own reuse
    rule - one extractor, not a second one.  `ranges`, when given, are
    the only seconds drawn (`strips_over`); None draws the whole file.
    """
    duration = float(duration_seconds or 0.0) or probe_duration(video_path)
    rate = float(fps or 0.0) or probe_fps(video_path)
    name = label or file_label(video_path)
    os.makedirs(directory, exist_ok=True)

    # THE LAST FRAME OF A FILE CANNOT BE FAST-SEEKED TO.
    #
    # `sample_times` puts its last sample one frame before the span's
    # end, which for a cutaway is exactly right: the frame at `video_out`
    # belongs to whatever plays next. A FILE has no next, so its last
    # span ends at the file's own end and that sample lands on the final
    # presentation timestamp - and `-ss` at exactly that instant decodes
    # nothing. Measured 2026-09-13 on a 4.000 s 30 fps h264 file: -ss
    # 3.967 produced no frame and exited 0, -ss 3.950 produced one. One
    # more frame of backoff, 33 ms, is inside the stated sampling
    # resolution and is the difference between a watch and no watch.
    tail = (1.0 / rate) if rate and rate > 0 else 1.0 / 30.0
    last_sampleable = max(0.0, duration - tail)

    rows, missing = [], []
    spans = (strip_spans(duration) if ranges is None
             else strips_over(ranges, duration))
    for start, end in spans:
        times = wf.sample_times(start, min(end, last_sampleable), rate)
        filename = strip_filename(name, start, times)
        out_path = os.path.join(directory, filename)
        if not wf.draw_strip(video_path, times, out_path):
            missing.append(f"{start:.3f}-{end:.3f}s")
            continue
        rows.append({
            "span_start": start,
            "span_end": end,
            "frames": len(times),
            "file": filename,
        })
    if ranges is None and duration > 0 and len(spans) == MAX_WATCH_STRIPS \
            and spans[-1][1] < duration - 1e-6:
        missing.append(
            f"{spans[-1][1]:.3f}-{duration:.3f}s (past the "
            f"{MAX_WATCH_STRIPS}-strip bound)")
    return {
        "directory": directory,
        "rows": rows,
        "missing": missing,
        "duration": round(duration, 3),
        "fps": rate,
    }


def assert_watched(drawn: dict, subject: str) -> None:
    """A watch that was asked for and drew nothing REFUSES.

    Mechanical, never a judgement.  The alternative is a watch record
    that says nothing was wrong because nothing was looked at, which is
    the same defect as the handoff that claimed to be watching a table.
    """
    if not isinstance(drawn, dict) or not drawn.get("rows"):
        raise NothingWasWatched(
            f"a watch of {subject} was asked for and NO STRIP WAS DRAWN",
            f"{len(list((drawn or {}).get('missing') or []))} span(s) "
            f"reported undrawable, measured duration "
            f"{(drawn or {}).get('duration', 0.0)!r}. Nothing saw this "
            f"picture, so nothing may report on it - this is the "
            f"instrument failing, not a verdict",
            "check that ffmpeg is on the path and that the file has a "
            "decodable video stream, then watch again")


# ── What a still can be asked ─────────────────────────────────────────

WATCH_QUESTIONS = {
    "text_in_frame": (
        "Is every word of on-screen text fully inside the frame - no "
        "letter clipped by an edge, no line running off the side, "
        "nothing sitting half outside the picture?"
    ),
    "text_size": (
        "Read at the size a phone shows this: is any caption or title so "
        "large it dominates the frame or covers the speaker, or so small "
        "it cannot be read?"
    ),
    "graphic_intact": (
        "Is every graphic drawn whole and drawn once - not cut off, not "
        "stretched to the wrong shape, not doubled, and not overlapping "
        "another graphic or a caption so that either becomes unreadable?"
    ),
    "subject_framed": (
        "Is the person's face inside the frame, not cut by an edge and "
        "not covered by an overlay?"
    ),
    "picture_legible": (
        "Is the picture in focus and exposed - or is it blurred, "
        "smeared, crushed or washed out in a way a viewer would read as "
        "a mistake rather than as an effect?"
    ),
    "picture_says_something": (
        "Does this frame show a recognisable subject, or is it a shot of "
        "nothing - a wall, a floor, a smear, an empty road?"
    ),
    "frame_filled": (
        "Does the picture fill the frame, or is there a black bar, a "
        "pillarbox, or a visible edge of the source inside it?"
    ),
    "within_strip": (
        "Across the frames of one strip, does anything appear, vanish, "
        "jump or slide inside a single continuous shot - a caption that "
        "moves, a graphic that shifts, a crop that changes?"
    ),
}
"""The questions a FRAME can answer that a structural verifier cannot.

Each one is CHECKABLE - a reader can say yes or no about a specific
picture and point at where.  *"Would I post this?"* is the captain's own
bar and is deliberately NOT in this list: it is the right bar for the
captain and an unanswerable one for a step, because it asks for a whole
judgement about a product rather than an observation about a frame.
What the list does is cover the defects the captain has actually had to
type onto a timeline, which is the honest version of the same demand.

No question here states a preference about the answer, a threshold, or
a value (AGENTS.md 10.5).  "Is any caption so large it covers the
speaker" names a defect; it does not name a size.
"""

NOT_ANSWERABLE_FROM_STILLS = {
    "where speech is cut": (
        "A jump cut mid-sentence, a repeated line, a stumble left in, a "
        "tail of dead air. These are in the WORDS and the SOUND. Six of "
        "the six recorded corrections are this, and no frame carries it. "
        "`reel_build.redundant_takes` owns the decision."
    ),
    "anything audible": (
        "Levels, clipping, a bed that swamps speech, audio cut off at an "
        "ending. `render_qa` measures these from the file and this "
        "watcher does not duplicate it."
    ),
    "picture against sound at one instant": (
        "Whether a card lands while someone is still talking, whether a "
        "caption is on the word being said. The strip has no audio and "
        "the samples are seconds apart."
    ),
    "anything shorter than the sampling resolution": (
        f"At {wf.SECONDS_UNSEEN_BETWEEN_SAMPLES:g} s between samples, a "
        f"10-frame animation head, a 7-frame caption drift and a 4-frame "
        f"card are simply not sampled. The block states the resolution "
        f"for exactly this reason."
    ),
    "motion between two samples": (
        "Whether a move is smooth, whether a ramp ramps, whether a "
        "transition reads. A strip is stills; it can show that a clip IS "
        "blurred and not that a blur was supposed to be ramping."
    ),
    "what another reel has and this one does not": (
        "One watch is of one file. An animation applied to seven reels "
        "and missing from the eighth is a COMPARISON, and nothing here "
        "makes one."
    ),
}
"""The boundary, written down.

A question list that says only what it covers teaches a reader to trust
it for everything - the same reason `motion_graphics_vocabulary` raises
on an entry carrying no `never`.
"""


# ── The block a model receives ────────────────────────────────────────

WATCH_LEGEND = {
    "span_start": "seconds into the rendered file where this strip starts",
    "span_end": "seconds into the rendered file where this strip ends",
    "frames": "how many frames of that span the strip shows, left to right",
    "file": "the strip's filename inside the directory named above",
    "framing": (
        "the framing the clip playing over this span DECLARES - 'letterbox' "
        "means bars above/below are the project's stated preference and not "
        "a defect; 'fill' means the frame is asked to be covered and bars "
        "are a defect"
    ),
}

_HEADERS = ("span_start", "span_end", "frames", "file", "framing")


def framing_label(intent) -> str:
    """A human-readable label for a declared framing intent.

    ``0.0`` letterboxes (bars are the project's stated preference), ``1.0``
    fills (the frame is asked to be covered), and a value between punches
    in partway - its bars are narrower rather than gone, and they are still
    declared.  ``None`` means the manifest declared nothing for the clip
    covering a span, which is reported as an empty label rather than read
    as a preference nobody made.
    """
    if intent is None:
        return ""
    value = float(intent)
    if value <= 0.0:
        return "letterbox"
    if value >= 1.0:
        return "fill"
    return "partial"


def framing_labels_for_rows(assembly_manifest: dict, rows: list) -> list:
    """The declared framing label per watch strip, read off the manifest.

    V1 first and V2 second, because the later span wins an overlap and V2
    is the track that covers V1 - the same order `_framing_spans` in step
    6.02 uses, so a V2 cutaway over a V1 clip is labelled with the V2
    clip's declaration.  A span no clip covers gets an empty label, which
    the handoff reads as "nothing declared" rather than as a preference.
    """
    from library.tools.render_qa import FramingSpan
    project_settings = assembly_manifest.get("project", {})
    fps = project_settings.get("frame_rate", 30.0) or 30.0
    spans = []
    for track in ("V1", "V2"):
        for clip in (assembly_manifest.get("tracks", {})
                      .get(track, {}).get("clips", [])):
            declared = clip.get("framing_intent")
            if declared is None:
                continue
            start_frame = clip.get("timeline_in_frame")
            end_frame = clip.get("timeline_out_frame")
            if (start_frame is None or end_frame is None
                    or end_frame <= start_frame):
                continue
            spans.append(FramingSpan(float(start_frame) / fps,
                                     float(end_frame) / fps,
                                     float(declared)))
    labels = []
    for row in rows:
        start = float(row.get("span_start") or 0.0)
        found = None
        for span in spans:
            if span.start - 1e-6 <= start < span.end:
                found = span.intent
        labels.append(framing_label(found))
    return labels


def build_watch_block(directory: str, rows: list, missing=(),
                      subject: str = "this render",
                      duration: float = 0.0, not_rewatched=(),
                      framing=()) -> str:
    """The text the watching step receives beside its prose.

    Carries the pictures, what each row IS, the sampling resolution, the
    questions, and the boundary - in that order.  It states no
    threshold and no preferred answer.
    """
    out = [
        f"You are shown the FINISHED PICTURE of {subject}. These are "
        f"frames of the rendered",
        "file itself - everything drawn over everything else, exactly as "
        "a viewer sees it.",
        "Nothing else in this context is a picture; the rest describes "
        "what was PLANNED.",
        "",
        f"  FRAMES: {directory}",
        "",
        "You have a shell and your own file tools. OPEN THE STRIPS. A "
        "verdict written",
        "without opening them is a verdict about the table, and that is "
        "the defect this",
        "block exists to end.",
        "",
        "A strip is ONE image: consecutive frames of one span laid out "
        "left to right, in",
        f"time order, sampled so that no more than "
        f"{wf.SECONDS_UNSEEN_BETWEEN_SAMPLES:g} s passes between two of "
        f"them.",
        "That sampling is the strip's RESOLUTION, and it is the limit of "
        "what you can say:",
        "anything shorter than it was never sampled and you must not "
        "report on it.",
        "",
    ]
    if duration and not_rewatched:
        out += [(f"The file is {float(duration):.3f} s long. A touch "
                 f"changed only the spans the strips below cover;"),
                ("the rest was watched before the touch, has not changed, "
                 "and is NOT shown: " + ", ".join(not_rewatched) + "."),
                "Judge only what is shown.", ""]
    elif duration:
        out += [f"The file is {float(duration):.3f} s long and the strips "
                f"below cover it end to end.", ""]
    for column, meaning in WATCH_LEGEND.items():
        out.append(f"  {column}: {meaning}")
    out.append("")
    out.append(f"[{len(rows)}]{{{','.join(_HEADERS)}}}")
    for index, row in enumerate(rows):
        if index < len(framing):
            row["framing"] = framing[index]
        out.append("\t".join(str(row.get(h, "")) for h in _HEADERS))
    if missing:
        out.append("")
        out.append(
            f"{len(list(missing))} span(s) have NO STRIP and are listed so "
            f"the absence is not read as an absence of defects - say so "
            f"rather than judging them: " + ", ".join(missing))
    out += [
        "",
        "ANSWER THESE, per strip, naming the span and where in the frame:",
        "",
    ]
    for key, question in WATCH_QUESTIONS.items():
        out.append(f"  {key}: {question}")
    out += [
        "",
        "These are the questions a picture can answer that a structural "
        "check cannot.",
        "Report what you SEE and where. Do not report on:",
        "",
    ]
    for key, why in NOT_ANSWERABLE_FROM_STILLS.items():
        out.append(f"  {key} - {why}")
    out += [
        "",
        "A clean strip is a real answer. Say a span is clean rather than "
        "finding something",
        "to say about it.",
    ]
    return "\n".join(out) + "\n"


def withheld_notice(harness: str, subject: str = "this render") -> str:
    """What stands in the block's place for a harness with no eyes.

    `window_frames`' rule, unchanged: a picture has no smaller textual
    form, so there is nothing to substitute.  What is substituted is the
    RECORD that there was a picture and this harness could not be shown
    it - which is what stops the step claiming to have watched.
    """
    return (
        f"Frames of {subject} were drawn for this run and are NOT shown "
        f"here: the {harness!r} harness cannot be shown a picture. NOTHING "
        f"HAS WATCHED THIS RENDER. Judge from the measurements alone and "
        f"say in your answer that the picture was not seen. This line is "
        f"the record of that, not a description of the frames.\n"
    )


# ── The record a watch leaves ─────────────────────────────────────────

def watch_record(video_path: str, drawn: dict, subject: str,
                 answer=None) -> dict:
    """What a watch leaves behind, answered or not.

    `watched` is about the INSTRUMENT and `answer` is the judgement.
    They are separate keys because "nobody looked" and "somebody looked
    and saw nothing wrong" are different facts, and a record that
    collapsed them would be the table-as-eyes defect again.
    """
    return {
        "video_path": video_path,
        "subject": subject,
        "watched": bool(drawn.get("rows")),
        "strips": len(drawn.get("rows") or []),
        "spans_not_drawn": list(drawn.get("missing") or []),
        "frames_directory": drawn.get("directory", ""),
        "duration": drawn.get("duration", 0.0),
        "seconds_unseen_between_samples":
            wf.SECONDS_UNSEEN_BETWEEN_SAMPLES,
        "questions_asked": sorted(WATCH_QUESTIONS),
        "not_answerable_from_stills": sorted(NOT_ANSWERABLE_FROM_STILLS),
        "answer": answer,
        "gates": False,
    }


def write_record(path: str, record: dict) -> str:
    """The record, on disk beside what it watched."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".",
                exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, default=str)
    return path


# ── Watching a delivered reel ─────────────────────────────────────────
#
# The reels path reaches a picture through `deliver-reel`'s output and
# through nothing else.  NOTHING BELOW RENDERS: it reads a file the
# captain already asked for, and `library/tools/reel_deliver.py` is not
# imported here at all, so no import of this module can start a render.
# `tests/test_render_watch.py` asserts that by reading the source.

DELIVER_SIDECAR_SUFFIX = ".deliver.json"


def delivered_reels(project_folder: str) -> list:
    """Every delivered reel this project has on disk, newest first.

    Read off the sidecars `deliver_reel` wrote - its own record of what
    it rendered and where - rather than by matching filenames. A
    delivery that happened is the only evidence a file is a reel's
    render; a `.mp4` in `exports/` could be anything.

    Rows: `{reel, timeline_name, video_path, sidecar, expected_seconds}`.
    """
    from library.tools.project_layout import Area, ProjectLayout

    exports = ProjectLayout(project_folder).write_dir(Area.EXPORTS)
    rows = []
    for sidecar in sorted(exports.glob("*" + DELIVER_SIDECAR_SUFFIX)):
        try:
            record = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        path = ((record.get("render") or {}).get("output_path")
                or (record.get("verification") or {}).get("output_path")
                or "")
        if not path:
            continue
        rows.append({
            "reel": record.get("reel"),
            "timeline_name": record.get("timeline_name", ""),
            "video_path": path,
            "sidecar": str(sidecar),
            "delivered": bool(record.get("delivered")),
        })
    rows.sort(key=lambda r: os.path.getmtime(r["sidecar"]), reverse=True)
    return rows


class NotDelivered(RenRefusal):
    """There is no rendered file of this reel to watch.

    Not a defect and not a refusal to work: it is the constraint this
    capability inherits.  A reel becomes a file only when the captain
    runs `deliver-reel`, so watching one is downstream of an act they
    have to take.
    """


def delivered_reel(project_folder: str, reel) -> dict:
    """The delivered file for one reel number, or a refusal saying why.

    The most recent delivery wins when a reel has been rendered more
    than once: the watch is of what is on disk NOW, and the newest
    sidecar is what `deliver-reel` last wrote.
    """
    rows = delivered_reels(project_folder)
    try:
        number = int(reel)
    except (TypeError, ValueError):
        raise NotDelivered(
            f"watch-reel takes a reel number, got {reel!r}",
            "a reel is addressed by its number",
            "pass the reel number, e.g. `ren watch <project> 3`") from None
    mine = [r for r in rows if r["reel"] == number]
    if not mine:
        known = sorted({r["reel"] for r in rows if r["reel"] is not None})
        raise NotDelivered(
            f"reel {number} has no rendered file in this project's exports/",
            "watching is of the PICTURE, and a reel becomes a picture "
            "only when the captain renders it. "
            f"Delivered so far: {known or '(none)'}",
            f"run `ren deliver <project> {number}` first, then watch")
    row = mine[0]
    if not os.path.isfile(row["video_path"]):
        raise NotDelivered(
            f"reel {number} was delivered to {row['video_path']} and that "
            f"file is not there now",
            f"the record is {row['sidecar']}",
            "deliver it again, or point --video at the file you want "
            "watched")
    return row


def _not_covered(rows: list, duration: float) -> list:
    """The seconds of the file no strip covers, as `"a-b s"` labels."""
    out, cursor = [], 0.0
    for row in sorted(rows, key=lambda r: r["span_start"]):
        if row["span_start"] > cursor + 1e-3:
            out.append(f"{cursor:.3f}-{row['span_start']:.3f}s")
        cursor = max(cursor, row["span_end"])
    if duration > cursor + 1e-3:
        out.append(f"{cursor:.3f}-{duration:.3f}s")
    return out


def watch_video(video_path: str, frames_dir: str, record_path: str,
                subject: str, dirty_receipts=None) -> dict:
    """Draw the strips for one rendered file and leave the record.

    Returns `{record, block}`.  Raises `NothingWasWatched` when the
    instrument produced no picture - a watch that was asked for and saw
    nothing must not read as a watch that found nothing.

    `dirty_receipts` are touch receipts (`dirty_regions`): only the
    seconds their PICTURE spans cover are drawn, and the rest is named
    in the record's `not_rewatched`.  A receipt with no dirty block, or
    a file rendered before the touch, draws the whole file.  A touch
    that changed no picture REFUSES (`NoPictureChanged`).
    """
    from library.tools import dirty_regions as dr

    dirty = dr.scope_for(video_path, dirty_receipts)
    ranges, scope = None, "whole file"
    if dirty is not None and dirty["whole_reel"]:
        scope = f"whole file: {dirty['whole_reel_reason']}"
    elif dirty is not None:
        ranges = [(start, end) for start, end, _, _ in
                  dr.spans_for(dirty, [dr.PICTURE])]
        if not ranges:
            raise NoPictureChanged(
                f"the touch changed no picture of {subject} "
                f"({dr.describe(dirty)})",
                "a watch is of the PICTURE, and every frame is the one "
                "already watched",
                "hear the reel instead (`ren hear`) for an audio touch, "
                "or watch without --dirty-receipt to look again anyway")
        scope = f"scoped to the touch: {dr.describe(dirty)}"
    drawn = draw_watch_strips(video_path, frames_dir, ranges=ranges)
    assert_watched(drawn, subject)
    not_rewatched = ([] if ranges is None
                     else _not_covered(drawn["rows"], drawn["duration"]))
    block = build_watch_block(drawn["directory"], drawn["rows"],
                              drawn["missing"], subject=subject,
                              duration=drawn["duration"],
                              not_rewatched=not_rewatched)
    record = watch_record(video_path, drawn, subject)
    record["scope"] = scope
    record["not_rewatched"] = not_rewatched
    record["block_path"] = os.path.join(
        os.path.dirname(record_path),
        os.path.splitext(os.path.basename(record_path))[0] + ".txt")
    with open(record["block_path"], "w", encoding="utf-8") as handle:
        handle.write(block)
    write_record(record_path, record)
    return {"record": record, "block": block, "record_path": record_path}


def watch_paths(project_folder: str, video_path: str) -> tuple:
    """Where one file's strips and record live: beside the file itself.

    `exports/` is where the deliverable is, and a watch of it belongs
    there rather than inside a step's output tree - no step produced it.
    """
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(project_folder)
    stem = os.path.splitext(os.path.basename(video_path))[0]
    frames = layout.write_path(Area.EXPORTS, "watch", stem, ".keep").parent
    record = layout.write_path(Area.EXPORTS, stem + ".watch.json")
    return str(frames), str(record)


def record_answer(record_path: str, answer) -> dict:
    """File a model's answer onto an existing watch record.

    Kept separate from `watch_video` because looking and judging are two
    acts by two parties: this module draws and asks, and whatever
    answered writes back here.  `gates` stays False - the answer is
    reported into the build record and refuses nothing.
    """
    with open(record_path, encoding="utf-8") as handle:
        record = json.load(handle)
    if not record.get("watched"):
        raise NothingWasWatched(
            f"{record_path} records a watch that drew no picture",
            "there is nothing an answer could be about",
            "watch the reel again so the record carries a picture, then "
            "answer")
    record["answer"] = answer
    write_record(record_path, record)
    return record
