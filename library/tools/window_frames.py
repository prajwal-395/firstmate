"""A frame of the window a cutaway will actually play, in the prompt.

At HEAD, **0 of the 12 LLM contexts contain an image part of any kind**.
Every step that chooses a picture chooses it from prose.  Coverage of
that prose is now good - `view:picture` reaches 6 of 12 steps and
describes 766.9 s of project 001's 807.0 s - but what it carries is
ACTION, not APPEARANCE: *"The vehicle is driving forward along a road
lined with trees"* says what happens and nothing about what it looks
like.  That is how the captain's marked cutaway - a close dashboard shot
panning up to nothing - could be selected and described accurately at
the same time.

The captain's ruling, 2026-08-29: *"fix the window rule and show the
model a frame of the window it will actually receive"*.  The window half
landed as `cutaway_window.py`; this module is the other half.

## What is shown, and why that is the window

`step_3_02_select_broll` does not name seconds.  It names a CLIP and a
`preferred_moment`, and `cutaway_window.choose_window` resolves that
moment to a window whose start is a candidate span's start and whose
length is the spine slot being covered.  So the windows the model can
RECEIVE are enumerable before it answers, and this module shows it one
picture of each.

An ANCHOR is a distinct `video_in` that `choose_window` can return for a
clip, computed across the slot lengths the spine really has.  It is
normally the span's own start; near the end of a clip `fit_to_clip`
pulls it earlier so a long slot still fits, which is why the anchor set
is computed rather than assumed to be the span list.  On project 001 -
17 clips, five distinct cutaway slot lengths - that is 100 anchors on
the vision documents the `001-pre-repass-20260829` snapshot froze.  The
count is a property of those documents, not a constant: re-run the
vision pass and it moves.

## Which frames of the window

The two ENDS are not a choice: they are the first and the last thing the
viewer sees of this cutaway, and a window is defined by them.  What is
chosen is how much of the middle travels with them, and that is a
RESOLUTION, not a preference - the same kind of number as
`picture_quality`'s 5 Hz sampling or `face_sample_dimensions`' short
side.  The rule is stated as one: **no more than
`SECONDS_UNSEEN_BETWEEN_SAMPLES` of the window passes between two
frames**, ends always included.  A 1.382 s slot gets 3 frames, a 4.186 s
slot gets 6.

A single frame would not have caught the defect this exists for.  A
dashboard shot that pans up to nothing looks correct in its first frame;
it is the last frame that says so.

A strip runs to the LONGEST slot that anchors there, so a shorter slot
plays a PREFIX of it.  One strip per (anchor, slot length) would be
exact and costs five times the strips on 001; the row carries
`video_in`, `strip_end` and `frames` instead and the header states the
prefix rule, which is a real cost - a reader has to divide - taken
deliberately.

## Nothing is filtered

Every candidate window of every clip gets a strip.  Whatever selects a
shortlist becomes the chooser (AGENTS.md §10.5), and the same reasoning
that ships all 78 sounds of the SFX catalogue ships all 100 strips here.
Ranking them - busiest, brightest, most faces - is exactly what
`cutaway_window.DECLINED_TO_RANK` refuses.

## The harness

A picture is not a document, so this is NOT a second copy of
`brief_reference`: there is no map to stand in for a body, no section to
inline, and above all **no fallback form**.  A harness that cannot be
shown a picture cannot be handed a smaller picture; it is handed nothing
and the prose path stands, which is the current behaviour.

`HARNESS_SHOWS_FRAMES` is that enumeration and it is complete - an
unknown harness raises, for the same reason
`brief_reference.HARNESS_READS_FILES` does.  The two are SEPARATE
capabilities on purpose: reading a text file and perceiving an image are
different things, and a harness could have the first without the second.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess


# ── Which harnesses can be shown a picture ────────────────────────────
#
# A COMPLETE enumeration.  An unknown name raises rather than being
# assumed either way, because a harness whose reach nobody has
# established is not a harness known to reach.
#
# - `agy`: the request is a file on disk answered by an AGENT with a
#   shell and its own file tools - "the agent IS the LLM"
#   (docs/RUN_001_END_TO_END.md §4).  Established on 2026-08-29 by
#   producing a strip with this module's own ffmpeg command and opening
#   it: the four tiles of clip_011 at 150.0-152.466 s read back as a
#   walking shot swinging past a storefront, which is appearance the
#   step's prose does not carry.
# - `api`: `llm_client.LLMClient.generate` takes ONE string and posts it.
#   There is no image part in that call and no tool loop on the other
#   side, so neither a path nor bytes reaches the model as a picture.
# - `mock`: replays a recorded answer.  No model runs, so nothing is
#   shown to anybody; listed as showing because a recorded answer is
#   unaffected either way, the same reading `HARNESS_READS_FILES` gives.
HARNESS_SHOWS_FRAMES = {
    "agy": True,
    "mock": True,
    "api": False,
}

WITHDRAWN_DELIVERIES = {
    "base64 in the context string": (
        "No harness this pipeline has decodes it. `api` posts the context "
        "as text, so a data: URI arrives as characters and is charged as "
        "characters; `agy` writes it into a JSON request file an agent "
        "reads. Both would carry ~55 KB per strip and show nobody a "
        "picture, which is a fake image part rather than an image part."
    ),
    "one frame per window": (
        "The defect this exists for is a shot that changes inside its own "
        "window - a close dashboard panning up to nothing. Its first "
        "frame is a good dashboard shot. One frame cannot disconfirm it."
    ),
    "a contact sheet per clip": (
        "One tile per window across a whole clip shows which windows "
        "differ from each other and never what happens INSIDE one, which "
        "is the half the prose already covers."
    ),
}


class UnknownHarness(ValueError):
    """A harness whose ability to be shown a picture is unestablished."""


def harness_shows_frames(harness: str) -> bool:
    """True when `harness` can be shown an image referenced by a prompt."""
    if harness not in HARNESS_SHOWS_FRAMES:
        raise UnknownHarness(
            f"harness {harness!r} is not in HARNESS_SHOWS_FRAMES "
            f"({sorted(HARNESS_SHOWS_FRAMES)}). Establish whether a model "
            f"answering through it can be shown a picture, and record the "
            f"answer there - a harness whose reach nobody has established "
            f"is not a harness known to reach."
        )
    return HARNESS_SHOWS_FRAMES[harness]


# ── The sampling rule ─────────────────────────────────────────────────

# How much of a window may pass between two frames of its strip.  A
# RESOLUTION, the same kind of number as the 5 Hz `picture_quality`
# samples at: it says how finely the strip can tell two moments apart,
# and it is stated wherever a strip is reported.  It is not a judgement
# about how much of a cutaway matters.
SECONDS_UNSEEN_BETWEEN_SAMPLES = 1.0

# A strip is one image and has to stay legible at a glance.  Both are
# mechanical: a bound on the picture, never on the edit.
MAX_FRAMES_PER_STRIP = 8
STRIP_FRAME_SHORT_SIDE = 360

# Beyond this many strips the extraction stops and SAYS which windows it
# did not draw.  It binds on a catalogue far larger than any project on
# disk (001 needs 100); whatever it drops is reported, never silently
# missing - the same shape `bridge.py`'s MAX_CANDIDATE_CLIPS takes.
MAX_STRIPS = 400

# ffmpeg is given a hard ceiling per strip so one unreadable file cannot
# wedge a run.  Mechanical.
FFMPEG_TIMEOUT_SECONDS = 60


def sample_times(video_in: float, video_out: float, source_fps: float) -> list:
    """The instants of `[video_in, video_out)` the strip shows.

    Both ends always, plus interior samples so that no more than
    `SECONDS_UNSEEN_BETWEEN_SAMPLES` passes between two of them.

    The last sample is one frame BEFORE `video_out`, because that is the
    last frame the viewer actually sees: a cutaway plays the half-open
    range and the frame at `video_out` belongs to whatever comes next.
    """
    if video_out <= video_in:
        return [round(max(0.0, video_in), 3)]
    fps = source_fps if source_fps and source_fps > 0 else 30.0
    last = max(video_in, video_out - 1.0 / fps)
    span = last - video_in
    if span <= 0:
        return [round(video_in, 3)]
    count = int(math.ceil(span / SECONDS_UNSEEN_BETWEEN_SAMPLES)) + 1
    count = max(2, min(count, MAX_FRAMES_PER_STRIP))
    step = span / (count - 1)
    return [round(video_in + i * step, 3) for i in range(count)]


# ── The anchors ───────────────────────────────────────────────────────

def window_anchors(clip_analysis: dict, temporal_index: dict,
                   clip_duration: float, slot_durations) -> list:
    """Every distinct `video_in` `choose_window` can return for a clip.

    Returns rows `{video_in, strip_end, slot_seconds, describes}`, where
    `strip_end` is the end of the LONGEST slot that anchors here - so the
    strip covers every window that starts at `video_in`, and a shorter
    slot plays a prefix of it.

    `slot_durations` are the lengths of the spine blocks a cutaway can
    cover.  They come from the spine rather than a constant, because the
    window's length is the slot's length and nothing else.
    """
    from library.tools.cutaway_window import candidate_windows

    anchors = {}
    for target in sorted({round(float(d), 3) for d in slot_durations if d}):
        for row in candidate_windows(
            "", clip_analysis, temporal_index, clip_duration, target
        ):
            entry = anchors.setdefault(row["video_in"], {
                "video_in": row["video_in"],
                "strip_end": row["video_out"],
                "slot_seconds": [],
                "describes": row["describes"],
            })
            entry["strip_end"] = max(entry["strip_end"], row["video_out"])
            entry["slot_seconds"].append(target)
            if not entry["describes"]:
                entry["describes"] = row["describes"]
    return [anchors[k] for k in sorted(anchors)]


# ── Drawing one ───────────────────────────────────────────────────────

def strip_filename(clip_id: str, video_in: float, times=None) -> str:
    """The strip's name, which IS its label AND its cache key.

    The window's start is in the filename so a reader never has to hold
    a mapping in their head, and so nothing has to draw text into the
    picture - which would need a font, and a font is a look decision
    arriving one level up (AGENTS.md §12).

    **The name also carries what was DRAWN**, because `draw_strip`
    reuses any file already at this path and the name used to record
    only `(clip_id, video_in)`.  Nothing in it said how many frames the
    strip has, where the window ends or at what scale it was sampled,
    while the table beside it recomputes `frames: len(times)` from
    current code.  On project 001 that shipped 83 of 94 rows declaring 5
    frames for a strip that has 6: the strips were drawn at 10:26 under
    an older sampling rule and the run that described them was at 12:36.
    Harmless only because the model trusted the picture over the table.
    Same class as #348/#350 - a cache invalidated by the footage and
    never by the code that wrote it.

    So the tag is the frame COUNT plus a digest of the exact instants
    and the sampling scale.  Any change to what is drawn is a different
    file, and a stale strip is never read.  `times=None` keeps the old
    name, for a caller that only wants to address an existing strip.
    """
    base = f"{clip_id}__{video_in:08.3f}"
    if times is None:
        return f"{base}.jpg"
    rule = json.dumps(
        {"times": [round(float(t), 3) for t in times],
         "short_side": STRIP_FRAME_SHORT_SIDE},
        sort_keys=True, separators=(",", ":"))
    tag = hashlib.sha256(rule.encode("utf-8")).hexdigest()[:6]
    return f"{base}__{len(times)}f{tag}.jpg"


def ffmpeg_command(source_file: str, times: list, out_path: str) -> list:
    """One ffmpeg call: N fast seeks into one file, hstacked into one JPEG.

    Fast seek (`-ss` BEFORE `-i`) per input, so the cost is a keyframe
    seek and a short decode rather than a linear pass.  Measured on 001's
    IMG_1816.MOV (188.6 s, 1920x1080 h264): 0.36 s wall clock for a
    four-frame strip at 150 s in.

    `-pix_fmt yuvj420p` is not decoration: the mjpeg encoder refuses a
    limited-range input outright ("Non full-range YUV is non-standard"),
    writes nothing, and exits 1.
    """
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error"]
    for t in times:
        cmd += ["-ss", f"{max(0.0, t):.3f}", "-i", source_file]
    chain = "".join(
        f"[{i}:v]scale=-2:{STRIP_FRAME_SHORT_SIDE}[s{i}];"
        for i in range(len(times))
    )
    if len(times) > 1:
        chain += "".join(f"[s{i}]" for i in range(len(times)))
        chain += f"hstack=inputs={len(times)}"
    else:
        chain += "[s0]null"
    cmd += ["-filter_complex", chain, "-frames:v", "1",
            "-pix_fmt", "yuvj420p", "-q:v", "4", out_path]
    return cmd


def draw_strip(source_file: str, times: list, out_path: str) -> bool:
    """Draw one strip, or report that it could not be drawn.

    A strip already on disk is reused: the same "already there?" check
    the per-clip index uses, so a re-run of the step costs nothing.
    """
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return True
    try:
        subprocess.run(
            ffmpeg_command(source_file, times, out_path),
            capture_output=True, encoding="utf-8",
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


# ── The block the step receives ───────────────────────────────────────

STRIP_LEGEND = {
    "clip_id": "the clip this window is cut from",
    "video_in": (
        "seconds into the clip where the window starts - the first frame "
        "of the cutaway"
    ),
    "strip_end": (
        "seconds into the clip where the STRIP ends. A slot shorter than "
        "`strip_end - video_in` plays a prefix of the strip"
    ),
    "frames": "how many frames of that window the strip shows, left to right",
    "file": "the strip's filename inside the directory named above",
}
"""What each column IS.  It never says what to conclude from one.

The same route `music_measurement.MEASUREMENT_LEGEND` and
`transition_carriers.CUTS_LEGEND` take, and for the same reason: step
3.02's `handoff.md` is frozen, so a table that reaches the prompt has to
carry its own definitions.
"""

_HEADERS = ("clip_id", "video_in", "strip_end", "frames", "file")


def build_block(directory: str, rows: list, missing: list = ()) -> str:
    """The text the step receives beside its prose.

    `rows` are `{clip_id, video_in, strip_end, frames, file}`.  Nothing
    is ranked, filtered or shortlisted; a window that could not be drawn
    is NAMED in `missing` rather than left out silently.
    """
    out = [
        "Every cutaway window you can choose has a REAL PICTURE on disk. "
        "These are frames of",
        "the footage itself, not descriptions of it - the prose elsewhere "
        "in this context says",
        "what HAPPENS in a clip and cannot say what it LOOKS like.",
        "",
        f"  FRAMES: {directory}",
        "",
        "You have a shell and your own file tools. OPEN the strip for any "
        "window you are",
        "considering before you decide it.",
        "",
        "A strip is ONE image: the frames of that window laid out left to "
        "right. The leftmost",
        "is the first frame the viewer sees and the rightmost is the last, "
        "with the middle",
        f"sampled so that no more than "
        f"{SECONDS_UNSEEN_BETWEEN_SAMPLES:g} s of the window passes "
        f"between two frames.",
        "That sampling is the strip's resolution: a shot can still change "
        "between two of them.",
        "",
        "A window is chosen by your own `preferred_moment`, which is "
        "matched against what the",
        "vision pass observed during each span. These are every window "
        "that matching can",
        "return - nothing here is ranked, filtered or recommended.",
        "",
    ]
    for column, meaning in STRIP_LEGEND.items():
        out.append(f"  {column}: {meaning}")
    out.append("")
    out.append(f"[{len(rows)}]{{{','.join(_HEADERS)}}}")
    for row in rows:
        out.append("\t".join(str(row.get(h, "")) for h in _HEADERS))
    if missing:
        out.append("")
        out.append(
            f"{len(missing)} window(s) have no strip and are listed so the "
            f"absence is not read as an absence of the window: "
            + ", ".join(missing)
        )
    return "\n".join(out) + "\n"


# ── Clause: a harness that cannot be shown a picture ──────────────────

FRAME_INPUTS = ("broll_window_frames",)
"""Every step input that carries a reference to frames drawn here.

`present_llm_step` walks this to WITHHOLD the block from a harness that
cannot be shown a picture.  Unlike `brief_reference.REFERENCED_INPUTS`
there is nothing to put back in its place: a picture has no smaller
textual form, so the step falls back to the prose it had before.
"""


def withheld_notice(harness: str) -> str:
    """What stands in the block's place, so the absence is not silent."""
    return (
        f"Frames of the candidate cutaway windows were drawn for this run "
        f"and are NOT shown here: the {harness!r} harness cannot be shown a "
        f"picture, so this step decides from the prose alone. This line is "
        f"the record of that, not a description of the frames.\n"
    )


def withhold_for_harness(inputs: dict, harness: str) -> tuple:
    """Drop every frame reference a harness cannot be shown.

    Returns `(inputs, withheld_keys)`.  `inputs` is copied only if
    something changed, the same contract
    `brief_reference.restore_for_harness` has.
    """
    if not harness or harness_shows_frames(harness):
        return inputs, []

    withheld = []
    for key in FRAME_INPUTS:
        value = inputs.get(key)
        if not isinstance(value, str) or not value:
            continue
        if not withheld:
            inputs = dict(inputs)
        inputs[key] = withheld_notice(harness)
        withheld.append(key)
    return inputs, withheld
