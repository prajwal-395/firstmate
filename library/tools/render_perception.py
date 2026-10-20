"""What a viewer of the DELIVERED file perceives, measured on that file.

A structurally valid timeline is not a finished video.  The plan can
declare a cut, a level, a caption and a beat, and the conform can put
something else on the screen: a cut nobody wrote, a level that jumps
between two adjacent sections, a caption drawn at a different size from
the one before it, a word cut in half by a seam.  None of that is visible
in the manifest, because the manifest is what the pipeline INTENDED, and
these checks are about what the pipeline DELIVERED.

Every check here is a MEASUREMENT on the rendered file.  A finding is
either a measurable technical failure - a number, against a stated
bound - or it is an aesthetic concern, and this module produces only the
first kind.  The second kind belongs to a model or a human and reaches
the report through `creative_qa`'s subjective half, which is a different
road with a different reader.

**One substrate, seven readings.**  The substrate is the render's own
structure measured against the plan's declared structure:

    MEASURED (the render)          DECLARED (the plan)
      cut points                     seams   (cut | split | transition)
      window levels                  sections
      caption ink                    cards

`detect_cut_points` finds the places the delivered picture actually
changes; `declared_seams` finds the places the plan said it would.  The
comparison between them is what every reading below is made of:

  * a measured cut that matches NO declared seam is a cut the plan never
    wrote (`cut_motivation`, QA-009);
  * a measured cut that lands inside a spoken word is a word the seam
    damaged (`dialogue_integrity`, QA-010);
  * a declared SPLIT - two clips cut contiguously from one source - must
    be invisible, and the frames either side of it say whether it is
    (`visual_continuity`, QA-011);
  * the delivered level of adjacent sections says whether the mix is
    consistent (`section_levels`, QA-012);
  * the delivered ink of the caption cards says whether they are alike
    (`caption_consistency`, QA-013);
  * a declared seam the render does not show is a piece of the plan that
    is not in the deliverable (`narrative_coverage`, QA-014);
  * a declared seam the render shows SOMEWHERE ELSE is a pacing that is
    not the pacing the brief asked for (`brief_pacing`, QA-015).

**A seam has a KIND, and the kind decides what the render must show.**
A `cut` must appear as a visible change of picture.  A `split` - the
same source, cut in two - must NOT appear as one, because the join is
meant to be invisible.  A `transition` is a declared blend and is
`render_qa.measure_transition_render`'s business.  Treating all three
as one thing is how a split gets reported as a missing cut and a cut gets
reported as a broken split, so the kind is read here and never guessed.

**Nothing here decides what a level or a pace SHOULD be.**  The bounds
are MECHANICAL defaults - a ratio, a decibel step, a fraction of a
section - and they are named in this file so a reader knows exactly what
judged what.  Every one of these checks REPORTS; promoting one to a
gate is the boolean at the top of its own function, exactly as it is for
every other report-only measurement in `render_qa`.  A gate that fails a
correct render is not coverage.

**What is already measured elsewhere, and is not measured again.**
`render_qa.measure_frame_occupancy` already judges the picture's
geometry against the declared framing and holds one geometry per
declared framing, so an UNEXPECTED FRAMING CHANGE is that check's
business and this file does not repeat it.  `render_qa.measure_lufs`
judges the master as a whole, so the level of a SECTION is not that
check's business either.  `subtitle_qa.verify_subtitle_timing` judges
caption TIMING against the plan, so this file does not re-time a card;
it asks whether the cards are drawn alike.  `reel_hearing` judges the
render's WORDS against the plan's, so this file does not transcribe the
deliverable.


The rules for this module
-----------------------
These are the engine's rules for this file, and they live HERE rather
than in AGENTS.md, which keeps the headline and points at the module
that owns the detail.

**A gate that FAILS correct output is no more coverage than one that cannot fail.**
Every check here reports, and the boolean that would promote it is named
beside it.

**A declared output has a READER.**
Every metric this module can emit has a row in
`library/tools/qa_findings.FINDING_READERS`; the contract test
`tests/contracts/test_qa_findings_reach_a_reader.py` harvests the metric
names out of this file and fails on one with no reader.

**No assessment field reports a default as though it were measured.**
A check that could not measure says so in its own detail, and a caller
that established nothing passes none rather than an empty list - an empty
list says "measured, and there is none".

**A MEASURABLE finding and a SUBJECTIVE concern are different kinds of
finding.**
Everything this module produces is a measurement, and `qa_findings` says
so by classifying it.  A judgement about taste is not a smaller kind of
failure; it is a different KIND, and a gate may not act on it.
"""

from __future__ import annotations

import math
import os
import re
import statistics
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from library.tools.render_qa import RenderQAResult

# ── Bounds ────────────────────────────────────────────────────────────
#
# Every number here is a MECHANICAL default: a ratio, a step, a
# fraction.  None of them is a taste, and each is named so a reader can
# see what judged what.  A bound that is really a taste belongs in a
# declaration the project makes, not here.

#: The scene score above which a frame change is a CUT.  ffmpeg's own
#: `scene` value runs 0..1; 0.35 is above the fast-pan noise a
#: hand-held shot produces and well below the near-1.0 of a hard cut
#: between two different shots.
SCENE_SCORE_THRESHOLD = 0.35

#: How close a change of picture must be to a declared seam to count as
#: that seam's STRUCTURE.  A change further away than this is a change
#: the plan never wrote; a declared seam with no change this close is
#: not in the deliverable at all; and a change this close that is not
#: exactly on the seam is a seam that DRIFTED, which is the pacing
#: reading's business.
STRUCTURE_WINDOW_SECONDS = 1.0

#: How much larger than the shot's own motion a seam's jump may be
#: before the join counts as visible.  Three times the local motion is
#: a jump; anything less is the footage moving.
CONTINUITY_JUMP_RATIO = 3.0

#: The floor under that ratio, in mean absolute luma levels on a 0-255
#: scale.  Below this a difference is not visible at all, so the ratio
#: cannot fire on a still frame that happens to differ by a unit.
CONTINUITY_MIN_JUMP = 6.0

#: A single frame this much brighter (or darker) than both of its
#: neighbours is a FLASH, which no plan asks for.
FLASH_JUMP_LEVELS = 40.0

#: How far the delivered level of two adjacent sections may differ before
#: the mix reads as inconsistent.  Six decibels is a doubling of
#: perceived loudness - the step the ear hears as a change of level
#: rather than as the next section.
SECTION_LEVEL_JUMP_DB = 6.0

#: How far the delivered height of a caption card may differ from the
#: reel's own norm before the cards count as drawn inconsistently, as a
#: fraction of the norm.
CAPTION_SIZE_TOLERANCE = 0.15

#: How far the delivered picture may drift from a declared seam, as a
#: fraction of the section it closes, before the pacing counts as
#: something other than the plan's.
PACING_DRIFT_RATIO = 0.10

#: The floor under the pacing drift, in seconds: a drift smaller than
#: this is the detector's own timing.
PACING_DRIFT_FLOOR_SECONDS = 0.25

# ── The gates ─────────────────────────────────────────────────────────
#
# Every check in this file REPORTS.  Promoting one to a gate is the
# boolean below, and nothing else - the same ruling every report-only
# measurement in `render_qa` carries.

CUT_MOTIVATION_GATES = False
DIALOGUE_INTEGRITY_GATES = False
VISUAL_CONTINUITY_GATES = False
FLASH_FRAMES_GATES = False
SECTION_LEVELS_GATES = False
CAPTION_CONSISTENCY_GATES = False
NARRATIVE_COVERAGE_GATES = False
BRIEF_PACING_GATES = False

#: The metric names this module can emit.  One enumeration, so the
#: reader table and this file cannot drift apart.
METRICS = (
    "cut_motivation",
    "dialogue_integrity",
    "visual_continuity",
    "flash_frames",
    "section_levels",
    "caption_consistency",
    "narrative_coverage",
    "brief_pacing",
)

# ── The plan's declarations, read once ───────────────────────────────

#: A seam the plan declared, and what the render must show at it.
SEAM_CUT = "cut"
SEAM_SPLIT = "split"
SEAM_TRANSITION = "transition"


@dataclass(frozen=True)
class Seam:
    """One place the plan said the timeline changes."""

    at: float
    """Timeline second of the change."""

    kind: str
    """`cut`, `split` or `transition` - see the module docstring."""

    label: str = ""
    """What produced it, for the finding's prose."""


@dataclass(frozen=True)
class Section:
    """One stretch of the delivered timeline, as the plan describes it."""

    start: float
    end: float
    label: str = ""
    quiet: bool = False
    """The plan declared this section silent, so a level step at its
    edge is the plan's and not the mix's."""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class Word:
    """One spoken word, in TIMELINE seconds."""

    text: str
    start: float
    end: float


@dataclass(frozen=True)
class CaptionCard:
    """One caption segment, as the plan placed it."""

    overlay_path: str
    start: float
    end: float
    label: str = ""


@dataclass(frozen=True)
class CutPoint:
    """One place the DELIVERED picture actually changes."""

    time: float
    score: float = 0.0


@dataclass(frozen=True)
class LevelReading:
    """The delivered level of one window, in dBFS RMS."""

    start: float
    end: float
    rms_db: float


@dataclass(frozen=True)
class PerceptionPlan:
    """What the plan declared, in the form these checks read it."""

    seams: Tuple[Seam, ...] = ()
    sections: Tuple[Section, ...] = ()
    words: Tuple[Word, ...] = ()
    captions: Tuple[CaptionCard, ...] = ()
    frame_rate: float = 0.0
    declared_end: float = 0.0
    """The last second the plan's own timeline covers."""

    @property
    def declared(self) -> bool:
        """Whether the plan declared enough to be measured against.

        A plan with no seams cannot answer "is this cut motivated", and
        a plan with no sections cannot answer "is this level
        consistent".
        """
        return bool(self.seams or self.sections)


def declared_seams(assembly_manifest: Optional[dict]) -> List[Seam]:
    """Every place the manifest's own timeline changes.

    Read off the manifest's picture tracks: the boundary between two
    clips on V1 or V2 is a change of picture, and a native transition is
    a change the plan drew deliberately.  Two clips cut CONTIGUOUSLY from
    one source are a `split` - the same shot in two pieces - and the
    render must NOT show a change there, because the join was meant to
    be invisible.

    Returns `[]` for a manifest with no picture tracks: that says the
    manifest declared no seam, which is a different claim from a
    manifest nobody asked.
    """
    if not isinstance(assembly_manifest, dict):
        return []
    tracks = assembly_manifest.get("tracks")
    if not isinstance(tracks, dict):
        return []

    seams: List[Seam] = []
    for track_name in ("V1", "V2"):
        track = tracks.get(track_name)
        if not isinstance(track, dict):
            continue
        clips = [c for c in (track.get("clips") or []) if isinstance(c, dict)]
        clips.sort(key=lambda c: _number(c.get("timeline_in"))
                   if _number(c.get("timeline_in")) is not None else 0.0)
        for index, clip in enumerate(clips):
            if index == 0:
                continue
            previous = clips[index - 1]
            # The boundary BETWEEN two clips is where the first one ends
            # and the second one begins - the previous clip's out point,
            # which is the same second as this clip's in point.
            at = _number(previous.get("timeline_out"))
            if at is None:
                at = _number(clip.get("timeline_in"))
            if at is None:
                continue
            kind = SEAM_CUT
            label = (f"{track_name} {previous.get('label') or '?'} -> "
                     f"{clip.get('label') or '?'}")
            if _is_contiguous_split(previous, clip):
                kind = SEAM_SPLIT
                label = (f"{track_name} split of "
                         f"{os.path.basename(str(clip.get('source_file') or ''))}")
            seams.append(Seam(at=float(at), kind=kind, label=label))

    for transition in assembly_manifest.get("native_transitions") or []:
        if not isinstance(transition, dict):
            continue
        cut = _number(transition.get("cut_point_timeline"))
        if cut is None:
            continue
        seams.append(Seam(at=float(cut), kind=SEAM_TRANSITION,
                           label=str(transition.get("transition_type")
                                     or "transition")))

    seams.sort(key=lambda s: s.at)
    return _dedupe_seams(seams)


def _dedupe_seams(seams: Sequence[Seam], epsilon: float = 1e-3) -> List[Seam]:
    """One seam per place, the strongest kind winning.

    A native transition declared at a clip boundary and that boundary
    are the same place on the timeline.  `transition` beats `cut`
    because the blend is the plan's more specific statement, and `split`
    beats `cut` because the join is meant to be invisible.
    """
    rank = {SEAM_SPLIT: 2, SEAM_TRANSITION: 1, SEAM_CUT: 0}
    kept: List[Seam] = []
    for seam in sorted(seams, key=lambda s: (s.at, -rank.get(s.kind, 0))):
        if kept and abs(seam.at - kept[-1].at) <= epsilon:
            if rank.get(seam.kind, 0) > rank.get(kept[-1].kind, 0):
                kept[-1] = seam
            continue
        kept.append(seam)
    return kept


def _is_contiguous_split(previous: dict, clip: dict) -> bool:
    """True when two clips are one shot cut in two.

    Same source file and contiguous source ranges: the second clip
    starts exactly where the first stopped.  The join is meant to be
    invisible, so the render must show no change at it.
    """
    source_a = os.path.basename(str(previous.get("source_file") or ""))
    source_b = os.path.basename(str(clip.get("source_file") or ""))
    if not source_a or source_a != source_b:
        return False
    out = _number(previous.get("source_out"))
    start = _number(clip.get("source_in"))
    if out is None or start is None:
        return False
    return abs(out - start) <= 0.05


def declared_sections(assembly_manifest: Optional[dict]) -> List[Section]:
    """The stretches of the timeline the plan describes, in order.

    The spine blocks are the plan's own structure, and each one declares
    what the bed does under it - so a block the plan marked `silent`
    is a level step the plan made, and the level check must not read it
    as a mixing mistake.
    """
    if not isinstance(assembly_manifest, dict):
        return []
    sections: List[Section] = []
    for index, block in enumerate(assembly_manifest.get("_spine_blocks") or []):
        if not isinstance(block, dict):
            continue
        start = _number(block.get("timeline_start"))
        end = _number(block.get("timeline_end"))
        if start is None or end is None or end <= start:
            continue
        behaviour = str(block.get("music_behavior") or "")
        sections.append(Section(
            start=float(start), end=float(end),
            label=str(block.get("type") or f"block {index}"),
            quiet=(behaviour == "silent"),
        ))
    sections.sort(key=lambda s: s.start)
    return sections


def declared_words(assembly_manifest: Optional[dict]) -> List[Word]:
    """Every spoken word the plan placed, in TIMELINE seconds.

    The spine carries each word's timing in the SOURCE domain and the
    block's own offset into the timeline, so the conversion is
    `spine_contract.source_to_timeline` and not a guess: there is no
    project-wide offset, and a word's position without its block is not
    a position at all.
    """
    from library.tools.spine_contract import source_to_timeline

    if not isinstance(assembly_manifest, dict):
        return []
    words: List[Word] = []
    for block in assembly_manifest.get("_spine_blocks") or []:
        if not isinstance(block, dict):
            continue
        timestamps = block.get("word_timestamps") or []
        if not timestamps:
            continue
        source_start = _number(block.get("source_start"))
        timeline_start = _number(block.get("timeline_start"))
        if source_start is None or timeline_start is None:
            continue
        for stamp in timestamps:
            if not isinstance(stamp, dict):
                continue
            word_start = _number(stamp.get("source_start"))
            word_end = _number(stamp.get("source_end"))
            if word_start is None or word_end is None:
                continue
            words.append(Word(
                text=str(stamp.get("word") or ""),
                start=source_to_timeline(word_start, block),
                end=source_to_timeline(word_end, block),
            ))
    words.sort(key=lambda w: w.start)
    return words


def declared_captions(assembly_manifest: Optional[dict]) -> List[CaptionCard]:
    """The caption segments the manifest places, in timeline order."""
    if not isinstance(assembly_manifest, dict):
        return []
    track = assembly_manifest.get("subtitle_overlay")
    if not isinstance(track, dict):
        return []
    cards: List[CaptionCard] = []
    for segment in track.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        path = segment.get("overlay_path")
        start = _number(segment.get("timeline_start"))
        end = _number(segment.get("timeline_end"))
        if not path or start is None or end is None:
            continue
        cards.append(CaptionCard(overlay_path=str(path), start=float(start),
                                  end=float(end),
                                  label=str(segment.get("label") or "")))
    cards.sort(key=lambda c: c.start)
    return cards


def perception_plan(assembly_manifest: Optional[dict]) -> PerceptionPlan:
    """Everything the manifest declares, read into one plan."""
    if not isinstance(assembly_manifest, dict):
        return PerceptionPlan()
    project = assembly_manifest.get("project") or {}
    frame_rate = (_number(project.get("frame_rate"))
                  if isinstance(project, dict) else None)
    sections = declared_sections(assembly_manifest)
    return PerceptionPlan(
        seams=tuple(declared_seams(assembly_manifest)),
        sections=tuple(sections),
        words=tuple(declared_words(assembly_manifest)),
        captions=tuple(declared_captions(assembly_manifest)),
        frame_rate=float(frame_rate or 0.0),
        declared_end=max((s.end for s in sections), default=0.0),
    )


# ── The render, measured ─────────────────────────────────────────────

_METADATA_TIME = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
_METADATA_SCORE = re.compile(r"lavfi\.scene_score=([0-9]*\.?[0-9]+)")


def detect_cut_points(video_path: str,
                      threshold: float = SCENE_SCORE_THRESHOLD,
                      timeout: int = 600) -> List[CutPoint]:
    """Every place the DELIVERED picture changes, measured.

    One pass of ffmpeg's own `scene` score over the master: the score
    is how different this frame is from the one before it, and a hard
    cut between two shots scores near 1.0 while a hand-held move scores
    well below the threshold.  Both the time and the score come off the
    same pass.

    A file ffmpeg cannot read yields an empty list.  An empty list is
    NOT "there are no cuts": a caller that established nothing says so
    by passing `None`, and this function's own result is never evidence
    that a master has no cuts in it.
    """
    if not video_path or not os.path.exists(video_path):
        return []
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", video_path,
        "-filter:v", f"select='gt(scene,{threshold})',metadata=print",
        "-an", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                                errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    output = result.stderr or ""
    cuts: List[CutPoint] = []
    pending: Optional[float] = None
    for line in output.splitlines():
        match = _METADATA_TIME.search(line)
        if match:
            pending = float(match.group(1))
            continue
        score_match = _METADATA_SCORE.search(line)
        if score_match and pending is not None:
            cuts.append(CutPoint(time=pending,
                                 score=float(score_match.group(1))))
            pending = None
    if pending is not None:
        # The score line is absent on some builds; the cut is still a
        # cut, and a missing score is not a missing cut.
        cuts.append(CutPoint(time=pending, score=0.0))
    cuts.sort(key=lambda c: c.time)
    return cuts


def file_duration(video_path: str) -> Optional[float]:
    """The delivered file's own length in seconds, or None.

    Read off the file rather than taken from the plan, because the
    question "is the deliverable as long as the plan said it would be"
    is a question about the DELIVERABLE.
    """
    if not video_path or not os.path.exists(video_path):
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return float((result.stdout or "").strip())
    except ValueError:
        return None


def measure_window_levels(video_path: str,
                          windows: Sequence[Tuple[float, float]]
                          ) -> List[LevelReading]:
    """The delivered level of each window, in dBFS RMS.

    One decode of the master, and every window read off the same pass:
    ffmpeg's `astats` reports the RMS of every audio frame it is given
    and `ametadata` prints it against that frame's own timestamp.  A
    window's level is the median of the frames inside it, because one
    transient should not decide what a section sounds like.
    """
    if not video_path or not os.path.exists(video_path):
        return []
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", video_path,
        "-af", ("astats=metadata=1:reset=1,"
                "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"),
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                                errors="replace", timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []

    samples: List[Tuple[float, float]] = []
    at: Optional[float] = None
    for line in (result.stdout or "").splitlines():
        if line.startswith("frame:"):
            marker = line.split("pts_time:", 1)
            if len(marker) < 2:
                at = None
                continue
            try:
                at = float(marker[1].split()[0])
            except (ValueError, IndexError):
                at = None
            continue
        if line.startswith("lavfi.astats.Overall.RMS_level=") and at is not None:
            try:
                samples.append((at, float(line.rsplit("=", 1)[1])))
            except ValueError:
                continue
    if not samples:
        return []

    readings: List[LevelReading] = []
    for start, end in windows:
        inside = [value for when, value in samples if start <= when < end]
        if not inside:
            continue
        readings.append(LevelReading(start=float(start), end=float(end),
                                     rms_db=float(statistics.median(inside))))
    return readings


def measure_seam_jump(video_path: str, at: float, frame_rate: float,
                      radius_seconds: float = 0.5
                      ) -> Optional[Tuple[float, float, float]]:
    """How far the picture jumps at a seam, and how it moves beside it.

    Returns `(jump, local_motion, jump_at)`: the largest difference
    between consecutive frames in the window around the seam, the MEDIAN
    difference between consecutive frames in that same window, and the
    second at which the largest one happened.

    The median is the shot's OWN motion, and it is what makes the
    largest one readable: a difference of six levels in a shot that
    moves six levels per frame is the footage moving, not a broken join.
    The window is centred on the seam, so a discontinuity that is NOT at
    the seam is not this seam's discontinuity - which is why the caller
    also asks where it happened.

    `None` when the window yields no frames, which is a measurement that
    could not be taken and is reported as such.
    """
    import numpy as np

    from library.tools.render_qa import _stream_raw_frames

    probe_width, probe_height = 160, 90
    fps = frame_rate if frame_rate and frame_rate > 0 else 25.0
    start = max(0.0, at - radius_seconds)
    span = radius_seconds * 2.0
    frames = []
    for frame in _stream_raw_frames(
            video_path, "gray", 1, probe_width, probe_height, fps,
            scaled=True, start_seconds=start, duration_seconds=span):
        frames.append(np.asarray(frame[0], dtype=np.float32))
    if len(frames) < 3:
        return None

    differences: List[float] = []
    for index in range(1, len(frames)):
        differences.append(float(np.mean(np.abs(
            frames[index] - frames[index - 1]))))
    if len(differences) < 2:
        return None
    # The window's LEADING edge is not a measurement: the first frame
    # after a seek is not the frame the previous one came from, and the
    # difference between them says nothing about the seam.
    differences = differences[1:]
    largest = max(range(len(differences)),
                  key=lambda index: differences[index])
    return (differences[largest],
            float(statistics.median(differences)),
            start + (largest + 2) / fps)


def measure_flashes(video_path: str, frame_rate: float,
                    sample_fps: Optional[float] = None) -> List[float]:
    """Every frame that flashes - far brighter or darker than both neighbours.

    A flash is one frame, not a shot: the frame before it and the frame
    after it are close to each other and it is not close to either.  No
    plan asks for one, and it is the one visual defect a viewer cannot
    mistake for a cut.

    The frames are sampled at the file's own rate when the caller
    declares one, because a flash lasts a single frame and a coarser
    grid steps over it.
    """
    import numpy as np

    from library.tools.render_qa import _stream_raw_frames

    fps = sample_fps
    if fps is None:
        fps = min(frame_rate, 30.0) if frame_rate else 30.0
    fps = max(fps, 8.0)
    means: List[float] = []
    for frame in _stream_raw_frames(video_path, "gray", 1, 160, 90,
                                    fps, scaled=True):
        means.append(float(np.mean(frame[0])))
    if len(means) < 3:
        return []
    flashes: List[float] = []
    for index in range(1, len(means) - 1):
        before, here, after = means[index - 1], means[index], means[index + 1]
        if ((here - before) > FLASH_JUMP_LEVELS
                and (here - after) > FLASH_JUMP_LEVELS):
            flashes.append(index / fps)
        elif ((before - here) > FLASH_JUMP_LEVELS
                and (after - here) > FLASH_JUMP_LEVELS):
            flashes.append(index / fps)
    return flashes


# ── Matching the measured to the declared ────────────────────────────


def match_cuts_to_seams(cuts: Sequence[CutPoint],
                         seams: Sequence[Seam],
                         tolerance: float = STRUCTURE_WINDOW_SECONDS
                         ) -> Tuple[List[Tuple[CutPoint, Seam]],
                                    List[CutPoint],
                                    List[Seam]]:
    """Pair the render's cuts with the plan's seams.

    Returns `(pairs, unmatched_cuts, unmatched_seams)`.  A cut and a
    seam pair when they sit within `tolerance` of each other; the
    nearest seam wins, and a seam is used once.
    """
    pairs: List[Tuple[CutPoint, Seam]] = []
    taken: set = set()
    for cut in sorted(cuts, key=lambda c: c.time):
        best: Optional[Tuple[float, int]] = None
        for index, seam in enumerate(seams):
            if index in taken:
                continue
            distance = abs(seam.at - cut.time)
            if distance > tolerance:
                continue
            if best is None or distance < best[0]:
                best = (distance, index)
        if best is None:
            continue
        taken.add(best[1])
        pairs.append((cut, seams[best[1]]))
    matched_cuts = {id(cut) for cut, _ in pairs}
    matched_seams = {id(seam) for _, seam in pairs}
    return (pairs,
            [c for c in cuts if id(c) not in matched_cuts],
            [s for s in seams if id(s) not in matched_seams])


# ── The readings ─────────────────────────────────────────────────────
#
# Every reading here is REPORT-ONLY, and it reports in the shape
# `qa_findings` reads: `passed` stays True whatever was measured, and
# the miss is carried by the severity and the value.  A report that
# arrives as a failure would be a gate, and a gate here would fail
# correct output on a threshold nobody measured.  Promoting one is the
# boolean at the top of its own function, and nothing else.


def _verdict(metric: str, found: bool, detail: str, value: Any,
             threshold: Any, gate: bool) -> RenderQAResult:
    """One reading, in the shape the reader classifies.

    `found` says what was measured.  With the gate off the reading
    PASSES and says what it found in its severity, which is what makes
    it an advisory finding rather than a failure; with the gate on, a
    miss is a failure at `error`.
    """
    return RenderQAResult(
        metric=metric,
        passed=(not found) if gate else True,
        value=value,
        threshold=threshold,
        severity=("error" if gate else "warning") if found else "info",
        detail=detail)


def measure_cut_motivation(video_path: str,
                            seams: Sequence[Seam],
                            cuts: Optional[Sequence[CutPoint]] = None,
                            tolerance: float = STRUCTURE_WINDOW_SECONDS,
                            gate: bool = CUT_MOTIVATION_GATES) -> RenderQAResult:
    """A cut in the deliverable that no declared seam explains (QA-009).

    The plan says where the picture changes; the render says where it
    actually did.  A cut that matches nothing is a cut nobody wrote -
    the conform put a picture change on the timeline that the manifest
    does not carry, and no reader of the manifest can see it.

    REPORTED, not gated: the detector's own threshold is what decides
    whether a measured change is a cut, and promoting the check is the
    boolean above.
    """
    if cuts is None:
        cuts = detect_cut_points(video_path)
    if not seams:
        return _verdict(
            "cut_motivation", True,
            ("NOT MEASURED - the plan declared no seam for this render to "
             "be measured against. A cut with nothing to explain it is "
             "not the same claim as a render with no cuts."),
            None, None, gate)
    pairs, unmatched, _ = match_cuts_to_seams(cuts, seams, tolerance)
    rows = [{"time": round(cut.time, 3), "scene_score": round(cut.score, 3)}
            for cut in unmatched]
    detail = (f"{len(unmatched)} cut(s) in the delivered picture match no "
              f"declared seam (of {len(cuts)} measured cut(s) against "
              f"{len(seams)} declared seam(s))")
    if rows:
        detail += ": " + ", ".join(f"{row['time']:.2f}s" for row in rows)
    return _verdict("cut_motivation", bool(rows), detail, rows,
                    {"tolerance_seconds": tolerance, "gates": gate}, gate)


def measure_dialogue_integrity(video_path: str,
                                words: Sequence[Word],
                                cuts: Optional[Sequence[CutPoint]] = None,
                                gate: bool = DIALOGUE_INTEGRITY_GATES) -> RenderQAResult:
    """A seam that lands inside a spoken word (QA-010).

    A word is damaged when the picture changes while it is still being
    spoken: the listener hears the word cut off, and no caption or
    karaoke highlight placed on that word's timing can be right.

    REPORTED, not gated - the same ruling as every other check here.
    """
    if not words:
        return _verdict(
            "dialogue_integrity", True,
            ("NOT MEASURED - the plan declared no word timing for this "
             "render to be measured against."),
            None, None, gate)
    if cuts is None:
        cuts = detect_cut_points(video_path)
    rows = []
    for cut in cuts:
        for word in words:
            if word.start < cut.time < word.end:
                rows.append({
                    "time": round(cut.time, 3),
                    "word": word.text,
                    "word_start": round(word.start, 3),
                    "word_end": round(word.end, 3),
                })
                break
    detail = (f"{len(rows)} cut(s) land inside a spoken word "
              f"(of {len(cuts)} measured cut(s) against {len(words)} "
              f"planned word(s))")
    if rows:
        detail += ": " + ", ".join(
            f"'{row['word']}' at {row['time']:.2f}s" for row in rows)
    return _verdict("dialogue_integrity", bool(rows), detail, rows,
                    {"gates": gate}, gate)


def measure_visual_continuity(video_path: str,
                              seams: Sequence[Seam],
                              frame_rate: float = 0.0,
                              gate: bool = VISUAL_CONTINUITY_GATES) -> RenderQAResult:
    """A join that should be invisible and is not (QA-011).

    A `split` - one shot cut in two - must leave no trace: the frames
    either side of it are consecutive frames of the same footage, and
    the seam is judged against the shot's OWN motion rather than against
    an absolute number, because a shot that moves a lot moves a lot at
    every frame.

    The UNEXPECTED FRAMING CHANGE is not measured here: `render_qa`'s
    `measure_frame_occupancy` already holds one geometry per declared
    framing and judges the delivered picture against the declared one,
    and this file does not repeat it.

    REPORTED, not gated.
    """
    splits = [seam for seam in seams if seam.kind == SEAM_SPLIT]
    if not splits:
        return _verdict(
            "visual_continuity", True,
            ("NOT MEASURED - the plan declared no split for this render "
             "to be measured against. A join that should be invisible "
             "needs a join the plan declared."),
            None, None, gate)
    rows: List[Dict[str, Any]] = []
    for seam in splits:
        measured = measure_seam_jump(video_path, seam.at, frame_rate)
        if measured is None:
            rows.append({"time": round(seam.at, 3), "kind": seam.kind,
                         "label": seam.label, "measured": False,
                         "reason": "no frames on one side of the seam"})
            continue
        jump, local, jump_at = measured
        # A discontinuity that is NOT at this seam is not this seam's
        # discontinuity: the window is centred on the seam, so the
        # largest difference in it belongs to the seam only when it
        # happened there.
        at_seam = abs(jump_at - seam.at) <= 2.0 / max(
            frame_rate if frame_rate else 25.0, 1.0)
        visible = at_seam and jump > max(CONTINUITY_MIN_JUMP,
                                         CONTINUITY_JUMP_RATIO * local)
        rows.append({
            "time": round(seam.at, 3), "kind": seam.kind,
            "label": seam.label, "measured": True,
            "jump": round(jump, 2), "local_motion": round(local, 2),
            "jump_at": round(jump_at, 3), "at_seam": bool(at_seam),
            "visible": bool(visible),
        })

    broken = [row for row in rows if row.get("visible")]
    detail = (f"{len(broken)} of {len(rows)} declared split(s) show a "
              f"visible join")
    if broken:
        detail += ": " + ", ".join(
            f"{row['time']:.2f}s (jump {row.get('jump')} vs motion "
            f"{row.get('local_motion')})" for row in broken)
    return _verdict("visual_continuity", bool(broken), detail,
                    {"splits": rows},
                    {"jump_ratio": CONTINUITY_JUMP_RATIO,
                     "min_jump": CONTINUITY_MIN_JUMP, "gates": gate}, gate)


def measure_flash_frames(video_path: str,
                         frame_rate: float = 0.0,
                         gate: bool = FLASH_FRAMES_GATES) -> RenderQAResult:
    """A single frame that flashes (QA-011).

    A flash is one frame, not a shot: the frame before it and the frame
    after it are close to each other and it is not close to either.  No
    plan asks for one, and it is the one visual defect a viewer cannot
    mistake for a cut.

    Unlike the other readings here this one needs no plan: a flash is a
    defect whoever asked for the video.  REPORTED, not gated.
    """
    flashes = measure_flashes(video_path, frame_rate)
    detail = f"{len(flashes)} flash frame(s) in the delivered picture"
    if flashes:
        detail += ": " + ", ".join(f"{t:.2f}s" for t in flashes)
    return _verdict("flash_frames", bool(flashes), detail,
                    {"flashes": [round(t, 3) for t in flashes]},
                    {"jump_levels": FLASH_JUMP_LEVELS, "gates": gate}, gate)


def measure_section_levels(video_path: str,
                           sections: Sequence[Section],
                           levels: Optional[Sequence[LevelReading]] = None,
                           tolerance_db: float = SECTION_LEVEL_JUMP_DB,
                           gate: bool = SECTION_LEVELS_GATES) -> RenderQAResult:
    """Two adjacent sections delivered at very different levels (QA-012).

    The whole master's loudness is `render_qa.measure_lufs`'s business;
    this asks a different question - whether the level STEPS between one
    section and the next.  A step the plan made is not a mixing
    mistake, so a section the plan marked `silent` is exempt on both
    of its edges.

    REPORTED, not gated - six decibels is a mechanical default and
    promoting it is the boolean above.
    """
    if not sections:
        return _verdict(
            "section_levels", True,
            ("NOT MEASURED - the plan declared no section for this render "
             "to be measured against."),
            None, None, gate)
    if levels is None:
        levels = measure_window_levels(
            video_path, [(s.start, s.end) for s in sections])
    by_window = {(round(r.start, 3), round(r.end, 3)): r for r in levels}

    readings = []
    for section in sections:
        reading = by_window.get((round(section.start, 3),
                                 round(section.end, 3)))
        if reading is None:
            continue
        readings.append({"label": section.label,
                         "start": round(section.start, 3),
                         "end": round(section.end, 3),
                         "rms_db": round(reading.rms_db, 2),
                         "quiet": section.quiet})

    steps = []
    for index in range(1, len(readings)):
        before, after = readings[index - 1], readings[index]
        if before["quiet"] or after["quiet"]:
            continue
        delta = float(after["rms_db"]) - float(before["rms_db"])
        steps.append({"from": before["label"], "to": after["label"],
                      "delta_db": round(delta, 2), "at": after["start"],
                      "over_tolerance": abs(delta) > tolerance_db})
    loud = [step for step in steps if step["over_tolerance"]]
    detail = (f"{len(loud)} adjacent section pair(s) differ by more than "
              f"{tolerance_db:.1f} dB (of {len(steps)} comparable pair(s) "
              f"across {len(readings)} measured section(s))")
    if loud:
        detail += ": " + ", ".join(
            f"{step['from']} -> {step['to']} {step['delta_db']:+.1f} dB at "
            f"{step['at']:.2f}s" for step in loud)
    return _verdict("section_levels", bool(loud), detail,
                    {"sections": readings, "steps": steps},
                    {"tolerance_db": tolerance_db, "gates": gate}, gate)


def measure_caption_consistency(captions: Sequence[CaptionCard],
                                frame_height: Optional[float] = None,
                                tolerance: float = CAPTION_SIZE_TOLERANCE,
                                gate: bool = CAPTION_CONSISTENCY_GATES) -> RenderQAResult:
    """Caption cards drawn at different sizes from one another (QA-013).

    The other three caption questions are already answered elsewhere and
    are not asked again here: `render_qa.measure_caption_legibility`
    judges whether a card can be READ, `qa/subtitle_qa` judges whether it
    is CLIPPED at an edge, and `subtitle_qa.verify_subtitle_timing`
    judges whether it is TIMED against the plan.  What was missing is
    whether the cards are drawn ALIKE - a reel whose captions change
    size halfway through reads as two captions from two different
    videos.

    REPORTED, not gated.
    """
    if not captions:
        return _verdict(
            "caption_consistency", True,
            ("NOT MEASURED - the plan declared no caption segment for "
             "this render to be measured against."),
            None, None, gate)
    from library.tools import render_qa as rq

    readings = []
    for card in captions:
        observation = rq.caption_legibility_observation(
            card.overlay_path, None, frame_height)
        if observation is None:
            readings.append({"label": card.label,
                             "start": round(card.start, 3),
                             "measured": False,
                             "reason": "the caption overlay could not be read"})
            continue
        readings.append({
            "label": card.label,
            "start": round(card.start, 3),
            "measured": True,
            "smallest_card_height": observation.get("smallest_card_height"),
            "legible": observation.get("legible"),
        })

    heights = [row["smallest_card_height"] for row in readings
               if row.get("measured") and row.get("smallest_card_height")]
    rows = []
    if heights:
        norm = float(statistics.median(heights))
        for reading in readings:
            height = reading.get("smallest_card_height")
            if not height or not norm:
                continue
            ratio = float(height) / norm
            reading["height_ratio"] = round(ratio, 3)
            if abs(ratio - 1.0) > tolerance:
                rows.append(reading)
    detail = (f"{len(rows)} caption segment(s) draw their smallest card at "
              f"a size {tolerance:.0%} away from the reel's own "
              f"(of {len(readings)} segment(s))")
    if rows:
        detail += ": " + ", ".join(
            f"{row.get('label') or row['start']} at "
            f"{row.get('height_ratio')}x" for row in rows)
    return _verdict("caption_consistency", bool(rows), detail,
                    {"segments": readings},
                    {"tolerance": tolerance, "gates": gate}, gate)


def measure_narrative_coverage(seams: Sequence[Seam],
                                sections: Sequence[Section] = (),
                                cuts: Optional[Sequence[CutPoint]] = None,
                                render_duration: Optional[float] = None,
                                tolerance: float = STRUCTURE_WINDOW_SECONDS,
                                gate: bool = NARRATIVE_COVERAGE_GATES) -> RenderQAResult:
    """A piece of the plan that is not in the deliverable (QA-014).

    The plan's cuts must be in the deliverable: a declared `cut` seam
    that the render does not show means the structure the plan wrote
    is not the structure that was delivered.  A declared `split` is the
    opposite - the join is meant to be invisible, so it is not evidence
    of anything missing.

    The render's own length is the other half: a deliverable that ends
    before the plan's last section is missing the end of it.

    The CONTENT half of this question - whether the deliverable SAYS
    what the plan said - is `reel_hearing`'s business, and it is not
    repeated here.

    REPORTED, not gated.
    """
    if not seams:
        return _verdict(
            "narrative_coverage", True,
            ("NOT MEASURED - the plan declared no seam for this render to "
             "be measured against."),
            None, None, gate)
    if cuts is None:
        cuts = []
    pairs, _, unmatched = match_cuts_to_seams(cuts, seams, tolerance)
    missing = [seam for seam in unmatched if seam.kind == SEAM_CUT]
    rows = [{"time": round(seam.at, 3), "label": seam.label}
            for seam in missing]

    short = None
    if render_duration is not None:
        planned_end = max((s.end for s in sections), default=0.0)
        if planned_end and render_duration + tolerance < planned_end:
            short = {"planned_end": round(planned_end, 3),
                     "render_duration": round(float(render_duration), 3)}

    detail = (f"{len(rows)} declared cut(s) are not in the delivered "
              f"picture (of {len(seams)} declared seam(s))")
    if short:
        detail += (f"; the deliverable ends at "
                   f"{short['render_duration']:.2f}s and the plan covers "
                   f"{short['planned_end']:.2f}s")
    value = {"missing_cuts": rows, "short": short}
    return _verdict("narrative_coverage", bool(rows or short), detail,
                    value, {"tolerance_seconds": tolerance, "gates": gate},
                    gate)


def measure_brief_pacing(seams: Sequence[Seam],
                          sections: Sequence[Section] = (),
                          cuts: Optional[Sequence[CutPoint]] = None,
                          tolerance: float = STRUCTURE_WINDOW_SECONDS,
                          drift_ratio: float = PACING_DRIFT_RATIO,
                          gate: bool = BRIEF_PACING_GATES) -> RenderQAResult:
    """The delivered pace is not the pace the plan wrote (QA-015).

    The brief is prose, and the plan is what the pipeline made of it,
    so the plan's own seams are the pace the brief asked for.  Where
    the render puts a seam somewhere else, the reel runs at a pace
    nobody chose.  The bound is a FRACTION of the section the seam
    closes, because a half-second matters in a two-second section and
    means nothing in a half-minute one.

    The CONTINUITY half of this question is `visual_continuity`'s.

    REPORTED, not gated.
    """
    if not seams:
        return _verdict(
            "brief_pacing", True,
            ("NOT MEASURED - the plan declared no seam for this render to "
             "be measured against."),
            None, None, gate)
    if cuts is None:
        cuts = []
    pairs, _, unmatched = match_cuts_to_seams(cuts, seams, tolerance)

    rows = []
    for cut, seam in pairs:
        if seam.kind == SEAM_SPLIT:
            continue
        section = _section_ending_at(sections, seam.at)
        span = section.duration if section else 0.0
        drift = float(cut.time) - float(seam.at)
        allowed = max(PACING_DRIFT_FLOOR_SECONDS, span * drift_ratio)
        if abs(drift) > allowed:
            rows.append({
                "declared_at": round(seam.at, 3),
                "delivered_at": round(cut.time, 3),
                "drift_seconds": round(drift, 3),
                "allowed_seconds": round(allowed, 3),
                "section": section.label if section else "",
            })
    detail = (f"{len(rows)} declared seam(s) are delivered somewhere else "
              f"(of {len(pairs)} matched seam(s))")
    if rows:
        detail += ": " + ", ".join(
            f"{row['declared_at']:.2f}s -> {row['delivered_at']:.2f}s "
            f"(drift {row['drift_seconds']:+.2f}s, allowed "
            f"{row['allowed_seconds']:.2f}s)" for row in rows)
    return _verdict("brief_pacing", bool(rows), detail, rows,
                    {"drift_ratio": drift_ratio,
                     "drift_floor_seconds": PACING_DRIFT_FLOOR_SECONDS,
                     "tolerance_seconds": tolerance, "gates": gate}, gate)


def _section_ending_at(sections: Sequence[Section],
                        at: float) -> Optional[Section]:
    """The section a seam at `at` closes, if any."""
    for section in sections:
        if abs(section.end - at) <= STRUCTURE_WINDOW_SECONDS:
            return section
    return None


# ── The composed capability ──────────────────────────────────────────


def verify_render_perception(video_path: str,
                             assembly_manifest: Optional[dict] = None,
                             plan: Optional[PerceptionPlan] = None,
                             render_duration: Optional[float] = None,
                             gate_overrides: Optional[Dict[str, bool]] = None
                             ) -> List[RenderQAResult]:
    """Every perceptual reading of one delivered file, in one call.

    The measurements are made ONCE - the render's cut points are found
    once and every reading that needs them reads the same list - and
    the readings are returned as the same `RenderQAResult` rows every
    other measurement in this pipeline produces, so they travel through
    the same report, the same reader and the same summary.

    `plan` is what the plan declared; `assembly_manifest` is read into
    one when `plan` is not given.  A plan that declared nothing yields
    readings that say they were NOT MEASURED, never readings that pass
    because there was nothing to check.
    """
    if plan is None:
        plan = perception_plan(assembly_manifest)
    overrides = dict(gate_overrides or {})
    if render_duration is None:
        render_duration = file_duration(video_path)

    cuts = detect_cut_points(video_path)
    levels = measure_window_levels(
        video_path, [(s.start, s.end) for s in plan.sections])

    return [
        measure_cut_motivation(
            video_path, plan.seams, cuts=cuts,
            gate=overrides.get("cut_motivation", CUT_MOTIVATION_GATES)),
        measure_dialogue_integrity(
            video_path, plan.words, cuts=cuts,
            gate=overrides.get("dialogue_integrity",
                               DIALOGUE_INTEGRITY_GATES)),
        measure_visual_continuity(
            video_path, plan.seams, plan.frame_rate,
            gate=overrides.get("visual_continuity",
                               VISUAL_CONTINUITY_GATES)),
        measure_flash_frames(
            video_path, plan.frame_rate,
            gate=overrides.get("flash_frames", FLASH_FRAMES_GATES)),
        measure_section_levels(
            video_path, plan.sections, levels=levels,
            gate=overrides.get("section_levels", SECTION_LEVELS_GATES)),
        measure_caption_consistency(
            plan.captions,
            gate=overrides.get("caption_consistency",
                               CAPTION_CONSISTENCY_GATES)),
        measure_narrative_coverage(
            plan.seams, plan.sections, cuts, render_duration=render_duration,
            gate=overrides.get("narrative_coverage",
                               NARRATIVE_COVERAGE_GATES)),
        measure_brief_pacing(
            plan.seams, plan.sections, cuts,
            gate=overrides.get("brief_pacing", BRIEF_PACING_GATES)),
    ]


def _number(value: Any) -> Optional[float]:
    """A float, or None when the value is not one."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number
