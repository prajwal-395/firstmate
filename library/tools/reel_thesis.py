"""The post-build thesis check: does the reel make its point (G1).

Gap G1 (field-test structural scout, 2026-09-19): nothing in the
pipeline reads a sentence. No gate asks whether a reel makes a point,
completes an answer, follows its last line from its setup, or belongs
where it sits. Reels 04, 10, 13, 14 and 27 all failed on this and
every existing gate passed them - every gate is
arithmetic/geometry/timing.

What was wrong with each, from the record
-----------------------------------------
- Reel 04: the reel's entire point ("one is a search engine, the
  other is a decision engine") sits one sentence past the cut; the
  reel plays setup with no takeaway, then a shared website-checkout
  closer about nothing it set up.
- Reel 10: pure selection - after the audit verdict the reel jumps
  ~18 master-minutes to a sales pitch instead of playing the
  answer ("your website is your resume ..."), which exists verbatim
  immediately after.
- Reel 13: a 7.6s span from moment 5's pool plays after the reel's
  own closer, cut mid-thought. (`reel_ledger` measured the mechanism
  afterwards: the snap widened the closer end outward through the
  next segment, so the kept sequence carries it - which is why a
  reading over the kept words sees it.)
- Reel 14: setup good, closer good, middle missing - "that's crazy"
  then the pitch, with the actual substance (the biggest geo
  mistake, what AI rewards instead) never chosen.
- Reel 27: the shared website-checkout closer opens on "So..." with
  no antecedent and argues nothing about reviews; the answer itself
  ends cleanly one clip earlier.

The check
---------
A post-build reading over the reel's KEPT WORD SEQUENCE - text
already on disk via the transcript mapping (`reel_ranges` over the
transcript, the one place the play order is spelled), roughly 2k
tokens per reel. No render, no audio, no re-transcription. Three
questions: what is the point (quoted), does the last line follow
from the setup, does any span belong to another moment's bounds.
The engine checks every quote against the kept words and derives
`coherent` / `incoherent` from the model's own categorical answers;
a recorded `incoherent` over fresh words REFUSES that reel's
promotion, per reel, while passing siblings promote.

Why this shape, and what it is not
----------------------------------
A MACHINE gate, not a person: the captain ruled that an asked-for
edit is the approval, and he will not watch every reel through. So
this refuses by itself on a recorded reading and never routes a
judgement to him.

NO NUMBER DECIDES IT (standing ruling 2026-09-16). There is no
confidence threshold, no score floor and no count of flagged spans
here - not even a generous one. Numeric fields beside an answer
(`score`, `confidence`, `rating`, ...) are DROPPED unread. The
foreign-span rule is existential containment verified
deterministically (a quoted span whose master seconds lie outside
the reel's own declared windows and inside another moment's
declared body), the same class of measurement as
`closer_fit._overlaps` - a misplacement established, not a
threshold crossed. `EPSILON` is float hygiene borrowed from
`reel_ledger`, never a tolerance for content.

THE JUDGE IS ASKED FOR A READING, NEVER FOR A VERDICT
(`reel_quality_bar`): the prompt carries the kept words and nothing
else - no seam marking (a reader told which lines are the ending
reads them as the ending they are meant to be), no verdict
vocabulary. The engine derives the verdict afterwards from the
three answers, and every quote is checked by containment first. An
answer that cannot be checked (unguarded quotes, a foreign span in
no sibling's bounds, an empty reason) reads UNJUDGED - failed
evidence, never evidence of failure - and an unjudged reel
promotes with its report. A gate that strands work it cannot judge
is worse than none (AGENTS.md 10.4).

Reuse is NOT a defect here either: a closer shared with other reels
is inside the reel's own declared closer window, so it can never
verify as foreign, and sharing one is never itself a reason the
reading flags. Which question decided a refusal travels as
`decided_by` (`point` / `ending` / `foreign`) with the model's own
reason carrying the why.

Discrimination (the failure mode this is designed against)
---------------------------------------------------------
A coherence reading was REMOVED on 2026-09-18 for firing on 29 of
31 moments including 20 of the 22 it approved - a signal that says
the same thing about everything is worse than none. So before this
refuses anything, the record it has to beat: the five failing kept
sequences above read INCOHERENT (04 via point+ending, 10 via
point+ending, 13 via ending+foreign, 14 via point+ending, 27 via
ending), and the five fixed sequences from the same report (each
reel ending on its own thesis) read COHERENT.
`tests/test_reel_thesis.py` pins that separation on fixtures built
from the report's verbatim quotes. If a future batch shows this
reading constant across approved and rejected alike, DEMOTE it to a
report exactly the way the coherence warning was removed - do not
calibrate it with a number.

Where it runs
------------
- `survey` renders one prompt per approved reel; the model answers
  each; `record` files the answers into the sidecar
  (`review/reel_thesis.json`). A build places, it does not ask -
  verdicts come from the sidecar, never from a fresh judgement.
- The promotion gate (`gate_promotion`, called by
  `reel_build.promote_staged_reels` - the one path both the inline
  build and the `verify_reels` node promote through) recomputes
  each staged reel's kept words, compares the content hash, and
  refuses the reels with a fresh recorded `incoherent`, per reel.
  Missing sidecar, stale words, unjudged reels and unreadable
  inputs all REPORT and promote: an instrument must never fail the
  build it instruments.

`tests/test_reel_thesis.py`.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from library.tools.reel_build import ReelBuildError

#: The verdicts the ENGINE derives from a checked reading. The model is
#: never asked for one (see module docstring): it answers the three
#: questions and the derivation below honours them.
THESIS_VERDICTS = ("coherent", "incoherent")

#: A judgement that was never made. A reader state, never something a
#: model writes: `read_thesis_answer` returns this when the answer
#: cannot be checked, WITH the reason. An unjudged reel promotes with
#: its report - failed evidence is not evidence of failure.
UNJUDGED = "unjudged"

#: The two values the `last_follows` answer may carry. Observations,
#: not verdicts: which one the model wrote decides nothing until the
#: engine derives from it.
FOLLOWS = "follows"
DOES_NOT_FOLLOW = "does_not_follow"

SIDECAR_NAME = "reel_thesis.json"

#: Words a prompt for this reading must never contain. The derivation
#: names the verdicts, so a prompt carrying them would hand the model
#: the answer sheet (`reel_quality_bar.FORBIDDEN_IN_THE_ASK` is that
#: module's own list for its own ask; this is this ask's).
FORBIDDEN_IN_THESIS_ASK = (
    "coherent", "incoherent", "pass", "fail", "failing",
    "good", "bad", "quality", "score", "threshold",
    "verdict", "closer", "call to action", "ending",
)


class ThesisRefused(ReelBuildError):
    """Promotion refused reels whose recorded reading is incoherent.

    A `ReelBuildError` on purpose: every existing promotion handler
    (the guard's partial path here, `step_7_02_verify_reels`, the
    inline build) already catches that for "passing reels landed,
    refused ones stay staged", files the landed reels' marker losses
    off `.markers`, and re-raises. A new exception shape would need
    every one of those taught; a subclass rides them unchanged.

    Carries `refused` (final timeline names still staged, with their
    reasons) and `promoted` (final names that landed before the
    raise). A partial refusal still RAISES after the passing reels
    fully promoted - automation must not read it as clean - and the
    refused stagings stay in the project with their holds intact for
    a deliberate re-run (re-survey, fix and rebuild).
    """

    def __init__(self, refused: dict, promoted: list):
        self.refused = dict(refused)
        self.promoted = list(promoted)
        self.markers = {}
        names = ", ".join(sorted(self.refused))
        super().__init__(
            f"REFUSING to promote {len(self.refused)} reel(s) on the "
            f"recorded thesis reading: {names}. "
            + " ".join(
                f"{name}: {self.refused[name]}" for name in sorted(self.refused)
            )
            + (f" Promoted before this refusal: "
               f"{', '.join(sorted(self.promoted))}."
               if self.promoted else " Nothing promoted.")
            + " The refused stagings stay in the project with their "
            "holds - re-survey after a fix, or rebuild the reel onto "
            "its own thesis, then promote again.")


def _snapped(moment, transcript: dict):
    """This moment the way the build ranges it: snapped, in memory.

    Mirrors `closer_fit._snapped`: the stored proposals file predates
    the boundary drawer, so a stored boundary can sit inside a word -
    and the build repairs each moment on the way through
    (`reel_proposal.snap_moment_to_speech`) without rewriting the
    file. What a viewer hears is the SNAPPED ranges, so this is what
    the words are read off. A snap that itself raises returns the
    moment as stored: the builder's refusal then says why, in its own
    words.
    """
    try:
        from library.tools.reel_proposal import snap_moment_to_speech

        fixed, _moves = snap_moment_to_speech(moment, transcript)
        return fixed
    except Exception:  # noqa: BLE001 - the builder reports the refusal.
        return moment


def kept_words(moment, transcript: dict, *,
               extra_cuts=(), insisted_spans=()) -> List[dict]:
    """Every timed word this reel PLAYS, in the order it plays them.

    Read off the PLAYED ranges - the snapped moment through
    `reel_build.reel_ranges`, the one place the play order is
    spelled - through `reel_time`, the same arithmetic the picture
    goes through, so a bad take the cutter removed has taken its
    words with it here too. Each entry is `{"word", "speaker", "at",
    "master", "master_end"}` with `at` the reel second and
    `master`/`master_end` the transcript seconds (the Q3 evidence).
    A moment the builder refuses carries no words rather than a
    guess: the builder's refusal says why, in its own words, and a
    second report beside it would read as a second defect.

    The snap-widened closer is why this sees reel 13's tail: the
    repair widened the stored closer end outward through the next
    segment (`reel_ledger` measured it), so `reel_ranges` places
    those words and they are read here like any other kept word.
    """
    from library.tools.reel_build import reel_ranges, reel_time

    try:
        ranges = reel_ranges(_snapped(moment, transcript), transcript,
                             extra_cuts=extra_cuts,
                             insisted_spans=insisted_spans)
    except Exception:  # noqa: BLE001 - refused spans are the
        # builder's to report, not this instrument's.
        return []
    timed: List[Tuple[float, float, str, object]] = []
    for segment in (transcript or {}).get("segments") or ():
        speaker = segment.get("speaker")
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                start, end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            token = str(word.get("word") or "")
            if token and end > start:
                timed.append((start, end, token, speaker))
    found: List[Tuple[float, dict]] = []
    for start, end, token, speaker in timed:
        at = reel_time(start, ranges)
        if at is None:
            # A word the range end lands exactly on belongs to the
            # range it closes (`reel_time(at_end=True)`); the
            # half-open read above drops it, so check the closing
            # read before skipping (the `closer_fit` seam).
            at = reel_time(end, ranges, at_end=True)
            if at is None:
                continue
        found.append((at, {"word": token, "speaker": speaker, "at": at,
                           "master": start, "master_end": end}))
    found.sort(key=lambda pair: pair[0])
    return [word for _, word in found]


def kept_text(words: Sequence[dict]) -> str:
    """The kept words as one spoken line, in play order."""
    return " ".join(str(word.get("word", "")) for word in words or ()).strip()


def thesis_lines(moment, transcript: dict, *,
                 extra_cuts=(), insisted_spans=()) -> List[dict]:
    """What a viewer HEARS, as speaker-attributed lines, in play order.

    One spelling: `reel_quality_bar.played_speech` - the body with
    its bad takes cut and then the closer, the same list the builder,
    the caption pass and the judge all read. The prompt shows these
    lines; the engine checks quotes against `kept_text` (the same
    words, normalised). Word timings do not reach the prompt
    (AGENTS.md 10.1): no per-word seconds travel here.

    `played_speech` takes no insistences (the judge reads the same
    way), while `kept_words` withdraws insisted cuts - so the shown
    lines are a SUBSET of the checked words, never the reverse: a
    reader can only quote what it was shown, and everything shown is
    checked.
    """
    from library.tools.reel_quality_bar import played_speech

    try:
        lines = played_speech(_snapped(moment, transcript), transcript,
                              extra_cuts=extra_cuts)
    except Exception:  # noqa: BLE001 - the builder reports the refusal.
        return []
    return [{"speaker": line.get("speaker"), "says": line.get("text") or ""}
            for line in lines or []
            if (line.get("text") or "").strip()]


def _speakers_in(words: Sequence[dict]) -> List[str]:
    """The voices in these words, first-heard order."""
    speakers: List[str] = []
    for word in words or ():
        speaker = word.get("speaker")
        if speaker and speaker not in speakers:
            speakers.append(speaker)
    return speakers


def content_hash(kept: str, sibling_bodies: Sequence[tuple] = ()) -> str:
    """A short digest of the words (and neighbour bounds) a reading
    covers.

    The survey records this beside each verdict; the gate compares it
    against the live words and reports a verdict whose words moved as
    STALE rather than as a verdict about the current reel. Sibling
    declared bodies join the hash because a foreign-span verdict is
    verified against them - a neighbour redrawn since the survey is a
    different check, not the recorded one. Twelve hex characters, the
    `closer_fit` convention: a judgement key, not a security
    boundary.
    """
    neighbours = ",".join(
        f"{float(start):.2f}-{float(end):.2f}"
        for start, end in (sibling_bodies or ()))
    digest = hashlib.sha256(
        f"{kept}\n---\n{neighbours}".encode("utf-8")).hexdigest()
    return digest[:12]


def declared_windows(moment) -> Tuple[Tuple[float, float], Optional[tuple]]:
    """This reel's own DECLARED windows: body, closer-or-None.

    One spelling: `reel_ledger.stored_windows` - read before any
    repair runs, because a repair widens boundaries outward and the
    repaired moment no longer says what was declared. The foreign
    check is verified against the DECLARATION, so a snap-widened
    closer cannot testify for itself.
    """
    from library.tools.reel_ledger import stored_windows

    return stored_windows(moment)


def sibling_bodies(moments: Sequence, own_number: int) -> List[dict]:
    """Every OTHER moment's declared body window, reel-number order.

    The Q3 evidence: a quoted span is foreign when its master seconds
    lie outside this reel's own declared windows and inside one of
    these. Declarations compared with declarations
    (`reel_ledger._invaded_reels` compares the same way); placed
    content never testifies about whose pool it is. A sibling closer
    is never listed: a shared ending clip is reuse, not a defect.
    """
    out = []
    for moment in moments or ():
        try:
            number = int(getattr(moment, "number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if number == int(own_number):
            continue
        body, _closer = declared_windows(moment)
        out.append({"reel": number,
                    "start": float(body[0]), "end": float(body[1])})
    out.sort(key=lambda row: row["reel"])
    return out


def thesis_context(moment, transcript: dict, moments: Sequence, *,
                   extra_cuts=(), insisted_spans=()) -> dict:
    """Everything a model needs to read one reel's thesis.

    The reel's own kept words as speaker-attributed lines in play
    order, who speaks, and how many words - REPORTED, never decided:
    an empty kept sequence is reported as empty, not filled in. No
    seam marking (see module docstring), no verdict vocabulary, no
    measurements beyond the words. Sibling declared bodies join the
    content hash (see `content_hash`) but are NOT shown: the model
    flags spans that read as belonging elsewhere, and the engine
    verifies each flag against the declarations itself.
    """
    number = int(getattr(moment, "number", 0) or 0)
    words = kept_words(moment, transcript, extra_cuts=extra_cuts,
                       insisted_spans=insisted_spans)
    lines = thesis_lines(moment, transcript, extra_cuts=extra_cuts,
                         insisted_spans=insisted_spans)
    text = kept_text(words)
    siblings = sibling_bodies(moments, number)
    neighbour_windows = [(row["start"], row["end"]) for row in siblings]
    return {
        "reel": number,
        "name": str(getattr(moment, "timeline_name", "")
                    or getattr(moment, "name", "") or ""),
        "lines": lines,
        "kept_text": text,
        "speakers": _speakers_in(words),
        "word_count": len(words),
        "sibling_count": len(siblings),
        "content_hash": content_hash(text, neighbour_windows),
    }


def render_thesis_prompt(context: dict) -> str:
    """The three questions for the model, given one reel's context.

    Point (quoted), last-line follow, foreign spans - and the one or
    two sentences saying what the reel adds up to, with the words
    that decide it. The verdict words appear nowhere here: the model
    answers the questions and the engine derives the verdict.
    """
    parts = [
        "You are reading one short reel as a stranger who has never "
        "heard the episode. Read only what these lines say, in the "
        "order they play.",
        "",
        f"REEL {context.get('reel')}",
        "",
    ]
    lines = context.get("lines") or []
    if lines:
        for line in lines:
            speaker = line.get("speaker") or "Someone"
            parts.append(f"{speaker}: {line.get('says', '')}")
    else:
        parts.append("(no measured speech plays on this reel)")
    parts += [
        "",
        "Answer three questions as JSON with exactly these keys:",
        "{",
        '  "point": "the one thing this reel concludes, in one '
        'sentence of your own; if it sets something up it never '
        'resolves, say what is left open instead",',
        '  "point_quote": "the words where it concludes it, copied '
        'exactly from the lines above; empty when it never does",',
        '  "last_follows": "follows" | "does_not_follow",',
        '  "closing_quote": "the last words a viewer hears, copied '
        'exactly from the lines above",',
        '  "closing_reason": "one sentence on how those last words '
        'relate to what came before, citing the words",',
        '  "foreign_spans": [{"quote": "words copied exactly that read '
        'as part of a different conversation", "why": "one sentence"}],',
        '  "reason": "one or two sentences saying what this reel adds '
        'up to, citing the words that decide it"',
        "}",
        'Say "follows" when the last words answer, extend or land the '
        "reel's own point, and \"does_not_follow\" when they start a "
        "thought the reel never set up or answer a question the reel "
        "never asked. Leave \"foreign_spans\" empty when every span "
        "belongs here.",
    ]
    return "\n".join(parts)


def assert_ask_carries_no_verdict(text: str) -> None:
    """Raise if the thesis ask names a verdict word.

    The derivation honours the model's answers, so a prompt carrying
    the verdict vocabulary would hand it the answer sheet. Mechanical,
    like `reel_quality_bar.assert_ask_is_uncontaminated`, over this
    ask's own list.
    """
    found = sorted({
        word for word in FORBIDDEN_IN_THESIS_ASK
        if re.search(r"\b" + re.escape(word) + r"s?\b",
                     str(text), re.IGNORECASE)})
    if found:
        raise RuntimeError(
            f"the thesis ask contains {', '.join(repr(word) for word in found)}. "
            f"The model is asked three questions about the reel's words "
            f"and never for a verdict on it: a reader that knows which "
            f"way an answer counts grades itself.")


def _word_tokens(words: Sequence[dict]) -> List[str]:
    """Normalised tokens of the kept words, in play order."""
    from library.tools.reel_quality_bar import normalise

    return normalise(" ".join(
        str(word.get("word", "")) for word in words or ())).split()


def _locate_quote(quote: str, words: Sequence[dict]) -> Optional[tuple]:
    """The master span of a quote's first occurrence, or None.

    Containment on normalised tokens - present or absent, no
    fraction, no similarity, no threshold
    (`reel_quality_bar.check_reading` is the same contract). The
    FIRST occurrence places it, said rather than hidden.
    """
    from library.tools.reel_quality_bar import normalise

    want = normalise(quote).split()
    if not want:
        return None
    tokens = _word_tokens(words)
    for index in range(len(tokens) - len(want) + 1):
        if tokens[index:index + len(want)] == want:
            first = words[index]
            last = words[index + len(want) - 1]
            try:
                return (float(first["master"]), float(last["master_end"]))
            except (KeyError, TypeError, ValueError):
                return None
    return None


def _outside_own(span: tuple, own_body: tuple,
                 own_closer: Optional[tuple]) -> List[tuple]:
    """The parts of a master span outside the reel's own declarations.

    Float hygiene only (`reel_ledger.EPSILON`): a boundary the snap
    wrote as 349.53999999999996 for 349.54 must still read as contact.
    Hygiene never admits content - an overhang is measured exactly.
    """
    from library.tools.reel_ledger import EPSILON

    parts = [span]
    for window in [own_body] + ([own_closer] if own_closer else []):
        if window is None:
            continue
        remaining = []
        for start, end in parts:
            if end <= float(window[0]) + EPSILON or \
                    start >= float(window[1]) - EPSILON:
                remaining.append((start, end))
                continue
            if start < float(window[0]) - EPSILON:
                remaining.append((start, float(window[0])))
            if end > float(window[1]) + EPSILON:
                remaining.append((float(window[1]), end))
        parts = remaining
    return [(round(start, 3), round(end, 3)) for start, end in parts
            if end - start > EPSILON]


def _inside_a_sibling_body(parts: Sequence[tuple],
                           siblings: Sequence[dict]) -> Optional[dict]:
    """The sibling body a foreign part falls in, or None.

    Overlap, not containment of the whole part: a span straddling a
    declaration edge still names the pool it reaches into, and the
    recorded seconds say which part. Shared endings never testify -
    only declared BODIES are listed, so a reused closer cannot verify
    as foreign.
    """
    from library.tools.reel_ledger import EPSILON

    for start, end in parts or ():
        for sibling in siblings or ():
            overlap = (min(end, float(sibling["end"]))
                       - max(start, float(sibling["start"])))
            if overlap > EPSILON:
                return {"reel": int(sibling["reel"]),
                        "seconds": [round(max(start, float(sibling["start"])), 3),
                                    round(min(end, float(sibling["end"])), 3)]}
    return None


def read_thesis_answer(data, *, words: Sequence[dict],
                       kept: str, own_body: tuple,
                       own_closer: Optional[tuple],
                       siblings: Sequence[dict]) -> dict:
    """Read a model's thesis answer as `{verdict, reason, decided_by}`.

    The three answers are checked, then the verdict is DERIVED from
    the model's own categorical answers - never asked, never scored:

    - the point is unstated (`point_quote` empty) -> `incoherent`
      (`decided_by: point`);
    - the last words do not follow (`last_follows` is
      `does_not_follow`) -> `incoherent` (`decided_by: ending`);
    - a quoted span verifies outside the reel's own declared windows
      and inside another moment's declared body -> `incoherent`
      (`decided_by: foreign`).

    Anything that cannot be checked - a missing key, an unknown
    `last_follows`, an empty reason, a quote nowhere in the kept
    words, a claimed foreign span in no sibling's bounds - reads
    UNJUDGED with the reason it could not be read, never as a low
    mark and never coerced to a verdict. Numeric fields beside the
    answers (`score`, `confidence`, `rating`, ...) are DROPPED
    unread: there is no magnitude in this judgement, and reading one
    would invent a scale the captain refused.
    """
    if not isinstance(data, dict):
        return {"verdict": UNJUDGED,
                "reason": "no answer was recorded for this reel",
                "decided_by": []}
    last_follows = data.get("last_follows")
    if last_follows not in (FOLLOWS, DOES_NOT_FOLLOW):
        return {"verdict": UNJUDGED,
                "reason": ("the answer carries no usable last-line "
                           f"reading ({last_follows!r} is not "
                           "follows|does_not_follow)"),
                "decided_by": []}
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return {"verdict": UNJUDGED,
                "reason": ("the answer carries no reason, and a reading "
                           "without one cannot be shown to anyone"),
                "decided_by": []}
    from library.tools.reel_quality_bar import normalise

    body = normalise(kept)
    point_quote = data.get("point_quote") or ""
    if isinstance(point_quote, str) and point_quote.strip():
        if normalise(point_quote) not in body:
            return {"verdict": UNJUDGED,
                    "reason": (f"point_quote {point_quote[:70]!r} is not "
                               "in what this reel says"),
                    "decided_by": []}
    closing_quote = data.get("closing_quote") or ""
    if isinstance(closing_quote, str) and closing_quote.strip():
        if normalise(closing_quote) not in body:
            return {"verdict": UNJUDGED,
                    "reason": (f"closing_quote {closing_quote[:70]!r} is "
                               "not in what this reel says"),
                    "decided_by": []}
    foreign = data.get("foreign_spans") or []
    if not isinstance(foreign, list):
        return {"verdict": UNJUDGED,
                "reason": "foreign_spans is not a list, so there is "
                          "nothing to check it against",
                "decided_by": []}
    verified = []
    for entry in foreign:
        quote = (entry or {}).get("quote") or ""
        if not isinstance(quote, str) or not quote.strip():
            return {"verdict": UNJUDGED,
                    "reason": ("a foreign_spans entry carries no quote, "
                               "so nothing in the reel can be checked "
                               "against it"),
                    "decided_by": []}
        span = _locate_quote(quote, words)
        if span is None:
            return {"verdict": UNJUDGED,
                    "reason": (f"foreign span {quote[:70]!r} is not in "
                               "what this reel says"),
                    "decided_by": []}
        outside = _outside_own(span, own_body, own_closer)
        if not outside:
            return {"verdict": UNJUDGED,
                    "reason": (f"foreign span {quote[:70]!r} lies inside "
                               "this reel's own declared windows - a "
                               "shared ending clip is reuse, not a defect"),
                    "decided_by": []}
        home = _inside_a_sibling_body(outside, siblings)
        if home is None:
            return {"verdict": UNJUDGED,
                    "reason": (f"foreign span {quote[:70]!r} falls in no "
                               "other moment's declared body"),
                    "decided_by": []}
        verified.append({"quote": quote.strip(), **home,
                         "why": str((entry or {}).get("why") or "")})
    decided_by = []
    if not (isinstance(point_quote, str) and point_quote.strip()):
        decided_by.append("point")
    if last_follows == DOES_NOT_FOLLOW:
        decided_by.append("ending")
    if verified:
        decided_by.append("foreign")
    if decided_by:
        return {"verdict": "incoherent", "reason": reason.strip(),
                "decided_by": decided_by, "verified_foreign": verified}
    return {"verdict": "coherent", "reason": reason.strip(),
            "decided_by": [], "verified_foreign": []}


def thesis_path(project_folder: str) -> Path:
    """Where the survey sidecar lives: review-side, beside proposals."""
    return Path(project_folder) / "pipeline_output" / "review" / SIDECAR_NAME


def read_thesis_verdicts(project_folder: str) -> dict:
    """The recorded thesis verdicts, keyed by reel number as a string.

    Missing or unreadable is `{}` - "not yet judged", never an error
    at promotion time. An instrument must never fail the build it
    instruments.
    """
    try:
        with open(thesis_path(project_folder), "r",
                  encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    verdicts = data.get("verdicts") if isinstance(data, dict) else None
    return verdicts if isinstance(verdicts, dict) else {}


def write_thesis_verdicts(project_folder: str, records: dict) -> Path:
    """File the survey's verdicts beside the proposals they judge.

    `records` maps reel number to `{verdict, reason, decided_by,
    judged_by, judged_at, content_hash, word_count}`. Returns the
    sidecar path.
    """
    path = thesis_path(project_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"verdicts": records,
               "sidecar": SIDECAR_NAME,
               "why": ("per-reel thesis judgements for gap G1: the kept "
                       "word sequence read with three questions, verdicts "
                       "derived by the engine, promotion refused on a "
                       "fresh incoherent. Recorded by the survey before "
                       "anything is promoted.")}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


def _load_survey_inputs(project_folder: str):
    """The approved moments and the transcript the survey reads from.

    Reads the live proposals file - what the captain approved, not a
    pipeline snapshot - and the timeline transcript the build reads.
    Every APPROVED moment is surveyed, closer or not: a reel ending
    on its own thesis (reel 27 fixed) has a point to read and no
    ending to excuse. Also loads the captain's recorded keep
    exclusions and insistences, so the kept words are what the build
    plays rather than what a raw take-cut pass would leave.
    """
    import json as _json

    from library.tools import transcript_corrections as _tc
    from library.tools.reel_proposal import proposal_path, read_proposal
    from library.tools.timeline_transcript import (
        transcript_path as _transcript_path)

    moments = read_proposal(str(proposal_path(project_folder)))
    surveyed = [moment for moment in moments
                if str(getattr(getattr(moment, "approval", ""),
                               "value", getattr(moment, "approval", "")))
                == "approved"]
    transcript_path = _transcript_path(project_folder)
    with open(transcript_path, "r", encoding="utf-8") as handle:
        transcript = _json.load(handle)
    return (surveyed, transcript,
            _tc.keep_exclusions(project_folder),
            _tc.keep_insistences(project_folder))


def ranges_inputs(moment, transcript: dict, keep_exclusions,
                  keep_insistences) -> tuple:
    """This moment's recorded cuts + insistences, without the chatter.

    ONE spelling: `reel_build.moment_cuts_and_insistences` owns the
    computation (`closer_fit.ranges_inputs` is the same call for the
    closer survey). That function prints what it honours for the
    build log; the survey is not the run that honours them, so
    stdout is held while it runs rather than interleaved into prompt
    files or JSON.
    """
    import contextlib
    import io

    from library.tools.reel_build import moment_cuts_and_insistences

    with contextlib.redirect_stdout(io.StringIO()):
        return moment_cuts_and_insistences(moment, transcript,
                                           keep_exclusions,
                                           keep_insistences)


def survey_contexts(project_folder: str) -> List[dict]:
    """One thesis context per surveyed reel, reel-number order."""
    surveyed, transcript, keep_exclusions, keep_insistences = \
        _load_survey_inputs(project_folder)
    contexts = []
    for moment in sorted(surveyed, key=moment_number):
        cuts, insisted = ranges_inputs(moment, transcript,
                                       keep_exclusions, keep_insistences)
        contexts.append(thesis_context(moment, transcript, surveyed,
                                       extra_cuts=cuts,
                                       insisted_spans=insisted))
    return contexts


def survey_verification(moment, transcript: dict, moments: Sequence, *,
                        extra_cuts=(), insisted_spans=()) -> dict:
    """The engine-side evidence one recorded answer is checked against.

    The kept words with their master positions, the reel's own
    declared windows and its siblings' declared bodies - everything
    `read_thesis_answer` needs that the prompt never carries.
    """
    words = kept_words(moment, transcript, extra_cuts=extra_cuts,
                       insisted_spans=insisted_spans)
    own_body, own_closer = declared_windows(moment)
    number = int(getattr(moment, "number", 0) or 0)
    return {"words": words, "kept": kept_text(words),
            "own_body": own_body, "own_closer": own_closer,
            "siblings": sibling_bodies(moments, number)}


def moment_number(moment) -> int:
    """A moment's reel number as an int, for sorting."""
    try:
        return int(getattr(moment, "number", 0) or 0)
    except (TypeError, ValueError):
        return 0


_REEL_LABEL_RE = re.compile(r"^reel\s+(\d+)\b", re.IGNORECASE)
"""A final timeline label naming a reel: `Reel 04 - slug`.

The label SHAPE is owned by `reel_proposal` (`REEL_NAME_FORMAT`,
and `_REEL_LABEL_RE` there parses render labels the same way): this
parses promotion-time final names so the gate can join them to plan
moments without re-deriving the build's own mapping. A label naming
no reel promotes without a reading, said aloud - guessing is
refused.
"""


def gate_promotion(project_folder: str,
                   staged_to_final: dict) -> dict:
    """Partition staged reels into promotable vs thesis-refused.

    Reads the live plan, the timeline transcript and the recorded
    thesis sidecar - text already on disk, no Resolve, no render -
    recomputes each staged reel's kept words and honours a FRESH
    recorded `incoherent` by refusing that reel. Returns
    `{"promotable": {final: staging}, "refused": {final: reason},
    "lines": [...]}`.

    FAIL-OPEN BY DESIGN: a reel with no recorded verdict, a verdict
    over different words (STALE), an unjudged reading, an unparsable
    label, or any unreadable input promotes with its report. The
    gate refuses exactly one thing - a grounded incoherent reading
    of the words the reel still plays - because a gate that strands
    work it cannot judge is worse than none (AGENTS.md 10.4).
    """
    lines: List[str] = []
    promotable = dict(staged_to_final or {})
    refused: Dict[str, str] = {}
    verdicts = read_thesis_verdicts(project_folder)
    if not verdicts:
        return {"promotable": promotable, "refused": refused, "lines": lines}
    try:
        from library.tools.reel_proposal import proposal_path, read_proposal
        from library.tools.timeline_transcript import (
            transcript_path as _transcript_path)
        from library.tools import transcript_corrections as _tc

        with open(proposal_path(project_folder), "r",
                  encoding="utf-8") as handle:
            moments = read_proposal(handle.name)
        with open(_transcript_path(project_folder), "r",
                  encoding="utf-8") as handle:
            transcript = json.load(handle)
        keep_exclusions = _tc.keep_exclusions(project_folder)
        keep_insistences = _tc.keep_insistences(project_folder)
    except Exception as exc:  # noqa: BLE001 - instrument, not gate.
        lines.append(f"  thesis gate unreadable ({exc}) - promoting "
                     f"without the reading")
        return {"promotable": promotable, "refused": refused, "lines": lines}
    by_number = {moment_number(moment): moment for moment in moments}
    for final in list((staged_to_final or {}).keys()):
        match = _REEL_LABEL_RE.match(str(final).strip())
        if not match:
            lines.append(f"  thesis: {final} names no reel - promoting "
                         f"without a reading")
            continue
        number = int(match.group(1))
        moment = by_number.get(number)
        if moment is None:
            lines.append(f"  thesis: reel {number} is not among the "
                         f"planned moments - promoting without a reading")
            continue
        verdict = verdicts.get(str(number))
        if not verdict:
            lines.append(f"  thesis: reel {number} not yet judged - run "
                         f"the thesis survey before promotion judges it")
            continue
        try:
            cuts, insisted = ranges_inputs(moment, transcript,
                                           keep_exclusions, keep_insistences)
            context = thesis_context(moment, transcript, moments,
                                     extra_cuts=cuts,
                                     insisted_spans=insisted)
        except Exception as exc:  # noqa: BLE001 - one unreadable reel
            # must not cost its siblings their promotion.
            lines.append(f"  thesis: reel {number} unreadable ({exc}) - "
                         f"promoting without the reading")
            continue
        live_hash = context.get("content_hash")
        if not verdict.get("content_hash") or \
                verdict.get("content_hash") != live_hash:
            lines.append(f"  thesis: reel {number} STALE "
                         f"({(verdict or {}).get('verdict', 'unjudged')} over "
                         f"different words) - promoting without the reading; "
                         f"re-survey this reel")
            continue
        if verdict.get("verdict") == "incoherent":
            reason = str(verdict.get("reason") or "").strip()
            decided = (verdict.get("decided_by") or [])
            refused[final] = (
                f"recorded thesis reading is incoherent "
                f"(decided by {', '.join(decided) if decided else 'the reading'}): "
                f"{reason}")
            lines.append(f"  thesis REFUSES reel {number}: {reason}")
            promotable.pop(final, None)
        elif verdict.get("verdict") == "coherent":
            lines.append(f"  thesis coherent: reel {number} - "
                         f"{str(verdict.get('reason') or '').strip()}")
        else:
            lines.append(f"  thesis: reel {number} unjudged "
                         f"({str(verdict.get('reason') or '').strip()}) - "
                         f"promoting with the report")
    if refused:
        lines.append(f"  thesis: {len(promotable)} promotable, "
                     f"{len(refused)} refused "
                     f"({', '.join(sorted(refused))}) - refused stagings "
                     f"stay with their holds")
    return {"promotable": promotable, "refused": refused, "lines": lines}


def summarize(verdicts: dict) -> List[str]:
    """The survey's deliverable: which reels read incoherent.

    `verdicts` maps reel number to its recorded `{verdict, reason,
    decided_by}`. Coherent, incoherent and unjudged reels are each
    named, so the count reads as measured rather than as complete
    when it is not.
    """
    incoherent, coherent, unjudged = [], [], []
    for key in sorted(verdicts, key=lambda k: int(k)):
        verdict = (verdicts[key] or {}).get("verdict")
        if verdict == "incoherent":
            incoherent.append(int(key))
        elif verdict == "coherent":
            coherent.append(int(key))
        else:
            unjudged.append(int(key))
    judged = len(incoherent) + len(coherent)
    lines = [f"thesis: {len(incoherent)} of {judged} judged reels incoherent"
             + (f": reels {', '.join(map(str, incoherent))}" if incoherent
                else " - every judged reel states its point and lands it")]
    for reel in incoherent:
        record = verdicts[str(reel)] or {}
        lines.append(f"  reel {reel} "
                     f"({', '.join(record.get('decided_by') or ['the reading'])}): "
                     f"{record.get('reason', '')}")
    if unjudged:
        lines.append(f"  unjudged: reels {', '.join(map(str, unjudged))} "
                     "- surveyed but without a usable reading, or not surveyed")
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`survey` renders prompts; `record` files answers; `report` prints.

    - `survey --project <path> [--prompts-dir <dir>]`: one prompt file
      per reel (or contexts as JSON on stdout without the dir). The
      model answers each with the `render_thesis_prompt` schema.
    - `record --project <path> --answers <json> [--judged-by <name>]`:
      checks each answer through `read_thesis_answer` and files the
      sidecar the promotion gate honours.
    - `report --project <path>`: the incoherent list with its count,
      from the sidecar.
    """
    parser = argparse.ArgumentParser(prog="reel_thesis")
    sub = parser.add_subparsers(dest="command", required=True)
    survey = sub.add_parser("survey", help="render one prompt per reel")
    survey.add_argument("--project", required=True)
    survey.add_argument("--prompts-dir", default="")
    record = sub.add_parser("record", help="file model answers")
    record.add_argument("--project", required=True)
    record.add_argument("--answers", required=True)
    record.add_argument("--judged-by", default="")
    report = sub.add_parser("report", help="print the incoherent list+count")
    report.add_argument("--project", required=True)
    args = parser.parse_args(argv)

    if args.command == "survey":
        contexts = survey_contexts(args.project)
        for context in contexts:
            assert_ask_carries_no_verdict(render_thesis_prompt(context))
        if args.prompts_dir:
            out_dir = Path(args.prompts_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            for context in contexts:
                path = out_dir / f"reel_{context['reel']:02d}_thesis.txt"
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(render_thesis_prompt(context))
            print(f"wrote {len(contexts)} prompt(s) to {out_dir}")
        else:
            printable = [{key: context[key] for key in
                          ("reel", "name", "lines", "speakers",
                           "word_count", "content_hash")}
                         for context in contexts]
            print(json.dumps(printable, indent=2))
        return 0
    if args.command == "record":
        with open(args.answers, "r", encoding="utf-8") as handle:
            answers = json.load(handle)
        surveyed, transcript, keep_exclusions, keep_insistences = \
            _load_survey_inputs(args.project)
        by_number = {moment_number(m): m for m in surveyed}
        stamped = (datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"))
        records = {}
        for key, answer in (answers or {}).items():
            try:
                number = int(key)
            except (TypeError, ValueError):
                continue
            moment = by_number.get(number)
            if moment is None:
                continue
            cuts, insisted = ranges_inputs(moment, transcript,
                                           keep_exclusions, keep_insistences)
            check = survey_verification(moment, transcript, surveyed,
                                        extra_cuts=cuts,
                                        insisted_spans=insisted)
            context = thesis_context(moment, transcript, surveyed,
                                     extra_cuts=cuts,
                                     insisted_spans=insisted)
            read = read_thesis_answer(
                answer, words=check["words"], kept=check["kept"],
                own_body=check["own_body"], own_closer=check["own_closer"],
                siblings=check["siblings"])
            records[str(number)] = {
                **read,
                "judged_by": args.judged_by or "model",
                "judged_at": stamped,
                "content_hash": context.get("content_hash"),
                "word_count": context.get("word_count"),
            }
        path = write_thesis_verdicts(args.project, records)
        print(f"filed {len(records)} verdict(s) to {path}")
        return 0
    if args.command == "report":
        for line in summarize(read_thesis_verdicts(args.project)):
            print(line, file=sys.stderr)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
