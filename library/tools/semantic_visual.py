"""A visual on screen because of what is being SAID.

The captain, 2026-09-08: *"i really want to be able to recreate some vox
style animations but through our pipeline ... what we are doing right now
is just animating the text from the audio of the video, but i want like
intricate fidelity here in being able to create visuals that actually
coincide along with the audio itself. like for example if im talking about
money, then having assets of currency animated in."*

The gap is SELECTION, not drawing: the composition already draws bars,
counters, stamps and accents. What was missing is the step that reads
"we're talking about money here" and asks for currency - and the timing
that puts it ON the word rather than near it.

What this module is, and is not
-------------------------------
This module resolves TIMING. A plan entry names an `anchor_phrase` - words
from the speech, in the model's own spelling - and this module searches
the measured word timings for that phrase and returns its window in
timeline seconds. That is the AGENTS.md 6 discipline (anchored by SEARCH,
never asserted) applied to the overlay layer.

This module never decides WHAT the visual is. The entry carries `subject`
as free text - the model's reasoning, "money - paid advertising budgets" -
and it travels onto the resolved moment as provenance. Nothing here reads
it: the same phrase with two different subjects resolves identically
(`tests/test_semantic_visual.py::test_the_subject_is_inert`), which is
what makes a keyword-to-icon table impossible rather than merely absent.
An engine-side dict from words to glyphs would be hardcoded taste
(AGENTS.md 10.5), so its absence is asserted structurally in the same
file.

What the asset is
-----------------
`ASSET_SOURCE` states it in one place: the mark is COMPOSED from type and
flat shapes the renderer already draws - a glyph the plan names, on a
backplate in a colour the plan states. No network call, no licence to
clear, no file to fetch. A fetched illustration would need a licence and
a network round trip at render time; a generated one would need a model
this pipeline does not run. Saying which source was chosen, and what it
costs, is the brief's explicit demand, so it lives here rather than in a
commit message.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple

#: What the asset IS, and what that choice costs. One place, stated
#: plainly, because the brief demands the honesty be in the mechanism.
ASSET_SOURCE = (
    "composed: the mark is drawn from type and flat shapes the MotionGraphics "
    "composition already renders - a glyph the plan names in `copy`, on a "
    "backplate in a colour the plan states. No network fetch, no licensed "
    "file, nothing staged from outside the run. A fetched illustration "
    "would cost a licence and a network round trip at render time; a "
    "generated one would cost a model this pipeline does not run."
)

#: Punctuation stripped before two words are compared. The model quotes
#: from a summary table, not from the timing track, so "money," must
#: match "money" - normalising is reading, not choosing.
_PUNCT = re.compile(r"[^\w\s']", re.UNICODE)


class SemanticVisualError(ValueError):
    """An anchor phrase that cannot become a timing, carrying its reason.

    The reason is one of the anchor drop reasons `motion_graphics_plan`
    also refuses by name, so the resolver and this module cannot drift
    into two vocabularies for one refusal.
    """

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


def normalize_word(raw: Any) -> str:
    """One word, lowercased, without surrounding punctuation."""
    text = str(raw or "").strip().lower()
    text = _PUNCT.sub("", text)
    return text.strip("'")


def _phrase_tokens(phrase: str) -> List[str]:
    return [t for t in (normalize_word(p) for p in str(phrase).split())
            if t]


def find_phrase_window(
    words: Sequence[Dict[str, Any]],
    phrase: str,
) -> Tuple[float, float]:
    """The timeline window where `phrase` is said, as (start, end).

    `words` carries `{word, start, end}` in TIMELINE seconds - the shape
    `collect_word_windows` produces. Every occurrence of the phrase's
    first word is tried in order and the first complete match wins; a
    match whose every word is timed returns the span from the first
    word's start to the last word's end.

    Refusals, never guesses: an unfindable phrase, an anchor word with
    no measurement, and no timings at all are three different absences
    and raise under three different reasons.
    """
    tokens = _phrase_tokens(phrase)
    if not tokens:
        raise SemanticVisualError(
            "anchor_phrase_not_found",
            f"{phrase!r} names no words to search for.")
    timed = [w for w in (words or []) if normalize_word(w.get("word"))]
    if not timed:
        raise SemanticVisualError(
            "no_word_timings_to_anchor_against",
            "no measured word timings reached the resolver on this run")
    norms = [normalize_word(w.get("word")) for w in timed]
    for i, first in enumerate(norms):
        if first != tokens[0]:
            continue
        span = timed[i:i + len(tokens)]
        if [normalize_word(w.get("word")) for w in span] != tokens:
            continue
        starts = [w.get("start") for w in span]
        ends = [w.get("end") for w in span]
        if any(s is None or e is None
               or isinstance(s, bool) or isinstance(e, bool)
               for s, e in zip(starts, ends)):
            raise SemanticVisualError(
                "anchor_word_untimed",
                f"{phrase!r} is said but its measured window is missing - "
                f"landing on an unmeasured word would be landing near it")
        return float(starts[0]), float(ends[-1])
    raise SemanticVisualError(
        "anchor_phrase_not_found",
        f"{phrase!r} occurs nowhere in the measured words")


def collect_word_windows(audio_spine: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every timed word of the spine, in TIMELINE seconds.

    Spine word timings ride in SOURCE seconds (AGENTS.md 6) while the
    overlay layer times in timeline seconds. Each block carries both
    clocks - `timeline_start` and `source_start` - so the mapping is
    arithmetic, not a guess. Blocks missing either clock, or words
    missing either end, are skipped: a window that cannot be placed on
    the timeline is not a window.
    """
    windows: List[Dict[str, Any]] = []
    for block in (audio_spine or {}).get("structure", []) or []:
        timeline_start = block.get("timeline_start")
        source_start = block.get("source_start")
        if (timeline_start is None or source_start is None
                or isinstance(timeline_start, bool)
                or isinstance(source_start, bool)):
            continue
        try:
            offset = float(timeline_start) - float(source_start)
        except (TypeError, ValueError):
            continue
        for w in block.get("word_timestamps") or []:
            start, end = w.get("start"), w.get("end")
            if (start is None or end is None
                    or isinstance(start, bool) or isinstance(end, bool)):
                continue
            try:
                windows.append({
                    "word": str(w.get("word", "")),
                    "start": float(start) + offset,
                    "end": float(end) + offset,
                })
            except (TypeError, ValueError):
                continue
    return sorted(windows, key=lambda w: (w["start"], w["end"]))


def resolve_anchor_timing(
    entry: Dict[str, Any],
    words: Sequence[Dict[str, Any]],
) -> Tuple[float, float, str]:
    """An anchored entry's (start, duration, basis) in timeline seconds.

    The visual ARRIVES on the first anchored word. How long it holds is
    the plan's number - `hold_seconds` - and only when the plan states
    none does the measurement answer: the words' own span. A hold is a
    magnitude and magnitudes belong to whoever declares them; the span
    fallback is what "cued to its own measured word window" means when
    the plan declines to say more.
    """
    phrase = str(entry.get("anchor_phrase") or "").strip()
    start, end = find_phrase_window(words, phrase)
    hold = entry.get("hold_seconds")
    if hold is None:
        duration = end - start
    else:
        try:
            duration = float(hold)
        except (TypeError, ValueError):
            raise SemanticVisualError(
                "no_timing_declared",
                f"hold_seconds={hold!r} is not a number")
    if duration <= 0:
        raise SemanticVisualError(
            "no_timing_declared",
            f"hold_seconds={hold!r} holds no frames")
    lead = entry.get("lead_seconds") or 0.0
    try:
        lead = float(lead)
    except (TypeError, ValueError):
        raise SemanticVisualError(
            "no_timing_declared",
            f"lead_seconds={entry.get('lead_seconds')!r} is not a number")
    return max(0.0, start - lead), duration, f"word_window:{phrase}"


# A name the resolver checks for without importing this module's
# implementation detail into its own drop table twice. Kept beside the
# phrase key so a reader of a plan sees both spellings in one place.
ANCHOR_PHRASE_KEY = "anchor_phrase"
SUBJECT_KEY = "subject"


def entry_subject(entry: Dict[str, Any]) -> str:
    """The model's free-text subject, carried as provenance, never read."""
    raw = entry.get(SUBJECT_KEY)
    return str(raw).strip() if isinstance(raw, (str, int, float)) else ""
