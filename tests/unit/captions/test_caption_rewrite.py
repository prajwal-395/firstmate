"""The caption rewrite fits new words onto a card's measured windows.

A caption fix changes what a rendered card SAYS. The card's timing is
measured, not invented, so the fit is the whole contract: a word that
survives keeps the window it was measured with, a replaced run shares
the span it replaces, and a text that cannot be timed inside the card's
own span refuses instead of being drawn.

These tests pin that fit, and the refusals that keep a fix from
quietly becoming a re-timing.

They also pin the CARRYING, which the product's own reels exposed: a
real reel's caption card is a TIGHT card, and its sidecar records the
tight canvas and the tight style geometry, while the render entry
refuses anything that is not the project's own frame and geometry.  A
fix that handed the sidecar straight back therefore refused on every
real reel - the product's caption medium was the one thing a caption
fix could not address.
"""
from __future__ import annotations

import pytest

from library.tools.caption_rewrite import (
    CaptionRewriteError,
    caption_fix_operation,
    carrying_matches,
    fit_words,
    full_frame_props,
    rewrite_props,
)


def _words(*pairs):
    """The card's measured windows: `(word, start, end)` each."""
    return [{"word": word, "startFrame": start, "endFrame": end}
            for word, start, end in pairs]


def _props(text="the wrong number", words=None, start=0, end=48):
    """One caption card's render props."""
    return {
        "subtitles": [{
            "text": text,
            "startFrame": start,
            "endFrame": end,
            "emphasisWords": ["number"],
            "words": words if words is not None else _words(
                ("the", 0, 8), ("wrong", 8, 24), ("number", 24, 48)),
            "fitScale": 1.0,
        }],
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "durationInFrames": 60,
        "style": {"fontSize": 64},
    }


def test_a_surviving_word_keeps_the_window_it_was_measured_with():
    """Defect: a fix that re-timed every word moved the captions off
    the speech they cover."""
    fitted = fit_words(
        _words(("the", 0, 8), ("wrong", 8, 24), ("number", 24, 48)),
        ["the", "right", "number"], span=(0, 48))

    assert [(row["word"], row["startFrame"], row["endFrame"])
            for row in fitted] == [
        ("the", 0, 8), ("right", 8, 24), ("number", 24, 48)]


def test_a_replaced_run_shares_the_span_it_replaces():
    """The new words sweep where the old ones did - the card's own
    measured span, split between them."""
    fitted = fit_words(
        _words(("the", 0, 8), ("wrong", 8, 24), ("number", 24, 48)),
        ["the", "right", "figure", "number"], span=(0, 48))

    assert [(row["word"], row["startFrame"], row["endFrame"])
            for row in fitted] == [
        ("the", 0, 8), ("right", 8, 16), ("figure", 16, 24),
        ("number", 24, 48)]


def test_an_inserted_run_takes_the_gap_between_its_neighbours():
    fitted = fit_words(
        _words(("the", 0, 8), ("wrong", 12, 24), ("number", 24, 48)),
        ["the", "very", "wrong", "number"], span=(0, 48))

    assert [(row["word"], row["startFrame"], row["endFrame"])
            for row in fitted] == [
        ("the", 0, 8), ("very", 8, 12), ("wrong", 12, 24),
        ("number", 24, 48)]


def test_an_inserted_word_with_no_gap_to_take_refuses():
    """Defect: a word given a window nobody measured would highlight
    over words the speech does not cover."""
    with pytest.raises(CaptionRewriteError, match="do not fit"):
        fit_words(
            _words(("the", 0, 8), ("wrong", 8, 24), ("number", 24, 48)),
            ["the", "very", "wrong", "number"], span=(0, 48))


def test_a_text_that_cannot_be_timed_inside_the_card_refuses():
    """Defect: a caption drawn with words no sweep can cross says
    something its timing does not carry."""
    with pytest.raises(CaptionRewriteError, match="do not fit"):
        fit_words(_words(("the", 0, 2)), ["one", "two", "three", "four",
                                          "five"], span=(0, 2))


def test_a_card_with_no_measured_words_refuses_the_fit():
    with pytest.raises(CaptionRewriteError, match="no measured word windows"):
        fit_words([], ["one"], span=(0, 8))


def test_the_rewrite_keeps_the_cards_own_timing():
    """The fix changes what the card says, never when it says it."""
    props = _props()
    rewritten = rewrite_props(props, "the right number")

    cue = rewritten["subtitles"][0]
    assert cue["text"] == "the right number"
    assert (cue["startFrame"], cue["endFrame"]) == (0, 48)
    assert [(row["word"], row["startFrame"], row["endFrame"])
            for row in cue["words"]] == [
        ("the", 0, 8), ("right", 8, 24), ("number", 24, 48)]
    # The card's own bounds, style and frame survive the rewrite.
    assert rewritten["style"] == props["style"]
    assert rewritten["width"] == props["width"]
    assert rewritten["height"] == props["height"]
    assert rewritten["durationInFrames"] == props["durationInFrames"]
    # The props on disk are never edited.
    assert props["subtitles"][0]["text"] == "the wrong number"


def test_the_rewrite_re_derives_the_emphasis_words():
    """An emphasis word the new text does not contain scales nothing."""
    rewritten = rewrite_props(_props(), "the right figure")
    assert "number" not in rewritten["subtitles"][0]["emphasisWords"]


def test_a_rewrite_of_the_same_text_refuses():
    with pytest.raises(CaptionRewriteError, match="already reads"):
        rewrite_props(_props(), "the wrong number")


def test_a_card_drawing_several_cues_refuses_the_fix():
    """Defect: a fix that guessed which cue was wrong would rewrite a
    caption nobody named."""
    props = _props()
    props["subtitles"].append(dict(props["subtitles"][0]))

    with pytest.raises(CaptionRewriteError, match="holds 2 caption cues"):
        rewrite_props(props, "the right number")


def test_the_operation_carries_the_text_it_applies():
    operation = caption_fix_operation("clip-17", "the right number")

    assert operation == {"op": "caption_fix", "unique_id": "clip-17",
                         "text": "the right number"}


def test_an_operation_without_a_card_or_a_text_refuses():
    with pytest.raises(ValueError, match="names no unique_id"):
        caption_fix_operation("", "the right number")
    with pytest.raises(ValueError, match="needs the card's new text"):
        caption_fix_operation("clip-17", "   ")


# ── The carrying: a tight card's sidecar, rebuilt for the render ──────


def _project(tmp_path):
    """A project that declares the delivery frame and the tight look."""
    (tmp_path / "project.yaml").write_text(
        "project_name: caption-fix-test\n"
        "pipeline:\n"
        "  delivery_format: vertical_1080x1920\n"
        "  subtitle_typography:\n"
        "    font: Montserrat\n"
        "    size: 51\n"
        "  subtitle_overlay_geometry: tight\n",
        encoding="utf-8")
    return tmp_path


def _project_style(project_folder, speaker="Akshita"):
    """The style the render entry expects for this project."""
    from library.tools.subtitle_style import resolve_subtitle_style

    return resolve_subtitle_style({}, {}, str(project_folder),
                                  speaker=speaker, reel_name=None)


def _tight_card(project_folder, style=None):
    """A real reel's caption card: the TIGHT props its sidecar records.

    The renderer draws a tight card natively at the tight canvas and
    writes the tight props beside it, so this is what a caption fix
    actually reads off a built reel.
    """
    from library.tools.tight_box import constant_caption_box

    style = style if style is not None else _project_style(project_folder)
    full = _props()
    full["width"], full["height"] = 1080, 1920
    full["style"] = dict(style)
    full["_speaker"] = "Akshita"
    box = constant_caption_box(full)
    tight = dict(box.props)
    tight["subtitles"] = full["subtitles"]
    return tight


def test_a_tight_cards_sidecar_is_rebuilt_as_the_projects_own_props(tmp_path):
    """The defect: the render entry refuses a tight cards props, so a
    fix on a real reel refused before anything was drawn."""
    project = _project(tmp_path)
    tight = _tight_card(project)
    expected = _project_style(project)

    rebuilt = full_frame_props(tight, str(project))

    assert (rebuilt["width"], rebuilt["height"]) == (1080, 1920)
    assert rebuilt["style"]["safeArea"] == expected["safeArea"]
    assert (rebuilt["style"]["captionMaxWidth"]
            == expected["captionMaxWidth"])
    # The card's own typography and colours are carried through: the
    # approved styling is what the re-render draws.
    for key in ("fontFamily", "fontSize", "fontWeight", "fontColor",
                "accentColor", "outlineColor", "outlineWidth", "position"):
        assert rebuilt["style"][key] == tight["style"][key], key
    # The text and the timing are untouched by the rebuild.
    assert rebuilt["subtitles"] == tight["subtitles"]


def test_a_card_whose_wrap_no_longer_matches_the_project_refuses(tmp_path):
    """Defect: a re-render at the projects width would redraw an
    approved card with different line breaks - a fix that changes the
    look is not a fix."""
    project = _project(tmp_path)
    tight = _tight_card(project)
    tight["style"] = dict(tight["style"], captionMaxWidth=400)

    with pytest.raises(CaptionRewriteError, match="no longer matches"):
        full_frame_props(tight, str(project))


def test_the_re_render_lands_on_the_cards_own_canvas(tmp_path):
    """The placement carries a transform computed for the canvas the
    card was drawn on."""
    project = _project(tmp_path)
    tight = _tight_card(project)
    rebuilt = full_frame_props(tight, str(project))

    ok, reason = carrying_matches(tight, rebuilt, str(project))

    assert ok is True
    assert reason == ""


def test_a_re_render_onto_another_canvas_is_refused(tmp_path):
    """Defect: a card redrawn on another canvas would leave the placed
    transform pointing at the wrong box."""
    project = _project(tmp_path)
    tight = _tight_card(project)
    rebuilt = full_frame_props(tight, str(project))
    other = dict(tight, width=640, height=480)

    ok, reason = carrying_matches(other, rebuilt, str(project))

    assert ok is False
    assert "canvas" in reason


def test_a_full_canvas_card_is_matched_against_the_delivery_frame(tmp_path):
    """A project that declares no tight captions draws on the frame."""
    project = tmp_path / "full"
    project.mkdir()
    (project / "project.yaml").write_text(
        "project_name: caption-fix-full\n"
        "pipeline:\n"
        "  delivery_format: vertical_1080x1920\n"
        "  subtitle_overlay_geometry: full\n",
        encoding="utf-8")
    full = _props()
    full["width"], full["height"] = 1080, 1920
    full["style"] = dict(_project_style(project))

    ok, reason = carrying_matches(full, full, str(project))

    assert ok is True
    assert reason == ""
