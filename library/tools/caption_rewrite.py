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

THE CARRYING, and the refusal a real reel exposed.  A card's sidecar
records the card AS IT WAS DRAWN: for a project that declares tight
captions that is the tight canvas (720x480 on a 1080x1920 delivery) and
the tight style geometry, because the renderer draws a tight card
natively at its own canvas.  The render entry, however, refuses props
whose frame or whose style geometry is not the PROJECT's - it re-derives
the tight canvas itself and refuses to draw a card whose wrap or whose
safe area belongs to another frame.  So a fix that hands the sidecar
straight back refuses on every real reel, and the product's own caption
medium is the one thing a caption fix cannot address.

`full_frame_props` rebuilds the props the render entry expects from the
card's own: the delivery frame, and the PROJECT's geometry for this
reel and speaker, with the card's own typography, colours, outline,
position and wrap width carried through untouched.  The re-derived
canvas is then checked against the card's own (`carrying_matches`), so
a fix that would redraw the card on a different canvas - and so break
the transform its placement carries - refuses BEFORE anything renders.
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


def full_frame_props(props: Mapping[str, Any], project_folder: str,
                     reel_name: str | None = None) -> dict[str, Any]:
    """The card's own props, rebuilt as the RENDER ENTRY needs them.

    The sidecar records the card as it was drawn - for a tight card, the
    tight canvas and the tight style geometry - while the render entry
    refuses anything that is not the project's own frame and geometry.
    This rebuilds the props the entry expects, from the card's own:

    * the frame is the PROJECT's delivery frame;
    * the style's `safeArea` and `captionMaxWidth` are the PROJECT's,
      resolved for this reel and this speaker exactly as the render
      entry resolves them;
    * everything else - the typeface, the size, the weight, the colours,
      the outline, the position, the speaker - is the CARD's own, so the
      approved styling is what the re-render draws.

    A card whose own wrap width no longer matches the project's is
    REFUSED: re-rendering it at the project's width would redraw an
    approved card differently (the words would wrap differently), and a
    fix that changes the look is not a fix.
    """
    from library.tools.delivery_format import resolve_delivery_format
    from library.tools.subtitle_style import resolve_subtitle_style

    style = props.get("style")
    if not isinstance(style, Mapping):
        raise CaptionRewriteError(
            "the card carries no style - the re-render would have to "
            "guess the typography the captain approved")
    width, height = resolve_delivery_format(project_folder)
    expected = resolve_subtitle_style(
        {}, {}, project_folder, speaker=props.get("_speaker"),
        reel_name=reel_name or None)
    card_width = style.get("captionMaxWidth")
    project_width = expected.get("captionMaxWidth")
    if card_width != project_width:
        raise CaptionRewriteError(
            f"the card's wrap width ({card_width}px) no longer matches "
            f"the project's ({project_width}px) - re-rendering it would "
            "redraw an approved card with different line breaks, and a "
            "fix that changes the look is not a fix")
    new_style = dict(style)
    new_style["safeArea"] = expected.get("safeArea")
    new_style["captionMaxWidth"] = project_width
    new_props = dict(props)
    new_props["width"], new_props["height"] = int(width), int(height)
    new_props["style"] = new_style
    return new_props


def carrying_matches(original: Mapping[str, Any], render_props: Mapping,
                     project_folder: str) -> tuple[bool, str]:
    """Whether the re-render lands on the card's OWN canvas.

    A caption card's placement carries a transform computed for the
    canvas it was drawn on, so a fix that redraws the card on a
    different canvas would leave the placed transform pointing at the
    wrong box.  The canvas the renderer will produce is computed here
    from the same inputs the renderer uses - the project's declared
    geometry and the props - and compared with the card's own.

    Returns `(ok, reason)`; the reason names what differs.
    """
    from library.tools.delivery_format import resolve_delivery_format
    from library.tools.overlay_mode import resolve_overlay_geometry

    try:
        was = (int(original.get("width") or 0),
               int(original.get("height") or 0))
    except (TypeError, ValueError):
        return False, "the card's own canvas is unreadable"
    geometry = resolve_overlay_geometry(project_folder or None)
    if geometry == "tight":
        from library.tools.tight_box import constant_caption_box

        try:
            box = constant_caption_box(dict(render_props))
        except Exception as exc:  # noqa: BLE001 - any refusal is a refusal
            return False, f"the tight canvas cannot be computed: {exc}"
        if box is None:
            return False, "the card draws no subtitles, so it has no canvas"
        now = (int(box.width), int(box.height))
    else:
        frame = resolve_delivery_format(project_folder)
        now = (int(frame[0]), int(frame[1]))
    if now != was:
        return False, (
            f"the re-render would draw the card on a {now[0]}x{now[1]} "
            f"canvas where the card is a {was[0]}x{was[1]} one - the "
            "placement's own transform belongs to the canvas the card "
            "was drawn on")
    return True, ""


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
