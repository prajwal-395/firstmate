"""Motion-graphics reuse is decided by pixels, never by placing.

The reel lower-third path renders one speaker card per reel through
`render_one_segment` with `reuse=True` - but the drawing digest hashed
the whole props object, including the placement and provenance keys
the plan carries for readers (`timeline_start`/`timeline_end`, the
whole-piece `timelineProgress*` fractions, `timing_basis`,
`subject`, `why`, `colorBasis`). Every reel's card therefore digested
differently and re-rendered from scratch: measured on geo-podcast as
26 Craig cards and 27 Akshita cards decoding framemd5-identical while
carrying 53 distinct digests.

`render_one_segment` behind a stubbed `subprocess.run`, so what is
pinned is the unit's own decisions without paying for Remotion.
"""
import copy
import json
import os
import shutil
import subprocess
import sys

import pytest

NEEDS_FFMPEG = shutil.which("ffmpeg") is None
FFMPEG_REASON = "needs ffmpeg; runs in CI, which installs it (AGENTS.md 9)"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_06_render_motion_graphics.post_bridge import (  # noqa: E402
    _mg_drawing_digest,
    render_one_segment,
)
from library.tools.render_cache import (  # noqa: E402
    motion_segment_name,
)


def _lower_third(progress_end=0.0438, timeline_start=0.0):
    return {
        "element": "lower_third",
        "anchor": "bottom_left",
        "row": 0,
        "runs": [{"text": "Craig Lucie", "type_role": "display"},
                 {"text": "CEO Lucie Content",
                  "type_role": "supporting"}],
        "color": "#FBF0B8",
        "colorBasis": "stated by the plan",
        "entrance": "draw",
        "exit": "fade",
        "timeline_start": timeline_start,
        "timeline_end": timeline_start + 3.5,
        "timing_basis": "declared",
        "subject": "",
        "timelineProgressStart": 0.0,
        "timelineProgressEnd": progress_end,
        "startFrame": 0,
        "durationFrames": 84,
        "asset": "",
        "footprint": None,
        "emphasis": None,
        "why": "first appearance of 'Craig' in this reel, at 0.0s",
        "data": {"construction": "staged_rule",
                 "speaker": "Craig"},
    }


def _props(element):
    return {
        "elements": [element],
        "fps": 23.976023976023978,
        "width": 514,
        "height": 480,
        "safeArea": {"top": 310, "right": 48,
                     "bottom": 48, "left": 48},
        "durationInFrames": 84,
    }


def test_reel_variants_share_a_digest():
    """Two reels' placings of one card digest identically.

    The progress fractions move with each reel's length and the
    bounds with each placing; neither is read by the composition
    for a `lower_third`, so neither may decide reuse.
    """
    first = _mg_drawing_digest(
        _props(_lower_third(progress_end=0.0438,
                            timeline_start=0.0)), "full", None)
    second = _mg_drawing_digest(
        _props(_lower_third(progress_end=0.0667,
                            timeline_start=19.06)), "full", None)
    assert first == second
    assert (motion_segment_name("", first)
            == motion_segment_name("", second))


def test_provenance_edits_share_a_digest():
    """The model's reasoning and the plan's provenance never re-render."""
    base = _lower_third()
    edited = copy.deepcopy(base)
    edited["why"] = "second appearance, later in the reel"
    edited["subject"] = "the guest"
    edited["timing_basis"] = "word_window:Craig"
    edited["colorBasis"] = "pipeline.speaker_subtitle_styles['Craig']"
    assert (_mg_drawing_digest(_props(base), "full", None)
            == _mg_drawing_digest(_props(edited), "full", None))


def test_progress_bar_progress_still_draws():
    """The one element that draws its fractions keeps them in the digest."""
    base = _lower_third()
    base["element"] = "progress_bar"
    moved = copy.deepcopy(base)
    moved["timelineProgressEnd"] = 0.9
    assert (_mg_drawing_digest(_props(base), "full", None)
            != _mg_drawing_digest(_props(moved), "full", None))


def test_drawing_changes_still_render():
    """A new drawing input needs no edit: it digests differently."""
    base = _props(_lower_third())
    for mutate in (
        lambda p: p["elements"][0]["runs"].__setitem__(
            0, {"text": "Someone Else", "type_role": "display"}),
        lambda p: p["elements"][0].__setitem__("color", "#000000"),
        lambda p: p["elements"][0]["data"].__setitem__(
            "construction", "other"),
        lambda p: p["elements"][0].__setitem__("durationFrames", 90),
        lambda p: p.__setitem__("durationInFrames", 90),
        lambda p: p["elements"][0].__setitem__("element", "title_lockup"),
    ):
        changed = copy.deepcopy(base)
        mutate(changed)
        assert (_mg_drawing_digest(changed, "full", None)
                != _mg_drawing_digest(base, "full", None))


def test_geometry_still_distinguishes():
    """Full canvas and tight canvas are different artefacts of one draw."""
    props = _props(_lower_third())
    assert (_mg_drawing_digest(props, "full", None)
            != _mg_drawing_digest(props, "tight", None))


class _StubRun:
    """Acts like a successful `npx remotion render`, drawing real pixels.

    Same seam as `test_motion_graphics_overlay_modes`: only `npx` is
    stubbed, so the carriage transcode still runs real ffmpeg.
    """

    def __init__(self):
        self.calls = []
        self.real_run = subprocess.run

    def __call__(self, *args, **kwargs):
        argv = args[0]
        if not argv or argv[0] != "npx":
            return self.real_run(*args, **kwargs)
        self.calls.append(argv)
        overlay_path = argv[4]
        props = json.load(open(argv[argv.index("--props") + 1],
                               encoding="utf-8"))
        self.real_run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"color=c=black@0:s={props['width']}x{props['height']}"
                   f":d=1:r=30,format=rgba",
             "-frames:v", str(max(props["durationInFrames"], 1)),
             "-c:v", "prores_ks", "-profile:v", "4444",
             "-pix_fmt", "yuva444p10le", overlay_path],
            check=True,
        )

        class Done:
            returncode = 0
            stderr = ""
        return Done()


def _planned(element, timeline_start=0.0):
    total = element["startFrame"] + element["durationFrames"]
    return {
        "index": 0,
        "timeline_start": timeline_start,
        "timeline_end": timeline_start + total / 30.0,
        "total_frames": total,
        "element_count": 1,
        "elements": [element["element"]],
        "props": _props(element),
    }


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_second_reel_reuses_the_first_reels_card(tmp_path, monkeypatch):
    """The reel path joins the cache: reel two reuses, never re-renders.

    Two placings of one card on two reels' timebases - different
    progress fractions, different bounds, different placement labels -
    render once. The entry still names its own placing; only the file
    is shared.
    """
    stub = _StubRun()
    monkeypatch.setattr(
        "library.steps.step_4_06_render_motion_graphics.post_bridge.subprocess.run",
        stub,
    )
    first = render_one_segment(
        _planned(_lower_third(progress_end=0.0438, timeline_start=0.0)),
        str(tmp_path), segment_name="lt_reel_01_00",
        overlay_geometry="full", reuse=True)
    assert first["provenance"] == "rendered"
    second = render_one_segment(
        _planned(_lower_third(progress_end=0.0667, timeline_start=19.06)),
        str(tmp_path), segment_name="lt_reel_02_00",
        overlay_geometry="full", reuse=True)
    assert second["provenance"] == "reused"
    assert second["overlay_path"] == first["overlay_path"]
    assert second["placement_label"] == "lt_reel_02_00"
    assert len(stub.calls) == 1
