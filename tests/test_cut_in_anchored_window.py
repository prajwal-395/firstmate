"""Comps key to the anchored span (finding 36).

4.03 resolved a `cut_in` anchored to the word 'quit' (0.2-7.185 s),
but the build drew one constant-zoom comp over the whole hook item
(0-216) at 1.15 - the exported frame at 0.07 s is already punched.
The resolved sub-block anchor reached per_clip as params but the
comp keyed its keyframes to the whole played item, ignoring it.

The fix threads the window through: compile_manifest records the
entry's absolute timeline span as `effect_window`, the applicator
turns it into comp frames (`effect_window_frames`), and `fx.zoom`
keys inside the window while holding neutral (1.0) outside it. A
window covering the whole played range keys exactly as before.

These tests read the serialized comp's own splines - the bytes
Resolve holds - never the plan. No Resolve writes.
"""
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.fusion.transition_frames import parse_splines, value_at


def _size_keys(comp_text):
    splines = parse_splines(comp_text)
    names = [n for n in splines if n.endswith("Size")]
    assert len(names) == 1, f"one zoom spline, got {sorted(splines)}"
    return splines[names[0]]


def _comp(zoom, window_frames=None, clip_dur=216):
    effects = {"zoom_start": zoom, "zoom_mid": zoom, "zoom_end": zoom}
    if window_frames is not None:
        effects["effect_window_frames"] = list(window_frames)
    return build_effect_comp(effects, clip_dur, (1920, 1080))


def test_anchored_cut_in_holds_neutral_outside_the_window():
    """Finding 36's exact shape: constant 1.15 over 216 played
    frames, anchored to 0.2-7.185 s = comp frames [6, 215]. Frame 2
    (0.07 s, before the word) holds neutral; the punch draws only
    inside the anchored span."""
    keys = _size_keys(_comp(1.15, [6, 215]))
    assert value_at(keys, 2) == 1.0
    assert value_at(keys, 100) == 1.15
    assert value_at(keys, 215) == 1.15


def test_unwindowed_cut_in_still_punches_the_whole_item():
    """No window (an unanchored entry spanning the block) keeps the
    current whole-item behaviour: a scalar Size over the clip, so
    every frame - including frame 2 - plays punched."""
    comp_text = _comp(1.15)
    assert "Size = Input { Value = 1.15, }" in comp_text
    assert parse_splines(comp_text) == {}


def test_a_full_range_window_keys_exactly_as_no_window():
    """A window covering everything played is no window: the windowed
    build carries the same scalar Size and no spline either."""
    comp_text = _comp(1.15, [0, 215])
    assert "Size = Input { Value = 1.15, }" in comp_text
    assert parse_splines(comp_text) == {}


def test_windowed_drift_ramps_inside_and_holds_outside():
    """A drift keys its eased ramp to the window and holds neutral
    on both sides of it."""
    effects = {"zoom_start": 1.0, "zoom_mid": 1.02, "zoom_end": 1.04,
               "effect_window_frames": [10, 50]}
    keys = _size_keys(build_effect_comp(effects, 216, (1920, 1080)))
    assert value_at(keys, 0) == 1.0
    assert value_at(keys, 5) == 1.0
    assert 1.0 < value_at(keys, 30) < 1.04
    assert value_at(keys, 50) == 1.04
    assert value_at(keys, 100) == 1.0
