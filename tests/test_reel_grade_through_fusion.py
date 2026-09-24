"""A declared look through Fusion on the reels path, at declared values.

The captain approved the Fusion route for the four nodes no scriptable
Color page call can reach (pivot contrast, glow, grain, vignette), and
their numbers live in a project's own `project.yaml` under
`style.series_look` - contrast 0.12, glow 0.20/0.72/3.5, grain 0.35/1.5,
vignette 0.35/0.30.

Two things have to be true for those numbers to reach the picture:

1. The comp must carry what Fusion's own tool means by them. The
   declaration says contrast in pivot-gain units (0 is neutral, the same
   units the reference stills were rendered in) and Fusion's
   `BrightnessContrast.Contrast` is neutral at 0.0 as well, so it is
   emitted VERBATIM. This file asserted `1.12` for a day, from the
   grade-variant report's PREDICTION that Fusion's neutral was 1.0;
   probing the tool says its default is 0.0, and a still off the live
   Reel 09 says `Contrast = 0.0` is byte-identical to having no node at
   all while `1.12` crushes the mean luma from 45.39 to 19.74 where the
   declared 0.12 takes it to 39.97 (see `fusion/effects.fx.grade`).
2. The reels path must merge the look onto every picture clip. Step
   5.04 merges `fusion_look` onto every V1/V2 clip of the master, but
   the reel manifest (`reel_look.fusion_manifest`) carried only the
   switch animation and the drift - the grade never reached a reel.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import reel_look
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.series_look import resolve_look

# A declared look's magnitudes, fixed here so the test never reaches a
# real project (AGENTS.md 8). The values exercise the machinery; the
# name is deliberately not any project's chosen look.
TEST_LOOK = {
    "name": "test_look",
    "cdl": {
        "slope": [1.03, 1.0, 0.96],
        "offset": [-0.01, 0.005, 0.02],
        "power": [1.0, 1.0, 1.0],
        "saturation": 1.12,
    },
    "contrast": 0.12,
    "glow": {"gain": 0.20, "threshold": 0.72, "size": 3.5},
    "grain": {"power": 0.35, "size": 1.5},
    "vignette": {"blend": 0.35, "soft": 0.30},
}


@dataclass
class _Clip:
    source_file: str
    track_type: str = "video"
    track_index: int = 1
    timeline_start: float = 0.0
    source_in: float = 0.0


def _placement(source_file, record_frame, seconds, track_index=1, fps=24.0):
    return {
        "clip": _Clip(source_file, track_index=track_index),
        "source_in": 0.0,
        "source_out": seconds,
        "record": record_frame / fps,
        "snapped_record": record_frame,
        "speaker": "Craig",
    }


def _two_row_plan():
    return {"video_tracks": [
        {"role": "a_roll", "occupant": "1", "index": 1},
        {"role": "a_roll", "occupant": "2", "index": 2},
    ]}


# ── 1. The declaration reaches the tool in the tool's own units ───────────

def test_declared_contrast_reaches_the_tool_verbatim():
    """0.12 declared is a pivot gain with 0 neutral, and so is Fusion's
    own Contrast: 0.0 renders byte-identical to no node at all. Emitting
    1.12 for a declared 0.12 shipped eight times the grade."""
    comp = build_effect_comp({"grade_contrast": 0.12}, 120,
                             source_res=(1080, 1920))
    assert "Contrast = Input { Value = 0.12, }," in comp
    assert "Contrast = Input { Value = 1.12, }," not in comp


# ── 2. The reels path merges the grade onto every picture row ─────────────

def test_reel_manifest_merges_grade_onto_both_picture_rows():
    grade = resolve_look(TEST_LOOK).fusion()
    placements = [
        _placement("/a.mxf", 0, 5.0, track_index=1),
        _placement("/b.mxf", 120, 5.0, track_index=2),
    ]
    manifest = reel_look.fusion_manifest(
        placements, {"power": {}}, [], 24.0,
        track_plan=_two_row_plan(), grade_look=dict(grade))
    per_clip = manifest["fusion_effects"]["per_clip"]
    assert set(manifest["tracks"]) == {"V1", "V2"}
    for label in ("reel_picture_00", "reel_picture_01"):
        effects = per_clip[label]
        assert effects["grade_contrast"] == pytest.approx(0.12)
        assert effects["glow_gain"] == pytest.approx(0.20)
        assert effects["film_grain"] is True
        assert effects["vignette"] is True
    # The switch animation still rides the first and last footage clip.
    assert per_clip["reel_picture_00"]["tv_power_head"] is True
    assert per_clip["reel_picture_01"]["tv_power_tail"] is True


def test_reel_grade_never_overwrites_a_planned_value():
    """The compile_manifest rule, unchanged: a value the planner asked
    for wins over the look's."""
    grade = resolve_look(TEST_LOOK).fusion()
    placements = [_placement("/a.mxf", 0, 10.0)]
    motion = [{"target_block_position": 0, "effect_type": "slow_zoom_in",
               "params": {"zoom_start": 1.0, "zoom_end": 1.04,
                          "grade_contrast": 0.05}}]
    manifest = reel_look.fusion_manifest(
        placements, {"power": {}}, motion, 24.0, grade_look=dict(grade))
    effects = manifest["fusion_effects"]["per_clip"]["reel_picture_00"]
    assert effects["grade_contrast"] == pytest.approx(0.05)


def test_no_grade_look_means_no_grade_keys_on_reels():
    """A project that declares no look gets no grade on its reels
    either - not a comp with quiet values in it."""
    placements = [_placement("/a.mxf", 0, 10.0)]
    manifest = reel_look.fusion_manifest(
        placements, {"power": {}}, [], 24.0, grade_look=None)
    effects = manifest["fusion_effects"]["per_clip"]["reel_picture_00"]
    assert "grade_contrast" not in effects
    assert "glow_gain" not in effects
    assert "film_grain" not in effects
    assert "vignette" not in effects
    assert effects["tv_power_head"] is True


# ── 3. The grade look resolves from the project's own project.yaml ────────


