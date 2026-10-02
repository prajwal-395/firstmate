"""The caption-collision rule `lower_third` was waiting on.

It must REFUSE a real collision and must PASS everything else - a gate
that fails correct output is no more coverage than one that cannot fail
(AGENTS.md 10.4).
"""

import pytest

from library.tools import caption_band as band
from library.tools.motion_graphics_plan import resolve_plan

SPINE = {"structure": [
    {"block_type": "speech", "timeline_start": 0.0, "timeline_end": 10.0},
    {"block_type": "broll", "timeline_start": 10.0, "timeline_end": 20.0},
    {"block_type": "hook", "timeline_start": 20.0, "timeline_end": 24.0},
]}


def _entry(element, anchor, start, duration, copy="Dr Ada Lovelace"):
    entry = {"element": element, "anchor": anchor,
             "start_seconds": start, "duration_seconds": duration,
             "color": "#ffffff"}
    if copy is not None:
        entry["copy"] = [{"text": copy, "type_role": "display"}]
    return entry


def _resolve(entry, bands=frozenset({"bottom"})):
    return resolve_plan([entry], timeline_duration=30, fps=30,
                        caption_bands=bands,
                        captioned_spans=band.captioned_spans(SPINE))


def test_copy_in_the_caption_band_is_refused_only_where_the_band_is_known():
    resolved = _resolve(_entry("lower_third", "bottom_centre", 1, 4))
    assert not resolved.moments
    assert resolved.dropped[0].reason == "collides_with_the_caption_band"
    assert "bottom band" in resolved.dropped[0].detail
    # No guess: a resolver called without the rule behaves as before it.
    resolved = resolve_plan([_entry("lower_third", "bottom_centre", 1, 4)],
                            timeline_duration=30, fps=30)
    assert resolved.moments


def test_an_unreadable_caption_position_raises_rather_than_guessing(monkeypatch):
    """A position nothing can place is a refusal, never a default band.

    Guessing `bottom` here would put a graphic under a caption on every
    project whose style names something this module has not seen.
    """
    monkeypatch.setattr(band, "resolve_subtitle_style",
                        lambda **_: {"position": "sideways"})
    with pytest.raises(band.CaptionBandError):
        band.occupied_bands()


# ── Two graphics drawn through each other ────────────────────────────
#
# A different collision from the caption one, found the same way: by
# compositing over a real reel frame rather than rendering one element
# at a time. `library/tools/motion_graphics_plan.overlapping_pairs`.

from library.tools.motion_graphics_plan import overlapping_pairs  # noqa: E402


def _moment(element, anchor, start=0, frames=90, row=0):
    return {"element": element, "anchor": anchor, "row": row,
            "startFrame": start, "durationFrames": frames}


def test_graphics_drawn_through_each_other_are_reported():
    """A full-width centre collides with a corner in its own band;
    chrome (`persist`, FOR holding under the piece) is reported as
    chrome; and two cards at one anchor in one row at one moment are one
    layout slot occupied twice - the Reel 06 overlap of 2026-09-12 the
    detector built for it returned [] on."""
    pairs = overlapping_pairs([
        _moment("title_lockup", "top_centre"),
        _moment("context_stamp", "top_left"),
    ])
    assert len(pairs) == 1
    assert set(pairs[0]["elements"]) == {"title_lockup", "context_stamp"}
    assert pairs[0]["involves_chrome"] is False

    pairs = overlapping_pairs([
        _moment("title_lockup", "top_centre"),
        _moment("channel_bug", "top_right"),
    ])
    assert len(pairs) == 1 and pairs[0]["involves_chrome"] is True

    pairs = overlapping_pairs([
        _moment("lower_third", "bottom_left", start=0, frames=84),
        _moment("lower_third", "bottom_left", start=77, frames=84),
    ])
    assert len(pairs) == 1
    assert pairs[0]["anchors"] == ["bottom_left", "bottom_left"]
    assert pairs[0]["frames"] == [77, 84]
    assert "row 0" in pairs[0]["why"]
