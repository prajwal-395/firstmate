"""Candidate short-form moments, PROPOSED - and approval is a gate.

The captain's ruling (2026-09-04, Q4)
-------------------------------------
The pipeline proposes reel moments and the captain approves.  It reads
the transcript, proposes candidates with timecodes and a one-line reason
each, and the captain accepts, rejects or adjusts BEFORE any reel
timeline is built.  **Do not build timelines for unapproved moments.**

That last sentence is the whole reason this module exists rather than a
list of dicts.  An instruction not to build something is worth nothing if
the build path cannot tell an approved moment from a proposed one, so
approval is a STATE a moment carries and `assert_approved` is the gate
every build path has to pass.  A moment defaults to `PROPOSED`, and
`PROPOSED` fails the gate exactly as `REJECTED` does - because "nobody has
looked at it yet" and "the captain said no" are both "not approved", and
a default that passed would make the gate ornamental (AGENTS.md 10.4).

What is measured here and what is not
-------------------------------------
Choosing which moments are interesting is TASTE and belongs to a model
(AGENTS.md 10.5).  Nothing in this module scores, ranks or selects.  What
it does is:

- define what a proposal has to SAY - a span, a reason, a title,
- CHECK that a proposed span is real: inside the timeline, non-empty, and
  actually containing speech the transcript measured,
- carry approval, and refuse to let an unapproved moment through,
- and name the reel the captain's way.

The check is the part that stops a model inventing a timecode.  A
proposal naming seconds where nothing is said is refused by
`validate_proposal`, the same way `speech_sequence` is refused when its
chain disagrees with the files on disk.

The closing CTA, which need not be next to the body
---------------------------------------------------
**A moment is a BODY window plus, optionally, one CTA range taken from
ANYWHERE else in the episode.**  The captain's format ends on a spoken
call to action, and this episode says about six of them across nineteen
minutes.  While a moment was one contiguous window those two facts
capped the batch at about six reels; carrying the CTA as a SECOND range
uncaps it, and `reel_build.reel_ranges` lays it down last.

Three things this does NOT become:

- **It is not a licence to assemble a body from pieces.**  The captain
  rejected a batch that read as "two halves of different scripts
  combined".  A body plus one closing CTA is a format; a body stitched
  from scattered fragments is a collage.  `CallToAction` is ONE range and
  the body stays ONE window, which is what keeps the difference
  structural rather than a matter of restraint.
- **It is not an invented CTA.**  Every second of it is speech the
  episode really contains, and `validate_proposal` refuses a CTA range
  where the transcript measured nothing said - the same refusal a body
  span gets.  Nothing here authors, templates, pads or synthesises one.
- **It is not a judgement about which CTA suits which reel.**  That is
  taste and belongs to a model and the captain (AGENTS.md 10.5).
  Nothing in this module scores a CTA, ranks CTAs, or prefers the
  nearest one.

**The same CTA range may close any number of reels.**  Six spoken CTAs
closing sixteen reels is the whole point of the mechanism, and nothing is
copied or synthesised to do it - the same real clip is placed again,
which is an ordinary editing move.  So the cross-moment overlap rule
("two reels cannot share the same conversation") is deliberately about
BODIES only, and `validate_proposal` says so where it is enforced.

Naming
------
The captain's format, verbatim: `Reel 01 - <short topic slug>`.
`reel_timeline_name` is the only place it is spelled, so a rename is one
edit rather than a search.

`tests/test_reel_proposal.py`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field, replace
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REEL_NAME_FORMAT = "Reel {number:02d} - {slug}"
"""The captain's format, quoted from their answer. Two digits, zero
padded, then the topic slug."""

_SNAP_PASSES = 12
"""How many times `snap_to_speech` may widen before giving up. Widening
pulls in neighbours that can themselves be partially covered, so it
iterates to a fixed point; this only bounds a pathological transcript."""

MIN_REEL_SECONDS = 5.0
"""Below this a span cannot carry a spoken moment at all. A FLOOR ON
MEASUREMENT, not on taste: it refuses a span too short to contain the
speech the proposal claims, and says nothing about how long a good reel
is. There is deliberately no maximum - that is the captain's call."""

MIN_CTA_SECONDS = 0.04
"""Under one frame at any rate this engine renders, so nothing can be
placed from a closer this short. A MECHANICAL floor - it says a range is
too short to CUT, never anything about how long a good call to action is.
`reel_build.reel_ranges` refuses the same range at build time."""

MIN_TURN_SECONDS = 1.5
"""A speaker must contribute at least this much speech to count as having
a REAL TURN in the conversation, not just a stray word picked up by mic
bleed. The captain's words on batch one: 'its mostly just a single
person yapping and not really a convo'."""

class ProposalError(ValueError):
    """A proposal is not something that could be built."""


class NotApproved(RuntimeError):
    """A build path was handed a moment the captain has not approved."""


class Approval(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class CallToAction:
    """The spoken call to action a reel closes on.

    A range over the MASTER's own timebase, exactly like the body window,
    and under no obligation to sit next to it or after it.  The whole
    reason the type exists is that the episode's CTAs are where they are:
    six of them, scattered, and every reel has to end on one.

    `text` and `speaker` are MEASURED from the transcript by `enrich`, not
    asked of the model.  They are how a reader (and the captain) can see
    at a glance that the closer is real speech from the episode rather
    than a line somebody wrote - and they are the reason a CTA never has
    to be authored to be described.

    `note` is the model's one line on why this CTA closes THIS reel.
    Optional, because which CTA suits which reel is taste rather than
    something this module checks.
    """

    timeline_start: float
    timeline_end: float
    text: str = ""
    speaker: Optional[str] = None
    note: str = ""

    straddling_within: tuple = ()
    """Speech inside the CLOSER that `text` does not contain.

    `text` is measured from bound segments, so it has the same blind spot
    the body's preview had: a straddling segment is not in it and the
    reel plays it anyway.  On a borrowed closer that matters more than it
    does on a body - the whole claim being made about a CTA is that it is
    a COMPLETE spoken invitation, and the half of this episode's best one
    that finishes the sentence ("jump on lucycontent.com ... it's also in
    the link below") sits in exactly such a segment.  A closer whose
    completing words are here is not atomic, whatever `text` reads like.
    """

    @property
    def duration(self) -> float:
        return self.timeline_end - self.timeline_start

    @property
    def master_range(self) -> tuple:
        """The (start, end) pair `reel_build.reel_ranges` lays down last."""
        return (self.timeline_start, self.timeline_end)

    def as_dict(self) -> dict:
        out = asdict(self)
        out["duration_seconds"] = round(self.duration, 3)
        out["straddling_within"] = [dict(x) for x in self.straddling_within]
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "CallToAction":
        return cls(
            timeline_start=float(data["timeline_start"]),
            timeline_end=float(data["timeline_end"]),
            text=str(data.get("text", "")),
            speaker=data.get("speaker") or None,
            note=str(data.get("note", "")),
            straddling_within=tuple(dict(x) for x in
                                    (data.get("straddling_within") or ())),
        )


@dataclass(frozen=True)
class ReelMoment:
    """One candidate short-form clip, and where it came from."""

    number: int
    slug: str
    reason: str
    """One line, the captain's format. Why this moment, not a summary of
    what is said in it."""

    timeline_start: float
    timeline_end: float

    approval: Approval = Approval.PROPOSED
    approval_note: str = ""

    speakers: tuple = ()
    transcript_preview: str = ""
    duplicate_takes: tuple = ()
    """Repeated takes MEASURED inside this moment, each naming both
    timeline ranges. Reported so the captain can approve a moment and
    tell us which take to drop; nothing is removed from their edit."""

    source_spans: tuple = ()
    """Which raw footage this moment plays, as
    `{source_file, source_start, source_end}` - the ground truth, carried
    so a reel can be traced back without re-reading the timeline."""

    straddling_within: tuple = ()
    """Speech inside this moment's span that `transcript_preview` does
    NOT contain, because it straddles a cut and has no single source.
    The reel PLAYS it - `reel_build.placements` copies the master's
    clips over the keep ranges and knows nothing about segments - so a
    review surface that showed only the preview showed a reel that does
    not exist.  Reported per moment, never used for a boundary."""

    call_to_action: Optional["CallToAction"] = None
    """The spoken CTA this reel closes on, from ANYWHERE in the episode.

    `None` means this reel ends where its body ends, which is what every
    moment did before the field was added.  A moment that carries one
    plays its body and then this range, and `reel_build.reel_ranges` is
    the only place that order is spelled."""

    @property
    def duration(self) -> float:
        """The BODY window's length.  `total_duration` includes the CTA."""
        return self.timeline_end - self.timeline_start

    @property
    def total_duration(self) -> float:
        """How long the reel runs before any bad take is cut out of it.

        Body plus CTA.  `duration` is the body alone, and the two are
        deliberately different names rather than one that quietly changed
        meaning when the CTA arrived."""
        return self.duration + (self.call_to_action.duration
                                if self.call_to_action else 0.0)

    @property
    def timeline_name(self) -> str:
        return reel_timeline_name(self.number, self.slug)

    def as_dict(self) -> dict:
        body = asdict(self)
        body["approval"] = self.approval.value
        body["speakers"] = list(self.speakers)
        body["source_spans"] = [dict(s) for s in self.source_spans]
        body["duplicate_takes"] = [dict(d) for d in self.duplicate_takes]
        body["straddling_within"] = [dict(x) for x in self.straddling_within]
        body["has_duplicate_take"] = bool(self.duplicate_takes)
        body["timeline_name"] = self.timeline_name
        body["duration_seconds"] = round(self.duration, 3)
        body["call_to_action"] = (self.call_to_action.as_dict()
                                  if self.call_to_action else None)
        body["total_duration_seconds"] = round(self.total_duration, 3)
        return body

    @classmethod
    def from_dict(cls, data: dict) -> "ReelMoment":
        return cls(
            number=int(data["number"]),
            slug=str(data["slug"]),
            reason=str(data.get("reason", "")),
            timeline_start=float(data["timeline_start"]),
            timeline_end=float(data["timeline_end"]),
            approval=Approval(data.get("approval", "proposed")),
            approval_note=str(data.get("approval_note", "")),
            speakers=tuple(data.get("speakers") or ()),
            transcript_preview=str(data.get("transcript_preview", "")),
            source_spans=tuple(dict(s) for s in (data.get("source_spans") or ())),
            duplicate_takes=tuple(dict(d) for d in
                                  (data.get("duplicate_takes") or ())),
            straddling_within=tuple(dict(x) for x in
                                    (data.get("straddling_within") or ())),
            call_to_action=(CallToAction.from_dict(data["call_to_action"])
                            if data.get("call_to_action") else None),
        )


def slugify(text: str, limit: int = 40) -> str:
    """A topic slug for a reel name. Empty input gives `untitled`, which
    is visibly wrong in a timeline list rather than silently blank."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-")
    if not cleaned:
        return "untitled"
    return cleaned[:limit].rstrip("-")


def reel_timeline_name(number: int, slug: str) -> str:
    """`Reel 01 - topic`. The captain's format, spelled once."""
    return REEL_NAME_FORMAT.format(number=int(number), slug=slugify(slug))


# ── The gate ─────────────────────────────────────────────────────────

def assert_approved(moment: ReelMoment) -> ReelMoment:
    """Raise unless the captain approved this moment. Call before building.

    `PROPOSED` fails as hard as `REJECTED`. They are different sentences
    in the error and the same verdict, because a moment nobody has looked
    at is not a moment anybody agreed to.
    """
    if moment.approval is Approval.APPROVED:
        return moment
    if moment.approval is Approval.REJECTED:
        raise NotApproved(
            f"reel {moment.number} ({moment.slug!r}) was REJECTED"
            + (f": {moment.approval_note}" if moment.approval_note else "")
            + ". Building it anyway would overrule the captain.")
    raise NotApproved(
        f"reel {moment.number} ({moment.slug!r}) is still PROPOSED - the "
        f"captain has not looked at it. The pipeline proposes and the "
        f"captain approves; nothing is built before that.")


def approved_only(moments: Sequence[ReelMoment]) -> List[ReelMoment]:
    """The moments a build path may act on. Never filters silently -
    callers that want to know what was held back read `held_back`."""
    return [m for m in moments if m.approval is Approval.APPROVED]


def held_back(moments: Sequence[ReelMoment]) -> Dict[str, List[ReelMoment]]:
    """What is NOT being built, by why. Reported, never dropped quietly."""
    out: Dict[str, List[ReelMoment]] = {"proposed": [], "rejected": []}
    for moment in moments:
        if moment.approval is Approval.PROPOSED:
            out["proposed"].append(moment)
        elif moment.approval is Approval.REJECTED:
            out["rejected"].append(moment)
    return out


# ── Checking a proposal is real ──────────────────────────────────────

def _speech_within(transcript_segments: Sequence[dict],
                   start: float, end: float) -> List[dict]:
    """Transcript segments overlapping a span, in order."""
    hits = []
    for segment in transcript_segments:
        s, e = float(segment["timeline_start"]), float(segment["timeline_end"])
        if e > start and s < end:
            hits.append(segment)
    return sorted(hits, key=lambda x: x["timeline_start"])


def bound_segments(transcript: dict) -> List[dict]:
    """Only the segments that sit wholly inside ONE timeline clip.

    A segment with no `resolve_item_id` straddles a cut - 63 of the field
    test's 906 - and two things make it unsafe to anchor a reel on:

    - it came from two places in the raw footage, so it has no single
      ground truth, and
    - in practice it is often WhisperX bridging a silent gap. Craig's
      22.0-47.2s segment runs 25 seconds across a stretch where his track
      has no clip at all.

    So a reel boundary is never placed using one.  They are excluded from
    boundary arithmetic and REPORTED, not deleted: the speech is real and
    a reader should see it.
    """
    return [x for x in (transcript.get("segments") or [])
            if x.get("resolve_item_id")]


def straddling_segments(transcript: dict) -> List[dict]:
    """The complement of `bound_segments`, for reporting."""
    return [x for x in (transcript.get("segments") or [])
            if not x.get("resolve_item_id")]


def partial_overlaps(start: float, end: float,
                     transcript: dict) -> List[dict]:
    """Bound segments a `[start, end]` span cuts through rather than
    contains.  Non-empty means the reel would open or close mid-sentence.
    """
    # BOUND segments only - what this docstring has always said, and
    # what the code did not do.  A straddling segment carries no single
    # source, so `snap_to_speech` will not move a boundary to its edges
    # and `enrich` leaves it out of the preview; refusing a boundary for
    # cutting one therefore refuses spans that nothing can fix, and on
    # this episode a straddler overlaps almost every reel-length window.
    # What it cuts is REPORTED instead, by `straddling_within`, which is
    # the honest answer: the words are audible, they are not a sentence
    # boundary anyone can snap to, and the reader gets to see them.
    cut = []
    segments = bound_segments(transcript)
    for segment in segments:
        s, e = float(segment["timeline_start"]), float(segment["timeline_end"])
        if e > start and s < end and not (start <= s and e <= end):
            cut.append(segment)
    return cut


def snap_to_speech(start: float, end: float, transcript: dict,
                   ) -> tuple:
    """Move a span OUT to the nearest whole-segment boundaries.

    Outward rather than inward, because trimming to the nearest inner
    boundary silently drops words the proposer meant to include, while
    extending adds only what was already being spoken across the line.
    """
    # BOUND segments only.  `bound_segments` states the rule - "a reel
    # boundary is never placed using one" - and this function used to
    # read `transcript["segments"]` whole, which broke it in the one
    # direction that matters: a straddling segment is usually WhisperX
    # bridging SILENCE, so its far edge sits seconds past the last word
    # of the sentence the proposer meant to close on, in the middle of
    # the next topic.  Measured on the field test: a reel asked to end
    # at 195.2s was widened to 200.46s, which is 3.9s of silence and
    # then "so this is why like if you have an hvac company or you're
    # an attorney or you're" - cut mid-sentence; a reel asked to end at
    # 480.6s was widened the same way.  Neither ending was visible to a
    # reviewer, because `enrich` reads bound segments only, so the
    # preview showed a clean close that the built reel did not have.
    # The two halves now read the same list.
    segments = bound_segments(transcript)
    # ITERATE to a fixed point. Extending the span pulls in segments that
    # were outside it, and those can themselves be partially covered - so
    # one pass leaves a boundary mid-sentence and `validate_proposal`
    # refuses it. Found exactly that way, on reel 12 of the second batch.
    for _ in range(_SNAP_PASSES):
        touching = [x for x in segments
                    if float(x["timeline_end"]) > start
                    and float(x["timeline_start"]) < end]
        if not touching:
            return start, end
        widened = (min(float(x["timeline_start"]) for x in touching),
                   max(float(x["timeline_end"]) for x in touching))
        if widened == (start, end):
            return widened
        start, end = widened
    return start, end


# ── Repeated takes ───────────────────────────────────────────────────
#
# The captain warned that the rough cut "includes several takes of the
# same audio lines".  Q5 ruled: support their removal, do not build a
# detector - and surface it if it fell out for free.  It did, and then it
# turned out to be FOUR OF THE FIRST TEN proposals, so it is reported per
# moment rather than left as a footnote.
#
# This MEASURES text similarity and decides nothing.  Which take to keep,
# or whether to keep both, is the captain's call on their own edit;
# nothing here trims, reorders or removes anything.

_TAKE_STOPWORDS = frozenset("""
a an the and or but so if of to in on at by for with from as is it its
that this these those be was were been are am do does did doing have has
had you your yours we our i my me they them their he she his her not no
just like really about into over than then there here what which who whom
when where why how all any some more most very can could will would shall
should may might must
""".split())

TAKE_SIMILARITY = 0.65
"""How much of two stretches' content vocabulary must coincide before
they are reported as the same take.

A phrase-level check does NOT work here: take two of reel 02 is "stuffed
all their keywords with H1 tags" against "stuff all the H1 tags with
keywords", which shares no long n-gram and is plainly the same sentence.
Content-word overlap survives the reordering; exact phrases do not."""

TAKE_WINDOW_WORDS = 4
TAKE_WEAK_WINDOW = 3
"""Two bands, because no single window is both complete and clean.

Measured over the first ten proposals: at a 4-word window the detector
names reels 02, 05, 06 and 07 and nothing else - every one a real retake.
Dropping to 3 also catches reel 03, whose retake is only three content
words long, but picks up reel 01, where "AI actually understand" simply
recurs 7.8 seconds apart in ordinary speech.

So a 4-word match is reported as `repeat` and a 3-word one as
`possible`, rather than picking a threshold that is wrong in one
direction. This is the shape `footage_search` already uses for its dense
score floor and weak band (AGENTS.md 4): a ranking cannot say "not here",
so the boundary is REPORTED rather than decided.

Recall is worth more than precision here and that is a deliberate
choice: every finding shows both texts and both timecodes, so a false
one costs the captain a glance, while a missed one costs a reel that
stutters in public."""




def _content_words(text: str) -> set:
    words = re.findall(r"[a-z0-9']+", str(text).lower())
    return {w for w in words if w not in _TAKE_STOPWORDS and len(w) > 1}


def _timed_content_words(start: float, end: float,
                         transcript: dict) -> List[tuple]:
    """`(word, time)` for every content word spoken inside a span.

    Built from the WORD STREAM, not from segments. WhisperX segments a
    repeated take unevenly - reel 03's first take is spread over three
    segments of under a second each while the second take sits INSIDE a
    single longer one - so any comparison keyed to segment boundaries
    misses exactly the cases that matter.
    """
    out: List[tuple] = []
    for segment in _speech_within(bound_segments(transcript), start, end):
        words = segment.get("words") or []
        if words:
            for word in words:
                token = re.sub(r"[^a-z0-9']+", "", str(word.get("word", "")).lower())
                if token and token not in _TAKE_STOPWORDS and len(token) > 1:
                    out.append((token, float(word.get("start",
                                                      segment["timeline_start"]))))
        else:
            span_start = float(segment["timeline_start"])
            for token in _content_words_ordered(segment.get("text", "")):
                out.append((token, span_start))
    return out


def _content_words_ordered(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9']+", str(text).lower())
    return [w for w in words if w not in _TAKE_STOPWORDS and len(w) > 1]


def duplicate_takes(start: float, end: float, transcript: dict,
                    threshold: float = TAKE_SIMILARITY,
                    window: int = TAKE_WINDOW_WORDS,
                    weak_window: Optional[int] = TAKE_WEAK_WINDOW,
                    ) -> List[dict]:
    """Stretches inside a span that say the same thing twice.

    Two windows of `window` content words are the same take when their
    vocabularies overlap by `threshold` and they do not overlap in time.
    Each entry carries BOTH timeline ranges, so the captain can see which
    of the two to drop without hunting for it.

    Reports the FIRST repeat per region and then skips past it, so one
    doubled sentence is one finding rather than a dozen overlapping ones.
    """
    found = _scan_takes(start, end, transcript, threshold, window, "repeat")
    if weak_window and weak_window < window:
        covered = [(f["first_start"], f["second_end"]) for f in found]
        for weak in _scan_takes(start, end, transcript, threshold,
                                weak_window, "possible"):
            if not any(a <= weak["first_start"] <= b for a, b in covered):
                found.append(weak)
    found.sort(key=lambda f: f["first_start"])
    return found


def _scan_takes(start: float, end: float, transcript: dict,
                threshold: float, window: int, band: str) -> List[dict]:
    stream = _timed_content_words(start, end, transcript)
    if len(stream) < window * 2:
        return []

    sets = [({w for w, _ in stream[i:i + window]}, stream[i][1],
             stream[i + window - 1][1])
            for i in range(len(stream) - window + 1)]

    found: List[dict] = []
    consumed_until = -1.0
    for i, (first_set, first_start, first_end) in enumerate(sets):
        if first_start <= consumed_until:
            continue
        best = None
        for j in range(i + window, len(sets)):
            second_set, second_start, second_end = sets[j]
            if second_start <= first_end:
                continue
            score = len(first_set & second_set) / len(first_set | second_set)
            if score >= threshold and (best is None or score > best[0]):
                best = (score, second_start, second_end)
        if best:
            score, second_start, second_end = best
            found.append({
                "band": band,
                "similarity": round(score, 3),
                "first_start": round(first_start, 2),
                "first_end": round(first_end, 2),
                "first_text": _text_between(first_start, first_end, transcript),
                "second_start": round(second_start, 2),
                "second_end": round(second_end, 2),
                "second_text": _text_between(second_start, second_end,
                                             transcript),
            })
            consumed_until = second_end
    return found


def _text_between(start: float, end: float, transcript: dict) -> str:
    """What was said between two timeline seconds, for the report."""
    pieces = []
    for segment in _speech_within(bound_segments(transcript),
                                  start - 0.01, end + 0.01):
        text = (segment.get("text") or "").strip()
        if text:
            pieces.append(text)
    joined = " ".join(pieces)
    return joined[:200].rstrip() + ("..." if len(joined) > 200 else "")


def _check_call_to_action(moment: ReelMoment, transcript: dict,
                          timeline_duration: float, label: str) -> None:
    """Refuse a CTA range that is not real spoken audio from the episode.

    Exactly the refusals a body span gets - a real range, inside the
    timeline, speech measured inside it, whole segments at both edges -
    plus the one a body span cannot need: a CTA may not overlap its own
    reel's body, because that would play the same seconds of the episode
    twice on one reel.

    What is deliberately NOT checked is whether the passage is a GOOD
    call to action, or a call to action at all.  There is no keyword
    list, no pitch score and no similarity cutoff, because that judgement
    is the model's and the captain's (AGENTS.md 10.5).  What is checked
    is only that the seconds are real and somebody speaks in them, which
    is what stops a CTA being authored, templated, padded or invented.
    """
    cta = moment.call_to_action
    if cta is None:
        return
    if cta.timeline_end <= cta.timeline_start:
        raise ProposalError(
            f"{label}: its call to action runs {cta.timeline_start} to "
            f"{cta.timeline_end}, which is not a range")
    if cta.duration <= MIN_CTA_SECONDS:
        raise ProposalError(
            f"{label}: its call to action runs {cta.duration:.3f}s, under "
            f"a frame. Nothing can be placed from it, so a range this "
            f"short is a mistyped timecode rather than a closer.")
    if cta.timeline_start < 0 or cta.timeline_end > timeline_duration + 0.001:
        raise ProposalError(
            f"{label}: its call to action runs {cta.timeline_start:.2f}-"
            f"{cta.timeline_end:.2f}s, outside the timeline's "
            f"0-{timeline_duration:.2f}s")

    segments = transcript.get("segments") or []
    if segments and not _speech_within(segments, cta.timeline_start,
                                       cta.timeline_end):
        raise ProposalError(
            f"{label}: its call to action runs {cta.timeline_start:.2f}-"
            f"{cta.timeline_end:.2f}s, where the transcript measured no "
            f"speech at all. A CTA must be genuinely SPOKEN in the "
            f"episode - it is never authored, templated or padded.")

    cut = partial_overlaps(cta.timeline_start, cta.timeline_end, transcript)
    if cut:
        first = cut[0]
        raise ProposalError(
            f"{label}: its call to action cuts {len(cut)} segment(s) "
            f"rather than containing them, so the reel would close "
            f"mid-sentence. First: [{first['timeline_start']:.2f}-"
            f"{first['timeline_end']:.2f}s] "
            f"{first.get('text', '')[:60]!r}. Use `snap_to_speech` to "
            f"move the boundaries out to whole segments.")

    overlap_start = max(cta.timeline_start, moment.timeline_start)
    overlap_end = min(cta.timeline_end, moment.timeline_end)
    if overlap_end > overlap_start:
        raise ProposalError(
            f"{label}: its call to action ({cta.timeline_start:.2f}-"
            f"{cta.timeline_end:.2f}s) overlaps its own body "
            f"({moment.timeline_start:.2f}-{moment.timeline_end:.2f}s) by "
            f"{overlap_end - overlap_start:.2f}s, so the reel would play "
            f"those seconds twice. A CTA already inside the body needs no "
            f"second range - drop it, or move it to one that is elsewhere.")


def validate_proposal(moments: Sequence[ReelMoment],
                      transcript: dict,
                      timeline_duration: float) -> None:
    """Every moment names a real, non-empty span containing real speech.

    This is what stops a model inventing a timecode. A proposal is taste
    about WHICH moment matters; it is not licence to claim seconds that
    do not exist or that nobody speaks in.
    """
    if not moments:
        raise ProposalError(
            "no moments proposed. An empty proposal is the absence of a "
            "suggestion, not a suggestion of nothing.")

    segments = transcript.get("segments") or []
    seen_numbers = set()
    for moment in moments:
        label = f"reel {moment.number} ({moment.slug!r})"
        if moment.number in seen_numbers:
            raise ProposalError(f"{label}: reel number {moment.number} is used twice")
        seen_numbers.add(moment.number)
        if moment.number < 1:
            raise ProposalError(f"{label}: reel numbers start at 1")
        if not moment.reason.strip():
            raise ProposalError(
                f"{label} carries no reason. The captain asked for a "
                f"one-line reason each; a moment that cannot say why it "
                f"was picked cannot be judged.")
        if moment.timeline_end <= moment.timeline_start:
            raise ProposalError(
                f"{label} runs {moment.timeline_start} to "
                f"{moment.timeline_end}, which is not a range")
        if moment.timeline_start < 0 or moment.timeline_end > timeline_duration + 0.001:
            raise ProposalError(
                f"{label} runs {moment.timeline_start:.2f}-"
                f"{moment.timeline_end:.2f}s, outside the timeline's "
                f"0-{timeline_duration:.2f}s")
        if moment.duration < MIN_REEL_SECONDS:
            raise ProposalError(
                f"{label} is {moment.duration:.2f}s, under the "
                f"{MIN_REEL_SECONDS}s floor - too short to contain the "
                f"speech it claims")
        if segments and not _speech_within(segments, moment.timeline_start,
                                           moment.timeline_end):
            raise ProposalError(
                f"{label} runs {moment.timeline_start:.2f}-"
                f"{moment.timeline_end:.2f}s, where the transcript "
                f"measured no speech at all. A moment nobody speaks in is "
                f"an invented timecode.")
        cut = partial_overlaps(moment.timeline_start, moment.timeline_end,
                               transcript)
        if cut:
            first = cut[0]
            raise ProposalError(
                f"{label} cuts {len(cut)} segment(s) rather than "
                f"containing them - it would open or close mid-sentence. "
                f"First: [{first['timeline_start']:.2f}-"
                f"{first['timeline_end']:.2f}s] "
                f"{first.get('text', '')[:60]!r}. Use `snap_to_speech` to "
                f"move the boundaries out to whole segments.")
        _check_call_to_action(moment, transcript, timeline_duration, label)

    # NOTHING here refuses two reels for sharing seconds, and that is
    # true of the BODY as well as the CTA.
    #
    # The CTA half is #526's and is unchanged: this episode says about
    # six calls to action while the format asks every reel to close on
    # one, so a closer is reused BY DESIGN and closers are never compared.
    #
    # The BODY half is the captain's later ruling, and it reverses the
    # refusal #526 shipped ("Two reels cannot share the same
    # conversation", at a 1.0s threshold). Asked directly whether two
    # reels may draw on one passage he said "i mean they can as long as
    # its not like the exact same video yk", and then chose: judge it on
    # whether the two reels SAY DIFFERENT THINGS, never on seconds
    # shared. A 1.0s threshold is exactly the mechanical proxy for that
    # judgement he ruled out - one was measured against his own verdicts
    # and did not predict them - and it refuses a second reel on an
    # exchange worth two before he ever sees it.
    #
    # So the shared span is REPORTED, on both moments, and he decides.
    # See step_3_04_select_reels/post_bridge.py for where it is written,
    # and the handoff's "Two reels may draw on the same passage".


def overlaps_picture_hole(moment: ReelMoment,
                          transcript: dict) -> Optional[str]:
    """None if the moment avoids all picture holes, else a reason string.

    A hole in the captain's master is invisible to the model - it cannot
    avoid selecting over one.  This is a bad PICK, not an integrity
    failure: the model told the truth about the timecode, and raising
    would kill the whole batch for a defect the model had no way to avoid.
    Dropped with a reason so the captain sees which moments were affected.
    """
    holes = (transcript.get("derived_from") or {}).get("picture_holes") or []
    # Every master range the reel PLAYS, not just its body - a CTA taken
    # from elsewhere in the episode can sit over a hole the body avoids,
    # and it would play black just the same.
    spans = [("", moment.timeline_start, moment.timeline_end)]
    if moment.call_to_action:
        spans.append(("its call to action ",
                      moment.call_to_action.timeline_start,
                      moment.call_to_action.timeline_end))
    for where, span_start, span_end in spans:
        for h_start, h_end in holes:
            overlap_start = max(span_start, h_start)
            overlap_end = min(span_end, h_end)
            if overlap_end > overlap_start + 0.04:
                hole_dur = h_end - h_start
                return (
                    f"{where}contains a picture hole at "
                    f"{overlap_start:.2f}-{overlap_end:.2f}s "
                    f"({hole_dur:.1f}s hole in the master). "
                    f"A reel selected over a hole will play black."
                )
    return None

def is_conversation(moment: ReelMoment, transcript: dict) -> Optional[str]:
    """None if the moment is a conversation, else a reason string.

    A single-speaker moment is a bad PICK, not an integrity failure.
    validate_proposal raises on integrity; this returns a reason for
    post_bridge to drop the moment and report it.

    The captain, rejecting the first ten reels: 'its mostly just a
    single person yapping and not really a convo'.  They classified
    'both speakers with real turns' as the CHECKABLE half, explicitly
    not taste.
    """
    bound = bound_segments(transcript)
    hits = _speech_within(bound, moment.timeline_start, moment.timeline_end)
    turn_by_speaker: Dict[Optional[str], float] = {}
    for seg in hits:
        speaker = seg.get("speaker")
        if not speaker:
            continue
        seg_start = max(float(seg["timeline_start"]), moment.timeline_start)
        seg_end = min(float(seg["timeline_end"]), moment.timeline_end)
        dur = max(seg_end - seg_start, 0.0)
        turn_by_speaker[speaker] = turn_by_speaker.get(speaker, 0.0) + dur
    real_speakers = [s for s, d in turn_by_speaker.items()
                     if d >= MIN_TURN_SECONDS]
    if len(real_speakers) < 2:
        found = ", ".join(f"{s} ({d:.1f}s)" for s, d in
                          sorted(turn_by_speaker.items(),
                                 key=lambda x: -x[1]))
        return (
            f"has {len(real_speakers)} speaker(s) with real turns "
            f"(>= {MIN_TURN_SECONDS}s each): {found or 'none'}. "
            f"The captain: 'its mostly just a single person yapping "
            f"and not really a convo'."
        )
    return None


def straddling_within(start: float, end: float,
                      transcript: dict) -> List[dict]:
    """The words a span plays that `transcript_preview` cannot show.

    A straddling segment carries no single source, so no boundary is
    placed with one and `enrich` leaves it out of the preview.  It is
    still on the timeline and the built reel still plays it, so leaving
    it out of the REPORT as well is what let two reels be reviewed on a
    closing line they did not close on.

    Reported WORD BY WORD, intersected with the span, because a
    straddling segment is usually WhisperX bridging silence: its
    `timeline_start`/`timeline_end` can span half a minute of which two
    seconds are spoken.  `word_count` beside `first_word_at` and
    `last_word_at` is what tells a reader which it is - two words over
    nineteen seconds is a bridge, nineteen words over nineteen seconds
    is a sentence.  Summed word DURATIONS would not: WhisperX hangs the
    bridged silence on the last word before it, so the two words "well
    that's" measure 19.2 voiced seconds on this episode.  No gap
    threshold is applied and none is needed - the words are the
    measurement.
    """
    out: List[dict] = []
    for segment in straddling_segments(transcript):
        if not (float(segment["timeline_end"]) > start
                and float(segment["timeline_start"]) < end):
            continue
        inside = [w for w in (segment.get("words") or ())
                  if float(w.get("end", 0)) > start
                  and float(w.get("start", 0)) < end]
        if not inside:
            continue
        out.append({
            "speaker": segment.get("speaker"),
            "first_word_at": round(float(inside[0]["start"]), 2),
            "last_word_at": round(float(inside[-1]["end"]), 2),
            "word_count": len(inside),
            "text": " ".join(str(w.get("word", "")) for w in inside),
        })
    return sorted(out, key=lambda x: x["first_word_at"])


def enrich(moment: ReelMoment, transcript: dict) -> ReelMoment:
    """Fill a moment's measured fields from the transcript.

    Speakers, a preview of what is said and the ground-truth source spans
    are MEASURED, so they are attached here rather than asked of the
    model - a model that had to restate them could restate them wrongly.
    """
    # BOUND segments only, matching the boundary rule. A straddling
    # segment is often WhisperX bridging a silent gap - Craig's
    # 22.0-47.2s spans 25 seconds of a track that has no clip for most of
    # it - so counting one here would put words in a reel's preview that
    # the reel does not contain, and name a speaker who is not in it.
    hits = _speech_within(bound_segments(transcript),
                          moment.timeline_start, moment.timeline_end)
    speakers, spans, words = [], [], []
    for segment in hits:
        speaker = segment.get("speaker")
        if speaker and speaker not in speakers:
            speakers.append(speaker)
        words.append((segment.get("text") or "").strip())
        if segment.get("source_file"):
            spans.append({
                "source_file": segment["source_file"],
                "source_start": segment.get("source_start"),
                "source_end": segment.get("source_end"),
            })
    preview = " ".join(w for w in words if w)
    return replace(moment, speakers=tuple(speakers),
                   transcript_preview=preview, source_spans=tuple(spans),
                   straddling_within=tuple(straddling_within(
                       moment.timeline_start, moment.timeline_end,
                       transcript)),
                   call_to_action=enrich_call_to_action(
                       moment.call_to_action, transcript),
                   duplicate_takes=tuple(duplicate_takes(
                       moment.timeline_start, moment.timeline_end,
                       transcript)))


def enrich_call_to_action(cta: Optional[CallToAction],
                          transcript: dict) -> Optional[CallToAction]:
    """Fill a CTA's `text` and `speaker` from the transcript.

    MEASURED, never asked of the model, for the same reason the body's
    preview is: a model that had to restate the words could restate them
    wrongly, and the whole guarantee here is that the closer is speech
    the episode really contains. A CTA naming seconds nobody speaks in
    keeps its empty text and is refused by `validate_proposal`.
    """
    if cta is None:
        return None
    hits = _speech_within(bound_segments(transcript),
                          cta.timeline_start, cta.timeline_end)
    said = " ".join((h.get("text") or "").strip() for h in hits).strip()
    speakers = [h.get("speaker") for h in hits if h.get("speaker")]
    # The MEASUREMENT wins over anything already on the record. A CTA
    # whose range the captain moved keeps its old words and speaker
    # otherwise, which is a stale reading presented as a measured one.
    return replace(
        cta,
        text=said or cta.text,
        speaker=(speakers[0] if speakers else cta.speaker),
        straddling_within=tuple(straddling_within(
            cta.timeline_start, cta.timeline_end, transcript)))


# ── Persistence, which is also the captain's review surface ──────────

PROPOSAL_FORMAT = "reel_proposal/1"


def proposal_document(moments: Sequence[ReelMoment], transcript: dict) -> dict:
    derived = transcript.get("derived_from") or {}
    return {
        "format": PROPOSAL_FORMAT,
        "derived_from": derived,
        "instruction": (
            "The pipeline PROPOSES; the captain approves. Set each "
            "moment's \"approval\" to \"approved\" or \"rejected\" (and "
            "optionally an \"approval_note\"). Nothing is built for a "
            "moment left \"proposed\". A moment's \"call_to_action\" "
            "is the spoken closer it ends on and may name ANY seconds of "
            "the episode, including seconds another reel also closes on. "
            "Adjust any timecode freely, but note WHERE it is checked: "
            "the spans in this file were checked against the transcript "
            "when it was written, and reading it back does NOT re-run "
            "those checks. A closer overlapping its own body is refused "
            "again when the reel is BUILT; a body span you edit here is "
            "not re-checked at all, so move it with snap_to_speech or "
            "re-run the selector."),
        "moment_count": len(moments),
        "moments": [m.as_dict() for m in moments],
    }


PROPOSAL_FILENAME = "reel_proposals_v2.json"
"""The captain's review surface, and the file `build-reels` reads.

Spelled ONCE. It was spelled in `reel_build.rebuild_reels_in_project` as
a composed string and nowhere else, which meant the build read a plan
that nothing in this repository wrote: every batch of proposals reached
that path by hand. `write_from_step_output` is the writer.
"""


def proposal_path(project_folder) -> Path:
    from library.tools.project_layout import Area, ProjectLayout

    return (ProjectLayout(str(project_folder))
            .read_path(Area.REVIEW, PROPOSAL_FILENAME))


def write_from_step_output(project_folder, force: bool = False) -> Path:
    """Publish step 3.4's chosen moments as the captain's review file.

    The step writes `reel_selection` into its own output; the captain
    reviews `reel_proposals_v2.json`; `build-reels` reads that file back.
    Without this function the middle of that chain was a person copying
    JSON, and a plan that was never regenerated read exactly like one
    that was.

    REFUSES to overwrite a file the captain has already ruled on unless
    `force` is set. An approval is their answer and losing it silently
    would put a rejected moment back in front of the builder.
    """
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(str(project_folder))
    step_output = layout.step_dir("select_reels", "output.json")
    if not step_output.exists():
        raise ProposalError(
            f"{step_output} does not exist - step 3.4 has not run for "
            f"this project, so there is nothing to propose.")
    selection = (json.loads(step_output.read_text())
                 .get("reel_selection") or {})
    moments = [ReelMoment.from_dict(m) for m in (selection.get("moments") or [])]

    transcript_file = layout.read_path(
        Area.SCRATCH, "timeline_transcript", "transcript.json")
    transcript = json.loads(transcript_file.read_text())

    path = proposal_path(project_folder)
    if path.exists() and not force:
        existing = read_proposal(path)
        ruled = [m for m in existing if m.approval is not Approval.PROPOSED]
        if ruled:
            raise ProposalError(
                f"{path} already carries {len(ruled)} moment(s) the "
                f"captain has ruled on "
                f"({', '.join(m.slug for m in ruled)}). Overwriting would "
                f"discard their answer. Pass force=True only if that is "
                f"what is intended.")
    return write_proposal(path, moments, transcript)


def write_proposal(path, moments: Sequence[ReelMoment],
                   transcript: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(proposal_document(moments, transcript), indent=2),
        encoding="utf-8")
    return path


def read_proposal(path) -> List[ReelMoment]:
    """Load a proposal back, including whatever the captain decided."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != PROPOSAL_FORMAT:
        raise ProposalError(
            f"{path} is {data.get('format')!r}, not {PROPOSAL_FORMAT!r}")
    return [ReelMoment.from_dict(m) for m in data.get("moments", [])]


def render_for_review(moments: Sequence[ReelMoment]) -> str:
    """The proposal as the captain reads it: timecode, reason, one line."""
    def timecode(seconds: float) -> str:
        total = int(seconds)
        return f"{total // 60:02d}:{total % 60:02d}"

    lines = []
    for moment in moments:
        mark = {"approved": "[x]", "rejected": "[-]",
                "proposed": "[ ]"}[moment.approval.value]
        lines.append(
            f"{mark} {moment.timeline_name}")
        lines.append(
            f"      {timecode(moment.timeline_start)}-"
            f"{timecode(moment.timeline_end)} "
            f"({moment.duration:.0f}s)"
            + (f"  {', '.join(moment.speakers)}" if moment.speakers else ""))
        lines.append(f"      {moment.reason}")
        if moment.call_to_action:
            cta = moment.call_to_action
            lines.append(
                f"      closes on {timecode(cta.timeline_start)}-"
                f"{timecode(cta.timeline_end)} "
                f"({cta.duration:.0f}s"
                + (f", {cta.speaker}" if cta.speaker else "") + ")"
                + (f": \"{cta.text[:110]}"
                   f"{'...' if len(cta.text) > 110 else ''}\""
                   if cta.text else ""))
        if moment.transcript_preview:
            lines.append(f"      \"{moment.transcript_preview}\"")
        if moment.source_spans:
            sources = ", ".join(
                f"{Path(s['source_file']).name} [{s.get('source_start', 0.0):.1f}-{s.get('source_end', 0.0):.1f}s]"
                for s in moment.source_spans if s.get("source_file")
            )
            if sources:
                lines.append(f"      sources: {sources}")
        lines.append("")
    return "\n".join(lines)
