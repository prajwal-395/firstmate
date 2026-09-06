"""The captain's four qualities, each held the way its KIND allows.

The captain's words, 2026-09-06, verbatim:

    "given all the raw footage, the goal is to create as many good reels
    as possible -- ones that have some kind of call to action, that
    provide value, are coherent in the conversation and what is being
    said, and are around the time frame we want the reels to"

    "i want to know that the quality of videos you produce before any
    visual effects and stuff get implemented are good enough and those
    qualities are generalized into the pipeline"

Four qualities.  Two of them are EXACT and two of them are JUDGEMENT, and
the whole design of this module is that those two kinds are held
differently rather than averaged into one number.

What was there before, and what was missing
-------------------------------------------
`reel_conformance_verifier` had nineteen checks and every one was
mechanical: format, item count, picture holes, audio holes, caption
timing, caption overlap, styling, plan-matches-timeline.  Not one asked
whether the reel was any good.  `reel_exchange` computed
`within_length_guidance` and only REPORTED it.  So a reel could pass
every check the pipeline had and still be worthless, and on 2026-09-05
nineteen of them did.

Measured on the field test's own approved plan (25 moments, 2026-09-06)
before a line of this was written:

- eight ran outside the captain's own 45-90s guidance - four of them
  under 45 and one at 108.1s - and nothing failed;
- seven named no closer at all;
- one 4.4-second closer closed seven reels and another closed five, and
  nothing counted them.

Every one of those is exact.  None of them was being held.

The two EXACT qualities
-----------------------
**DURATION.**  `reel_exchange.LENGTH_GUIDANCE` is the captain's brief -
45 to 90 seconds - and it is imported from there rather than restated,
because a guidance spelled twice is this repository's dominant bug class
(AGENTS.md 10.1).  The SELECTOR still weighs it: a story that needs 95
seconds to finish is a real answer and `exchange_windows` deliberately
stopped truncating at 90.  The BAR reports it.  Those are two different
moments and the difference is the point - guidance you weigh while
choosing becomes a finding once the choice is made, which is what "make
it BITE at the point a reel is judged" means.  Nothing here shortens,
drops or rewrites a reel; a reel outside the band is REPORTED outside the
band and the captain still approves it or does not.

**A CALL TO ACTION.**  Three things, all exact:

1. *One exists.*  Not "the moment declared a `cta` field" - a reel whose
   body already ends on a spoken invitation needs no declared closer and
   was never a defect.  What is checked is whether the seconds the reel
   PLAYS LAST are a call to action, and the evidence for what counts as
   one is the batch's own: `declared_closers` collects every span any
   moment in the batch named as its closer, and a reel whose tail plays
   one of them ends on one.  The alternative was a word list, which is
   the defect `sfx_library` exists to have removed (AGENTS.md 10.5).
   A batch that declares no closer anywhere establishes nothing, and
   every reel in it reads ABSENT - which is the correct answer, not a
   failure of the instrument.
2. *It is inside what the reel plays.*  `reel_build.reel_ranges` lays the
   closer down last, so this is structural - but a plan is a FILE THE
   CAPTAIN EDITS, `read_proposal` does not re-run validation, and the
   proposal file says so in its own instruction line.  So the checks
   `validate_proposal` ran when the proposal was WRITTEN are run again
   here against what will actually be played.
3. *Whose it is.*  How many reels close on the same seconds is counted
   and named.  It is REPORTED and does not fail, and that is not
   softness: `reel_proposal`'s own docstring rules that the same CTA may
   close any number of reels, and six spoken closers covering sixteen
   reels is the mechanism working.  A gate that FAILS correct output is
   no more coverage than one that cannot fail (AGENTS.md 10.4).  What was
   missing was never a rule - it was the COUNT.  Nothing said "this
   4.4-second sentence is the ending of seven of your nineteen reels",
   and a reader who cannot see that cannot rule on it.

The two JUDGEMENT qualities, and why they are not asked for
-----------------------------------------------------------
**COHERENCE** - "a stranger who has never heard the episode can follow
it" - and **VALUE** - "does this hand a viewer something they can use" -
cannot be computed.  They go to a model.

The hard part is that a recorded judgement must be worth something.  A
model asked "is this good?" that answers "yes" has told you nothing, and
on 2026-09-05 a model was handed the criteria it would be graded on and
duly graded itself well.  Two mechanisms stop that here, and both are
structural rather than a matter of prompt discipline:

**1. The judge is never asked for a verdict.  It is asked for a
READING.**  What does this reel claim, quoting it; what could a listener
repeat or act on afterwards, quoting it; how does it open and how does it
end, quoted; what does it refer to that a listener could not know from
the reel itself, quoting where; where does it stop adding anything.  Not
one of those questions has a good answer and a bad answer.  The ENGINE
derives the verdicts from the reading afterwards
(`coherence_of`, `value_of`), and the mapping is not in the prompt.  A
judge that does not know which way an answer counts cannot flatter
itself.

**2. Every reading is CHECKED against the reel's own words.**  Each
observation carries a QUOTE, and a quote either appears in what the reel
plays or it does not - there is no fraction, no similarity and no
threshold.  A reading whose quotes are not in the reel is REFUSED and
recorded as refused; it is never stored as a verdict.  That is the whole
of "a judgement that cannot be checked against the transcript is not
evidence, and you should say so rather than storing it", implemented:
`check_reading` returns the reasons and `judge` keeps them.

`FORBIDDEN_IN_THE_ASK` is the third leg and it is mechanical: the words
that would hand the judge the criteria - coherent, value, quality, good,
pass, fail, atomic, hook, call to action, 45, 90 - may not appear in what
the judge is sent, and `assert_ask_is_uncontaminated` raises if one does.
`tests/test_reel_quality_bar.py` runs it over the real handoff, so the
guarantee does not depend on anyone remembering it.

The judge is also given the reel's WORDS AND NOTHING ELSE.  Not its
slug, not the `reason` the selector wrote for it, not its `hook` or
`close` or `value` fields, not its measurements, not its findings.  The
selector's handoff states the criteria in full and asks the selector to
argue for its own choices; a judge that read that argument would be
grading the argument.

The ranking
-----------
"As many good reels as possible" needs an ordering, and an ordering is
the one form of judgement this repository already knows how to take:
**it is an ORDERING and there is NO SCORE** (`passage_engagement`, and
the captain's ruling of 2026-09-02 behind it).  The judge places the
batch in one ordering with a one-sentence basis each; a reel it declines
to place reads UNJUDGED and is never coerced to last or to zero.
`ranked` is the reader, and `compare ranks only near the top` applies
here for the same reason it applies there.

Nothing here drops a reel.  The bar REPORTS - per reel, with its
findings, its derived verdicts and its place in the ordering - and the
caller decides.  A funnel that discards without saying why is the defect
`reel_exchange` was rebuilt to remove, and it would be the same defect
here.

Reachability
------------
    python3 -m library.tools.reel_quality_bar --project <project_folder>
    python3 -m library.tools.reel_quality_bar --project <p> --json

`tests/test_reel_quality_bar.py`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# ONE spelling of the captain's length brief, owned by the module whose
# docstring records where it came from and why it stopped being a
# boundary.  Restating the numbers here would be the second enumeration
# AGENTS.md 10.1 is about.
from library.tools.reel_exchange import LENGTH_GUIDANCE

__all__ = [
    "EXACT",
    "FORBIDDEN_IN_THE_ASK",
    "JUDGEMENT",
    "QUALITIES",
    "READING_FIELDS",
    "READING_SCHEMA",
    "BarFinding",
    "BarReport",
    "Quality",
    "Reading",
    "ReelVerdict",
    "assert_ask_is_uncontaminated",
    "check_reading",
    "coherence_of",
    "cta_reading",
    "declared_closers",
    "duration_reading",
    "exact_findings",
    "format_table",
    "judge",
    "playable_ranges",
    "played_speech",
    "ranked",
    "read_one",
    "reel_text",
    "value_of",
]


# What the bar said the first time it was run over real work.  Recorded
# for the reason `passage_engagement.MEASURED_SPREAD` is recorded: a
# design argument that has never met the material is a design argument,
# and the honest half of a measurement is the column that did not move.
FIRST_MEASUREMENT = {
    "project": "lucie/geo-podcast (field test)",
    "plan": "reel_proposals_v2.json, 25 moments, all APPROVED",
    "when": "2026-09-06",
    "verdict": {"pass": 7, "fail": 18},
    # Three of the four discriminated.  Each is reported with both sides
    # because a check that only ever fires, or only ever stays quiet, is
    # not measuring (AGENTS.md 10.4).
    "duration": {"within_guidance": 16, "outside": 9,
                 "range_seconds": [23.3, 108.1]},
    "call_to_action": {"declared": 18, "in_body": 5, "absent": 2,
                       "most_reused_closer_closes": 8},
    "coherence": {"followable": 11, "not_followable": 14},
    # And one did NOT.  Said plainly rather than left for a reader to
    # notice: every one of the 25 yielded a quotable takeaway, so the
    # `value` column was constant and carried no information about this
    # batch.  ONE batch cannot separate the two explanations - an
    # interview about a subject may simply always contain an assertion
    # worth repeating, or the question may be too easy to answer
    # positively - and nothing here should be changed on a sample of one.
    # What would settle it is a batch containing a stretch of pure
    # agreement or banter; if `value` still reads `delivers` on that, the
    # question is wrong.
    "value": {"delivers": 25, "delivers_nothing": 0,
              "reading": "did not discriminate on this batch; see above"},
    # The instrument checking itself.  The reader was never told a closer
    # was wanted and independently found one on reels 05 and 09, which
    # the exact half reads as ABSENT because no moment in the batch names
    # those seconds.  Both are right about what they can see, and
    # QB-CTA-DISAGREEMENT is where that is said.
    "disagreements": 2,
    "grounding": {
        "readings_supplied": 25, "refused": 0,
        "note": ("the checker was separately shown to refuse three "
                 "deliberately falsified quotes on this same material, "
                 "and to report a real quote taken from the middle as "
                 "MISPLACED rather than refusing it"),
    },
}


# ── The four qualities, as data ──────────────────────────────────────

EXACT = "exact"
"""Measurable from the material.  Measure it, and a disagreement is a
finding."""

JUDGEMENT = "judgement"
"""Not computable.  Route it to the model, take a READING rather than a
verdict, check the reading against the transcript, and derive the verdict
here."""


@dataclass(frozen=True)
class Quality:
    """One of the captain's four, and how it is held."""

    name: str
    kind: str
    asked: str
    """The captain's own words for what this quality is."""
    held_by: Tuple[str, ...]
    """For an EXACT quality, the functions in this module that measure
    it.  For a JUDGEMENT one, the fields of the reading it is derived
    from - never a question put to the model in these terms."""


QUALITIES: Tuple[Quality, ...] = (
    Quality(
        name="duration",
        kind=EXACT,
        asked="are around the time frame we want the reels to",
        held_by=("duration_reading",),
    ),
    Quality(
        name="call_to_action",
        kind=EXACT,
        asked="have some kind of call to action",
        held_by=("declared_closers", "cta_reading"),
    ),
    Quality(
        name="coherence",
        kind=JUDGEMENT,
        asked="are coherent in the conversation and what is being said",
        held_by=("assumes_known",),
    ),
    Quality(
        name="value",
        kind=JUDGEMENT,
        asked="that provide value",
        held_by=("takeaway_quote",),
    ),
)


def assert_qualities_are_well_formed() -> None:
    """Every quality names something that exists in this module.

    A quality whose measurement is a name nobody implemented is the
    prose prerequisite in a costume (AGENTS.md 3), and a JUDGEMENT
    quality derived from a reading field that is not in the contract
    would silently read as UNJUDGED for ever.
    """
    reading_names = {f.name for f in READING_FIELDS}
    for quality in QUALITIES:
        if quality.kind not in (EXACT, JUDGEMENT):
            raise RuntimeError(
                f"quality {quality.name!r} has kind {quality.kind!r}, which "
                f"is neither {EXACT!r} nor {JUDGEMENT!r}. There is no third "
                f"kind: a property is measured or it is judged.")
        if not quality.held_by:
            raise RuntimeError(
                f"quality {quality.name!r} names nothing that holds it. A "
                f"quality with no reader is a declaration, not a check.")
        for held in quality.held_by:
            if quality.kind == EXACT:
                if held not in globals():
                    raise RuntimeError(
                        f"quality {quality.name!r} is EXACT and names "
                        f"{held!r}, which this module does not define.")
            elif held not in reading_names:
                raise RuntimeError(
                    f"quality {quality.name!r} is JUDGEMENT and is derived "
                    f"from reading field {held!r}, which READING_FIELDS "
                    f"does not contain.")


# ── Findings ─────────────────────────────────────────────────────────

QB_DURATION = "QB-DURATION"
QB_UNBUILDABLE = "QB-UNBUILDABLE"
QB_CTA_ABSENT = "QB-CTA-ABSENT"
QB_CTA_IN_BODY = "QB-CTA-IN-BODY"
QB_CTA_SILENT = "QB-CTA-SILENT"
QB_CTA_OUTSIDE = "QB-CTA-OUTSIDE"
QB_CTA_FRAGMENT = "QB-CTA-FRAGMENT"
QB_CTA_SHARED = "QB-CTA-SHARED"
QB_CTA_NOT_LAST = "QB-CTA-NOT-LAST"
QB_CTA_DISAGREEMENT = "QB-CTA-DISAGREEMENT"
QB_NOT_FOLLOWABLE = "QB-NOT-FOLLOWABLE"
QB_NO_TAKEAWAY = "QB-NO-TAKEAWAY"
QB_UNGROUNDED = "QB-UNGROUNDED"
QB_OPENING_MISPLACED = "QB-OPENING-MISPLACED"
QB_CLOSING_MISPLACED = "QB-CLOSING-MISPLACED"

ERROR = "error"
WARNING = "warning"

FINDING_OWNERS: Dict[str, str] = {
    # Which quality each finding is about, so a report can be read by
    # quality rather than by code.
    QB_DURATION: "duration",
    QB_UNBUILDABLE: "duration",
    QB_CTA_ABSENT: "call_to_action",
    QB_CTA_IN_BODY: "call_to_action",
    QB_CTA_SILENT: "call_to_action",
    QB_CTA_OUTSIDE: "call_to_action",
    QB_CTA_FRAGMENT: "call_to_action",
    QB_CTA_SHARED: "call_to_action",
    QB_CTA_NOT_LAST: "call_to_action",
    QB_CTA_DISAGREEMENT: "call_to_action",
    QB_NOT_FOLLOWABLE: "coherence",
    QB_NO_TAKEAWAY: "value",
    QB_UNGROUNDED: "judgement",
    QB_OPENING_MISPLACED: "judgement",
    QB_CLOSING_MISPLACED: "judgement",
}


@dataclass(frozen=True)
class BarFinding:
    """One thing the bar has to say about one reel."""

    code: str
    reel: str
    message: str
    severity: str = ERROR
    detail: Optional[dict] = None

    @property
    def quality(self) -> str:
        return FINDING_OWNERS.get(self.code, "unknown")

    def as_dict(self) -> dict:
        out = {"code": self.code, "quality": self.quality, "reel": self.reel,
               "message": self.message, "severity": self.severity}
        if self.detail:
            out["detail"] = self.detail
        return out


# ── What the reel actually says ──────────────────────────────────────

def _words(text) -> List[str]:
    return re.findall(r"[a-z0-9']+", str(text).lower())


def normalise(text) -> str:
    """A quote and a transcript compared on the same footing.

    Case, punctuation and whitespace are removed because a model
    re-typing a sentence is not obliged to reproduce WhisperX's commas.
    Nothing else is: no stemming, no stopword removal and no similarity.
    A quote is present or it is not.
    """
    return " ".join(_words(text))


def playable_ranges(moment, transcript: dict) -> Tuple[list, str]:
    """`(ranges, refusal)` - what the reel plays, or why it plays nothing.

    `reel_build.reel_ranges` RAISES for a plan that cannot be laid out:
    a closer under a frame, or one overlapping its own body so the reel
    would play those seconds twice.  Both are real defects and both are
    things the bar exists to report - so the exception is turned into a
    refusal here rather than being allowed to take the whole batch's
    report with it.  A reel that cannot be built has not passed anything.
    """
    from library.tools.reel_build import ReelBuildError, reel_ranges

    try:
        return list(reel_ranges(moment, transcript)), ""
    except ReelBuildError as refused:
        return [], str(refused)
    except (KeyError, TypeError, ValueError) as broken:
        return [], f"{type(broken).__name__}: {broken}"


def played_speech(moment, transcript: dict) -> List[dict]:
    """Every line a viewer HEARS, in the order the reel plays it.

    Built over `reel_build.reel_ranges`, so it is the body with its bad
    takes cut out and then the closer - the one place that order is
    spelled, and the same list the builder, the caption pass and the
    conformance verifier all read.  A reel's own words therefore cannot
    disagree with what was placed.

    STRADDLING segments are included, and that is deliberate and is the
    opposite of what `bound_segments` is for.  A boundary may never be
    placed on one, because it has no single source clip - but the reel
    PLAYS it, `placements` copies the master's clips over the keep ranges
    and knows nothing about segments, and a judge shown only the bound
    rows would be reading a reel that does not exist.  `bound` says which
    is which for a reader who needs to know.
    """
    segments = sorted((transcript.get("segments") or []),
                      key=lambda s: float(s.get("timeline_start") or 0.0))
    out: List[dict] = []
    offset = 0.0
    for range_start, range_end in playable_ranges(moment, transcript)[0]:
        for segment in segments:
            start = float(segment.get("timeline_start") or 0.0)
            end = float(segment.get("timeline_end") or 0.0)
            if end <= range_start or start >= range_end:
                continue
            text = (segment.get("text") or "").strip()
            if not text:
                continue
            out.append({
                "speaker": segment.get("speaker"),
                "reel_start": round(offset + max(start, range_start)
                                    - range_start, 2),
                "reel_end": round(offset + min(end, range_end)
                                  - range_start, 2),
                "text": text,
                "bound": bool(segment.get("resolve_item_id")),
            })
        offset += range_end - range_start
    return out


def reel_text(moment, transcript: dict) -> str:
    """Everything the reel says, joined, in play order."""
    return " ".join(line["text"] for line in played_speech(moment, transcript))


def delivered_seconds(moment, transcript: dict) -> float:
    """How long the reel RUNS - body minus bad takes, plus the closer.

    Not `duration` (the body window) and not `total_duration` (body plus
    closer, before anything is cut).  What a viewer sits through is the
    sum of the ranges that are placed, and the three numbers are
    genuinely different: a moment carrying a whole removed take is
    shorter than either.

    0.0 for a reel that cannot be laid out at all - `playable_ranges`
    says why, and `exact_findings` reports it.
    """
    return sum(end - start
               for start, end in playable_ranges(moment, transcript)[0])


# ── EXACT: duration ──────────────────────────────────────────────────

def duration_reading(moment, transcript: dict) -> dict:
    """How long the reel runs, and which side of the guidance it falls."""
    ranges, refusal = playable_ranges(moment, transcript)
    seconds = sum(end - start for start, end in ranges)
    low, high = LENGTH_GUIDANCE
    if refusal:
        # A reel nothing can lay out has no length to hold against the
        # guidance, and reporting 0.0s as "under 45" would be a finding
        # about the instrument. The refusal is the finding.
        return {"delivered_seconds": 0.0, "unbuildable": refusal,
                "guidance_seconds": [low, high], "within_guidance": True,
                "outside_by_seconds": 0.0}
    return {
        "delivered_seconds": round(seconds, 1),
        "body_seconds": round(moment.timeline_end - moment.timeline_start, 1),
        "closer_seconds": round(moment.call_to_action.duration, 1)
        if moment.call_to_action else 0.0,
        "removed_by_cuts_seconds": round(
            (moment.timeline_end - moment.timeline_start)
            + (moment.call_to_action.duration if moment.call_to_action else 0.0)
            - seconds, 1),
        "guidance_seconds": [low, high],
        "within_guidance": low <= seconds <= high,
        "outside_by_seconds": round(
            low - seconds if seconds < low
            else seconds - high if seconds > high else 0.0, 1),
    }


# ── EXACT: the call to action ────────────────────────────────────────

def _frame_tolerance(transcript: dict) -> float:
    """One frame, from the transcript's own rate.

    MECHANICAL, not editorial (AGENTS.md 10.5): it exists because a
    boundary snapped to speech lands within a frame of a closer's edge
    and "the same second" has to mean something at frame resolution.  It
    says nothing about how long or how good anything is.
    """
    fps = float((transcript.get("derived_from") or {}).get("fps") or 0.0)
    return 1.0 / fps if fps > 0 else 1001.0 / 24000.0


def declared_closers(moments: Sequence) -> Dict[Tuple[float, float], List[int]]:
    """Every span the batch itself calls a call to action, and who names it.

    This is the whole evidence base for "does this reel end on a call to
    action", and it is deliberately the batch's own answer rather than a
    list of words.  A word list would be the defect `sfx_library` exists
    to have removed: it decides for the model what a call to action
    sounds like, on one episode's vocabulary, and then reads its own
    decision back as a measurement.

    A batch that declares no closer establishes nothing, and every reel
    in it reads ABSENT.  That is the honest answer - the first field-test
    batch of ten named no closer anywhere - and it is a finding rather
    than a silence.
    """
    out: Dict[Tuple[float, float], List[int]] = {}
    for moment in moments:
        closer = getattr(moment, "call_to_action", None)
        if closer is None:
            continue
        key = (round(float(closer.timeline_start), 2),
               round(float(closer.timeline_end), 2))
        out.setdefault(key, []).append(int(moment.number))
    return out


def cta_reading(moment, transcript: dict,
                closers: Dict[Tuple[float, float], List[int]]) -> dict:
    """What closes this reel, where it came from, and who else uses it.

    Three sources, and the difference between the first two is the one
    that stops this check firing on correct output:

    - `declared`  - the moment names a closer, played after its body.
    - `in_body`   - it names none, and does not need one: the seconds it
      plays already CONTAIN a span some moment in this batch calls a
      closer.  Five of the field test's twenty-five are this, and
      treating them as missing a call to action would have been five
      findings about correct output.
    - `absent`    - it names none and plays none.

    The captain asked for reels "that have some kind of call to action",
    so the test is CONTAINMENT and not "ends on".  Whether the closer is
    the last thing played is a second, separate fact - `is_the_ending` -
    because the format the selector's handoff describes does end on one,
    and a reel that says its invitation and then talks for another
    second is a real observation rather than a missing CTA.
    """
    from library.tools.reel_build import cta_range

    tolerance = _frame_tolerance(transcript)
    duration = float((transcript.get("derived_from") or {})
                     .get("duration_seconds") or 0.0)
    declared = cta_range(moment)
    reading: dict = {
        "source": "absent",
        "span": None,
        "text": "",
        "speaker": None,
        "shared_with": [],
        "closes_reels": 0,
        "in_own_body": False,
        "outside_episode": False,
        "silent": False,
        "incomplete": False,
        "unwritten_words": [],
        "is_the_ending": False,
    }

    if declared is not None:
        key = (round(declared[0], 2), round(declared[1], 2))
        others = [n for n in closers.get(key, ()) if n != int(moment.number)]
        closer = moment.call_to_action
        reading.update({
            "source": "declared",
            "span": [round(declared[0], 2), round(declared[1], 2)],
            "text": closer.text or "",
            "speaker": closer.speaker,
            "shared_with": sorted(others),
            "closes_reels": len(closers.get(key, ())),
            "in_own_body": (min(declared[1], moment.timeline_end)
                            > max(declared[0], moment.timeline_start)),
            "outside_episode": bool(
                declared[0] < 0
                or (duration and declared[1] > duration + 0.001)),
            "silent": not (closer.text or "").strip(),
            # A closer whose completing words straddle a cut is not the
            # complete, self-contained invitation the format asks for -
            # `CallToAction.straddling_within` is measured for exactly
            # this and nothing read it.
            "incomplete": bool(closer.straddling_within),
            "unwritten_words": [dict(x) for x in closer.straddling_within],
            # `reel_ranges` lays a declared closer down LAST, always, and
            # that is the one place the order is spelled - so a declared
            # closer that is not the ending would mean the builder and
            # this reader disagree, which is not a thing to assert here.
            "is_the_ending": True,
        })
        return reading

    # No declared closer.  Does the reel play one anyway, inside the
    # seconds it already covers?  Tested against the ranges that are
    # PLACED rather than against the body window, because a bad-take cut
    # can remove the very seconds a closer would have sat in.
    ranges = playable_ranges(moment, transcript)[0]
    if not ranges:
        return reading
    played_to = ranges[-1][1]
    contained = [
        ((start, end), numbers) for (start, end), numbers in closers.items()
        if any(range_start - tolerance <= start
               and end <= range_end + tolerance
               for range_start, range_end in ranges)]
    if not contained:
        return reading
    # THE LAST one, not the first.  A reel can contain several - reel 03
    # of the field test plays one closer at 321.6 and ends on a different
    # one at 341.3 - and picking the earliest reported it as "carries a
    # call to action but does not end on it" when it does. Sorting by end
    # makes `is_the_ending` a fact about the reel rather than about which
    # closer the iteration happened to reach first.
    (start, end), numbers = max(contained, key=lambda item: item[0][1])
    reading.update({
        "source": "in_body",
        "span": [round(start, 2), round(end, 2)],
        "shared_with": sorted(int(n) for n in numbers),
        "closes_reels": len(numbers),
        "is_the_ending": abs(end - played_to) <= tolerance,
    })
    return reading


def exact_findings(moment, transcript: dict,
                   closers: Dict[Tuple[float, float], List[int]],
                   ) -> List[BarFinding]:
    """Everything the two EXACT qualities have to say about one reel."""
    name = moment.timeline_name
    out: List[BarFinding] = []

    # The CALL TO ACTION first, and deliberately so.  A closer that
    # overlaps its own body is BOTH a call-to-action defect and the
    # reason `reel_ranges` refuses to lay the reel out; naming it as the
    # first is what stops the second swallowing it.  Its declared half
    # needs no ranges, so it survives a plan nothing can build.
    cta = cta_reading(moment, transcript, closers)
    out.extend(_cta_findings(moment, name, cta, closers))

    duration = duration_reading(moment, transcript)
    if duration.get("unbuildable"):
        # Nothing else can be read off a plan nothing can lay out. Say so
        # only where the CTA half has not already named the cause -
        # otherwise one defect is reported twice under two codes.
        if not any(f.severity == ERROR for f in out):
            out.append(BarFinding(
                code=QB_UNBUILDABLE, reel=name, severity=ERROR,
                message=(f"cannot be laid out at all, so nothing about it "
                         f"can be measured: {duration['unbuildable']}"),
                detail=duration))
        return out
    if not duration["within_guidance"]:
        low, high = LENGTH_GUIDANCE
        side = "under" if duration["delivered_seconds"] < low else "over"
        out.append(BarFinding(
            code=QB_DURATION, reel=name, severity=ERROR,
            message=(
                f"runs {duration['delivered_seconds']:.1f}s, "
                f"{duration['outside_by_seconds']:.1f}s {side} the "
                f"{low:.0f}-{high:.0f}s the brief asks for"),
            detail=duration))

    return out


def _cta_findings(moment, name: str, cta: dict,
                  closers: Dict[Tuple[float, float], List[int]],
                  ) -> List[BarFinding]:
    """Everything the CALL TO ACTION quality has to say about one reel."""
    out: List[BarFinding] = []
    if cta["source"] == "absent":
        out.append(BarFinding(
            code=QB_CTA_ABSENT, reel=name, severity=ERROR,
            message=(
                "ends on nothing this batch calls a call to action: it "
                "names no closer, and the seconds it plays last are not a "
                "span any other moment closes on"
                + (". No moment in this batch names a closer at all, so "
                   "nothing establishes what one sounds like here"
                   if not closers else "")),
            detail=cta))
    if cta["in_own_body"]:
        out.append(BarFinding(
            code=QB_CTA_IN_BODY, reel=name, severity=ERROR,
            message=(
                f"its closer {cta['span']} sits inside its own body "
                f"({moment.timeline_start:.1f}-{moment.timeline_end:.1f}s), "
                f"so the reel plays those seconds twice"),
            detail=cta))
    if cta["outside_episode"]:
        out.append(BarFinding(
            code=QB_CTA_OUTSIDE, reel=name, severity=ERROR,
            message=(f"its closer {cta['span']} falls outside the episode, "
                     f"so there is nothing to place from it"),
            detail=cta))
    if cta["silent"]:
        out.append(BarFinding(
            code=QB_CTA_SILENT, reel=name, severity=ERROR,
            message=(f"its closer {cta['span']} has no measured speech in "
                     f"it, so the reel ends on silence"),
            detail=cta))
    if cta["incomplete"]:
        out.append(BarFinding(
            code=QB_CTA_FRAGMENT, reel=name, severity=WARNING,
            message=(
                f"its closer plays {len(cta['unwritten_words'])} passage(s) "
                f"of speech its own text does not contain, so what a viewer "
                f"hears is not the complete invitation the text reads like"),
            detail=cta))
    if cta["source"] != "absent" and not cta["is_the_ending"]:
        out.append(BarFinding(
            code=QB_CTA_NOT_LAST, reel=name, severity=WARNING,
            message=(
                f"carries a call to action at {cta['span']} but keeps "
                f"playing after it, so the reel does not END on the "
                f"invitation the format closes on"),
            detail=cta))
    if cta["closes_reels"] > 1:
        out.append(BarFinding(
            code=QB_CTA_SHARED, reel=name, severity=WARNING,
            message=(
                f"plays the same call to action {cta['span']} as "
                f"{len(cta['shared_with'])} other reel(s) in this batch "
                f"({', '.join(str(n) for n in cta['shared_with'])}) - "
                f"reuse is permitted and how much of it reads as "
                f"repetition is the captain's call, but nothing counted "
                f"it before"),
            detail=cta))
    return out


# ── JUDGEMENT: what the model is asked for ───────────────────────────

@dataclass(frozen=True)
class ReadingField:
    """One thing the judge writes down, and how it is checked."""

    name: str
    asks: str
    """The question, in the terms the judge is asked it. Criteria-free."""
    grounding: str
    """`contains` - the quote must appear in the reel's own words.
    `prefix` / `suffix` - it must additionally be where it is claimed to
    be. `none` - free text nothing can check, and therefore nothing the
    engine derives a verdict from."""
    required: bool = True
    """A required field missing REFUSES the reading. `takeaway_quote` is
    NOT required, because "this reel delivers nothing a listener could
    use" is a real answer and the one this whole design has to be able to
    receive."""


READING_FIELDS: Tuple[ReadingField, ...] = (
    ReadingField(
        name="claim_quote", grounding="contains",
        asks="the words in which the reel says the one thing it is saying"),
    ReadingField(
        name="claim", grounding="none",
        asks="what that is, in one sentence of your own"),
    ReadingField(
        name="opening_quote", grounding="prefix",
        asks="the first words a listener hears"),
    ReadingField(
        name="closing_quote", grounding="suffix",
        asks="the last words a listener hears"),
    ReadingField(
        name="closing_asks_for", grounding="none",
        asks="what, if anything, those last words ask the listener to do; "
             "empty if they ask for nothing"),
    ReadingField(
        name="takeaway_quote", grounding="contains", required=False,
        asks="the words a listener could repeat or act on afterwards; "
             "empty if the reel has none"),
    ReadingField(
        name="takeaway", grounding="none", required=False,
        asks="what that is, in one sentence of your own"),
    ReadingField(
        name="assumes_known", grounding="contains",
        asks="everything the reel refers to that a listener could not know "
             "from the reel itself, each with the words where it does so"),
    ReadingField(
        name="stops_developing_at", grounding="none",
        asks="the second, counted from the reel's own start, after which "
             "nothing further is added"),
    ReadingField(
        name="rank", grounding="none",
        asks="this reel's place in one ordering over the batch, strongest "
             "first"),
    ReadingField(
        name="basis", grounding="none",
        asks="one sentence on what puts it there"),
)

READING_SCHEMA: dict = {
    "readings": [
        {
            "reel": "int - the reel number this reading is of",
            "claim_quote": "string",
            "claim": "string",
            "opening_quote": "string",
            "closing_quote": "string",
            "closing_asks_for": "string",
            "takeaway_quote": "string",
            "takeaway": "string",
            "assumes_known": [{"what": "string", "quote": "string"}],
            "stops_developing_at": "number - seconds from the reel's start",
            "rank": "int - 1 is strongest",
            "basis": "string",
        }
    ]
}

FORBIDDEN_IN_THE_ASK: Tuple[str, ...] = (
    r"coheren\w*", r"incoheren\w*",
    r"values?", r"valuable", r"worthless",
    r"qualit(y|ies)",
    r"good enough", r"how good", r"bad reels?",
    r"pass", r"fails?", r"failing", r"verdicts?",
    r"criteri\w+", r"scores?", r"scoring", r"grades?", r"rating",
    r"thresholds?",
    r"calls? to action", r"ctas?",
    r"atomic", r"hooks?",
    r"45", r"90", r"length guidance",
)
"""Word patterns that would hand the judge the answer sheet.

On 2026-09-05 a model was told the criteria it would be graded on and
graded itself well; the report called that contamination and was right.
So the contamination is made mechanical rather than a matter of anyone
remembering: `assert_ask_is_uncontaminated` runs over the WHOLE assembled
prompt the judge is really sent - handoff, craft role, schema and every
appended block - and `tests/test_reel_quality_bar.py` runs it over the
files on disk.

Three are worth naming.  **"call to action"** is forbidden because the
judge is asked what the closing words ASK THE LISTENER TO DO, which is
the observation; being told that a call to action is wanted turns the
observation into a target.  **"45" and "90"** because a judge told the
band writes readings that argue for it.  And **"pass"** is matched at a
word boundary rather than as a substring precisely so that "passage" -
an ordinary word for a stretch of speech - is not caught: a guard that
fires on correct wording gets disabled.

Each entry is a regex fragment matched between `\\b` boundaries, case
insensitively.  A near miss is cheap to reword; a false negative is what
this exists to stop.
"""

_FORBIDDEN = tuple(
    (pattern, re.compile(r"\b(?:" + pattern + r")\b", re.IGNORECASE))
    for pattern in FORBIDDEN_IN_THE_ASK)


def assert_ask_is_uncontaminated(text: str, where: str = "the ask") -> None:
    """Raise if what the judge is sent names any of the criteria."""
    found = sorted({
        matcher.search(str(text)).group(0)
        for pattern, matcher in _FORBIDDEN if matcher.search(str(text))})
    if found:
        raise RuntimeError(
            f"{where} contains {', '.join(repr(f) for f in found)}. The "
            f"judge is asked for a READING of the reel and never for a "
            f"verdict on it: a judge that knows which way an answer counts "
            f"grades itself. See reel_quality_bar.FORBIDDEN_IN_THE_ASK.")


# ── JUDGEMENT: checking a reading against the reel ───────────────────

FOLLOWABLE = "followable"
NOT_FOLLOWABLE = "not_followable"
DELIVERS = "delivers"
DELIVERS_NOTHING = "delivers_nothing"
UNJUDGED = "unjudged"
"""Never a low mark.  A reel nobody read reads UNJUDGED and says so -
the same rule `passage_engagement.engagement_rank` holds, and for the
same reason: a missing measurement that resolves to a value reads as a
real one."""


@dataclass(frozen=True)
class Reading:
    """One judge's reading of one reel, after it has been checked."""

    reel: int
    claim_quote: str = ""
    claim: str = ""
    opening_quote: str = ""
    closing_quote: str = ""
    closing_asks_for: str = ""
    takeaway_quote: str = ""
    takeaway: str = ""
    assumes_known: Tuple[dict, ...] = ()
    stops_developing_at: Optional[float] = None
    rank: Optional[int] = None
    basis: str = ""

    ungrounded: Tuple[str, ...] = ()
    """Why this reading could not be checked against the reel. Non-empty
    means REFUSED: the verdicts are not derived from it and it is
    reported as evidence that failed, never stored as a judgement."""

    misplaced: Tuple[str, ...] = ()
    """Quotes that ARE in the reel but not where the judge said they
    were. A weaker fault than a hallucination and reported separately,
    because refusing on it would fail a judge that read the reel and
    quoted from one word in."""

    @property
    def refused(self) -> bool:
        return bool(self.ungrounded)

    def as_dict(self) -> dict:
        return {
            "reel": self.reel,
            "claim_quote": self.claim_quote,
            "claim": self.claim,
            "opening_quote": self.opening_quote,
            "closing_quote": self.closing_quote,
            "closing_asks_for": self.closing_asks_for,
            "takeaway_quote": self.takeaway_quote,
            "takeaway": self.takeaway,
            "assumes_known": [dict(a) for a in self.assumes_known],
            "stops_developing_at": self.stops_developing_at,
            "rank": self.rank,
            "basis": self.basis,
            "ungrounded": list(self.ungrounded),
            "misplaced": list(self.misplaced),
            "refused": self.refused,
        }


def check_reading(entry: dict, words: str,
                  runs_for_seconds: Optional[float] = None) -> Tuple[list, list]:
    """`(ungrounded, misplaced)` for one reading against the reel's words.

    A quote is checked by CONTAINMENT of its normalised words in the
    reel's normalised words.  Present or absent - no fraction, no
    similarity and no threshold, which is what makes the check something
    a reader can re-run rather than something they have to trust.

    `prefix` and `suffix` are checked separately and produce `misplaced`
    rather than `ungrounded`: a judge that quoted the reel's second
    sentence as its opening read the reel and got the boundary wrong,
    which is worth reporting and is not worth throwing the reading away
    for.  A quote that is nowhere in the reel is a different thing and
    the reading is refused.
    """
    ungrounded: List[str] = []
    misplaced: List[str] = []
    body = normalise(words)

    for f in READING_FIELDS:
        if f.grounding == "none":
            continue
        if f.name == "assumes_known":
            for item in (entry.get("assumes_known") or []):
                quote = normalise((item or {}).get("quote"))
                if not quote:
                    ungrounded.append(
                        f"assumes_known entry {(item or {}).get('what')!r} "
                        f"carries no quote, so nothing in the reel can be "
                        f"checked against it")
                elif quote not in body:
                    ungrounded.append(
                        f"assumes_known quote {(item or {}).get('quote')!r} "
                        f"is not in what this reel says")
            continue

        raw = entry.get(f.name)
        quote = normalise(raw)
        if not quote:
            if f.required:
                ungrounded.append(
                    f"{f.name} is empty, and it is how this reading is "
                    f"anchored to the reel")
            continue
        if quote not in body:
            ungrounded.append(
                f"{f.name} {str(raw)[:70]!r} is not in what this reel says")
            continue
        if f.grounding == "prefix" and not body.startswith(quote):
            misplaced.append(
                f"{f.name} is in the reel but is not how it opens")
        if f.grounding == "suffix" and not body.endswith(quote):
            misplaced.append(
                f"{f.name} is in the reel but is not how it ends")

    stops = entry.get("stops_developing_at")
    if stops is not None and runs_for_seconds:
        try:
            stops = float(stops)
        except (TypeError, ValueError):
            ungrounded.append(
                f"stops_developing_at {entry.get('stops_developing_at')!r} "
                f"is not a number of seconds")
        else:
            if stops < 0 or stops > runs_for_seconds + 1.0:
                ungrounded.append(
                    f"stops_developing_at {stops:.1f}s is outside this "
                    f"reel, which runs {runs_for_seconds:.1f}s")
    return ungrounded, misplaced


def read_one(entry: dict, words: str,
             runs_for_seconds: Optional[float] = None) -> Reading:
    """One checked reading. Refused readings come back marked, not dropped."""
    ungrounded, misplaced = check_reading(entry, words, runs_for_seconds)
    stops = entry.get("stops_developing_at")
    try:
        stops = float(stops) if stops is not None else None
    except (TypeError, ValueError):
        stops = None
    rank = entry.get("rank")
    try:
        rank = int(rank) if rank is not None else None
    except (TypeError, ValueError):
        rank = None
    return Reading(
        reel=int(entry.get("reel") or 0),
        claim_quote=str(entry.get("claim_quote") or ""),
        claim=str(entry.get("claim") or ""),
        opening_quote=str(entry.get("opening_quote") or ""),
        closing_quote=str(entry.get("closing_quote") or ""),
        closing_asks_for=str(entry.get("closing_asks_for") or ""),
        takeaway_quote=str(entry.get("takeaway_quote") or ""),
        takeaway=str(entry.get("takeaway") or ""),
        assumes_known=tuple(dict(a) for a in
                            (entry.get("assumes_known") or [])),
        stops_developing_at=stops,
        rank=rank,
        basis=str(entry.get("basis") or ""),
        ungrounded=tuple(ungrounded),
        misplaced=tuple(misplaced),
    )


# ── JUDGEMENT: the verdicts the ENGINE derives ───────────────────────

def coherence_of(reading: Optional[Reading]) -> str:
    """Can a stranger who has never heard the episode follow it?

    Derived, never asked.  The judge was asked what the reel REFERS TO
    that a listener could not know from the reel itself, and had to quote
    the reel's own words for each - so the observation is checkable and
    the judge was not told which way it counts.  A reel that leans on
    nothing outside itself is one a stranger can follow; that is the
    handoff's own definition of the failure, in the selector's words:
    "the passage leans on something said earlier in the episode that is
    not inside the reel".

    A refused reading is UNJUDGED.  Evidence that failed its own check is
    not evidence, and a reading whose quotes are not in the reel says
    nothing about the reel.
    """
    if reading is None or reading.refused:
        return UNJUDGED
    return NOT_FOLLOWABLE if reading.assumes_known else FOLLOWABLE


def value_of(reading: Optional[Reading]) -> str:
    """Does it hand a viewer something they can use?

    Derived from whether the judge could QUOTE the words a listener
    could repeat or act on.  "Empty if the reel has none" is written into
    the question, and an empty answer is a real one rather than a
    refusal to answer: a reel that is two people agreeing pleasantly has
    no takeaway to quote and the judge should say so.
    """
    if reading is None or reading.refused:
        return UNJUDGED
    return DELIVERS if reading.takeaway_quote.strip() else DELIVERS_NOTHING


# ── The verdict ──────────────────────────────────────────────────────

PASS = "pass"
FAIL = "fail"


@dataclass
class ReelVerdict:
    """What the bar says about one reel."""

    number: int
    name: str
    delivered_seconds: float
    duration: dict
    cta: dict
    findings: List[BarFinding] = field(default_factory=list)
    coherence: str = UNJUDGED
    value: str = UNJUDGED
    reading: Optional[Reading] = None

    @property
    def errors(self) -> List[BarFinding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> List[BarFinding]:
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def verdict(self) -> str:
        return FAIL if self.errors else PASS

    @property
    def rank(self) -> Optional[int]:
        """The judge's place for this reel, or None.

        None means UNJUDGED and never last (`passage_engagement`)."""
        return self.reading.rank if self.reading and not self.reading.refused \
            else None

    def as_dict(self) -> dict:
        return {
            "reel": self.number,
            "name": self.name,
            "verdict": self.verdict,
            "delivered_seconds": round(self.delivered_seconds, 1),
            "duration": self.duration,
            "call_to_action": self.cta,
            "coherence": self.coherence,
            "value": self.value,
            "rank": self.rank,
            "basis": self.reading.basis if self.reading else "",
            "findings": [f.as_dict() for f in self.findings],
            "reading": self.reading.as_dict() if self.reading else None,
        }


@dataclass
class BarReport:
    """The bar's answer for a whole batch."""

    verdicts: List[ReelVerdict] = field(default_factory=list)
    judged: bool = False
    """False when no reading was supplied at all - so a reader can tell
    "the model has not been asked yet" from "the model found nothing"."""
    not_read: List[int] = field(default_factory=list)
    """Reels the judgement did not cover."""

    @property
    def failing(self) -> List[ReelVerdict]:
        return [v for v in self.verdicts if v.verdict == FAIL]

    @property
    def passing(self) -> List[ReelVerdict]:
        return [v for v in self.verdicts if v.verdict == PASS]

    def as_dict(self) -> dict:
        return {
            "format": "reel_quality_bar/1",
            "qualities": [
                {"name": q.name, "kind": q.kind, "asked": q.asked,
                 "held_by": list(q.held_by)} for q in QUALITIES],
            "judged": self.judged,
            "reels": len(self.verdicts),
            "passing": len(self.passing),
            "failing": len(self.failing),
            "not_read": list(self.not_read),
            "guidance_seconds": list(LENGTH_GUIDANCE),
            "verdicts": [v.as_dict() for v in self.verdicts],
        }


def judge(moments: Sequence, transcript: dict,
          judgement: Optional[dict] = None) -> BarReport:
    """The bar, over a whole batch.

    `judgement` is the model's readings - `{"readings": [...]}` - or
    None.  Absent, the EXACT half still runs in full and the two
    JUDGEMENT qualities read UNJUDGED; that is deliberate, because
    duration and the closer are properties of the plan and are worth
    holding whether or not anyone has been asked to read the reels yet.
    """
    closers = declared_closers(moments)
    entries = {}
    for entry in ((judgement or {}).get("readings") or []):
        try:
            entries[int(entry.get("reel"))] = entry
        except (TypeError, ValueError):
            continue

    report = BarReport(judged=bool(entries))
    for moment in moments:
        number = int(moment.number)
        findings = exact_findings(moment, transcript, closers)
        duration = duration_reading(moment, transcript)
        cta = cta_reading(moment, transcript, closers)

        reading = None
        entry = entries.get(number)
        if entry is not None:
            reading = read_one(entry, reel_text(moment, transcript),
                               duration["delivered_seconds"])
        else:
            report.not_read.append(number)

        name = moment.timeline_name
        if reading is not None and reading.refused:
            findings.append(BarFinding(
                code=QB_UNGROUNDED, reel=name, severity=ERROR,
                message=(
                    "the reading of this reel could not be checked against "
                    "what it says, so it is not evidence and no judgement "
                    "is recorded from it: "
                    + "; ".join(reading.ungrounded)),
                detail={"ungrounded": list(reading.ungrounded)}))
        if reading is not None:
            for misplaced in reading.misplaced:
                findings.append(BarFinding(
                    code=(QB_OPENING_MISPLACED if "opens" in misplaced
                          else QB_CLOSING_MISPLACED),
                    reel=name, severity=WARNING, message=misplaced,
                    detail={"reading": reading.as_dict()}))

        coherence = coherence_of(reading)
        value = value_of(reading)
        if coherence == NOT_FOLLOWABLE:
            findings.append(BarFinding(
                code=QB_NOT_FOLLOWABLE, reel=name, severity=ERROR,
                message=(
                    "leans on "
                    + "; ".join(
                        f"{(a or {}).get('what')!r} (at {(a or {}).get('quote')!r})"
                        for a in reading.assumes_known)
                    + " - a listener who has not heard the episode cannot "
                      "follow it"),
                detail={"assumes_known":
                        [dict(a) for a in reading.assumes_known]}))
        if value == DELIVERS_NOTHING:
            findings.append(BarFinding(
                code=QB_NO_TAKEAWAY, reel=name, severity=ERROR,
                message=("nothing in it can be quoted as something a "
                         "listener could repeat or act on"),
                detail={"claim": reading.claim}))

        # The instrument checking itself, in BOTH directions.  The reader
        # was never told a closer was wanted, so its answer about what
        # the last words ask for is independent evidence about the same
        # thing the exact half measures - and where the two disagree, one
        # of them is wrong and a reader should look.  Reported, never
        # resolved here: resolving it would mean preferring one
        # instrument over the other with nothing to prefer it on.
        #
        # The SECOND direction is the one that found something.  On the
        # field test's 25 approved moments the exact half read reels 05
        # and 09 as ABSENT - correctly, by its own evidence, because no
        # moment in the batch names those seconds as a closer - while
        # both end on a spoken invitation ("if you want to check
        # yourself, the Lucy visibility system, the links in our bio").
        # `declared_closers` can only know the closers the batch named,
        # and that is a real limit rather than a bug; this is where it is
        # SAID instead of being a silent hole.
        if reading is not None and not reading.refused:
            asks = bool(reading.closing_asks_for.strip())
            if cta["source"] != "absent" and not asks:
                findings.append(BarFinding(
                    code=QB_CTA_DISAGREEMENT, reel=name, severity=WARNING,
                    message=(
                        f"the closer {cta['span']} is placed last, and the "
                        f"reader of this reel found its last words asking "
                        f"the listener for nothing. One of the two is "
                        f"wrong"),
                    detail={"cta": cta,
                            "closing_quote": reading.closing_quote}))
            elif cta["source"] == "absent" and asks:
                findings.append(BarFinding(
                    code=QB_CTA_DISAGREEMENT, reel=name, severity=WARNING,
                    message=(
                        f"no moment in this batch names these closing "
                        f"seconds as a call to action, and the reader of "
                        f"this reel found its last words asking the "
                        f"listener to {reading.closing_asks_for.strip()!r}. "
                        f"If the reader is right, this reel has one and "
                        f"the batch has never named it"),
                    detail={"cta": cta,
                            "closing_quote": reading.closing_quote,
                            "closing_asks_for": reading.closing_asks_for}))

        report.verdicts.append(ReelVerdict(
            number=number, name=name,
            delivered_seconds=duration["delivered_seconds"],
            duration=duration, cta=cta, findings=findings,
            coherence=coherence, value=value, reading=reading))
    return report


def ranked(report: BarReport) -> List[ReelVerdict]:
    """The batch in the judge's ordering, unjudged reels LAST and SAID SO.

    They are last in the printing order because a list has to print in
    some order; they carry `rank is None` and every reader must say so
    rather than treat the position as a place.  That is the rule
    `passage_engagement` holds - a rank of None is never coerced to 0 or
    to last - and the distinction is the difference between "the model
    put this eighteenth" and "nobody read it".
    """
    return sorted(report.verdicts,
                  key=lambda v: (v.rank is None, v.rank or 0, v.number))


# ── Reading it back ──────────────────────────────────────────────────

def format_table(report: BarReport) -> str:
    """One row per reel, in the judge's ordering."""
    low, high = LENGTH_GUIDANCE
    lines = [
        f"REEL QUALITY BAR - {len(report.verdicts)} reel(s), "
        f"{len(report.passing)} pass, {len(report.failing)} fail",
        f"guidance {low:.0f}-{high:.0f}s; coherence and value are the "
        f"model's reading, checked against each reel's own words",
        "",
        f"{'rank':>4}  {'#':>3}  {'verdict':<7}  {'secs':>6}  "
        f"{'closer':<9}  {'coherence':<14}  {'value':<16}  name",
    ]
    for v in ranked(report):
        lines.append(
            f"{(str(v.rank) if v.rank is not None else '-'):>4}  "
            f"{v.number:>3}  {v.verdict.upper():<7}  "
            f"{v.delivered_seconds:>6.1f}  "
            f"{v.cta['source']:<9}  {v.coherence:<14}  {v.value:<16}  "
            f"{v.name}")
    if report.not_read:
        lines += ["", f"NOT READ by any judge: "
                      f"{', '.join(str(n) for n in report.not_read)} - "
                      f"their coherence and value are UNJUDGED, which is not "
                      f"a low mark"]
    findings = [f for v in report.verdicts for f in v.findings]
    if findings:
        lines += ["", f"FINDINGS ({len(findings)})", ""]
        for finding in findings:
            lines.append(f"  [{finding.severity:<7}] {finding.code:<21} "
                         f"{finding.reel}")
            lines.append(f"            {finding.message}")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────

JUDGEMENT_FILENAME = "reel_judgement.json"
"""Where step 3.05's answer is kept for a reader outside a run.

One spelling, here, because the step writes it and this CLI reads it."""


def judgement_path(project_folder: str):
    """Beside the proposal it judges, addressed the same way it is."""
    from library.tools.project_layout import Area, ProjectLayout

    return (ProjectLayout(str(project_folder))
            .read_path(Area.REVIEW, JUDGEMENT_FILENAME))


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Hold the captain's four qualities against a reel plan.")
    parser.add_argument("--project", required=True,
                        help="Absolute path to the project folder.")
    parser.add_argument("--plan", default="",
                        help="Reel plan to read (default: the project's "
                             "approved proposal).")
    parser.add_argument("--judgement", default="",
                        help="The model's readings (default: the project's "
                             f"review/{JUDGEMENT_FILENAME}, when it has "
                             f"one).")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of a table.")
    args = parser.parse_args(argv)

    from library.tools.reel_proposal import proposal_path, read_proposal
    from library.tools.timeline_transcript import transcript_path

    project = os.path.abspath(args.project)
    moments = read_proposal(args.plan or proposal_path(project))
    transcript = json.loads(
        transcript_path(project).read_text(encoding="utf-8"))

    supplied = args.judgement or judgement_path(project)
    try:
        with open(supplied, encoding="utf-8") as handle:
            judgement = json.load(handle)
    except (OSError, ValueError):
        judgement = None
        print(f"no reading of these reels at {supplied} - the two EXACT "
              f"qualities are held below and coherence and value read "
              f"UNJUDGED.\n", file=sys.stderr)

    report = judge(moments, transcript, judgement)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(format_table(report))
    return 1 if report.failing else 0


assert_qualities_are_well_formed()


if __name__ == "__main__":
    sys.exit(main())
