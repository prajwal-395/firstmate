"""Keyframes land inside the PLAYED window, not across the whole source.

This is the test that proves finding 4 is fixed: a segment from a 5657-frame
source clip that plays only frames 25-97 must have its zoom ramp and
transition keyframes WITHIN 25-97, not at 0/2828/5656 (the full source).

A test using source_in=0 would pass against the broken code, so every
parametrized case here uses a non-zero source_in.
"""
import re

import pytest

from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.fusion.effects import _reset_counters, fx


# ── Helpers ──────────────────────────────────────────────────

def _extract_spline_keyframes(comp: str, spline_name: str) -> list[int]:
    """Frame numbers from a named BezierSpline in a serialized comp."""
    pattern = rf'{re.escape(spline_name)}\s*=\s*BezierSpline\s*\{{'
    match = re.search(pattern, comp)
    if not match:
        return []
    # Depth-based brace matching to find the full block
    block_start = match.end()
    depth = 1
    pos = block_start
    while depth > 0 and pos < len(comp):
        if comp[pos] == '{': depth += 1
        elif comp[pos] == '}': depth -= 1
        pos += 1
    block = comp[block_start:pos]
    return [int(m.group(1)) for m in re.finditer(r'\[(\d+)\]', block)]


def _extract_all_keyframes(comp: str) -> dict[str, list[int]]:
    """All named BezierSplines and their keyframe positions."""
    result = {}
    for m in re.finditer(r'(\w+)\s*=\s*BezierSpline\s*\{', comp):
        name = m.group(1)
        frames = _extract_spline_keyframes(comp, name)
        if frames:
            result[name] = frames
    return result


# ── Hook scenario from project 001 ──────────────────────────
# 5657-frame source, segment plays frames 25-97 (2.4 seconds at 30fps)

HOOK_CLIP_DUR = 5657
HOOK_SOURCE_IN = 25
HOOK_SOURCE_OUT = 97


class TestZoomKeyframesInPlayedWindow:
    """The zoom ramp must land inside the played segment."""

    def test_zoom_in_within_played_window(self):
        """slow_zoom_in 1.0 -> 1.03 on the hook clip."""
        _reset_counters()
        block = fx.zoom(
            HOOK_CLIP_DUR,
            start=1.0, mid=1.015, end=1.03,
            source_in=HOOK_SOURCE_IN,
            source_out=HOOK_SOURCE_OUT,
        )
        # Find the spline in the block's nodes
        from library.tools.fusion.nodes import BezierSpline
        splines = [n for n in block.nodes if isinstance(n, BezierSpline)]
        assert splines, "zoom block has no spline"
        frames = [kf.frame for kf in splines[0].keyframes]
        assert min(frames) >= HOOK_SOURCE_IN, (
            f"Zoom keyframe at frame {min(frames)} is before played window "
            f"(source_in={HOOK_SOURCE_IN})"
        )
        assert max(frames) <= HOOK_SOURCE_OUT, (
            f"Zoom keyframe at frame {max(frames)} is after played window "
            f"(source_out={HOOK_SOURCE_OUT})"
        )

    def test_zoom_out_within_played_window(self):
        """slow_zoom_out 1.03 -> 1.0 on a non-zero-start segment."""
        _reset_counters()
        block = fx.zoom(
            HOOK_CLIP_DUR,
            start=1.03, mid=1.015, end=1.0,
            source_in=HOOK_SOURCE_IN,
            source_out=HOOK_SOURCE_OUT,
        )
        from library.tools.fusion.nodes import BezierSpline
        splines = [n for n in block.nodes if isinstance(n, BezierSpline)]
        assert splines
        frames = [kf.frame for kf in splines[0].keyframes]
        assert min(frames) >= HOOK_SOURCE_IN
        assert max(frames) <= HOOK_SOURCE_OUT


class TestTransitionKeyframesInPlayedWindow:
    """Transitions must fire within the played segment, not at clip_dur."""

    @pytest.mark.parametrize("ttype", ["fade_to_black", "defocus", "flash"])
    def test_tail_transition_within_played_window(self, ttype):
        """Tail transitions must end at source_out, not clip_dur-1."""
        _reset_counters()
        block = fx.transition_tail(
            HOOK_CLIP_DUR, ttype, dur_frames=7,
            source_out=HOOK_SOURCE_OUT,
        )
        from library.tools.fusion.nodes import BezierSpline
        splines = [n for n in block.nodes if isinstance(n, BezierSpline)]
        assert splines, f"{ttype} tail has no spline"
        frames = [kf.frame for kf in splines[0].keyframes]
        assert max(frames) <= HOOK_SOURCE_OUT, (
            f"{ttype} tail keyframe at {max(frames)} is after source_out "
            f"({HOOK_SOURCE_OUT}) - transition fires after clip is gone"
        )

    @pytest.mark.parametrize("ttype", ["fade_to_black", "defocus", "flash"])
    def test_head_transition_within_played_window(self, ttype):
        """Head transitions must start at source_in, not frame 0."""
        _reset_counters()
        block = fx.transition_head(
            HOOK_CLIP_DUR, ttype, dur_frames=7,
            source_in=HOOK_SOURCE_IN,
            source_out=HOOK_SOURCE_OUT,
        )
        from library.tools.fusion.nodes import BezierSpline
        splines = [n for n in block.nodes if isinstance(n, BezierSpline)]
        assert splines, f"{ttype} head has no spline"
        frames = [kf.frame for kf in splines[0].keyframes]
        assert min(frames) >= HOOK_SOURCE_IN, (
            f"{ttype} head keyframe at {min(frames)} is before source_in "
            f"({HOOK_SOURCE_IN}) - transition fires before clip starts"
        )


class TestBuildEffectCompWithSourceWindow:
    """build_effect_comp places all keyframes within source_in..source_out."""

    def test_hook_zoom_and_defocus_within_played_window(self):
        """Reproduce the exact hook scenario from project 001.

        The hook is 2.4s (72 frames) taken from source seconds 0.836-3.234
        of a 5657-frame clip, i.e. source frames 25-97.

        Before the fix, Transform1Size had keyframes at [0]=1.0,
        [2828]=1.0, [5656]=1.03 and the defocus had keyframes at
        [5646]=0.0 .. [5656]=3.0 - all outside the played window.
        """
        effects = {
            'zoom_start': 1.0,
            'zoom_mid': 1.015,
            'zoom_end': 1.03,
            'tail_transition': 'defocus',
            'tail_transition_frames': 10,
            'source_in_frame': HOOK_SOURCE_IN,
            'source_out_frame': HOOK_SOURCE_OUT,
            'vignette': False,
        }
        comp = build_effect_comp(effects, HOOK_CLIP_DUR,
                                 source_res=(1920, 1080))

        all_kf = _extract_all_keyframes(comp)
        assert all_kf, "No keyframed splines found in comp"

        # The zoom spline must be entirely within the played window
        zoom_kf = all_kf.get("Transform1Size", [])
        assert zoom_kf, "No zoom keyframes"
        assert min(zoom_kf) >= HOOK_SOURCE_IN, (
            f"Zoom keyframe at {min(zoom_kf)} before source_in "
            f"({HOOK_SOURCE_IN})"
        )
        assert max(zoom_kf) <= HOOK_SOURCE_OUT, (
            f"Zoom keyframe at {max(zoom_kf)} after source_out "
            f"({HOOK_SOURCE_OUT})"
        )

        # The defocus transition's active frames must end at source_out.
        # BezierSpline.sampled may place a neutral hold_before at frame 0
        # (value=0.0 means no defocus), which is fine since frame 0 isn't
        # played and the held value is neutral.
        defocus_kf = all_kf.get("TransDefocus1Size", [])
        assert defocus_kf, "No defocus keyframes"
        assert max(defocus_kf) <= HOOK_SOURCE_OUT, (
            f"Defocus keyframe at {max(defocus_kf)} after source_out "
            f"({HOOK_SOURCE_OUT}) - transition fires after clip is gone"
        )
        # Active defocus frames (excluding the hold-before) must start
        # near source_out - dur_frames
        active_kf = [f for f in defocus_kf if f > 0]
        if active_kf:
            assert min(active_kf) >= HOOK_SOURCE_OUT - 10, (
                f"Defocus active keyframe at {min(active_kf)} is far from "
                f"source_out ({HOOK_SOURCE_OUT})"
            )

    def test_source_in_zero_still_works(self):
        """A segment starting at frame 0 (the common case) still works."""
        effects = {
            'zoom_start': 1.0,
            'zoom_mid': 1.02,
            'zoom_end': 1.03,
            'vignette': False,
        }
        comp = build_effect_comp(effects, 120, source_res=(1920, 1080))
        all_kf = _extract_all_keyframes(comp)
        assert all_kf, "No keyframed splines"
        for name, frames in all_kf.items():
            assert min(frames) >= 0
            assert max(frames) <= 119

    def test_omitted_source_window_defaults_to_full_source(self):
        """When source_in/out are not in effects, whole source is used."""
        effects = {
            'zoom_start': 1.0,
            'zoom_end': 1.03,
            'vignette': False,
        }
        comp = build_effect_comp(effects, 120, source_res=(1920, 1080))
        all_kf = _extract_all_keyframes(comp)
        assert all_kf
        # Should span 0..119 (the whole source)
        all_frames = [f for frames in all_kf.values() for f in frames]
        assert min(all_frames) == 0
        assert max(all_frames) == 119


class TestFadeKeyframesInPlayedWindow:
    """Fade keyframes land within the played segment."""

    def test_fade_out_at_source_out(self):
        _reset_counters()
        block = fx.fade(
            HOOK_CLIP_DUR,
            fade_out=10,
            source_in=HOOK_SOURCE_IN,
            source_out=HOOK_SOURCE_OUT,
        )
        from library.tools.fusion.nodes import BezierSpline
        splines = [n for n in block.nodes if isinstance(n, BezierSpline)]
        assert splines
        frames = [kf.frame for kf in splines[0].keyframes]
        # Fade-out end should be at source_out, not clip_dur-1
        assert max(frames) <= HOOK_SOURCE_OUT, (
            f"Fade keyframe at {max(frames)} is after source_out "
            f"({HOOK_SOURCE_OUT})"
        )

    def test_fade_in_at_source_in(self):
        _reset_counters()
        block = fx.fade(
            HOOK_CLIP_DUR,
            fade_in=10,
            source_in=HOOK_SOURCE_IN,
            source_out=HOOK_SOURCE_OUT,
        )
        from library.tools.fusion.nodes import BezierSpline
        splines = [n for n in block.nodes if isinstance(n, BezierSpline)]
        assert splines
        frames = [kf.frame for kf in splines[0].keyframes]
        # Fade-in start should be at source_in, not 0
        assert min(frames) >= HOOK_SOURCE_IN, (
            f"Fade keyframe at {min(frames)} is before source_in "
            f"({HOOK_SOURCE_IN})"
        )


class TestVfxLabelAtBoundary:
    """VFX at a clip boundary lands on the clip that starts there."""

    def test_float_boundary_prefers_next_clip(self):
        """Reproduce the speech_2/speech_3 bug.

        speech_2_seg0 ends at timeline_out=8.382000000000001.
        speech_3_seg0 starts at timeline_in=8.382.
        A VFX at timeline_start=8.38 should go to speech_3_seg0.

        Before the fix, _v1_label_at matched speech_2_seg0 because
        8.38 < 8.382000000000001 was True.
        """
        import importlib
        import library.steps.step_5_04_compile_manifest.step as cm
        _v1_label_at = cm._v1_label_at

        v1_clips = [
            {"label": "speech_2_seg0", "timeline_in": 5.553, "timeline_out": 8.382000000000001},
            {"label": "speech_3_seg0", "timeline_in": 8.382, "timeline_out": 18.397},
        ]

        # 8.38 is 2ms before speech_2's end - should match speech_3's start
        result = _v1_label_at(v1_clips, 8.38)
        assert result == "speech_3_seg0", (
            f"VFX at 8.38s matched {result!r} instead of speech_3_seg0 - "
            f"float boundary let the previous clip steal the assignment"
        )

    def test_clearly_inside_clip(self):
        """A point clearly inside a clip still matches it."""
        import library.steps.step_5_04_compile_manifest.step as cm
        _v1_label_at = cm._v1_label_at

        v1_clips = [
            {"label": "speech_2_seg0", "timeline_in": 5.553, "timeline_out": 8.382},
            {"label": "speech_3_seg0", "timeline_in": 8.382, "timeline_out": 18.397},
        ]

        assert _v1_label_at(v1_clips, 6.0) == "speech_2_seg0"
        assert _v1_label_at(v1_clips, 10.0) == "speech_3_seg0"


class TestVfxCollisionAssertion:
    """The preservation assertion catches VFX collisions."""

    def test_collision_raises(self):
        """Two VFX on the same clip must fail the assertion."""
        import library.steps.step_5_04_compile_manifest.step as cm

        manifest = {
            "tracks": {
                "V2": {"clips": []},
                "A3": {"clips": []},
            },
            "fusion_effects": {"per_clip": {"clip_a": {}}},
        }
        with pytest.raises(ValueError, match="collision"):
            cm._assert_planner_output_preserved(
                manifest,
                broll_planned=0, sfx_planned=0, vfx_planned=2,
                broll_dropped_by_overlap=[],
                vfx_collisions=["vfx_002@8.38s collided on speech_2_seg0"],
            )

    def test_no_collision_passes(self):
        """No collisions should not raise."""
        import library.steps.step_5_04_compile_manifest.step as cm

        manifest = {
            "tracks": {
                "V2": {"clips": []},
                "A3": {"clips": []},
            },
            "fusion_effects": {"per_clip": {"clip_a": {}, "clip_b": {}}},
        }
        # Should not raise
        cm._assert_planner_output_preserved(
            manifest,
            broll_planned=0, sfx_planned=0, vfx_planned=2,
            broll_dropped_by_overlap=[],
            vfx_collisions=[],
        )
