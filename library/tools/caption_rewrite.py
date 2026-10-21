"""Rewrite the text of a rendered caption card without moving its timing.

A caption fix is a TEXT change: the card says the wrong words and the
captain wants the right ones, with the timing, the placement and every
other caption untouched. A rendered card carries no text property - the
words are PIXELS - so the text cannot be edited in place. It is
re-rendered: the card's own measured word windows are re-fitted to the
new words (`rewrite_props` below), the card is drawn again through the
caption renderer (`subtitles.rerender_swap`), and the new file is
swapped onto the placement (`library/tools/caption_swap.py`).

What this module decides is the FIT: which frames each new word sweeps.
The card already carries the measured windows of the words it was drawn
from (`words` in the render props beside it), and a fix is not a
licence to invent new timings:

* a word that survives the rewrite keeps its measured window exactly;
* a run of words the rewrite REPLACED shares that run's measured span,
  split evenly, so the new words sweep where the old ones did;
* a run the rewrite INSERTED takes the gap between its neighbours,
  inside the card's own span.

A window that cannot be given a positive width REFUSES. A caption whose
words cannot be timed inside the card's measured span is not drawn: a
word with no sweep is a card that says something the timing does not,
and the refusal names the span that could not hold the text.

The card's cue keeps its own `startFrame`/`endFrame` - the fix changes
what the card says, never when it says it - and the emphasis words are
re-derived from the new text by the planner's own rule
(`step_4_01_plan_subtitles.identify_emphasis_words`), because an
emphasis word the new text does not contain would scale nothing.
"""

from __future__ import annotations

import difflib
import string
from collections.abc import Mapping, Sequence
from typing import Any


class CaptionRewriteError(ValueError):
    """The new text cannot be fitted onto the card's measured words.

    Raised, never repaired: the caller refuses the write rather than
    drawing a caption whose words the card's timing does not carry.
    """


def caption_fix_operation(unique_id: str, text: str) -> dict[str, Any]:
    """The `caption_fix` algebra operation for one caption card.

    The operation carries the text it applies. `edit_algebra` declares
    `text` REQUIRED on this op, so an operation that omits it never
    reaches a backend at all - and this is the ONE builder of a
    caption_fix operation, so the live fixture and any production
    caller state the same required parameter.
    """
    if not str(unique_id or "").strip():
        raise ValueError(
            "caption_fix names no unique_id - the card to fix is "
            "required, never inferred")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            "caption_fix needs the card's new text - an empty caption is "
            "a deletion, never a fix")
    return {"op": "caption_fix", "unique_id": str(unique_id),
            "text": str(text).strip()}


def _normalise(word: str) -> str:
    """A word compared by its letters, so `Revenue,` equals `revenue`."""
    return str(word).strip().strip(string.punctuation).lower()


def _tokens(text: str) -> list[str]:
    """The new text's words, exactly as the card will draw them."""
    return [token for token in str(text).split() if token]


def _emphasis_words(text: str) -> list[str]:
    """The new text's emphasis words, by the planner's own rule."""
    try:
        from library.steps.step_4_01_plan_subtitles.step import (
            identify_emphasis_words,
        )
    except ImportError:  # pragma: no cover - the planner is a sibling
        return []
    return list(identify_emphasis_words(text))


def fit_words(words: Sequence[Mapping], tokens: Sequence[str], *,
              span: tuple[int, int]) -> list[dict[str, Any]]:
    """The new words' sweep windows, fitted onto the card's own windows.

    `words` are the card's MEASURED windows (`word`, `startFrame`,
    `endFrame`), `tokens` the new text's words, `span` the card's own
    `[startFrame, endFrame]`.

    Raises `CaptionRewriteError` when a word cannot be given a window
    of positive width inside the card's span.
    """
    old = list(words)
    if not old:
        raise CaptionRewriteError(
            "the card carries no measured word windows - there is "
            "nothing to fit the new text onto")
    if not tokens:
        raise CaptionRewriteError("the new caption carries no words")
    old_words = [_normalise(entry.get("word", "")) for entry in old]
    new_words = [_normalise(token) for token in tokens]
    matcher = difflib.SequenceMatcher(
        None, old_words, new_words, autojunk=False)
    fitted: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # A word that survives keeps the window it was measured with.
            for offset, index in enumerate(range(i1, i2)):
                window = old[index]
                fitted.append({
                    "word": tokens[j1 + offset],
                    "startFrame": int(window["startFrame"]),
                    "endFrame": int(window["endFrame"])})
            continue
        if i2 > i1:
            # A replaced run: the new words share the span the old ones
            # were measured over.
            start = int(old[i1]["startFrame"])
            end = int(old[i2 - 1]["endFrame"])
        else:
            # An inserted run: the gap between its neighbours, inside the
            # card's own span.
            before = (int(old[i1 - 1]["endFrame"]) if i1 > 0
                      else int(span[0]))
            after = (int(old[i1]["startFrame"]) if i1 < len(old)
                     else int(span[1]))
            start, end = before, after
        count = j2 - j1
        if count <= 0:
            continue
        width = end - start
        if width < count:
            raise CaptionRewriteError(
                f"{count} new word(s) do not fit the {width}-frame span "
                "the card measured for them - the new caption cannot be "
                "timed inside the card it replaces")
        base, extra = divmod(width, count)
        cursor = start
        for offset in range(count):
            length = base + (1 if offset < extra else 0)
            fitted.append({"word": tokens[j1 + offset],
                           "startFrame": cursor,
                           "endFrame": cursor + length})
            cursor += length
    return fitted


def rewrite_props(props: Mapping[str, Any], new_text: str) -> dict[str, Any]:
    """The render props for the same card, carrying the new text.

    The card's cue keeps its own frames, its own bounds and its own
    style; only the text, the words' sweep windows and the emphasis
    words change. The returned props are a NEW mapping - the card's
    props on disk are never edited.
    """
    cues = props.get("subtitles")
    if not isinstance(cues, list) or not cues:
        raise CaptionRewriteError("the card carries no caption cue")
    if len(cues) != 1:
        raise CaptionRewriteError(
            f"the card holds {len(cues)} caption cues - a fix names one "
            "card's text, and a card drawing several cues cannot say "
            "which one is the wrong one")
    cue = cues[0]
    if not isinstance(cue, Mapping):
        raise CaptionRewriteError("the card's caption cue is unreadable")
    text = str(new_text).strip()
    if not text:
        raise CaptionRewriteError("the new caption carries no text")
    if text == str(cue.get("text") or "").strip():
        raise CaptionRewriteError(
            "the card already reads that text - there is nothing to fix")
    measured = cue.get("words")
    if not isinstance(measured, list) or not measured:
        raise CaptionRewriteError(
            "the card carries no measured word windows - the new text "
            "cannot be fitted onto its timing")
    span = (int(cue.get("startFrame") or 0), int(cue.get("endFrame") or 0))
    fitted = fit_words(measured, _tokens(text), span=span)
    new_cue = dict(cue)
    new_cue["text"] = text
    new_cue["words"] = fitted
    new_cue["emphasisWords"] = _emphasis_words(text)
    if len(fitted) != len(measured):
        # Explicit line breaks are word indices; they mean nothing else
        # once the word count moved.
        new_cue.pop("lineBreakAfter", None)
    new_props = dict(props)
    new_props["subtitles"] = [new_cue]
    return new_props
