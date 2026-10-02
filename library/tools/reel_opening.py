"""What the first seconds of a reel actually say, reported to the model.

Why this exists
---------------
Step 3.4's handoff already states the rule, in its own words:

    **It opens on a hook.** The first line has to earn the next five
    seconds: a question, a claim, or a provocation. Not throat-clearing,
    not a speaker settling into a sentence, not "yeah, so".

Nothing measured whether it was obeyed. Of the nineteen reels the captain
approved on 2026-09-05, four open on exactly what that paragraph forbids:

- reel 01 "okay so i'm hearing just from a lot of different marketing
  directors" - a speaker settling into a sentence;
- reel 02 "Yeah. So" - two words, because the take carrying the setup was
  cut as a redundant take and nothing re-read the opening afterwards;
- reel 04 "earlier it doesn't even matter if you're a 15 man shop" - the
  word "earlier" points at something outside the reel;
- reel 17 "Let's take that example. You are able to find that nail salon"
  - the example belongs to reel 16 and this viewer has not seen it.

A rule stated to a model and never checked is the vacuous gate this
repository keeps removing (AGENTS.md 10.4), and it is the same shape as
the caption check that expected zero cards and passed.

What this is NOT
----------------
**There is no score, no threshold and no rejection here.** The captain
ruled that taste belongs to the model and no threshold may be invented
for it, so this module never decides whether an opening is good. It
reports two things a machine can see and a model cannot see from a
transcript alone, and then gets out of the way:

- a **back-reference**: the opening points at something the reel does not
  contain. "earlier", "that example", "like I said" - deixis with no
  referent inside the span.
- an **answer with no question**: the opening is shaped like a reply -
  "Absolutely", "Yeah, so", "Correct" - and no question is asked inside
  the reel before it. The viewer hears an answer to something they never
  heard asked.

Both are facts about the span, not judgements about the writing. A reel
that opens on a claim, a provocation or a question produces nothing here,
and so does one whose answer-shaped opener really does follow a question
that is inside the reel.

**The observation names the fix it does not apply.** Every one of the four
is repaired by moving the start, which is the model's decision and is the
same kind of decision as where to end - so the observation says so and
leaves it there.

Where it is read
----------------
Two places, and they are the same measurement at two moments:

- `reel_exchange._concerns_for` puts it beside the monologue and
  no-question concerns on every CANDIDATE window, so the model sees it
  while it is choosing.
- `reel_proposal` records it per MOMENT, measured on the span the model
  actually named and after `reel_build.redundant_takes` has removed what
  it removes - which is the only way reel 02's two-word opening is
  visible at all, because the words that made it read as a hook are cut.

`tests/unit/reels/test_reel_selection.py`.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

OPENING_SECONDS = 3.0
"""How much of the reel counts as "the opening".

Not a threshold on quality - nothing here passes or fails. It is the
window a viewer decides in, and the same window the handoff's own rule
talks about when it says the first line has to earn the next five
seconds. Both observations below are about what the FIRST WORDS point at,
so the window only has to be long enough to contain them."""

_BACK_REFERENCE = re.compile(
    r"\b(earlier|before|previously|as (i|we|you) (said|mentioned|"
    r"were saying)|like (i|we) said|going back to|back to (that|what)|"
    r"that example|this example|that one|the one (i|we) (just|were)|"
    r"speaking of that|to that point|on that note)\b", re.I)
"""Deixis that points OUT of the reel.

"Let's take that example" and "earlier it doesn't even matter" are the
two the captain's own set produced. Each names something the viewer is
assumed to have already heard, and a reel is watched by somebody who has
heard nothing else."""

_ANSWER_SHAPED = re.compile(
    r"^\W*(absolutely|correct|exactly|right|yes|yeah|yep|no|"
    r"definitely|sure|of course|true|agreed)\b", re.I)
"""An opener that only makes sense as a reply.

Matched at the START only. "Yeah, so search didn't change" opens on a
reply; "the answer is yes" does not, and neither does a sentence that
merely contains the word."""

_QUESTION = re.compile(
    r"\?|\b(what|why|how|who|when|where|which|explain|tell (me|us)|"
    r"walk me|talk to me|give me an example|is it|does it|do you|"
    r"can you|could you|should)\b", re.I)
"""Whether anything in the span ASKS.

Deliberately the same shape as `reel_exchange._ASK`, and deliberately
generous: this decides whether an answer-shaped opener is REPORTED, so a
false positive here silently drops an observation and a false negative
only adds one the model can dismiss in a sentence."""


def observations(words: Sequence[dict],
                 span_text: str = "") -> List[Dict[str, str]]:
    """What the opening points at that the reel does not contain.

    `words` are the reel's first words IN REEL ORDER, each `{"word": ...}`
    - already through `reel_build.reel_time`, so a bad take removed before
      the opening has taken its words with it.
    `span_text` is everything the reel says, used only to answer "does a
    question precede this answer".

    Returns a list of `{observation, opening, why, the_fix}` and an EMPTY
    list for an opening that points at nothing outside itself. An empty
    list is a complete answer, not a missing one.
    """
    opening = " ".join(str(w.get("word", "")) for w in words).strip()
    if not opening:
        return []

    out: List[Dict[str, str]] = []

    hit = _BACK_REFERENCE.search(opening)
    if hit:
        out.append({
            "observation": "back_reference",
            "opening": opening,
            "matched": hit.group(0),
            "why": (f"the opening says {hit.group(0)!r}, which points at "
                    f"something said outside this reel - a viewer who has "
                    f"not heard the episode has nothing to attach it to"),
            "the_fix": ("move the start past the back-reference, or start "
                        "somewhere that stands alone"),
        })

    answer = _ANSWER_SHAPED.match(opening)
    if answer and not _asks_before(opening, span_text):
        out.append({
            "observation": "answer_with_no_question",
            "opening": opening,
            "matched": answer.group(1),
            "why": (f"the opening is a reply - it begins "
                    f"{answer.group(1)!r} - and nothing inside the reel "
                    f"asks anything before it, so the viewer hears an "
                    f"answer to a question they never heard"),
            "the_fix": ("start on the question this answers, if it is "
                        "close enough to include, or start after the "
                        "acknowledgement on the substance itself"),
        })

    return out


def _asks_before(opening: str, span_text: str) -> bool:
    """Is there a question in the span before the answer-shaped opener?

    The comparison is on the span's own text rather than on timings,
    because a question can be the tail of the same turn the reel opens
    inside. Absent `span_text` this answers False, which reports the
    observation - the model can dismiss it, and silence could not be
    dismissed at all.
    """
    if not span_text:
        return False
    head = span_text.strip()
    cut = head.lower().find(opening.strip().lower()[:40])
    before = head[:cut] if cut > 0 else ""
    return bool(_QUESTION.search(before))


def opening_words(ranges: Sequence[Tuple[float, float]],
                  transcript: dict,
                  seconds: float = OPENING_SECONDS) -> List[dict]:
    """The words a viewer hears in the reel's first `seconds`.

    Read off the PLAYED ranges, not off the span, because the two differ
    the moment a bad take is cut out of the opening - which is how reel 02
    came to open on "Yeah. So". `reel_time` is the arithmetic the picture
    goes through, so the captions and this agree by construction.

    Untimed words are skipped rather than guessed at: a segment carrying
    a full sentence of text with no word timings is a transcription
    artefact, and placing it in the opening would report a hook nobody
    spoke.

    **Only the FIRST SPEAKER's words are returned.** This is a two-mic
    recording and both tracks play, so the first three seconds of a reel
    contain whatever the other person was saying underneath - measured,
    reel 07's opening reads "Absolutely. broken And just like a hiring
    manner" once Craig's track is interleaved into Akshita's. The rule
    the handoff states is about the first LINE, which is one person
    speaking, and a reader handed the interleaving cannot see it.
    """
    from library.tools.reel_build import reel_time

    found: List[Tuple[float, dict]] = []
    for segment in (transcript.get("segments") or []):
        for word in (segment.get("words") or []):
            if not word.get("timed"):
                continue
            at = reel_time(float(word["start"]), ranges)
            if at is not None and at < seconds:
                found.append((at, {"word": word.get("word", ""),
                                   "at": at,
                                   "speaker": segment.get("speaker")}))
    found.sort(key=lambda pair: pair[0])
    if not found:
        return []
    opener = found[0][1].get("speaker")
    return [word for _, word in found if word.get("speaker") == opener]


def for_moment(moment, transcript: dict,
               span_text: str = "") -> List[Dict[str, str]]:
    """Observations on a moment's opening, as the reel will play it.

    Runs the moment through the SAME `reel_ranges` the builder does, so
    what is measured is what a viewer hears rather than what the span
    contains.
    """
    from library.tools.reel_build import reel_ranges

    try:
        ranges = reel_ranges(moment, transcript)
    except Exception:  # noqa: BLE001 - a span the builder refuses is
        # reported by the builder, in its own words. Declining to also
        # report a hook on it keeps one refusal per defect.
        return []
    return observations(opening_words(ranges, transcript), span_text)


def concern_lines(observed: Sequence[Dict[str, str]]) -> List[str]:
    """The observations as the one-line strings a concerns list carries.

    `reel_exchange._concerns_for` returns plain sentences and this has to
    read as one of them, not as a nested structure the reader has to
    unpack differently from its neighbours.
    """
    return [f"{item['why']} - {item['the_fix']}" for item in observed]
