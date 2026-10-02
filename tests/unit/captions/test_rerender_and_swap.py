"""The caption re-render-and-swap entry point refuses what it cannot do.

`rerender_and_swap` (reached as `subtitles.rerender_swap`) renders named
cards through one batch engine and swaps them onto every timeline
holding the old files. These tests pin its boundaries without rendering
anything and without Resolve: frame-sequence projects are refused
before any render and before Resolve is contacted, and malformed pairs
are refused rather than guessed at.
"""

from __future__ import annotations

import pytest

import library.steps.step_4_05_render_subtitles.step as r405


def test_frames_project_refused_before_any_render_or_resolve(
        tmp_path, monkeypatch):
    monkeypatch.setattr(r405, "resolve_overlay_container",
                        lambda *args, **kwargs: "frames")
    report = r405.rerender_and_swap(str(tmp_path), [])
    assert report["ok"] is False
    assert "frame sequence" in report["error"]
    assert report["map"] == {}
    assert report["swapped"] == []


def test_a_non_dict_pair_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not a dict"):
        r405.rerender_and_swap(str(tmp_path), ["old.mov"])

