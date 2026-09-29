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

from library.tools.ren_refusal import RenRefusal

REEL_NAME_FORMAT = "Reel {number:02d} - {slug}"
"""The captain's format, quoted from their answer. Two digits, zero
padded, then the topic slug."""

_SNAP_PASSES = 12
"""How many times `snap_to_speech` may widen before giving up. Widening
pulls in neighbours that can themselves be partially covered, so it
iterates to a fixed point; this only bounds a pathological transcript."""

_BOUNDARY_EPSILON_SECONDS = 1e-6

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

class ProposalError(RenRefusal):
    """A proposal is not something that could be built."""


class NotApproved(RenRefusal):
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

    refused_take_groups: tuple = ()
    """Repetitions inside this moment that the builder will NOT cut,
    because cutting them would remove part of a take and leave the rest.

    Measured on the same span the builder reads, so the model that chose
    the span sees, while it can still redraw it, that the repetition it
    was told would be removed is staying in. Reel 03 is why: two of the
    three lines of one take were cut and the third was refused, so the
    orphaned tail led the reel. See `reel_build.redundant_runs`."""

    opening_observations: tuple = ()
    """What this reel's FIRST SECONDS point at that the reel does not
    contain, measured on the ranges the builder will actually play.

    The handoff states the hook rule and, until 2026-09-05, nothing
    measured whether a proposal obeyed it - so four of the captain's
    nineteen approved reels opened on a back-reference, a stumble or an
    answer to a question the viewer never heard. There is no score and
    nothing is rejected: this reports two facts a machine can see, and
    re-drawing the start is the model's decision.
    See library/tools/reel_opening.py."""

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

    closer_repeats: tuple = ()
    """What the closer repeats - of itself, and of the body it closes.

    Measured by `reel_build.closer_repeats` on the ranges the reel
    actually plays, so the model that chose the span sees, while it can
    still pick another closer, that the reel says those words twice.  A
    closer echoing the body may be a deliberate callback, so this
    reports and never refuses - same shape as `refused_take_groups`."""

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
        body["refused_take_groups"] = [dict(g) for g
                                       in self.refused_take_groups]
        body["straddling_within"] = [dict(x) for x in self.straddling_within]
        body["opening_observations"] = [dict(o) for o
                                        in self.opening_observations]
        body["call_to_action"] = (self.call_to_action.as_dict()
                                   if self.call_to_action else None)
        body["closer_repeats"] = [dict(r) for r in self.closer_repeats]
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
            refused_take_groups=tuple(
                dict(g) for g in (data.get("refused_take_groups") or ())),
            opening_observations=tuple(
                dict(o) for o in (data.get("opening_observations") or ())),
            straddling_within=tuple(dict(x) for x in
                                    (data.get("straddling_within") or ())),
            call_to_action=(CallToAction.from_dict(data["call_to_action"])
                            if data.get("call_to_action") else None),
            closer_repeats=tuple(
                dict(r) for r in (data.get("closer_repeats") or ())),
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
            + (f": {moment.approval_note}" if moment.approval_note else ""),
            "building it anyway would overrule the captain",
            "rule on a different moment, or ask the captain to "
            "re-consider this one - then build")
    raise NotApproved(
        f"reel {moment.number} ({moment.slug!r}) is still PROPOSED",
        "the captain has not looked at it. The pipeline proposes and "
        "the captain approves; nothing is built before that",
        "get the captain's approval on this reel first, then build")


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


_REEL_LABEL_RE = re.compile(r"^reel\s+(\d+)\b", re.IGNORECASE)
"""A render's timeline label naming a reel: `Reel 09 - slug (staging)`."""


def refuse_rejected_reel_timeline(timeline_label, project_folder) -> None:
    """Raise `NotApproved` when the label names a REJECTED reel, else return.

    The gate `assert_approved` with a live reading: the reel NUMBER is
    parsed off the render's own timeline label (`Reel 09 - ...`) and the
    verdict is read off `reel_proposals_v2.json` ON THIS CALL, never
    cached - the captain rules on reels while renders are in flight,
    and a gate holding yesterday's rejection is the same defect
    pointing the other way.

    Only REJECTED refuses. PROPOSED proceeds: staging builds (`rebuild
    staging` timelines) are how the captain REVIEWS a moment before
    approving it, so refusing unreviewed moments would refuse the
    review itself. A label naming no reel (the master timeline, an
    unnamed spine) proceeds, as does anything unreadable - a missing
    proposals file, an unparseable one, a number it does not list.
    A gate that fails correct output is worse than no gate (AGENTS.md
    10.4), so every one of those proceeds with its reason SAID on
    stderr rather than refusing work it cannot judge.
    """
    import sys  # noqa: PLC0415 - stderr notes only, no dependency

    label = str(timeline_label or "")
    match = _REEL_LABEL_RE.match(label.strip())
    if not match:
        return
    number = int(match.group(1))
    try:
        path = proposal_path(project_folder)
    except Exception as exc:  # noqa: BLE001 - default open, said aloud
        print(f"  approval gate: cannot locate the proposals file "
              f"({exc}) - proceeding without a verdict",
              file=sys.stderr)
        return
    if not path.is_file():
        print(f"  approval gate: no {PROPOSAL_FILENAME} for this "
              f"project - proceeding without a verdict",
              file=sys.stderr)
        return
    try:
        moments = read_proposal(path)
    except Exception as exc:  # noqa: BLE001 - default open, said aloud
        print(f"  approval gate: {path} cannot be read ({exc}) - "
              f"proceeding without a verdict", file=sys.stderr)
        return
    for moment in moments:
        if int(moment.number) == number:
            if moment.approval is Approval.REJECTED:
                raise NotApproved(
                    f"reel {moment.number} ({moment.slug!r}) was REJECTED"
                    + (f": {moment.approval_note}"
                       if moment.approval_note else ""),
                    "rendering its captions anyway would overrule the "
                    "captain",
                    "rule on a different moment, or ask the captain to "
                    "re-consider this one - then render")
            return
    print(f"  approval gate: reel {number} is not among the "
          f"{len(moments)} proposed moment(s) - proceeding without "
          f"a verdict", file=sys.stderr)


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
    contains. Non-empty means the reel opens or closes mid-sentence.
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
        cuts_start = s < start < e
        cuts_end = s < end < e
        if e > start and s < end and (cuts_start or cuts_end):
            cut.append(segment)
    return cut


def _word_intervals(transcript: dict) -> List[tuple]:
    """Every timed word interval in the transcript, bound or straddling.

    Untimed words (no usable start/end) cannot say whether a boundary
    lands inside them, so they are not offered as evidence that one did -
    the same rule F8's `_row_words` applies at the gate.
    """
    out = []
    for segment in transcript.get("segments") or ():
        for word in segment.get("words") or ():
            try:
                word_start = float(word["start"])
                word_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if word_end > word_start:
                out.append((word_start, word_end))
    return out


def _snap_out_of_words(start: float, end: float,
                       words: Sequence[tuple]) -> tuple:
    """Move each boundary out of any word interior, outward.

    A boundary strictly inside a word moves to the word's start (a
    start) or end (an end), so the reel opens and closes ON word edges
    rather than through a word.  Outward for the same reason the
    segment pass widens outward: moving inward drops a word the
    proposer meant to include.

    A boundary exactly ON a word edge is already clean and stays.

    Iterated to a fixed point: a word edge can itself sit inside ANOTHER
    overlapping word, and one pass would leave the boundary mid-word.
    Each move is strictly outward and lands on a word edge, of which
    there are finitely many, so the loop always settles.
    """
    for _ in range(len(words) + 1):
        moved = False
        for word_start, word_end in words:
            if word_start < start < word_end:
                start = word_start
                moved = True
            if word_start < end < word_end:
                end = word_end
                moved = True
        if not moved:
            break
    return start, end


def _widen_to_segments(start: float, end: float,
                       segments: Sequence[dict]) -> tuple:
    """One fixed-point segment pass: the loop `snap_to_speech` always ran.

    A boundary that CUTS a bound segment widens outward to cover it -
    extending adds only what was already being spoken across the line,
    while trimming inward would silently drop words.  A boundary that
    sits in clean silence is KEPT: pulling it back to the nearest
    segment edge deletes a tail breath the proposer meant (a switch-off
    animation's room, a word's last frame), and no segment is cut by
    keeping it, so there is nothing to widen to.  Measured 2026-09-11:
    a closer end at 342.03s in the silence after "The link's in our
    bio." (next speech 342.04s) was pulled back to 341.27s, playing the
    TV switch-off over her last words instead of after them.
    """
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
        new_start = start
        if any(float(x["timeline_start"]) < start < float(x["timeline_end"])
               for x in touching):
            new_start = min(float(x["timeline_start"]) for x in touching)
        new_end = end
        if any(float(x["timeline_start"]) < end < float(x["timeline_end"])
               for x in touching):
            new_end = max(float(x["timeline_end"]) for x in touching)
        if (new_start, new_end) == (start, end):
            return new_start, new_end
        start, end = new_start, new_end
    return start, end


def snap_to_speech(start: float, end: float, transcript: dict,
                   ) -> tuple:
    """Snap boundaries outward to transcript segment and word edges.

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
    words = _word_intervals(transcript)
    # Four phases, in this order, and the order is the contract.
    # Segments first: a boundary must never cut a bound row, and only
    # the segment pass knows rows.  Words second: a segment edge can
    # still sit inside a STRADDLING row's word - reel 5 of the rebuild
    # ended at 413.851s, Akshita's row end, through Craig's overlapping
    # 'about' (412.77-414.03s) - which the segment pass cannot see.
    # Segments a third time, because the word move can touch a bound
    # row the first pass did not reach; and words last, so the span the
    # proposal stores opens and closes on word edges.  The segment phases
    # settle by the existing fixed-point loop and the word phases by the
    # one above, so the sequence always settles.
    # A final span that still cuts a bound row is refused downstream by
    # `validate_proposal`. Other overlaps genuinely lack a reconciled
    # edge, so the proposal refuses them instead of guessing.
    start, end = _widen_to_segments(start, end, segments)
    start, end = _snap_out_of_words(start, end, words)
    start, end = _widen_to_segments(start, end, segments)
    start, end = _snap_out_of_words(start, end, words)
    return start, end


def _word_through(transcript: dict, when: float) -> Optional[str]:
    """The word spoken at `when`, or None.

    For the build-time repair report only: names the word a stored
    boundary sat inside, so the operator sees WHAT moved it.
    """
    for segment in transcript.get("segments") or ():
        for word in segment.get("words") or ():
            try:
                word_start = float(word["start"])
                word_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if word_end > word_start and word_start < when < word_end:
                return str(word.get("word", ""))
    return None


def _is_held_move(move: dict) -> bool:
    """A move that changes nothing and asks a human: a tail the pass
    abstained on, or a fully judged tail reported rather than
    applied. Both move nothing; both need a decision as loudly as a
    cascade does."""
    return bool(move.get("abstained", False) or move.get("reported", False))


def _tail_why(tail: dict) -> str:
    """The human sentence behind a stranded-tail move.

    An extension that finished a sentence must read differently from
    one that ran on, and a hold differently from both - a later reader
    checks the WHY, not just the new number. Every number quoted is
    measured from the kept range's own segment word timings.
    """
    verdict = (tail or {}).get("verdict", "")
    measured = (tail or {}).get("pace_from", "") == "closing segment"
    pace_note = (f"{tail.get('pace', 0.0):.2f}s (median word)"
                 if measured else
                 f"{tail.get('pace', 0.0):.2f}s "
                 f"({tail.get('pace_from', 'fallback')})")
    if verdict == "extend":
        quoted = " ".join(tail.get("tail_words", [])[:12])
        if len(tail.get("tail_words", [])) > 12:
            quoted += " ..."
        why = (
            f"finishes the {tail.get('kind', 'sentence-tail')} "
            f"({quoted!r}): the first stranded word starts "
            f"{tail.get('gap', 0.0):.2f}s past the bound, inside the "
            f"speaker's pace of {pace_note}, so the thought runs to "
            f"the sentence end at {tail.get('tail_end', 0.0):.2f}s")
        if tail.get("authorized_by"):
            why += (f" - applied under the recorded ruling "
                    f"({tail['authorized_by']}), which accepted the "
                    f"longer reel")
        return why
    if verdict == "hold":
        following = ""
        if tail.get("next_gap") is not None:
            following = (f"; the next speech starts "
                         f"{tail.get('next_gap', 0.0):.2f}s later, "
                         f"beyond the pace, so the reel still ends "
                         f"before it either way")
        return (
            f"approved bound stands: the final word "
            f"{tail.get('word', '')!r} is already covered with "
            f"{tail.get('remainder', 0.0):.2f}s of breath inside the "
            f"pace of {pace_note} - drift since "
            f"approval does not move approved spans{following}")
    if verdict == "report":
        quoted = " ".join(tail.get("tail_words", [])[:12])
        if len(tail.get("tail_words", [])) > 12:
            quoted += " ..."
        return (
            f"reports the severed {tail.get('kind', 'sentence-tail')} "
            f"({quoted!r}): it runs to {tail.get('tail_end', 0.0):.2f}s "
            f"- longer than the closing thought - so the span places "
            f"as approved and a human decides")
    return str((tail or {}).get("reason", "the tail cannot be judged"))


def snap_moment_to_speech(moment: ReelMoment,
                          transcript: dict,
                          tail_extend_authorizations=None) -> tuple:
    """Repair a STORED moment's boundaries at build time.

    The boundary drawer snaps every boundary OUT of word interiors when
    a proposal is GENERATED (`step_3_04_select_reels/post_bridge.py`),
    but the stored `reel_proposals_v2.json` predates that drawer and is
    read AS-IS at build time - so a stored boundary can still sit inside
    a word and stall the rebuild at the F8 gate.  This applies the SAME
    `snap_to_speech` to whatever proposal is read, on the way through:
    the body window and, where one is declared, the closing CTA range.

    Option (b) and deliberately not (a): it moves boundaries off word
    edges WITHOUT re-running selection, so WHICH moments the captain
    approved is untouched - only where each one opens and closes.  The
    file on disk is never rewritten; the repair lives on the in-memory
    moment the build and the verifier both consume.

    Returns `(repaired, moves)`.  A moment already clean returns ITSELF
    with no moves - the snap is a fixed point on word edges, so repair
    is idempotent and a fresh proposal passes through byte-identical in
    its spans.  `moves` names each repaired boundary, what it was, what
    it is now, and the word it sat inside, because a repair the operator
    cannot see is a silent content change.

    Before the word/segment phases, the body END takes the stranded-tail
    pass (`reel_build.repair_moment_tail`): an end that leaves whole
    kept words unplayed inside the speaker's own pace moves OUT to the
    sentence end first, and an end that already covers all but breath
    of the final word HOLDS - drift in word timings since approval does
    not move approved spans, only stranded whole words do.  A hold
    suppresses the word/segment phases for that boundary, so the held
    value is what the build places.  A tail the pass cannot judge is
    reported as an abstaining move (`was == now`, `abstained: True`)
    and the span is left for a human to redraw.  A tail that runs
    longer than the closing thought is likewise reported (`reported:
    True`) - unless `tail_extend_authorizations` (a `{reel_number:
    reason}` map, `library/tools/tail_extend_authorization.py`) names
    this moment's own number, in which case the recorded ruling
    applies the report as an extension and the finding, the WHY and
    the ledger all name whose decision that was.  A reel answers only
    its own entry; every other reel reports exactly as before.
    Every tail move
    carries `attribution` (`tail-extend`, `tail-hold`, `tail-abstain`)
    and its WHY, the pin-vs-snap provenance
    `pipeline_output/review/moment_boundary_repairs.json` is built
    from.  Closer ends stay snap-owned - recorded closer pins own
    closer starts, and no tail pass moves a closer end; body starts
    are the onset mirror this pass deliberately does not cover.
    """
    from library.tools.reel_build import repair_moment_tail
    from library.tools.tail_extend_authorization import (
        authorized_for as _authorized_for,
    )

    moves: List[dict] = []
    stored_start = float(moment.timeline_start)
    stored_end = float(moment.timeline_end)
    cta = getattr(moment, "call_to_action", None)
    later: List[tuple] = []
    if cta is not None:
        try:
            later.append((float(cta.timeline_start),
                          float(cta.timeline_end)))
        except (TypeError, ValueError):
            later = []
    authorized_entry = _authorized_for(tail_extend_authorizations,
                                       int(moment.number))
    authorized_by = str(authorized_entry.get("reason", "")
                        ) if authorized_entry else ""
    tailed_end, tail = repair_moment_tail(stored_end, stored_start,
                                          transcript, later=later,
                                          authorized_by=authorized_by)
    held_end: Optional[float] = None
    if tail is not None:
        verdict = tail.get("verdict", "")
        attribution = {"extend": "tail-extend",
                       "hold": "tail-hold",
                       "report": "tail-report"}.get(verdict, "tail-abstain")
        record = {"boundary": "body_end",
                  "was": stored_end, "now": tailed_end,
                  "through": _word_through(transcript, stored_end),
                  "attribution": attribution,
                  "why": _tail_why(tail),
                  "tail_kind": tail.get("kind", ""),
                  "tail_words": tail.get("tail_words", []),
                  "gap": tail.get("gap"),
                  "pace": tail.get("pace"),
                  "pace_from": tail.get("pace_from", "")}
        if verdict == "abstain":
            record["abstained"] = True
        if verdict == "report":
            record["reported"] = True
        if tail.get("authorized_by"):
            # The applied obedience names whose decision it was, and
            # the applied seconds are weighed against the recorded
            # ones before anything places them: an authorisation for
            # materially different seconds refuses here, not on the
            # timeline.
            from library.tools.tail_extend_authorization import (
                check_applied as _check_applied,
            )
            _check_applied(int(moment.number), authorized_entry,
                           tailed_end, tail.get("gap", 0.0))
            record["authorized_by"] = str(tail["authorized_by"])
        moves.append(record)
        if verdict == "hold":
            held_end = stored_end
    start, end = snap_to_speech(stored_start, tailed_end, transcript)
    if held_end is not None and end != held_end:
        # The hold overrules the phases for this boundary: the approved
        # edge stands.  Widening the end cannot move the start earlier
        # (a newly touched segment starts past the old end, so the
        # minimum over touched starts is unchanged), so reverting the
        # end leaves the start exactly as a held snap would compute it.
        end = held_end
        moves = [move for move in moves
                 if not (move.get("boundary") == "body_end"
                         and "attribution" not in move)]
    if start != stored_start:
        moves.append({"boundary": "body_start",
                      "was": stored_start, "now": start,
                      "through": _word_through(transcript, stored_start)})
    if end != tailed_end:
        moves.append({"boundary": "body_end",
                      "was": tailed_end, "now": end,
                      "through": _word_through(transcript, tailed_end)})
    new_cta = cta
    if cta is not None:
        cta_start, cta_end = snap_to_speech(float(cta.timeline_start),
                                            float(cta.timeline_end),
                                            transcript)
        if cta_start != float(cta.timeline_start):
            moves.append({"boundary": "cta_start",
                          "was": float(cta.timeline_start), "now": cta_start,
                          "through": _word_through(
                              transcript, float(cta.timeline_start))})
        if cta_end != float(cta.timeline_end):
            moves.append({"boundary": "cta_end",
                          "was": float(cta.timeline_end), "now": cta_end,
                          "through": _word_through(
                              transcript, float(cta.timeline_end))})
        if (cta_start, cta_end) != (float(cta.timeline_start),
                                    float(cta.timeline_end)):
            new_cta = replace(cta, timeline_start=cta_start,
                              timeline_end=cta_end)
    if not moves:
        return moment, moves
    return (replace(moment, timeline_start=start, timeline_end=end,
                    call_to_action=new_cta), moves)


SNAP_DECISION_SECONDS = 2.0
"""A snap move bigger than this needs a decision before the build.

`snap_to_speech` widens outward to whole segments in a fixed-point
loop, and transcript segments overlap by ASR jitter - so a snap can
walk from one segment into the next and keep going.  Measured on the
canary batch: one closer start moved 9.1s, pulling an unrelated
preamble into the reel and turning a ~40s reel into 61s, and another
body start moved -9.7s on the same mechanism.  The loop is correct
per word and wrong per discourse - it has no notion that the
proposal meant THIS sentence - so moves over about two seconds are
REPORTED, never re-decided here.  The preview below turns a
finished-timeline discovery into a line of output beforehand.
"""


def _transcript_word_list(transcript: dict) -> List[tuple]:
    """Every timed word as `(token, start, end)`, in transcript order.

    The same evidence `_word_intervals` reads, plus the token: the
    preview must name WHAT a move pulls in, not just how far it goes.
    Untimed words cannot place anything and are not listed.
    """
    out = []
    for segment in (transcript or {}).get("segments") or ():
        for word in segment.get("words") or ():
            try:
                word_start = float(word["start"])
                word_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            token = str(word.get("word") or "").strip()
            if token and word_end > word_start:
                out.append((token, word_start, word_end))
    return out


def _delta_words(transcript: dict, boundary: str, was: float,
                 now: float) -> tuple:
    """`(pulled_in, dropped)`: timed words the move covers, each
    `{"word", "start", "end"}` in transcript order.

    A word counts when it overlaps the newly covered (or uncovered)
    range even partly - a snap that lands mid-word would have kept
    walking, so anything touched is played.  The snap only ever
    widens, so in practice everything lands in `pulled_in`; the
    direction is still read off the move rather than assumed, so a
    narrowing repair would report drops instead of silence.
    """
    lo, hi = (was, now) if was <= now else (now, was)
    covered = [{"word": token, "start": start, "end": end}
               for token, start, end in _transcript_word_list(transcript)
               if start < hi and end > lo]
    if not covered:
        return [], []
    if boundary in ("body_start", "cta_start"):
        widened = now < was
    else:
        widened = now > was
    return (covered, []) if widened else ([], covered)


def _opening_words(transcript: dict, when: float, count: int = 6) -> str:
    """The words a span opens on at `when`, for pin phrases.

    The first `count` timed tokens starting at or after `when` - what
    the captain names when a flagged closer is pinned with
    `record-closer --anchor/--from`.  Empty where nothing timed is
    spoken there (silence), and the caller then says so instead of
    offering a pin command with no phrases in it.
    """
    tokens = [token for token, start, _ in
              _transcript_word_list(transcript)
              if start >= when - 1e-6][:count]
    return " ".join(tokens)


def preview_snap(moments: Sequence["ReelMoment"], transcript: dict,
                 threshold: float = SNAP_DECISION_SECONDS,
                 tail_extend_authorizations=None) -> dict:
    """Run `snap_moment_to_speech` over moments and report the moves.

    Read-only: nothing is repaired, rewritten or re-decided - the
    build keeps doing exactly what it does today.  Returns
    `{"threshold", "moments": [{"reel", "slug", "moves"}], "moved",
    "flagged"}` where each move carries `boundary`, `was`, `now`,
    `delta`, `through` (the word a stored boundary sat inside, as the
    build already reports), `pulled_in`/`dropped` word lists, and
    `needs_decision` - true when the boundary moves over `threshold`,
    or when the stranded-tail pass abstains and holds the span for a
    human. Tail moves also carry their `attribution` and WHY, so the
    preview agrees with the build on what was decided, not just how
    far a boundary moves. `tail_extend_authorizations` is the same
    map the build hands the snap
    (`library/tools/tail_extend_authorization.py`), so an authorised
    reel previews extended exactly as the build will place it.
    """
    entries = []
    moved = 0
    flagged = 0
    for moment in moments or []:
        _repaired, moves = snap_moment_to_speech(
            moment, transcript or {},
            tail_extend_authorizations=tail_extend_authorizations)
        move_reports = []
        for move in moves:
            was, now = float(move["was"]), float(move["now"])
            delta = now - was
            pulled, dropped = _delta_words(transcript or {},
                                           move["boundary"], was, now)
            abstained = _is_held_move(move)
            # A held span moves nothing, but it needs a decision as
            # loudly as a cascade does.
            needs = abs(delta) > threshold or abstained
            moved += 0 if abstained else 1
            flagged += 1 if needs else 0
            # The pin phrases travel with the report, so rendering
            # needs no transcript: what the ruled span opens on
            # (`anchor_phrase`) and what the snapped span opens on
            # (`snapped_phrase`) - the two phrases a closer pin is
            # recorded with.  Ends carry none; no pin kind moves one.
            anchor_phrase, snapped_phrase = "", ""
            if move["boundary"] in ("body_start", "cta_start"):
                anchor_phrase = _opening_words(transcript or {}, was)
                snapped_phrase = _opening_words(transcript or {}, now)
            move_reports.append({
                "boundary": move["boundary"],
                "was": was,
                "now": now,
                "delta": delta,
                "through": move.get("through"),
                "pulled_in": pulled,
                "dropped": dropped,
                "needs_decision": needs,
                "anchor_phrase": anchor_phrase,
                "snapped_phrase": snapped_phrase,
                # The stranded-tail provenance travels with the
                # report, so the beforehand preview and the build
                # agree on WHY - not just how far the boundary moves.
                "attribution": move.get("attribution", ""),
                "why": move.get("why", ""),
                "authorized_by": move.get("authorized_by", ""),
                "tail_kind": move.get("tail_kind", ""),
                "gap": move.get("gap"),
                "pace": move.get("pace"),
                "pace_from": move.get("pace_from", ""),
                "abstained": bool(move.get("abstained", False)),
                "reported": bool(move.get("reported", False)),
            })
        entries.append({"reel": int(moment.number),
                        "slug": moment.slug,
                        "moves": move_reports})
    return {"threshold": float(threshold),
            "moments": entries,
            "moved": moved,
            "flagged": flagged}


def _quote_words(words: Sequence[dict], limit: int = 90) -> str:
    """`"first ... last"` for a pulled-in word list, truncated."""
    text = " ".join(w["word"] for w in words)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " ..."


def decision_lines(number: int, move: dict, transcript: dict,
                   project_folder: str = "",
                   threshold: float = SNAP_DECISION_SECONDS) -> List[str]:
    """The NEEDS DECISION lines for one flagged repair move, else [].

    Pure: the build and the verifier print these beside their existing
    per-move lines, so a cascade is loud on the run that would have
    placed it - while a run with nothing flagged prints exactly what
    it prints today.  A flagged closer names its pin route
    (`record-closer` with the anchor/from phrases read off the
    transcript); a flagged body end the tail pass already extended
    points at its recorded WHY, and any other flagged boundary names
    the proposal as the place the decision lives.

    `move` is either a `preview_snap` report entry (carrying its
    phrases and verdict) or a raw `snap_moment_to_speech` move (the
    build loop's shape) - missing keys are derived from `transcript`
    so both callers read one spelling.
    """
    was, now = float(move["was"]), float(move["now"])
    if move.get("reported"):
        return [
            f"  Reel {number:02d}: {move['boundary']} NEEDS DECISION: "
            f"reported at {was:.3f}s - {move.get('why', '')}",
            f"    recorded: {move.get('why', '')} - see "
            f"pipeline_output/review/moment_boundary_repairs.json",
        ]
    if move.get("abstained"):
        return [
            f"  Reel {number:02d}: {move['boundary']} NEEDS DECISION: "
            f"held at {was:.3f}s - {move.get('why', '')}",
            f"    decide: redraw the approved proposal span - the tail "
            f"pass judged nothing and applied nothing",
        ]
    needs = bool(move.get("needs_decision",
                          abs(now - was) > threshold))
    if not needs:
        return []
    pulled = move.get("pulled_in") or []
    dropped = move.get("dropped") or []
    if not pulled and not dropped:
        pulled, dropped = _delta_words(transcript or {},
                                       move["boundary"], was, now)
    words = pulled or dropped
    verb = "pulls in" if pulled else "drops"
    quote = (f' - {verb} {len(words)} word(s): '
             f'"{_quote_words(words)}"' if words else "")
    lines = [
        f"  Reel {number:02d}: {move['boundary']} NEEDS DECISION: "
        f"{was:.3f}s -> {now:.3f}s ({now - was:+.3f}s){quote}",
    ]
    project = project_folder or "<project>"
    if move["boundary"] == "cta_start":
        anchor = move.get("anchor_phrase") or _opening_words(
            transcript or {}, was)
        opening = move.get("snapped_phrase") or _opening_words(
            transcript or {}, now)
        if anchor and opening:
            lines.append(
                f"    decide: python3 -m library.tools.captain_edits "
                f"{project} record-closer --anchor {anchor!r} "
                f"--from {opening!r} --reason "
                f"'snap preview: closer would open {now - was:+.1f}s "
                f"from the ruled opening'")
        else:
            lines.append(
                f"    decide: restate the closer opening in words "
                f"(ruled {was:.2f}s, snapped {now:.2f}s) - no readable "
                f"words at one end, so record-closer needs its "
                f"--anchor/--from by hand")
    else:
        if move.get("attribution") in ("tail-extend", "tail-hold",
                                           "tail-report"):
            lines.append(
                f"    recorded: {move.get('why', '')} - see "
                f"pipeline_output/review/moment_boundary_repairs.json")
        else:
            lines.append(
                f"    decide: adjust the approved proposal span - the file "
                f"keeps what the captain ruled on; the stranded-tail "
                f"pass declined (the gap outruns the speaker's pace), "
                f"so no repair extends the body further")
    return lines


def render_snap_preview(report: dict) -> str:
    """The preview as lines: one per move, loud where decided.

    A moment with no moves reads as one quiet line; a clean batch
    reads as one line total.  The flagged lines are the same
    `decision_lines` the build prints, so the beforehand report and
    the build agree word for word.
    """
    threshold = float(report.get("threshold", SNAP_DECISION_SECONDS))
    moments = report.get("moments") or []
    lines = [
        f"snap preview: {len(moments)} moment(s), "
        f"{report.get('moved', 0)} boundar(y/ies) would move, "
        f"{report.get('flagged', 0)} need(s) a decision "
        f"(over {threshold:.1f}s)",
    ]
    for entry in moments:
        for move in entry.get("moves") or []:
            was, now = float(move["was"]), float(move["now"])
            if move.get("reported"):
                lines.append(
                    f"  Reel {entry['reel']:02d}: {move['boundary']} "
                    f"REPORTED at {was:.3f}s - "
                    f"{move.get('why', '')}")
            elif move.get("abstained"):
                lines.append(
                    f"  Reel {entry['reel']:02d}: {move['boundary']} "
                    f"HELD FOR DECISION at {was:.3f}s - "
                    f"{move.get('why', '')}")
            else:
                lines.append(
                    f"  Reel {entry['reel']:02d}: {move['boundary']} "
                    f"{was:.3f}s -> {now:.3f}s ({now - was:+.3f}s)"
                    + (" NEEDS DECISION" if move.get("needs_decision")
                       else ""))
            lines.extend(decision_lines(entry["reel"], move, {},
                                        "", threshold))
    if not report.get("moved"):
        lines.append("  no boundary moves - the build places the "
                     "stored spans as ruled.")
    return "\n".join(lines)


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
                           timeline_duration: float, label: str,
                           pinned: bool = False) -> None:
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

    `pinned` is a closer the captain's recorded pin just redrew
    (`captain_edits.apply_closer_redraws`, applied before this check):
    the pin may open mid-ROW on a clean timed-word edge, where the
    row is WhisperX's chunking and the captain ruled the words twice.
    The whole-segments refusal is skipped for exactly that span - and
    only where both edges still open on timed-word edges, re-verified
    here, so a re-transcription between the pin and this check still
    refuses rather than shipping a half-word. Applying the pin is
    obedience, not re-decision; refusing it here would make a recorded
    decision break every future regeneration.
    """
    cta = moment.call_to_action
    if cta is None:
        return
    if cta.timeline_end <= cta.timeline_start:
        raise ProposalError(
            f"{label}: its call to action runs {cta.timeline_start} to "
            f"{cta.timeline_end}, which is not a range",
            "a closer that ends before it starts cannot be placed",
            "propose a call to action whose end is after its start")
    if cta.duration <= MIN_CTA_SECONDS:
        raise ProposalError(
            f"{label}: its call to action runs {cta.duration:.3f}s, under "
            f"a frame",
            "nothing can be placed from it, so a range this short is a "
            "mistyped timecode rather than a closer",
            "re-check the timecode and propose the full closer span")
    if cta.timeline_start < 0 or cta.timeline_end > timeline_duration + 0.001:
        raise ProposalError(
            f"{label}: its call to action runs {cta.timeline_start:.2f}-"
            f"{cta.timeline_end:.2f}s, outside the timeline's "
            f"0-{timeline_duration:.2f}s",
            "a closer outside the episode cannot be cut from it",
            "move the call to action inside the timeline's "
            f"0-{timeline_duration:.2f}s")

    segments = transcript.get("segments") or []
    if segments and not _speech_within(segments, cta.timeline_start,
                                       cta.timeline_end):
        raise ProposalError(
            f"{label}: its call to action runs {cta.timeline_start:.2f}-"
            f"{cta.timeline_end:.2f}s, where the transcript measured no "
            f"speech at all",
            "a CTA must be genuinely SPOKEN in the episode - it is never "
            "authored, templated or padded",
            "move the call to action onto speech the transcript measured")

    cut = partial_overlaps(cta.timeline_start, cta.timeline_end, transcript)
    if cut:
        first = cut[0]
        if pinned:
            from library.tools import captain_edits as _edits
            if (_edits._opens_on_word_edge(cta.timeline_start,
                                           transcript, edge="start")
                    and _edits._opens_on_word_edge(cta.timeline_end,
                                                   transcript,
                                                   edge="end")):
                cut = []
        if cut:
            raise ProposalError(
                f"{label}: its call to action cuts {len(cut)} segment(s) "
                f"rather than containing them, so the reel would close "
                f"mid-sentence. First: [{first['timeline_start']:.2f}-"
                f"{first['timeline_end']:.2f}s] "
                f"{first.get('text', '')[:60]!r}",
                "a closer must contain whole segments",
                "use `snap_to_speech` to move the boundaries out to whole "
                "segments")

    overlap_start = max(cta.timeline_start, moment.timeline_start)
    overlap_end = min(cta.timeline_end, moment.timeline_end)
    if overlap_end > overlap_start:
        raise ProposalError(
            f"{label}: its call to action ({cta.timeline_start:.2f}-"
            f"{cta.timeline_end:.2f}s) overlaps its own body "
            f"({moment.timeline_start:.2f}-{moment.timeline_end:.2f}s) by "
            f"{overlap_end - overlap_start:.2f}s, so the reel would play "
            f"those seconds twice",
            "a CTA already inside the body needs no second range",
            "drop the call to action, or move it to a range that is "
            "elsewhere")


def validate_proposal(moments: Sequence[ReelMoment],
                      transcript: dict,
                      timeline_duration: float,
                      pinned_cta_reels: frozenset = frozenset()) -> None:
    """Every moment names a real, non-empty span containing real speech.

    This is what stops a model inventing a timecode. A proposal is taste
    about WHICH moment matters; it is not licence to claim seconds that
    do not exist or that nobody speaks in.

    `pinned_cta_reels` are reel numbers whose closer a recorded
    captain's pin just redrew (`captain_edits.apply_closer_redraws`,
    before this check): their whole-segments refusal is the pin's own
    word-edge guarantee instead. Every other reel - and every body
    span, pinned or not - is checked exactly as before.
    """
    if not moments:
        raise ProposalError(
            "no moments proposed",
            "an empty proposal is the absence of a suggestion, not a "
            "suggestion of nothing",
            "propose at least one moment with a reason")

    segments = transcript.get("segments") or []
    seen_numbers = set()
    for moment in moments:
        label = f"reel {moment.number} ({moment.slug!r})"
        if moment.number in seen_numbers:
            raise ProposalError(
                f"{label}: reel number {moment.number} is used twice",
                "two reels cannot share a number",
                "renumber so every reel has its own number")
        seen_numbers.add(moment.number)
        if moment.number < 1:
            raise ProposalError(
                f"{label}: reel numbers start at 1",
                "reel 0 and negatives name nothing",
                "number the reels from 1")
        if not moment.reason.strip():
            raise ProposalError(
                f"{label} carries no reason",
                "the captain asked for a one-line reason each; a moment "
                "that cannot say why it was picked cannot be judged",
                "write one line as the reason for this moment")
        if moment.timeline_end <= moment.timeline_start:
            raise ProposalError(
                f"{label} runs {moment.timeline_start} to "
                f"{moment.timeline_end}, which is not a range",
                "a moment that ends before it starts cannot be cut",
                "propose a span whose end is after its start")
        if moment.timeline_start < 0 or moment.timeline_end > timeline_duration + 0.001:
            raise ProposalError(
                f"{label} runs {moment.timeline_start:.2f}-"
                f"{moment.timeline_end:.2f}s, outside the timeline's "
                f"0-{timeline_duration:.2f}s",
                "a moment outside the episode cannot be cut from it",
                "move the moment inside the timeline's "
                f"0-{timeline_duration:.2f}s")
        if moment.duration < MIN_REEL_SECONDS:
            raise ProposalError(
                f"{label} is {moment.duration:.2f}s, under the "
                f"{MIN_REEL_SECONDS}s floor",
                "too short to contain the speech it claims",
                "widen the span past the floor, or drop the moment")
        if segments and not _speech_within(segments, moment.timeline_start,
                                           moment.timeline_end):
            raise ProposalError(
                f"{label} runs {moment.timeline_start:.2f}-"
                f"{moment.timeline_end:.2f}s, where the transcript "
                f"measured no speech at all",
                "a moment nobody speaks in is an invented timecode",
                "move the moment onto speech the transcript measured")
        cut = partial_overlaps(moment.timeline_start, moment.timeline_end,
                               transcript)
        if cut:
            first = cut[0]
            raise ProposalError(
                f"{label} cuts {len(cut)} segment(s) rather than "
                f"containing them - it would open or close mid-sentence. "
                f"First: [{first['timeline_start']:.2f}-"
                f"{first['timeline_end']:.2f}s] "
                f"{first.get('text', '')[:60]!r}",
                "a reel must open and close on whole segments",
                "use `snap_to_speech` to move the boundaries out to whole "
                "segments")
        midword = _midword_keep_edges_for(moment, transcript)
        if midword:
            first = midword[0]
            raise ProposalError(
                f"{label} would keep an edge at {first['edge']:.2f}s "
                f"through the word {first['word']!r} "
                f"({first['word_start']:.2f}-{first['word_end']:.2f}s), "
                f"once its repeated takes are cut out of it - the reel "
                f"would play that word cut in half and then jump",
                "a repair in either direction changes content: widening "
                "reinstates part of a take the cutter dropped, "
                "narrowing drops more speech it kept",
                "redraw the span past the take instead - no snap can do "
                "this one")
        _check_call_to_action(moment, transcript, timeline_duration, label,
                              pinned=(moment.number in pinned_cta_reels))

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

def is_conversation(moment: ReelMoment, transcript: dict,
                    min_speakers: int = 2) -> Optional[str]:
    """None if the moment meets the project's speaker count, else a reason.

    A single-speaker moment on a two-speaker project is a bad PICK, not
    an integrity failure. validate_proposal raises on integrity; this
    returns a reason for post_bridge to drop the moment and report it.

    `min_speakers` is the PROJECT's declared count (select_reels reads
    it off `source.speakers`): 2 is the historical two-hander - the
    captain, rejecting the first ten reels: 'its mostly just a single
    person yapping and not really a convo' - 1 is a monologue, where
    only a moment nobody speaks in fails, and 0 passes everything with
    speech (validate_proposal still refuses an invented timecode).
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
    if len(real_speakers) < min_speakers:
        found = ", ".join(f"{s} ({d:.1f}s)" for s, d in
                          sorted(turn_by_speaker.items(),
                                 key=lambda x: -x[1]))
        if min_speakers <= 1:
            return (
                f"has no speaker with real turns "
                f"(>= {MIN_TURN_SECONDS}s each): {found or 'none'}. "
                f"A moment nobody speaks in is an invented timecode."
            )
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
                   opening_observations=tuple(
                       opening_for(moment, transcript, preview)),
                    duplicate_takes=tuple(duplicate_takes(
                        moment.timeline_start, moment.timeline_end,
                        transcript)),
                    refused_take_groups=tuple(refused_for(moment, transcript)),
                    closer_repeats=tuple(closer_for(moment, transcript)))


def _midword_keep_edges_for(moment: ReelMoment,
                            transcript: dict) -> list:
    """Interior cut edges through words, measured for the plan gate.

    The cutter (`reel_build.redundant_takes` + `keep_ranges`) draws the
    edges the snap never touches, so the plan asks what they cut through
    before it is accepted - the same reason `refused_for` measures what
    the builder will leave in while the span can still be redrawn. A
    lazy import, like `refused_for` and `opening_for`: the builder
    imports this module back.
    """
    from library.tools.reel_build import midword_keep_edges
    return midword_keep_edges(moment.timeline_start, moment.timeline_end,
                              transcript)


def refused_for(moment, transcript: dict) -> list:
    """Repetitions the builder will leave in this moment, and why.

    Measured here for the same reason `opening_observations` is: what a
    viewer hears is the span plus every bad take the builder removes,
    and a take it will NOT remove is as much a fact about the reel as
    one it will.  The model is reached at SELECTION time and not at
    build time, so this is where a "the repetition is staying in unless
    you move the boundary" can still be acted on.
    """
    from library.tools.reel_build import refused_take_groups
    return refused_take_groups(moment.timeline_start, moment.timeline_end,
                               transcript)


def closer_for(moment, transcript: dict) -> list:
    """What this moment's closer repeats, measured for the plan.

    The closer comes from anywhere in the episode and is placed whole,
    so a closer echoing the body is the reel playing those words twice -
    and the model that picked the closer is the one that can still pick
    another.  A lazy import, like `refused_for`: the builder imports
    this module back.
    """
    from library.tools.reel_build import closer_repeats
    return closer_repeats(moment, transcript)


def opening_for(moment, transcript: dict, preview: str) -> list:
    """What this moment's opening points at, on the ranges that PLAY.

    Measured here rather than asked of the model, for the same reason
    `transcript_preview` and the CTA's own words are: the model named a
    span, and what a viewer hears in its first seconds is a consequence
    of that span plus every bad take the builder removes - which is how
    a moment whose setup is cut ends up opening on two words.
    """
    from library.tools.reel_opening import for_moment
    return for_moment(moment, transcript, preview)


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
    from library.tools.project_layout import ProjectLayout

    layout = ProjectLayout(str(project_folder))
    step_output = layout.step_dir("select_reels", "output.json")
    if not step_output.exists():
        raise ProposalError(
            f"{step_output} does not exist",
            "step 3.4 has not run for this project, so there is nothing "
            "to propose",
            "run the pipeline through select_reels first, then propose")
    selection = (json.loads(step_output.read_text())
                 .get("reel_selection") or {})
    moments = [ReelMoment.from_dict(m) for m in (selection.get("moments") or [])]

    from library.tools.timeline_transcript import transcript_path
    transcript_file = transcript_path(project_folder)
    transcript = json.loads(transcript_file.read_text())

    path = proposal_path(project_folder)
    if path.exists() and not force:
        existing = read_proposal(path)
        ruled = [m for m in existing if m.approval is not Approval.PROPOSED]
        if ruled:
            raise ProposalError(
                f"{path} already carries {len(ruled)} moment(s) the "
                f"captain has ruled on "
                f"({', '.join(m.slug for m in ruled)})",
                "overwriting would discard their answer",
                "pass force=True only if discarding the ruling is what "
                "is intended - or run `ren propose` with --force")
    return write_proposal(path, moments, transcript)


def write_proposal(path, moments: Sequence[ReelMoment],
                   transcript: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Canonical spelling (library/tools/stable_json.py): the proposal is
    # a declaration the captain rules on and a variant merge reads, so
    # the same moments are the same bytes.
    from library.tools.stable_json import dumps_stable
    path.write_text(
        dumps_stable(proposal_document(moments, transcript)),
        encoding="utf-8")
    return path


def read_proposal(path) -> List[ReelMoment]:
    """Load a proposal back, including whatever the captain decided."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != PROPOSAL_FORMAT:
        raise ProposalError(
            f"{path} is {data.get('format')!r}, not {PROPOSAL_FORMAT!r}",
            "a foreign document is not a reel proposal",
            "point at the proposal file this pipeline published "
            "(`ren propose`), not another document")
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


def main(argv=None) -> int:
    """`python3 -m library.tools.reel_proposal preview-snap <project>`.

    The pre-build report: runs `snap_moment_to_speech` over the
    stored proposal and prints how far each boundary would move and
    what words that pulls in or drops, flagging moves over
    `--threshold` (default 2.0s) as needing a decision.  A report,
    never a gate: it always exits 0 once printed, and the build
    repairs exactly as before.  Missing proposal or transcript
    refuses (exit 2) rather than previewing nothing.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="library.tools.reel_proposal",
        description="Preview the build-time boundary snap before "
                    "building: how far each stored boundary would move, "
                    "what words that pulls in, and what needs a decision.")
    parser.add_argument("project_folder")
    parser.add_argument("--threshold", type=float,
                        default=SNAP_DECISION_SECONDS)
    args = parser.parse_args(
        list(sys.argv[1:] if argv is None else argv))
    try:
        moments = read_proposal(str(proposal_path(args.project_folder)))
    except (OSError, ValueError, ProposalError) as exc:
        print(f"REFUSED: no readable reel proposal: {exc}", file=sys.stderr)
        return 2
    try:
        from library.tools.timeline_transcript import transcript_path
        transcript_file = transcript_path(args.project_folder)
        transcript = json.loads(Path(transcript_file).read_text(
            encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"REFUSED: no readable transcript: {exc}", file=sys.stderr)
        return 2
    try:
        from library.tools.tail_extend_authorization import (
            AuthorizationError,
            load_authorizations,
        )
        authorizations = load_authorizations(args.project_folder)
    except AuthorizationError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(render_snap_preview(
        preview_snap(moments, transcript, args.threshold,
                     tail_extend_authorizations=authorizations)))
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
