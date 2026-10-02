"""A reel is a CONVERSATIONAL EXCHANGE, and this module measures them.

What a reel is
--------------
Learned from the captain's own worked example, after a first batch of ten
was rejected as *"mostly just a single person yapping and not really a
convo"*.  They named 0:00-3:13 of the field-test timeline as deliberately
planned to become "a reel or two".  Collapsed into turns it is twelve:

    Craig frames the question           21.4s
    Akshita answers                     19.5s   \\  the definition
    Akshita starts an example            5.3s   /   exchange
    Craig RE-ASKS the same question     22.2s   \\  the same exchange
    Akshita RE-ANSWERS, twice      9.5 + 20.4s  /   recorded again
    Craig "so give me an example"        0.9s   \\
    Akshita's audit example             31.7s    |  the example
    Craig reacts, "keyword stuffing"     5.3s    |  exchange
    Akshita's takeaway                  17.7s   /
    Craig pitches the Lucy system       11.0s      a CTA

One definition exchange recorded TWICE, one example exchange, and a
pitch.  That is what "a reel or two" means, and it gives the unit:

    **Craig frames or asks, Akshita answers, optionally Craig reacts and
    Akshita lands the takeaway.  The unit is the EXCHANGE, not the best
    line in it.**

Nothing here is scored
----------------------
Choosing which conversation is worth cutting is taste and belongs to a
model (AGENTS.md 10.5).  This module measures structure and REPORTS
concerns; it never ranks, never filters silently, and never drops a
window.  `exchange_windows` returns every window it found, each carrying
its measurements and a list of `concerns` naming what a reader should
look at.  A caller that wants only clean ones filters them itself, and
can always say what left.

That is deliberate: the first batch failed because a selector's judgement
was invisible, and a funnel that discards 31 of 49 windows without saying
why is the same defect wearing a different hat.

Retakes at the EXCHANGE level
-----------------------------
`library/tools/reel_proposal.duplicate_takes` finds a repeated LINE
inside one moment.  Whole exchanges repeat too - the definition exchange
above is recorded twice across a minute - so offering both as separate
reels hands the captain one conversation as two.

Similarity is CONTAINMENT, not Jaccard, and that is measured rather than
preferred: two takes of one exchange differ in length because the second
is usually more complete, and Jaccard punishes exactly that.  On the
known pair, containment reads 0.597 against 0.302 and 0.177 for
unrelated neighbours; Jaccard reads 0.394 against 0.168 and 0.096, a real
gap but a narrower one on smaller numbers.

Two bands, the same shape the line-level detector uses and for the same
reason - the boundary is REPORTED, not decided.

`tests/unit/reels/test_reel_exchange.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import List, Optional, Sequence, Tuple

TURN_GAP_SECONDS = 2.5
"""Two segments from one speaker closer than this are one turn. Longer
and the speaker paused enough that the other could have come in."""

LENGTH_GUIDANCE = (45.0, 90.0)
"""The captain's brief - "between 45-90 seconds and on average a minute
or under" - as GUIDANCE THE MODEL WEIGHS, never a boundary this module
enforces.

It was a hard window until 2026-09-04, and that silently withheld every
stretch needing longer to finish. A story that runs 95 seconds because
that is how long it takes to deliver something and close it is a real
answer; a story cut at 90 is not. Candidates are reported with their
length whichever side of the guidance they fall."""

MIN_REEL_SECONDS, MAX_REEL_SECONDS = LENGTH_GUIDANCE
"""Kept as names because the guidance is quoted in the prompt."""

ABSURD_SECONDS = 300.0
"""The only length bound left, and it is mechanical rather than
editorial: past five minutes a "candidate" is most of the episode and
carries no information for a chooser."""

SAME_EXCHANGE = 0.55
POSSIBLE_RETAKE = 0.40
"""Containment bands for "these two windows are the same conversation".
See the module docstring for the measurement behind them."""

CRAIG_SHARE_CONCERN = 0.25
MIN_ALTERNATIONS_WHEN_ONE_SIDED = 3
"""Thresholds for RAISING A CONCERN, never for dropping a window.

Measured against windows classifiable by reading: a 23%-pitch window is
Craig selling the Lucy system rather than talking about GEO, and a window
where Craig holds under a quarter of the time AND contributes only once
is a monologue with a prompt attached. Alternation count alone
discriminates nothing - good windows measured 1 and 3, and so did a bad
one."""

_PITCH = re.compile(
    r"\b(lucy visibility|visibility score|link'?s? in (our|the) bio|"
    r"check it out|send us a dm|go to our website|our website|"
    r"we'?ve been building|definitely check)\b", re.I)

_ASK = re.compile(
    r"\b(what|why|how|who|when|where|give me an example|tell us|explain|"
    r"walk me|talk to me|is it|does it|can you|what'?s|what are|"
    r"should be doing)\b", re.I)


@dataclass(frozen=True)
class Turn:
    """One speaker's uninterrupted contribution."""

    speaker: Optional[str]
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def asks(self) -> bool:
        return bool(_ASK.search(self.text))

    @property
    def pitches(self) -> bool:
        return bool(_PITCH.search(self.text))


@dataclass(frozen=True)
class Exchange:
    """A candidate reel: a run of turns, measured and never scored."""

    start: float
    end: float
    turns: Tuple[Turn, ...]
    concerns: Tuple[str, ...] = ()
    retake_of: Optional[float] = None
    retake_band: str = ""
    retake_containment: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def speakers(self) -> List[str]:
        seen = []
        for turn in self.turns:
            if turn.speaker and turn.speaker not in seen:
                seen.append(turn.speaker)
        return seen

    @property
    def alternations(self) -> int:
        return sum(1 for a, b in zip(self.turns, self.turns[1:])
                   if a.speaker != b.speaker)

    def seconds_for(self, speaker: str) -> float:
        return sum(t.duration for t in self.turns if t.speaker == speaker)

    def share_for(self, speaker: str) -> float:
        return self.seconds_for(speaker) / self.duration if self.duration else 0.0

    @property
    def pitch_share(self) -> float:
        return (sum(t.duration for t in self.turns if t.pitches)
                / self.duration if self.duration else 0.0)

    @property
    def question_turns(self) -> int:
        return sum(1 for t in self.turns if t.asks)

    def measurements(self, lead: str = "") -> dict:
        body = {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration_seconds": round(self.duration, 1),
            "turns": len(self.turns),
            "alternations": self.alternations,
            "speakers": self.speakers,
            "seconds_by_speaker": {s: round(self.seconds_for(s), 1)
                                   for s in self.speakers},
            "share_by_speaker": {s: round(self.share_for(s), 3)
                                 for s in self.speakers},
            "question_turns": self.question_turns,
            "pitch_share": round(self.pitch_share, 3),
            "closes_on_pitch": bool(self.turns and self.turns[-1].pitches),
            "within_length_guidance": (
                LENGTH_GUIDANCE[0] <= self.duration <= LENGTH_GUIDANCE[1]),
            "concerns": list(self.concerns),
        }
        if self.retake_of is not None:
            body["retake_of"] = round(self.retake_of, 2)
            body["retake_band"] = self.retake_band
            body["retake_containment"] = round(self.retake_containment, 3)
        if lead:
            body["lead_share"] = round(self.share_for(lead), 3)
        return body


# ── Turns ────────────────────────────────────────────────────────────

def turns_from_transcript(transcript: dict,
                          gap: float = TURN_GAP_SECONDS) -> List[Turn]:
    """Bound segments collapsed into turns, in timeline order.

    Straddling segments are excluded for the reason
    `reel_proposal.bound_segments` gives: they have no single ground
    truth and are often WhisperX bridging a silent gap.
    """
    from library.tools.reel_proposal import bound_segments

    segments = sorted(bound_segments(transcript),
                      key=lambda s: s["timeline_start"])
    turns: List[dict] = []
    for segment in segments:
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        if (turns and turns[-1]["speaker"] == segment.get("speaker")
                and segment["timeline_start"] - turns[-1]["end"] < gap):
            turns[-1]["end"] = float(segment["timeline_end"])
            turns[-1]["text"] += " " + text
        else:
            turns.append({"speaker": segment.get("speaker"),
                          "start": float(segment["timeline_start"]),
                          "end": float(segment["timeline_end"]),
                          "text": text})
    return [Turn(**t) for t in turns]


# ── Candidate exchanges ──────────────────────────────────────────────

def _concerns_for(exchange: Exchange, lead: str, answerer: str) -> List[str]:
    """What a reader should look at. Never a verdict, never a filter."""
    out = []
    # Pitch is REPORTED in `measurements`, never raised here. The
    # captain's format is "an atomic segment of conversation that
    # provides value and then makes a little CTA at the end" - so a
    # closing pitch is the ENDING, not a defect. Treating it as one
    # discarded ten candidates and removed the thing the format ends on.
    if (exchange.share_for(lead) < CRAIG_SHARE_CONCERN
            and exchange.alternations < MIN_ALTERNATIONS_WHEN_ONE_SIDED):
        out.append(
            f"{lead} holds {exchange.share_for(lead):.0%} and speaks once - "
            f"a monologue with a prompt attached rather than an exchange")
    if not exchange.question_turns:
        out.append("nobody asks anything - no question opens it")
    # What the opening POINTS AT, which the share and alternation numbers
    # cannot see. The handoff states the hook rule - "not throat-clearing,
    # not a speaker settling into a sentence" - and until 2026-09-05
    # nothing measured whether a window obeyed it, so four of nineteen
    # approved reels opened on exactly what it forbids. Reported here
    # beside the other concerns because that is what a concern IS: what a
    # reader should look at, never a verdict and never a filter.
    # See library/tools/reel_opening.py.
    from library.tools.reel_opening import concern_lines, observations
    first = exchange.turns[0] if exchange.turns else None
    if first is not None:
        out.extend(concern_lines(observations(
            [{"word": w} for w in str(first.text).split()[:24]],
            " ".join(str(t.text) for t in exchange.turns))))
    if answerer not in exchange.speakers:
        out.append(f"{answerer} never speaks")
    return out


def exchange_windows(turns: Sequence[Turn], lead: str, answerer: str,
                     minimum: float = LENGTH_GUIDANCE[0],
                     maximum: float = ABSURD_SECONDS,
                     require_lead_opens: bool = True) -> List[Exchange]:
    """Every candidate window, measured. NOTHING is dropped here.

    **The CEILING is what was withholding stories, and it is gone.** A
    window grows until the exchange is at least `minimum` long and is
    bounded only by `ABSURD_SECONDS`, so a story needing 95 seconds to
    deliver something and close on it is offered with its length rather
    than truncated at 90. `within_length_guidance` reports which side of
    the captain's 45-90s brief each candidate falls, and the MODEL
    weighs it.

    `minimum` still starts at the guidance floor: a window shorter than
    that has not finished being an exchange, and lowering it made the
    generator stop at the first qualifying turn, cutting the closing CTA
    off the end - the same truncation from the other direction.

    A window opens on a `lead` turn when `require_lead_opens` - the
    captain's exchanges start with Craig's question. That is enforced on
    thin evidence (one three-minute span), so it is a FLAG rather than a
    constant: pass False and the opener becomes a concern instead of a
    precondition.
    """
    out: List[Exchange] = []
    for i, turn in enumerate(turns):
        if require_lead_opens and turn.speaker != lead:
            continue
        for j in range(i + 1, len(turns)):
            span = turns[j].end - turn.start
            if span < minimum:
                continue
            if span > maximum:
                break
            run = tuple(turns[i:j + 1])
            if len({t.speaker for t in run if t.speaker}) < 2:
                continue
            candidate = Exchange(start=turn.start, end=turns[j].end, turns=run)
            out.append(Exchange(
                start=candidate.start, end=candidate.end, turns=run,
                concerns=tuple(_concerns_for(candidate, lead, answerer))))
            break
    return out


def monologue_windows(turns: Sequence[Turn], speaker: str,
                      minimum: float = LENGTH_GUIDANCE[0],
                      maximum: float = ABSURD_SECONDS) -> List[Exchange]:
    """Every candidate window of a ONE-speaker project, measured.

    The monologue path: a project declaring a single speaker has no
    exchange structure - no lead to open, no answerer to weigh, no
    alternations to count - so `exchange_windows` offers it nothing
    (its two-speaker requirement skips every run). A window here is a
    run of turns grown until the exchange is at least `minimum` long
    and bounded by `ABSURD_SECONDS`, the same shape minus the
    conversation. Measurements are the same table; `alternations` is
    0 and the speaker shares are 1.0 by construction, which is what
    tells a reader this candidate was cut on delivery and close
    rather than on exchange.
    """
    out: List[Exchange] = []
    own = [t for t in turns if t.speaker == speaker]
    for i, turn in enumerate(own):
        for j in range(i + 1, len(own)):
            span = own[j].end - turn.start
            if span < minimum:
                continue
            if span > maximum:
                break
            run = tuple(own[i:j + 1])
            out.append(Exchange(start=turn.start, end=own[j].end,
                                turns=run))
            break
    return out


# ── Retakes of a whole exchange ──────────────────────────────────────

def _vocabulary(exchange: Exchange) -> set:
    from library.tools.reel_proposal import _content_words
    return _content_words(" ".join(t.text for t in exchange.turns))


def containment(a: Exchange, b: Exchange) -> float:
    """Overlap as a fraction of the SMALLER vocabulary.

    Not Jaccard: the second take of an exchange is usually the more
    complete one, and Jaccard reads that extra material as difference.
    """
    wa, wb = _vocabulary(a), _vocabulary(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def overlaps(a: Exchange, b: Exchange) -> bool:
    """True when two windows cover any of the same timeline."""
    return a.start < b.end and b.start < a.end


def collapse_overlapping(exchanges: Sequence[Exchange]) -> List[List[Exchange]]:
    """Group windows that cover the same stretch of timeline.

    These are NOT retakes. `exchange_windows` emits one window per
    opening turn, so a conversation with four possible openers yields
    four overlapping windows of itself - measured at 27:34, where five
    windows scored containment 1.00 against each other. Treating those as
    "recorded five times" is a different error from treating a genuine
    second take as a new conversation, and conflating the two made the
    retake count meaningless.

    The FIRST of each group is the earliest opener; the rest are
    alternative framings of one conversation.

    Membership is tested against the GROUP'S HEAD, never against any
    member, and the difference is not a detail
    -------------------------------------------------------------------
    Testing against any member CHAINS: A overlaps B, B overlaps C, so C
    joins A's group even where A and C share no second at all.  The head
    is the only window that survives into the candidate table, so
    everything the chain swallowed leaves the table with it.

    Measured on the field test 2026-09-06, after the transcript was
    re-bound (`timeline_transcript.rebind_document`).  Chaining put
    248.83-299.41, 267.36-328.23, 290.73-341.27, 312.75-362.78 and
    342.04-413.85 in ONE group represented by the first: a group
    spanning 248 seconds standing in for a 50-second window, which is
    not "the same stretch of timeline" by any reading.  The candidate
    covering the captain's approved reel 03 - 312.75-362.78 - vanished
    from the table entirely, and 68 raw windows reduced to 25 candidates
    where the head test gives 37.

    It got WORSE as the transcript got better: re-binding the straddling
    rows added real turns, denser turns produce more overlapping
    windows, and more overlap chains further.  So the failure mode is
    that improving a measurement upstream shrinks what the model is
    shown - the opposite of what the captain's "aim wide, do not curate"
    asks for.

    Heads may now overlap each other, which is correct and is what
    `collapse_retakes` already assumes: it SKIPS a candidate that
    overlaps the head rather than calling it a second take, so two
    framings of one conversation still cannot be counted as recorded
    twice.
    """
    groups: List[List[Exchange]] = []
    for candidate in sorted(exchanges, key=lambda e: e.start):
        for group in groups:
            if overlaps(group[0], candidate):
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    return groups


def collapse_retakes(exchanges: Sequence[Exchange],
                     within_seconds: float = 300.0,
                     ) -> List[List[Exchange]]:
    """Group conversations recorded MORE THAN ONCE.

    Runs over non-overlapping conversations only - `collapse_overlapping`
    has already reduced each stretch of timeline to one representative,
    so anything grouped here is a genuine second take taken at a
    different time.

    The FIRST of each group is the earliest take; the rest carry
    `retake_of` and a band. Nothing is discarded.
    """
    groups: List[List[Exchange]] = []
    for candidate in sorted(exchanges, key=lambda e: e.start):
        placed = False
        for group in groups:
            head = group[0]
            if candidate.start - group[-1].end > within_seconds:
                continue
            if overlaps(head, candidate):
                continue
            score = containment(head, candidate)
            band = ("same" if score >= SAME_EXCHANGE
                    else "possible" if score >= POSSIBLE_RETAKE else "")
            if band:
                group.append(Exchange(
                    start=candidate.start, end=candidate.end,
                    turns=candidate.turns, concerns=candidate.concerns,
                    retake_of=head.start, retake_band=band,
                    retake_containment=score))
                placed = True
                break
        if not placed:
            groups.append([candidate])
    return groups


# ── The funnel, said out loud ────────────────────────────────────────

def funnel(turns: Sequence[Turn], lead: str, answerer: str,
           **kwargs) -> dict:
    """Every window found, what it measured, and what left at each stage.

    Returns the SURVIVORS and the DISCARDS with a reason each, because a
    selector that reports only what it kept is the defect that produced
    the first rejected batch.
    """
    windows = exchange_windows(turns, lead, answerer, **kwargs)
    clean = [w for w in windows if not w.concerns]
    flagged = [w for w in windows if w.concerns]

    # Same stretch of timeline first, THEN recorded-twice. Doing it the
    # other way round counts five framings of one conversation as five
    # takes of it.
    stretches = collapse_overlapping(clean)
    representatives = [g[0] for g in stretches]
    alternatives = [e for g in stretches for e in g[1:]]

    groups = collapse_retakes(representatives)
    firsts = [g[0] for g in groups]
    retakes = [e for g in groups for e in g[1:]]
    return {
        "windows_found": len(windows),
        "with_concerns": len(flagged),
        "clean": len(clean),
        "distinct_stretches": len(stretches),
        "alternative_framings": len(alternatives),
        "distinct_conversations": len(groups),
        "collapsed_as_retakes": len(retakes),
        "survivors": firsts,
        "flagged": flagged,
        "retakes": retakes,
        "alternatives": alternatives,
    }
