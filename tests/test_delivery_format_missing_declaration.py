"""A missing frame declaration fails loudly, never resolves to a shape.

Slices 3 and 4 of the delivery-format generalisation removed every
shape default from the fusion layer and the edit_video render/validate
path. This file pins the refusal side of that change: each test states
the input that carries no frame and asserts the loud failure it gets.
A `.get` with a shape default is the specific anti-pattern
(AGENTS.md 10.1) - a promised key that is missing must fail naming
what is missing, not read as vertical.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.tools.fusion.comp_builder import (  # noqa: E402
    MissingSourceFrame, build_effect_comp)
from library.tools.fusion.effects import fx  # noqa: E402
from library.tools.fusion.engine import CompEngine  # noqa: E402
from library.steps.step_6_01_render.resolve_build_timeline import (  # noqa: E402
    _preflight_check)

EFFECTS = {"vignette": True, "vignette_blend": 0.25, "vignette_soft": 0.35}


# ── Slice 3: the fusion layer states the source frame ─────────────


def test_build_effect_comp_refuses_an_unstated_source_frame():
    """source_res=None is "could not tell", not vertical."""
    with pytest.raises(MissingSourceFrame, match="source_res"):
        build_effect_comp(dict(EFFECTS), 120, source_res=None)


def test_build_effect_comp_refuses_an_omitted_source_frame():
    """The parameter is required: omitting it is a TypeError naming it."""
    with pytest.raises(TypeError, match="source_res"):
        build_effect_comp(dict(EFFECTS), 120)


def test_comp_engine_refuses_an_unstated_canvas():
    with pytest.raises(TypeError, match="width"):
        CompEngine(clip_dur=30)


def test_from_params_refuses_a_frameless_call():
    """Neither source_res nor width/height: a ValueError naming both."""
    with pytest.raises(ValueError, match="source_res"):
        CompEngine.from_params(clip_dur=90, zoom_start=1.0)


def test_effect_builders_take_no_default_frame():
    """One representative per builder family: res is required."""
    with pytest.raises(TypeError, match="res"):
        fx.vignette(clip_dur=90, width=1.0, height=1.0,
                    soft=0.35, blend=0.25)
    with pytest.raises(TypeError, match="res"):
        fx.fade(90, fade_in=10)
    with pytest.raises(TypeError, match="res"):
        fx.transition_tail(90, "fade_to_black", 7)
    with pytest.raises(TypeError, match="res"):
        fx.transition_head(90, "fade_to_black", 7)
    with pytest.raises(TypeError, match="res"):
        fx.tv_power_head(600)
    with pytest.raises(TypeError, match="res"):
        fx.tv_power_tail(600)


def test_verify_layer_takes_no_default_frame():
    """The verify wrappers forward the frame; omitting it names it."""
    from library.tools import treatment_verify as tv

    with pytest.raises(TypeError, match="source_res"):
        tv.verify_treatment(dict(EFFECTS), "tv_power_head", 120)
    with pytest.raises(TypeError, match="source_res"):
        tv.verify_drift({"zoom_start": 1.0, "zoom_end": 1.04}, 120)
    with pytest.raises(TypeError, match="source_res"):
        tv.build_alone("tv_power_head", dict(EFFECTS), 120)


def test_verify_treatment_skill_refuses_without_frame_or_file():
    """No source_res and no source_file to measure one off: refused."""
    from library.skills.verify_treatment.skill import run

    with pytest.raises(ValueError, match="source frame"):
        run(dict(EFFECTS), "tv_power_head", 120,
            "/nonexistent-project", "plan_vfx")


# ── Slice 4: the edit_video render/validate path ──────────────────


def test_preflight_refuses_a_manifest_with_no_declared_resolution():
    """The 6.01 build refuses before touching Resolve: no frame, no
    timeline. Input: a manifest whose project block carries no
    resolution key at all."""
    manifest = {
        "project": {"name": "Test"},
        "tracks": {"V1": {"clips": []}},
    }
    errors = _preflight_check(manifest)
    assert any("resolution" in e for e in errors), errors


def test_validate_output_refuses_a_manifest_with_no_declared_resolution():
    """The 6.02 gate grades the render against the manifest's declared
    frame; a manifest without one cannot be validated. Input: an
    assembly manifest whose project block carries no resolution."""
    from library.steps.step_6_02_validate_output.bridge import (
        validate_output)

    with pytest.raises(ValueError, match="resolution"):
        validate_output(
            {"output_path": "/nonexistent/master.mp4"},
            {"project": {"frame_rate": 30}},
            project_folder="")


def test_full_render_qa_reports_not_checked_without_a_declared_frame():
    """No expected_resolution: the resolution result reports NOT
    CHECKED and FAILS - never a pass, never a silent vertical. The
    eight file-only measurements are mocked; the input under test is
    the missing expected_resolution."""
    from library.tools import render_qa

    with (patch.object(render_qa, "measure_lufs",
                       return_value=MagicMock(metric="loudness")),
          patch.object(render_qa, "detect_black_frames",
                       return_value=MagicMock(metric="black_frames")),
          patch.object(render_qa, "detect_freeze_frames",
                       return_value=MagicMock(metric="freeze_frames")),
          patch.object(render_qa, "analyze_color_histogram",
                       return_value=MagicMock(metric="chroma")),
          patch.object(render_qa, "measure_frame_occupancy",
                       return_value=MagicMock(metric="frame_occupancy")),
          patch.object(render_qa, "measure_chroma_presence",
                       return_value=MagicMock(metric="chroma_presence")),
          patch.object(render_qa, "measure_face_intact",
                       return_value=MagicMock(metric="face_intact")),
          patch.object(render_qa, "measure_silence_under_picture",
                       return_value=MagicMock(
                           metric="silence_under_picture")),
          patch.object(render_qa, "verify_framerate",
                       return_value=MagicMock(metric="framerate")),
          patch.object(render_qa, "verify_audio_streams",
                       return_value=MagicMock(metric="audio"))):
        results = render_qa.run_full_render_qa(
            "dummy.mp4", 30.0, expected_resolution=None)

    by_metric = {r.metric: r for r in results
                 if hasattr(r, "metric")}
    assert "resolution" in by_metric
    resolution = by_metric["resolution"]
    assert resolution.passed is False
    assert "not checked" in resolution.detail.lower()


def test_verify_resolution_takes_no_default_frame():
    """Both dimensions are required: omitting them names them."""
    from library.tools.render_qa import verify_resolution

    with pytest.raises(TypeError):
        verify_resolution("dummy.mp4")


def test_conform_targets_take_no_default_frame():
    """3.01 and 3.02 read the delivery format at their entry point;
    the pure functions take it as a required argument."""
    from library.steps.step_3_01_assign_aroll.step import assign_a_roll
    from library.steps.step_3_02_select_broll.post_bridge import (
        resolve_broll)

    with pytest.raises(TypeError):
        assign_a_roll({"structure": []}, [])
    with pytest.raises(TypeError):
        resolve_broll([], [], [], [], [], {})
