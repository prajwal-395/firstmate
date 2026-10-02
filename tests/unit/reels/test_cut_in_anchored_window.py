"""A comp keys its zoom to the anchored `effect_window_frames` and holds
neutral outside it; no window (or a full-range one) keys as before.
Asserted on the serialized comp's own splines.

History: docs/evidence/composed_edit.md.
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


def test_no_window_or_a_full_range_window_punches_the_whole_item():
    """No window (an unanchored entry spanning the block) keeps the
    whole-item behaviour - a scalar Size, every frame punched - and a
    window covering everything played is no window: same scalar, no
    spline either."""
    for window in (None, [0, 215]):
        comp_text = _comp(1.15, window)
        assert "Size = Input { Value = 1.15, }" in comp_text, window
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
