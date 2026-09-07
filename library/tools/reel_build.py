"""Cutting an approved reel onto its own timeline, bad takes removed.

The captain, approving the second batch: *"the only thing i would say is
to remove the bad takes out so that the timelines of the reels are the
finished cut"*.

What is CUT and what is only FLAGGED
------------------------------------
A retake is easy to see and hard to prove.  Measured over the sixteen
approved reels, every loose rule removed real content:

- **containment alone** drops "make sure you're writing about that"
  against "Make sure you're writing why you're better than a competitor"
  at 1.00, because a short phrase is wholly inside a longer sentence,
- **without a same-speaker test** it drops Craig's actual question
  against Akshita's answer echoing it - and worse, mic bleed puts her
  words on his track, so the two sides can BOTH read as Craig,
- **without a duration test** it drops a 4.3s line to keep a 0.5s
  fragment of the same sentence.

So the cut rule is deliberately narrow, and everything it is not sure
about is REPORTED rather than removed:

    same speaker, durations within DURATION_RATIO of each other,
    containment >= CUT_CONTAINMENT and Jaccard >= CUT_JACCARD,
    the second beginning within CUT_WINDOW_SECONDS of the first ending.

`redundant_takes` returns those.  `suspected_takes` returns the near
misses, which become MARKERS on the built timeline rather than edits -
the captain reviews in Resolve through
`library/tools/marker_feedback.py`, so a suspect belongs where they are
already looking.  A wrong cut is content they have to notice is missing;
a marker is one keystroke to act on.

A TAKE IS REMOVED WHOLE OR NOT AT ALL
-------------------------------------
The unit of a cut is a RUN of repeated lines, not a pair.  A run with a
line the pairing test could not accept is left entirely alone and
REPORTED (`redundant_runs`, `refused_take_groups`), because removing
part of a take strands the rest where its own opening used to be - and
at the head of a span that fragment becomes the reel's first line.  The
rule has no number in it, and `assert_takes_are_whole` refuses a cut
list that breaks it whatever produced that list.
`docs/RULE_EVIDENCE.md#the-take-that-was-two-thirds-cut` has the
measurement, and what it does and does not change across the nineteen.

**The LATER take is kept.** A retake exists because the first attempt was
flubbed - reel 02's first is "stuffed all their keywords with H1 tags",
which is backwards, and its retake says it correctly. That is a
JUDGEMENT, so it is recorded per cut and reversible rather than silent.

Sync
----
A reel is built from KEEP RANGES over the master's own timebase, not by
copying clips and closing gaps per track.  Both picture tracks are cut
against the same ranges and laid down at the same running offset, so
removing a take cannot slide one speaker against the other.

The closing CTA comes from anywhere in the episode
--------------------------------------------------
A reel's body is ONE contiguous master window with its bad takes taken
out of it.  Its closer need not be next to it: `reel_ranges` appends the
moment's `call_to_action` range LAST, and `placements` lays the ranges
end to end at a running offset without caring how far apart they were on
the master.  That is not a new capability in the placement math - it has
always taken a list of ranges and never objected to a distant one - it is
only the first thing to hand it one.

Why this had to exist: the captain's format closes every reel on a
genuinely spoken call to action, and this episode says about six of them
in nineteen minutes.  While a reel was one window, "every reel ends on a
spoken CTA" and "about sixteen reels" could not both be true, and the
batch came out at three.  **The same CTA range may close any number of
reels** - nothing is copied or synthesised to do it, the same real clip is
placed again, which is an ordinary editing move.

Two things this deliberately is not:

- **Not a way to assemble a body.**  The captain rejected a batch that
  read as "two halves of different scripts combined".  `reel_ranges`
  takes ONE body window and at most ONE closer, so a collage is not
  expressible here rather than merely discouraged.
- **Not a chooser.**  Which passage is a good CTA, and which reel it
  suits, is the model's judgement and the captain's approval.  Nothing in
  this module scores, ranks or matches one.

The CTA range is placed WHOLE - `redundant_takes` is not run over it.  It
is a passage the plan named to the second and the captain approved; a
retake scan silently shortening the closer would be a worse failure than
leaving a repetition in a clip somebody chose deliberately.

`tests/test_reel_build.py`.
"""
from __future__ import annotations

from library.tools.resolve_lock import assert_current_timeline
from library.tools.timeline_ingest import resolve_project_exactly

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

CUT_CONTAINMENT = 0.75
CUT_JACCARD = 0.55
CUT_WINDOW_SECONDS = 30.0
DURATION_RATIO = 2.0
"""A cut needs all of these. Each one is here because dropping it removed
real content from the sixteen approved reels - see the module docstring.

`MIN_TAKE_SECONDS = 1.5` was a sixth condition and is GONE, 2026-09-05.
It skipped any pair where EITHER side was shorter than a second and a
half, and the protection the docstring claims for a duration test -
"without a duration test it drops a 4.3s line to keep a 0.5s fragment of
the same sentence" - is `DURATION_RATIO`'s, not its: a 4.3s line against
a 0.5s fragment is a ratio of 8.6 and was already refused.  What the
floor uniquely blocked was a pair where BOTH sides are short and their
durations agree, which is a retake WhisperX happened to segment finely
rather than anything a viewer would call short.

Measured on reel 03 of the captain's approved nineteen: three takes of
"search didn't change, the question changed, whoever AI understands best
gets the answer" inside forty seconds, its retakes segmented at 0.20s to
1.66s, the identical pair at 309.92/310.12 scoring containment 1.000 and
Jaccard 1.000 with a duration ratio of 1.30 - and `redundant_takes`
returned NOTHING, because every side was under the floor.  The proposal's
own word-stream detector reported the repeat at similarity 1.0 and the
build left it in.  A number with no reason of its own, standing between a
confidently detected repeat and the cut it asked for, is the hardcoded
threshold this pipeline does not have (AGENTS.md 10.5)."""

SUSPECT_CONTAINMENT = 0.60
"""Below the cut bar and above this, a marker is written instead."""

MIN_RANGE_SECONDS = 0.04
"""The shortest range `keep_ranges` will emit, and the same bound
`_is_removed` reads.

MECHANICAL, not taste: a frame at this project's 24000/1001 fps is
0.0417s, so a range under this places no picture at all. It was already
here as the bare literal `b - a > 0.04` in `keep_ranges`; naming it is
the whole change, so that "this segment leaves nothing placeable" and
"this range places nothing" ask one question rather than two."""

def required_tracks(clips) -> dict:
    """How many video AND AUDIO tracks a reel needs.

    **A reel needs one AUDIO track per picture track, and a new timeline
    has exactly ONE.** This is not symmetry for its own sake: measured
    2026-09-04, the first sixteen reels were built with two video tracks
    and one audio track, so Akshita's V1 clips carried their sound to A1
    and Craig's V2 clips had nowhere to put theirs. Resolve placed his
    PICTURE and discarded his AUDIO, returning True the whole way. Every
    reel reported success and played with one speaker silent - on a
    format whose entire unit is a two-speaker conversation.

    The tell is exact and worth knowing: A1's item count equalled V1's in
    all sixteen, and the V2 items contributed no audio at all.

    AGENTS.md 5 already warned about the reverse - placing V1 clips while
    extra tracks exist floods the timeline - so the rule is that track
    counts are DECLARED from the material, never left at the default.
    """
    indexes = sorted({c.track_index for c in clips})
    return {"video": max(indexes) if indexes else 1,
            "audio": max(indexes) if indexes else 1}


def audio_layout(master_clips) -> Dict[int, set]:
    """Which SOURCE FILES belong on each audio track, from the master.

    **Every source here carries FOUR audio channels**, and a linked
    append brings all of them - Resolve then spreads them across whatever
    audio tracks exist. On a two-track reel that put Akshita on A1 AND
    A2, and Craig on A2 underneath her.

    The captain's master already answers the question: A1 holds only
    their three files and A2 only Craig's four. So the layout is READ off
    the master rather than assumed, and `strays` names anything that
    landed outside it.

    This is the second half of the missing-audio defect. Adding the
    audio track stopped Craig being dropped; without this, he is present
    but mixed under a duplicate of Akshita.
    """
    out: Dict[int, set] = {}
    for clip in master_clips:
        out.setdefault(clip.track_index, set()).add(clip.source_file)
    return out


def strays(timeline, layout: Dict[int, set]) -> List:
    """Audio items sitting on a track their source does not belong to."""
    out = []
    for index, allowed in layout.items():
        for item in (timeline.GetItemListInTrack("audio", index) or []):
            pool_item = item.GetMediaPoolItem()
            path = pool_item.GetClipProperty("File Path") if pool_item else None
            if path and path not in allowed:
                out.append(item)
    return out


REEL_RESOLUTION = (1080, 1920)
"""Set EXPLICITLY on every reel timeline.

Measured in phase one: the PROJECT's own resolution is 3840x2160 and only
the existing timelines override it, so a timeline created through the API
inherits the horizontal UHD default. That is a silent wrong answer rather
than an error, and sixteen of them would be sixteen rebuilds."""


class ReelBuildError(RuntimeError):
    """A reel could not be built safely."""


@dataclass(frozen=True)
class Cut:
    """One take removed, and the one kept in its place."""

    dropped_start: float
    dropped_end: float
    dropped_text: str
    kept_start: float
    kept_end: float
    kept_text: str
    speaker: Optional[str]
    containment: float
    jaccard: float

    def as_dict(self) -> dict:
        return {
            "dropped_start": round(self.dropped_start, 2),
            "dropped_end": round(self.dropped_end, 2),
            "dropped_text": self.dropped_text,
            "kept_start": round(self.kept_start, 2),
            "kept_end": round(self.kept_end, 2),
            "kept_text": self.kept_text,
            "speaker": self.speaker,
            "containment": round(self.containment, 3),
            "jaccard": round(self.jaccard, 3),
            "kept": "the later take - a retake exists because the first "
                    "was flubbed",
        }


@dataclass(frozen=True)
class Blocked:
    """A repeated take the scan could not pair SAFELY.

    Same speaker, inside the same window, over the containment and
    Jaccard bars - and refused because the two readings differ in
    length by more than `DURATION_RATIO`.  That refusal is right on its
    own terms: it is what stops a 4.3s line being dropped to keep a
    0.5s fragment of it.

    It is recorded rather than discarded because the refusal is only
    safe in ISOLATION.  A blocked segment sitting in the middle of a run
    of repeated lines is not a pair the cutter declined to judge - it is
    a piece of a take the cutter is otherwise removing, and leaving it
    behind strands it (see `redundant_runs`)."""

    start: float
    end: float
    text: str
    matched_start: float
    matched_end: float
    matched_text: str
    speaker: Optional[str]
    containment: float
    jaccard: float
    duration_ratio: float

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "text": self.text,
            "matched_start": round(self.matched_start, 2),
            "matched_end": round(self.matched_end, 2),
            "matched_text": self.matched_text,
            "speaker": self.speaker,
            "containment": round(self.containment, 3),
            "jaccard": round(self.jaccard, 3),
            "duration_ratio": round(self.duration_ratio, 2),
            "refused_by": (f"the two readings differ in length by "
                           f"{self.duration_ratio:.2f}x, over "
                           f"DURATION_RATIO={DURATION_RATIO}"),
        }


def _pair_scores(a: dict, b: dict) -> Tuple[float, float]:
    from library.tools.reel_proposal import _content_words

    wa, wb = _content_words(a.get("text", "")), _content_words(b.get("text", ""))
    if not wa or not wb:
        return 0.0, 0.0
    shared = len(wa & wb)
    return shared / min(len(wa), len(wb)), shared / len(wa | wb)


def _segments_in(start: float, end: float, transcript: dict) -> List[dict]:
    from library.tools.reel_proposal import bound_segments

    return sorted(
        (s for s in bound_segments(transcript)
         if s["timeline_end"] > start and s["timeline_start"] < end),
        key=lambda s: s["timeline_start"])


def _scan(start: float, end: float, transcript: dict,
          containment_floor: float, jaccard_floor: float,
          enforce_shape: bool) -> Tuple[List[Cut], List[Blocked]]:
    """Every pair the text bars accept, split by what the SHAPE test did.

    The two returns are the same scan seen twice: `cuts` are the pairs
    that passed every condition, `blocked` are the ones that read as the
    same sentence by the same speaker inside the same window and were
    refused ONLY because their durations disagree by more than
    `DURATION_RATIO`.

    A blocked pair used to be a `continue` and nothing else, which is
    how a run of three repeated lines came to have two of them cut and
    the third left standing (see `redundant_runs`).  Scoring now happens
    BEFORE the shape test so that a refusal can say what it refused;
    which pairs are CUT is unchanged, because a cut still needs both.
    """
    inside = _segments_in(start, end, transcript)
    used, found, blocked = set(), [], []
    for index, first in enumerate(inside):
        if id(first) in used:
            continue
        held: Optional[Blocked] = None
        for second in inside[index + 1:]:
            if id(second) in used or first.get("speaker") != second.get("speaker"):
                continue
            if second["timeline_start"] - first["timeline_end"] > CUT_WINDOW_SECONDS:
                break
            da = first["timeline_end"] - first["timeline_start"]
            db = second["timeline_end"] - second["timeline_start"]
            if da <= 0 or db <= 0:
                continue
            containment, jaccard = _pair_scores(first, second)
            if containment < containment_floor or jaccard < jaccard_floor:
                continue
            ratio = max(da, db) / min(da, db)
            if enforce_shape and ratio > DURATION_RATIO:
                if held is None:
                    held = Blocked(
                        start=float(first["timeline_start"]),
                        end=float(first["timeline_end"]),
                        text=(first.get("text") or "").strip(),
                        matched_start=float(second["timeline_start"]),
                        matched_end=float(second["timeline_end"]),
                        matched_text=(second.get("text") or "").strip(),
                        speaker=first.get("speaker"),
                        containment=containment, jaccard=jaccard,
                        duration_ratio=ratio)
                continue
            used.add(id(first))
            used.add(id(second))
            found.append(Cut(
                dropped_start=float(first["timeline_start"]),
                dropped_end=float(first["timeline_end"]),
                dropped_text=(first.get("text") or "").strip(),
                kept_start=float(second["timeline_start"]),
                kept_end=float(second["timeline_end"]),
                kept_text=(second.get("text") or "").strip(),
                speaker=first.get("speaker"),
                containment=containment, jaccard=jaccard))
            held = None
            break
        if held is not None:
            blocked.append(held)
    return found, blocked


@dataclass(frozen=True)
class RedundantRun:
    """One uninterrupted run of repeated speech, and whether it can go.

    A run is a maximal chain of segments that are CONSECUTIVE in the
    span's own segment list, carry ONE speaker, and are every one of them
    a repeated take - one the scan either cut or blocked.  The chain
    breaks at the first segment that is neither, and at a change of
    speaker.  There is no time threshold in that definition and there
    must not be one: "nothing else was said in between" is a fact about
    the transcript, not a number somebody chose.

    `whole` is False when any member is `Blocked`, which is the only way
    a run can be partly removable.
    """

    segments: Tuple[dict, ...]
    cuts: Tuple[Cut, ...]
    blocked: Tuple[Blocked, ...]

    @property
    def whole(self) -> bool:
        return not self.blocked

    @property
    def withdrawn(self) -> bool:
        """Did holding this run whole actually take a cut away?

        A run with a blocked member and NO cut in it is the duration
        guard doing its ordinary job on a lone pair - nothing was going
        to be removed there and nothing is being withheld.  Only a run
        that has both is a cut this rule withdrew, and only those are
        reported, so the report accounts for exactly the difference the
        rule makes."""
        return bool(self.cuts) and bool(self.blocked)

    @property
    def start(self) -> float:
        return float(self.segments[0]["timeline_start"])

    @property
    def end(self) -> float:
        return float(max(s["timeline_end"] for s in self.segments))

    @property
    def speaker(self) -> Optional[str]:
        return self.segments[0].get("speaker")

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "speaker": self.speaker,
            "lines": [(segment.get("text") or "").strip()
                      for segment in self.segments],
            "would_have_cut": [cut.as_dict() for cut in self.cuts],
            "could_not_cut": [block.as_dict() for block in self.blocked],
            "why_nothing_was_cut": (
                f"{len(self.cuts)} of {len(self.segments)} lines in this "
                f"repeated run could be paired safely and "
                f"{len(self.blocked)} could not, so cutting it would have "
                f"removed part of a take and left the rest standing where "
                f"its own opening used to be. A take is removed WHOLE or "
                f"not at all, so this repetition is still in the reel."),
            "the_fix": (
                "decide which take to keep and redraw the span past the "
                "other, or leave the repetition in - the choice of take "
                "is a judgement and this is not the place that makes it"),
        }


def redundant_runs(start: float, end: float,
                   transcript: dict) -> List[RedundantRun]:
    """Every run of repeated speech inside a span, WHOLE or not.

    Why a run rather than a pair
    ---------------------------
    Measured on reel 03 of the captain's approved nineteen, span
    301.24-341.27s: Akshita says one sentence three times, and the
    transcript segments the first take as three consecutive lines -

        301.24-302.57  "Yeah, so search didn't change."
        302.63-303.45  "The question changed."
        303.55-306.40  "And whoever AI best understands, gets the answer."

    The first two paired with the second take at containment 1.000 and
    Jaccard 1.000 and were CUT.  The third paired with the second take's
    own third line at containment 1.000 and Jaccard 1.000 too, and was
    refused because 2.851s against 0.600s is a ratio of 4.75, over
    `DURATION_RATIO`.  So two thirds of a take were removed and its tail
    was left - and because the take was at the head of the span, that
    orphaned tail became the reel's FIRST LINE.  The reel opened on
    "And whoever AI best understands, gets the answer", the answer
    before the question, and the model's own written hook did not arrive
    until 3.41 seconds in.

    The guard was not wrong.  It answers a MECHANICAL question - are
    these two utterances the same sentence, safely enough to drop one -
    and 2.851s against 0.600s is exactly the shape it exists to refuse
    (`test_a_fragment_is_never_kept_over_a_full_line`).  What it must not
    do is decide, on its own, that two thirds of a take may go: whether
    what is left reads is a judgement, and this module does not hold it.

    So the unit of a cut is the RUN, and the rule has no number in it:

        **A repeated run is removed WHOLE or not at all.**

    A run with a blocked member is removed not at all, and is REPORTED
    instead - `refused_take_groups`, carried per moment by
    `reel_proposal.enrich` so the model that chose the span sees it while
    it can still redraw the span, and printed by
    `rebuild_reels_in_project` so an operator sees it at build time.
    """
    inside = _segments_in(start, end, transcript)
    cuts, blocked = _scan(start, end, transcript,
                          CUT_CONTAINMENT, CUT_JACCARD, True)
    cut_at = {round(c.dropped_start, 4): c for c in cuts}
    blocked_at = {round(b.start, 4): b for b in blocked}

    runs: List[RedundantRun] = []
    chain: List[dict] = []

    def close() -> None:
        if not chain:
            return
        runs.append(RedundantRun(
            segments=tuple(chain),
            cuts=tuple(cut_at[key] for key in
                       (round(float(s["timeline_start"]), 4) for s in chain)
                       if key in cut_at),
            blocked=tuple(blocked_at[key] for key in
                          (round(float(s["timeline_start"]), 4) for s in chain)
                          if key in blocked_at)))
        chain.clear()

    for segment in inside:
        key = round(float(segment["timeline_start"]), 4)
        if key not in cut_at and key not in blocked_at:
            close()
            continue
        if chain and chain[-1].get("speaker") != segment.get("speaker"):
            close()
        chain.append(segment)
    close()
    return runs


def refused_take_groups(start: float, end: float,
                        transcript: dict) -> List[dict]:
    """The repeated runs this span will NOT cut, and why.

    An empty list is a complete answer: every repetition the scan was
    going to cut, it could cut whole.  A non-empty one names a
    repetition that is STILL IN THE REEL, what part of it could have
    gone, what stopped the rest, and that redrawing the span is the way
    out.  Nothing here scores or rejects the moment - it reports
    (AGENTS.md 10.5).

    Only runs where a cut was actually WITHDRAWN appear.  A lone pair
    the duration guard refused is not a withheld cut and is not listed
    here; it is a near miss, and `suspected_takes` is where those live.
    """
    return [run.as_dict() for run in redundant_runs(start, end, transcript)
            if run.withdrawn]


def _overlaps(a_start: float, a_end: float,
              b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _is_removed(segment: dict, cuts: Sequence[Cut]) -> bool:
    """Does `cuts` leave nothing placeable of this segment?

    "Placeable" is `MIN_RANGE_SECONDS`, the bound `keep_ranges` already
    applies to every range it emits, so this asks the same question the
    range arithmetic answers rather than a second one of its own.
    """
    left = (float(segment["timeline_end"]) - float(segment["timeline_start"])
            - sum(_overlaps(float(segment["timeline_start"]),
                            float(segment["timeline_end"]),
                            cut.dropped_start, cut.dropped_end)
                  for cut in cuts))
    return left < MIN_RANGE_SECONDS


def assert_takes_are_whole(cuts: Sequence[Cut], start: float, end: float,
                           transcript: dict) -> None:
    """REFUSE a cut list that would remove part of a repeated run.

    Asked of the LIST ABOUT TO BE APPLIED, whatever produced it - the
    same reason `assert_deletion_scope` is not folded into
    `timelines_to_replace`.  A guard that is the selection restated
    cannot catch the selection being wrong, and `redundant_takes` has
    TWO producers: the pair scan, and the word-stream detector whose
    findings are appended after it.  This one re-derives the runs from
    the transcript and checks the cut list against them.

    **Why this rule cannot strand a fragment at the head of a reel.**  A
    reel opens on the first second its keep ranges retain.  A cut only
    ever removes segments, and after this guard every run a cut touches
    is removed entirely - so the segment that leads the reel is either
    the span's own first segment, untouched, or the first segment of
    whatever follows a wholly removed run: the start of another
    speaker's turn or of speech that is not a repetition at all.  A
    partly removed run is what leaves a tail with its own opening gone,
    and a partly removed run is not expressible once this passes.
    """
    for run in redundant_runs(start, end, transcript):
        touched = [segment for segment in run.segments
                   if any(_overlaps(float(segment["timeline_start"]),
                                    float(segment["timeline_end"]),
                                    cut.dropped_start, cut.dropped_end) > 0
                          for cut in cuts)]
        if not touched:
            continue
        left = [segment for segment in run.segments
                if not _is_removed(segment, cuts)]
        if left:
            raise ReelBuildError(
                f"REFUSING to cut: {len(touched)} line(s) of a "
                f"{len(run.segments)}-line repeated run "
                f"({run.start:.2f}-{run.end:.2f}s, {run.speaker}) would be "
                f"removed and {len(left)} would be left standing - "
                f"{[(segment.get('text') or '').strip()[:50] for segment in left]}. "
                f"A take is removed WHOLE or not at all: removing part of "
                f"one strands what is left where its own opening used to "
                f"be, which is how reel 03 came to open on the answer "
                f"before the question was asked. Either the whole run "
                f"goes, or none of it does and "
                f"`refused_take_groups` reports it.")


def redundant_takes(start: float, end: float, transcript: dict) -> List[Cut]:
    """Takes confident enough to REMOVE, and WHOLE enough to be coherent.

    Two producers, one rule.  The pair scan finds retakes the transcript
    segmented into separate lines; `reel_proposal.duplicate_takes` finds
    the ones it did not, off the word stream.  Both are then held to
    `redundant_runs`: a cut inside a run that cannot be removed whole is
    withdrawn, and `refused_take_groups` says so.
    """
    from library.tools.reel_proposal import duplicate_takes
    cuts, _blocked = _scan(start, end, transcript,
                           CUT_CONTAINMENT, CUT_JACCARD, True)

    for dt in duplicate_takes(start, end, transcript):
        seg1 = _segments_in(dt["first_start"], dt["first_end"], transcript)
        seg2 = _segments_in(dt["second_start"], dt["second_end"], transcript)
        if seg1 and seg2 and id(seg1[0]) == id(seg2[0]):
            # Evidence from reel 03 (301.2-341.3) and reel 05 (287, 792) confirms the earlier takes were aborted flubs and the later take is the completed thought.
            cuts.append(Cut(
                dropped_start=dt["first_start"],
                dropped_end=dt["first_end"],
                dropped_text=dt.get("first_text", "").strip(),
                kept_start=dt["second_start"],
                kept_end=dt["second_end"],
                kept_text=dt.get("second_text", "").strip(),
                speaker=seg1[0].get("speaker"),
                containment=dt.get("similarity", 0.0),
                jaccard=dt.get("similarity", 0.0)
            ))

    refused = [run for run in redundant_runs(start, end, transcript)
               if not run.whole]
    kept = [cut for cut in cuts
            if not any(_overlaps(cut.dropped_start, cut.dropped_end,
                                 run.start, run.end) > 0 for run in refused)]
    return sorted(kept, key=lambda c: c.dropped_start)


def suspected_takes(start: float, end: float, transcript: dict) -> List[Cut]:
    """Near misses. These become MARKERS, never edits."""
    confident = {(c.dropped_start, c.kept_start)
                 for c in redundant_takes(start, end, transcript)}
    loose, _blocked = _scan(start, end, transcript,
                            SUSPECT_CONTAINMENT, 0.0, False)
    return [c for c in loose if (c.dropped_start, c.kept_start) not in confident]


def keep_ranges(start: float, end: float,
                cuts: Sequence[Cut]) -> List[Tuple[float, float]]:
    """The reel's span with each dropped take taken out of it.

    Ranges are over the MASTER's timebase and are applied to both picture
    tracks identically, which is what keeps the two speakers in sync.
    """
    ranges = [(start, end)]
    for cut in sorted(cuts, key=lambda c: c.dropped_start):
        out: List[Tuple[float, float]] = []
        for a, b in ranges:
            if cut.dropped_end <= a or cut.dropped_start >= b:
                out.append((a, b))
                continue
            if a < cut.dropped_start:
                out.append((a, cut.dropped_start))
            if cut.dropped_end < b:
                out.append((cut.dropped_end, b))
        ranges = out
    return [(a, b) for a, b in ranges if b - a > MIN_RANGE_SECONDS]


def cta_range(moment) -> Optional[Tuple[float, float]]:
    """The moment's closing CTA range on the MASTER, or None.

    One place reads `call_to_action` off a moment, so a moment that does
    not carry the attribute at all - an older plan, a stand-in - reads as
    "no closer" rather than raising.

    **A closer must present two real NUMBERS, or it is not a closer.**
    That is not defensiveness about types: a bare `unittest.mock.MagicMock`
    answers every attribute with a truthy mock, and `float()` of one is
    1.0, so a moment stand-in that never mentioned a CTA otherwise reads
    as closing on the single second 1.00-1.00. `ReelMoment.from_dict`
    already coerces the real thing through `float()`, so anything
    reaching here that is not a number is a stand-in rather than a plan.
    """
    cta = getattr(moment, "call_to_action", None)
    if cta is None:
        return None
    start = getattr(cta, "timeline_start", None)
    end = getattr(cta, "timeline_end", None)
    if not isinstance(start, (int, float)) or isinstance(start, bool):
        return None
    if not isinstance(end, (int, float)) or isinstance(end, bool):
        return None
    return (float(start), float(end))


def reel_ranges(moment, transcript: dict) -> List[Tuple[float, float]]:
    """Every master range this reel plays, IN THE ORDER IT PLAYS THEM.

    The body first, with its bad takes cut out of it, and then the
    closing CTA - which may come from anywhere in the episode and is
    under no obligation to be adjacent to, or after, the body.

    This is the ONE place that order is spelled.  `build_reel_timeline`,
    the caption pass and the conformance verifier all call it, so a reel
    cannot be built to one order and checked against another.
    """
    cuts = redundant_takes(moment.timeline_start, moment.timeline_end,
                           transcript)
    # The cut list is checked against the transcript's own runs before it
    # becomes the reel's shape, so a producer that bypassed
    # `redundant_takes` cannot strand a fragment silently.
    assert_takes_are_whole(cuts, moment.timeline_start, moment.timeline_end,
                           transcript)
    ranges = keep_ranges(moment.timeline_start, moment.timeline_end, cuts)
    closer = cta_range(moment)
    if closer is None:
        return ranges
    if closer[1] - closer[0] <= 0.04:
        raise ReelBuildError(
            f"the closer runs {closer[0]:.2f}-{closer[1]:.2f}s, under a "
            f"frame. Nothing can be placed from it.")
    if min(closer[1], moment.timeline_end) > max(closer[0],
                                                 moment.timeline_start):
        raise ReelBuildError(
            f"the closer ({closer[0]:.2f}-{closer[1]:.2f}s) overlaps its "
            f"own body ({moment.timeline_start:.2f}-"
            f"{moment.timeline_end:.2f}s), so the reel would play those "
            f"seconds twice and `reel_time` would map them to the first "
            f"copy only - leaving the second silently uncaptioned. "
            f"`validate_proposal` refuses this when the proposal is "
            f"WRITTEN; this is the same refusal at BUILD time, because a "
            f"plan is a file the captain edits and `read_proposal` does "
            f"not re-run validation.")
    return ranges + [closer]


def closer_seam(moment, ranges: Sequence[Tuple[float, float]]
                ) -> Optional[float]:
    """The REEL second at which the closer starts, or None if there is
    no closer.

    The one seam a caption card may not span. The seams a bad-take cut
    leaves are NOT this: those join speech the editor deliberately made
    contiguous, and a card reading across one is a sentence as spoken.
    A closer is a different passage of the episode, and a card joining
    the body's last words to its first would be a sentence nobody said.
    """
    if cta_range(moment) is None:
        return None
    return sum(range_end - range_start
               for range_start, range_end in ranges[:-1])


def reel_time(master_time: float,
              ranges: Sequence[Tuple[float, float]],
              at_end: bool = False) -> Optional[float]:
    """Where a MASTER second lands on the reel, or None if it was cut.

    A reel is its keep ranges laid end to end, so a caption timed against
    the master has to come through the same arithmetic the picture did -
    otherwise removing a bad take slides every caption after it out of
    sync with the speech it belongs to.

    The ranges are walked IN LIST ORDER and the first one containing the
    second wins, so a closing CTA range that sits earlier on the master
    than the body still maps to the END of the reel.  That holds because
    the ranges are DISJOINT: a CTA overlapping its own body is refused by
    `reel_proposal.validate_proposal` when the proposal is written and by
    `reel_ranges` when the reel is built, which is what stops one master
    second having two answers here.

    A range is half-open, `[start, end)`, which is the right reading for
    a word's START and the wrong one for its END. `snap_to_speech` lands
    a range end EXACTLY on a segment's `timeline_end`, which is exactly
    the last word's end - so the closing word of every range read the
    exclusive way falls outside every range and returns None. On a reel
    that closes on a CTA those are the words the whole feature exists to
    deliver. Pass `at_end=True` to read `(start, end]` instead.
    """
    cursor = 0.0
    for range_start, range_end in ranges:
        inside = (range_start < master_time <= range_end if at_end
                  else range_start <= master_time < range_end)
        if inside:
            return cursor + (master_time - range_start)
        cursor += range_end - range_start
    return None


def placements(ranges: Sequence[Tuple[float, float]],
               clips: Sequence, fps: float) -> List[dict]:
    """Where each master clip lands on the reel, in exact frames and seconds.

    One entry per (keep range, overlapping clip). `record` is the running
    offset on the REEL, so the ranges close up and both tracks move
    together. Math is done in frames to prevent rounding holes at cuts.
    """
    out: List[dict] = []
    cursor_frames = 0
    for range_start, range_end in ranges:
        range_start_f = int(round(range_start * fps))
        range_end_f = int(round(range_end * fps))
        range_frames = range_end_f - range_start_f
        
        for clip in clips:
            clip_start_f = int(round(clip.timeline_start * fps))
            clip_end_f = int(round(clip.timeline_end * fps))
            
            overlap_start_f = max(clip_start_f, range_start_f)
            overlap_end_f = min(clip_end_f, range_end_f)
            if overlap_end_f - overlap_start_f <= 0:
                continue
                
            into_clip_f = overlap_start_f - clip_start_f
            clip_source_in_f = int(round(clip.source_in * fps))
            
            # Keep seconds for backward compatibility, but provide exact snapped frames
            source_in_f = clip_source_in_f + into_clip_f
            source_out_f = source_in_f + (overlap_end_f - overlap_start_f)
            record_f = cursor_frames + (overlap_start_f - range_start_f)
            
            out.append({
                "clip": clip,
                "source_in": source_in_f / fps,
                "source_out": source_out_f / fps,
                "record": record_f / fps,
                "snapped_record": record_f,
                "track_index": clip.track_index,
                "speaker": clip.speaker,
            })
        cursor_frames += range_frames
    return out


class _SegmentsWithEntries(list):
    """A list of rendered segments that also carries the plan's entries.

    ``reel_subtitle_segments`` returns rendered segments (overlay paths)
    for timeline building, but the provenance hash must digest the plan's
    CAPTION ENTRIES (text, start, length) and the FOOTAGE BINDING (which
    clips they were computed against).  This subclass is a plain list
    everywhere an iterable or list is expected, so
    ``build_reel_timeline`` and test mocks are unaffected, while the
    build loop can read ``.caption_entries`` for the caption hash and
    ``.spine`` for the footage binding hash.
    """

    def __init__(self):
        super().__init__()
        self.caption_entries = []
        self.spine = None


def reel_subtitle_segments(moment, transcript: dict, ranges, project_folder: str,
                           fps: float, width: int, height: int,
                           timeline_name: str = "") -> list:
    """Caption one reel THROUGH THE PIPELINE'S OWN STEPS.

    The captain's ruling of 2026-09-04: `reel_subtitles.py` should never
    have existed, and "whatever funcitonality was put into that python
    file should have been augmented into the pipeline". This is that
    augmentation, and it adds no caption logic of its own:

        reel_spine.spine_for_reel   the reel's own audio spine, in reel time
        subtitles.plan              step 4.01 groups and styles the cards
        subtitles.render_segment    step 4.05 renders each one

    Both steps are reached through the operation registry, so this is a
    named operation being driven rather than a script reimplementing a
    step. Neither step knows a reel from a master - the spine is the only
    thing that differs, which is the whole point of the producer.

    Per-speaker styling is 4.01's, resolved from the spine's own speakers.
    This function names no speaker: the list that used to live here was
    hardcoded to one series' two hosts.
    """
    import sys

    from library.steps.step_4_05_render_subtitles.generate_remotion_props import (
        generate_subtitle_props_per_block,
    )
    from library.tools import operations
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.reel_spine import spine_for_reel

    # What Resolve will CALL this reel.  It is the segment name's
    # `timeline` component (library/tools/subtitle_segment_id.py), and
    # that component is what stops one reel's overlay overwriting
    # another's - so a build into a different container must caption
    # into different files, or it silently rewrites the overlays the
    # timeline it is NOT touching is still pointing at.
    name = timeline_name or moment.timeline_name

    # A reel that cannot be spined is REPORTED and built without
    # captions, not allowed to abort the other eighteen. The reason is
    # printed rather than swallowed: a reel silently shipping with no
    # subtitles is the defect this whole change exists to fix.
    from library.tools.reel_spine import ReelSpineError
    try:
        spine = spine_for_reel(moment, transcript, ranges)
    except ReelSpineError as why:
        print(f"  {name}: NO CAPTIONS - {why}",
              file=sys.stderr)
        return []
    if spine.get("bleed_blocks_dropped"):
        print(f"  {name}: dropped "
              f"{spine['bleed_blocks_dropped']} mic-bleed block(s)",
              file=sys.stderr)
    # A row the transcriber split mid-sentence is too short to carry a
    # legible card and is given back to its sentence upstream of the
    # grouping.  Both halves are SAID: how many were rejoined, and the
    # text of every one that could not be - the latter is a card that
    # will flash, and it is named rather than left to a warning three
    # steps later.
    if spine.get("fragment_blocks_merged"):
        print(f"  {name}: rejoined "
              f"{spine['fragment_blocks_merged']} mid-sentence row(s) "
              f"to their own sentence", file=sys.stderr)
    for text in spine.get("fragment_blocks_unmerged") or []:
        print(f"  {name}: SHORT BLOCK {text!r} - under the caption floor "
              f"and nothing contiguous on the side its sentence runs, so "
              f"its card will be short", file=sys.stderr)
    # Speech this reel PLAYS that no block carries, in reel seconds. The
    # honest outcome for a row nothing can bind is that the build SAYS
    # which seconds go uncaptioned, rather than something guessing a
    # clip for it.
    for span in spine.get("unbindable_spans") or []:
        print(f"  {name}: NO CAPTION over reel "
              f"{span['reel_start']:.2f}-{span['reel_end']:.2f}s "
              f"({span['seconds']:.2f}s of {span['speaker']}'s words, "
              f"master {span['master_start']:.1f}-{span['master_end']:.1f}) "
              f"- no clip carries them", file=sys.stderr)

    plan = operations.get("subtitles.plan").run(
        spine, brand_effect={}, brand_style={}, project_folder=project_folder)
    plan_entries = (plan.get("subtitle_plan") or {}).get(
        "subtitle_entries") or []
    props_list = generate_subtitle_props_per_block(
        plan["subtitle_plan"], fps=int(round(fps)), width=width, height=height,
        audio_spine=spine)
    if not props_list:
        result = _SegmentsWithEntries()
        result.caption_entries = plan_entries
        result.spine = spine
        return result

    out_dir = str(ProjectLayout(project_folder).write_dir(
        Area.SUBTITLE_SEGMENTS, step="render_subtitles"))
    render = operations.get("subtitles.render_segment")
    segments = _SegmentsWithEntries()
    segments.caption_entries = plan_entries
    segments.spine = spine
    for index, props in enumerate(props_list, 1):
        rendered = render.run(props, out_dir, name,
                              progress=f"[{index}/{len(props_list)}]")
        if rendered is not None:
            segments.append(rendered)
    return segments


def build_reel_timeline(project, moment, master_clips, subtitle_segments, fps, width, height, project_folder, transcript, timeline_name: str = ""):
    """Place one reel.  `timeline_name` is what Resolve will CALL it.

    Defaults to `moment.timeline_name`, which is the plan's own name and
    what every build did before `built_name` existed.  A caller that
    passes something else is building the same reel into a different
    container - see `built_name`.
    """
    import sys, os
    name = timeline_name or moment.timeline_name
    pool = project.GetMediaPool()
    timeline = pool.CreateEmptyTimeline(name)
    if not timeline:
        raise ValueError(f"Failed to create timeline {name}")
        
    project.SetCurrentTimeline(timeline)
    
    timeline.SetSetting("useCustomSettings", "1")
    timeline.SetSetting("timelineResolutionWidth", "1080")
    timeline.SetSetting("timelineResolutionHeight", "1920")
    
    while timeline.GetTrackCount("video") < 3:
        timeline.AddTrack("video")
    while timeline.GetTrackCount("audio") < 2:
        timeline.AddTrack("audio")
        
    timeline.SetTrackName("video", 3, "Captions")
    
    ranges = reel_ranges(moment, transcript)
    placements_list = placements(ranges, master_clips, fps)
    
    root_folder = pool.GetRootFolder()
    def _find_pool_item(folder, filepath):
        for item in folder.GetClipList():
            if item.GetClipProperty("File Path") == filepath:
                return item
        for sub in folder.GetSubFolderList():
            found = _find_pool_item(sub, filepath)
            if found: return found
        return None
        


        
    for p in placements_list:
        c = p["clip"]
        pool_item = _find_pool_item(root_folder, c.source_file)
        if not pool_item:
            print(f"Source file {c.source_file} not in media pool", file=sys.stderr)
            continue

        pool_fps_str = pool_item.GetClipProperty("FPS") or str(fps)
        pool_fps = float(pool_fps_str)

        assert_current_timeline(project, timeline)

        pool.AppendToTimeline([{
            "mediaPoolItem": pool_item,
            "startFrame": int(round(p["source_in"] * pool_fps)),
            "endFrame": int(round(p["source_out"] * pool_fps)),
            "mediaType": 1 if c.track_type == "video" else 2,
            "trackIndex": p["track_index"],
            "recordFrame": p["snapped_record"]
        }])

    # Captions are PLACED here and RENDERED by step 4.05, which is the
    # pipeline's renderer. This used to carry its own `npx remotion
    # render` loop - a third implementation of the same call - and it is
    # gone; `reel_subtitle_segments` above drives the step instead.
    for segment in (subtitle_segments or []):
        items = pool.ImportMedia([segment["overlay_path"]])
        if not items:
            print(f"Failed to import {segment['overlay_path']}",
                  file=sys.stderr)
            continue

        assert_current_timeline(project, timeline)
        pool.AppendToTimeline([{
            "mediaPoolItem": items[0],
            # 4.05 renders animation handles either side of the content
            # and reports where the content actually starts and ends.
            # Placing the whole rendered clip would overlap the next.
            "startFrame": segment["source_in_frame"],
            "endFrame": segment["source_out_frame"],
            "trackIndex": 3,
            "recordFrame": int(round(segment["timeline_start"] * fps)),
        }])


def timelines_to_replace(project, target_names) -> list:
    """The existing timelines THIS build will replace, by exact name.

    Exact, never a prefix.  The loop this replaces collected every
    timeline whose name began `"Reel "`, which on the field-test project
    is nineteen approved timelines - so building one reel deleted the
    other eighteen and deleted any reel the current plan no longer
    contains, with nothing backing them up and nothing saying so.

    A near match lands elsewhere is already the rule for addressing a
    Resolve PROJECT (AGENTS.md 5).  It is the same rule for a timeline,
    and this is the call site where getting it wrong destroys work
    rather than reading the wrong thing.
    """
    found = []
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() in target_names:
            found.append(timeline)
    return found


def assert_deletion_scope(timelines, target_names) -> None:
    """REFUSE to delete a timeline this build did not plan to place.

    The captain's ruling of 2026-09-06, on finding the unconditional
    delete on main: *"a refusal is cheap and a deleted timeline is
    not"*.

    This is deliberately NOT the same computation as
    `timelines_to_replace`, and it is deliberately not folded into it.
    A guard that is the selection restated cannot catch the selection
    being wrong; this one is asked of the LIST ABOUT TO BE DELETED,
    whatever produced it, immediately before `DeleteTimelines`.  So a
    later change to how the delete set is chosen fires this rather than
    quietly widening the blast radius, which is exactly how the loop it
    replaces came to take nineteen timelines to build one.

    `tests/test_reel_build_touches_only_its_own_timelines.py` calls this
    directly with both a permitted and a refused list, and drives a
    build with an over-collecting selection to prove it fires where it
    is actually wired.
    """
    unplanned = sorted(
        timeline.GetName() for timeline in timelines
        if timeline.GetName() not in target_names)
    if unplanned:
        raise ReelBuildError(
            f"REFUSING to build: it would delete {len(unplanned)} "
            f"timeline(s) this build never planned to place - "
            f"{unplanned}. This build places "
            f"{sorted(target_names)}. A timeline that is not being "
            f"rebuilt must be left alone: deleting it destroys approved "
            f"work that nothing here backs up, and a build that quietly "
            f"widened its own delete set is how nineteen approved reels "
            f"came to be deleted in order to write one.")


def reel_numbers(only) -> Optional[set]:
    """`only`, normalised to a set of reel numbers, or `None` for all.

    Here rather than in `step_7_01_build_reels`, because this is the
    module that READS the value: what a malformed one means is the
    reader's to say, and a guard in the step body would make that step
    refuse without an input its own manifest declares OPTIONAL - the
    disagreement `tests/test_input_declarations_are_true.py` exists to
    catch (AGENTS.md 3).

    A string is accepted because an operation's overrides can arrive
    from a shell or from JSON on stdin, where `--only-reel 3` and
    `"3 5"` are the same request.  Anything that is not a number
    REFUSES: a build cannot guess which moment a name refers to, and
    guessing wrong places the wrong reel onto a timeline.
    """
    if only is None:
        return None
    if isinstance(only, str):
        only = [part for part in only.replace(",", " ").split() if part]
    try:
        return {int(entry) for entry in only}
    except (TypeError, ValueError) as bad:
        raise ReelBuildError(
            f"only must be reel NUMBERS, got {only!r} ({bad}). A build "
            f"cannot guess which moment a name refers to, and guessing "
            f"wrong places the wrong reel onto a timeline.") from bad


def built_name(moment, name_suffix: str = "") -> str:
    """What Resolve will CALL this reel when THIS build places it.

    The plan owns the reel's name (`ReelMoment.timeline_name`); a build
    owns the container it puts it in.  They are the same string by
    default and must be allowed to differ, because a build that can only
    write the plan's name can only ever REPLACE what is already there.

    Spelled once, here, so the timeline Resolve creates, the name the
    build records, the key `caption_hashes` is filed under and the
    `timeline` component of every caption filename cannot drift apart -
    and a caption filename that drifted would overwrite the overlays a
    timeline this build is not touching still points at.
    """
    return f"{moment.timeline_name}{name_suffix}"


def rebuild_reels_in_project(project_slug: str, skip_captions: bool = False,
                             verify: bool = True, only=None,
                             name_suffix: str = "",
                             organise: bool = True) -> dict:
    """Build every approved reel, and RETURN the record of what was placed.

    A BUILD MAY ONLY DELETE WHAT IT IS ABOUT TO PLACE.  This used to
    delete every timeline whose name began `"Reel "` and then build the
    approved moments, which is a different and much larger act: on the
    field-test project it destroys nineteen approved timelines in order
    to write nineteen, and it destroys any reel the current plan no
    longer contains without saying so.  The names this call will place
    are computed first and only those are deleted, so a rebuild replaces
    exactly its own output and a build of one reel touches one timeline.
    `write_provenance` has merged rather than replaced since #568 for
    the same reason - *"a partial rebuild must not delete the provenance
    of the reels it did not touch"* - and the delete loop was the half
    that made a partial rebuild impossible in the first place.

    `only` selects WHICH approved moments to build, by reel number.
    `None` is every approved moment, which is what every caller had.

    `name_suffix` is appended to the name each reel is built INTO.  `""`
    is the plan's own name, which is what every caller had.  A non-empty
    suffix builds the same plan into a different container, which is the
    only way to compare a rebuild against an approved timeline instead
    of overwriting it.  It reaches the captions too (`built_name`).

    `organise` files the media pool after the build, so a rebuild TIDIES
    UP rather than accumulating: the reels this call placed land in
    `Reels/Current plan`, and a reel the live plan no longer names moves
    to `Reels/Earlier plans` - moved and relabelled, never deleted.
    Without it, `CreateEmptyTimeline` and `ImportMedia` put what they
    make into whatever bin was CURRENT, which is wherever the operator
    last clicked; measured on the field test, that scattered 49
    timelines and 2,573 renders across three bins with nothing recording
    why.  It runs AFTER the build and after provenance, because filing
    is about reels that already exist and a failure to file must not
    read as a failure to build.  See
    `library/tools/resolve_organization.py`.

    `verify` defaults to True, so nothing that called this before gets a
    weaker gate than it had: a direct caller still has the conformance
    verifier run at the end and still gets a raise on a defective build.

    The one caller that passes False is the `build_reels` node of
    `library/processes/reels`, whose process has `verify_reels` as its
    own node.  A build that was placed and a build that conformed are two
    facts that fail for different reasons, and a ledger keyed by node id
    can only tell them apart if two nodes recorded them.  Running the
    verifier in both places would report one set of findings twice under
    two step ids.

    The RETURN VALUE is what the edge to `verify_reels` carries.  Every
    field of it was already computed here and then dropped on the floor -
    `built_reel_names` and `caption_hashes` went into provenance and
    nowhere else - which is how a verifier could re-derive a different
    grouping a day later and grade against it.
    """
    import os
    import sys
    import json
    import yaml
    
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
        os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/libfusionscript.dylib"
        if "PYTHONPATH" not in os.environ:
            os.environ["PYTHONPATH"] = ""
        os.environ["PYTHONPATH"] += ":" + os.environ["RESOLVE_SCRIPT_API"] + "/Modules"
        sys.path.insert(0, os.environ["RESOLVE_SCRIPT_API"] + "/Modules")
        import DaVinciResolveScript as dvr

    from library.tools.resolve_locale import scriptapp_preserving_locale
    from library.tools.reel_proposal import read_proposal
    from library.tools.timeline_ingest import snapshot_timeline
    from library.tools.project_registry import get_project
    
    resolve = scriptapp_preserving_locale(dvr, "Resolve")
    pm = resolve.GetProjectManager()
    
    if os.path.isabs(project_slug) and os.path.isdir(project_slug):
        project_folder = project_slug
    else:
        proj = get_project(project_slug)
        if not proj:
            raise ValueError(f"Unknown project {project_slug}")
        project_folder = proj.root
    
    with open(os.path.join(project_folder, "project.yaml")) as f:
        config = yaml.safe_load(f)
        
    resolve_config = config.get("resolve", {})
    resolve_name = resolve_config.get("project_name", os.path.basename(project_slug))
    master_timeline_name = resolve_config.get("timeline_name")
    if not master_timeline_name:
        raise ValueError("Missing 'timeline_name' under 'resolve' in project.yaml")
    
    project = resolve_project_exactly(pm, resolve_name)
        
    from library.tools.reel_proposal import proposal_path as _proposal_path
    proposal_path = str(_proposal_path(project_folder))
    moments = read_proposal(proposal_path)

    # Archive the plan so it survives being overwritten by the next
    # selector run.  The archive sits alongside the live file, named
    # with a timestamp so it sorts chronologically and never collides.
    from library.tools.plan_provenance import archive_plan
    archive_plan(proposal_path)
    
    with open(os.path.join(project_folder, "pipeline_output/scratch/timeline_transcript/transcript.json")) as f:
        transcript = json.load(f)
        
    timeline = None
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t.GetName() == master_timeline_name:
            timeline = t
            break
            
    if not timeline:
        raise ValueError(f"Could not find master timeline {master_timeline_name}")
        
    snapshot = snapshot_timeline(timeline, project.GetName())
    master_clips = snapshot.clips
    
    pool = project.GetMediaPool()

    # WHICH moments this call builds, decided before anything is touched.
    # `only` is reel numbers; an approved moment not named by it is left
    # exactly as it is, timeline and all.
    wanted = reel_numbers(only)
    building = [m for m in moments
                if str(getattr(m.approval, "value", m.approval)) == "approved"
                and (wanted is None or int(m.number) in wanted)]
    if wanted is not None:
        missing = wanted - {int(m.number) for m in building}
        if missing:
            raise ReelBuildError(
                f"asked to build reel(s) {sorted(missing)}, and the plan "
                f"{proposal_path} has no APPROVED moment with those "
                f"numbers. Approved: "
                f"{sorted(int(m.number) for m in building)}. A build that "
                f"quietly skipped them would report success having placed "
                f"nothing.")

    # DELETE ONLY WHAT THIS CALL IS ABOUT TO PLACE, and REFUSE rather
    # than delete anything else. See `timelines_to_replace` and
    # `assert_deletion_scope`.
    target_names = {built_name(m, name_suffix) for m in building}
    timelines_to_delete = timelines_to_replace(project, target_names)
    assert_deletion_scope(timelines_to_delete, target_names)

    if timelines_to_delete:
        print(f"Replacing {len(timelines_to_delete)} existing timeline(s): "
              f"{sorted(t.GetName() for t in timelines_to_delete)}",
              flush=True)
        pool.DeleteTimelines(timelines_to_delete)
    else:
        print(f"Deleting nothing: none of "
              f"{sorted(target_names)} exists yet.", flush=True)

    built_reel_names = []
    caption_hashes = {}
    footage_binding_hashes = {}
    for moment in building:
        name = built_name(moment, name_suffix)
        print(f"Building {name}", flush=True)
        built_reel_names.append(name)
        # A repetition this build is LEAVING IN, and why, said where the
        # operator is already looking. Silence here is what let reel 03
        # be rebuilt worse at the open than the timeline it replaced.
        for group in refused_take_groups(moment.timeline_start,
                                         moment.timeline_end, transcript):
            print(f"  repetition kept at {group['start']:.2f}-"
                  f"{group['end']:.2f}s ({group['speaker']}): "
                  f"{group['why_nothing_was_cut']}", flush=True)
        ranges = reel_ranges(moment, transcript)
        # Was: computed by the standalone captioner and then passed as
        # None, so every reel built since #524 carried no subtitles at
        # all while the work was done and discarded.
        subtitle_segments = None if skip_captions else reel_subtitle_segments(
            moment, transcript, ranges, project_folder,
            fps=24000 / 1001, width=1080, height=1920, timeline_name=name)

        # RECORD what was placed. Derived at build time and previously
        # written down nowhere, which is why the verifier could re-derive
        # a different grouping a day later and grade against it.
        #
        # Two hashes, answering different questions:
        # - caption_content_hash: WHAT was said and WHEN in the reel.
        # - footage_binding_hash: WHICH footage the captions were
        #   computed against. A caption that passes the content check
        #   but fails the binding check was placed against footage that
        #   moved - exactly the defect that was invisible before.
        if subtitle_segments:
            entries = getattr(subtitle_segments, "caption_entries", None)
            if entries:
                from library.tools.plan_provenance import caption_content_hash
                caption_hashes[name] = caption_content_hash(entries)
            spine = getattr(subtitle_segments, "spine", None)
            if spine:
                from library.tools.plan_provenance import footage_binding_hash
                try:
                    footage_binding_hashes[name] = footage_binding_hash(spine)
                except ValueError:
                    pass  # No bindings in spine - skip silently

        build_reel_timeline(
            project=project,
            moment=moment,
            master_clips=master_clips,
            subtitle_segments=subtitle_segments,
            fps=24000/1001,
            width=1080,
            height=1920,
            project_folder=project_folder,
            transcript=transcript,
            timeline_name=name,
        )

    # Record which plan we built from, so the verifier can detect
    # if the plan changes before verification runs.
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    from library.tools.plan_provenance import write_provenance
    # MERGES into any existing record: a partial rebuild must not
    # delete the provenance of the reels it did not touch.
    write_provenance(review_dir, proposal_path, built_reel_names,
                     caption_hashes=caption_hashes,
                     footage_binding_hashes=footage_binding_hashes)

    organised = None
    if organise:
        from library.tools.execution.organise_media_pool import (
            organise_project, render_unplaced)
        organised = organise_project(
            project, project_folder, master_timeline_name, apply=True)
        print(f"Filed {len(organised['journal']['moves'])} media-pool "
              f"item(s); undo with "
              f"resolve-organize --revert "
              f"{organised['journal']['journal_path']}", flush=True)
        # A rebuild renders a NEW caption identity for every passage it
        # changed and imports it; the previous generation's pool items
        # stay, and filing them under `Not placed on any timeline` moves
        # them without ever saying how many there now are.  So the build
        # that produced them SAYS so, on the run that produced them.
        # Measured on the field test: 1,216 such items, 5.40 GiB, and no
        # rebuild had ever mentioned one of them.
        print(render_unplaced(organised["unplaced"]), flush=True)

    if verify:
        verify_built_reels(
            project_folder=project_folder,
            resolve_project_name=resolve_name,
            master_timeline_name=master_timeline_name,
            plan_path=proposal_path,
            transcript_path=os.path.join(project_folder, "pipeline_output/scratch/timeline_transcript/transcript.json")
        )

    return {
        "timelines_built": built_reel_names,
        "caption_hashes": caption_hashes,
        "plan_path": proposal_path,
        "resolve_project_name": resolve_name,
        "master_timeline_name": master_timeline_name,
        "captions_rendered": not skip_captions,
        "verified_in_place": bool(verify),
        # What this build was ASKED for, so the record can say that it
        # built one reel into a new container rather than reading like a
        # full rebuild that placed one timeline.
        "reels_requested": None if wanted is None else sorted(wanted),
        "name_suffix": name_suffix,
        # Where the media pool was filed, and the journal that undoes it.
        # None when the caller declined; never a silent empty record.
        "organised": organised,
    }



def verify_built_reels(project_folder: str, resolve_project_name: str, master_timeline_name: str, plan_path: str, transcript_path: str) -> None:
    """Run the reel conformance verifier as a quality gate after building reels.
    
    If the verifier finds ANY errors, this raises a RuntimeError with the findings,
    failing the build. The raw JSON and human-readable table are preserved in 
    the project's pipeline_output/review directory.
    """
    import os
    import json
    
    out_dir = os.path.join(project_folder, "pipeline_output", "review")
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "conformance_report.json")
    
    try:
        from library.tools.reel_conformance_verifier import run_verification
    except ImportError as e:
        raise RuntimeError(f"Reel conformance verifier is unavailable: {e}")
        
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            transcript = json.load(f)
            
        exit_code = run_verification(
            project_name=resolve_project_name,
            master_name=master_timeline_name,
            plan_path=plan_path,
            transcript=transcript,
            json_path=json_path
        )
    except Exception as e:
        raise RuntimeError(f"Reel conformance verifier failed to run: {e}")
        
    if exit_code == 1:
        findings_msg = "See conformance_report.json for details."
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    report = json.load(f)
                errors = [f for f in report.get("findings", []) if f.get("severity") == "error"]
                if errors:
                    findings_msg = json.dumps(errors, indent=2)
            except Exception:
                pass
        raise RuntimeError(f"Reel build produced a defective timeline. Verification failed with findings:\n{findings_msg}")
    elif exit_code == 2:
        raise RuntimeError("Reel conformance verifier encountered a fatal error (e.g. timeline changed during verification).")
