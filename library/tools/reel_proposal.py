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


class ProposalError(ValueError):
    """A proposal is not something that could be built."""


class NotApproved(RuntimeError):
    """A build path was handed a moment the captain has not approved."""


class Approval(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


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

    @property
    def duration(self) -> float:
        return self.timeline_end - self.timeline_start

    @property
    def timeline_name(self) -> str:
        return reel_timeline_name(self.number, self.slug)

    def as_dict(self) -> dict:
        body = asdict(self)
        body["approval"] = self.approval.value
        body["speakers"] = list(self.speakers)
        body["source_spans"] = [dict(s) for s in self.source_spans]
        body["duplicate_takes"] = [dict(d) for d in self.duplicate_takes]
        body["has_duplicate_take"] = bool(self.duplicate_takes)
        body["timeline_name"] = self.timeline_name
        body["duration_seconds"] = round(self.duration, 3)
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
    cut = []
    for segment in bound_segments(transcript):
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

    Straddling segments are ignored as anchors; a span that touches only
    those comes back unchanged and `validate_proposal` refuses it.
    """
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
    if len(preview) > 400:
        preview = preview[:397].rstrip() + "..."
    return replace(moment, speakers=tuple(speakers),
                   transcript_preview=preview, source_spans=tuple(spans),
                   duplicate_takes=tuple(duplicate_takes(
                       moment.timeline_start, moment.timeline_end,
                       transcript)))


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
            "moment left \"proposed\". Adjust timeline_start/end freely - "
            "they are re-checked against the transcript on load."),
        "moment_count": len(moments),
        "moments": [m.as_dict() for m in moments],
    }


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
        if moment.transcript_preview:
            preview = moment.transcript_preview
            lines.append(f"      \"{preview[:150]}"
                         f"{'...' if len(preview) > 150 else ''}\"")
        lines.append("")
    return "\n".join(lines)
