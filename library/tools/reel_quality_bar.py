"""The captain's four qualities, each held the way its KIND allows.

A reel should carry a call to action, provide value, be coherent, and run
around the length the captain wants (captain, 2026-09-06).  Two of those
qualities are EXACT and two are JUDGEMENT, and they are held differently
rather than averaged into one number.

The current contract
--------------------
**DURATION is MEASURED, never judged.**  There is no length band here and
nothing in its place - no window, no warning threshold, no configurable
default (`DURATION_GATE_REMOVED`).  `duration_reading` records how long
the reel RUNS, reconciled with the ending the build appends, resolved
through the owners' own functions (`reel_ending`,
`reel_build.plan_cards`); nothing here restates the ending's length.  The
only length ERROR is `reel_exchange.ABSURD_SECONDS`, imported, which is
mechanical rather than editorial (AGENTS.md 10.5).  The selector may weigh
the captain's length guidance while choosing; the bar reports no band.

**A CALL TO ACTION is three exact checks.**
1. *One exists*: the seconds the reel PLAYS LAST are a span some moment in
   the batch declared as its closer (`declared_closers`) - never a word
   list.  A batch declaring no closer reads ABSENT on every reel.
2. *It is inside what the reel plays*: the checks `validate_proposal` ran
   are run again against what will be played, because the plan is a file
   the captain edits.
3. *Whose it is*: how many reels close on the same seconds is COUNTED and
   REPORTED (`QB-CTA-SHARED`), never failed - the same CTA may close any
   number of reels (`reel_proposal`).

**COHERENCE and VALUE go to a model, which is asked for a READING, never a
verdict.**  The judge answers questions with no good or bad answer (what
does the reel claim, what could a listener repeat, how does it open and
end, what does it lean on that is unheard, where does it stop adding),
each with a QUOTE.  The ENGINE derives the verdicts afterwards
(`coherence_of`, `value_of`); the mapping is not in the prompt.
- `check_reading` checks every quote against what the reel plays - exact
  containment, no similarity or threshold.  A reading whose quotes are not
  in the reel is REFUSED and recorded as refused, never stored as a
  verdict.
- `FORBIDDEN_IN_THE_ASK` names the words that would hand the judge the
  criteria; `assert_ask_is_uncontaminated` raises if one reaches the ask.
- The judge is sent the reel's WORDS AND NOTHING ELSE - not its slug, the
  selector's reason, its hook/close/value fields, measurements or findings.
- COHERENCE RECORDS AND DOES NOT GATE (`COHERENCE_DOES_NOT_GATE` carries
  each rejected gate with the number that killed it).  `coherence_of` and
  `dependency_positions` are derived on every judged reel; no warning is
  raised from them.

**The ranking is an ORDERING and there is NO SCORE** (`passage_engagement`).
The judge places the batch in one ordering with a one-sentence basis each;
a reel it declines to place reads UNJUDGED, never last or zero.  `ranked`
is the reader.

**Nothing here drops, shortens or rewrites a reel.**  The bar REPORTS per
reel - findings, derived verdicts, place in the ordering - and the caller
decides.

Reachability
------------
    python3 -m library.tools.reel_quality_bar --project <project_folder>
    python3 -m library.tools.reel_quality_bar --project <p> --json

`tests/test_reel_quality_bar.py`, `tests/test_quality_bar_thesis_ending.py`.
The measurements and rulings behind each rule (the 2026-09-05 batch that
passed every mechanical check, the 31-reel coherence calibration, the
2026-09-18 removal of the duration gate and the not-followable warning):
docs/evidence/reel_quality_bar.md.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

# ONE spelling of the captain's mechanical length bound, owned by the
# module whose docstring records where it came from.  The 45-90s GUIDANCE
# this module used to import alongside it is gone (2026-09-18,
# `DURATION_GATE_REMOVED`): a guidance spelled here as well as in
# `reel_exchange` would be the second enumeration AGENTS.md 10.1 is
# about, and there is no band left to spell.
from library.tools.reel_exchange import ABSURD_SECONDS

__all__ = [
    "BODY",
    "COHERENCE_DOES_NOT_GATE",
    "DURATION_DOES_NOT_GATE",
    "DURATION_GATE_REMOVED",
    "EXACT",
    "FORBIDDEN_IN_THE_ASK",
    "IN_CALL_TO_ACTION",
    "JUDGEMENT",
    "OPENING",
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
    "dependency_positions",
    "duration_reading",
    "ending_seconds",
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


COHERENCE_DOES_NOT_GATE = {
    "ruled": "2026-09-06",
    "batch": "lucie/geo-podcast, the 31 proposals of the harvest run",
    "why": (
        "`coherence_of` was `NOT_FOLLOWABLE if reading.assumes_known else "
        "FOLLOWABLE`, and it read not_followable on 31 of 31 - a column "
        "constant across a batch carries no information about that batch. "
        "A second reader that never saw the first reproduced it exactly, "
        "31 of 31, so the observations are sound and about the material; "
        "what the quality cannot do is DISCRIMINATE."),
    # AGENTS.md 10.4 says to give a model-judged gate a DETERMINISTIC half
    # and let that carry the verdict.  Four candidates for one were
    # measured against the captain's own 31 reels before this was written.
    # Each is recorded with the number that killed it, because "we looked
    # and there is nothing" is only worth anything with the looking in it.
    "candidates_measured": [
        {"half": "the reel begins strictly inside a transcript segment",
         "needs_a_model": False,
         "measured": "0 of 31",
         "rejected": "boundaries are always snapped to a segment start, so "
                     "this is constant and could never fail"},
        {"half": "the reel opens at a sentence boundary, read off the "
                 "transcript's punctuation",
         "needs_a_model": False,
         "measured": "401 of 940 segments carry any of . ? !",
         "rejected": "it would measure which transcription pass wrote the "
                     "segment, not how the reel opens"},
        {"half": "the reel begins partway through the speaker's own turn",
         "needs_a_model": False,
         "measured": "9 of 31 - it does discriminate",
         "rejected": "it measures a DIFFERENT property. Reel 11 opens on "
                     "the words 'so your' and still starts its speaker's "
                     "turn cleanly, because an answer grammatically "
                     "continues the question that prompted it"},
        {"half": "a pronoun or demonstrative in the opening with no "
                 "antecedent inside the reel",
         "needs_a_model": False,
         "measured": "14 of 31 over the first ten words, 23 of 31 over the "
                     "first twenty",
         "rejected": "the count is decided by a window width nobody can "
                     "source, and it calls reel 11 - 'so your', the "
                     "plainest mid-sentence opening in the batch - clean"},
        {"half": "a dependency quoted from the reel's FIRST WORD, derived "
                 "from the checked reading",
         "needs_a_model": True,
         "measured": "18 of 31 (reader 1) against 13 of 31 (reader 2), "
                     "agreeing on 26 of 31",
         "rejected": "it discriminates, but which reels fail depends on "
                     "which model read them. Deriving a verdict from it "
                     "would be enforcing a model's opinion with an "
                     "arithmetic step in front of it"},
    ],
    "ruling": (
        "There is no honest deterministic half, so coherence RECORDS and "
        "does not gate. QB-NOT-FOLLOWABLE is a WARNING carrying "
        "`dependency_positions` - which is exact - and the reading stays "
        "as evidence. If a later batch produces a half that separates "
        "reels without a model, this is the record it has to beat."),
    # The second direction, and the one that makes the demotion urgent
    # rather than merely tidy.  26 of the 31 - IDENTICALLY under both
    # readers - lean on something inside their own declared call to
    # action, almost always "the Lucy visibility system" arriving named.
    # That is the closer the captain ASKED for, and the gate was failing
    # reels for carrying it.  A gate that FAILS CORRECT OUTPUT is no more
    # coverage than one that cannot fail (AGENTS.md 10.4).
    "dependencies_inside_the_declared_closer": {
        "reader_1": "26 of 31", "reader_2": "26 of 31"},
    # 2026-09-18: the WARNING this ruling created is gone too.  The
    # script-QA batch read not_followable on 29 of 31 moments, 20 of the
    # 22 it approved - the column the 2026-09-06 ruling already knew was
    # constant proved constant on new material, and a signal that says
    # the same thing about everything is worse than none.  Calibration
    # was the other option and had already been tried (the four halves
    # above).  What stays is the recording: `coherence_of` and
    # `dependency_positions` on every judged verdict, no finding.
    "warning_removed": "2026-09-18, with the duration gate (option c)",
}
"""Why the coherence quality does not decide pass or fail.

Kept beside `FIRST_MEASUREMENT` and for the same reason: a design
argument that has never met the material is a design argument.
"""


DURATION_GATE_REMOVED = {
    "ruled": "2026-09-18",
    "option": "c",
    "captain": (
        '"Delete the gate; the script reading judges length as part of '
        'judging the reel."'),
    "standing_ruling": "2026-09-16: there is no hard coded number that hits this",
    "what_went": (
        "The 45-90s window came OUT of the quality bar - no wider window, "
        "no warning threshold, no configurable default, all three being "
        "the same defect wearing a different number. QB-DURATION no "
        "longer exists, and `duration_reading` carries no guidance keys. "
        "Length reaches the model as one input among others: step 3.05 "
        "sends `runs_for_seconds` beside the lines, reconciled with the "
        "ending the same way the bar's own figure is."),
    "evidence": (
        "Tonight's script QA judged 22 reels on their words and length "
        "never decided a verdict. The reading does the job the gate was "
        "pretending to do. What replaces the gate is the captain's "
        "approval, which is already the mechanism: a reel is BUILT only "
        "once they approve it (`reel_proposal.assert_approved`), and "
        "PROPOSED fails that gate exactly as REJECTED does."),
    "what_stays": (
        "`duration_reading` still measures how long the reel RUNS on every "
        "reel - reconciled with the ending the build appends - and "
        "QB-ABSURD-LENGTH still errors past `reel_exchange.ABSURD_SECONDS`, "
        "which is mechanical rather than editorial (AGENTS.md 10.5)."),
}
"""Why the duration band is gone entirely, not demoted again.

Supersedes `DURATION_DOES_NOT_GATE` (2026-09-09), which stays below as
the history of the warning this ruling deleted.
"""


DURATION_DOES_NOT_GATE = {
    "ruled": "2026-09-09",
    "batch": "lucie/geo-podcast, the 31 proposals of the harvest run",
    "captain": (
        '"thats fine, render them" (of reels 26 and 31); "the time '
        'amount is a rule of thumb, there can be exceptions if the '
        'video is good still"'),
    "why": (
        "The brief already said the 45-90s band is a preference - "
        '"no fixed target", "preferably", "No hard cap" - and a '
        "47-second reel inside the band can still be wrong, so length "
        "was never the thing that disqualifies. Ten of the harvest "
        "batch's eleven rejections were duration and nothing else "
        "(delivered 39.5, 44.7, 18.1, 43.1, 40.3, 40.4, 29.6, 43.8, "
        "39.6 and 93.1s against the band; reel 03 missed by 0.3s), "
        "and the captain ruled the band guides rather than gates."),
    # What the bar judges is DELIVERED seconds - the body minus the bad
    # takes the build cuts, plus the closer - and that is also what the
    # approval notes print, so the two agree. The `duration_seconds`
    # beside them in old proposal files is the BODY window only: it does
    # not include the closer and does not subtract the cuts, so reel 31
    # reads 83.7s there (inside the band) while delivering 93.1s (over
    # it). That field is stale by construction - removed from
    # `ReelMoment.as_dict`, which nothing reads back - and reading it as
    # the judged number is the unauditable gate this record refuses.
    "judged_number": "delivered_seconds",
    "ruling": (
        "Duration RECORDS and does not gate. QB-DURATION is a WARNING "
        "carrying `duration_reading` - which is exact - and the reading "
        "stays as evidence. No replacement gate, score or tolerance was "
        "invented: judging whether a video is good still is taste, not "
        "the engine's to compute (AGENTS.md 10.5). What replaces the "
        "gate is the captain's approval, which is already the mechanism: "
        "a reel is BUILT only once they approve it "
        "(`reel_proposal.assert_approved`), and PROPOSED fails that gate "
        "exactly as REJECTED does."),
}
"""Why the duration band does not decide pass or fail.

The same shape as `COHERENCE_DOES_NOT_GATE`: the reading is MEASURED
and RECORDED on every reel, and it does not by itself reject.
"""


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

QB_ABSURD_LENGTH = "QB-ABSURD-LENGTH"
QB_UNBUILDABLE = "QB-UNBUILDABLE"
QB_CTA_ABSENT = "QB-CTA-ABSENT"
QB_CTA_IN_BODY = "QB-CTA-IN-BODY"
QB_CTA_SILENT = "QB-CTA-SILENT"
QB_CTA_OUTSIDE = "QB-CTA-OUTSIDE"
QB_CTA_FRAGMENT = "QB-CTA-FRAGMENT"
QB_CTA_OPENS_MID_SENTENCE = "QB-CTA-OPENS-MID-SENTENCE"
QB_CTA_SHARED = "QB-CTA-SHARED"
QB_CTA_NOT_LAST = "QB-CTA-NOT-LAST"
QB_CTA_DISAGREEMENT = "QB-CTA-DISAGREEMENT"
QB_NO_TAKEAWAY = "QB-NO-TAKEAWAY"
QB_UNGROUNDED = "QB-UNGROUNDED"
QB_OPENING_MISPLACED = "QB-OPENING-MISPLACED"
QB_CLOSING_MISPLACED = "QB-CLOSING-MISPLACED"

ERROR = "error"
WARNING = "warning"

FINDING_OWNERS: Dict[str, str] = {
    # Which quality each finding is about, so a report can be read by
    # quality rather than by code.
    QB_ABSURD_LENGTH: "duration",
    QB_UNBUILDABLE: "duration",
    QB_CTA_ABSENT: "call_to_action",
    QB_CTA_IN_BODY: "call_to_action",
    QB_CTA_SILENT: "call_to_action",
    QB_CTA_OUTSIDE: "call_to_action",
    QB_CTA_FRAGMENT: "call_to_action",
    QB_CTA_OPENS_MID_SENTENCE: "call_to_action",
    QB_CTA_SHARED: "call_to_action",
    QB_CTA_NOT_LAST: "call_to_action",
    QB_CTA_DISAGREEMENT: "call_to_action",
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
    """The comparable words of a value, and NONE of an absent one.

    `None` comes back empty rather than as the word "none", which is
    what `str(None).lower()` gave it.  That difference is a gate that
    fails correct output (AGENTS.md 10.4): `takeaway_quote` is declared
    OPTIONAL precisely so that "this reel delivers nothing a listener
    could use" can be answered - and a judge that answered it by leaving
    the key OUT rather than sending `""` had its whole reading refused,
    because "none" is not in what the reel says.  It never fired only
    because every reading on disk happens to carry the key.
    `claim_parts` is optional the same way and would have inherited it.
    """
    if text is None:
        return []
    return re.findall(r"[a-z0-9']+", str(text).lower())


def normalise(text) -> str:
    """A quote and a transcript compared on the same footing.

    Case, punctuation and whitespace are removed because a model
    re-typing a sentence is not obliged to reproduce WhisperX's commas.
    Nothing else is: no stemming, no stopword removal and no similarity.
    A quote is present or it is not.
    """
    return " ".join(_words(text))


def playable_ranges(moment, transcript: dict,
                      extra_cuts=()) -> Tuple[list, str]:
    """`(ranges, refusal)` - what the reel plays, or why it plays nothing.

    `reel_build.reel_ranges` RAISES for a plan that cannot be laid out:
    a closer under a frame, or one overlapping its own body so the reel
    would play those seconds twice.  Both are real defects and both are
    things the bar exists to report - so the exception is turned into a
    refusal here rather than being allowed to take the whole batch's
    report with it.  A reel that cannot be built has not passed anything.

    `extra_cuts` are the captain's recorded strikes for this moment
    (`transcript_corrections.exclusion_cuts_for_span`). Empty (every
    existing caller) reads exactly what it read before; a caller proving
    what a BUILT reel plays passes the same cuts the builder cut.
    """
    from library.tools.reel_build import ReelBuildError, reel_ranges

    try:
        return list(reel_ranges(moment, transcript,
                                extra_cuts=extra_cuts)), ""
    except ReelBuildError as refused:
        return [], str(refused)
    except (KeyError, TypeError, ValueError) as broken:
        return [], f"{type(broken).__name__}: {broken}"


def played_speech(moment, transcript: dict,
                  with_words: bool = False,
                  extra_cuts=()) -> List[dict]:
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

    `with_words` adds each line's own words, in REEL seconds, and is off
    for every existing caller.  It is off because **word timings do not
    reach a prompt** (AGENTS.md 10.1) and the judge's `lines` table is a
    prompt; it exists because this is the ONE place the master-to-reel
    offset for a line is computed, so a caller that needs a word's reel
    second has to get it here or spell the same arithmetic a second
    time.  `library/tools/explainer_plan.py` is the caller: a stage of
    an explainer is anchored to the word its quote begins on, and a
    stage anchored to the START of a twenty-word line can be four
    seconds early.

    `extra_cuts` is the same parameter `playable_ranges` carries: the
    captain's recorded strikes, so a caller proving what a built reel
    plays reads the struck seconds as absent.
    """
    segments = sorted((transcript.get("segments") or []),
                      key=lambda s: float(s.get("timeline_start") or 0.0))
    out: List[dict] = []
    offset = 0.0
    for index, (range_start, range_end) in enumerate(
            playable_ranges(moment, transcript,
                            extra_cuts=extra_cuts)[0]):
        for segment in segments:
            start = float(segment.get("timeline_start") or 0.0)
            end = float(segment.get("timeline_end") or 0.0)
            if end <= range_start or start >= range_end:
                continue
            text = (segment.get("text") or "").strip()
            if not text:
                continue
            line = {
                "speaker": segment.get("speaker"),
                "reel_start": round(offset + max(start, range_start)
                                    - range_start, 2),
                "reel_end": round(offset + min(end, range_end)
                                  - range_start, 2),
                "text": text,
                "bound": bool(segment.get("resolve_item_id")),
                "range": index,
            }
            if with_words:
                # A word's own reel second, by the SAME shift the line
                # got. Only words the transcriber really timed are
                # carried: `timed: false` is an interpolation, and an
                # anchor computed from one would be a guess wearing a
                # measurement's clothes.
                line["words"] = [
                    {"word": word.get("word") or "",
                     "at": round(offset + float(word.get("start") or 0.0)
                                 - range_start, 3)}
                    for word in (segment.get("words") or [])
                    if word.get("timed")
                    and range_start <= float(word.get("start") or 0.0) < range_end
                ]
            out.append(line)
        offset += range_end - range_start
    return out


def reel_text(moment, transcript: dict) -> str:
    """Everything the reel says, joined, in play order."""
    return " ".join(line["text"] for line in played_speech(moment, transcript))


def _declared_body_ending(moment, transcript: dict,
                          ending: dict | None) -> dict | None:
    """A project pin that ends the body suppresses its planned closer.

    The proposal may still carry a borrowed CTA, whose source seconds
    can precede the body. A word-anchored ending in the body's approved
    transcript is the captain's declaration that playback stops there.
    Prefer the timed transcript measurement; when it lacks corrected
    words, the approved preview is the recorded wording the captain
    approved. An anchor in the CTA itself keeps the CTA.
    """
    closer = getattr(moment, "call_to_action", None)
    if (not ending or ending.get("source") == "call_to_action"
            or closer is None):
        return None
    body = replace(moment, call_to_action=None)
    reading = thesis_reading(body, transcript, ending)
    if reading is not None:
        if thesis_reading(moment, transcript, ending) is None:
            return reading
        return None
    anchor = _words((ending.get("ends_on") or {}).get("anchor_phrase"))
    if not anchor or _contains_word_run(_words(closer.text), anchor):
        return None
    return _approved_ending_preview(body, ending, require_tail=True)


def _contains_word_run(haystack: list[str], needle: list[str]) -> bool:
    return any(haystack[index:index + len(needle)] == needle
               for index in range(len(haystack) - len(needle) + 1))


def _approved_ending_preview(moment, ending: dict | None,
                             require_tail: bool = False) -> dict | None:
    """Read an explicit ending from the captain-approved moment text.

    Some live, hand-edited endings restore words absent from the timed
    transcript. The captain's keyed declaration still names the cut,
    and the approved moment preview confirms those words belong to that
    reel. This reading records that source without pretending the stale
    timed transcript measured a span. A declaration whose anchor is not
    in the approved moment remains absent.
    """
    anchor_text = ((ending or {}).get("ends_on") or {}).get(
        "anchor_phrase")
    anchor = _words(anchor_text)
    preview = _words(getattr(moment, "transcript_preview", ""))
    if not anchor or not _contains_word_run(preview, anchor):
        return None
    if require_tail and preview[-len(anchor):] != anchor:
        return None
    return {
        "source": "thesis",
        "span": None,
        "text": str(anchor_text).strip(),
        "speaker": None,
        "shared_with": [],
        "closes_reels": 0,
        "in_own_body": False,
        "outside_episode": False,
        "silent": False,
        "incomplete": False,
        "unwritten_words": [],
        "opens_mid_sentence": False,
        "opening_head": "",
        "is_the_ending": True,
        "evidence": (
            "captain ending declaration matches approved moment preview; "
            "timed transcript did not resolve it"),
    }


def delivered_seconds(moment, transcript: dict,
                        project_folder=None) -> float:
    """How long the reel RUNS - the reconciled figure, not the body window.

    Body minus bad takes, plus the closer, plus the ending the build
    appends after them (freeze tail and head/tail cards).  `project_folder`
    resolves the ending with the owners' own functions; without one there
    is no declaration to resolve and the figure is the body only - see
    `duration_reading`, which says so rather than reporting it as the
    reel.  One spelling: this delegates rather than re-summing.
    """
    return duration_reading(
        moment, transcript, project_folder)["delivered_seconds"]


def ending_seconds(moment, transcript: dict, project_folder) -> dict:
    """The seconds the build appends after the body: freeze tail + cards.

    Resolved, never restated: the ending declaration comes from
    `reel_ending.resolve_ending` (a hand-written pin wins, otherwise the
    freeze a CTA-closing reel inherits), the freeze length from
    `reel_ending.ending_tail_frames`, and the cards from
    `reel_build.plan_cards` - the same functions the build and the
    conformance verifier read.  A hardcoded length here would be the
    standing-ruling violation ("there is no hard coded number that hits
    this") wearing a different number, and the reel the old warning
    called 5.5s short built at 45.92s is what a restated figure buys.

    Span cards replace the body rather than sitting beside it
    (`reel_conformance_verifier` derives `plan_seconds` the same way),
    so `span_present` travels for the caller that sums.

    Without a project folder there is no declaration to resolve, and a
    card plan that refuses is the build's own refusal arriving early:
    both come back as `resolved: False` with the reason in `why`, so a
    body-only figure is never reported as the reel in silence.
    """
    unresolved = {"freeze_seconds": 0.0, "card_seconds": 0.0, "total": 0.0,
                  "span_present": False, "cards": [], "ending_source": "none",
                  "resolved": False, "why": ""}
    if not project_folder:
        unresolved["why"] = (
            "no project folder, so no ending declaration to resolve: "
            "this figure is the body only")
        return unresolved
    fps = float((transcript.get("derived_from") or {}).get("fps") or 0.0)
    if not fps:
        unresolved["why"] = (
            "the transcript names no fps, so freeze frames cannot be read "
            "as seconds: this figure is the body only")
        return unresolved
    from library.tools import reel_ending as _ending

    try:
        ending = _ending.resolve_ending(
            project_folder, moment.timeline_name, moment, transcript)
    except Exception as refused:  # noqa: BLE001 - the build refuses on
        # this too, and the bar must report the batch, not die on one reel
        unresolved["why"] = (
            f"the ending could not be resolved: {refused}")
        return unresolved
    # The project's TV-frame look, or the element's own declared timings
    # where the project cannot be read - the same fallback
    # `reel_conformance_verifier._reel_look_declaration` holds, for the
    # same reason: a look nobody can read is the build's refusal, not
    # this module's, and the element's own timings are the answer
    # meanwhile.
    look = None
    try:
        from library.tools import reel_look as _look
        from library.tools.delivery_format import resolve_delivery_format

        frame_w, frame_h = resolve_delivery_format(project_folder)
        look = _look.resolve_look(project_folder, frame_w, frame_h)
    except Exception:  # noqa: BLE001 - see above
        look = None
    freeze_seconds = _ending.ending_tail_frames(ending, look) / fps if ending else 0.0
    try:
        from library.tools.reel_build import (
            plan_cards as _plan_cards,
            reel_resolution as _resolution,
        )

        width, height = _resolution(project_folder)
        ranges = playable_ranges(moment, transcript)[0]
        cards = _plan_cards(moment, transcript, ranges, project_folder,
                            fps, width=width, height=height,
                            ending=ending, look=look)
    except Exception as refused:  # noqa: BLE001 - see above
        unresolved["why"] = (
            f"the reel's cards could not be planned: {refused}")
        return unresolved
    planned = [{"placement": c.placement, "element": c.element,
                "render_name": c.render_name,
                "duration_seconds": round(c.duration_frames / fps, 2)}
               for c in (cards or [])]
    card_seconds = sum(c.duration_frames for c in (cards or ())) / fps
    span_present = any(c.placement == "span" for c in (cards or ()))
    source = "none"
    if ending is not None:
        source = ("inherited" if _ending.is_inherited(ending)
                  else "declared")
    return {"freeze_seconds": freeze_seconds, "card_seconds": card_seconds,
            "total": freeze_seconds + card_seconds,
            "span_present": span_present, "cards": planned,
            "ending_source": source, "resolved": True, "why": ""}


# ── EXACT: duration ──────────────────────────────────────────────────

def duration_reading(moment, transcript: dict,
                     project_folder=None) -> dict:
    """How long the reel runs: body, closer, and the ending after them.

    `delivered_seconds` is the RECONCILED figure - what a viewer sits
    through.  The body minus the bad takes the build cuts, plus the
    closer, plus the freeze tail and the head/tail cards the build
    appends (`ending_seconds`).  Not `duration` (the body window) and
    not `total_duration` (body plus closer, before anything is cut):
    three genuinely different numbers, and the old warning judged the
    second while the reel built the first plus 3.8s of ending.

    `project_folder` resolves the ending.  Without one the figure is
    the body only and SAYS SO (`ending_resolved` False with the reason
    in the `ending` breakdown) - a body-only figure reported as the
    reel is the defect `DURATION_GATE_REMOVED` records.
    """
    measured_moment = moment
    if project_folder and getattr(moment, "call_to_action", None) is not None:
        from library.tools import reel_ending as _ending

        declared = _ending.declared_ending(
            project_folder, moment.timeline_name)
        if _declared_body_ending(moment, transcript, declared) is not None:
            measured_moment = replace(moment, call_to_action=None)

    ranges, refusal = playable_ranges(measured_moment, transcript)
    body = sum(end - start for start, end in ranges)
    ending = ending_seconds(measured_moment, transcript, project_folder)
    if refusal:
        # A reel nothing can lay out has no length to hold against
        # anything, and reporting 0.0s as a length would be a finding
        # about the instrument. The refusal is the finding.
        return {"delivered_seconds": 0.0, "unbuildable": refusal,
                "body_seconds": 0.0,
                "closer_seconds": 0.0,
                "removed_by_cuts_seconds": 0.0,
                "ending_seconds": 0.0,
                "ending_resolved": False,
                "ending": ending}
    if ending["span_present"]:
        # A span card replaces the footage video for the whole body, so
        # adding the body again would report the reel at twice its
        # length - the same branch `reel_conformance_verifier` takes.
        delivered = ending["card_seconds"] + ending["freeze_seconds"]
    else:
        delivered = body + ending["card_seconds"] + ending["freeze_seconds"]
    return {
        "delivered_seconds": round(delivered, 1),
        "body_seconds": round(
            measured_moment.timeline_end - measured_moment.timeline_start, 1),
        "closer_seconds": round(measured_moment.call_to_action.duration, 1)
        if measured_moment.call_to_action else 0.0,
        "removed_by_cuts_seconds": round(
            (measured_moment.timeline_end - measured_moment.timeline_start)
            + (measured_moment.call_to_action.duration
               if measured_moment.call_to_action else 0.0)
            - body, 1),
        "ending_seconds": round(ending["total"], 1),
        "ending_resolved": ending["resolved"],
        "ending": ending,
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


def thesis_reading(moment, transcript: dict,
                   ending: Optional[dict]) -> Optional[dict]:
    """A reel the project declares ends on its own thesis, measured.

    The fourth source beside `cta_reading`'s three, and the one that
    stops that check firing on a re-cut the captain authorised: Reel
    27 (2026-09-19) ends Q->A on "...recommended later on." with its
    shared website-checkout closer deleted as topically alien, and no
    own-thread replacement in the episode. `declared`/`in_body` cannot
    express that - a closer inside its own body is refused as a double
    play - so "ends on a call to action" would fail a reel that ends
    exactly where it was told to.

    - `thesis` - a HAND-WRITTEN entry from `external/reel_ending.json`
      (`library/tools/reel_ending.py`: word-anchored, reasoned, one
      reel), matched by the same prefix rule the build honours. An
      inherited ending never reaches here: `cta_default_ending`
      returns None where the plan names no closer, so any entry for a
      closer-less reel is written, not derived.
    - MEASURED, not taken on faith: the anchor's words must tail-match
      the reel's played speech (the reel ENDS on those words), and the
      run must occur in timed transcript words inside the played
      ranges (the span is evidence, recorded). A declaration whose
      anchor is not the reel's tail reads ABSENT exactly as before -
      fail closed.

    Returns the reading (`source: "thesis"`, `is_the_ending: True`),
    or None where there is no declaration or it is not honoured.
    """
    anchor = ((ending or {}).get("ends_on") or {}).get("anchor_phrase")
    want = _words(anchor) if isinstance(anchor, str) else []
    if not want:
        return None
    said = _words(reel_text(moment, transcript))
    if len(said) < len(want) or said[-len(want):] != want:
        return None
    ranges, _ = playable_ranges(moment, transcript)
    if not ranges:
        return None
    segments = sorted((transcript.get("segments") or []),
                      key=lambda s: float(s.get("timeline_start") or 0.0))
    timed: list = []
    for segment in segments:
        start = float(segment.get("timeline_start") or 0.0)
        end = float(segment.get("timeline_end") or 0.0)
        if not any(s0 < end and s1 > start for s0, s1 in ranges):
            continue
        for word in (segment.get("words") or []):
            try:
                w0, w1 = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if w1 <= w0:
                continue
            tokens = _words(word.get("word") or "")
            if not tokens:
                continue
            timed.append((tokens[0], w0, w1,
                          segment.get("speaker")))
    resolutions: list = []
    for index in range(len(timed) - len(want) + 1):
        if [t[0] for t in timed[index:index + len(want)]] == want:
            resolutions.append(timed[index:index + len(want)])
    if not resolutions:
        return None
    # THE LAST telling, not the first: the build truncates to the
    # anchor shot, so the occurrence that plays last is the evidence.
    run = max(resolutions, key=lambda occurrence: occurrence[-1][2])
    return {
        "source": "thesis",
        "span": [round(run[0][1], 2), round(run[-1][2], 2)],
        "text": str(anchor).strip(),
        "speaker": run[-1][3],
        "shared_with": [],
        "closes_reels": 0,
        "in_own_body": False,
        "outside_episode": False,
        "silent": False,
        "incomplete": False,
        "unwritten_words": [],
        "opens_mid_sentence": False,
        "opening_head": "",
        "is_the_ending": True,
    }


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


def cta_opening_head(transcript: dict, cta_start: float,
                      cta_speaker: Optional[str]) -> Optional[str]:
    """The sentence head a closer leaves behind, or None when it opens clean.

    The proposal gate checks whole SEGMENTS, and ASR segments split
    mid-sentence - so a closer can start exactly on a segment edge and
    still open on the back half of a statement ("we're calling the
    Lucie visibility system", whose head "it's exactly why we've been
    building this platform" plays nowhere on the reel). This is the
    deterministic half of that observation: the latest segment ending
    before the closer starts is its lead-in. A lead-in nobody speaks
    (episode start), a turn change (another speaker), or a lead-in the
    transcriber closed with sentence-terminal punctuation all open
    clean. A same-speaker lead-in with no closing punctuation means the
    closer's first words continue a sentence the reel never plays the
    head of, and the trailing words are returned as the evidence.

    WARNING-level on purpose (AGENTS.md 10.4): ASR punctuation is a
    measurement, not a verdict, and a transcript that drops a period
    reads here as a fragment. The message quotes the head so the reader
    can dismiss it, and the bar never refuses on it.
    """
    segments = transcript.get("segments") or []
    earlier = [s for s in segments
               if float(s.get("timeline_end") or 0) <= float(cta_start) + 1e-6]
    if not earlier:
        return None
    lead = max(earlier, key=lambda s: float(s.get("timeline_end") or 0))
    if (lead.get("speaker") or "") != (cta_speaker or ""):
        return None
    text = re.sub(r"(\.\.\.+|\u2026)\s*$", "",
                  (lead.get("text") or "").rstrip())
    if text.endswith((".", "?", "!")):
        return None
    words = (lead.get("text") or "").split()
    return " ".join(words[-12:]) if words else None


def cta_reading(moment, transcript: dict,
                closers: Dict[Tuple[float, float], List[int]],
                thesis: Optional[dict] = None) -> dict:
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
        "opens_mid_sentence": False,
        "opening_head": "",
        "is_the_ending": False,
    }

    body_ending = _declared_body_ending(moment, transcript, thesis)
    if body_ending is not None:
        reading.update(body_ending)
        return reading

    if declared is not None:
        key = (round(declared[0], 2), round(declared[1], 2))
        others = [n for n in closers.get(key, ()) if n != int(moment.number)]
        closer = moment.call_to_action
        # A closer that opens on a sentence's back half is a THIRD
        # captain fragment report (reel 09's "we're calling the
        # Lucie visibility system"); the segment gate cannot see
        # it, so the bar records it here instead of refusing it.
        opening_head = (cta_opening_head(
            transcript, declared[0], closer.speaker) or "")
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
            "opening_head": opening_head,
            "opens_mid_sentence": bool(opening_head),
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
        # No closer anywhere in what the reel plays. A project-declared
        # ending (`external/reel_ending.json`, captain-authorised re-cut)
        # is the fourth answer. Prefer timed-word evidence; a corrected
        # ending absent from the transcript can still be confirmed by
        # the approved moment preview. A declaration represented in
        # neither place reads ABSENT.
        thesis_reading_result = thesis_reading(moment, transcript, thesis)
        if thesis_reading_result is None:
            thesis_reading_result = _approved_ending_preview(
                moment, thesis)
        if thesis_reading_result is not None:
            reading.update(thesis_reading_result)
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
                   thesis: Optional[dict] = None,
                   project_folder=None,
                   ) -> List[BarFinding]:
    """Everything the two EXACT qualities have to say about one reel.

    Duration is MEASURED here (`duration_reading`, reconciled with the
    ending when `project_folder` resolves one) and never judged: the
    45-90s window came out on 2026-09-18 (`DURATION_GATE_REMOVED`), so
    there is no band branch and nothing to widen, warn or configure.
    The one length ERROR is the mechanical absurd bound below.
    """
    name = moment.timeline_name
    out: List[BarFinding] = []

    # The CALL TO ACTION first, and deliberately so.  A closer that
    # overlaps its own body is BOTH a call-to-action defect and the
    # reason `reel_ranges` refuses to lay the reel out; naming it as the
    # first is what stops the second swallowing it.  Its declared half
    # needs no ranges, so it survives a plan nothing can build.
    cta = cta_reading(moment, transcript, closers, thesis=thesis)
    out.extend(_cta_findings(moment, name, cta, closers))

    duration = duration_reading(moment, transcript, project_folder)
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
    if duration["delivered_seconds"] > ABSURD_SECONDS:
        out.append(BarFinding(
            code=QB_ABSURD_LENGTH, reel=name, severity=ERROR,
            message=(
                f"runs {duration['delivered_seconds']:.1f}s, past the "
                f"{ABSURD_SECONDS:.0f}s at which a candidate is most of "
                f"the episode rather than a reel"),
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
    if cta.get("opens_mid_sentence"):
        head = cta.get("opening_head") or ""
        shared = ", ".join(
            str(n) for n in cta.get("shared_with") or []) or "none"
        out.append(BarFinding(
            code=QB_CTA_OPENS_MID_SENTENCE, reel=name, severity=WARNING,
            message=(
                f"its closer opens on the back half of a sentence whose "
                f"head ({head!r}) the reel never plays, so the invitation "
                f"starts mid-thought. Redrawing the start moves every reel "
                f"sharing this span ({shared}), which is the captain's "
                f"call, not a trim"),
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
        name="claim_parts", grounding="contains", required=False,
        asks="if the one thing it is saying is made of parts a listener "
             "has to hold together, those parts in the order the reel "
             "says them, each with the words where it says it; empty if "
             "it is one indivisible statement"),
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
            "claim_parts": [{"part": "string", "quote": "string"}],
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
    claim_parts: Tuple[dict, ...] = ()
    """The claim's parts in the order the reel says them, each with the
    words where it says it.  OPTIONAL and usually empty: a claim that is
    one indivisible statement has no parts, and `()` is the honest
    answer for most reels.  `library/tools/explainer_plan.py` is the one
    reader - it anchors each part to a reel second by searching for its
    quote, which is the only thing that makes a staged explainer
    expressible."""
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
            "claim_parts": [dict(a) for a in self.claim_parts],
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

        # `claim_parts` is grounded exactly as `assumes_known` is, and
        # for a sharper reason: its quote is not evidence for the part,
        # it is the ONLY thing that puts the part in time.
        # `explainer_plan.anchor_stages` anchors each part by searching
        # the reel's own lines for that quote, so an ungrounded part is
        # a part that cannot be drawn at all rather than one drawn
        # without support.
        if f.name == "claim_parts":
            for item in (entry.get("claim_parts") or []):
                what = (item or {}).get("part")
                quote = normalise((item or {}).get("quote"))
                if not what:
                    ungrounded.append(
                        "a claim_parts entry carries no `part`, so there is "
                        "nothing to hold")
                elif not quote:
                    ungrounded.append(
                        f"claim_parts entry {what!r} carries no quote, so "
                        f"nothing in the reel says when it is said")
                elif quote not in body:
                    ungrounded.append(
                        f"claim_parts quote {(item or {}).get('quote')!r} "
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
        claim_parts=tuple(dict(a) for a in
                          (entry.get("claim_parts") or [])),
        stops_developing_at=stops,
        rank=rank,
        basis=str(entry.get("basis") or ""),
        ungrounded=tuple(ungrounded),
        misplaced=tuple(misplaced),
    )


# ── JUDGEMENT: the verdicts the ENGINE derives ───────────────────────

OPENING = "opening"
IN_CALL_TO_ACTION = "in_call_to_action"
BODY = "body"
"""WHERE in a reel one of its dependencies is said.

`OPENING` means the quote begins at the reel's FIRST WORD - offset zero,
which is a position and not a window somebody chose.  There is
deliberately no "first N words" and no "first sentence": both would be a
boundary this module invented, and the transcript's own segments are
between 2 and 20 words long on the field-test episode, so a boundary
drawn on them would move with the chunker rather than with the reel.

`IN_CALL_TO_ACTION` means the quote lies in the range `reel_ranges` lays
down last - the closer.  That one matters more than it looks: see
`COHERENCE_DOES_NOT_GATE`.
"""


def dependency_positions(reading: Optional[Reading], moment,
                         transcript: dict) -> List[dict]:
    """Each thing the reel leans on, and WHERE the reel says it.

    Pure arithmetic over a quote `check_reading` has already established
    is in the reel - so this adds no judgement of its own and cannot
    disagree with the reading it is given.  A quote that is not in the
    reel is skipped rather than placed, because the reading carrying it
    was refused and nothing is derived from a refused reading.

    `at_word` is the FIRST occurrence, which is the same containment
    `check_reading` uses; a quote a reel says twice is placed at the
    first of them and that is stated rather than hidden.
    """
    if reading is None or reading.refused or not reading.assumes_known:
        return []
    words = normalise(reel_text(moment, transcript))
    if not words:
        return []

    closer_at = None
    if cta_range_of(moment) is not None:
        lines = played_speech(moment, transcript)
        last = max((line["range"] for line in lines), default=None)
        if last is not None and last > 0:
            before = " ".join(line["text"] for line in lines
                              if line["range"] < last)
            closer_at = len(normalise(before).split())

    placed: List[dict] = []
    for item in reading.assumes_known:
        quote = normalise((item or {}).get("quote"))
        if not quote or quote not in words:
            continue
        at = len(words[:words.index(quote)].split())
        if at == 0:
            where = OPENING
        elif closer_at is not None and at >= closer_at:
            where = IN_CALL_TO_ACTION
        else:
            where = BODY
        placed.append({
            "what": (item or {}).get("what"),
            "quote": (item or {}).get("quote"),
            "at_word": at,
            "position": where,
        })
    return placed


def cta_range_of(moment):
    """`reel_build.cta_range`, imported where it is used."""
    from library.tools.reel_build import cta_range

    return cta_range(moment)


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
    dependencies: List[dict] = field(default_factory=list)
    """What this reel leans on, each placed at the word it is said -
    `dependency_positions`.  Exact, and the reason coherence can be
    recorded usefully without deciding anything."""

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
            "coherence_gates": False,
            "duration_gates": False,
            "dependencies": list(self.dependencies),
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
            "verdicts": [v.as_dict() for v in self.verdicts],
        }


def judge(moments: Sequence, transcript: dict,
          judgement: Optional[dict] = None,
          project_folder: Optional[str] = None) -> BarReport:
    """The bar, over a whole batch.

    `judgement` is the model's readings - `{"readings": [...]}` - or
    None.  Absent, the EXACT half still runs in full and the two
    JUDGEMENT qualities read UNJUDGED; that is deliberate, because
    duration and the closer are properties of the plan and are worth
    holding whether or not anyone has been asked to read the reels yet.

    `project_folder` supplies the project's hand-written thesis
    endings (`external/reel_ending.json`): a closer-less reel one
    declares and honours reads `thesis`, never ABSENT. Absent (every
    existing caller without a project in hand) reads exactly what it
    read before - an unreadable endings file REFUSES rather than
    reading as undeclared, because quiet non-application is the
    defect `load_endings` exists to end.
    """
    closers = declared_closers(moments)
    entries = {}
    for entry in ((judgement or {}).get("readings") or []):
        try:
            entries[int(entry.get("reel"))] = entry
        except (TypeError, ValueError):
            continue

    theses: Dict[int, dict] = {}
    if project_folder:
        from library.tools import reel_ending as _endings
        endings = _endings.load_endings(project_folder)
        by_number = {}
        for moment in moments:
            by_number.setdefault(str(moment.timeline_name),
                                 int(moment.number))
        for ending in endings:
            for name, number in by_number.items():
                if name == ending["reel"] or name.startswith(
                        ending["reel"]):
                    theses[number] = ending

    report = BarReport(judged=bool(entries))
    for moment in moments:
        number = int(moment.number)
        thesis = theses.get(number)
        findings = exact_findings(moment, transcript, closers,
                                  thesis=thesis,
                                  project_folder=project_folder)
        duration = duration_reading(moment, transcript, project_folder)
        cta = cta_reading(moment, transcript, closers, thesis=thesis)

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
        # RECORDED, never signalled.  The QB-NOT-FOLLOWABLE warning that
        # used to fire here was removed on 2026-09-18: it read
        # not_followable on 29 of 31 script-QA moments, 20 of the 22 the
        # reading approved, so it could not be what separated approved
        # from rejected (`DURATION_GATE_REMOVED`, and the module
        # docstring).  What stays is the exact half - every dependency
        # placed at the word the reel says it - and the derived value,
        # both on the verdict as evidence a reader weighs.
        positions = dependency_positions(reading, moment, transcript)
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
            coherence=coherence, value=value, reading=reading,
            dependencies=positions))
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
    lines = [
        f"REEL QUALITY BAR - {len(report.verdicts)} reel(s), "
        f"{len(report.passing)} pass, {len(report.failing)} fail",
        f"secs are what a viewer sits through - body, closer and the "
        f"ending the build appends; coherence and value are the "
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
"""Where a judgement placed BY HAND is looked for.

One spelling, here.  It is not where step 3.05 writes - the step writes
its output the way every step does, into
`pipeline_output/steps/3_05_judge_reels/output.json` - and reading only
this path was the defect `read_judgement` exists to remove."""


def judgement_path(project_folder: str):
    """Beside the proposal it judges, addressed the same way it is."""
    from library.tools.project_layout import Area, ProjectLayout

    return (ProjectLayout(str(project_folder))
            .read_path(Area.REVIEW, JUDGEMENT_FILENAME))


def step_judgement_path(project_folder: str):
    """Where step 3.05 really writes: its own step directory.

    `step_exporter.export_step_output` writes `<step_dir>/output.json`
    for every step, and 3.05 is not an exception.  Addressed through
    `ProjectLayout.step_dir` rather than composed, so the one owner of
    the project-side layout stays the one owner (AGENTS.md 8).
    """
    from library.tools.project_layout import STEP_OUTPUT_FILE, ProjectLayout

    return ProjectLayout(str(project_folder)).step_dir(
        "judge_reels") / STEP_OUTPUT_FILE


def read_judgement(project_folder: str):
    """Step 3.05's reading of these reels, or None, and WHERE it came from.

    Returns `(judgement, source_path)`; `(None, "")` when there is none.

    The defect this removes
    -----------------------
    Step 3.05 computes `reel_judgement` and returns it as its output, so
    the runner stores it in `pipeline_data.json` and exports it to the
    step's own directory.  Both readers of a judgement - this module's
    CLI and `reel_conformance_verifier`, which is what step 7.02 runs -
    opened `review/reel_judgement.json` instead, **and nothing in this
    repository ever wrote that file.**  So coherence and value, two of
    the captain's four reel qualities, read UNJUDGED on every
    verification, and the message printed beside them said "Run
    judge_reels (step 3.05)" - which could be run, and changed nothing.

    On the captain's `geo-podcast` the file exists and is byte-for-byte
    the step's own `reel_judgement`, written one minute after the step
    ran: the middle of the chain was a person.  That is the same defect
    `reel_proposal.write_from_step_output` was written to remove for the
    proposal, and the same fix does not apply here - the proposal is a
    file the captain RULES on, so it has to be a file; a judgement is
    only ever the model's reading, so it is read from where the step
    wrote it.

    The hand-placed file still WINS when it exists, because a captain
    who put one there meant it to be read.
    """
    for path in (judgement_path(project_folder),
                 step_judgement_path(project_folder)):
        try:
            with open(path, encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError):
            continue
        # The step's own output carries the judgement under its key
        # beside whatever else the step emitted; a hand-placed file is
        # the judgement itself. Both are accepted, and neither is
        # guessed at: the key is the one `post_bridge.resolve` returns.
        if isinstance(document, dict) and "reel_judgement" in document:
            document = document["reel_judgement"]
        if isinstance(document, dict):
            return document, str(path)
    return None, ""


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
                             f"review/{JUDGEMENT_FILENAME} when it has "
                             f"one, otherwise step 3.05's own output).")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of a table.")
    args = parser.parse_args(argv)

    from library.tools.reel_proposal import proposal_path, read_proposal
    from library.tools.timeline_transcript import transcript_path

    project = os.path.abspath(args.project)
    moments = read_proposal(args.plan or proposal_path(project))
    transcript = json.loads(
        transcript_path(project).read_text(encoding="utf-8"))

    if args.judgement:
        try:
            with open(args.judgement, encoding="utf-8") as handle:
                judgement, source = json.load(handle), args.judgement
        except (OSError, ValueError):
            judgement, source = None, ""
    else:
        judgement, source = read_judgement(project)
    if judgement is None:
        print(f"no reading of these reels on file - the two EXACT "
              f"qualities are held below and coherence and value read "
              f"UNJUDGED. Looked in {judgement_path(project)} and "
              f"{step_judgement_path(project)}.\n", file=sys.stderr)
    else:
        print(f"reading these reels from {source}\n", file=sys.stderr)

    report = judge(moments, transcript, judgement,
                   project_folder=project)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(format_table(report))
    return 1 if report.failing else 0


assert_qualities_are_well_formed()


if __name__ == "__main__":
    sys.exit(main())
