"""Prove that built reel timelines match the plan that produced them.

This verifier exists because the captain's standing goal requires proof
that produced videos match quality gates, that timelines have no errors
against what was planned, and that the plans themselves are good.

On 2026-09-04 a scout audited the 16 built reels and found 8 real
disagreements (F1-F8) classified as ENCODING or PLANNING.  That audit
was done with throwaway scripts; this tool is the permanent, re-runnable
replacement.

What it checks, ported from the audit's findings
-------------------------------------------------
- **F1: Picture/audio continuity** - one-frame black/silent holes at cuts.
  Distinguished from holes inherited from the master (F3), which are the
  plan's fault, not the builder's.
- **F2: Caption card duration** - each card placed one frame shorter than
  rendered.
- **F3: Master-inherited holes** - picture holes in the master that a reel
  span reproduces faithfully.  These are the plan's fault and reported as
  warnings, not failures.
- **F4: Item count and per-speaker duration** - clips silently dropped
  where two placements shared one record position.
- **F5: Caption coverage** - seconds of real speech with no caption,
  split by straddling segments (a real gap) vs frame-quantisation residue
  (not a defect).
- **F6: Caption card overlap** - cards that cannot coexist on one track.
- **F7: Short placed items** - caption, video and audio items under
  the 0.5s readability floor (`manifest_validator.
  MIN_CAPTION_DISPLAY_SECONDS`), measured on what the build placed.
- **F9: Duplicate placements** - a placement loop that runs once per clip
  PER CLIP, producing N*N items at duplicate record positions.  This
  verifier is the only guard against that class of bug because
  build_reel_timeline has zero tests.
- **F10: Format mismatch** - a reel timeline that is not 1080x1920 or not
  at the master's frame rate.  This project has a documented history of a
  correct vertical timeline rendering out LANDSCAPE while every structural
  check passed.
- **F12: Delivered framing** - F10 proves the FRAME is 1080x1920; this
  proves what is IN it.  Every reel item in the field test carries
  Resolve's identity transform on a `scaleToFit` timeline, so a
  3840x2160 source is delivered as a 1080x607.5 strip - 31.64% of the
  frame - and nothing on the reels path had ever opened a video file or
  read `framing_intent` to find out whether that was wanted.  See
  `library/tools/reel_framing.py`.
- **F11: Subtitle styling** - per-speaker styling is the diarization
  signal.  Reads the CARDS ON THE TIMELINE (not the config), verifies
  speaker attribution via overlay filenames, asserts that a two-speaker
  reel shows two distinct speaker slugs and distinct overlay sources.
  A gate that checks the config instead of the output would pass when
  the builder applied one style to everyone.
- **Plan quality gates** - reel length 45-90s guidance, both speakers
  present with real turns, picture continuity across the plan span.

Read-only proof
---------------
The tool hashes every timeline's structural_signature before and after
the run.  If they differ, it reports a FATAL error and exits 2.

This tool is the independent check on the fixer's work (PR #507) and
does not coordinate with it.

``tests/test_reel_conformance_verifier.py``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# The caption readability floor, shared with the manifest's own P6 check
# so a reel and a master are never held to different floors.  F7 applies
# it with NO exemption - see `check_short_captions` for why the
# last-of-block clause cannot survive on the placed path.
from library.tools.explainer_plan import EXPLAINER_TRACK, RENDER_PREFIX
from library.tools.reel_semantic_visual import (
    AWAITING_MODEL_ANSWER as SEMANTIC_AWAITING_ANSWER)
from library.tools.reel_semantic_visual import (
    RENDER_PREFIX as SEMANTIC_RENDER_PREFIX)
from library.tools.reel_semantic_visual import SEMANTIC_TRACK
from library.tools.speaker_identity import (
    TRACK_NAME as LOWER_THIRD_TRACK_NAME,
    RENDER_PREFIX as LOWER_THIRD_RENDER_PREFIX,
)
from library.tools.reel_semantic_visual import SPAN_EVERY_EVENT_DROPPED
from library.tools.frame_utils import span_frames
from library.tools.manifest_validator import MIN_CAPTION_DISPLAY_SECONDS
from library.tools.reel_exchange import LENGTH_GUIDANCE


# ── Finding classes ──────────────────────────────────────────────────

class FindingClass:
    """The audit's finding classes, each with a code, owner and severity."""
    F1 = "F1"  # ENCODING: one-frame holes at cuts
    F2 = "F2"  # ENCODING: caption cards placed one frame short
    F3 = "F3"  # PLANNING: master-inherited picture holes (warning)
    F4 = "F4"  # ENCODING: dropped clips (item count mismatch)
    F5 = "F5"  # PLANNING: uncaptioned speech (straddling segments)
    F6 = "F6"  # PLANNING: overlapping caption cards
    F7 = "F7"  # PLANNING: placed items under the readability floor (<0.5s)
    F8 = "F8"  # PLANNING: boundary cuts through unseen speech

    # 2026-09-06.  F2 paired planned cards to placed items BY LIST INDEX
    # while its own comment said it matched on start frame.  With 832
    # cards planned and 763 placed, every pair after a reel's first
    # unplaced card compared one card's plan against a DIFFERENT card's
    # item - 701 findings, r(planned, placed) = 0.027, which reads as a
    # placement defect and is not one.  Pairing by start frame makes the
    # length difference visible as what it is instead of absorbing it
    # into a shift, and this is that finding.
    F14 = "F14"  # PLANNING: caption planned and never placed
    F15 = "F15"  # PLANNING: caption card hangs past its words
    F16 = "F16"  # PLANNING: a placed caption name breaks mid-word
    F17 = "F17"  # PLANNING: caption card mixes speakers (bleed)

    # 2026-09-07: the ANIMATED EXPLAINER, on its own video track.
    # `_snapshot_to_reel_timeline` classified a clip by
    # `track <= 2 -> picture`, `== 3 -> captions`, `audio -> audio`, and
    # an implicit else that DROPPED it. So an explainer segment was not
    # read as a duplicate placement and not read as a picture hole - it
    # was not read at all, which is the gate-that-cannot-fail shape
    # wearing a different hat. F21 is what reads it.
    F21 = "F21"  # ENCODING: the explainer, against the plan the build wrote

    # 2026-09-08: SEMANTIC VISUALS, on their own video track. The same
    # shape as the explainer one above it: a placed item the build never
    # recorded is out-of-band content on the captain's timeline, and a
    # recorded segment with no placed item is a plan that did not reach
    # the timeline. F22 is what reads both.
    F22 = "F22"  # ENCODING: the semantic visuals, against the record the build wrote

    # 2026-09-09: the SPAN picture plan, against the record the build
    # wrote. `resolve_span_plan` distinguishes a model that looked and
    # chose stillness (`span_no_events_planned` - a decision) from a
    # model whose every beat was REFUSED
    # (`span_every_event_dropped` - the absence of a decision
    # surviving). Nothing read that difference, so an all-refused plan
    # built green and silent. F23 refuses the second and passes the
    # first: a reel whose every planned picture was refused must not
    # build green and silently.
    F23 = "F23"  # PLANNING: an all-refused span picture plan

    # 2026-09-12: the SPEAKER LOWER THIRDS, against the plan the build
    # wrote. The same two directions F21 and F22 carry - a recorded
    # segment with no placed item is a name the viewer never saw, and a
    # placed item no plan accounts for is an out-of-band append - plus
    # one this layer has and they do not: the reel must not name one
    # speaker TWICE, because "on the first appearance" is the whole ask
    # (`library/tools/speaker_identity.py`).
    F24 = "F24"  # ENCODING: the speaker lower thirds, against the plan

    # Plan quality gates (not from the audit, from the captain's list)
    PQ_LENGTH = "PQ-LENGTH"       # outside the 45-90s PREFERENCE (warning)
    PQ_SPEAKERS = "PQ-SPEAKERS"   # not both speakers with real turns
    PQ_PICTURE = "PQ-PICTURE"     # plan span contains picture holes

    # Post-audit finding, 2026-09-04: a placement loop that runs once
    # per clip PER CLIP produces N*N placements at duplicate record
    # positions.  build_reel_timeline has zero tests, so this verifier
    # is currently the only thing standing between that class of bug
    # and the captain's timelines.
    F9 = "F9"  # ENCODING: duplicate placements at same record position

    # Captain's explicit gates, 2026-09-04.
    F10 = "F10"  # ENCODING: timeline format mismatch (not 1080x1920 or wrong fps)
    F11 = "F11"  # PLANNING: subtitle styling - speakers not differentiated

    # 2026-09-06.  F10 checks that the FRAME is 1080x1920; nothing checked
    # what is IN it.  Every video item on all 49 timelines of the field
    # test carries Resolve's identity transform on a `scaleToFit`
    # timeline, so a 3840x2160 source is delivered as 1080x607.5 - 31.64%
    # of the frame, the rest black - and `build_reel_timeline` never read
    # `framing_intent` to find out whether that was wanted.  The whole
    # reels path opens no video file, so this was the one picture
    # property nothing could see.  See library/tools/reel_framing.py.
    F12 = "F12"  # ENCODING: delivered picture is not the declared framing

    # 2026-09-07.  A reel may now contain a FULL-FRAME element - a card
    # that REPLACES picture on V1 for its own stretch of reel time
    # (library/tools/full_frame_element.py).  Three checks in this file
    # would have misread one, and every one of them is a false ERROR on a
    # correct build rather than a miss:
    #
    #   F4  counts V1/V2 items against the plan's placements, so a card
    #       reads as an extra picture item AND its seconds are added to
    #       whichever speaker owns V1.
    #   F12 looks each V1/V2 item's source up in the CATALOG, and a
    #       rendered card is not footage and is not in it, so the reel
    #       comes back "framing could not be read".
    #   The caption checks derive their expected cards from the reel's
    #       spine, which is measured in BODY seconds; a head card pushes
    #       every placed caption down by its length, so all of them read
    #       as misplaced.
    #
    # F13 is the class for the card itself, and it can fail in BOTH
    # directions: a declared card that is not on the timeline, and an
    # item on the timeline that no declaration accounts for.
    F13 = "F13"  # ENCODING: full-frame card missing, extra or mis-sized

    # Plan provenance, 2026-09-05: the verifier was caught grading 16
    # reels against a plan describing 14 completely different moments,
    # producing 42 confident, precise, meaningless errors.  This class
    # fires when the verifier cannot establish that the plan it was
    # handed actually describes the timelines it is reading - and the
    # entire run is REFUSED rather than producing numbers that look like
    # signal but are noise.
    PLAN_MISMATCH = "PLAN-MISMATCH"

    # Anti-vacuity, 2026-09-05.  The caption checks were guarded with
    # `if plan.captions and timeline.caption_items:`, `plan.captions`
    # defaulted to `()`, and nothing ever filled it - so the verifier
    # reported captions expected/actual as 0/28, 0/39 ... 0/762: it
    # expected zero captions, found 762, and PASSED.  An empty reference
    # set silently DISABLED F2, F5, F6 and F7 rather than failing them,
    # which is the gate-that-cannot-fail this repository keeps removing
    # (AGENTS.md 10.4).
    #
    # So the emptiness is now the finding.  A check with nothing to
    # compare against REFUSES; it never passes quietly.  The plan side is
    # derived in `_derive_plan_from_master`, but this class exists so the
    # guarantee does not depend on that derivation continuing to work: if
    # the caption cards ever stop reaching the plan again, the verifier
    # says so instead of going green on nothing.
    NO_REFERENCE = "NO-REFERENCE"

    # Transition overlays, 2026-09-07.  `_snapshot_to_reel_timeline`
    # sorted a timeline's clips into picture (V1-V2), captions (V3) and
    # audio, and DROPPED everything else on the floor - so a clip on V4
    # existed on the captain's timeline and in no check.  Not read as a
    # duplicate placement, not read as a picture hole: not read at all,
    # which is the worst of the three because it is invisible.
    #
    # F18 grades the transition elements against the plan that placed
    # them.  F19 is the anti-silent-drop half: ANY video item on a track
    # this verifier does not classify is reported, so the next feature
    # that adds a track cannot repeat this.
    F18 = "F18"  # ENCODING: transition element missing, extra or misplaced
    F19 = "F19"  # ENCODING: a video item on a track nothing grades

    # A fact about the delivered picture rather than a defect: an element
    # laid over a cut draws over the captions under it.  Whether that is
    # wanted is the captain's, so this REPORTS and does not fail - but it
    # is measured, because it was previously neither.
    F20 = "F20"  # PLANNING: transition element covers a caption (warning)


ENCODING_CLASSES = {FindingClass.F1, FindingClass.F2, FindingClass.F4,
                    FindingClass.F9, FindingClass.F10, FindingClass.F12,
                    FindingClass.F13, FindingClass.F18,
                    FindingClass.F19}
PLANNING_CLASSES = {FindingClass.F3, FindingClass.F5, FindingClass.F6,
                    FindingClass.F7, FindingClass.F8, FindingClass.F11, FindingClass.F14, FindingClass.F15, FindingClass.F16, FindingClass.F17,
                    FindingClass.F20}
PLAN_QUALITY_CLASSES = {FindingClass.PQ_LENGTH, FindingClass.PQ_SPEAKERS,
                        FindingClass.PQ_PICTURE}
PROVENANCE_CLASSES = {FindingClass.PLAN_MISMATCH, FindingClass.NO_REFERENCE}

WARNING_CLASSES = {FindingClass.F3, FindingClass.F20}
"""F3 (master-inherited holes) is the plan's fault, not the build's.
F20 (a transition element over a caption) is what the gesture DOES, not
a defect - hiding a cut hides what is on it. Both are reported so they
are visible, and neither fails the gate."""


@dataclass(frozen=True)
class Finding:
    """One disagreement between plan and timeline."""

    finding_class: str
    reel: str
    message: str
    severity: str = "error"
    """'error' fails the gate; 'warning' is reported but does not."""
    detail: Optional[dict] = None
    """Machine-readable detail for programmatic consumers."""

    def as_dict(self) -> dict:
        d = {"class": self.finding_class, "reel": self.reel,
             "message": self.message, "severity": self.severity}
        if self.detail:
            d["detail"] = self.detail
        return d


# ── Data structures for the plan ─────────────────────────────────────

@dataclass(frozen=True)
class PlannedPlacement:
    """One clip the plan says should be on the reel."""
    track_index: int
    speaker: Optional[str]
    record_seconds: float
    source_in: float
    source_out: float
    source_file: str


@dataclass(frozen=True)
class PlannedCaption:
    """One caption card the plan says should exist."""
    start_seconds: float
    end_seconds: float
    text: str
    speaker: Optional[str]
    frames: int
    block_position: Optional[str] = None
    """The spine block this card was planned from, as a string."""
    block_end_seconds: Optional[float] = None
    """Where that block ends, in REEL seconds - what F7's exemption needs."""


@dataclass(frozen=True)
class PlannedCard:
    """One full-frame element the plan says this reel contains.

    The verifier's own flattening of `full_frame_element.PlannedCard`, so
    the check functions stay pure and testable without the renderer - the
    same reason `PlannedPlacement` exists beside `reel_build`'s dicts.
    """
    render_name: str
    placement: str
    reel_start_frame: int
    duration_frames: int
    element: str = ""
    """Which roster entry planned it (`full_frame_element`'s key).

    Empty for the cards the tests construct by hand, which predate the
    second entry; ``check_full_frame_cards`` reads it only to name what
    it is holding ("card" or "span segment") in a finding.
    """


@dataclass(frozen=True)
class ReelPlan:
    """Everything the plan says about one reel."""
    reel_name: str
    reel_number: int
    plan_seconds: float
    """Total planned duration in seconds."""
    plan_frames: float
    """Total planned duration in frames (may be fractional)."""
    span_start: float
    """Start of the reel span on the master timeline, in seconds."""
    span_end: float
    """End of the reel span on the master timeline, in seconds."""
    placements: Tuple[PlannedPlacement, ...]
    captions: Tuple[PlannedCaption, ...] = ()
    captions_unavailable: Optional[str] = None
    """Why the expected caption cards could not be derived AT ALL.

    `None` means they were derived - `captions` is then the answer, and
    an empty `captions` means this reel legitimately has none. A string
    means the pipeline could not be asked, and the difference is the
    whole point: collapsing the two is how the caption gate came to
    expect zero cards, find 762, and pass."""

    cards: Tuple["PlannedCard", ...] = ()
    """The full-frame elements this reel contains, if any.

    Empty for every project that declares none, which was every project
    before 2026-09-07. `lead_seconds` below is what the HEAD ones push
    everything else down by."""

    card_row_role: Optional[str] = None
    """The declared full-frame CARD row (`effect.card_row_role`).

    Which NAMED row head/tail cards play on - the captain's answer to
    the logo-on-V1 defect, resolved with the same code the build used.
    None is every reel planned before the declaration existed, and the
    checks read those the legacy way (cards on V1) rather than failing
    history for not naming a row nobody had asked for yet. A
    `full_frame_span` is never governed by this: a span IS the body's
    picture and stays on it.
    """

    lead_seconds: float = 0.0
    """How long the head cards hold before the first frame of footage.

    Derived from `cards`, carried rather than recomputed so the caption
    derivation and the placement derivation cannot disagree about it."""

    cuts: tuple = ()
    """Bad takes removed (from reel_build.Cut)."""
    keep_ranges: Tuple[Tuple[float, float], ...] = ()
    call_to_action: Optional[Tuple[float, float]] = None
    held_frames: int = 0
    """Frames the declared FREEZE tail holds, from no keep range.

    Picture the viewer watches that the ranges do not describe, the
    same case a full-frame card is (`library/tools/reel_ending.py`).
    """
    declared_short_captions: Tuple[Tuple[int, int], ...] = ()
    """Cards a caption-timing PIN left under the readability floor.

    `((start_frame, frames), ...)`. F7 reports these instead of
    failing them: the captain shortened them himself and recorded why,
    and a gate that fails correct output is no more coverage than one
    that cannot fail (AGENTS.md 10.4). Every other short card still
    FAILS.
    """
    """The closing CTA range on the MASTER, which may come from anywhere
    in the episode and is the LAST of `keep_ranges`.  Recorded separately
    because the checks that read reel BOUNDARIES want the body span and
    the closer's span - the seams `keep_ranges` also carries are bad-take
    cuts inside the body, which are a different thing and were never
    boundaries any check looked at."""


# ── Data structures for what is on the timeline ──────────────────────

@dataclass(frozen=True)
class TimelineItem:
    """One item on one track of a reel timeline."""
    track_type: str
    track_index: int
    start_frame: int
    end_frame: int
    duration_frames: int
    source_start_frame: int
    source_end_frame: int
    source_file: str
    speaker: Optional[str]
    name: str
    unique_id: str = ""
    track_name: str = ""
    """The Resolve row's name as read back (`TimelineClip.track_name`).

    The bucketing below reads this FIRST: rows are named from the
    track plan, so a name says which role a row carries wherever it
    sits. Indices are the fallback, for timelines built before rows
    were named - and for rows the plan packed (a semantic row with no
    transitions above it sits on V4, not V6, and only its name still
    says what it is)."""

    transform: dict = field(default_factory=dict)
    """`TimelineItem.GetProperty()` as `timeline_ingest` read it.

    Empty means Resolve did not say, which F12 treats as unreadable
    rather than as an identity transform - see
    `library/tools/reel_framing.py`."""

    @property
    def start_seconds(self) -> float:
        return self.start_frame / (24000 / 1001)

    @property
    def end_seconds(self) -> float:
        return self.end_frame / (24000 / 1001)


@dataclass(frozen=True)
class ReelTimeline:
    """What is actually on a reel timeline in Resolve."""
    reel_name: str
    fps: float
    total_frames: int
    video_items: Tuple[TimelineItem, ...]
    """All video track items, in order."""
    audio_items: Tuple[TimelineItem, ...]
    """All audio track items, in order."""
    caption_items: Tuple[TimelineItem, ...]
    """V3 caption items, in order."""
    overlay_items: Tuple[TimelineItem, ...] = ()
    """V4 transition-element items, in order.

    Empty on every reel built before `transition_overlay` existed, and
    on every reel of a project that declares no element - which is what
    "declare nothing and get nothing" looks like from here."""
    unclassified_items: Tuple[TimelineItem, ...] = ()
    """Video items on a track this verifier grades with nothing.

    NOT dropped. Until 2026-09-07 the bucketing was `<=2 picture, ==3
    captions, audio` with an implicit else that discarded the item, so a
    clip on V4 was on the timeline and in no check at all. F19 reports
    whatever lands here, so the next feature to add a track finds out on
    the first run instead of never."""
    explainer_items: Tuple[TimelineItem, ...] = ()
    """Items on the explainer track, in order.

    `library/tools/explainer_plan.EXPLAINER_TRACK` is which track that
    is, named there once so the placement and the check cannot disagree
    about it."""
    semantic_items: Tuple[TimelineItem, ...] = ()
    """Items on the semantic-visual track, in order.

    `library/tools/reel_semantic_visual.SEMANTIC_TRACK` is which track
    that is, named there once so the placement and the check cannot
    disagree about it."""
    lower_third_items: Tuple[TimelineItem, ...] = ()
    """Items on the speaker lower-third row, in order.

    `library/tools/speaker_identity.TRACK_NAME` is which row that is,
    named there once so the placement and the check cannot disagree
    about it. Filed by NAME only: this row has no legacy index, because
    nothing placed on it before F24 existed."""
    frame_items: Tuple[TimelineItem, ...] = ()
    """Items on the TV-frame set row, in order.

    The set the picture plays inside - one rendered overlay per run of
    picture - never picture itself and never a caption. Under the
    captain's Reel 09 ruling the frame sits above two picture rows (V3
    on a two-angle reel); on a single-angle reel or a timeline built
    before rows were named it is V2. Filed by row NAME, never by slot,
    so the caption checks cannot read the bezel as a card."""
    width: int = 0
    """Timeline resolution width, read from Resolve."""
    height: int = 0
    """Timeline resolution height, read from Resolve."""
    markers: dict = field(default_factory=dict)
    picture_frames: Optional[int] = None
    """Frames of picture (V1/V2 - footage and full-frame cards) from the
    timeline origin: max V1/V2 end minus the snapshot's start frame.

    Computed where both are known (`_snapshot_to_reel_timeline`), because
    `total_frames` above is Resolve's GetEndFrame minus GetStartFrame over
    ALL tracks - V3 captions included - while the plan
    `check_plan_describes_timeline` holds against the extent is a PICTURE
    plan (keep ranges plus card frames). Captions are laid by different
    arithmetic (`frame_utils.span_frames` per-edge rounding on reel
    seconds) than picture (per-range sums on master seconds), and the two
    projections of the same seconds disagree by a frame at unlucky
    fractions - measured 2026-09-08 as reel 13's "plan 1902f vs timeline
    1903f" refusal on a build whose picture tiled exactly. The gate reads
    this field so it grades picture against picture; audio, captions and
    overlays keep their own gates (F1-audio, F2, F15, F18, F21).

    None on hand-built fixtures, where `verify_reel` falls back to
    `total_frames` - the old, caption-inclusive reading."""


# ── The actual checks ────────────────────────────────────────────────

# The reel video track a transition element is placed on. Read from the
# module that places them, so the verifier and the placer cannot disagree
# about which track it grades.
from library.tools.transition_overlay import OVERLAY_TRACK  # noqa: E402


def _fps() -> float:
    """The exact frame rate Resolve computes with."""
    return 24000 / 1001


def check_picture_holes(reel_name: str,
                        video_items: Sequence[TimelineItem],
                        master_holes: Optional[Sequence[dict]] = None,
                        ) -> List[Finding]:
    """F1: Find every gap across the union of all picture tracks.

    A gap is a span where no picture item exists across ANY track - a genuine black frame.
    Distinguished from F3 (master-inherited holes) when master_holes is provided.
    """
    findings: List[Finding] = []
    
    global_coverage = []
    sorted_all = sorted(video_items, key=lambda i: i.start_frame)
    if sorted_all:
        c_start, c_end = sorted_all[0].start_frame, sorted_all[0].end_frame
        for item in sorted_all[1:]:
            if item.start_frame <= c_end:
                c_end = max(c_end, item.end_frame)
            else:
                global_coverage.append((c_start, c_end))
                c_start, c_end = item.start_frame, item.end_frame
        global_coverage.append((c_start, c_end))

    global_gaps = []
    for i in range(len(global_coverage) - 1):
        global_gaps.append((global_coverage[i][1], global_coverage[i+1][0]))

    by_track: Dict[int, List[TimelineItem]] = {}
    for item in video_items:
        by_track.setdefault(item.track_index, []).append(item)

    for g_start, g_end in global_gaps:
        gap = g_end - g_start
        if gap > 0:
            tracks_with_gaps = []
            for track, items in sorted(by_track.items()):
                s_items = sorted(items, key=lambda i: i.start_frame)
                for i in range(len(s_items) - 1):
                    if s_items[i].end_frame <= g_start and s_items[i+1].start_frame >= g_end:
                        tracks_with_gaps.append(track)
                        break
            
            track_msg = f"V{tracks_with_gaps[0]}" if len(tracks_with_gaps) == 1 else "The timeline"
            track_val = tracks_with_gaps[0] if tracks_with_gaps else None
            
            is_master_hole = False
            if master_holes is not None:
                for mh in master_holes:
                    mh_len = mh.get("length", 0)
                    if (abs(gap - mh_len) <= 2 and gap > 2):
                        is_master_hole = True
                        break

            if is_master_hole:
                findings.append(Finding(
                    finding_class=FindingClass.F3,
                    reel=reel_name,
                    message=f"{track_msg} has a {gap}-frame picture hole at frame {g_start}, inherited from the master timeline",
                    severity="warning",
                    detail={"track": track_val, "frame": g_start, "gap_frames": gap, "inherited": True, "tracks": tracks_with_gaps}
                ))
            else:
                findings.append(Finding(
                    finding_class=FindingClass.F1,
                    reel=reel_name,
                    message=f"{track_msg} has a {gap}-frame black hole at frame {g_start}",
                    severity="error",
                    detail={"track": track_val, "frame": g_start, "gap_frames": gap, "inherited": False, "tracks": tracks_with_gaps}
                ))

    return findings


def check_audio_holes(reel_name: str,
                      audio_items: Sequence[TimelineItem],
                      ) -> List[Finding]:
    """F1 (audio half): Find every gap across the union of all audio tracks.

    A gap is a span where no audio item exists across ANY track - a genuine silent hole.
    The audit found audio holes are identical to picture holes.
    """
    findings: List[Finding] = []
    
    global_coverage = []
    sorted_all = sorted(audio_items, key=lambda i: i.start_frame)
    if sorted_all:
        c_start, c_end = sorted_all[0].start_frame, sorted_all[0].end_frame
        for item in sorted_all[1:]:
            if item.start_frame <= c_end:
                c_end = max(c_end, item.end_frame)
            else:
                global_coverage.append((c_start, c_end))
                c_start, c_end = item.start_frame, item.end_frame
        global_coverage.append((c_start, c_end))

    global_gaps = []
    for i in range(len(global_coverage) - 1):
        global_gaps.append((global_coverage[i][1], global_coverage[i+1][0]))

    by_track: Dict[int, List[TimelineItem]] = {}
    for item in audio_items:
        by_track.setdefault(item.track_index, []).append(item)

    for g_start, g_end in global_gaps:
        gap = g_end - g_start
        if gap > 0:
            tracks_with_gaps = []
            for track, items in sorted(by_track.items()):
                s_items = sorted(items, key=lambda i: i.start_frame)
                for i in range(len(s_items) - 1):
                    if s_items[i].end_frame <= g_start and s_items[i+1].start_frame >= g_end:
                        tracks_with_gaps.append(track)
                        break
            
            track_msg = f"A{tracks_with_gaps[0]}" if len(tracks_with_gaps) == 1 else "The timeline"
            track_val = tracks_with_gaps[0] if tracks_with_gaps else None
            
            findings.append(Finding(
                finding_class=FindingClass.F1,
                reel=reel_name,
                message=f"{track_msg} has a {gap}-frame silent hole at frame {g_start}",
                severity="error",
                detail={"track": track_val, "frame": g_start, "gap_frames": gap, "inherited": False, "tracks": tracks_with_gaps, "type": "audio"}
            ))

    return findings


def _item_stem(item) -> str:
    """The render basename an item IS, without its extension.

    One spelling for the classifier below and `card_items`: a
    full-frame element is identified by its planned render name
    wherever it sits, so the two readings cannot disagree about what
    a card is.
    """
    stem = (item.source_file or "").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if not stem:
        stem = (item.name or "").rsplit(".", 1)[0]
    return stem


def card_items(video_items: Sequence[TimelineItem],
               cards: Sequence["PlannedCard"],
               ) -> Dict[str, TimelineItem]:
    """Which timeline item is which planned element, by RENDER NAME.

    Positive identification, never a guess: `full_frame_element` names
    every rendered card `reel_NN_card_MM.mov` and every span segment
    `reel_NN_span_SS.mov`, and that basename is on the media pool item,
    so an item is an element because it IS that file. The
    alternative - "a V1 item whose source is not in the catalog" - would
    make every catalog gap look like an element, which is the shape of the
    footage-relink defect rather than a way to find one.

    Items the plan does not account for are simply absent from the
    mapping; :func:`check_full_frame_cards` is what reports them.
    """
    wanted = {card.render_name: card for card in cards}
    found: Dict[str, TimelineItem] = {}
    for item in video_items:
        stem = _item_stem(item)
        if stem in wanted and stem not in found:
            found[stem] = item
    return found


#: The basename shapes `full_frame_element` renders to.  Used ONLY
#: to spot an item that LOOKS like a full-frame element and is not in
#: the plan - the other direction of the same gate.  Identification of a
#: PLANNED element is by exact name (`card_items`).
CARD_NAME_SHAPE = re.compile(r"^reel_\d+_(card_\d+|span_\d+)$")


def check_full_frame_cards(reel_name: str,
                           cards: Sequence["PlannedCard"],
                           video_items: Sequence[TimelineItem],
                           width: int, height: int,
                           fps: float,
                           card_row_role: Optional[str] = None,
                           ) -> List[Finding]:
    """F13: every declared full-frame element is on the timeline, whole.

    Both directions, because a gate that can only fail one way is half a
    gate (AGENTS.md 10.4):

    - an element the plan declares and the timeline does not carry.  A reel
      that quietly starts on speech is indistinguishable from a project
      that declared no card at all, which is how the 4th Wall end card
      survived four months;
    - an item shaped like a rendered element that no declaration accounts
      for - the out-of-band append `bookends` forbids, arriving on the
      reels path instead;
    - an element placed at the wrong reel second, or with the wrong number
      of frames.  One frame either way is an F1 black hole or an overlap.
    - an element on the wrong ROW. A head/tail card plays beside the
      footage on the project's declared card row
      (`effect.card_row_role`); a card on any other row fails here, by
      NAME - which is the gate the logo-on-V1 defect was missing.

    `card_row_role` None is the legacy reading: every reel planned
    before the declaration existed carried its cards on V1, and the
    check reads those against V1 rather than failing history for not
    naming a row nobody had asked for yet. A `full_frame_span` always
    reads V1 - a span IS the body's picture, whatever the card row is.

    Nothing here is a judgement.  Every comparison is between integers
    the plan already fixed, or a row name against the role that put
    the card there.
    """
    findings: List[Finding] = []
    placed = card_items(video_items, cards)
    if card_row_role is not None:
        from library.tools.timeline_layout import ROLE_ROW_BASE
        try:
            card_base = ROLE_ROW_BASE[card_row_role]
        except KeyError:
            raise ValueError(
                f"card_row_role {card_row_role!r} names no card row; "
                f"card roles are semantic and motion_graphics") from None

    for card in cards:
        noun = ("span segment" if card.element == "full_frame_span"
                else "card")
        item = placed.get(card.render_name)
        if item is None:
            findings.append(Finding(
                finding_class=FindingClass.F13, reel=reel_name,
                message=(
                    f"the plan declares a full-frame {noun} "
                    f"{card.render_name!r} at reel frame "
                    f"{card.reel_start_frame} and no item on the timeline "
                    f"is it"),
                severity="error",
                detail={"render_name": card.render_name,
                        "expected_start_frame": card.reel_start_frame,
                        "placed": False}))
            continue
        expected_start = card.reel_start_frame
        if item.start_frame != expected_start:
            findings.append(Finding(
                finding_class=FindingClass.F13, reel=reel_name,
                message=(
                    f"full-frame {noun} {card.render_name!r} starts at frame "
                    f"{item.start_frame}, planned {expected_start}"),
                severity="error",
                detail={"render_name": card.render_name,
                        "actual_start_frame": item.start_frame,
                        "expected_start_frame": expected_start}))
        if item.duration_frames != card.duration_frames:
            findings.append(Finding(
                finding_class=FindingClass.F13, reel=reel_name,
                message=(
                    f"full-frame {noun} {card.render_name!r} runs "
                    f"{item.duration_frames} frames, planned "
                    f"{card.duration_frames}"),
                severity="error",
                detail={"render_name": card.render_name,
                        "actual_frames": item.duration_frames,
                        "expected_frames": card.duration_frames}))
        if card.element == "full_frame_span" or card_row_role is None:
            # Body picture on the picture row (spans), or the legacy
            # reading for reels planned before the card row was
            # declared (everything on V1).
            if item.track_index != 1:
                findings.append(Finding(
                    finding_class=FindingClass.F13, reel=reel_name,
                    message=(
                        f"full-frame {noun} {card.render_name!r} is on V"
                        f"{item.track_index}. A full-frame element REPLACES "
                        f"picture and belongs on V1; above V2 nothing in this "
                        f"file can see it (F4 and F12 read V1 and V2 only), "
                        f"and the footage under it would go on playing its "
                        f"sound, which the reels path cannot turn down"),
                    severity="error",
                    detail={"render_name": card.render_name,
                            "track": item.track_index}))
        elif not _is_layer_named(item.track_name or "", card_base):
            findings.append(Finding(
                finding_class=FindingClass.F13, reel=reel_name,
                message=(
                    f"full-frame {noun} {card.render_name!r} is on "
                    f"V{item.track_index} "
                    f"({item.track_name or 'unnamed'}), not the declared "
                    f"card row: effect.card_row_role is "
                    f"{card_row_role!r}, so it belongs on the "
                    f"{card_base!r} row. A card plays beside the "
                    f"footage, and its row is organisation - landing "
                    f"it on a camera row is the logo-on-V1 defect"),
                severity="error",
                detail={"render_name": card.render_name,
                        "track": item.track_index,
                        "track_name": item.track_name,
                        "card_row_role": card_row_role}))

    known = {card.render_name for card in cards}
    for item in video_items:
        stem = _item_stem(item)
        if CARD_NAME_SHAPE.match(stem) and stem not in known:
            findings.append(Finding(
                finding_class=FindingClass.F13, reel=reel_name,
                message=(
                    f"the timeline carries {stem!r}, which is shaped like a "
                    f"rendered full-frame element, and no declaration accounts "
                    f"for it. An element appended out of band is the defect "
                    f"`bookends` refuses by name on the master"),
                severity="error",
                detail={"render_name": stem, "declared": False,
                        "start_frame": item.start_frame}))
    return findings


def check_item_count(reel_name: str,
                     planned_placements: Sequence[PlannedPlacement],
                     actual_video_items: Sequence[TimelineItem],
                     fps: float,
                     cards: Sequence["PlannedCard"] = (),
                     look=None,
                     ) -> List[Finding]:
    """F4: Compare planned item count vs placed, and per-speaker duration.

    The audit found two reels where items were fewer than planned because
    two placements shared one record position and one was silently
    dropped.  Do NOT trust a producer's self-report - measure the timeline.
    """
    findings: List[Finding] = []

    # Compare total picture item counts
    expected_count = len(planned_placements)
    # Count only V1 and V2 items (picture tracks), and only FOOTAGE ones:
    # a full-frame card is a picture item on V1 that no `placements` entry
    # describes, because it comes from a declaration rather than from the
    # master.  `check_full_frame_cards` is what holds it, by name and by
    # frame, in both directions - dropping it here is not a hole.
    card_by_item = {id(i): n for n, i in
                    card_items(actual_video_items, cards).items()}
    # The TV frame is the SET the picture plays inside, one rendered
    # overlay per run of picture, and no `placements` entry describes
    # it - the same reason a full-frame card is excluded above. It
    # reaches here on the frame row the plan named for it (V3 above two
    # picture rows, V2 on a single-angle reel), and
    # `library/tools/reel_look.py` decides which items those are, so
    # the placer and this check cannot disagree; a frame-shaped overlay
    # the declaration does not account for is held below rather than
    # let through here.
    from library.tools import reel_look as _look
    frame_by_item = _look.frame_overlay_items(actual_video_items, look)
    actual_picture = [i for i in actual_video_items
                      if i.track_index in (1, 2)
                      and id(i) not in card_by_item
                      and id(i) not in frame_by_item]
    actual_count = len(actual_picture)

    # Under the look each speaker keeps their own picture row
    # (captain's ruling on Reel 09 - no collapse), so a track-to-speaker
    # mapping reads the row the plan put each speaker on.  The SOURCE
    # FILE identifies them as well, which is what the plan and the
    # timeline both carry; it stays the tiebreaker wherever the track
    # mapping cannot answer.
    by_source: Dict[str, str] = {}
    if look is not None:
        for p in planned_placements:
            if p.speaker and p.source_file:
                by_source[p.source_file] = p.speaker

    if actual_count != expected_count:
        findings.append(Finding(
            finding_class=FindingClass.F4,
            reel=reel_name,
            message=(
                f"planned {expected_count} picture items, found "
                f"{actual_count} on the timeline"),
            severity="error",
            detail={
                "expected": expected_count,
                "actual": actual_count,
            },
        ))

    # Per-speaker duration comparison
    planned_by_speaker: Dict[str, float] = {}
    track_to_speaker: Dict[int, str] = {}
    for p in planned_placements:
        speaker = p.speaker or "unknown"
        planned_by_speaker[speaker] = (
            planned_by_speaker.get(speaker, 0.0)
            + (p.source_out - p.source_in))
        if speaker != "unknown":
            track_to_speaker[p.track_index] = speaker

    actual_by_speaker: Dict[str, float] = {}
    unmapped_duration: Dict[int, float] = {}
    track_names: Dict[int, str] = {}

    for item in actual_picture:
        if item.speaker:
            track_names[item.track_index] = item.speaker

        speaker = (by_source.get(item.source_file)
                   if by_source else None) or track_to_speaker.get(
                       item.track_index)
        if not speaker:
            # Fall back to timeline's track names if they match a planned speaker
            if item.speaker and item.speaker in planned_by_speaker:
                speaker = item.speaker
            else:
                unmapped_duration[item.track_index] = (
                    unmapped_duration.get(item.track_index, 0.0)
                    + item.duration_frames / fps)
                continue

        actual_by_speaker[speaker] = (
            actual_by_speaker.get(speaker, 0.0)
            + item.duration_frames / fps)

    for track_idx, dur in sorted(unmapped_duration.items()):
        name_str = f"named '{track_names[track_idx]}'" if track_names.get(track_idx) else "unnamed"
        findings.append(Finding(
            finding_class=FindingClass.F4,
            reel=reel_name,
            message=(
                f"Track V{track_idx} ({name_str}) has {dur:.2f}s of video, "
                f"but could not be mapped to any speaker in the plan."
            ),
            severity="error",
            detail={
                "track": track_idx,
                "unmapped_duration": round(dur, 2)
            },
        ))

    for speaker in set(list(planned_by_speaker) + list(actual_by_speaker)):
        planned_s = planned_by_speaker.get(speaker, 0.0)
        actual_s = actual_by_speaker.get(speaker, 0.0)
        # Allow small tolerance for frame rounding (F1's one frame per clip)
        tolerance = max(0.5, actual_count * 0.05)
        if abs(planned_s - actual_s) > tolerance:
            findings.append(Finding(
                finding_class=FindingClass.F4,
                reel=reel_name,
                message=(
                    f"{speaker}: planned {planned_s:.2f}s, got "
                    f"{actual_s:.2f}s (delta {actual_s - planned_s:+.2f}s)"),
                severity="error",
                detail={
                    "speaker": speaker,
                    "planned_seconds": round(planned_s, 2),
                    "actual_seconds": round(actual_s, 2),
                    "delta_seconds": round(actual_s - planned_s, 2),
                },
            ))

    return findings


def check_plan_describes_timeline(reel_name: str,
                                 keep_ranges: Sequence[Tuple[float, float]],
                                 total_frames: int,
                                 fps: float,
                                 card_frames: int = 0,
                                 held_frames: int = 0,
                                 ) -> List[Finding]:
    """Refuse F4 when the RE-DERIVED plan is not the plan that was built.

    The picture plan this verifier grades against is not read from a
    file: `_derive_plan_from_master` recomputes it by calling
    `reel_build.redundant_takes` and `keep_ranges` with TODAY'S code.
    That is the plan the build used only while the code has not moved,
    and on the captain's nineteen it had.  `MIN_TAKE_SECONDS` was
    removed from `reel_build` in 890a61b at 22:58 on 2026-09-05; the
    nineteen were built at 14:46 the same day, eight hours earlier.
    Today's cut rule therefore finds retakes the build never cut - and
    on reel 06 loses one it did - so the derived plan lays down a
    different number of picture items over a different number of frames.

    F4 then reported that difference as clips the builder had DROPPED:
    "planned 6 picture items, found 4 on the timeline", ten such
    findings across five reels, every one of them the arithmetic
    consequence of a cut set that never existed.  That reads as an
    encoding defect and is not one - it is this tool comparing a reel to
    a plan that never produced it, which is the same failure PR #522 and
    PR #568 already refuse at the file and the caption level.

    **The test is frame-exact and carries no tolerance.** The plan's
    length in frames is what `reel_build.placements` lays down -
    ``int(round(end * fps)) - int(round(start * fps))`` summed over the
    keep ranges - and that is the same integer arithmetic the builder
    ran, not an approximation of it. Measured on the nineteen: the two
    numbers are EQUAL on all fourteen reels whose item counts agree and
    differ on all five that do not. There is no band between them to
    choose, so none is chosen.

    A build that genuinely dropped a clip also fails this test, and says
    so more precisely than F4 did - it names the frames that are missing
    rather than inferring a cause from an item count. The gate is not
    weakened: the same builds fail, with a truer message.

    `card_frames` is what the reel's full-frame elements occupy, in the
    same integer frames. They are part of the reel's length and none of
    them comes from a keep range, so a reel carrying one is longer than
    its ranges by exactly this - and left out, EVERY such reel would
    refuse F4 as "not the plan that built this reel". Zero for every
    project that declares none.

    `held_frames` is the declared FREEZE tail
    (`library/tools/reel_ending.py`), and it is the same case for the
    same reason: the hold is picture the viewer watches, it comes from
    no keep range, and left out it refuses every reel that declares
    one. Zero for every project that declares none.

    `total_frames` is the PICTURE extent - V1/V2 to the timeline origin -
    never the whole-timeline GetEndFrame minus GetStartFrame. The plan
    under test describes picture only, and the caption layer it does not
    describe is laid by different arithmetic: `frame_utils.span_frames`
    rounds each record edge on reel seconds, while the sum above rounds
    each range edge on master seconds, and the two projections of the
    same seconds diverge by a frame at unlucky fractions. Measured
    2026-09-08: a single 79.354s keep range lays 1902 picture frames
    while a caption closing exactly at the reel end records to frame
    1903, so the timeline carries 1903 on a build whose picture tiled
    exactly (reel 13, refused as +1f/+0.04s). The caller (`verify_reel`)
    passes the picture extent; a caller passing the all-tracks extent
    reintroduces that refusal. No tolerance is added - a genuinely
    dropped or lengthened picture clip still moves the picture extent by
    its frames and still refuses, frame-exact.
    """
    # `round()` on a float returns an int, so this IS
    # `reel_build.placements`' own `int(round(x * fps))` per range edge -
    # the builder's arithmetic, not an approximation of it.
    planned_frames = sum(
        round(end * fps) - round(start * fps)
        for start, end in keep_ranges) + int(card_frames) + int(held_frames)
    if planned_frames == total_frames:
        return []
    delta = total_frames - planned_frames
    return [Finding(
        finding_class=FindingClass.PLAN_MISMATCH,
        reel=reel_name,
        message=(
            f"the plan re-derived for this reel lays down "
            f"{planned_frames} frames and the picture on the timeline "
            f"carries "
            f"{total_frames} ({delta:+d} frames, "
            f"{delta / fps:+.2f}s), so it is not the plan that built "
            f"this reel - F4 is REFUSED rather than reporting the "
            f"difference as dropped clips"),
        severity="error",
        detail={
            "planned_frames": planned_frames,
            "timeline_frames": total_frames,
            "delta_frames": delta,
            "delta_seconds": round(delta / fps, 3),
            "keep_ranges": [(round(a, 3), round(b, 3))
                            for a, b in keep_ranges],
            "refused": "F4",
        },
    )]


def check_caption_duration(reel_name: str,
                           planned_captions: Sequence[PlannedCaption],
                           actual_captions: Sequence[TimelineItem],
                           fps: float,
                           ) -> List[Finding]:
    """F2: Check that each caption card's placed duration matches plan.

    The audit found 567 of 575 cards placed one frame shorter than their
    rendered .mov - the same off-by-one as F1.

    **Cards are paired on START FRAME, never on list position.** This
    used to read `actual = actual_captions[i]` under a comment saying it
    matched by start frame - the comment described the right check and
    the code did a different one. It only agrees with itself while the
    two lists are the same length, and they are not: measured on the
    captain's nineteen, 832 cards were planned and 763 placed. After a
    reel's first unplaced card every remaining pair compared one card's
    plan against a DIFFERENT card's item, which produced 701 findings
    and a correlation between planned and placed duration of 0.027 -
    reading exactly like a placement defect. It was not one: placement
    is faithful, measured item by item against the rendered files on
    reel 01 and equal on 27 of 28.

    `PAIRING_TOLERANCE_FRAMES` is a MECHANICAL identity tolerance, not a
    quality threshold - it decides which item IS this card, not whether
    the card is good. It is two frames because the audit found 566 of
    575 on exactly the planned frame and the rest one frame off, and
    because `start_seconds` is rounded into frames on both sides.

    A planned card with no item near its start frame is no longer
    absorbed into a shift; it is reported as F14.

    **Cards are graded as the SEGMENTS the builder places, never one by
    one.** Step 4.01 groups a block's words into several cards
    (`subtitle_entries`) and step 4.05 renders one overlay per block
    (`generate_subtitle_props_per_block`), sequencing that block's cards
    INSIDE the file - so `build_reel_timeline` places one V3 item per
    block, spanning its first card's start to its last card's end. That
    is the unit the pipeline promises: `compile_manifest` refuses a
    build whose per-block segment does not span the block's captions.
    Pairing each card against the block-spanning item compares a card's
    duration to its container's, which fires F2 on every block's first
    card (delta = the rest of the block) and F14 on every other card -
    one finding per planned card - on a correct build. Measured 2026-09-07
    on a freshly rebuilt reel 1: 34 planned cards, 34 such findings.

    So the expected side is grouped by block first, and each expected
    SEGMENT (min card start to max card end, in the same frames
    arithmetic the builder places with) is paired to the placed item at
    its start frame. Per-card timing inside the file is the renderer's,
    not the timeline's: the item cannot express it, so the check grades
    what the timeline decides - position and span - and grading the
    container against the container is what keeps this a real gate.
    A short, long, shifted or missing segment still fails.

    The segment span is rounded PER EDGE - `span_frames(start, end)` -
    never `round((end - start) * fps)`.  Two blocks that abut in
    seconds share one edge and must share it in frames: duration
    rounding lays them overlapping by a frame whenever both fractions
    round the same way, and Resolve trims one off the later item.
    Measured 2026-09-08 on reel 07 blocks 22/23, abutting at 62.374s:
    asked [1442,1496) over [1495,1529), read back 33 of 34 planned -
    the lone F2 of the rebuild, deterministic across both attempts.
    The builder (`build_reel_timeline`) places this same span, read
    from the same helper, so placer and check agree by construction
    and the gate stays exact: a short, long or shifted segment still
    draws F2 with a non-zero delta.
    """
    findings: List[Finding] = []

    # The unit the builder places: one segment per spine block, spanning
    # the block's cards. Grouped here rather than read from the render,
    # because the plan is what is being graded and the render is what
    # the pairing below already reads off the timeline.
    segments: List[dict] = []
    by_block: Dict[object, List[int]] = {}
    for i, cap in enumerate(planned_captions):
        by_block.setdefault(cap.block_position, []).append(i)
    for block, indices in by_block.items():
        cards = [planned_captions[i] for i in indices]
        start = min(c.start_seconds for c in cards)
        end = max(c.end_seconds for c in cards)
        # Per-edge span, the same `span_frames` the builder places -
        # never round((end - start) * fps), which overlaps an abutting
        # neighbour by a frame on adverse fractions (reel 07, 2026-09-08).
        span_start, span_end = span_frames(start, end, fps)
        segments.append({
            "block": block,
            "start_seconds": start,
            "frames": max(span_end - span_start, 1),
            "card_count": len(cards),
            "text": cards[0].text,
            "first_index": min(indices),
        })
    segments.sort(key=lambda s: (s["start_seconds"], s["first_index"]))

    unclaimed = list(enumerate(actual_captions))
    for n, seg in enumerate(segments):
        want = seg["start_seconds"] * fps
        best = None
        for position, (_, item) in enumerate(unclaimed):
            gap = abs(item.start_frame - want)
            if gap <= PAIRING_TOLERANCE_FRAMES and (
                    best is None or gap < best[0]):
                best = (gap, position)
        if best is None:
            findings.append(Finding(
                finding_class=FindingClass.F14,
                reel=reel_name,
                message=(
                    f"caption segment {n+1} (block {seg['block']}, "
                    f"{seg['card_count']} cards starting "
                    f"'{seg['text'][:30]}') was planned at "
                    f"{seg['start_seconds']:.2f}s ({want:.0f}f) and no item "
                    f"is placed within {PAIRING_TOLERANCE_FRAMES} frames "
                    f"of it - it was planned and never placed"),
                severity="error",
                detail={"segment_index": n,
                        "block": None if seg["block"] is None
                        else str(seg["block"]),
                        "card_count": seg["card_count"],
                        "planned_start_seconds": round(
                            seg["start_seconds"], 3),
                        "planned_start_frame": round(want),
                        "planned_frames": seg["frames"],
                        "text": seg["text"][:60]},
            ))
            continue
        _, (_, actual) = best[0], unclaimed.pop(best[1])
        delta = actual.duration_frames - seg["frames"]
        if delta != 0:
            findings.append(Finding(
                finding_class=FindingClass.F2,
                reel=reel_name,
                message=(
                    f"caption segment {n+1} (block {seg['block']}, "
                    f"{seg['card_count']} cards starting "
                    f"'{seg['text'][:30]}') planned "
                    f"{seg['frames']} frames, placed {actual.duration_frames} "
                    f"(delta {delta:+d})"),
                severity="error",
                detail={
                    "segment_index": n,
                    "block": None if seg["block"] is None
                    else str(seg["block"]),
                    "card_count": seg["card_count"],
                    "planned_frames": seg["frames"],
                    "actual_frames": actual.duration_frames,
                    "delta": delta,
                    "text": seg["text"][:60],
                },
            ))

    return findings


def check_caption_reference(reel_name: str,
                            planned_captions: Sequence[PlannedCaption],
                            actual_captions: Sequence[TimelineItem],
                            unavailable: Optional[str] = None,
                            ) -> List[Finding]:
    """NO-REFERENCE: a caption check with nothing to compare against.

    The guard this replaces was `if plan.captions and
    timeline.caption_items:`, and `plan.captions` was `()` on every run
    the verifier ever made.  So F2 never ran, and the report printed
    "captions expected 0, actual 762" beside a PASS.  A gate that cannot
    fail is worse than no gate, because it reads as coverage
    (AGENTS.md 10.4).

    A reel with no captions on EITHER side is not a defect - that is a
    reel built with `--skip-captions`, and it has nothing to say about
    caption timing.  A disagreement about whether captions exist at all
    is always a defect, in whichever direction it points: cards on the
    timeline the plan never asked for cannot be checked, and cards the
    plan asked for that are not on the timeline were not built.
    """
    # "Could not be asked" is not "has none", and the two must never
    # collapse. This is the branch that stops the gate going quiet when
    # whatever produces the expected cards moves or is deleted.
    if unavailable:
        return [Finding(
            finding_class=FindingClass.NO_REFERENCE,
            reel=reel_name,
            message=(f"the expected caption cards could not be derived, so "
                     f"F2, F5, F6 and F7 have no reference set and are "
                     f"REFUSED: {unavailable}"),
            severity="error",
            detail={"planned": None, "actual": len(actual_captions),
                    "direction": "unavailable", "reason": unavailable},
        )]

    if not planned_captions and not actual_captions:
        return []
    if planned_captions and actual_captions:
        return []

    if actual_captions:
        message = (
            f"{len(actual_captions)} caption cards are on the timeline "
            f"and the plan derived NONE, so F2, F5, F6 and F7 have no "
            f"reference set. They are REFUSED rather than skipped: an "
            f"empty expected side used to disable them silently and "
            f"report a pass")
        detail = {"planned": 0, "actual": len(actual_captions),
                  "direction": "timeline_only"}
    else:
        message = (
            f"the plan derived {len(planned_captions)} caption cards and "
            f"the timeline carries NONE, so the captions were planned and "
            f"never built")
        detail = {"planned": len(planned_captions), "actual": 0,
                  "direction": "plan_only"}

    return [Finding(
        finding_class=FindingClass.NO_REFERENCE,
        reel=reel_name,
        message=message,
        severity="error",
        detail=detail,
    )]


def _clip_to_reel(seg_start: float,
                  seg_end: float,
                  keep_ranges: Sequence[Tuple[float, float]],
                  lead_seconds: float = 0.0,
                  ) -> List[Tuple[float, float]]:
    """The parts of a master interval that the reel PLAYS, in reel seconds.

    A transcript row is not all-or-nothing against a reel. It can begin
    before the reel and end inside it, begin inside and run past the end,
    or have an interior cut taken out of its middle - and in every one of
    those cases the part inside is real speech a viewer hears.

    Returns one piece per keep range the interval touches, or an empty
    list for an interval the reel does not play at all, which is the only
    honest reading of "not in this reel".

    `reel_build.reel_time` stays the sole owner of the arithmetic. Each
    piece lies wholly inside one range, so its start is read inclusively
    and its end with `at_end=True` - a range is `[start, end)` and a
    piece ending exactly on a range edge would otherwise map to None.

    `lead_seconds` is what a HEAD full-frame card pushes the reel down
    by. The callers compare these pieces against items READ OFF THE
    TIMELINE, whose frames already include the card, so a lead left out
    here reports every second of speech as uncaptioned by exactly the
    card's length (`library/tools/full_frame_element.py`).
    """
    from library.tools.reel_build import reel_time

    pieces: List[Tuple[float, float]] = []
    for range_start, range_end in keep_ranges:
        low = max(range_start, seg_start)
        high = min(range_end, seg_end)
        if high <= low:
            continue
        at_low = reel_time(low, keep_ranges, lead_seconds=lead_seconds)
        at_high = reel_time(high, keep_ranges, at_end=True,
                            lead_seconds=lead_seconds)
        if at_low is None or at_high is None or at_high <= at_low:
            continue
        pieces.append((at_low, at_high))
    return pieces


MAX_WORD_SECONDS = 3.0
"""The longest a single word's timing may span and still count as speech.

Measured on the captain's field test: 8,492 words on bound rows run
0.02-2.65s (p99.9 0.92s), while the transcript's 11 straddling-residue
rows are single words spanning 1.26-62.63s - WhisperX stretching the
last word across the silence before the speaker resumes (F8's docstring
names the 19.04s and 34.47s cases). A word longer than every one of
those 8,492 is the aligner bridging silence, not speech, and counting
it second-for-second reports pauses between the other speaker's cards
as uncaptioned speech: reel 10's 34.13s "audits" read as 3.6s of Craig
with no caption, where one talk-over word is all he says in 34s.

Such words are EXCLUDED from the speech measure and REPORTED, never
silently skipped (AGENTS.md 10.4): the warning names how many and
their total span, so a future transcript with genuinely slower speech
shows up as a warning rather than passing quietly.
"""


def check_caption_coverage(reel_name: str,
                           transcript_segments: Sequence[dict],
                           caption_cards: Sequence[dict],
                           keep_ranges: Sequence[Tuple[float, float]],
                           fps: float,
                           lead_seconds: float = 0.0,
                           ) -> List[Finding]:
    """F5: Measure seconds of real speech with no caption over it.

    Split by cause as the audit did:
    - Straddling segments (excluded by bound_segments) - a real gap, F5
    - Frame-quantisation residue (~26ms per word) - NOT a defect

    The 24.6s residue from the audit is frame quantisation at card edges
    spread over ~900 words and is not reported as a finding.

    **A row is CLIPPED to the reel, never dropped for reaching past it.**
    This used to map the row's own `timeline_start` and `timeline_end`
    through `reel_time` and `continue` the moment either came back None -
    which is every row that begins before the reel or ends after it, and
    every row an interior cut runs through. Those are precisely the
    straddling rows the paragraph above says this check exists to count,
    so it was declining to look at its own subject: measured on the
    captain's nineteen approved reels it reported **29.2s** of the
    **170.1s** actually uncaptioned, and skipped 51 rows carrying 364.7s
    of in-reel overlap. Two of those rows exist per reel by construction,
    at the two boundaries, so this was never an edge case.

    The row is therefore intersected with each keep range and each piece
    mapped separately. `reel_time` is still the only arithmetic - read
    inclusively at a piece's start and with `at_end=True` at its end,
    because a range is half-open and a piece ending exactly on a range
    edge would otherwise fall outside every range. Mapping the two raw
    endpoints was wrong even when it returned numbers: a row an interior
    cut runs through mapped to one contiguous reel interval spanning the
    removed take, so seconds the builder had cut out counted as speech.

    **Seconds are WORD SPANS minus caption coverage, because a row's
    span is not speech.** WhisperX bridges a silent stretch into the row
    beside it - 657.4-695.9 is 38.5s carrying nineteen words, and
    606.3-614.4 is 8.1s carrying the single word "Yeah". This check used
    to subtract caption coverage from the ROW ENVELOPE and report the
    remainder as "speech with no caption", which is what its class
    docstring, its message and AGENTS.md all say it measures and is not
    what it measured: on the captain's nineteen it reported 95.1s of
    uncaptioned speech across twelve reels, up to 32.3s on one, where the
    silence inside the rows was the overwhelming majority of it.

    The rows carry `words` with per-word start and end, so each word is
    clipped to the reel separately and the uncovered part of a WORD is
    what is counted. A row with no word list falls back to its envelope
    and that fallback is REPORTED, never silent: a transcript that
    stopped carrying word timings must not quietly turn this check back
    into the one it replaced (AGENTS.md 10.4).

    **A word longer than `MAX_WORD_SECONDS` is the aligner bridging
    silence, not speech, and is not counted.** The word-level fix above
    assumed each word's span is speech; reel 10's "audits" is one word
    spanning 34.13s, of which ~0.4s is Craig talking and the rest is
    Akshita's story. Counting it reported 3.6s of "speech with no
    caption" where the transcript attributes no words to anyone - pauses
    between her cards. Excluded words are REPORTED in a warning naming
    how many and their total span, so the narrowing is visible rather
    than a quieter gate.

    **Seconds are summed PER ROW, so two uncaptioned rows overlapping each
    other count twice.** This is a two-mic recording and both tracks
    carry rows, so the total is an upper bound on wall-clock uncaptioned
    time rather than equal to it. That is deliberate: the finding names
    rows, and halving a row because the other speaker was also
    uncaptioned would report less speech missing than is missing.

    This measures the INSTRUMENT, not the picture. Whether caption cards
    should be derived from unanchored rows at all is a separate question
    that changes what a viewer sees, and nothing here answers it.
    """
    findings: List[Finding] = []
    if not transcript_segments or not keep_ranges:
        return findings

    from library.tools.reel_build import reel_time

    # Compute speech coverage from caption cards
    caption_intervals = []
    for card in caption_cards:
        c_start = card.get("reel_start", card.get("start_seconds", 0))
        c_end = card.get("reel_end", card.get("end_seconds", 0))
        caption_intervals.append((c_start, c_end))
    caption_intervals.sort()

    # Measure speech seconds
    straddling_uncaptioned = 0.0
    residue_uncaptioned = 0.0

    rows_without_words = 0
    stretched_words = 0
    stretched_span = 0.0
    stretched_uncovered = 0.0

    for segment in transcript_segments:
        has_item_id = bool(segment.get("resolve_item_id"))
        seg_start = float(segment.get("timeline_start", 0))
        seg_end = float(segment.get("timeline_end", 0))

        # The speech this row carries. Word intervals when the row has
        # them; its envelope only when it does not, and that is counted
        # and reported rather than passed off as a word measurement.
        spoken = [(a, b) for a, b, _ in _row_words(segment)]
        envelope_only = not spoken
        stretched: list = []
        if envelope_only:
            spoken = [(seg_start, seg_end)]
        else:
            measurable = []
            for a, b in spoken:
                if b - a > MAX_WORD_SECONDS:
                    stretched.append((a, b))
                else:
                    measurable.append((a, b))
            spoken = measurable

        uncaptioned = 0.0
        played = 0.0
        for spoken_start, spoken_end in spoken:
            for reel_start, reel_end in _clip_to_reel(
                    spoken_start, spoken_end, keep_ranges, lead_seconds):
                # How much of this piece is captioned?
                captioned = 0.0
                for c_start, c_end in caption_intervals:
                    overlap_start = max(reel_start, c_start)
                    overlap_end = min(reel_end, c_end)
                    if overlap_end > overlap_start:
                        captioned += overlap_end - overlap_start

                played += reel_end - reel_start
                uncaptioned += (reel_end - reel_start) - captioned

        # A row the reel does not play cannot be missing a caption on it,
        # and must not be counted as a row measured on its envelope.
        if envelope_only and played > 0:
            rows_without_words += 1

        if uncaptioned > 0.01:  # more than 10ms
            if not has_item_id:
                straddling_uncaptioned += uncaptioned
            else:
                residue_uncaptioned += uncaptioned

        # A stretched word the reel plays under no card is speech the
        # gate declined to measure, and that is said. One fully covered
        # by cards, or not played at all, leaves nothing uncaptioned
        # and is not reported: a warning on every reel a stretched word
        # merely touches would be noise, not coverage.
        for stretch_start, stretch_end in stretched:
            stretch_uncovered = 0.0
            for reel_start, reel_end in _clip_to_reel(
                    stretch_start, stretch_end, keep_ranges, lead_seconds):
                captioned = 0.0
                for c_start, c_end in caption_intervals:
                    overlap_start = max(reel_start, c_start)
                    overlap_end = min(reel_end, c_end)
                    if overlap_end > overlap_start:
                        captioned += overlap_end - overlap_start
                stretch_uncovered += (reel_end - reel_start) - captioned
            if stretch_uncovered > 0.01:
                stretched_words += 1
                stretched_span += stretch_end - stretch_start
                stretched_uncovered += stretch_uncovered

    # Straddling uncaptioned speech is a real defect (F5)
    if straddling_uncaptioned > 0.5:
        findings.append(Finding(
            finding_class=FindingClass.F5,
            reel=reel_name,
            message=(
                f"{straddling_uncaptioned:.1f}s of speech from straddling "
                f"segments has no caption"),
            severity="error",
            detail={
                "straddling_seconds": round(straddling_uncaptioned, 1),
                "residue_seconds": round(residue_uncaptioned, 1),
                "total_uncaptioned_seconds": round(
                    straddling_uncaptioned + residue_uncaptioned, 1),
            },
        ))

    if rows_without_words:
        findings.append(Finding(
            finding_class=FindingClass.F5,
            reel=reel_name,
            message=(
                f"{rows_without_words} transcript row(s) carried no word "
                f"timings, so their whole envelope was counted as speech "
                f"- the seconds above are an upper bound for those rows"),
            severity="warning",
            detail={"rows_without_word_timings": rows_without_words},
        ))

    if stretched_words:
        findings.append(Finding(
            finding_class=FindingClass.F5,
            reel=reel_name,
            message=(
                f"{stretched_words} word(s) spanning longer than "
                f"{MAX_WORD_SECONDS:.1f}s were not counted as speech "
                f"({stretched_span:.1f}s of word span, "
                f"{stretched_uncovered:.1f}s of it with no caption over "
                f"it) - the aligner stretches a word across silence, "
                f"and counting it reports pauses as uncaptioned speech"),
            severity="warning",
            detail={"stretched_words": stretched_words,
                    "stretched_span_seconds": round(stretched_span, 1),
                    "stretched_uncovered_seconds": round(
                        stretched_uncovered, 1)},
        ))

    # Residue is NOT a defect - but report it for visibility if large
    # enough that someone might notice it (> 5s per reel, arbitrary)

    return findings


def check_caption_overlaps(reel_name: str,
                           caption_cards: Sequence[dict],
                           fps: float,
                           ) -> List[Finding]:
    """F6: Find caption cards that overlap on a single subtitle track.

    The audit found 15 overlapping pairs.  Two cards covering the same
    seconds (mic bleed captioned under both speakers) cannot coexist on
    one V3 track - Resolve trims the later card's head.
    """
    findings: List[Finding] = []
    if len(caption_cards) < 2:
        return findings

    sorted_cards = sorted(caption_cards,
                          key=lambda c: c.get("reel_start",
                                              c.get("start_seconds", 0)))
    for i in range(len(sorted_cards) - 1):
        curr = sorted_cards[i]
        nxt = sorted_cards[i + 1]
        curr_end = curr.get("reel_end", curr.get("end_seconds", 0))
        nxt_start = nxt.get("reel_start", nxt.get("start_seconds", 0))
        overlap = curr_end - nxt_start
        if overlap > 0.001:  # more than 1ms
            overlap_frames = int(round(overlap * fps))
            findings.append(Finding(
                finding_class=FindingClass.F6,
                reel=reel_name,
                message=(
                    f"caption cards overlap by {overlap_frames} frames "
                    f"({overlap:.3f}s): '{curr.get('text', '')[:30]}' and "
                    f"'{nxt.get('text', '')[:30]}'"),
                severity="error",
                detail={
                    "overlap_frames": overlap_frames,
                    "overlap_seconds": round(overlap, 3),
                    "card_a_text": curr.get("text", "")[:60],
                    "card_b_text": nxt.get("text", "")[:60],
                },
            ))
    return findings


def _card_end(card) -> float:
    return card.get("reel_end", card.get("end_seconds", 0))


def _hang_limit_seconds(word_count: int) -> float:
    """How long a caption of this many words may stay on screen.

    The ONE expression of the hang bound, shared by the plan-side and
    the placed-side F15 rather than restated in each: a card of N words
    should not exceed ``N * max_gap`` seconds, and never less than the
    legibility floor. ``max_gap`` (1.0 s) is the pipeline's silence
    threshold - if two words were further apart they would never have
    been grouped together - and 0.7 s is the minimum display duration
    the grouping extends a short card to. Measured across 92 real cards:
    0 false positives, highest observed per-word rate 0.960 s.
    """
    # max_gap: the pipeline's silence threshold (step_4_01 _feasible
    # rejects groups spanning a wider gap).
    MAX_GAP = 1.0
    MIN_CAPTION_DISPLAY = 0.7
    return max(MIN_CAPTION_DISPLAY, word_count * MAX_GAP)


def check_caption_hangs(reel_name: str,
                        caption_cards: Sequence[dict],
                        fps: float) -> List[Finding]:
    """F15 (plan side): A card must not hang far past the speech it belongs to.

    The primary defense is in the grouping (step_4_01 ``split_into_groups``),
    which caps a card's end at `last_word_end + max_gap` using the actual
    word timestamps.  The verifier has no per-word timestamps on placed
    cards, so this check uses a word-count heuristic: a card of N words
    should not exceed ``N * max_gap`` seconds.  ``max_gap`` (1.0 s) is the
    pipeline's silence threshold - if two words were further apart they
    would never have been grouped together.

    Measured across 92 real cards from the captain's projects: 0 false
    positives.  The highest per-word rate observed is 0.960 s, leaving
    a 0.040 s margin.  This makes the check a secondary safety net, not
    the primary cap.

    This grades the PLAN's cards.  What the viewer sees is what was
    PLACED, and a span correct in the plan and wrong on the timeline
    passes this - reel 05's 239-frame hang did exactly that.  The placed
    half is :func:`check_placed_caption_hangs`, which `verify_reel`
    runs beside this one.
    """
    findings: List[Finding] = []
    for card in caption_cards:
        start = card.get("reel_start", 0)
        end = card.get("reel_end", 0)
        text = card.get("text", "")
        word_count = len(text.split())
        if word_count == 0:
            continue
        # Detection heuristic: N words * max_gap.  The grouping cap uses
        # the actual last-word-end, which is tighter and cannot cut speech.
        max_duration = _hang_limit_seconds(word_count)
        if end - start > max_duration:
            findings.append(Finding(
                finding_class=FindingClass.F15,
                reel=reel_name,
                message=(
                    f"caption card '{text}' hangs far past its speech "
                    f"({end - start:.2f}s for {word_count} words, "
                    f"limit {max_duration:.1f}s)"
                ),
            ))
    return findings


def check_placed_caption_hangs(
        reel_name: str,
        caption_cards: Sequence[dict],
        placed_items: Sequence[TimelineItem],
        fps: float) -> List[Finding]:
    """F15 (placed side): a placed caption item must not hang past its words.

    The blind spot this closes: :func:`check_caption_hangs` grades the
    plan's cards, so a span that is correct in the plan and wrong on the
    timeline passes.  Reel 05's card at frame 1329 ("yeah so ranking
    tells google") was five words in the plan and 239 frames - nearly ten
    seconds - on the timeline, under a green verdict.

    Each placed item is paired to its planned SEGMENT by start frame -
    the same ``PAIRING_TOLERANCE_FRAMES`` identity F2 already uses, and
    the same per-block grouping (a block's cards sequenced inside one
    placed overlay is the unit the builder places).  The DURATION is read
    off the placed item; the WORD BUDGET comes from the paired segment's
    cards, because a placed overlay carries no text - and the bound is
    :func:`_hang_limit_seconds`, the same expression as the plan side,
    not a second opinion about how long words may last.

    Reports DIVERGENCE only: a pair whose planned segment already hangs
    is the plan side's finding, and reporting it here too would count
    one defect twice.  An item with no planned segment near its start,
    and a segment with no item, are F14/F2's to report, not this one's.
    """
    findings: List[Finding] = []
    segments: List[dict] = []
    by_block: Dict[object, List[dict]] = {}
    for card in caption_cards:
        by_block.setdefault(card.get("block_position"), []).append(card)
    for block, cards in by_block.items():
        starts = [c.get("reel_start", c.get("start_seconds", 0))
                  for c in cards]
        ends = [c.get("reel_end", c.get("end_seconds", 0)) for c in cards]
        words = sum(len(str(c.get("text", "")).split()) for c in cards)
        segments.append({
            "block": block,
            "start_seconds": min(starts),
            "planned_seconds": max(ends) - min(starts),
            "words": words,
            "text": str(cards[0].get("text", "")),
        })
    segments.sort(key=lambda s: s["start_seconds"])

    unclaimed = list(placed_items)
    for n, seg in enumerate(segments):
        if seg["words"] == 0:
            continue
        want = seg["start_seconds"] * fps
        best = None
        for position, item in enumerate(unclaimed):
            gap = abs(item.start_frame - want)
            if gap <= PAIRING_TOLERANCE_FRAMES and (
                    best is None or gap < best[0]):
                best = (gap, position)
        if best is None:
            continue
        item = unclaimed.pop(best[1])
        placed_seconds = item.duration_frames / fps
        limit = _hang_limit_seconds(seg["words"])
        if seg["planned_seconds"] > limit:
            # The plan already hangs here - the plan side's finding.
            # Saying it again would count one defect twice.
            continue
        if placed_seconds > limit:
            findings.append(Finding(
                finding_class=FindingClass.F15,
                reel=reel_name,
                message=(
                    f"placed caption segment {n + 1} (block "
                    f"{seg['block']}, {seg['words']} words starting "
                    f"'{seg['text'][:30]}') runs {placed_seconds:.2f}s "
                    f"on the timeline against a plan of "
                    f"{seg['planned_seconds']:.2f}s "
                    f"(limit {limit:.1f}s) - correct in the plan, "
                    f"hanging on the timeline"),
                severity="error",
                detail={
                    "segment_index": n,
                    "block": None if seg["block"] is None
                    else str(seg["block"]),
                    "words": seg["words"],
                    "planned_seconds": round(seg["planned_seconds"], 3),
                    "placed_seconds": round(placed_seconds, 3),
                    "placed_frames": item.duration_frames,
                    "limit_seconds": round(limit, 3),
                    "text": seg["text"][:60],
                },
            ))
    return findings


_CURRENT_SPAN_RE = re.compile(r"^\d+-\d+$")
_CURRENT_DIGEST_RE = re.compile(r"^[0-9a-f]{8}$")


def _is_current_caption_shape(parts: Sequence[str]) -> bool:
    """The current producer's `sub_<speaker>_<clip>_<span>_<digest>`.

    Five fields, the span a millisecond pair and the digest 8 hex -
    which is also what separates it from the oldest producer's
    `sub_<timeline>_<speaker>_<text>_<digest>`: a text slug is never
    shaped like a span beside a hex digest.
    """
    return (len(parts) == 5
            and _CURRENT_SPAN_RE.match(parts[3]) is not None
            and _CURRENT_DIGEST_RE.match(parts[4]) is not None)


def _grade_current_caption_slugs(parts: Sequence[str]) -> List[str]:
    """Grade the speaker and clip slugs of a current-shape name.

    The producer's `slug` breaks on word boundaries and strips
    trailing dashes, so a component ending on a dash was cut, and one
    ending `-<single letter>` (other than `a` or `I`, and never a
    digit) ends inside a word.  The span and digest carry no words to
    cut; they must simply parse, because a name failing the shape is
    not this producer's output.
    """
    reasons: List[str] = []
    for label, comp in (("speaker", parts[1]), ("clip", parts[2])):
        if comp.endswith("-"):
            reasons.append(
                f"{label} slug {comp!r} ends on a dash - it was cut, "
                f"and a correct slug never ends with one")
        elif re.search(r"-[b-hj-z]$", comp):
            reasons.append(
                f"{label} slug {comp!r} ends inside a word")
    if not parts[1] or not parts[2]:
        reasons.append("a provenance slug is empty - an absence the "
                       "producer names, never omits")
    return reasons


def check_caption_slugs(reel_name: str,
                        caption_items: Sequence[TimelineItem],
                        ) -> List[Finding]:
    """F16: a placed caption name must not break mid-word.

    Reel 05's caption names broke as `invisible-o`, `envisio`, `goo` and
    `goog` - every truncated card on that reel measured at the slug
    length limit, because the slug hard-cut at a character count.  A
    caption's TEXT never truncates (4.01 joins whole word tokens, pinned
    by `test_no_card_from_a_reel_spine_ever_splits_a_word`), so the name
    is the one place a mid-word cut can reach a viewer - it is what an
    editor reads on the V3 track and in the media pool.

    Three shapes, because the producer changed twice and all three meet
    on real timelines:

    - The CURRENT producer emits
      `sub_<speaker>_<clip>_<span>_<digest>` - provenance plus content,
      no timeline anywhere, because variant timelines sharing words
      share the file.  The speaker and clip slugs are graded the way
      the old text slug was: a correct `slug` never ends a component
      on a dash and never ends one inside a word, and the span
      (`<ms>-<ms>`) and digest (8 hex) must parse - a name that fails
      the shape is not this producer's output and is said so rather
      than graded as a truncation.
    - The PREVIOUS producer emitted
      `sub_<timeline>_<speaker>_<block>_<span>_<digest>`.  Its timeline
      component must equal what `slug` returns for this reel's own
      name today: a file from another reel placed here is the old
      overwrite made visible.  A correct producer agrees with its
      check by construction; a hard cut at the limit does not.
    - The OLDEST producer emitted
      `sub_<timeline>_<speaker>_<text>_<digest>`, carrying the caption
      TEXT slugged into the filename.  Its text slug is graded on its
      own shape, with no source text and no limit literal: a component
      ending in a dash was cut (a correct slug never ends with one -
      the producer strips them), and one ending in `-<single letter>`
      other than `a` or `I` ends inside a word, because no English
      word is one letter long besides those two.  Digits are excluded
      - a lone `3` is a number, not a fragment.  What this half cannot
      see is said plainly: a multi-letter fragment (`envisio`, `goog`)
      is indistinguishable from a complete word without the source
      text, so the timeline - not the verifier - is where that half is
      closed, by the producer never cutting one.

    One finding per reel, listing every offender: fifty-six copies of
    one sentence is a report nobody reads.  Names outside every known
    segment shape are skipped - an unparseable V3 name is F11's
    unattributed-card territory, not a truncation.
    """
    from library.tools.subtitle_segment_id import slug

    want = slug(reel_name, "notimeline")
    bad: List[dict] = []
    for item in caption_items:
        stem = (item.name or "").rsplit(".", 1)[0]
        parts = stem.split("_")
        if len(parts) < 2 or parts[0] != "sub":
            continue
        reasons: List[str] = []
        if _is_current_caption_shape(parts):
            reasons.extend(_grade_current_caption_slugs(parts))
        elif len(parts) == 6:
            if parts[1] != want:
                reasons.append(
                    f"timeline slug {parts[1]!r} is not what the producer "
                    f"writes for this reel ({want!r})")
        elif len(parts) == 5:
            # The oldest producer carried the timeline too: a foreign
            # timeline slug here is the same overwrite made visible.
            if parts[1] != want:
                reasons.append(
                    f"timeline slug {parts[1]!r} is not what the producer "
                    f"writes for this reel ({want!r})")
            text_comp = parts[3]
            if text_comp.endswith("-"):
                reasons.append(
                    f"caption slug {text_comp!r} ends on a dash - it was "
                    f"cut, and a correct slug never ends with one")
            elif re.search(r"-[b-hj-z]$", text_comp):
                reasons.append(
                    f"caption slug {text_comp!r} ends inside a word")
        if reasons:
            bad.append({"name": item.name,
                        "start_frame": item.start_frame,
                        "reasons": reasons})
    if not bad:
        return []
    listed = ", ".join(f"{b['name']!r} at frame {b['start_frame']}"
                       for b in bad[:3])
    if len(bad) > 3:
        listed += f", +{len(bad) - 3} more"
    return [Finding(
        finding_class=FindingClass.F16,
        reel=reel_name,
        message=(
            f"{len(bad)} of {len(caption_items)} placed caption names "
            f"break mid-word: {listed}"),
        severity="error",
        detail={
            "items": len(bad),
            "items_total": len(caption_items),
            "want_timeline_slug": want,
            "offenders": bad[:10],
        },
    )]


def _is_sequential_turn(words: Sequence[dict]) -> bool:
    """Do these card-overlapping words contain a speaker TURN?

    True when the speakers' word hulls are DISJOINT in time - one
    speaker stops, the other starts - which is the one shape a
    regrouping can separate: the card can be split between them.  Two
    voices sounding at the same instant (mic bleed, or a talk-over
    like reel 5's 'about' under 'recommend you or your brand.') cannot
    be grouped apart, so that is not a card defect.

    Hulls, not pairs: in sustained simultaneous speech (reel 15's
    passage, both mics carrying 'yeah so ai is actually better' at
    once) the FIRST word of one speaker and the LAST of the other are
    trivially disjoint, while every instant between them carries both
    voices.  A pairwise rule reads that chorus as a turn; separability
    of the whole hulls does not.

    Decided on MASTER intervals, which are the measured values.  The
    reel mapping is monotone but does float arithmetic, so an exact
    abutment in master time can read as a hairline overlap in reel
    time - deciding here keeps a back-to-back turn a turn.
    """
    hulls: Dict[str, list] = {}
    for word in words:
        speaker = word["speaker"]
        start, end = word["master_start"], word["master_end"]
        if speaker in hulls:
            hulls[speaker][0] = min(hulls[speaker][0], start)
            hulls[speaker][1] = max(hulls[speaker][1], end)
        else:
            hulls[speaker] = [start, end]
    spans = list(hulls.values())
    for i, first in enumerate(spans):
        for second in spans[i + 1:]:
            if min(first[1], second[1]) > max(first[0], second[0]):
                return False
    return True


#: How much two reel-time intervals must overlap to count as overlapping.
#:
#: Float hygiene, not a judgement.  Both sides of the comparison are
#: DERIVED - card edges from word ends through `reel_time`, word pieces
#: from range edges through the same function - so an exact abutment in
#: the transcript can read as a ~1e-9s overlap here.  Reel 10's closing
#: card ends exactly where its closer begins; the dust made Akshita's
#: first closer word overlap the card by a nanosecond, and with the two
#: words 1085 master seconds apart the turn test read a hairline of
#: nothing as a speaker turn.  A microsecond is four orders below a
#: frame (41.7ms) - no render can express it - while real overlaps
#: (bleed, talk-over, turns) are milliseconds at the very least.
REEL_OVERLAP_EPSILON = 1e-6


def check_mixed_speakers(reel_name: str,
                         caption_cards: Sequence[dict],
                         transcript_segments: Sequence[dict],
                         keep_ranges: Sequence[Tuple[float, float]],
                         fps: float,
                         lead_seconds: float = 0.0) -> List[Finding]:
    """F17: A card must not mix two speakers.

    Maps each card's time span to the transcript's diarised word timings
    and reports any card whose span overlaps speech from more than one
    speaker.

    Pre-575, this fired 18 times across 19 reels - every firing was
    microphone bleed (one mic hearing the other speaker's words at the
    same instant), not two people talking at once.  575 cuts bleed at
    the spine block boundary so a block carries one speaker; this check
    catches any bleed that survives that cut.

    **Overlap is not mixing.**  Reel 5 of the rebuild carries a card of
    Akshita's words alone ('recommend you or your brand.') over Craig's
    simultaneous onset ('about') - every regrouping of those words
    overlaps him too, so failing the card fails correct output
    (AGENTS.md 10.4).  Only a SEQUENTIAL turn errors: one speaker's
    words then the other's, disjoint in time, which a split between
    them separates.  Simultaneous overlap is COUNTED AND NAMED in a
    warning of its own, because a check that narrows what it errors on
    and does not say so reports a clean reel and tells nobody what it
    declined to look at (the F7-held and F8-between-words convention).
    """
    findings: List[Finding] = []

    # Build a list of all spoken words mapped to reel time.
    reel_words = []
    for segment in transcript_segments:
        speaker = segment.get("speaker", "unknown")
        for word in (segment.get("words") or ()):
            try:
                start = float(word["start"])
                end = float(word["end"])
                text = word["word"]
                for r_start, r_end in _clip_to_reel(start, end, keep_ranges,
                                                    lead_seconds):
                    reel_words.append({
                        "word": text.lower(),
                        "speaker": speaker,
                        "master_start": start,
                        "master_end": end,
                        "reel_start": r_start,
                        "reel_end": r_end
                    })
            except (KeyError, ValueError):
                continue

    # Check each card against the reel words.
    simultaneous: List[tuple] = []
    for card in caption_cards:
        c_start = card.get("reel_start", 0)
        c_end = card.get("reel_end", 0)
        card_text = card.get("text", "").lower()

        overlapping_words = [
            rw for rw in reel_words
            if rw["reel_start"] < c_end - REEL_OVERLAP_EPSILON
            and rw["reel_end"] > c_start + REEL_OVERLAP_EPSILON
        ]
        speakers = set(rw["speaker"] for rw in overlapping_words)
        if len(speakers) <= 1:
            continue
        if _is_sequential_turn(overlapping_words):
            findings.append(Finding(
                finding_class=FindingClass.F17,
                reel=reel_name,
                message=(
                    f"caption card '{card_text}' mixes speakers: "
                    f"{', '.join(sorted(speakers))}"
                ),
            ))
        else:
            simultaneous.append((card_text, sorted(speakers)))

    if simultaneous:
        listed = ", ".join(
            f"'{text[:40]}' ({'/'.join(voices)})"
            for text, voices in simultaneous[:5])
        if len(simultaneous) > 5:
            listed += f", +{len(simultaneous) - 5} more"
        findings.append(Finding(
            finding_class=FindingClass.F17,
            reel=reel_name,
            message=(
                f"{len(simultaneous)} caption card(s) span simultaneous "
                f"speech by two speakers - talk-over or mic bleed, not a "
                f"turn, so no regrouping separates them: {listed}"),
            severity="warning",
            detail={
                "simultaneous_cards": len(simultaneous),
                "cards": [
                    {"text": text[:60], "speakers": voices}
                    for text, voices in simultaneous[:10]
                ],
            },
        ))

    return findings


def check_short_captions(reel_name: str,
                         caption_cards: Sequence[dict],
                         fps: float,
                         min_duration_seconds: float = MIN_CAPTION_DISPLAY_SECONDS,
                         declared_short: Sequence[Tuple[int, int]] = (),
                         ) -> List[Finding]:
    """F7: a placed caption item under the readability floor FAILS.

    AGENTS.md 10.4 requires no caption card under 0.5s.  The floor is
    NOT chosen here: it is `manifest_validator.
    MIN_CAPTION_DISPLAY_SECONDS`, the pipeline's own hard floor - the
    threshold `render_qa`'s `subtitle_too_short` already uses and the
    one the manifest's P6 check enforces.  At 23.976fps it is 12
    frames; the flash cards this exists to catch are 2-3.

    There is deliberately NO last-of-block exemption.  The exemption's
    intent was real: a block-final card cannot be lengthened by any
    regrouping (it is on screen until the next card's first word, and
    the last card of a block has no next word).  But that is a fact
    about the grouping, not a claim the card is readable - and on the
    placed path the clause is always satisfied, because a one-card
    block's only card is trivially its last.  Re-measured 2026-09-06
    across 23 built reels, 44 of 45 under-floor cards satisfied both
    clauses trivially, being last because they were alone; on
    2026-09-08 the captain's approved reels 02, 03 and 19 each carried
    a 2-3 frame flash every gate had HELD.  A check whose exemption is
    always satisfied is a check that cannot refuse.

    So an unreadable card fails whatever block it ends with, and the
    remedy stays where it belongs: upstream, in `reel_spine.
    _merge_fragment_blocks` (rejoin the fragment row to its sentence)
    and in never authoring a sub-second keep - never in a warning that
    calls a flash acceptable because nothing could lengthen it.

    There is ONE exemption and it is not a grouping fact: a card the
    project DECLARED short in `external/caption_timing.json`. The
    captain trimmed Reel 13's last closer card to three frames himself
    and recorded why, and a gate that FAILS correct output is no more
    coverage than one that cannot fail (AGENTS.md 10.4). Such a card is
    REPORTED as a warning naming the declaration - never silently
    passed, and never counted in `short_captions`. `declared_short` is
    `((start_frame, frames), ...)` from the plan's own retime
    (`_retime_planned_captions`), so nothing but a recorded pin can
    reach it: a flash the grouper authored still fails, which is every
    case this check was written for.

    Measured in FRAMES off the placed item, not seconds off the plan:
    the caller passes the cards built from `timeline.caption_items`,
    whose `frames` is the placed `duration_frames`.
    """
    findings: List[Finding] = []
    floor_frames = int(math.ceil(min_duration_seconds * fps))
    declared = {(int(start), int(count)) for start, count in declared_short}

    for i, card in enumerate(caption_cards):
        start_seconds = card.get("reel_start", card.get("start_seconds", 0))
        duration = _card_end(card) - start_seconds
        frames = card.get("frames")
        if frames is None:
            frames = int(round(duration * fps))
        if frames >= floor_frames:
            continue
        start_frame = int(round(float(start_seconds) * fps))
        was_declared = (start_frame, int(frames)) in declared
        findings.append(Finding(
            finding_class=FindingClass.F7,
            reel=reel_name,
            message=(
                f"caption card {i+1} '{card.get('text', '')[:30]}' "
                f"is {frames} frames ({duration:.3f}s), under the "
                f"{min_duration_seconds}s readability floor "
                f"({floor_frames} frames at {fps:.3f}fps)"
                + (" - DECLARED that length in "
                   "external/caption_timing.json, so it is reported "
                   "rather than failed" if was_declared else "")),
            severity="warning" if was_declared else "error",
            detail={
                "caption_index": i,
                "duration_seconds": round(duration, 3),
                "duration_frames": frames,
                "text": card.get("text", "")[:60],
                "minimum_seconds": min_duration_seconds,
                "minimum_frames": floor_frames,
                "declared": was_declared,
            },
        ))

    return findings


def _placed_item_frames(item) -> Optional[int]:
    """A placed timeline item's duration in frames, dict or TimelineItem."""
    if isinstance(item, dict):
        frames = item.get("duration_frames")
        if frames is not None:
            return int(frames)
        start = item.get("start_frame")
        end = item.get("end_frame")
        if start is not None and end is not None:
            return int(end) - int(start)
        return None
    frames = getattr(item, "duration_frames", None)
    if frames is not None:
        return int(frames)
    return int(item.end_frame) - int(item.start_frame)


def _placed_item_name(item) -> str:
    if isinstance(item, dict):
        return str(item.get("name", ""))
    return str(getattr(item, "name", ""))


def check_short_av_items(reel_name: str,
                         video_items,
                         audio_items,
                         fps: float,
                         min_duration_seconds: float = MIN_CAPTION_DISPLAY_SECONDS,
                         ) -> List[Finding]:
    """F7, placed picture-and-sound half: a placed video or audio item
    under the SAME readability floor FAILS.

    The report's keep-range slivers are this class: R02's 3-frame
    "their keywords" video chirp, R04's 10-frame "in AI over" and
    5-frame "size." audio fragments, R02's 5-frame "that's" blip, R10's
    6-frame breath.  A viewer hears an orphaned word-sliver across a
    multi-second gap, and no caption check can see it because most of
    these slivers carry no card at all.  Reel timelines carry no SFX
    stingers or other legitimately sub-second items - picture on
    V1/V2, dialogue on the two audio tracks - so the floor needs no
    track exemption either.
    """
    findings: List[Finding] = []
    floor_frames = int(math.ceil(min_duration_seconds * fps))

    for kind, items in (("video", video_items or ()),
                        ("audio", audio_items or ())):
        for n, item in enumerate(items):
            frames = _placed_item_frames(item)
            if frames is None:
                continue
            if frames < floor_frames:
                findings.append(Finding(
                    finding_class=FindingClass.F7,
                    reel=reel_name,
                    message=(
                        f"{kind} item {n+1} "
                        f"'{_placed_item_name(item)[:30]}' is {frames} "
                        f"frames ({frames / fps:.3f}s), under the "
                        f"{min_duration_seconds}s readability floor "
                        f"({floor_frames} frames at {fps:.3f}fps)"),
                    severity="error",
                    detail={
                        "kind": kind,
                        "item_index": n,
                        "duration_seconds": round(frames / fps, 3),
                        "duration_frames": frames,
                        "name": _placed_item_name(item)[:60],
                        "minimum_seconds": min_duration_seconds,
                        "minimum_frames": floor_frames,
                    },
                ))

    return findings


def check_boundary_speech(reel_name: str,
                          span_start: float,
                          span_end: float,
                          transcript_segments: Sequence[dict],
                          ranges: Optional[Sequence[Tuple[float, float]]] = None,
                          ) -> List[Finding]:
    """F8: Find reel boundaries that cut through speech the boundary
    logic cannot see.

    `snap_to_speech` widens a span to whole BOUND segments, but straddling
    segments are invisible to it.  The audit found 11 boundaries that cut
    through a real sentence.

    **A boundary is tested against the row's WORDS, never against the
    row's outer envelope.**  A straddling row is not a sentence: it is
    what WhisperX returned for one speaker's isolated track, and its
    envelope spans every second that speaker was silent inside it.  On
    the captain's nineteen, row 22.04-47.23s carries "this is a
    completely different system right" ending at 24.28s and "so give me
    an example of that difference" starting at 45.41s - twenty-one
    seconds of that row's envelope is Craig not speaking.  Testing
    `seg_start < when < seg_end` fired on boundaries that cut nothing:
    measured over all nineteen, 16 findings, 13 of them landing in a
    silence between two words of the same row, the nearest word edge as
    far as 35.8s away.  The rows carry `words` with per-word start and
    end and always have, so the envelope was never the finest
    measurement available - it was just the one being read.

    **A row with no word list falls back to its envelope, and SAYS it
    did.**  A transcript that stopped carrying word timings must not
    silently switch this check off, which is the vacuous-gate failure
    this file exists to remove (AGENTS.md 10.4).

    **The word's own duration travels in the finding.**  A word is a
    tenth of a second; this transcript contains a "well" of 19.04s and
    another of 34.47s, where the aligner stretched one word across the
    silence before the speaker resumed.  A reader who sees the duration
    can tell a cut sentence from a stretched alignment without leaving
    the report.

    **Boundaries that land inside a row and outside every word are
    COUNTED AND REPORTED**, not dropped.  A check that narrows what it
    looks at and does not say so reports a clean reel and tells nobody
    what it declined to look at.

    `ranges` is every master span the reel PLAYS, and every edge of every
    one is a boundary a viewer hears.  A reel that closes on a CTA taken
    from elsewhere in the episode has four such edges, not two, and the
    closer's are the ones that decide whether it ends on a finished
    sentence.  Omitted, the single body span is used, which is what every
    reel was before a closer could come from elsewhere.

    A boundary time is reported ONCE.  A closer beginning exactly where
    the body ends is one edge the viewer hears, not two, and reporting it
    as both an END and a START would read as two defects.
    """
    findings: List[Finding] = []
    checked = list(ranges) if ranges else [(span_start, span_end)]

    # Distinct boundary times, each labelled by what it is. A time that is
    # both an end and a start is reported as an END - it is where the
    # picture leaves the passage, which is the half a viewer hears cut.
    boundaries: List[Tuple[float, str]] = []
    seen: Dict[float, str] = {}
    for range_start, range_end in checked:
        for when, kind in ((range_start, "start"), (range_end, "end")):
            key = round(when, 6)
            if key in seen:
                if seen[key] != "end" and kind == "end":
                    seen[key] = "end"
                continue
            seen[key] = kind
    boundaries = sorted(seen.items())

    in_row_not_in_word = 0
    rows_without_words = 0

    for segment in transcript_segments:
        seg_start = float(segment.get("timeline_start", 0))
        seg_end = float(segment.get("timeline_end", 0))
        has_item_id = bool(segment.get("resolve_item_id"))

        # Only straddling segments are invisible to snap_to_speech
        if has_item_id:
            continue

        text = (segment.get("text") or "")[:60]
        speaker = segment.get("speaker", "unknown")
        words = _row_words(segment)
        if not words:
            rows_without_words += 1

        for when, kind in boundaries:
            if not (seg_start < when < seg_end):
                continue

            cut = _word_at(words, when)
            if words and cut is None:
                # Inside the row's envelope, between two of its words -
                # the row is not speaking here.  Counted, not dropped.
                in_row_not_in_word += 1
                continue

            if cut is None:
                where = (f"row {seg_start:.2f}-{seg_end:.2f}s, which "
                         f"carries no word timings")
                detail_word = None
            else:
                word_start, word_end, word_text = cut
                where = (f"the word '{word_text}' "
                         f"({word_start:.2f}-{word_end:.2f}s, "
                         f"{word_end - word_start:.2f}s long)")
                detail_word = {
                    "word": word_text,
                    "start": round(word_start, 3),
                    "end": round(word_end, 3),
                    "duration_seconds": round(word_end - word_start, 3),
                }

            findings.append(Finding(
                finding_class=FindingClass.F8,
                reel=reel_name,
                message=(
                    f"{kind.upper()} at {when:.2f}s cuts {speaker} "
                    f"mid-speech, through {where}, in row "
                    f"{seg_start:.2f}-{seg_end:.2f}s "
                    f"\"{text}\""),
                severity="error",
                detail={
                    "boundary": kind,
                    "boundary_time": round(when, 2),
                    "segment_start": round(seg_start, 2),
                    "segment_end": round(seg_end, 2),
                    "speaker": speaker,
                    "text": text,
                    "word": detail_word,
                },
            ))

    if in_row_not_in_word or rows_without_words:
        findings.append(Finding(
            finding_class=FindingClass.F8,
            reel=reel_name,
            message=(
                f"{in_row_not_in_word} boundary/row overlap(s) landed "
                f"between two words of a straddling row and cut no "
                f"speech; {rows_without_words} straddling row(s) carried "
                f"no word timings and were tested on their envelope"),
            severity="warning",
            detail={
                "in_row_between_words": in_row_not_in_word,
                "rows_without_word_timings": rows_without_words,
            },
        ))

    return findings


def _row_words(segment: dict) -> List[Tuple[float, float, str]]:
    """A transcript row's words as (start, end, text), timed ones only.

    A word with no usable interval cannot say whether a boundary lands
    inside it, so it is not offered as evidence that one did.
    """
    out: List[Tuple[float, float, str]] = []
    for word in (segment.get("words") or ()):
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        out.append((start, end, str(word.get("word", ""))))
    return out


def _word_at(words: Sequence[Tuple[float, float, str]],
             when: float) -> Optional[Tuple[float, float, str]]:
    """The word being spoken at `when`, or None.

    Strictly inside, matching the row test it replaces: a boundary
    sitting exactly on a word's first or last frame is the edge
    `snap_to_speech` aims for, not a cut through it.
    """
    for start, end, text in words:
        if start < when < end:
            return (start, end, text)
    return None


# ── F9: Duplicate placements ─────────────────────────────────────────

def check_duplicate_placements(reel_name: str,
                               video_items: Sequence[TimelineItem],
                               fps: float,
                               ) -> List[Finding]:
    """F9: Detect duplicate items placed at the same record position.

    Found 2026-09-04: a placement loop that iterates once per clip PER
    CLIP produces N*N items instead of N.  A 4-clip reel gets 16 items
    at 4 unique record positions, each position occupied by 4 identical
    clips.  build_reel_timeline has zero tests, so this verifier is
    currently the only guard against this class of bug.

    Detection: group items by (track_index, start_frame).  Any position
    with more than one item is a duplicate placement.  Items at the same
    position on DIFFERENT tracks are expected (V1 and V2 can share a
    record position - that is what the captain's edit does with two
    speakers).
    """
    findings: List[Finding] = []
    by_position: Dict[tuple, List[TimelineItem]] = {}
    for item in video_items:
        key = (item.track_index, item.start_frame)
        by_position.setdefault(key, []).append(item)

    for (track, frame), items in sorted(by_position.items()):
        if len(items) > 1:
            findings.append(Finding(
                finding_class=FindingClass.F9,
                reel=reel_name,
                message=(
                    f"V{track} has {len(items)} items at frame {frame} "
                    f"({frame / fps:.2f}s) - duplicate placement"),
                severity="error",
                detail={
                    "track": track,
                    "frame": frame,
                    "count": len(items),
                    "items": [
                        {"name": i.name, "source_file": i.source_file,
                         "duration_frames": i.duration_frames}
                        for i in items
                    ],
                },
            ))

    # Also check for the N*N symptom: if total items / unique positions
    # is a perfect square, that is the signature of the nested-loop bug.
    unique_positions = len(by_position)
    total_items = len(video_items)
    if unique_positions > 0 and total_items > unique_positions:
        import math
        ratio = total_items / unique_positions
        sqrt = math.isqrt(total_items)
        if sqrt * sqrt == total_items and sqrt == unique_positions:
            findings.append(Finding(
                finding_class=FindingClass.F9,
                reel=reel_name,
                message=(
                    f"{total_items} items at {unique_positions} positions "
                    f"is {sqrt}x{sqrt} - signature of a placement loop "
                    f"that runs once per clip PER CLIP"),
                severity="error",
                detail={
                    "total_items": total_items,
                    "unique_positions": unique_positions,
                    "ratio": ratio,
                    "suspected_clip_count": sqrt,
                },
            ))

    return findings


# ── F18/F19/F20: transition elements laid over a cut ─────────────────

# One frame of the reel's own rate. A transition element hides a cut, so
# a placement one frame off exposes the jump it exists to hide AND covers
# a frame of the wrong shot. The tolerance is the resolution of the
# medium - mechanical, not editorial (AGENTS.md 10.5).
OVERLAY_FRAME_TOLERANCE = 0


def check_transition_overlays(reel_name: str,
                              planned: Sequence[dict],
                              overlay_items: Sequence[TimelineItem],
                              fps: float,
                              ) -> List[Finding]:
    """F18: the transition elements the plan wrote are the ones on V4.

    Graded in BOTH directions, because each one has already happened to
    something on this path: a planned element that is not on the timeline
    is a declaration that silently drew nothing (the defect class
    `bookends`, `timed_text_overlay` and `motion_graphics_plan` were each
    built to close), and an element on the timeline that no plan wrote is
    a placement nothing can account for.

    Pairing is by RECORD FRAME, never by list index. F2 was paired by
    index while its comment claimed start frame, and every pair after the
    first missing card compared one card's plan to a different card's
    item - 701 findings that read as a placement defect and were not one.
    """
    findings: List[Finding] = []

    by_frame: Dict[int, List[TimelineItem]] = {}
    for item in overlay_items:
        by_frame.setdefault(item.start_frame, []).append(item)

    planned_frames = set()
    for entry in planned:
        record = int(entry["record_frame"])
        planned_frames.add(record)
        duration = int(entry["duration_frames"])
        matched = by_frame.get(record)
        if not matched:
            findings.append(Finding(
                finding_class=FindingClass.F18,
                reel=reel_name,
                message=(
                    f"the transition element planned for seam "
                    f"{entry.get('seam_index')} at frame {record} "
                    f"({record / fps:.2f}s) is not on V"
                    f"{entry.get('track_index', OVERLAY_TRACK)} at all"),
                severity="error",
                detail={"seam_index": entry.get("seam_index"),
                        "record_frame": record,
                        "duration_frames": duration,
                        "placed": False},
            ))
            continue
        if len(matched) > 1:
            findings.append(Finding(
                finding_class=FindingClass.F18,
                reel=reel_name,
                message=(
                    f"{len(matched)} transition elements sit at frame "
                    f"{record} - two clips cannot occupy the same frames "
                    f"of one track, so Resolve kept one of them"),
                severity="error",
                detail={"record_frame": record, "count": len(matched)},
            ))
        item = matched[0]
        delta = item.duration_frames - duration
        if abs(delta) > OVERLAY_FRAME_TOLERANCE:
            findings.append(Finding(
                finding_class=FindingClass.F18,
                reel=reel_name,
                message=(
                    f"the transition element at frame {record} was planned "
                    f"{duration} frames and is {item.duration_frames} "
                    f"({delta:+d})"),
                severity="error",
                detail={"record_frame": record, "planned_frames": duration,
                        "actual_frames": item.duration_frames,
                        "delta_frames": delta},
            ))

    for frame, items in sorted(by_frame.items()):
        if frame not in planned_frames:
            findings.append(Finding(
                finding_class=FindingClass.F18,
                reel=reel_name,
                message=(
                    f"V{items[0].track_index} carries "
                    f"{len(items)} element(s) at frame {frame} "
                    f"({frame / fps:.2f}s) that no plan wrote: "
                    f"{items[0].name or items[0].source_file}"),
                severity="error",
                detail={"record_frame": frame, "planned": False,
                        "names": [i.name for i in items]},
            ))
    return findings


def check_unclassified_video(reel_name: str,
                             unclassified: Sequence[TimelineItem],
                             fps: float,
                             ) -> List[Finding]:
    """F19: a video item on a track this verifier grades with nothing.

    The anti-silent-drop half. Every earlier bucketing sent unknown
    tracks to an implicit `else: pass`, so a clip could be on the
    captain's timeline and in no check - which reads as coverage and is
    the gate-that-cannot-fail shape (AGENTS.md 10.4). Anything that is
    not picture, caption or transition element is REPORTED, by track.
    """
    if not unclassified:
        return []
    by_track: Dict[int, List[TimelineItem]] = {}
    for item in unclassified:
        by_track.setdefault(item.track_index, []).append(item)
    return [
        Finding(
            finding_class=FindingClass.F19,
            reel=reel_name,
            message=(
                f"V{track} carries {len(items)} video item(s) and this "
                f"verifier has no check for that track - picture rows "
                f"are per angle, then the Frame row, the Subtitles row, "
                f"Transitions, the Explainer, the Semantic row and the "
                f"Motion Graphics row the speaker lower thirds ride, in "
                f"the track plan's order. An item nothing "
                f"grades is not an item nothing is wrong with."),
            severity="error",
            detail={"track": track, "count": len(items),
                    "names": [i.name for i in items][:10],
                    "total_seconds": round(
                        sum(i.duration_frames for i in items) / fps, 3)},
        )
        for track, items in sorted(by_track.items())
    ]


def check_overlay_caption_coverage(reel_name: str,
                                   overlay_items: Sequence[TimelineItem],
                                   caption_items: Sequence[TimelineItem],
                                   fps: float,
                                   ) -> List[Finding]:
    """F20: which caption cards a transition element draws over.

    A WARNING, and deliberately so. An element that hides a cut hides
    what is on that frame - that is the gesture, not a defect, and a
    check that failed it would be the gate-that-fails-correct-output
    shape this repository also removes (AGENTS.md 10.4). What was
    previously wrong is that nobody measured it: captions are bound to
    footage, and a picture event that covers a caption was invisible.

    No threshold. Every overlap is reported, one frame included, because
    how much cover is too much is a judgement and this is a measurement.
    """
    from library.tools.transition_overlay import (
        OverlayPlacement, captions_covered)

    if not overlay_items or not caption_items:
        return []
    placements = [
        OverlayPlacement(
            seam_index=index, seam_kind="", record_frame=item.start_frame,
            duration_frames=item.duration_frames,
            track_index=item.track_index, element_path=item.source_file,
            anchor="",
        )
        for index, item in enumerate(overlay_items)
    ]
    spans = [(c.start_frame, c.end_frame, c.name) for c in caption_items]
    findings: List[Finding] = []
    for coverage in captions_covered(placements, spans, fps):
        findings.append(Finding(
            finding_class=FindingClass.F20,
            reel=reel_name,
            message=(
                f"a transition element covers {coverage.covered_seconds:.2f}s "
                f"of the caption at frame {coverage.caption_start_frame} "
                f"({coverage.caption_name or 'unnamed'})"),
            severity="warning",
            detail=coverage.as_dict(),
        ))
    return findings


# ── F10: Format mismatch ─────────────────────────────────────────────

def check_format(reel_name: str,
                 width: int, height: int, fps: float,
                 master_fps: float,
                 expected_width: Optional[int] = None,
                 expected_height: Optional[int] = None,
                 ) -> List[Finding]:
    """F10: Verify the reel timeline is the DECLARED frame at the master's rate.

    This project has a documented history of a correct vertical timeline
    rendering out LANDSCAPE while every other check passed - duration,
    frame rate, audio streams and frame occupancy all green on a file of
    the wrong shape.  Refusing to check the shape is how that happened.

    The frame is the project's DECLARED delivery format
    (`library/tools/delivery_format.py`), passed in by the caller - it
    was `EXPECTED_WIDTH/EXPECTED_HEIGHT = 1080/1920` here, which is the
    other way to get the same wrong answer: a gate that FAILS correct
    output (AGENTS.md 10.4).  A project shipping 16:9 long-form would
    have had every reel refused for being exactly what it declared.

    A caller that names no expected frame gets a WARNING saying the
    shape was not checked, never a pass: the whole reason this check
    exists is that everything else stayed green on the wrong shape.
    """
    findings: List[Finding] = []

    if expected_width is None or expected_height is None:
        findings.append(Finding(
            finding_class=FindingClass.F10,
            reel=reel_name,
            message=(
                f"Timeline is {width}x{height} and NOTHING declared the "
                f"frame it should be - the shape was not checked. Pass "
                f"the project's delivery format "
                f"(library/tools/delivery_format.py)."),
            severity="warning",
            detail={
                "actual_width": width,
                "actual_height": height,
                "expected_width": None,
                "expected_height": None,
            },
        ))
    elif width != int(expected_width) or height != int(expected_height):
        findings.append(Finding(
            finding_class=FindingClass.F10,
            reel=reel_name,
            message=(
                f"Timeline is {width}x{height}, expected "
                f"{int(expected_width)}x{int(expected_height)} "
                f"(the project's declared delivery format)"),
            severity="error",
            detail={
                "actual_width": width,
                "actual_height": height,
                "expected_width": int(expected_width),
                "expected_height": int(expected_height),
            },
        ))

    # Frame rate must match the master's - a reel at a different rate
    # from its source produces frame blending or stutters.
    if fps and master_fps and abs(fps - master_fps) > 0.01:
        findings.append(Finding(
            finding_class=FindingClass.F10,
            reel=reel_name,
            message=(
                f"Timeline fps {fps:.3f} does not match master "
                f"{master_fps:.3f}"),
            severity="error",
            detail={
                "actual_fps": fps,
                "master_fps": master_fps,
            },
        ))

    return findings


# ── F12: Delivered framing ───────────────────────────────────────────


def check_delivered_framing(reel_name: str,
                            video_items: Sequence[TimelineItem],
                            width: int, height: int,
                            source_sizes: Optional[dict] = None,
                            declared_intent: Optional[float] = None,
                            declared_crop_factor: float = 1.0,
                            cards: Sequence["PlannedCard"] = (),
                            look=None,
                            ) -> List[Finding]:
    """F12: Verify the picture on the frame is the picture declared.

    F10 above proves the FRAME is 1080x1920.  This proves what is IN it.
    The two are not the same question and this project has already paid
    for the difference once: a correct vertical timeline that rendered
    out landscape passed every structural check there was.  A correct
    vertical timeline carrying a 31.6% strip of picture in a sea of black
    passes them all too.

    Nothing is judged here and no number is invented.  The rectangle a
    clip puts on the frame is arithmetic on its own Resolve transform
    (`library/tools/reel_framing.py`), the rectangle the project ASKED
    for is the same arithmetic run forwards from `framing_intent`, and
    the comparison is between integer pixels.

    Silence is not a pass.  A reel whose source dimensions are not in
    the catalog, or whose items report no transform, comes back as a
    WARNING naming what could not be read - never as nothing.  The
    finding that says "this was not measured" is the one the caption
    gate lacked when it expected zero cards, found 762 and passed
    (AGENTS.md 10.4).
    """
    from library.tools.framing_intent import FILL
    from library.tools.reel_framing import (
        ReelFramingError, declared_picture, delivered_picture, disagreement)

    findings: List[Finding] = []
    if not width or not height:
        return findings
    if declared_intent is None:
        # Nobody resolved a declaration for this run. Saying so is the
        # finding; grading against a guess would be the vacuity.
        return [Finding(
            finding_class=FindingClass.F12, reel=reel_name,
            message=("No framing_intent was resolved for this project, so "
                     "what the reel delivers cannot be compared to what it "
                     "was meant to deliver. Nothing was graded."),
            severity="warning", detail={"declared_intent": None})]

    sizes = dict(source_sizes or {})
    # A full-frame card is not footage: it is not in the catalog and there
    # is no framing INTENT to grade it against, because it was rendered at
    # the delivery frame by construction.  Skipping it silently would be
    # the vacuity this function's own docstring refuses, so it is graded
    # instead - against the WHOLE FRAME, which is what "full-frame" means
    # and what a scaled or letterboxed card would fail.
    card_by_item = {id(i): n for n, i in card_items(video_items, cards).items()}
    for item in video_items:
        if id(item) in card_by_item:
            sizes[item.source_file] = {"width": width, "height": height,
                                       "rotation": 0}
    # Under a declared TV frame the shot plays at the project's own
    # framing MULTIPLIED by the declared punch-in - that is what
    # `tv_frame.v1_zoom_for_look` puts on V1 - so the declaration this
    # check grades against is the multiplied one.  Without it every shot
    # under the look reads as a framing violation, which is a gate
    # failing correct output (AGENTS.md 10.4).  The frame overlay itself
    # is the SET rather than footage and is excluded, by the same
    # enumeration the placer and F4 use.
    from library.tools import reel_look as _look
    declared_crop_factor = _look.declared_zoom_over(declared_crop_factor, look)
    frame_by_item = _look.frame_overlay_items(video_items, look)
    # A declared FREEZE tail is excluded on the same terms as the frame
    # overlay, and stated here rather than left to read as a catalog
    # gap: a hold is a copy of a frame this check already graded as the
    # shot, it is in no catalog, and `reel_build._inherit_freeze_
    # treatment` refuses it outright if it draws differently from the
    # frame it holds - a stronger guarantee than this one, not a
    # weaker.
    from library.tools import reel_ending as _ending
    freeze_by_item = _ending.freeze_items(video_items)
    # A card on the declared card row is graded against the whole frame
    # wherever it sits (the injection above sizes every planned card at
    # the delivery frame): without the `card_by_item` arm a card that
    # moved off V1/V2 would leave this check silently rather than pass
    # it - an overlay invisible to every check, the defect class this
    # repository keeps removing (AGENTS.md 10.4).
    footage = [i for i in video_items
               if (i.track_index in (1, 2) or id(i) in card_by_item)
               and id(i) not in frame_by_item
               and id(i) not in freeze_by_item]
    unreadable: List[str] = []
    # One finding per distinct disagreement, not per item: 34 clips of one
    # source all conform the same way, and 34 copies of one sentence is a
    # report nobody reads.
    seen: dict = {}
    for item in footage:
        meta = sizes.get(item.source_file) or {}
        if not meta.get("width") or not meta.get("height"):
            name = item.source_file.rsplit("/", 1)[-1] or item.name
            if name not in unreadable:
                unreadable.append(name)
            continue
        is_card = id(item) in card_by_item
        try:
            delivered = delivered_picture(
                meta["width"], meta["height"], width, height,
                item.transform, meta.get("rotation", 0))
            # A card's declaration IS the frame. `framing_intent` is a
            # statement about how FOOTAGE is fitted, and a card was not
            # fitted - it was drawn at 1080x1920. Grading it against the
            # project's letterbox would report every correct card as
            # wrong.
            declared = declared_picture(
                meta["width"], meta["height"], width, height,
                FILL if is_card else declared_intent,
                1.0 if is_card else declared_crop_factor,
                meta.get("rotation", 0))
        except ReelFramingError as exc:
            name = item.source_file.rsplit("/", 1)[-1] or item.name
            if name not in unreadable:
                unreadable.append(f"{name} ({exc})")
            continue
        # Under the look a punch-in is AIMED at the measured subject, so
        # the delivered picture is the declared SIZE at an offset
        # position. The offset is not re-derived here - that would mean
        # re-running the face measurement - it is checked against the
        # bound the placer clamps to: an aim may move the picture only
        # as far as it can without uncovering an edge it was covering.
        # An aim past that bound is a real defect and is reported; an
        # aim inside it is the declared picture, moved on purpose.
        # Under the look a punch-in is AIMED at the measured subject and
        # sized to cover the frame's own screen window, so the delivered
        # picture is bigger than the declaration's and offset. The aim is
        # not re-derived here - that would mean re-running the face
        # measurement - the PROPERTY is checked instead, and it is the
        # one that matters: no black inside the television's screen.
        if look is not None and not is_card:
            window = _look.screen_window_rect_for(look, width, height)
            uncovered = _look.uncovered_window_edges(delivered, window)
            if uncovered:
                findings.append(Finding(
                    finding_class=FindingClass.F12, reel=reel_name,
                    message=(
                        f"{item.source_file.rsplit('/', 1)[-1]} leaves black "
                        f"inside the television's screen: "
                        f"{', '.join(uncovered)}. The picture is "
                        f"{delivered.rect} and the screen window is "
                        f"({window[0]:.0f}, {window[1]:.0f}, "
                        f"{window[2]:.0f}, {window[3]:.0f})"),
                    severity="error",
                    detail={"uncovered": uncovered,
                            "picture": list(delivered.rect),
                            "window": [round(v) for v in window]}))
            # Nothing further to compare: the declared rect is a
            # statement about the DELIVERY FRAME, and under the look the
            # picture is sized to the screen instead. Grading it against
            # the frame reported every correct shot as wrong.
            continue

        why = disagreement(delivered, declared)
        if why is None:
            continue
        seen.setdefault(why, {"count": 0, "delivered": delivered,
                              "declared": declared, "sources": []})
        seen[why]["count"] += 1
        source_name = item.source_file.rsplit("/", 1)[-1]
        if source_name not in seen[why]["sources"]:
            seen[why]["sources"].append(source_name)

    for why, record in seen.items():
        findings.append(Finding(
            finding_class=FindingClass.F12, reel=reel_name,
            message=f"{record['count']} of {len(footage)} picture items: {why}",
            severity="error",
            detail={
                "items": record["count"],
                "items_total": len(footage),
                "sources": record["sources"],
                "delivered": record["delivered"].as_dict(),
                "declared": record["declared"].as_dict(),
            }))

    if unreadable:
        findings.append(Finding(
            finding_class=FindingClass.F12, reel=reel_name,
            message=("Framing could not be read for "
                     f"{', '.join(unreadable)}: the catalog does not give "
                     "the source dimensions, so this reel's picture was "
                     "not graded."),
            severity="warning",
            detail={"unreadable_sources": unreadable}))

    return findings


# ── F11: Subtitle styling ────────────────────────────────────────────

# The segment naming convention puts the speaker in the filename:
#   sub_<timeline>_<speaker>_<block>_<span>_<digest>.mov
# This regex extracts the speaker slug from either the source_file
# path or the clip name.
_SPEAKER_FROM_SEGMENT = re.compile(
    r"sub_[^_]+_([^_]+)_",
)


def _caption_speaker(item: TimelineItem) -> Optional[str]:
    """Extract the speaker slug from a caption card's source_file or name.

    Returns the slug (lowercase), or None if it cannot be determined.
    The segment naming convention is documented in
    library/tools/subtitle_segment_id.py.
    """
    for field in (item.source_file, item.name):
        if not field:
            continue
        # Try the filename component
        basename = field.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        m = _SPEAKER_FROM_SEGMENT.match(basename)
        if m:
            slug = m.group(1)
            if slug != "nospeaker":
                return slug
    return None


def check_explainer(reel_name: str,
                    explainer_items: Sequence[TimelineItem],
                    planned: Optional[dict],
                    fps: float) -> List[Finding]:
    """F21: the animated explainer, against the plan THE BUILD WROTE.

    Both directions, and every one of them is reachable from a real
    build:

    - a reel whose plan says `planned` and whose timeline carries no
      explainer.  A reel that quietly starts without one is
      indistinguishable from a project that declared nothing, which is
      how the 4th Wall end card survived four months;
    - an item on the explainer track that no plan accounts for - the
      out-of-band append `bookends.assert_no_invented_bookends` refuses
      on the master, refused here too;
    - an explainer at the wrong reel second, or with the wrong number
      of frames;
    - two explainer items overlapping, which is one build having run
      twice into one timeline.

    `planned` is the entry `explainer_plan.read_plans` returns for this
    reel - **read from what the build recorded, never re-derived**. A
    re-derived plan is only the build's plan while nothing changed in
    between, and that assumption already produced 42 confident
    meaningless errors on this path (PLAN-MISMATCH).

    Given no recorded plan at all this returns NOTHING rather than
    guessing: a project built before explainers existed has no record,
    and grading it against an absence would fail every correct reel.
    """
    findings: List[Finding] = []
    items = sorted(explainer_items or [], key=lambda i: i.start_frame)
    # The row the items are actually on - the plan owns the index, so
    # the message reads it off the timeline rather than a constant
    # (V6 on a two-angle reel without the look, V7 under it).
    track = items[0].track_index if items else EXPLAINER_TRACK
    if planned is None:
        if items:
            findings.append(Finding(
                finding_class=FindingClass.F21, reel=reel_name,
                message=(
                    f"{len(items)} item(s) on the explainer track (V"
                    f"{track}) and this reel has no recorded "
                    f"explainer plan at all, so nothing accounts for them"),
                detail={"items": len(items), "planned": None}))
        return findings

    expected = list(planned.get("segments") or [])
    if not expected and planned.get("basis") not in (None, ""):
        # The plan says why it drew nothing. An item present anyway is
        # out of band whatever the reason was.
        if items:
            findings.append(Finding(
                finding_class=FindingClass.F21, reel=reel_name,
                message=(
                    f"{len(items)} item(s) on V{track} but the "
                    f"build recorded no explainer for this reel "
                    f"({planned.get('basis')})"),
                detail={"items": len(items),
                        "basis": planned.get("basis")}))
        return findings

    # Paired by RECORD FRAME, never by list index - the mistake that
    # turned F2 into 701 meaningless findings.
    by_frame = {item.start_frame: item for item in items}
    matched = set()
    for segment in expected:
        want_start = int(round(float(segment["timeline_start"]) * fps))
        want_frames = int(segment["total_frames"])
        item = by_frame.get(want_start)
        if item is None:
            near = min((abs(i.start_frame - want_start), i) for i in items) \
                if items else None
            findings.append(Finding(
                finding_class=FindingClass.F21, reel=reel_name,
                message=(
                    f"explainer planned at reel frame {want_start} "
                    f"({float(segment['timeline_start']):.2f}s) and no item "
                    f"on the explainer track (V{track}) starts there"
                    + (f"; nearest is frame {near[1].start_frame}"
                       if near else "; the track is empty")),
                detail={"planned_start_frame": want_start,
                        "planned_frames": want_frames,
                        "found": [i.start_frame for i in items]}))
            continue
        matched.add(item.start_frame)
        if item.duration_frames != want_frames:
            findings.append(Finding(
                finding_class=FindingClass.F21, reel=reel_name,
                message=(
                    f"explainer at reel frame {want_start} runs "
                    f"{item.duration_frames} frames, planned {want_frames}"),
                detail={"planned_frames": want_frames,
                        "actual_frames": item.duration_frames,
                        "start_frame": want_start}))

    for item in items:
        if item.start_frame in matched:
            continue
        findings.append(Finding(
            finding_class=FindingClass.F21, reel=reel_name,
            message=(
                f"an item on V{track} at reel frame "
                f"{item.start_frame} ({item.name or 'unnamed'}) that no "
                f"recorded explainer accounts for"),
            detail={"start_frame": item.start_frame,
                    "name": item.name,
                    "render_prefix": RENDER_PREFIX}))

    # PER ROW. The defect is one build having run twice into one
    # timeline, which stacks two items on one row; two items that
    # overlap on DIFFERENT rows are two graphics the plan deliberately
    # puts on screen together (the captain's Reel 26 request), and
    # reading them as one row would refuse the thing he asked for.
    by_row: dict = {}
    for item in items:
        by_row.setdefault(item.track_index, []).append(item)
    for row, row_items in sorted(by_row.items()):
        for earlier, later in zip(row_items, row_items[1:]):
            if later.start_frame < earlier.end_frame:
                findings.append(Finding(
                    finding_class=FindingClass.F21, reel=reel_name,
                    message=(
                        f"two explainer items overlap on V{row}: "
                        f"[{earlier.start_frame}..{earlier.end_frame}) and "
                        f"[{later.start_frame}..{later.end_frame})"),
                    detail={"first": [earlier.start_frame,
                                      earlier.end_frame],
                            "second": [later.start_frame,
                                       later.end_frame]}))
    return findings


def check_semantic_visuals(reel_name: str,
                           semantic_items: Sequence[TimelineItem],
                           planned: Optional[dict],
                           fps: float) -> List[Finding]:
    """F22: the semantic visuals, against the record THE BUILD WROTE.

    The same shape as F21 beside it, for the same reasons:

    - a reel whose record says `planned` and whose timeline carries no
      semantic visual. A reel that quietly starts without one is
      indistinguishable from a reel the model planned none for;
    - an item on the semantic track that no record accounts for - the
      out-of-band append `bookends.assert_no_invented_bookends`
      refuses on the master, refused here too;
    - a visual at the wrong reel second, or with the wrong number of
      frames;
    - two semantic items overlapping, which is one build having run
      twice into one timeline.

    `planned` is the entry `reel_semantic_visual.read_records` returns
    for this reel - **read from what the build recorded, never
    re-derived**.

    Given no recorded plan at all this returns NOTHING rather than
    guessing: a reel built before semantic visuals existed has no
    record, and grading it against an absence would fail every correct
    reel. A record whose basis is `awaiting_model_answer` with nothing
    placed is REPORTED as a warning - the reel owes a model answer and
    built without one, which must be named rather than graded as a
    clean pass.
    """
    findings: List[Finding] = []
    items = sorted(semantic_items or [], key=lambda i: i.start_frame)
    # The row the items are actually on - the plan owns the index, so
    # the message reads it off the timeline rather than a constant
    # (V6 on a two-angle reel without the look, V7 under it).
    track = items[0].track_index if items else SEMANTIC_TRACK
    if planned is None:
        if items:
            findings.append(Finding(
                finding_class=FindingClass.F22, reel=reel_name,
                message=(
                    f"{len(items)} item(s) on the semantic track (V"
                    f"{track}) and this reel has no recorded "
                    f"semantic-visual plan at all, so nothing accounts "
                    f"for them"),
                detail={"items": len(items), "planned": None}))
        return findings

    expected = list(planned.get("segments") or [])
    if not expected and planned.get("basis") not in (None, ""):
        # The record says why it placed nothing. An item present anyway
        # is out of band whatever the reason was.
        if items:
            findings.append(Finding(
                finding_class=FindingClass.F22, reel=reel_name,
                message=(
                    f"{len(items)} item(s) on V{track} but the "
                    f"build recorded no semantic visual for this reel "
                    f"({planned.get('basis')})"),
                detail={"items": len(items),
                        "basis": planned.get("basis")}))
            return findings
        if planned.get("basis") == SEMANTIC_AWAITING_ANSWER:
            # The build owes a model answer for this reel and built
            # without visuals. That is the normal state of a headless
            # build - it never blocks on a model - so this REPORTS as
            # a warning rather than failing the gate: a build the
            # captain wants to look at is still worth building, but a
            # reel that is waiting on its plan must be NAMED rather
            # than grading as a clean pass with nothing said about
            # its incompleteness (AGENTS.md 10.4).
            findings.append(Finding(
                finding_class=FindingClass.F22, reel=reel_name,
                message=(
                    f"no model answer on file for this reel's "
                    f"semantic-visual ask ({SEMANTIC_AWAITING_ANSWER}): "
                    f"it built without visuals and is incomplete, "
                    f"not finished"),
                severity="warning",
                detail={"items": 0,
                        "basis": planned.get("basis")}))
        return findings

    # Paired by RECORD FRAME, never by list index - the mistake that
    # turned F2 into 701 meaningless findings.
    by_frame = {item.start_frame: item for item in items}
    matched = set()
    for segment in expected:
        want_start = int(round(float(segment["timeline_start"]) * fps))
        want_frames = int(segment["total_frames"])
        item = by_frame.get(want_start)
        if item is None:
            near = min((abs(i.start_frame - want_start), i) for i in items) \
                if items else None
            findings.append(Finding(
                finding_class=FindingClass.F22, reel=reel_name,
                message=(
                    f"semantic visual planned at reel frame {want_start} "
                    f"({float(segment['timeline_start']):.2f}s) and no item "
                    f"on the semantic track (V{track}) starts there"
                    + (f"; nearest is frame {near[1].start_frame}"
                       if near else "; the track is empty")),
                detail={"planned_start_frame": want_start,
                        "planned_frames": want_frames,
                        "found": [i.start_frame for i in items]}))
            continue
        matched.add(item.start_frame)
        if item.duration_frames != want_frames:
            findings.append(Finding(
                finding_class=FindingClass.F22, reel=reel_name,
                message=(
                    f"semantic visual at reel frame {want_start} runs "
                    f"{item.duration_frames} frames, planned {want_frames}"),
                detail={"planned_frames": want_frames,
                        "actual_frames": item.duration_frames,
                        "start_frame": want_start}))

    for item in items:
        if item.start_frame in matched:
            continue
        findings.append(Finding(
            finding_class=FindingClass.F22, reel=reel_name,
            message=(
                f"an item on V{track} at reel frame "
                f"{item.start_frame} ({item.name or 'unnamed'}) that no "
                f"recorded semantic visual accounts for"),
            detail={"start_frame": item.start_frame,
                    "name": item.name,
                    "render_prefix": SEMANTIC_RENDER_PREFIX}))

    # PER ROW. The defect is one build having run twice into one
    # timeline, which stacks two items on one row; two items that
    # overlap on DIFFERENT rows are two graphics the plan deliberately
    # puts on screen together (the captain's Reel 26 request), and
    # reading them as one row would refuse the thing he asked for.
    by_row: dict = {}
    for item in items:
        by_row.setdefault(item.track_index, []).append(item)
    for row, row_items in sorted(by_row.items()):
        for earlier, later in zip(row_items, row_items[1:]):
            if later.start_frame < earlier.end_frame:
                findings.append(Finding(
                    finding_class=FindingClass.F22, reel=reel_name,
                    message=(
                        f"two semantic items overlap on V{row}: "
                        f"[{earlier.start_frame}..{earlier.end_frame}) and "
                        f"[{later.start_frame}..{later.end_frame})"),
                    detail={"first": [earlier.start_frame,
                                      earlier.end_frame],
                            "second": [later.start_frame,
                                       later.end_frame]}))
    return findings


def check_speaker_lower_thirds(reel_name: str,
                               lower_third_items: Sequence[TimelineItem],
                               planned: Optional[dict],
                               fps: float) -> List[Finding]:
    """F24: the speaker lower thirds, against the plan THE BUILD WROTE.

    The two directions F21 and F22 carry, for the same reasons - a
    recorded segment with no placed item is a name the viewer never
    saw, and an item no plan accounts for is an out-of-band append -
    plus the one this layer has and they do not.

    **A speaker may be named ONCE.**  "on the first appearance" is the
    whole of what the captain asked for on 2026-09-12, so a reel whose
    recorded plan introduces one speaker twice is a defect even where
    every item it placed matches that plan.  That half reads the plan's
    own `introductions`, which is where the once-per-speaker decision
    was made, rather than counting items: two speakers introduced at
    the same second are legitimately two entries in ONE segment, and
    counting items would refuse them.

    `planned` is `speaker_identity.plan_for`'s entry for this reel -
    **read from what the build recorded, never re-derived**, the same
    discipline F21 and F22 keep.  None means this build recorded
    nothing, which is what every reel built before this layer existed
    looks like, and grading one against an absence would fail a correct
    reel.
    """
    findings: List[Finding] = []
    items = sorted(lower_third_items or [], key=lambda i: i.start_frame)
    track = items[0].track_index if items else 0
    if planned is None:
        if items:
            findings.append(Finding(
                finding_class=FindingClass.F24, reel=reel_name,
                message=(
                    f"{len(items)} item(s) on the lower-third row (V"
                    f"{track}) and this reel has no recorded speaker "
                    f"lower-third plan at all, so nothing accounts for "
                    f"them"),
                detail={"items": len(items), "planned": None}))
        return findings

    # ONCE PER SPEAKER, off the plan's own introductions.
    seen: Dict[str, int] = {}
    for introduction in (planned.get("introductions") or []):
        label = str(introduction.get("speaker") or "")
        seen[label] = seen.get(label, 0) + 1
    for label, count in sorted(seen.items()):
        if count > 1:
            findings.append(Finding(
                finding_class=FindingClass.F24, reel=reel_name,
                message=(
                    f"{label!r} is introduced {count} times on this reel; "
                    f"a lower third names a speaker on their FIRST "
                    f"appearance and on no other"),
                detail={"speaker": label, "introductions": count}))

    expected = list(planned.get("segments") or [])
    if not expected and planned.get("basis") not in (None, ""):
        if items:
            findings.append(Finding(
                finding_class=FindingClass.F24, reel=reel_name,
                message=(
                    f"{len(items)} item(s) on V{track} but the build "
                    f"recorded no speaker lower third for this reel "
                    f"({planned.get('basis')})"),
                detail={"items": len(items),
                        "basis": planned.get("basis")}))
        return findings

    # Paired by RECORD FRAME, never by list index.
    by_frame = {item.start_frame: item for item in items}
    matched = set()
    for segment in expected:
        want_start = int(round(float(segment["timeline_start"]) * fps))
        want_frames = int(segment["total_frames"])
        item = by_frame.get(want_start)
        if item is None:
            near = min((abs(i.start_frame - want_start), i) for i in items) \
                if items else None
            findings.append(Finding(
                finding_class=FindingClass.F24, reel=reel_name,
                message=(
                    f"speaker lower third planned at reel frame "
                    f"{want_start} "
                    f"({float(segment['timeline_start']):.2f}s) and no item "
                    f"on the lower-third row (V{track}) starts there"
                    + (f"; nearest is frame {near[1].start_frame}"
                       if near else "; the row is empty")),
                detail={"planned_start_frame": want_start,
                        "planned_frames": want_frames,
                        "found": [i.start_frame for i in items]}))
            continue
        matched.add(item.start_frame)
        if item.duration_frames != want_frames:
            findings.append(Finding(
                finding_class=FindingClass.F24, reel=reel_name,
                message=(
                    f"speaker lower third at reel frame {want_start} runs "
                    f"{item.duration_frames} frames, planned {want_frames}"),
                detail={"planned_frames": want_frames,
                        "actual_frames": item.duration_frames,
                        "start_frame": want_start}))

    for item in items:
        if item.start_frame in matched:
            continue
        findings.append(Finding(
            finding_class=FindingClass.F24, reel=reel_name,
            message=(
                f"an item on V{track} at reel frame {item.start_frame} "
                f"({item.name or 'unnamed'}) that no recorded speaker "
                f"lower third accounts for"),
            detail={"start_frame": item.start_frame, "name": item.name,
                    "render_prefix": LOWER_THIRD_RENDER_PREFIX}))
    return findings


def check_span_plan(reel_name: str,
                    planned: Optional[dict]) -> List[Finding]:
    """F23: an all-refused span picture plan is a REFUSED plan, not a still reel.

    `planned` is the entry `reel_semantic_visual.read_span_records`
    returns for this reel - **read from what the build recorded, never
    re-derived**.

    Two outcomes that look identical if you only count moments both
    reach different verdicts here, and that is the whole of this check:

    - `span_no_events_planned`: the model looked and chose stillness -
      a decision. Passes.
    - `span_every_event_dropped`: the model proposed pictures and every
      one was REFUSED - the absence of a decision surviving. Fails:
      a reel whose every planned picture was refused must not build
      green and silently.

    Given no recorded plan at all this returns NOTHING rather than
    guessing: a reel built before span planning existed has no record,
    and grading it against an absence would fail every correct reel.
    A plan the model filled (`span_events_planned`) also passes here:
    nothing places the moments yet, so there is no placement to grade
    them against - the placer, when it lands, adds that half.
    """
    if planned is None:
        return []
    if planned.get("basis") != SPAN_EVERY_EVENT_DROPPED:
        return []
    proposed = planned.get("proposed", len(planned.get("entries") or []))
    reasons = sorted({str(d.get("reason") or "?")
                      for d in (planned.get("dropped") or [])})
    return [Finding(
        finding_class=FindingClass.F23, reel=reel_name,
        message=(
            f"the model planned {proposed} span picture beat(s) and "
            f"every one was refused ({', '.join(reasons)}). An "
            f"all-refused plan is not a decision for no pictures - "
            f"the reel builds still, and silently, unless this refuses it"),
        severity="error",
        detail={"basis": SPAN_EVERY_EVENT_DROPPED,
                "proposed": proposed,
                "resolved": planned.get("resolved", 0),
                "drop_reasons": reasons})]


def check_subtitle_styling(reel_name: str,
                           caption_items: Sequence[TimelineItem],
                           video_items: Sequence[TimelineItem],
                           ) -> List[Finding]:
    """F11: Verify per-speaker subtitle differentiation on the OUTPUT.

    The captain asked for per-speaker styling as the diarization signal -
    that is how a viewer tells Craig from Akshita.  This check reads the
    CARDS THAT ARE ON THE TIMELINE, not the styling configuration:

    1. Every caption card must carry speaker attribution (derived from
       its overlay filename).  If it cannot be determined, that is a
       finding - a gate that cannot determine its input says so loudly
       rather than returning clean.

    2. If the timeline carries two speakers on V1/V2, the caption cards
       must show at least two distinct speaker slugs.  All cards from
       the same speaker means the builder applied one style to everyone.

    3. Cards from different speakers must reference different overlay
       source files.  If two speakers' cards come from overlays with
       the same source path, they were rendered identically.
    """
    findings: List[Finding] = []

    if not caption_items:
        # No captions on the timeline at all.  This is not an F11
        # styling defect - it is a coverage gap (F5) or a missing
        # overlay, and other checks catch that.
        return findings

    # ── Derive speaker attribution from each card ────────────────
    attributed: Dict[str, List[TimelineItem]] = {}
    unattributed: List[TimelineItem] = []

    for cap in caption_items:
        speaker = _caption_speaker(cap)
        if speaker:
            attributed.setdefault(speaker, []).append(cap)
        else:
            unattributed.append(cap)

    # Report cards where speaker cannot be determined
    if unattributed:
        findings.append(Finding(
            finding_class=FindingClass.F11,
            reel=reel_name,
            message=(
                f"{len(unattributed)} of {len(caption_items)} caption "
                f"cards have no speaker attribution in their filename - "
                f"cannot verify per-speaker styling"),
            severity="error",
            detail={
                "unattributed_count": len(unattributed),
                "total_captions": len(caption_items),
                "example_names": [
                    c.name or c.source_file for c in unattributed[:3]
                ],
            },
        ))

    # ── How many speakers does the video track show? ─────────────
    video_speakers = set()
    for v in video_items:
        if v.speaker:
            video_speakers.add(v.speaker)

    # If the timeline has two speakers but captions show only one
    # speaker slug, the builder applied one style to everyone.
    if len(video_speakers) >= 2 and len(attributed) < 2:
        styled_speakers = sorted(attributed.keys()) if attributed else []
        findings.append(Finding(
            finding_class=FindingClass.F11,
            reel=reel_name,
            message=(
                f"Video tracks show {len(video_speakers)} speakers "
                f"({sorted(video_speakers)}) but captions are "
                f"attributed to only {len(attributed)} "
                f"({styled_speakers}) - no visual diarization"),
            severity="error",
            detail={
                "video_speakers": sorted(video_speakers),
                "caption_speakers": styled_speakers,
            },
        ))

    # ── Cards from different speakers must use different overlays ─
    if len(attributed) >= 2:
        # Collect the set of source files per speaker
        sources_by_speaker: Dict[str, set] = {}
        for speaker, caps in attributed.items():
            sources_by_speaker[speaker] = {
                c.source_file for c in caps if c.source_file
            }

        speakers = sorted(attributed.keys())
        for i in range(len(speakers)):
            for j in range(i + 1, len(speakers)):
                s1, s2 = speakers[i], speakers[j]
                shared = sources_by_speaker.get(s1, set()) & \
                    sources_by_speaker.get(s2, set())
                if shared:
                    findings.append(Finding(
                        finding_class=FindingClass.F11,
                        reel=reel_name,
                        message=(
                            f"Speakers {s1!r} and {s2!r} share "
                            f"{len(shared)} overlay source file(s) - "
                            f"identical rendering, no visual "
                            f"diarization"),
                        severity="error",
                        detail={
                            "speaker_1": s1,
                            "speaker_2": s2,
                            "shared_sources": sorted(shared),
                        },
                    ))

    return findings


# ── Plan quality gates ───────────────────────────────────────────────

# ONE spelling of the captain's length brief, owned by the module whose
# docstring records where it came from.  It was `REEL_LENGTH_MIN = 45.0`
# and `REEL_LENGTH_MAX = 90.0` written out here until 2026-09-06, which
# is a second enumeration of a number `reel_exchange` already declares
# (AGENTS.md 10.1) - and the two could have drifted without anything
# noticing, because nothing in this file ever ran the check that used
# them.
REEL_LENGTH_MIN, REEL_LENGTH_MAX = LENGTH_GUIDANCE


def check_plan_length(reel_name: str,
                      plan_seconds: float,
                      ) -> List[Finding]:
    """Plan quality: how long the reel runs, against the guidance.

    **The measurement belongs to `library/tools/reel_quality_bar.py` and
    this is the wrapper that puts its answer in this file's vocabulary.**
    Until 2026-09-06 this function held the band as two literals of its
    own AND was called by nothing: `verify_reel` never ran it, so a reel
    running 108 seconds passed nineteen checks with the length gate
    sitting in the file untouched.  A gate that cannot fire reads as
    coverage (AGENTS.md 10.4).

    It fires now - `verify_reel` calls it - and it fires as a WARNING.
    It was an ERROR until 2026-09-06, on the reasoning that "the point at
    which a reel is JUDGED is where the captain's brief has to bite".
    The brief does not bite there, and that is not a matter of taste: it
    says "no fixed target but preferably between 45-90 seconds" and "No
    hard cap", and settles it with "a coherent 90-second reel is right, a
    stitched 47-second one is not" - 47 seconds being INSIDE the band.
    `reel_exchange.LENGTH_GUIDANCE` says the same in its own docstring.
    On the harvest batch this failed ten reels on length alone, one of
    them by 0.3 seconds.

    THE MEASUREMENT IS UNCHANGED - same band, same arithmetic, same ten
    reels reported - and only the severity moved.  The ERROR that holds
    length now is `reel_quality_bar.QB_ABSURD_LENGTH`, over
    `reel_exchange.ABSURD_SECONDS`, which is mechanical rather than
    editorial (AGENTS.md 10.5).
    """
    from library.tools.reel_quality_bar import LENGTH_GUIDANCE as _band

    low, high = _band
    if low <= plan_seconds <= high:
        return []
    side, by = (("under", low - plan_seconds) if plan_seconds < low
                else ("over", plan_seconds - high))
    return [Finding(
        finding_class=FindingClass.PQ_LENGTH,
        reel=reel_name,
        message=(
            f"runs {plan_seconds:.1f}s, {by:.1f}s {side} the "
            f"{low:.0f}-{high:.0f}s the brief PREFERS - reported, not "
            f"held against the reel"),
        severity="warning",
        detail={
            "plan_seconds": round(plan_seconds, 1),
            "minimum": low,
            "maximum": high,
            "outside_by_seconds": round(by, 1),
        },
    )]


def check_plan_speakers(reel_name: str,
                        placements: Sequence[PlannedPlacement],
                        min_speaker_seconds: float = 2.0,
                        ) -> List[Finding]:
    """Plan quality: both speakers present with real turns."""
    findings: List[Finding] = []
    by_speaker: Dict[str, float] = {}
    for p in placements:
        speaker = p.speaker or "unknown"
        by_speaker[speaker] = (by_speaker.get(speaker, 0.0)
                               + (p.source_out - p.source_in))

    speakers_with_real_turns = [
        s for s, dur in by_speaker.items()
        if dur >= min_speaker_seconds
    ]
    if len(speakers_with_real_turns) < 2:
        findings.append(Finding(
            finding_class=FindingClass.PQ_SPEAKERS,
            reel=reel_name,
            message=(
                f"only {len(speakers_with_real_turns)} speaker(s) with "
                f">= {min_speaker_seconds}s of real turns: "
                f"{by_speaker}"),
            severity="warning",
            detail={
                "speakers": dict(by_speaker),
                "speakers_with_real_turns": speakers_with_real_turns,
                "minimum_seconds": min_speaker_seconds,
            },
        ))
    return findings


def check_plan_picture_continuity(
    reel_name: str,
    span_start: float,
    span_end: float,
    master_video_items: Optional[Sequence[dict]] = None,
    ranges: Optional[Sequence[Tuple[float, float]]] = None,
) -> List[Finding]:
    """Plan quality: no picture holes in the master across the reel span.

    The F3 lesson - a span is currently validated against speech only, so
    a reel can be selected straight over a hole in the master.  This gate
    measures picture continuity so the plan does not select over a gap.

    Master-inherited holes are a warning because they are the plan's
    fault, not the build's.  The verifier reports them distinctly so the
    captain can decide whether they should fail.

    `ranges` is every master range the reel plays, measured INDEPENDENTLY:
    a closing CTA taken from elsewhere in the episode plays black over a
    hole exactly as the body would, and the dead master time BETWEEN the
    body and the closer is not a hole in the reel because the reel never
    plays it.  Omitted, the single body span is measured.
    """
    findings: List[Finding] = []
    if not master_video_items:
        return findings

    spans = list(ranges) if ranges else [(span_start, span_end)]
    if len(spans) > 1:
        out: List[Finding] = []
        for one_start, one_end in spans:
            out.extend(check_plan_picture_continuity(
                reel_name, one_start, one_end, master_video_items))
        return out

    span_start, span_end = spans[0]

    # Find gaps in master video within the span
    items_in_span = []
    for item in master_video_items:
        item_start = item.get("timeline_start", item.get("start", 0))
        item_end = item.get("timeline_end", item.get("end", 0))
        if item_end > span_start and item_start < span_end:
            items_in_span.append(item)

    if not items_in_span:
        findings.append(Finding(
            finding_class=FindingClass.PQ_PICTURE,
            reel=reel_name,
            message=(
                f"no master video items found in span "
                f"{span_start:.2f}-{span_end:.2f}s"),
            severity="warning",
            detail={
                "span_start": round(span_start, 2),
                "span_end": round(span_end, 2),
            },
        ))
        return findings

    # Check for gaps between items (by track)
    by_track: Dict[int, list] = {}
    for item in items_in_span:
        track = item.get("track_index", item.get("track", 1))
        by_track.setdefault(track, []).append(item)

    # A gap where BOTH tracks have no clip is a picture hole
    # (V2 sits above V1 on the master)
    all_intervals = []
    for track, items in by_track.items():
        for item in items:
            s = max(item.get("timeline_start", item.get("start", 0)),
                    span_start)
            e = min(item.get("timeline_end", item.get("end", 0)),
                    span_end)
            if e > s:
                all_intervals.append((s, e))

    if not all_intervals:
        return findings

    # Merge all intervals across all tracks
    all_intervals.sort()
    merged = [all_intervals[0]]
    for s, e in all_intervals[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # Find gaps in coverage
    coverage_start = max(merged[0][0], span_start)
    coverage_end = min(merged[-1][1], span_end)

    for i in range(len(merged) - 1):
        gap_start = merged[i][1]
        gap_end = merged[i + 1][0]
        gap_frames = int(round((gap_end - gap_start) * _fps()))
        if gap_frames > 0:
            findings.append(Finding(
                finding_class=FindingClass.PQ_PICTURE,
                reel=reel_name,
                message=(
                    f"master has a {gap_frames}-frame picture hole at "
                    f"{gap_start:.2f}s inside reel span"),
                severity="warning",
                detail={
                    "gap_start": round(gap_start, 2),
                    "gap_end": round(gap_end, 2),
                    "gap_frames": gap_frames,
                    "gap_seconds": round(gap_end - gap_start, 2),
                },
            ))

    return findings


# ── Verification result ──────────────────────────────────────────────

@dataclass
class ReelResult:
    """All findings for one reel, plus the numbers for the per-reel table."""
    reel_name: str
    reel_number: int
    plan_seconds: float
    plan_frames: float
    actual_frames: int
    items_expected: int
    items_actual: int
    one_frame_holes: int
    big_holes: List[dict]
    captions_expected: int
    captions_actual: int
    speech_seconds: float
    uncaptioned_seconds: float
    uncaptioned_pct: float
    short_captions: int
    edge_cuts: int
    bad_take_cuts: int
    markers: int
    findings: List[Finding]

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "warning"]


@dataclass
class VerificationReport:
    """Complete verification of all reels."""
    project_name: str
    master_timeline: str
    reel_results: List[ReelResult]
    read_only_proof: dict
    """Before/after hashes proving no timeline was modified."""
    plan_source: str = ""
    """What the verification graded against: a proposal file path, or
    'DERIVED from master'.  A verifier that silently grades against a
    re-derived plan when the real one was missing tells the captain
    something different from what he thinks he is reading."""
    provenance_findings: List[Finding] = field(default_factory=list)
    """Plan provenance findings (PLAN-MISMATCH).  Separate from per-reel
    findings because they apply to the run as a whole, not to one reel."""

    quality_bar: object = None
    """The captain's four qualities over the same plan, as a
    `reel_quality_bar.BarReport`, or None when there was no plan to hold
    them against.  Its FINDINGS are folded into the per-reel results
    above so they count; this is the object itself, kept so the JSON can
    carry the per-reel verdicts, the derived readings and the ordering -
    none of which is a finding and all of which is what "as many good
    reels as possible" needs to be able to rank."""

    motion_graphics_tightness: dict = field(default_factory=dict)
    """The tight-vs-full census over the rendered motion-graphics
    artefacts (`mg_tight_box.check_motion_graphics_dir`), or
    `{"unavailable": reason}` where no project folder or artefact dir
    could be read. Set by `run_verification`, never a finding: the
    sweep reports, the step-4.06 guard refuses."""

    @property
    def all_findings(self) -> List[Finding]:
        out = list(self.provenance_findings)
        for r in self.reel_results:
            out.extend(r.findings)
        return out

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.all_findings)

    @property
    def total_errors(self) -> int:
        return sum(1 for f in self.all_findings if f.severity == "error")

    @property
    def total_warnings(self) -> int:
        return sum(1 for f in self.all_findings if f.severity == "warning")

    def as_dict(self) -> dict:
        d = {
            "project": self.project_name,
            "master_timeline": self.master_timeline,
            "plan_source": self.plan_source,
            "read_only_proof": self.read_only_proof,
            "summary": {
                "reels_checked": len(self.reel_results),
                "total_errors": self.total_errors,
                "total_warnings": self.total_warnings,
                "passed": not self.has_errors,
            },
            "by_class": _findings_by_class(self.all_findings),
            "reels": [
                {
                    "reel_name": r.reel_name,
                    "reel_number": r.reel_number,
                    "plan_seconds": round(r.plan_seconds, 2),
                    "plan_frames": round(r.plan_frames, 1),
                    "actual_frames": r.actual_frames,
                    "items": f"{r.items_expected}/{r.items_actual}",
                    "one_frame_holes": r.one_frame_holes,
                    "captions": f"{r.captions_expected}/{r.captions_actual}",
                    "uncaptioned_seconds": round(r.uncaptioned_seconds, 1),
                    "uncaptioned_pct": round(r.uncaptioned_pct, 1),
                    "short_captions": r.short_captions,
                    "edge_cuts": r.edge_cuts,
                    "bad_take_cuts": r.bad_take_cuts,
                    "markers": r.markers,
                    "errors": len(r.errors),
                    "warnings": len(r.warnings),
                    "findings": [f.as_dict() for f in r.findings],
                }
                for r in self.reel_results
            ],
        }
        if self.provenance_findings:
            d["provenance_findings"] = [
                f.as_dict() for f in self.provenance_findings
            ]
        if self.quality_bar is not None:
            d["quality_bar"] = self.quality_bar.as_dict()
        if self.motion_graphics_tightness:
            d["motion_graphics_tightness"] = self.motion_graphics_tightness
        return d


def _findings_by_class(findings: Sequence[Finding]) -> dict:
    """Count findings by class for the summary."""
    by_class: Dict[str, dict] = {}
    for f in findings:
        entry = by_class.setdefault(f.finding_class, {
            "count": 0, "errors": 0, "warnings": 0, "reels": set()})
        entry["count"] += 1
        if f.severity == "error":
            entry["errors"] += 1
        else:
            entry["warnings"] += 1
        entry["reels"].add(f.reel)
    # Convert sets to sorted lists for JSON serialization
    for entry in by_class.values():
        entry["reels"] = sorted(entry["reels"])
    return by_class


# ── The per-reel table (human-readable output) ──────────────────────

def format_table(report: VerificationReport) -> str:
    """The per-reel table, close to the audit's format."""
    lines = []
    header = (
        f" {'#':>2} {'reel':<48} {'plan_s':>7} {'expF':>7} {'actF':>6} "
        f"{'items e/a':>9} {'1f-holes':>8} {'big hole':>12} "
        f"{'caps e/a':>8} {'speech':>7} {'uncap':>6} {'%':>5} "
        f"{'<0.5s':>5} {'edge':>4} {'cut':>4} {'mk':>4}")
    sep = "-" * len(header)

    lines.append(header)
    lines.append(sep)

    totals = {
        "items_e": 0, "items_a": 0, "holes": 0, "big": 0,
        "caps_e": 0, "caps_a": 0, "speech": 0.0, "uncap": 0.0,
        "short": 0, "edge": 0, "cut": 0, "mk": 0,
    }

    for r in sorted(report.reel_results, key=lambda x: x.reel_number):
        big_hole_str = "-"
        if r.big_holes:
            bh = r.big_holes[0]
            big_hole_str = f"{bh.get('gap_frames', 0)}f @{bh.get('frame', 0) / _fps():.1f}s"

        line = (
            f" {r.reel_number:>2} {r.reel_name:<48} "
            f"{r.plan_seconds:>7.2f} "
            f"{r.plan_frames:>7.1f} "
            f"{r.actual_frames:>6} "
            f"{r.items_expected:>4}/{r.items_actual:<4} "
            f"{r.one_frame_holes:>8} "
            f"{big_hole_str:>12} "
            f"{r.captions_expected:>4}/{r.captions_actual:<3} "
            f"{r.speech_seconds:>7.1f} "
            f"{r.uncaptioned_seconds:>6.1f} "
            f"{r.uncaptioned_pct:>5.1f} "
            f"{r.short_captions:>5} "
            f"{r.edge_cuts:>4} "
            f"{r.bad_take_cuts:>4} "
            f"{r.markers:>4}")
        lines.append(line)

        totals["items_e"] += r.items_expected
        totals["items_a"] += r.items_actual
        totals["holes"] += r.one_frame_holes
        totals["big"] += len(r.big_holes)
        totals["caps_e"] += r.captions_expected
        totals["caps_a"] += r.captions_actual
        totals["speech"] += r.speech_seconds
        totals["uncap"] += r.uncaptioned_seconds
        totals["short"] += r.short_captions
        totals["edge"] += r.edge_cuts
        totals["cut"] += r.bad_take_cuts
        totals["mk"] += r.markers

    lines.append(sep)
    uncap_pct = (totals["uncap"] / totals["speech"] * 100
                 if totals["speech"] > 0 else 0.0)
    lines.append(
        f" {'':>2} {'TOTAL':<48} "
        f"{'':>7} "
        f"{'':>7} "
        f"{'':>6} "
        f"{totals['items_e']:>4}/{totals['items_a']:<4} "
        f"{totals['holes']:>8} "
        f"{totals['big']:>12} "
        f"{totals['caps_e']:>4}/{totals['caps_a']:<3} "
        f"{totals['speech']:>7.1f} "
        f"{totals['uncap']:>6.1f} "
        f"{uncap_pct:>5.1f} "
        f"{totals['short']:>5} "
        f"{totals['edge']:>4} "
        f"{totals['cut']:>4} "
        f"{totals['mk']:>4}")

    return "\n".join(lines)


def format_findings(report: VerificationReport) -> str:
    """The findings listed by class, after the table."""
    lines = []
    for cls in sorted({f.finding_class for f in report.all_findings}):
        class_findings = [f for f in report.all_findings
                          if f.finding_class == cls]
        errors = [f for f in class_findings if f.severity == "error"]
        warnings = [f for f in class_findings if f.severity == "warning"]

        label = "ENCODING" if cls in ENCODING_CLASSES else (
            "PLANNING" if cls in PLANNING_CLASSES else (
            "PROVENANCE" if cls in PROVENANCE_CLASSES else "PLAN-QUALITY"))
        lines.append(f"\n### {cls} - {label} ({len(class_findings)} findings)")

        for f in class_findings[:20]:  # cap per class for readability
            icon = "ERROR" if f.severity == "error" else "WARN"
            lines.append(f"  [{icon}] {f.reel}: {f.message}")
        if len(class_findings) > 20:
            lines.append(f"  ... and {len(class_findings) - 20} more")

    return "\n".join(lines)


# ── Folding the quality bar into the per-reel results ────────────────

def attach_quality_bar(bar_report, reel_results: Sequence[ReelResult],
                       ) -> List[str]:
    """Put the bar's findings on the reel results they are about.

    Returns the reels whose findings reached NO result, as lines a caller
    prints.  That return value is the point of the function: a finding
    that quietly attaches to nothing makes the report read as though the
    bar had nothing to say, which is the vacuity this file keeps removing
    (AGENTS.md 10.4).

    Matched by NAME first and then by NUMBER, the same two ways
    `run_verification` matches a moment to a timeline.  A built reel's
    name carries a suffix the plan's does not - "(pipeline rebuild)",
    "(selector redraw)" - so name alone attached 20 of 25 on the field
    test and dropped five without saying so.

    DURATION is not folded: `check_plan_length` is the same measurement
    in this file's vocabulary and carrying both would report one reel's
    length twice under two codes.
    """
    from library.tools import reel_quality_bar as _bar

    by_name = {r.reel_name: r for r in reel_results}
    by_number: Dict[int, ReelResult] = {}
    for result in reel_results:
        by_number.setdefault(result.reel_number, result)

    unattached: List[str] = []
    for verdict in bar_report.verdicts:
        target = by_name.get(verdict.name) or by_number.get(verdict.number)
        if target is None:
            if verdict.findings:
                unattached.append(
                    f"{verdict.name} ({len(verdict.findings)} finding(s))")
            continue
        for finding in verdict.findings:
            if finding.code == _bar.QB_DURATION:
                continue
            target.findings.append(Finding(
                finding_class=finding.code, reel=finding.reel,
                message=finding.message, severity=finding.severity,
                detail=finding.detail))
    return unattached


# ── What the project declares about the picture ──────────────────────


def _catalog_source_sizes(project_folder: Optional[str]) -> dict:
    """`{source path: {width, height, rotation}}` from the CATALOG.

    Step 1.02 already measured every source file; F12 needs the source
    shape and nothing else, so it reads that rather than re-probing 132GB
    of MXF.  A project with no catalog comes back empty and F12 reports
    the absence by name.

    The catalog is read through `pipeline_data.json` rather than the step
    directory, for the reason `compile_manifest` states: the state file
    is the one place a step's output is guaranteed to have landed
    (AGENTS.md 10.1).
    """
    if not project_folder:
        return {}
    path = os.path.join(project_folder, "pipeline_data.json")
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return {}
    catalog = (((state.get("step_outputs") or {}).get("catalog") or {})
               .get("clip_catalog") or [])
    sizes = {}
    for entry in catalog:
        source = entry.get("source_file") or entry.get("path") or ""
        if not source:
            continue
        sizes[source] = {"width": entry.get("width"),
                         "height": entry.get("height"),
                         "rotation": entry.get("rotation", 0)}
    return sizes


def _declared_framing(project_folder: Optional[str]) -> Tuple[Optional[float],
                                                              float]:
    """`(framing_intent, crop_factor)` this project declares, or `(None, 1.0)`.

    Through `framing_intent.resolve_framing_intent`, which is the
    precedence this engine has: spine block, then project.yaml, then
    brand template, then `DEFAULT_FRAMING_INTENT`.  A reel has no spine
    block, so the top of that ladder is simply absent here.

    Returning `None` when the project cannot be read is deliberate and is
    not the same as returning the default: a run that does not know what
    was asked for must say so, and F12 does.
    """
    if not project_folder:
        return None, 1.0
    try:
        from library.tools.brand_registry import (
            project_template_name, resolve_project_template)
        from library.tools.framing_intent import (
            resolve_crop_factor, resolve_framing_intent)
        template = resolve_project_template(
            project_template_name(project_folder))
        return (resolve_framing_intent(project_folder=project_folder,
                                       template=template),
                resolve_crop_factor(project_folder=project_folder,
                                    template=template))
    except Exception:  # noqa: BLE001 - a project that cannot say
        return None, 1.0


# ── Read-only proof ──────────────────────────────────────────────────

def hash_snapshot_dict(data: dict) -> str:
    """SHA-256 of a snapshot's serializable representation."""
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Verifying one reel ───────────────────────────────────────────────

def verify_reel(plan: ReelPlan,
                timeline: ReelTimeline,
                transcript_segments: Optional[Sequence[dict]] = None,
                caption_cards: Optional[Sequence[dict]] = None,
                master_holes: Optional[Sequence[dict]] = None,
                master_fps: float = 0.0,
                caption_provenance: Optional[dict] = None,
                master_video_items: Optional[Sequence[dict]] = None,
                source_sizes: Optional[dict] = None,
                declared_intent: Optional[float] = None,
                declared_crop_factor: float = 1.0,
                look=None,
                planned_overlays: Optional[Sequence[dict]] = None,
                explainer_plan: Optional[dict] = None,
                semantic_plan: Optional[dict] = None,
                span_plan: Optional[dict] = None,
                lower_third_plan: Optional[dict] = None,
                expected_frame: Optional[Tuple[int, int]] = None,
                ) -> ReelResult:
    """Run all checks on one reel and return the result.

    `master_video_items` is what `check_plan_picture_continuity` measures
    against - every video item on the MASTER, as dicts. It returns
    nothing at all without them, so a caller that omits it gets that
    gate's silence rather than its answer.
    """
    fps = timeline.fps or _fps()
    findings: List[Finding] = []

    # F1: Picture holes
    picture_findings = check_picture_holes(
        plan.reel_name, timeline.video_items, master_holes)
    findings.extend(picture_findings)

    # F1 audio half
    audio_findings = check_audio_holes(plan.reel_name, timeline.audio_items)
    findings.extend(audio_findings)

    # F4: Item count and per-speaker duration - only against a plan that
    # demonstrably describes THIS timeline.  See
    # `check_plan_describes_timeline` for what a re-derived plan is worth.
    plan_ranges = plan.keep_ranges or ((plan.span_start, plan.span_end),)
    # A span's segments are the body's own frames, not extra ones - the
    # footage video they replace was suppressed at build time - so
    # counting them beside the ranges would lay down twice the reel.
    span_present = any(c.placement == "span" for c in plan.cards)
    # The actual side is the PICTURE extent, never the whole-timeline
    # one. `timeline.total_frames` is GetEndFrame minus GetStartFrame over
    # every track, V3 captions included, and captions are laid by
    # different arithmetic than picture (`frame_utils.span_frames`
    # per-edge rounding on reel seconds, versus per-range sums on master
    # seconds in `reel_build.placements`). The two projections of the
    # same seconds diverge by a frame at unlucky fractions: 2026-09-08,
    # reel 13, plan 1902f vs timeline 1903f on a build whose picture
    # tiled exactly, the closing caption's record end landing one frame
    # past it. Grading the picture plan against the caption-inclusive
    # extent refuses correct output (AGENTS.md 10.4); the caption tail
    # itself is F2/F15's to judge, on seconds. Hand-built timelines predate
    # `picture_frames` and fall back to the old reading.
    picture_frames = (timeline.picture_frames
                      if timeline.picture_frames is not None
                      else timeline.total_frames)
    not_this_plan = check_plan_describes_timeline(
        plan.reel_name, plan_ranges, picture_frames, fps,
        card_frames=(0 if span_present
                     else sum(c.duration_frames for c in plan.cards)),
        held_frames=plan.held_frames)
    findings.extend(not_this_plan)
    if not not_this_plan:
        findings.extend(check_item_count(
            plan.reel_name, plan.placements, timeline.video_items, fps,
            cards=plan.cards, look=look))

    # F13: the full-frame elements themselves, in both directions.
    # Runs unconditionally: "the plan declares none and the timeline
    # carries one" is exactly the half a conditional would skip.
    # `card_row_role` is the project's declared card row - None on
    # reels planned before the declaration existed, which F13 reads
    # the legacy way (cards on V1).
    findings.extend(check_full_frame_cards(
        plan.reel_name, plan.cards, timeline.video_items,
        timeline.width, timeline.height, fps,
        card_row_role=plan.card_row_role))

    # F18/F19/F20: the transition elements over this reel's cuts.
    #
    # F18 grades in both directions and runs whenever EITHER side has
    # something, so "nothing planned, something placed" is a finding
    # rather than a check that skipped itself. `planned_overlays` of None
    # and an empty V4 is the only quiet case: a project that declares no
    # element, which is most of them.
    if planned_overlays or timeline.overlay_items:
        findings.extend(check_transition_overlays(
            plan.reel_name, planned_overlays or (),
            timeline.overlay_items, fps))
        findings.extend(check_overlay_caption_coverage(
            plan.reel_name, timeline.overlay_items,
            timeline.caption_items, fps))

    # F19 always runs: its whole job is to notice a track nobody thought
    # about, so guarding it on a track nobody thought about would be
    # circular.
    findings.extend(check_unclassified_video(
        plan.reel_name, timeline.unclassified_items, fps))

    # F9: Duplicate placements at same record position
    findings.extend(check_duplicate_placements(
        plan.reel_name, timeline.video_items, fps))

    # F10: Format (resolution and frame rate). `expected_frame` is the
    # project's DECLARED delivery format, resolved by the caller - not a
    # constant here, because a constant here refuses a correct reel in
    # any other frame. None is not a pass: check_format warns.
    if timeline.width and timeline.height:
        findings.extend(check_format(
            plan.reel_name, timeline.width, timeline.height, fps,
            master_fps or fps,
            expected_width=(expected_frame[0] if expected_frame else None),
            expected_height=(expected_frame[1] if expected_frame else None)))

    # F12: The picture in the frame, not just the shape of the frame.
    # Runs only where F10 established a frame to measure against; a
    # timeline with no resolution has nothing to be a fraction OF.
    if timeline.width and timeline.height:
        findings.extend(check_delivered_framing(
            plan.reel_name, timeline.video_items,
            timeline.width, timeline.height,
            source_sizes=source_sizes,
            declared_intent=declared_intent,
            declared_crop_factor=declared_crop_factor,
            cards=plan.cards, look=look))

    # F11: Subtitle styling - reads the OUTPUT cards, not the config
    findings.extend(check_subtitle_styling(
        plan.reel_name, timeline.caption_items, timeline.video_items))

    # F16: caption names break on a word boundary, never mid-word.
    # Reads the placed names only - no plan reference needed, the
    # producer recomputes what a correct name carries - so it runs
    # unconditionally like F11 beside it.
    findings.extend(check_caption_slugs(
        plan.reel_name, timeline.caption_items))

    # The caption checks, and the emptiness that used to disable them.
    #
    # A reel built with `--skip-captions` legitimately has none on either
    # side, and that is not a defect - it is a reel nobody captioned.
    # Every OTHER combination is: cards on the timeline with nothing in
    # the plan to check them against is the vacuous case this class was
    # added for, and a plan that asked for cards the timeline does not
    # carry is the same hole seen from the other side.  Both refuse.
    findings.extend(check_caption_reference(
        plan.reel_name, plan.captions, timeline.caption_items,
        unavailable=plan.captions_unavailable))

    # Below here every check needs a reference set. When it could not be
    # derived at all, the refusal above IS the finding - running these on
    # a guess would be the vacuity, one layer down.
    have_reference = not plan.captions_unavailable

    # F2: Caption card duration - GRADED ONLY against a recorded baseline.
    #
    # F2 and F14 compare a plan to a placement, and the plan they compare
    # is RE-DERIVED here from today's code. That is only the build's plan
    # while nothing has changed in between, and on the captain's nineteen
    # something had: the moment hash matched, and today's planner produced
    # 39 cards where the build placed 28. Grading that produced 701
    # findings that read as a placement defect and were not one.
    #
    # So absence REFUSES rather than passing quietly - the same rule the
    # NO_REFERENCE class already applies to an empty caption plan.
    gradeable = bool(have_reference and plan.captions
                     and timeline.caption_items)
    may_grade, why = check_captions_match_provenance(
        plan.reel_name, plan.captions, caption_provenance)
    if gradeable and not may_grade:
        findings.append(Finding(
            finding_class=FindingClass.NO_REFERENCE,
            reel=plan.reel_name,
            message=why,
            severity="error",
            detail={"check": "F2/F14", "graded": False},
        ))
    if gradeable and may_grade:
        findings.extend(check_caption_duration(
            plan.reel_name, plan.captions, timeline.caption_items, fps))

    # F6 reads the CARDS rather than the placed items, because it
    # measures overlap against what the plan asked for.  The
    # plan's own derived cards are the reference when the caller passes
    # none, which is every real run: `verify_built_reels` never passed
    # `caption_cards`, so it was skipped on live builds even before the
    # plan side was empty.
    #
    # F5 no longer reads these - it reads the cards ON THE TIMELINE, for
    # the reason spelled out below.  F7 joined it: it reads the placed
    # caption ITEMS, because a card that was never placed is never on
    # screen and a flash that WAS placed is the defect.  F6 still grades
    # the re-derived plan, which is the same reference F2 above now
    # REFUSES without a recorded baseline; extending that refusal to it
    # is owed work, not something this comment should imply is already
    # done.
    cards = caption_cards if caption_cards is not None else [
        {"reel_start": c.start_seconds, "reel_end": c.end_seconds,
         "text": c.text, "speaker": c.speaker, "frames": c.frames,
         "block_position": c.block_position,
         "block_end": c.block_end_seconds}
        for c in plan.captions]

    # F5: Caption coverage, measured against THE CARDS ON THE TIMELINE.
    #
    # F5 answers "how much speech plays with nothing on screen", and only
    # a card that was placed is on screen.  It used to be handed `cards`
    # - the plan's cards, RE-DERIVED here from today's code - and on the
    # captain's nineteen that reference is not what was built: 832 cards
    # derived against 763 placed, differing on every reel.  Grading
    # coverage against cards that do not exist reported 95.1s of
    # uncaptioned speech where the placed cards leave 6.3s, and 19.9s on
    # reel 05 where the placed cards leave none.
    #
    # This is the rule F11 already applies one check along - read the
    # cards on the timeline, not the config that asked for them - and
    # for the same reason: a gate that grades the request rather than
    # the output passes when the output disagrees with it.
    #
    # A reel with no cards at all is NOT measured here: that is either a
    # `--skip-captions` build or captions planned and never built, and
    # `check_caption_reference` above has already said which.
    placed_cards = [
        {"reel_start": item.start_frame / fps,
         "reel_end": (item.start_frame + item.duration_frames) / fps,
         "text": item.name,
         "speaker": _caption_speaker(item),
         "frames": item.duration_frames}
        for item in timeline.caption_items]

    if have_reference and transcript_segments and placed_cards:
        findings.extend(check_caption_coverage(
            plan.reel_name, transcript_segments, placed_cards,
            plan.keep_ranges or [(plan.span_start, plan.span_end)],
            fps, lead_seconds=plan.lead_seconds))

    # F6: Caption overlap
    if have_reference and cards:
        findings.extend(check_caption_overlaps(
            plan.reel_name, cards, fps))

    # F7: short placed items - caption cards, then picture and sound.
    #
    # The caption half reads the CARDS ON THE TIMELINE (`placed_cards`
    # above), never the plan's derived cards: on the captain's nineteen
    # 832 cards derived against 763 placed, and the three flash cards
    # the 2026-09-08 report names - R02 card 0 "yeah." 3f, R03 card 3
    # "yeah" 3f, R19 card 32 "yeah." 2f - are placed items.  There is no
    # last-of-block exemption: each was trivially the last card of its
    # own block, which is why every gate HELD them.  See
    # `check_short_captions` for the exemption's intent and why it
    # cannot survive here.
    #
    # The A/V half applies the same floor to placed video and audio
    # items - the keep-range slivers (R02's 3f chirp, R04's 10f and 5f
    # fragments, R10's 6f blip) play as orphaned word-slivers most of
    # which carry no card at all.  It measures only the timeline, so it
    # needs no plan reference; the caption half keeps the reference
    # gate its siblings use.
    if have_reference and placed_cards:
        findings.extend(check_short_captions(
            plan.reel_name, placed_cards, fps,
            declared_short=plan.declared_short_captions))
    if timeline.video_items or timeline.audio_items:
        findings.extend(check_short_av_items(
            plan.reel_name, timeline.video_items, timeline.audio_items,
            fps))

    # F15: Caption hangs - a card whose duration far exceeds its speech.
    # Both halves: the plan side grades what was asked for, the placed
    # side grades what is on the timeline. The placed half reports
    # DIVERGENCE only (a pair the plan already hangs is the plan side's
    # finding), so one defect is never counted twice - and a span that
    # is correct in the plan and hanging on the timeline, which is what
    # reel 05 shipped, is the placed half's alone.
    if have_reference and cards:
        findings.extend(check_caption_hangs(
            plan.reel_name, cards, fps))
    if have_reference and cards and timeline.caption_items:
        findings.extend(check_placed_caption_hangs(
            plan.reel_name, cards, timeline.caption_items, fps))

    # F17: Mixed speakers on one card (requires transcript for diarisation)
    if have_reference and cards and transcript_segments:
        findings.extend(check_mixed_speakers(
            plan.reel_name, cards, transcript_segments,
            plan.keep_ranges or [(plan.span_start, plan.span_end)],
            fps, lead_seconds=plan.lead_seconds))

    # The spans a viewer hears an edge of: the body, and the closer if
    # the reel has one.  NOT the bad-take seams inside the body, which
    # `keep_ranges` also carries - those were never boundaries these
    # checks looked at, and a reel with no closer must measure exactly
    # what it measured before.
    heard_spans = [(plan.span_start, plan.span_end)]
    if plan.call_to_action:
        heard_spans.append(plan.call_to_action)

    # F8: Boundary speech, at every edge of every span heard - a reel
    # closing on a CTA from elsewhere has four, and the closer's two
    # decide whether it ends on a finished sentence.
    if transcript_segments:
        findings.extend(check_boundary_speech(
            plan.reel_name, plan.span_start, plan.span_end,
            transcript_segments, ranges=heard_spans))

    # Plan quality gates
    findings.extend(check_plan_length(plan.reel_name, plan.plan_seconds))
    findings.extend(check_plan_speakers(plan.reel_name, plan.placements))
    if master_holes is not None:
        # `master_video_items` is what this check MEASURES, and until
        # 2026-09-06 no caller passed it - `check_plan_picture_continuity`
        # returns an empty list on its second line without them, so the
        # gate ran on every reel and could not produce a finding
        # (AGENTS.md 10.4). `run_verification` builds them off the same
        # master snapshot `master_holes` comes from.
        #
        # The RANGES are what the reel PLAYS, not its body window: a
        # bad-take cut removes seconds the viewer never sees, so a master
        # hole inside one is not a hole in the reel. `plan.keep_ranges`
        # is that list and already carries the closer last;
        # `heard_spans` - the body whole, plus the closer - is the
        # fallback for a plan that has none.
        findings.extend(check_plan_picture_continuity(
            plan.reel_name, plan.span_start, plan.span_end,
            master_video_items=master_video_items,
            ranges=plan.keep_ranges or heard_spans))

    # F21: the animated explainer, against the plan the build recorded.
    # Passing None means "no record", which returns nothing rather than
    # grading a pre-explainer build against an absence.
    findings.extend(check_explainer(
        plan.reel_name, timeline.explainer_items, explainer_plan, fps))

    # F22: the semantic visuals, against the record the build wrote.
    # Passing None means "no record", which returns nothing rather than
    # grading a pre-semantic build against an absence.
    findings.extend(check_semantic_visuals(
        plan.reel_name, timeline.semantic_items, semantic_plan, fps))

    # F24: the speaker lower thirds, against the plan the build wrote.
    # Passing None means "no record", which returns nothing rather than
    # grading a build from before this layer against an absence.
    findings.extend(check_speaker_lower_thirds(
        plan.reel_name, timeline.lower_third_items, lower_third_plan, fps))

    # F23: the span picture plan, against the record the build wrote.
    # Passing None means "no record", which returns nothing rather than
    # grading a pre-span build against an absence.
    findings.extend(check_span_plan(plan.reel_name, span_plan))

    # Compute summary numbers
    one_frame_holes = sum(
        1 for f in findings
        if f.finding_class == FindingClass.F1
        and f.detail and f.detail.get("gap_frames") == 1)
    big_holes = [
        f.detail for f in findings
        if f.finding_class in (FindingClass.F1, FindingClass.F3)
        and f.detail and f.detail.get("gap_frames", 0) > 2]
    short_caps = sum(1 for f in findings
                     if f.finding_class == FindingClass.F7
                     and f.severity == "error")
    edge_cuts = sum(1 for f in findings
                    if f.finding_class == FindingClass.F8)

    # Speech and caption numbers
    speech_seconds = 0.0
    uncaptioned_seconds = 0.0
    for f in findings:
        if f.finding_class == FindingClass.F5 and f.detail:
            uncaptioned_seconds += f.detail.get("straddling_seconds", 0)

    return ReelResult(
        reel_name=plan.reel_name,
        reel_number=plan.reel_number,
        plan_seconds=plan.plan_seconds,
        plan_frames=plan.plan_frames,
        actual_frames=timeline.total_frames,
        items_expected=len(plan.placements),
        items_actual=len([i for i in timeline.video_items
                          if i.track_index in (1, 2)]),
        one_frame_holes=one_frame_holes,
        big_holes=big_holes,
        captions_expected=len(plan.captions),
        captions_actual=len(timeline.caption_items),
        speech_seconds=speech_seconds,
        uncaptioned_seconds=uncaptioned_seconds,
        uncaptioned_pct=(
            uncaptioned_seconds / speech_seconds * 100
            if speech_seconds > 0 else 0.0),
        short_captions=short_caps,
        edge_cuts=edge_cuts,
        bad_take_cuts=len(plan.cuts),
        markers=len(timeline.markers),
        findings=findings,
    )


def _is_layer_named(row: str, base: str) -> bool:
    """Whether a track name is this layer's row, numbered or not.

    `timeline_layout._layered_names` gives the first row the bare name
    and numbers the rest ("Semantic", "Semantic 2"), which is how two
    tight-box animations that play at once get a row each. Matching the
    bare name alone would file every further row as unclassified and
    report a correct build as carrying items nothing grades.
    """
    row = (row or "").strip()
    if row == base:
        return True
    if row.startswith(base + " "):
        return row[len(base) + 1:].isdigit()
    return False


# ── Converting timeline_ingest snapshots to our types ────────────────

def _snapshot_to_reel_timeline(snapshot, cards=()) -> ReelTimeline:
    """Convert a TimelineSnapshot to our ReelTimeline.

    This is the bridge between `timeline_ingest` (which reads Resolve)
    and our check functions (which are pure and tested without Resolve).

    `cards` are the plan's full-frame elements (anything with a
    `render_name`): an item the plan names is PICTURE wherever it sits,
    filed into `video_items` before any role bucketing reads its row.
    Cards ride the declared card row - an overlay row - while spans
    stay on the picture row, and a row-name bucketing would file the
    former as decoration. Without the plan's names the classifier
    cannot tell a closing card from a semantic visual, so `()` keeps
    the legacy buckets exactly.
    """
    fps = snapshot.fps
    wanted = {getattr(card, "render_name", "") or "" for card in cards}
    wanted.discard("")

    video_items = []
    audio_items = []
    caption_items = []
    overlay_items = []
    unclassified_items = []
    explainer_items = []
    semantic_items = []
    lower_third_items = []
    frame_items = []
    for clip in snapshot.clips:
        item = TimelineItem(
            track_type=clip.track_type,
            track_index=clip.track_index,
            start_frame=int(round(clip.timeline_start * fps)),
            end_frame=int(round(clip.timeline_end * fps)),
            duration_frames=int(round(clip.duration * fps)),
            source_start_frame=clip.source_in_frame,
            source_end_frame=clip.source_out_frame,
            source_file=clip.source_file,
            speaker=clip.speaker,
            name=clip.name,
            unique_id=clip.resolve_item_id,
            track_name=getattr(clip, "track_name", "") or "",
            transform=dict(getattr(clip, "transform", None) or {}),
        )
        row = (item.track_name or "").strip()
        is_video = clip.track_type == "video"
        # The plan's own elements first, by render name: a card the
        # plan declares IS picture wherever it sits - on the declared
        # card row (an overlay row) or on the picture row (a span) -
        # so its row must not decide its bucket. Names first, across
        # ALL roles, then indices. A packed reel
        # puts its semantic row where transitions would sit (V4) - so
        # an index fallback consulted role-by-role would file a row
        # named "Semantic" as a transition element before its name is
        # ever read. Indices answer only for rows nobody named, which
        # is every timeline built before rows were named. The frame
        # row is named first of all: under the Reel 09 ruling it sits
        # on V3 above two picture rows, where the old `== 3` fallback
        # would file the set as a caption card.
        if is_video and _item_stem(item) in wanted:
            video_items.append(item)
        elif is_video and row == "Frame":
            frame_items.append(item)
        elif is_video and clip.track_index <= 2:
            video_items.append(item)
        elif is_video and row in ("Subtitles", "Captions"):
            caption_items.append(item)
        elif is_video and row == "Transitions":
            overlay_items.append(item)
        elif is_video and _is_layer_named(row, "Explainer"):
            explainer_items.append(item)
        elif is_video and _is_layer_named(row, "Semantic"):
            semantic_items.append(item)
        elif is_video and _is_layer_named(row, LOWER_THIRD_TRACK_NAME):
            lower_third_items.append(item)
        elif is_video and clip.track_index == 3:
            caption_items.append(item)
        elif is_video and clip.track_index == OVERLAY_TRACK:
            overlay_items.append(item)
        elif is_video and clip.track_index == EXPLAINER_TRACK:
            explainer_items.append(item)
        elif is_video and clip.track_index == SEMANTIC_TRACK:
            semantic_items.append(item)
        elif clip.track_type == "audio":
            audio_items.append(item)
        elif clip.track_type == "video":
            # Every video track this verifier grades with nothing. Kept and
            # reported by F19 rather than discarded - see
            # `ReelTimeline.unclassified_items`.
            unclassified_items.append(item)

    return ReelTimeline(
        reel_name=snapshot.timeline_name,
        fps=fps,
        total_frames=snapshot.end_frame - snapshot.start_frame,
        # The picture extent from the timeline origin, for the
        # picture-scoped length gate in `verify_reel`. `video_items` here
        # is picture only (footage and full-frame cards - the classifier
        # above files captions, overlays, the explainer and semantic
        # rows, and the Frame set row, into their own buckets), so a
        # caption tail past the last picture frame does not read as a
        # longer reel. A card on the declared card row is picture too -
        # the classifier files it here by render name whatever its
        # track - so the extent reads it wherever it sits. Empty when
        # the timeline carries no picture at all, which is itself a
        # refusal unless the plan is empty too.
        picture_frames=(
            max((i.end_frame for i in video_items
                  if i.track_index in (1, 2)
                  or _item_stem(i) in wanted),
                 default=snapshot.start_frame)
            - snapshot.start_frame),
        video_items=tuple(sorted(video_items,
                                 key=lambda i: (i.track_index, i.start_frame))),
        audio_items=tuple(sorted(audio_items,
                                 key=lambda i: (i.track_index, i.start_frame))),
        caption_items=tuple(sorted(caption_items,
                                   key=lambda i: i.start_frame)),
        overlay_items=tuple(sorted(overlay_items,
                                   key=lambda i: i.start_frame)),
        unclassified_items=tuple(sorted(
            unclassified_items, key=lambda i: (i.track_index, i.start_frame))),
        explainer_items=tuple(sorted(explainer_items,
                                     key=lambda i: i.start_frame)),
        semantic_items=tuple(sorted(semantic_items,
                                    key=lambda i: i.start_frame)),
        lower_third_items=tuple(sorted(lower_third_items,
                                       key=lambda i: i.start_frame)),
        frame_items=tuple(sorted(frame_items,
                                 key=lambda i: i.start_frame)),
        width=snapshot.width,
        height=snapshot.height,
    )


def _reel_look_declaration(project_folder: str):
    """The project's TV-frame look, or None - for the tail element's own
    timings, which a project may redeclare through it."""
    if not project_folder:
        return None
    try:
        from library.tools import reel_look as _reel_look

        from library.tools.delivery_format import resolve_delivery_format

        frame_w, frame_h = resolve_delivery_format(project_folder)
        return _reel_look.resolve_look(project_folder, frame_w, frame_h)
    except Exception:  # noqa: BLE001 - a look this module cannot read is
        # not this module's refusal to make: the BUILD refuses on it, and
        # the element's own declared timings are the answer meanwhile.
        return None


def _derive_plan_from_master(
    reel_name: str,
    reel_number: int,
    span_start: float,
    span_end: float,
    master_snapshot,
    transcript: Optional[dict] = None,
    call_to_action: Optional[Tuple[float, float]] = None,
    moment=None,
    project_folder: str = "",
) -> ReelPlan:
    """Derive what the plan says about one reel from the master timeline.

    Uses the repo's own build arithmetic: reel_build.redundant_takes,
    keep_ranges, placements - the same route the audit used to re-derive
    the plan.

    `call_to_action` is the moment's closing CTA range, which may come
    from ANYWHERE on the master and is laid down LAST.  It has to be
    re-derived here or the whole verification reads a reel built with one
    as defective: the built timeline would carry picture items the
    derived plan never listed, and F4 would report every such reel as
    "planned N items, found N+k" - a hard error, on a correct build.
    `plan_seconds` would be short by the CTA's length everywhere it is
    reported or compared, including `render_check.check_duration`.
    """
    from library.tools.reel_build import (
        keep_ranges as compute_keep_ranges,
        lead_frames as compute_lead_frames,
        placements as compute_placements,
        plan_cards as compute_cards,
        redundant_takes,
    )

    fps = master_snapshot.fps

    # Compute cuts (bad takes removed)
    cuts = ()
    kr = [(span_start, span_end)]
    if transcript:
        cuts_list = redundant_takes(span_start, span_end, transcript)
        # The captain's recorded keep INSISTENCES withdraw take cuts,
        # and they must be withdrawn HERE too: a reel cannot be built
        # to one rule and checked against another, and without this
        # every withdrawn cut reads as a plan mismatch on the seconds
        # he asked to keep.
        if project_folder:
            from library.tools import transcript_corrections as _insist
            from library.tools.reel_build import withdraw_insisted_cuts
            cuts_list, _ = withdraw_insisted_cuts(
                cuts_list,
                _insist.insisted_spans_for_span(
                    span_start, span_end,
                    _insist.keep_insistences(project_folder)))
        # And the take judge withdraws structurally indefensible
        # candidates HERE too, in the same order `reel_ranges` applies
        # them - insistence first, judge second - for the same reason:
        # a reel cannot be built to one rule and checked against
        # another.
        from library.tools.reel_build import judge_take_cuts
        cuts_list = judge_take_cuts(cuts_list, span_start, span_end,
                                    transcript or {})[0]
        cuts = tuple(cuts_list)
        kr = compute_keep_ranges(span_start, span_end, cuts_list)
        # Wordless islands the take cuts strand between them are
        # absorbed HERE too, in the same place `reel_ranges` absorbs
        # them - a reel cannot be built to one rule and checked
        # against another, and without this every absorbed island
        # reads as "planned N items, found N-1".
        from library.tools.reel_build import absorb_wordless_take_gaps
        kr = absorb_wordless_take_gaps(kr, cuts_list, transcript or {})
        # The captain's recorded strikes, cut the same way the builder
        # cuts them (`reel_build.reel_ranges(extra_cuts=...)`): a reel
        # cannot be built to one rule and checked against another, and
        # without this every honoured strike reads as "planned N items,
        # found N+k". Plain intervals here - the take-wholeness guard
        # is about takes, and a strike keeps no take.
        if project_folder:
            from library.tools import transcript_corrections as _tc
            from library.tools.reel_build import (
                absorb_wordless_remnants as _absorb,
                subtract_interval_cuts as _subtract,
            )
            struck = _tc.grow_cuts_over_wordless_leadin(
                _tc.exclusion_cuts_for_span(
                    span_start, span_end,
                    _tc.keep_exclusions(project_folder)),
                transcript or {})
            # BOTH edges, exactly as the build grows them - a plan
            # derived with one growth and a timeline placed with two
            # disagree by construction.
            struck, _ = _tc.grow_cuts_over_wordless_tail(
                struck, transcript or {})
            if struck:
                kr, _ = _absorb(
                    _subtract(kr, [(s, e) for s, e, _ in struck]),
                    struck, transcript or {})

    # The closer, whole and last - the same order `reel_build.reel_ranges`
    # builds it in, and the reason a reel cannot be built to one order and
    # checked against another.
    closer = None
    if call_to_action and call_to_action[1] - call_to_action[0] > 0.04:
        closer = (float(call_to_action[0]), float(call_to_action[1]))
        kr = list(kr) + [closer]

    # Compute placements from master clips
    master_clips = master_snapshot.picture_clips()

    # WHERE THIS REEL ENDS (`library/tools/reel_ending.py`), re-derived
    # with the same code the build used and at the same seam - after
    # the closer is appended and before cards, captions and placements
    # read the ranges. This function's own premise: a reel cannot be
    # built to one rule and checked against another. Without it the
    # re-derived plan still reaches past the master's cut and
    # PLAN-MISMATCH reports the truncated picture as a defect.
    ending_declaration = None
    if project_folder:
        from library.tools import reel_ending as _ending

        # The MOMENT and the TRANSCRIPT travel for the same reason the
        # rest of this re-derivation does: a reel with no pin still
        # ends somewhere, because it INHERITS an ending from the call
        # to action it closes on. Resolving without them here would
        # re-derive a plan with no freeze in it and report every
        # inheriting reel's held frame as an item the plan does not
        # have.
        ending_declaration = _ending.resolve_ending(
            project_folder, reel_name, moment, transcript or {})
        if ending_declaration is not None:
            kr, _ending_record = _ending.apply_ending(
                kr, compute_placements(kr, master_clips, fps),
                transcript or {}, ending_declaration, fps)
    # `fps` is REQUIRED: `placements` computes each clip's record frame
    # from it (PR #524), and calling without it raised on every run -
    # `verify_built_reels` caught the TypeError and re-raised it as
    # "Reel conformance verifier failed to run", so the verifier the
    # build gate depends on could not run at all.
    # The reel's FULL-FRAME elements, re-derived from the project's own
    # declaration with the same code the build used - the same principle
    # as the placements above, and for the same reason: a reel cannot be
    # built to one rule and checked against another. A project that
    # declares none gets `()` here, which is every project that has not
    # opted in.
    cards: Tuple[PlannedCard, ...] = ()
    lead_frames = 0
    # The declared CARD row, re-derived with the same code the build
    # used (`reel_build.card_row_role_for_project`) - the same
    # principle as the cards below, and for the same reason: a reel
    # cannot be built to one rule and checked against another. None
    # where nothing declares one, and then F13 reads the cards the
    # legacy way (on V1).
    card_row_role = None
    if project_folder:
        from library.tools.reel_build import (
            card_row_role_for_project as _card_row,
        )
        card_row_role = _card_row(project_folder)
    if moment is not None and project_folder:
        # The ENDING travels, because a freeze holds picture after the
        # keep ranges and a tail element starts after it
        # (`reel_ending.ending_tail_frames`). Re-deriving without it
        # would place every declared closing element 19 frames early
        # and report the built one as a plan mismatch.
        planned_cards = compute_cards(
            moment, transcript or {}, kr, project_folder, fps,
            ending=ending_declaration,
            look=_reel_look_declaration(project_folder))
        cards = tuple(PlannedCard(render_name=c.render_name,
                                  placement=c.placement,
                                  reel_start_frame=c.reel_start_frame,
                                  duration_frames=c.duration_frames,
                                  element=c.element)
                      for c in planned_cards)
        lead_frames = compute_lead_frames(planned_cards, fps)

    # A span replaces the footage video for the whole body
    # (`reel_build.build_reel_timeline` suppresses it where a span
    # plays), so the re-derived picture plan is empty rather than the
    # ranges: expecting the suppressed clips here would report every
    # span-built reel as "planned N items, found 0".  Cards never take
    # this branch - a card sits beside the footage, not in place of it.
    span_present = any(c.placement == "span" for c in cards)
    placed = ([] if span_present
              else compute_placements(kr, master_clips, fps,
                                      lead_frames=lead_frames))

    # The declared FREEZE, re-derived with the owner's own planner:
    # the hold is a picture clip on the ending shot's row, so a plan
    # that leaves it out reports the reel as one item and 18 frames
    # longer than "the plan" and PLAN-MISMATCH refuses the whole
    # grading. Re-derived, never rendered - the verifier reads.
    freeze_plan = None
    if ending_declaration is not None and placed:
        from library.tools import reel_ending as _ending

        freeze_plan = _ending.plan_freeze(
            placed, ending_declaration, fps,
            look=_reel_look_declaration(project_folder))
        if freeze_plan is not None:
            placed = list(placed) + [
                _ending.freeze_placement(freeze_plan, fps)]

    planned_placements = tuple(
        PlannedPlacement(
            track_index=p["track_index"],
            speaker=p.get("speaker"),
            record_seconds=p["record"],
            source_in=p["source_in"],
            source_out=p["source_out"],
            source_file=p["clip"].source_file,
        )
        for p in placed
    )

    # The reel's length is what a viewer watches, cards included: a card
    # occupies reel time rather than sitting over it, so leaving it out
    # would report a 60s reel as 55s to PQ-LENGTH and to
    # `render_check.check_duration`.  A span's segments ARE the body's
    # seconds rather than extra ones - they abut over exactly the keep
    # ranges - so adding the body again would report every span-built
    # reel at twice its length.
    card_seconds = sum(c.duration_frames for c in cards) / fps if cards else 0.0
    # A held frame occupies reel time exactly as a card does: the
    # viewer watches it, so leaving it out reports the reel short.
    freeze_seconds = (freeze_plan.duration_frames / fps
                      if freeze_plan is not None else 0.0)
    plan_seconds = (card_seconds if span_present
                    else sum(b - a for a, b in kr) + card_seconds
                    ) + freeze_seconds
    plan_frames = plan_seconds * fps
    lead_seconds = lead_frames / fps if fps else 0.0

    # A reel whose captions cannot be derived is REFUSED, not scored as
    # zero. The reason travels with the plan so the finding can name it.
    captions: Tuple[PlannedCaption, ...] = ()
    captions_unavailable: Optional[str] = None
    declared_short: Tuple[Tuple[int, int], ...] = ()
    if moment is None:
        captions_unavailable = (
            "no proposal moment was matched to this timeline, so there is "
            "nothing to build a reel spine from - `reel_proposal.read_"
            "proposal` is what supplies it")
    try:
        if captions_unavailable:
            raise CaptionsUnavailable(captions_unavailable)
        captions, declared_short = _derive_planned_captions(
            moment, kr, transcript, fps, project_folder,
            lead_seconds=lead_seconds, timeline=reel_name)
    except CaptionsUnavailable as unavailable:
        captions_unavailable = str(unavailable)

    return ReelPlan(
        reel_name=reel_name,
        reel_number=reel_number,
        plan_seconds=plan_seconds,
        plan_frames=plan_frames,
        span_start=span_start,
        span_end=span_end,
        placements=planned_placements,
        captions=captions,
        captions_unavailable=captions_unavailable,
        cards=cards,
        card_row_role=card_row_role,
        lead_seconds=lead_seconds,
        cuts=cuts,
        keep_ranges=tuple(kr),
        call_to_action=closer,
        held_frames=(freeze_plan.duration_frames
                     if freeze_plan is not None else 0),
        declared_short_captions=declared_short,
    )


#: Where the verifier asks the PIPELINE for a reel's spine.
#:
#: The expected cards come from step 4.01 - the same code that plans a
#: master's captions - run against the reel's own spine.  What turns a
#: moment into a spine is `reel_spine`, a PRODUCER and not a second
#: captioner.  Both are named here rather than imported at module scope
#: so this constant IS the dependency: a reader sees what the verifier
#: needs without reading the function.
#:
#: This used to import `reel_subtitles.reel_captions`, and that was wrong
#: twice over.  It was the parallel module the captain ruled should never
#: have existed - "the subtitles was meant to utilize the pipeline
#: subtitles step" - so the verifier checked the build against a rule
#: living beside the pipeline rather than in it.  And the import was lazy
#: inside `except ImportError: return ()`, so the day that module was
#: deleted the plan side would have gone permanently empty and the
#: caption gate would have returned to expecting zero cards, finding
#: hundreds, and passing.  A silent fallback is how that defect survived
#: the first time.
REEL_SPINE_PRODUCER = ("library.tools.reel_spine", "spine_for_reel")

#: Step 4.01, reached THROUGH THE REGISTRY rather than by importing its
#: module.  `Operation.run` resolves the step's own function and never
#: wraps it, so this is 4.01's code and not a copy of it - which is the
#: whole reason the registry exists.
SUBTITLE_PLAN_OPERATION = "subtitles.plan"


from library.tools.plan_provenance import check_captions_match_provenance

PAIRING_TOLERANCE_FRAMES = 2
"""How near a placed item must start to be THIS planned card.

Mechanical identity, not quality (AGENTS.md 10.5): it answers "which item
is this card", never "is this card right". Two frames because the audit
measured 566 of 575 cards on exactly the planned frame with the rest one
frame off, and because both sides round seconds into frames."""


class CaptionsUnavailable(Exception):
    """The pipeline could not be asked what captions to expect.

    Distinct from "this reel has no captions", which is a legitimate
    answer and not an error.  Raised so the caller REFUSES and names what
    is missing, rather than returning an empty tuple that reads exactly
    like a reel nobody captioned.
    """


def _resolve(dotted: Tuple[str, str]):
    """A module attribute by name, or None. Never a silent substitute."""
    import importlib
    module_name, attribute = dotted
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    return getattr(module, attribute, None)


def _derive_planned_captions(
    moment,
    keep_ranges: Sequence[Tuple[float, float]],
    transcript: Optional[dict],
    fps: float,
    project_folder: str = "",
    lead_seconds: float = 0.0,
    timeline: str = "",
) -> Tuple[Tuple[PlannedCaption, ...], Tuple[Tuple[int, int], ...]]:
    """The caption cards the plan says this reel should carry, and the
    ones a caption-timing PIN deliberately left under the readability
    floor.

    Asked of the PIPELINE, for the same reason `_derive_plan_from_master`
    re-derives placements from `reel_build`: a reel cannot be built to one
    rule and checked against another.  The rule is step 4.01's, so 4.01 is
    what runs - through the reel's own spine, which is what makes a step
    written for a master reachable by a reel.

    **`keep_ranges` is passed to the spine producer EXPLICITLY**, and it
    is the same tuple `_derive_plan_from_master` built the placements
    from.  `spine_for_reel` recomputes `reel_ranges` when the argument is
    omitted, and a verifier that spines against one order while checking
    a build made from another is checking a different reel -
    `reel_ranges` is the one place play order is spelled.

    Card timings come back in REEL SECONDS, which is the domain the
    timeline items are in.  They are never compared against master times:
    measured on 001, per-block offsets spread 146.5s across a 56.6s
    timeline and the sign flips, so that comparison fails silently rather
    than loudly.

    Styling is deliberately NOT resolved.  A caption's look belongs to the
    project (AGENTS.md 14) and none of F2, F5, F6 or F7 reads it - they
    read timing, coverage, overlap and length.

    RAISES `CaptionsUnavailable` when the pipeline cannot be asked at all,
    which includes a reel with no speech left on it.  It does not return
    `()`: an empty tuple is what a reel built with `--skip-captions`
    legitimately produces, and collapsing "has none" into "could not find
    out" is the whole defect this function exists to remove.
    """
    if not transcript:
        raise CaptionsUnavailable(
            "no timeline transcript, so the reel has no spine to plan "
            "captions from - `timeline_transcript` is what produces it")

    spine_for_reel = _resolve(REEL_SPINE_PRODUCER)
    if spine_for_reel is None:
        raise CaptionsUnavailable(
            f"{REEL_SPINE_PRODUCER[1]} is not available in "
            f"{REEL_SPINE_PRODUCER[0]} - that is what turns a reel's "
            f"played ranges into the spine step 4.01 plans captions from, "
            f"and without it the expected caption cards cannot be derived "
            f"at all. REFUSED rather than reported as zero cards, because "
            f"zero is what a --skip-captions reel legitimately has")

    try:
        from library.tools import operations
        planner = operations.get(SUBTITLE_PLAN_OPERATION)
    except Exception as unreachable:  # noqa: BLE001 - the registry names
        # the operation; anything that stops it resolving is reported as
        # itself rather than folded into "no captions".
        raise CaptionsUnavailable(
            f"the {SUBTITLE_PLAN_OPERATION} operation could not be "
            f"resolved ({unreachable}) - step 4.01 is the captioner and "
            f"the verifier has no second copy of its rule") from unreachable

    try:
        spine = spine_for_reel(moment, transcript, list(keep_ranges),
                               lead_seconds=lead_seconds)
    except Exception as no_spine:  # noqa: BLE001 - `ReelSpineError` names
        # the counts and is a VERIFICATION FINDING, not a crash: a reel
        # with no speech left on it is something the report must say.
        raise CaptionsUnavailable(
            f"the reel has no spine to caption: {no_spine}") from no_spine

    plan = planner.run(spine, caption_case="lowercase", brand_effect={},
                       brand_style={}, project_folder=project_folder) or {}
    entries = (plan.get("subtitle_plan") or {}).get("subtitle_entries") or []

    # Where each spine block ends, in the same REEL seconds the cards are
    # in. F7's exemption needs it: a card that ends with its block is one
    # nothing can lengthen, and the block end is the only way to know.
    block_end = {
        str(block.get("position")): block.get("timeline_end")
        for block in (spine.get("structure") or [])
        if block.get("timeline_end") is not None
    }

    cards = []
    for entry in entries:
        # REEL seconds. Never compared against a master time.
        start, end = entry.get("timeline_start"), entry.get("timeline_end")
        if start is None or end is None:
            continue
        position = entry.get("spine_block_position")
        position = None if position is None else str(position)
        cards.append(PlannedCaption(
            start_seconds=float(start),
            end_seconds=float(end),
            text=str(entry.get("text", "")),
            speaker=entry.get("speaker"),
            frames=max(int(round((float(end) - float(start)) * fps)), 1),
            block_position=position,
            block_end_seconds=block_end.get(position),
        ))
    return _retime_planned_captions(tuple(cards), spine, fps,
                                    project_folder, timeline)


def _retime_planned_captions(cards, spine: dict, fps: float,
                             project_folder: str, timeline: str = ""):
    """Apply the project's caption-timing pins to the DERIVED plan.

    With the owner's own applier rather than a second copy of its rule
    (`library/tools/caption_timing.py`), for this module's standing
    reason: a reel cannot be built to one rule and checked against
    another. Without this, F2/F14 report every pinned card as "planned
    and never placed" - the pin moved the placement and the verifier
    was still expecting the unpinned frame.

    Returns `(cards, declared_short)`. `declared_short` is
    `((start_frame, frames), ...)` for the cards a pin left under the
    readability floor - F7 reads it to tell a flash the captain
    DECLARED, with a recorded reason, from one a grouper authored
    behind everyone's back. A project that declares no pins gets its
    cards back untouched and nothing declared.
    """
    if not project_folder or not cards:
        return cards, ()
    from library.tools import caption_timing as _caption_timing

    pins = _caption_timing.load_pins(project_folder)
    if not pins:
        return cards, ()
    # The OWNER's join and the OWNER's applier, on the shape step 4.01
    # emits - so the plan the verifier derives and the plan the build
    # recorded are retimed by one piece of code.
    entries = [{"spine_block_position": card.block_position,
                "timeline_start": card.start_seconds,
                "timeline_end": card.end_seconds}
               for card in cards]
    moved, _applied, short, _stale = _caption_timing.retime_entries(
        entries, spine, pins, fps, timeline=timeline)
    out = []
    for card, probe in zip(cards, moved):
        start = float(probe["timeline_start"])
        end = float(probe["timeline_end"])
        out.append(dataclasses.replace(
            card, start_seconds=start, end_seconds=end,
            frames=max(int(round((end - start) * fps)), 1)))
    declared_short = tuple(
        (int(row["now"][0]), int(row["now"][1] - row["now"][0]))
        for row in short)
    return tuple(out), declared_short


def _find_master_picture_holes(master_snapshot) -> List[dict]:
    """Find picture holes in the master timeline for F3 attribution."""
    fps = master_snapshot.fps
    by_track: Dict[int, list] = {}
    for clip in master_snapshot.picture_clips():
        by_track.setdefault(clip.track_index, []).append(clip)

    # Merge all picture intervals to find where NOTHING has coverage.
    all_intervals = []
    for clips in by_track.values():
        for clip in clips:
            all_intervals.append((
                int(round(clip.timeline_start * fps)),
                int(round(clip.timeline_end * fps)),
            ))

    if not all_intervals:
        return []

    all_intervals.sort()
    merged = [list(all_intervals[0])]
    for s, e in all_intervals[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    holes = []
    for i in range(len(merged) - 1):
        gap_start = merged[i][1]
        gap_end = merged[i + 1][0]
        gap_len = gap_end - gap_start
        if gap_len > 0:
            holes.append({
                "frame": gap_start,
                "length": gap_len,
                "at_seconds": gap_start / fps,
            })
    return holes


# ── Plan provenance check ────────────────────────────────────────────

def check_plan_provenance(
    plan_path: str,
    reel_names: list[str],
    review_dir: str,
) -> List[Finding]:
    """Refuse to grade when the plan does not describe the timelines.

    The verifier was caught grading 16 built reels against a plan that
    described 14 completely different moments, producing 42 confident,
    precise, meaningless errors.  This check prevents that: it reads
    the provenance sidecar the builder wrote and compares the plan being
    graded now against the plan that was actually used to build.

    When provenance is absent (reels built before this change, or a
    standalone CLI invocation), the check falls back to comparing reel
    names in the plan against reel names on the timeline.  A plan that
    lists none of the reels it is being asked to grade is refused;
    missing provenance alone is a warning, not a refusal, because the
    common standalone-CLI path should not break.
    """
    from library.tools.plan_provenance import (
        read_provenance,
        check_plan_matches_provenance,
        check_reels_in_provenance,
    )

    findings: List[Finding] = []

    provenance = read_provenance(review_dir)
    if provenance is None:
        # No provenance file - reels predate this change or standalone
        # CLI invocation.  Warn but do not refuse.
        findings.append(Finding(
            finding_class=FindingClass.PLAN_MISMATCH,
            reel="(all)",
            message=(
                "no plan provenance record found - cannot verify that "
                "this plan describes these timelines. Reels built before "
                "provenance tracking are honestly lost to plan "
                "verification."),
            severity="warning",
            detail={
                "reason": "no_provenance_file",
                "review_dir": review_dir,
            },
        ))
        return findings

    # Provenance exists - check the content hash
    if plan_path and os.path.isfile(plan_path):
        matches, reason = check_plan_matches_provenance(plan_path, provenance)
        if not matches:
            findings.append(Finding(
                finding_class=FindingClass.PLAN_MISMATCH,
                reel="(all)",
                message=(
                    f"REFUSING to grade: {reason}. "
                    f"Every finding below would be noise. The plan used "
                    f"to build these reels was recorded at build time; "
                    f"the plan being graded now is a different document."),
                severity="error",
                detail={
                    "reason": "content_hash_mismatch",
                    "plan_path": plan_path,
                    "provenance_hash": provenance.get(
                        "plan_content_hash", ""),
                },
            ))
            return findings

    # Check that the reels on the timeline are the ones the build produced
    all_present, missing = check_reels_in_provenance(reel_names, provenance)
    if missing:
        findings.append(Finding(
            finding_class=FindingClass.PLAN_MISMATCH,
            reel="(all)",
            message=(
                f"{len(missing)} reel(s) on the timeline were not built "
                f"from the recorded plan: {missing}"),
            severity="warning",
            detail={
                "reason": "reels_not_in_provenance",
                "missing_reels": missing,
                "built_reels": provenance.get("built_reels", []),
            },
        ))

    return findings


# ── The full verification pipeline ───────────────────────────────────

def _repair_moments(moments, transcript, err):
    """Stored boundaries, repaired the way the build repairs them.

    One spelling for the gate and the build
    (`reel_build.rebuild_reels_in_project` runs the same
    `snap_moment_to_speech` on the way through): the gate grades what
    the build placed, so it reads the same repair rather than the raw
    file. Every moment survives - a moment already clean returns
    itself with no moves, and dropping a moveless moment would grade
    the batch against a smaller plan than the build placed.
    """
    from library.tools.reel_proposal import snap_moment_to_speech

    repaired = []
    for moment in moments:
        fixed, moves = snap_moment_to_speech(moment, transcript)
        for move in moves:
            word = (f" through '{move['through']}'"
                    if move.get("through") else "")
            print(f"  Reel {moment.number:02d}: "
                  f"{move['boundary']} {move['was']:.3f}s -> "
                  f"{move['now']:.3f}s{word} (stored proposal "
                  f"predates the boundary snap)", file=err)
        repaired.append(fixed)
    return repaired


def _apply_recorded_pins(moments, transcript, project_folder, err):
    """The captain's recorded closer pins, as the build applies them.

    One spelling for the gate and the build: the build redraws
    approved moments in memory and places the redrawn spans, so
    whatever derives a plan from the proposal file must apply the
    same pins or it grades seconds nobody placed. Returns the moments
    with pins applied (or unaltered where none are recorded). A
    recorded pin the gate cannot read refuses rather than grading
    silently past it.
    """
    if not project_folder:
        return list(moments)
    from library.tools import captain_edits as _edits
    try:
        pin_edits = _edits.load_edits(project_folder)
    except _edits.CaptainEditError as exc:
        raise RuntimeError(
            f"captain_edits cannot be read: {exc}. A recorded pin the "
            f"gate cannot read must refuse, never grade silently past "
            f"it.") from exc
    if not any(e.get("kind") == "redraw_closer" for e in pin_edits):
        return list(moments)
    redrawn, applied, held, stale = _edits.apply_closer_redraws(
        moments, transcript, pin_edits)
    for record in applied:
        print(f"  Reel {record['reel']:02d}: closer "
              f"{record['was'][0]:.3f}s -> {record['now'][0]:.3f}s "
              f"(recorded pin: {record['reason']})", file=err)
    for record in held:
        print(f"  Reel {record['reel']:02d}: closer already opens on "
              f"{record['anchor_phrase']!r} - pin held", file=err)
    _edits.report_stale(stale)
    return redrawn


def grades_as_a_reel(name: str, only_reels=None,
                     variant_names=()) -> bool:
    """Is this timeline one the conformance sweep should grade?

    A `Reel ...` timeline, and NEITHER a retired generation NOR a live
    comparison VARIANT.

    Promotion archives the timeline it replaced rather than deleting it
    (`library/tools/reel_retirement.py`), so `Reel 09 - ... (archived
    round 003)` is now in the project - and grading it against the
    CURRENT plan reports a mismatch that is a fact about it being
    retired, not a defect. Every round would otherwise double the
    sweep's findings, which is how a real finding stops being read.

    A VARIANT is excluded for the sharper version of the same reason,
    and it is the reason `build_reel_variants` already grades its own
    output with the STRUCTURAL verifier instead of this one: a variant's
    offsets are INTENTIONAL deviations from the plan, so the plan gate
    fails them BY DESIGN. Grading a live variant here would report a
    designed difference as a defect - a gate that fails correct output,
    which is no more coverage than one that cannot fail (AGENTS.md
    10.4). `variant_names` is the reel's DECLARED variant timeline names
    (`timeline_variants.declared_variant_names`), never a name guess: no
    syntactic rule tells `(final)` - a reel's own name - from
    `(reaction-cutaway)`, and excluding by parse would silently drop a
    deliverable out of the sweep.

    Either is still reachable when a caller names it explicitly in
    `only_reels`: refusing to look at something the operator asked for
    by name is a different failure from quietly grading what they did
    not.
    """
    from library.tools.resolve_bin_layout import is_archived_timeline

    if not (name or "").startswith("Reel "):
        return False
    named = set(only_reels or ())
    if is_archived_timeline(name) and name not in named:
        return False
    if name in set(variant_names or ()) and name not in named:
        return False
    return True


def declared_variants(project_folder) -> set:
    """Every variant timeline name this project has declared.

    Asked of the spec record through its owner
    (`timeline_variants.declared_variant_timelines`), which resolves
    each suffix against the PLAN's own reel name and answers EMPTY when
    there is no record - so a project that has never declared a variant
    behaves exactly as it did before this existed.
    """
    if not project_folder:
        return set()
    from library.tools.timeline_variants import declared_variant_timelines

    return declared_variant_timelines(str(project_folder))


def run_verification(
    project_name: str,
    master_name: str,
    plan_path: str = "",
    transcript: Optional[dict] = None,
    json_path: str = "",
    review_dir: str = "",
    project_folder: str = "",
    out=None,
    only_reels: Optional[Sequence[str]] = None,
) -> int:
    """Connect to Resolve, read everything, verify, report.

    `only_reels` names the EXACT reel timeline names to grade, and is
    how a partial build avoids re-grading the whole project: building
    one reel grades one reel, so N authorised rebuilds cost N
    verifications rather than 50 + 51 + ... Grading reel 17's build
    tells nothing new about reels 1 through 16, which were graded when
    they were built. `None` grades every `Reel *` timeline, which is
    the deliberate whole-project sweep - useful explicitly, wrong as
    the incidental cost of building one reel.

    A scoped name that matches no timeline is FATAL, never a smaller
    pass: grading a subset that silently excludes a misspelled reel is
    the gate-that-cannot-fail shape (AGENTS.md 10.4).

    Returns 0 on pass, 1 on findings, 2 on fatal error or read-only
    violation.

    Every Resolve call this function makes is a getter: GetTimelineCount,
    GetTimelineByIndex, GetTrackCount, GetItemListInTrack, GetName,
    GetSetting, GetStart, GetEnd, GetSourceStartFrame, GetSourceEndFrame,
    GetMediaPoolItem, GetClipProperty, GetUniqueId, GetMarkers,
    GetStartFrame, GetEndFrame, GetTrackName.

    No Set*, Add*, Append*, Delete*, OpenPage, LoadProject,
    SetCurrentTimeline or SetCurrentProject.
    """
    import re as re_mod

    from library.tools.timeline_ingest import (
        resolve_project_exactly,
        snapshot_timeline,
        snapshot_to_dict,
        TimelineIngestError,
    )
    from library.tools.marker_feedback import ResolveUnavailable, connect_resolve

    if out is None:
        out = sys.stdout
    err = sys.stderr

    # ── Which PROJECT this is, BEFORE anything reads a declaration ───
    #
    # Step 4.01 resolves the delivery format - and through it the safe
    # area captions are grouped against - from the project folder. The
    # plan lives at <project>/pipeline_output/review/, so the folder is
    # derivable from it; taking it from the caller when given keeps a
    # project addressed by absolute path (AGENTS.md 8) working either way.
    #
    # THIS RUNS FIRST, and that ordering is the whole point. Three
    # readers below answer EMPTY on an empty folder rather than
    # raising - `_apply_recorded_pins` (the captain's closer pins),
    # `declared_variants` (which timelines are comparison variants) and
    # the framing/look/card-row block - because a folder that cannot be
    # found is a declaration store that cannot be read. The derivation
    # used to sit below all three, so the in-process caller (the
    # build's own gate, which PASSES `project_folder`) read every
    # declaration and the CLI - which can only derive it - read none.
    #
    # Measured 2026-09-12 on the rebuilt field-test project, where the
    # build's own sweep PASSED and
    # `python3 -m library.tools.reel_conformance_verifier --plan ...`
    # FAILED on the same nine timelines:
    #
    # * "+54 frames, +2.25s ... it is not the plan that built this
    #   reel", ERROR on Reels 09, 26 and 28 - exactly the three built
    #   reels whose closer the captain's `redraw_closer` pin moves, and
    #   2.252s is exactly 54 frames at 23.976fps;
    # * Reel 09's declared cutaway variant graded against the plan it
    #   deviates from BY DESIGN, which `grades_as_a_reel` exists to
    #   prevent.
    #
    # A gate that fails correct output is no more coverage than one
    # that cannot fail (AGENTS.md 10.4).
    if not project_folder and plan_path:
        # Walk up until the candidate's own layout agrees that the plan
        # sits where it says the review area is. That asks
        # `project_layout` rather than restating "three parents up", so a
        # layout change moves this with it.
        from pathlib import Path

        from library.tools.project_layout import Area, ProjectLayout
        here = Path(plan_path).expanduser().resolve().parent
        for candidate in (here, *here.parents):
            try:
                if ProjectLayout(candidate).read_dir(Area.REVIEW) == here:
                    project_folder = str(candidate)
                    break
            except Exception:  # noqa: BLE001 - not a project root
                continue
    if plan_path and not project_folder:
        print("WARNING: no project folder could be derived from "
              f"{plan_path!r}, so the captain's recorded pins, the "
              f"declared framing, look and card row are NOT read - "
              f"every check that needs one grades a plan this project "
              f"never placed.", file=err)

    # ── Motion-graphics tightness census ──────────────────────────
    #
    # Resolve-independent: walks the rendered overlay artefacts and
    # counts tight canvases against refused ones by reason, so a
    # project carrying silent full-frame graphics shows it here.
    # Informational only - the sweep reports, the step-4.06 guard
    # refuses - and an unreadable artefact dir says `unavailable`,
    # never zero.
    motion_graphics_tightness: dict = {
        "unavailable": "no project folder"}
    if project_folder:
        try:
            from library.tools.delivery_format import (
                resolve_delivery_format as _resolve_format)
            from library.tools.mg_tight_box import (
                check_motion_graphics_dir as _check_mg_dir)
            from library.tools.project_layout import (
                Area as _Area, ProjectLayout as _Layout)
            _mg_dir = str(_Layout(project_folder).read_dir(
                _Area.MOTION_GRAPHICS_SEGMENTS))
            _full_w, _full_h = _resolve_format(project_folder)
            _mg_errors, motion_graphics_tightness = _check_mg_dir(
                _mg_dir, _full_w, _full_h)
            if _mg_errors:
                motion_graphics_tightness["undeclared_errors"] = \
                    _mg_errors[:10]
                print(f"Motion-graphics tightness: "
                      f"{len(_mg_errors)} UNDECLARED full-canvas "
                      f"artefact(s) - the step-4.06 guard refuses "
                      f"these on the next render pass", file=err)
            else:
                print(f"Motion-graphics tightness: "
                      f"{motion_graphics_tightness.get('tight', 0)} "
                      f"tight, "
                      f"{motion_graphics_tightness.get('full_by_design', 0)} "
                      f"full by design, "
                      f"{motion_graphics_tightness.get('full_with_reason', 0)} "
                      f"full with reason", file=err)
        except Exception as exc:  # noqa: BLE001 - census, never a gate
            motion_graphics_tightness = {"unavailable": str(exc)}

    # ── Connect ──────────────────────────────────────────────────────
    try:
        resolve = connect_resolve()
    except (ResolveUnavailable, Exception) as exc:
        print(f"FATAL: cannot reach Resolve: {exc}", file=err)
        print("This tool reads a LIVE Resolve project. Open Resolve "
              "with the project and try again.", file=err)
        return 2

    manager = resolve.GetProjectManager()
    try:
        project = resolve_project_exactly(manager, project_name)
    except TimelineIngestError as exc:
        print(f"FATAL: {exc}", file=err)
        return 2

    # The build's own record of what it placed. Read ONCE, and its
    # absence is a refusal per reel rather than a silent pass.
    from library.tools.plan_provenance import read_provenance
    caption_provenance = None
    if plan_path:
        caption_provenance = read_provenance(
            review_dir or os.path.dirname(os.path.abspath(plan_path)))

    # ── Find all timelines ───────────────────────────────────────────
    timeline_count = project.GetTimelineCount() or 0
    if timeline_count == 0:
        print("FATAL: project has no timelines.", file=err)
        return 2

    all_timelines = []
    for i in range(1, timeline_count + 1):
        tl = project.GetTimelineByIndex(i)
        if tl:
            all_timelines.append(tl)

    # Find the master and all reel timelines. A live comparison VARIANT
    # is left out the way a retired generation is - its offsets are
    # intentional deviations from the plan this verifier re-derives, so
    # grading one reports a designed difference as a defect. Named in
    # `only_reels` it is graded anyway.
    variant_names = declared_variants(project_folder)
    if variant_names:
        print(f"  {len(variant_names)} declared variant timeline(s) are "
              f"not graded by the plan verifier (build_reel_variants "
              f"grades them structurally): "
              f"{', '.join(sorted(variant_names))}", file=err)
    master_tl = None
    reel_timelines = []
    for tl in all_timelines:
        name = tl.GetName()
        if name == master_name:
            master_tl = tl
        elif grades_as_a_reel(name, only_reels, variant_names):
            reel_timelines.append(tl)

    if master_tl is None:
        all_names = [t.GetName() for t in all_timelines]
        print(f"FATAL: no timeline named exactly {master_name!r}. "
              f"Found: {all_names}", file=err)
        return 2

    if not reel_timelines:
        all_names = [t.GetName() for t in all_timelines]
        print(f"FATAL: no timelines starting with 'Reel ' found. "
              f"Found: {all_names}", file=err)
        return 2

    # Grade what was asked for, not the whole project. The build's own
    # record (`reel_build.timelines_built`) is what names the scope, so
    # a rebuild of one reel verifies one timeline instead of all fifty.
    # Matching is by EXACT name, never a prefix (AGENTS.md 5) - a
    # suffixed rebuild ("Reel 03 - x (whole-take rebuild)") is a
    # different container from the timeline it was rebuilt from.
    if only_reels is not None:
        wanted = list(only_reels)
        present = {tl.GetName(): tl for tl in reel_timelines}
        missing = [name for name in wanted if name not in present]
        if missing:
            print(f"FATAL: scoped to {len(wanted)} reel timeline(s) and "
                  f"{len(missing)} match nothing in Resolve project "
                  f"{project_name!r}: {missing}. "
                  f"Found: {sorted(present)}", file=err)
            return 2
        reel_timelines = [present[name] for name in wanted]
        if not reel_timelines:
            print(f"FATAL: scoped to zero reel timelines - nothing to "
                  f"grade, so there is nothing to pass.", file=err)
            return 2

    reel_timelines.sort(key=lambda t: t.GetName())
    print(f"Project: {project_name}", file=err)
    print(f"Master:  {master_name}", file=err)
    if only_reels is not None:
        print(f"Reels:   {len(reel_timelines)} (scoped - the build placed "
              f"these; the rest of the project is not re-graded)", file=err)
    else:
        print(f"Reels:   {len(reel_timelines)}", file=err)

    # A check that was never asked is not a check that passed.  Without a
    # transcript there is no speech to measure coverage or boundaries
    # against and no bad-take cuts to derive, so the run says which
    # checks did not run rather than printing a table with their columns
    # at zero (AGENTS.md 10.4).
    if not transcript:
        not_run = "F5 (caption coverage) and F8 (boundary speech)"
        print(f"Transcript: NONE - {not_run} DID NOT RUN, and the "
              f"bad-take cuts are underived, so the plan is the "
              f"uncut span. Pass --transcript to measure them.",
              file=err)
    else:
        print(f"Transcript: {len(transcript.get('segments') or ())} rows",
              file=err)

    # ── Snapshot everything BEFORE (for read-only proof) ─────────────
    print("Reading all timelines (before hash)...", file=err)
    before_hashes = {}
    snapshots = {}

    master_snapshot = snapshot_timeline(master_tl, project_name)
    before_hashes[master_name] = hash_snapshot_dict(
        snapshot_to_dict(master_snapshot))
    snapshots[master_name] = master_snapshot

    for tl in reel_timelines:
        name = tl.GetName()
        snap = snapshot_timeline(tl, project_name)
        before_hashes[name] = hash_snapshot_dict(snapshot_to_dict(snap))
        snapshots[name] = snap

    print(f"  {len(before_hashes)} timelines hashed.", file=err)

    # ── Load the plan if provided ────────────────────────────────────
    moments = []
    plan_source = ""
    if plan_path:
        if os.path.isfile(plan_path):
            from library.tools.reel_proposal import read_proposal
            moments = read_proposal(plan_path)
            if transcript:
                # The build repairs stored boundaries on the way through;
                # the gate grades what the build placed, so it reads the
                # same repair rather than the raw file.  Without this a
                # stored proposal predating the boundary snap fails here
                # on seconds the build no longer plays.
                moments = _repair_moments(moments, transcript, err)
                # The captain's recorded closer pins, applied for the
                # same reason as the repair above: the build redraws
                # approved moments in memory and places the redrawn
                # spans, so a gate deriving the un-pinned file grades a
                # plan the build never placed - Reel 09's +2.25s CTA
                # growth read as 54 dropped frames. A recorded pin the
                # gate cannot read refuses rather than grading past it.
                moments = _apply_recorded_pins(
                    moments, transcript, project_folder, err)
            plan_source = f"proposal file: {plan_path}"
            print(f"Plan:    {plan_source} ({len(moments)} moments)",
                  file=err)
        else:
            plan_source = (f"DERIVED from master (--plan {plan_path!r} "
                           f"does not exist)")
            print(f"WARNING: {plan_path!r} does not exist.", file=err)
            print(f"Plan:    {plan_source}", file=err)
    else:
        plan_source = "DERIVED from master (no --plan provided)"
        print(f"Plan:    {plan_source}", file=err)

    # ── Plan provenance check ────────────────────────────────────────
    # If a review_dir is provided, check whether the plan being graded
    # is the one that was actually used to build these reels.  A
    # content-hash mismatch means the selector has overwritten the plan
    # since the build - every finding would be noise.
    provenance_findings: List[Finding] = []
    plan_refused = False
    effective_review_dir = review_dir
    if not effective_review_dir and plan_path:
        # Infer review_dir from plan_path (plan sits in the review dir)
        effective_review_dir = os.path.dirname(os.path.abspath(plan_path))

    if effective_review_dir and plan_path:
        reel_names_on_timeline = [tl.GetName() for tl in reel_timelines]
        provenance_findings = check_plan_provenance(
            plan_path, reel_names_on_timeline, effective_review_dir)

        prov_errors = [f for f in provenance_findings
                       if f.severity == "error"]
        if prov_errors:
            plan_refused = True
            print(f"PLAN MISMATCH: {prov_errors[0].message}", file=err)

    # ── The transition-element plan the build recorded ───────────────
    #
    # READ from what the build wrote, never re-derived. A re-derived plan
    # is only the build's plan while nothing has changed in between, and
    # PLAN-MISMATCH exists because that assumption already produced 42
    # confident meaningless errors on this path. An absent file means the
    # build placed no element, and F18 then grades whatever is on V4
    # against nothing - which is the finding, not a skip.
    overlay_plans: Dict[str, list] = {}
    if effective_review_dir:
        overlay_path = os.path.join(effective_review_dir,
                                    "transition_overlays.json")
        if os.path.exists(overlay_path):
            with open(overlay_path, "r", encoding="utf-8") as f:
                for reel, record in (json.load(f) or {}).items():
                    overlay_plans[reel] = list(record.get("placements") or [])

    # ── Find master picture holes for F3 attribution ─────────────────
    master_holes = _find_master_picture_holes(master_snapshot)
    # The same clips, as dicts, for the plan-quality picture gate. That
    # gate returns NOTHING without them, which is how it managed to be
    # covered by tests and inert on every live run.
    master_video_items = [
        {"track_index": clip.track_index,
         "timeline_start": clip.timeline_start,
         "timeline_end": clip.timeline_end}
        for clip in master_snapshot.picture_clips()]
    if master_holes:
        print(f"Master picture holes: {len(master_holes)}", file=err)
        for hole in master_holes:
            print(f"  frame {hole['frame']} len {hole['length']} "
                  f"at {hole['at_seconds']:.2f}s", file=err)

    # ── Verify each reel ─────────────────────────────────────────────
    reel_results = []

    # ── What the project DECLARED the picture should look like ───────
    #
    # Resolved once for the run, from the same enumeration
    # `compile_manifest` uses, so a reel and a master cannot be held to
    # different framings.  A project folder that cannot be found leaves
    # this None, and F12 reports THAT rather than grading against a
    # default nobody declared.
    source_sizes = _catalog_source_sizes(project_folder)
    declared_intent, declared_crop_factor = _declared_framing(project_folder)
    # The declared TV-frame look, resolved ONCE from the same module the
    # build read it from, for the same reason the framing above is: a
    # reel and the check that grades it must not read two declarations.
    # None is a project that declares no look, and then every check
    # below reads exactly what it read before `reel_look` existed.
    from library.tools import reel_look as _reel_look
    # The frame the reels were BUILT at, read from the same declaration
    # `reel_build.reel_resolution` builds them at, so the gate and the
    # builder cannot disagree. None where no project reached this run,
    # and then F10 says the shape was not checked rather than passing it.
    expected_frame = None
    if project_folder:
        from library.tools.delivery_format import resolve_delivery_format

        _fw, _fh = resolve_delivery_format(project_folder)
        expected_frame = (int(_fw), int(_fh))

    declared_look = (_reel_look.resolve_look(project_folder)
                     if project_folder else None)
    # What the build recorded about each reel's explainer. Read ONCE and
    # read from the BUILD's own record; `{}` when no build ever wrote
    # one, which makes F21 silent rather than confident.
    from library.tools.speaker_identity import (
        plan_for as lower_third_plan_for_reel,
        read_plans as read_lower_third_plans)
    lower_third_plans = (read_lower_third_plans(project_folder)
                         if project_folder else {})
    from library.tools.explainer_plan import plan_for_reel, read_plans
    explainer_plans = read_plans(project_folder) if project_folder else {}
    if (explainer_plans.get("plans") or []):
        drawn = sum(1 for e in explainer_plans["plans"] if e.get("segments"))
        print(f"Explainer plans recorded: {len(explainer_plans['plans'])} "
              f"({drawn} with something drawn)", file=err)
    # What the build recorded about each reel's semantic visuals. Read
    # ONCE and read from the BUILD's own record; `{}` when no build
    # ever wrote one, which makes F22 silent rather than confident.
    from library.tools.reel_semantic_visual import (
        read_records as read_semantic_records)
    from library.tools.reel_semantic_visual import (
        record_for_reel as semantic_record_for_reel)
    semantic_records = read_semantic_records(
        project_folder) if project_folder else {}
    # What the build recorded about each reel's span picture plan. Read
    # ONCE and read from the BUILD's own record; `{}` when no build
    # ever wrote one, which makes F23 silent rather than confident.
    from library.tools.reel_semantic_visual import (
        read_span_records as read_span_plan_records)
    from library.tools.reel_semantic_visual import (
        span_record_for_reel as span_plan_record_for_reel)
    span_plan_records = read_span_plan_records(
        project_folder) if project_folder else {}
    if declared_intent is not None:
        print(f"Declared framing_intent: {declared_intent} "
              f"(crop factor {declared_crop_factor})", file=err)

    if plan_refused:
        # The plan does not describe these timelines.  Every F1-F11
        # finding would be noise that looks like signal.  REFUSE.
        print("  REFUSED: skipping all reel checks (plan mismatch).",
              file=err)
    else:
        for tl in reel_timelines:
            name = tl.GetName()
            snap = snapshots[name]

            # Parse reel number from name "Reel 01 - slug"
            reel_number = 0
            m = re_mod.match(r"Reel\s+(\d+)", name)
            if m:
                reel_number = int(m.group(1))

            # Find matching moment from the plan
            moment = None
            for mom in moments:
                if mom.timeline_name == name or mom.number == reel_number:
                    moment = mom
                    break

            if moment:
                # Derive plan from the proposal moment + master timeline
                from library.tools.reel_build import cta_range
                plan = _derive_plan_from_master(
                    name, reel_number,
                    moment.timeline_start, moment.timeline_end,
                    master_snapshot, transcript,
                    call_to_action=cta_range(moment),
                    moment=moment,
                    project_folder=project_folder or "")
            else:
                # No plan proposal available - we can still detect holes,
                # duplicates, and caption defects but item count comparison
                # against a plan is not possible.  The plan will carry empty
                # placements so F4 comparisons are skipped (planned count is
                # 0, so the expected == actual assertion is not run).
                plan_seconds = snap.duration
                plan = ReelPlan(
                    reel_name=name,
                    reel_number=reel_number,
                    plan_seconds=plan_seconds,
                    plan_frames=plan_seconds * snap.fps,
                    span_start=0.0,
                    span_end=plan_seconds,
                    placements=(),
                    keep_ranges=((0.0, plan_seconds),),
                )

            # Converted AFTER the plan, with the plan's cards: an item
            # the plan names is picture wherever it sits (the declared
            # card row is an overlay row), so the snapshot conversion
            # needs the names before it buckets by row.
            reel_tl = _snapshot_to_reel_timeline(snap, cards=plan.cards)

            # The transcript is what F5 measures coverage against, and
            # it was never passed - so F5 was skipped on every live run
            # regardless of the plan's caption side.
            result = verify_reel(
                plan, reel_tl,
                transcript_segments=(transcript or {}).get("segments"),
                master_holes=master_holes,
                master_fps=master_snapshot.fps,
                caption_provenance=caption_provenance,
                master_video_items=master_video_items,
                source_sizes=source_sizes,
                declared_intent=declared_intent,
                declared_crop_factor=declared_crop_factor,
                look=declared_look,
                planned_overlays=overlay_plans.get(name),
                explainer_plan=plan_for_reel(explainer_plans, name),
                semantic_plan=semantic_record_for_reel(
                    semantic_records, name),
                span_plan=span_plan_record_for_reel(
                    span_plan_records, name),
                lower_third_plan=lower_third_plan_for_reel(
                    lower_third_plans, name),
                expected_frame=expected_frame)
            reel_results.append(result)
            status = "FAIL" if result.errors else "ok"
            print(f"  {name}: {status} ({len(result.errors)} errors, "
                  f"{len(result.warnings)} warnings)", file=err)

    # ── The captain's four qualities ─────────────────────────────────
    #
    # Nineteen checks in this file and not one of them asked whether a
    # reel was worth publishing.  `library/tools/reel_quality_bar.py` is
    # where that question lives now, and this is where its answer reaches
    # a report about built timelines - so a reel that FAILS the bar is
    # reported as failing rather than shipped because its frame rate was
    # right.
    #
    # DURATION is deliberately left out of the fold: `check_plan_length`
    # above is the same measurement in this file's vocabulary, and
    # carrying both would report one reel's length twice under two
    # codes.  What comes in is the call-to-action half, which nothing
    # here measured, and the two JUDGEMENT qualities, which nothing here
    # could.
    bar_report = None
    if moments and transcript and not plan_refused:
        from library.tools import reel_quality_bar as _bar

        judgement, judgement_source = None, ""
        if project_folder:
            # `read_judgement`, never the review path alone. Step 3.05
            # writes its answer into its OWN step directory like every
            # other step; opening only `review/reel_judgement.json` -
            # which nothing in this repository writes - is why coherence
            # and value read UNJUDGED on every verification ever run.
            judgement, judgement_source = _bar.read_judgement(project_folder)
        bar_report = _bar.judge(moments, transcript, judgement)
        unattached = attach_quality_bar(bar_report, reel_results)
        if unattached:
            print(f"Quality bar: {len(unattached)} planned reel(s) have no "
                  f"timeline here, so their findings are in the quality_bar "
                  f"section only and not against a built reel: "
                  f"{'; '.join(unattached)}", file=err)
        if not bar_report.judged:
            print("Quality bar: no reading of these reels on file, so "
                  "coherence and value are UNJUDGED. Run judge_reels "
                  "(step 3.05).", file=err)
        else:
            print(f"Quality bar: read the judgement from "
                  f"{judgement_source}", file=err)

    # ── Snapshot everything AFTER (for read-only proof) ───────────────
    print("Re-reading all timelines (after hash)...", file=err)
    after_hashes = {}

    master_after = snapshot_timeline(master_tl, project_name)
    after_hashes[master_name] = hash_snapshot_dict(
        snapshot_to_dict(master_after))

    for tl in reel_timelines:
        name = tl.GetName()
        snap_after = snapshot_timeline(tl, project_name)
        after_hashes[name] = hash_snapshot_dict(
            snapshot_to_dict(snap_after))

    # ── Read-only proof ──────────────────────────────────────────────
    all_identical = True
    proof_rows = {}
    for name in sorted(before_hashes):
        identical = before_hashes[name] == after_hashes[name]
        if not identical:
            all_identical = False
        proof_rows[name] = {
            "before": before_hashes[name][:16] + "...",
            "after": after_hashes[name][:16] + "...",
            "identical": identical,
            "before_full": before_hashes[name],
            "after_full": after_hashes[name],
        }

    read_only_proof = {
        "all_identical": all_identical,
        "timelines_checked": len(proof_rows),
        "timelines": proof_rows,
    }

    if not all_identical:
        print("FATAL: timelines were MODIFIED during verification. "
              "This tool is read-only and something changed underneath "
              "it.", file=err)
        for name, row in proof_rows.items():
            if not row["identical"]:
                print(f"  CHANGED: {name}", file=err)
        return 2

    print(f"Read-only proof: ALL {len(proof_rows)} timelines identical "
          f"before/after.", file=err)

    # ── Build the report ─────────────────────────────────────────────
    report = VerificationReport(
        project_name=project_name,
        master_timeline=master_name,
        reel_results=reel_results,
        read_only_proof=read_only_proof,
        plan_source=plan_source,
        provenance_findings=provenance_findings,
        motion_graphics_tightness=motion_graphics_tightness,
    )

    report.quality_bar = bar_report

    # ── Print human-readable output ──────────────────────────────────
    print(file=out)
    print(f"Plan graded against: {plan_source}", file=out)
    print(file=out)
    print(format_table(report), file=out)
    print(file=out)
    if bar_report is not None:
        from library.tools.reel_quality_bar import format_table as _bar_table

        print(_bar_table(bar_report), file=out)
        print(file=out)
    if report.all_findings:
        print(format_findings(report), file=out)
        print(file=out)

    # Summary
    print("=" * 60, file=out)
    if report.has_errors:
        print(f"FAILED: {report.total_errors} error(s), "
              f"{report.total_warnings} warning(s) across "
              f"{len(reel_results)} reels.", file=out)
    else:
        print(f"PASSED: 0 errors, {report.total_warnings} warning(s) "
              f"across {len(reel_results)} reels.", file=out)

    # ── Write JSON ───────────────────────────────────────────────────
    if json_path:
        json_data = report.as_dict()
        # Recorded spelling corrections on the regenerated verdicts
        # (the 3.04 keep-exclusion precedent): verdict and finding
        # texts quoting speech the correction respelt carry the
        # corrected spelling, deterministically. Codes and severities
        # are not text and never move.
        from library.tools.display_respell import apply_post_pass
        apply_post_pass(json_data, project_folder or "",
                        "conformance verifier (conformance_report)")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, default=str)
        print(f"JSON written to {json_path}", file=err)

    return 1 if report.has_errors else 0


# ── CLI ──────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    """Entry point for the reel conformance verifier.

    Reads a LIVE Resolve project and its plan, reports every
    disagreement between them.  Exits 0 on pass, 1 on findings,
    2 on fatal error or read-only violation.

    --project and --master are REQUIRED.  Without them the tool refuses
    to run rather than guessing.

    Every Resolve call is a getter.  Read-only is proven by hashing
    every timeline's snapshot before and after and comparing.

    **`--transcript` is what lets F5 and F8 run at all.**  The build gate
    (`reel_build.verify_built_reels`) has always passed one; the CLI had
    no way to, so a human running this tool by hand got a report with the
    caption-coverage and boundary-speech checks silently absent and no
    line saying so.  A gate that cannot fail is worse than no gate
    (AGENTS.md 10.4), and one that is not asked is the same thing.

    Usage::

        python3 -m library.tools.reel_conformance_verifier \\
            --project "Podcast (field test)" \\
            --master "GEO Podcast - Synced" \\
            [--plan reel_proposal.json] \\
            [--transcript transcript.json] \\
            [--json output.json] \\
            [--reel "Reel 03 - slug"]

    No `--reel` grades every reel timeline (the explicit sweep); each
    `--reel` narrows the run to one exact timeline name.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="reel_conformance_verifier",
        description=(
            "Verify that built reel timelines match the plan that "
            "produced them.  Read-only against Resolve, with proof.  "
            "Exits 0 on pass, 1 on findings, 2 on fatal error."))
    parser.add_argument(
        "--project", required=True,
        help="Resolve project name (must be already open)")
    parser.add_argument(
        "--master", required=True,
        help="Master timeline name (e.g. 'GEO Podcast - Synced')")
    parser.add_argument(
        "--plan", default="",
        help="Path to reel_proposal.json plan file (optional; derives "
             "from master if absent)")
    parser.add_argument(
        "--transcript", default="",
        help="Path to the timeline transcript, written by `python3 -m "
             "library.tools.timeline_transcript <project> --write` - that "
             "module owns where it lands, so this names the producer "
             "rather than restating the path. F5, F8 and the bad-take "
             "cuts cannot be measured without it; omitted, the run SAYS "
             "they did not run")
    parser.add_argument(
        "--json", default="",
        help="Path to write machine-readable JSON output")
    parser.add_argument(
        "--review-dir", default="",
        help="Path to the review directory containing plan provenance "
             "(defaults to the plan file's parent directory)")
    parser.add_argument(
        "--reel", dest="only_reels", action="append", default=None,
        metavar="NAME",
        help="Grade only this exact reel timeline name; repeatable. "
             "Default (absent): grade every 'Reel *' timeline, which is "
             "the deliberate whole-project sweep. A build passes the "
             "names it placed so one reel costs one verification.")
    args = parser.parse_args(argv)

    transcript = None
    if args.transcript:
        if not os.path.isfile(args.transcript):
            print(f"FATAL: no transcript at {args.transcript}",
                  file=sys.stderr)
            return 2
        with open(args.transcript, encoding="utf-8") as handle:
            transcript = json.load(handle)

    return run_verification(
        project_name=args.project,
        master_name=args.master,
        plan_path=args.plan,
        transcript=transcript,
        json_path=args.json,
        review_dir=args.review_dir,
        only_reels=args.only_reels,
    )


if __name__ == "__main__":
    raise SystemExit(main())

