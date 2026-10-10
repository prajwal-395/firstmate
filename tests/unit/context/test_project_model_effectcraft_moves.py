"""Unit 7c tests: plan_vfx moves project onto EffectCraft transforms.

Fast boundary tests: the projected keys equal what Ren's Fusion path
keys for the same move (read off ``fx.zoom``/``fx.shake`` blocks, with
the drift easing and the shake RNG pinned independently), refusals stay
fail-closed, and the checked-in conformance requests regenerate
byte-exact. No EffectCraft CLI is built or invoked here; the
qualification proof renders these fixtures through the pinned CLI.
"""
import json
import math
import random
from pathlib import Path

import pytest

from library.tools import project_model_effectcraft as ec
from library.tools import project_model_effectcraft_moves as mv
from library.tools.fusion import effects as E
from library.tools.fusion.effects import fx

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests/fixtures/effectcraft_fframes_conformance"

WIDTH, HEIGHT, FPS, DUR = 96, 64, 10.0, 0.5
FRAMES = round(DUR * FPS)


def _comp():
    return ec.CompSpec(name="Main", width=WIDTH, height=HEIGHT,
                       frame_rate=FPS, duration_seconds=DUR)


def _renders(name):
    return [ec.RenderSpec(format="rgba_png_sequence",
                          output=f"move-{name}_#####.png")]


def _model_fixture(name):
    return json.loads((FIXTURES / f"model-move-{name}.json")
                      .read_text(encoding="utf-8"))


def _project(name):
    return ec.project_to_request(
        _model_fixture(name), _comp(), _renders(name),
        project_id=f"ec-conformance-move-{name}")


def _keys(projection, path):
    return [(op["time"], op["value"]) for op in
            projection.request["operations"]
            if op.get("op") == "keyframe" and op.get("path") == path]


@pytest.mark.parametrize("name", ["drift", "punch", "shake", "cut"])
def test_move_request_regenerates_the_checked_in_fixture(name):
    assert _project(name).request == json.loads(
        (FIXTURES / f"request-move-{name}.json").read_text(
            encoding="utf-8"))


def test_drift_scale_follows_the_sine_law_independently():
    """The drift easing is pinned without touching the projector's code.

    Sine ease-in-out sampled per frame: no shared helper, just
    ``math.cos`` - so this fails if the baked curve ever stops being
    Ren's Fusion drift. Expected values pass through the comp's own
    6-decimal serialization quantum (a Size of 1.00585786 serializes
    as 1.005858); the tolerance below covers only what the bake adds
    past that.
    """
    projection = _project("drift")
    keys = _keys(projection, "transform/scale")
    assert [t for t, _ in keys] == [f / FPS for f in range(FRAMES)]
    for frame, (time, value) in enumerate(keys):
        eased = 0.5 * (1.0 - math.cos(math.pi * frame / (FRAMES - 1)))
        expected = round(1.0 + 0.04 * eased, 6) * 100.0
        assert value == pytest.approx([expected, expected, 100.0],
                                      abs=mv.SCALE_TOLERANCE_PCT), frame


def test_punch_scale_matches_the_fusion_block_key_for_key():
    E._reset_counters()
    block = fx.zoom(5, start=1.0, mid=1.08, end=1.0,
                    zoom_in_duration_frames=1,
                    zoom_release_offset_frames=2,
                    zoom_out_duration_frames=1)
    spline = next(n for n in block.nodes if hasattr(n, "keyframes"))
    expected = {kf.frame: kf.value for kf in spline.keyframes}
    keys = _keys(_project("punch"), "transform/scale")
    assert len(keys) == len(expected) == FRAMES
    for frame, (time, value) in enumerate(keys):
        assert time == pytest.approx(frame / FPS)
        assert value[0] == pytest.approx(expected[frame] * 100.0,
                                         abs=mv.SCALE_TOLERANCE_PCT)
        assert (value[1], value[2]) == (value[0], 100.0)


def test_shake_position_matches_the_fusion_block_and_the_rng():
    E._reset_counters()
    block = fx.shake(5, x_amount=0.02, y_amount=0.01, decay_frames=3)
    splines = {n.name[-1]: n for n in block.nodes
               if hasattr(n, "keyframes")}
    keys = _keys(_project("shake"), "transform/position")
    assert len(keys) == FRAMES
    for frame, (time, value) in enumerate(keys):
        cx = next(kf.value for kf in splines["X"].keyframes
                  if kf.frame == frame)
        cy = next(kf.value for kf in splines["Y"].keyframes
                  if kf.frame == frame)
        assert value == pytest.approx(
            [cx * WIDTH, (1.0 - cy) * HEIGHT],
            abs=mv.POSITION_TOLERANCE_PX)
    # The seed contract, independently: the first frame draws first.
    drawn = random.Random(42)
    xo = drawn.uniform(-0.02, 0.02)
    yo = drawn.uniform(-0.01, 0.01)
    assert keys[0][1] == pytest.approx(
        [(0.5 + xo) * WIDTH, (0.5 - yo) * HEIGHT],
        abs=mv.POSITION_TOLERANCE_PX)


def test_cut_in_holds_a_constant_reframe_with_neutral_bounds():
    keys = _keys(_project("cut"), "transform/scale")
    by_time = dict(keys)
    assert by_time[0.0] == [100.0, 100.0, 100.0]
    assert by_time[0.1] == by_time[0.3] == [106.0, 106.0, 100.0]
    assert by_time[0.4] == [100.0, 100.0, 100.0]


def test_pan_end_projects_a_static_recentre():
    model = _model_fixture("drift")
    params = dict(model["effects"][0]["parameters"])
    params["pan_end"] = [0.6, 0.4]
    model["effects"][0]["parameters"] = params
    projection = ec.project_to_request(
        model, _comp(), _renders("drift"),
        project_id="ec-conformance-move-drift-pan")
    keys = _keys(projection, "transform/position")
    assert keys
    for _, value in keys:
        assert value == pytest.approx([0.6 * WIDTH, 0.6 * HEIGHT],
                                      abs=mv.POSITION_TOLERANCE_PX)


def test_neutral_pan_projects_no_position_keys():
    model = _model_fixture("drift")
    params = dict(model["effects"][0]["parameters"])
    params["pan_end"] = [0.5, 0.5]
    model["effects"][0]["parameters"] = params
    projection = ec.project_to_request(
        model, _comp(), _renders("drift"),
        project_id="ec-conformance-move-drift-neutral-pan")
    assert _keys(projection, "transform/position") == []
    assert projection.identity_map["effects"]["effect-drift"]["paths"] == [
        "transform/scale"]


def test_moves_take_no_engine_effect_slot():
    model = _model_fixture("drift")
    model["effects"].append({
        "id": "effect-blur", "class_id": "blur.gaussian",
        "target_id": "use-checker",
        "interval": {"clock_id": "clock-project", "start": 0, "end": 5},
        "parameters": {"blurriness": 6.0}, "keyframes": []})
    projection = ec.project_to_request(
        model, _comp(), _renders("drift"),
        project_id="ec-conformance-move-drift-blur")
    apply = next(op for op in projection.request["operations"]
                 if op["op"] == "apply_effect")
    assert apply["params"] == {"blurriness": 6.0}
    effect_keys = [op for op in projection.request["operations"]
                   if op.get("op") == "keyframe"
                   and op.get("path", "").startswith("effects/")]
    assert all(op["path"] == "effects/#1/blurriness"
               for op in effect_keys)
    identity = projection.identity_map["effects"]
    assert identity["effect-drift"]["move"] == "move.slow_zoom_in"
    assert set(identity["effect-blur"]) >= {"layer", "effect_index",
                                            "effectcraft_id"}


def _drift_model(**overrides):
    model = _model_fixture("drift")
    effect = model["effects"][0]
    for key, value in overrides.items():
        if key == "parameters":
            effect["parameters"] = value
        elif key == "class_id":
            effect["class_id"] = value
        elif key == "target_id":
            effect["target_id"] = value
        elif key == "keyframes":
            effect["keyframes"] = value
        elif key == "interval":
            effect["interval"] = value
        else:  # pragma: no cover - test helper, all keys listed
            raise AssertionError(f"unhandled override {key}")
    return model


def _refused(model, exc=mv.UnprojectableMove):
    with pytest.raises(exc):
        ec.project_to_request(model, _comp(), _renders("drift"),
                              project_id="ec-move-refused")


def test_unknown_move_class_is_still_an_unknown_effect():
    _refused(_drift_model(class_id="move.tractor_beam"),
             exc=ec.UnknownEffect)


def test_unknown_param_is_refused():
    _refused(_drift_model(parameters={"zoom_start": 1.0,
                                      "zoom_end": 1.04,
                                      "zoom_percent": 5}))


def test_non_numeric_param_is_refused():
    _refused(_drift_model(parameters={"zoom_start": "far",
                                      "zoom_end": 1.04}))


def test_mask_target_is_refused():
    model = _drift_model(target_id="mask-ghost")
    model["masks"] = [{
        "id": "mask-ghost", "entity_id": "object-checker",
        "interval": {"clock_id": "clock-project", "start": 0, "end": 5},
        "representation": {"kind": "rect",
                           "normalized_bbox": [0.0, 0.0, 0.5, 0.5]},
        "source_model": "synthetic_fixture",
        "provenance_id": "prov-ec-conf"}]
    with pytest.raises(mv.UnprojectableMove):
        ec.project_to_request(model, _comp(), _renders("drift"),
                              project_id="ec-move-mask-target")


def test_carried_keyframes_are_refused():
    _refused(_drift_model(keyframes=[{"tick": 0, "value": 1.0}]))


def test_zero_shake_is_refused():
    model = _model_fixture("shake")
    model["effects"][0]["parameters"] = {"shake_x": 0.0,
                                         "shake_y": 0.0}
    with pytest.raises(mv.UnprojectableMove):
        ec.project_to_request(model, _comp(), _renders("shake"),
                              project_id="ec-move-zero-shake")


def test_shakeless_shake_is_refused():
    model = _model_fixture("shake")
    model["effects"][0]["parameters"] = {"shake_decay_frames": 3}
    with pytest.raises(mv.UnprojectableMove):
        ec.project_to_request(model, _comp(), _renders("shake"),
                              project_id="ec-move-shakeless")


def test_legacy_punch_is_refused():
    _refused(_drift_model(parameters={"zoom_start": 1.0,
                                      "zoom_mid": 1.08,
                                      "zoom_end": 1.0}))


def test_overlapping_punch_ramps_are_refused():
    model = _model_fixture("punch")
    params = dict(model["effects"][0]["parameters"])
    params["zoom_release_offset_frames"] = 0
    model["effects"][0]["parameters"] = params
    with pytest.raises(mv.UnprojectableMove):
        ec.project_to_request(model, _comp(), _renders("punch"),
                              project_id="ec-move-overlap")


def test_punch_taller_than_its_window_is_refused():
    model = _model_fixture("punch")
    model["effects"][0]["interval"] = {"clock_id": "clock-project",
                                      "start": 0, "end": 2}
    with pytest.raises(mv.UnprojectableMove):
        ec.project_to_request(model, _comp(), _renders("punch"),
                              project_id="ec-move-short-window")


def test_empty_interval_is_refused_at_the_model_boundary():
    # The schema refuses an empty interval before projection runs;
    # move_comp_frames refuses one handed to it directly (below).
    with pytest.raises(ValueError, match="half-open"):
        ec.project_to_request(
            _drift_model(interval={"clock_id": "clock-project",
                                   "start": 2, "end": 2}),
            _comp(), _renders("drift"),
            project_id="ec-move-empty-interval")


def test_unknown_easing_is_refused():
    _refused(_drift_model(parameters={"zoom_start": 1.0,
                                      "zoom_end": 1.04,
                                      "zoom_easing": "Swirl"}))


def test_malformed_pan_is_refused():
    _refused(_drift_model(parameters={"zoom_start": 1.0,
                                      "zoom_end": 1.04,
                                      "pan_end": [0.6]}))


def test_move_comp_frames_follow_the_matte_touch_rule():
    interval = {"clock_id": "clock-project", "start": 0, "end": 5}
    assert mv.move_comp_frames(interval, 10, 10.0,
                               "effect-x") == (0, 4)
    edge = {"clock_id": "clock-project", "start": 5, "end": 6}
    assert mv.move_comp_frames(edge, 10, 10.0, "effect-x") == (5, 5)
    with pytest.raises(mv.UnprojectableMove):
        mv.move_comp_frames({"clock_id": "c", "start": 0, "end": 0},
                            10, 10.0, "effect-x")


def test_identity_map_names_move_paths_and_frames():
    identity = _project("shake").identity_map["effects"]
    assert identity["effect-shake"] == {
        "layer": "checker.bmp", "move": "move.screen_shake",
        "paths": ["transform/position"], "frames": [0, 4]}
    identity = _project("cut").identity_map["effects"]
    assert identity["effect-reframe"]["frames"] == [1, 3]


def test_non_dict_parameters_are_refused_at_the_model_boundary():
    # The schema requires a parameter mapping, so a list never reaches
    # the move projector; this pins the refusal layer, not its type.
    from library.tools.protocol_conformance import (
        ProtocolConformanceError,
    )
    model = _drift_model()
    model["effects"][0]["parameters"] = [1.0, 1.04]
    with pytest.raises(ProtocolConformanceError):
        ec.project_to_request(model, _comp(), _renders("drift"),
                              project_id="ec-move-list-params")


def test_missing_zoom_end_is_refused():
    _refused(_drift_model(parameters={"zoom_start": 1.0}))
