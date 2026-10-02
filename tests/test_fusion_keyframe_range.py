"""Keyframes land inside the PLAYED window, not across the whole source.

History: docs/evidence/resolve_test_history.md#test_fusion_keyframe_range.
"""
import re

import pytest

from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.fusion.effects import _reset_counters, fx
from library.tools.fusion.played_window import played_range


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
# The same window in the comp's own frames, which is where keyframes go.
HOOK_FIRST, HOOK_LAST = played_range(
    HOOK_CLIP_DUR, HOOK_SOURCE_IN, HOOK_SOURCE_OUT)


def test_zoom_and_transition_keyframes_land_inside_the_played_window():
    """slow_zoom_in 1.0 -> 1.03 on the hook clip, and every tail and
    head transition: a tail must end at the last played frame, not
    clip_dur-1 (past it, it fires after the clip is gone and Fusion
    holds it across everything that plays); a head must start at the
    first played frame, not frame 0."""
    from library.tools.fusion.nodes import BezierSpline

    def frames_of(block, what):
        splines = [n for n in block.nodes if isinstance(n, BezierSpline)]
        assert splines, f"{what} has no spline"
        return [kf.frame for kf in splines[0].keyframes]

    window = dict(source_in=HOOK_SOURCE_IN, source_out=HOOK_SOURCE_OUT)
    _reset_counters()
    frames = frames_of(fx.zoom(HOOK_CLIP_DUR, start=1.0, mid=1.015,
                               end=1.03, **window), "zoom")
    assert HOOK_FIRST <= min(frames) and max(frames) <= HOOK_LAST, frames
    for ttype in ("fade_to_black", "defocus", "flash"):
        _reset_counters()
        tail = frames_of(fx.transition_tail(
            HOOK_CLIP_DUR, ttype, dur_frames=7, res=(1080, 1920), **window),
            f"{ttype} tail")
        assert max(tail) <= HOOK_LAST, (ttype, tail)
        _reset_counters()
        head = frames_of(fx.transition_head(
            HOOK_CLIP_DUR, ttype, dur_frames=7, res=(1080, 1920), **window),
            f"{ttype} head")
        assert min(head) >= HOOK_FIRST, (ttype, head)


class TestBuildEffectCompWithSourceWindow:
    """build_effect_comp places all keyframes within source_in..source_out."""

    def test_hook_zoom_and_defocus_within_played_window(self):
        """Reproduce the exact hook scenario from project 001.

        The hook is 2.4s (73 frames) taken from source seconds
        0.836-3.234 of a 5657-frame clip, i.e. source frames 25-97,
        which is comp frames 0-72.

        Two readings have been wrong here. Originally Transform1Size had
        keyframes at [0], [2828], [5656] and the defocus at [5646]..
        [5656] - across the whole source. Then they moved to [25]..[97],
        which is the source's numbering and still outside the 73 frames
        the comp renders.
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
        assert min(zoom_kf) >= HOOK_FIRST, (
            f"Zoom keyframe at comp frame {min(zoom_kf)} before the first "
            f"played frame ({HOOK_FIRST})"
        )
        assert max(zoom_kf) <= HOOK_LAST, (
            f"Zoom keyframe at comp frame {max(zoom_kf)} after the last "
            f"played frame ({HOOK_LAST})"
        )

        # The defocus transition's active frames must end at source_out.
        # BezierSpline.sampled may place a neutral hold_before at frame 0
        # (value=0.0 means no defocus), which is fine since frame 0 isn't
        # played and the held value is neutral.
        defocus_kf = all_kf.get("TransDefocus1Size", [])
        assert defocus_kf, "No defocus keyframes"
        assert max(defocus_kf) <= HOOK_LAST, (
            f"Defocus keyframe at comp frame {max(defocus_kf)} after the "
            f"last played frame ({HOOK_LAST}) - it fires after the clip "
            f"is gone"
        )
        # Active defocus frames (excluding the neutral hold at frame 0)
        # must start dur_frames back from the last played frame.
        active_kf = [f for f in defocus_kf if f > 0]
        assert active_kf, "Defocus has no active keyframes"
        assert min(active_kf) >= HOOK_LAST - 10, (
            f"Defocus active keyframe at {min(active_kf)} is far from the "
            f"last played frame ({HOOK_LAST})"
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


class TestVfxCollisionAssertion:
    """The preservation assertion catches VFX collisions."""

    def test_collision_raises_and_absence_passes(self):
        """Two VFX on the same clip must fail the assertion; no
        collisions must not raise (B1 fold of `test_no_collision_passes`
        into its neighbouring collision test)."""
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

        manifest["fusion_effects"] = {"per_clip": {"clip_a": {}, "clip_b": {}}}
        cm._assert_planner_output_preserved(
            manifest,
            broll_planned=0, sfx_planned=0, vfx_planned=2,
            broll_dropped_by_overlap=[],
            vfx_collisions=[],
        )
