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


# ── Producer: plan_vfx toolkit entries to ``move.*`` model effects ──

def _mint_entry(vfx_id="vfx_001", block="1", effect_type="slow_zoom_in",
                params=None):
    return {"vfx_id": vfx_id, "target_block_position": block,
            "timeline_start": 0.0, "timeline_end": 0.5,
            "effect_type": effect_type, "params": dict(params or {})}


def _mint_single(entry, *, model=None, targets=None, raw=None,
                 keyframes=()):
    model = model if model is not None else _model_fixture("drift")
    if keyframes:
        entry = dict(entry, keyframes=list(keyframes))
    return mv.mint_move_effects(
        resolved_entries=[entry], model=model,
        targets={"1": "use-checker"} if targets is None else targets,
        raw_params_by_vfx=(
            {entry["vfx_id"]: raw} if raw is not None else None))


def _wide_model():
    """Drift fixture with the checker use spanning the whole comp."""
    model = _model_fixture("drift")
    for use in model["clip_uses"]:
        if use["id"] == "use-checker":
            use["timeline_interval"] = {"clock_id": "clock-project",
                                        "start": 0, "end": 5}
    return model


def _project_single(minted, model):
    solo = model
    solo["effects"] = [minted]
    return ec.project_to_request(
        solo, _comp(), _renders("drift"),
        project_id=f"ec-move-minted-{minted['id']}")


def _mint_value_for(name):
    if name.endswith("_frames"):
        return 3
    if name == "pan_end":
        return [0.45, 0.5]
    if name in ("shake_x", "shake_y"):
        return 0.004
    return 1.03


def test_every_toolkit_entry_mints_or_refuses_by_name():
    """Each toolkit spelling reaches the mint as a mint or a refusal.

    Iterates TOOLKIT_PARAMETERS rather than a hardcoded list: an entry
    the mint maps must carry the mapped class, and one it cannot map
    must raise UnprojectableMove - a toolkit entry may never fall
    through silently. This pins routing, not the set: a new toolkit
    effect extends the iteration instead of breaking a count.
    """
    from library.steps.step_4_03_plan_vfx.post_bridge import (
        TOOLKIT_PARAMETERS,
    )
    for effect_type, names in TOOLKIT_PARAMETERS.items():
        params = {name: _mint_value_for(name) for name in names}
        try:
            minted = mv.mint_move_effect(
                effect_type=effect_type, params=params,
                target_clip_use="use-x",
                timeline_interval={"clock_id": "c", "start": 0,
                                   "end": 5},
                effect_id="move-test", clip_use_ids={"use-x"},
                mask_ids=set())
        except mv.UnprojectableMove:
            continue
        assert effect_type in mv.PLAN_EFFECT_TO_MOVE_CLASS
        assert (minted["class_id"]
                == mv.PLAN_EFFECT_TO_MOVE_CLASS[effect_type])


def _wanted_path(fixture):
    return ("transform/position" if fixture == "shake"
            else "transform/scale")


@pytest.mark.parametrize(("plan_type", "params", "fixture", "move"), [
    ("slow_zoom_in", {"zoom_start": 1.0, "zoom_end": 1.04},
     "drift", "move.slow_zoom_in"),
    ("zoom_emphasis",
     {"zoom_start": 1.0, "zoom_mid": 1.08, "zoom_end": 1.0,
      "zoom_in_duration_frames": 1,
      "zoom_release_offset_frames": 2,
      "zoom_out_duration_frames": 1,
      # The bridge carries the plan's seconds beside its derivation;
      # the mint keeps the derivation, not the words.
      "zoom_in_seconds": 0.1, "zoom_out_seconds": 0.1},
     "punch", "move.zoom_emphasis"),
    ("screen_shake",
     {"shake_x": 0.02, "shake_y": 0.01, "shake_decay_frames": 3},
     "shake", "move.screen_shake"),
    ("cut_in",
     {"zoom_start": 1.06, "zoom_mid": 1.06, "zoom_end": 1.06},
     "cut", "move.cut_in"),
])
def test_mint_matches_the_proven_fixture_effect(plan_type, params,
                                                fixture, move):
    """Minted effects are the hand-proven conformance effects.

    The fixtures in ``effectcraft_fframes_conformance`` render through
    the pinned CLI; a minted effect carrying the same class, target,
    interval and parameters projects to the same request.
    """
    wanted = _model_fixture(fixture)["effects"][0]
    (minted,) = _mint_single(_mint_entry(effect_type=plan_type,
                                         params=params),
                             model=_wide_model())
    assert minted["class_id"] == wanted["class_id"] == move
    assert minted["target_id"] == wanted["target_id"] == "use-checker"
    assert minted["interval"] == {"clock_id": "clock-project",
                                  "start": 0, "end": 5}
    assert minted["parameters"] == wanted["parameters"]
    assert minted["keyframes"] == []
    projection = _project_single(minted, _wide_model())
    paths = {op["path"] for op in projection.request["operations"]
             if op.get("op") == "keyframe"}
    assert paths == {_wanted_path(fixture)}
    assert (projection.identity_map["effects"][minted["id"]]["move"]
            == move)


def test_mint_zoom_out_projects_a_descending_scale():
    (minted,) = _mint_single(_mint_entry(
        effect_type="slow_zoom_out",
        params={"zoom_start": 1.04, "zoom_end": 1.0}))
    assert minted["class_id"] == "move.slow_zoom_out"
    projection = _project_single(minted, _wide_model())
    scales = [op["value"][0] for op in
              projection.request["operations"]
              if op.get("op") == "keyframe"
              and op.get("path") == "transform/scale"]
    assert scales[0] == pytest.approx(104.0)
    assert scales[-1] == pytest.approx(100.0)
    assert scales == sorted(scales, reverse=True)


def test_mint_recovers_zoom_easing_from_the_raw_plan():
    """The bridge strips ``zoom_easing`` for Fusion; the mint recovers it.

    The drift curve reads the easing (``DRIFT_EASING`` only when the
    plan states nothing), so dropping it would bake a different move
    than the plan chose.
    """
    raw = {"zoom_start": 1.0, "zoom_end": 1.04,
           "zoom_easing": "Sine"}
    (minted,) = _mint_single(
        _mint_entry(params={"zoom_start": 1.0, "zoom_end": 1.04}),
        raw=raw)
    assert minted["parameters"]["zoom_easing"] == "Sine"


def test_mint_refuses_a_mask_target():
    model = _model_fixture("drift")
    model["masks"] = [{"id": "mask-x"}]
    with pytest.raises(mv.UnprojectableMove):
        _mint_single(_mint_entry(), model=model,
                     targets={"1": "mask-x"})


def test_mint_refuses_carried_keyframes():
    with pytest.raises(mv.UnprojectableMove):
        _mint_single(_mint_entry(), keyframes=[{"tick": 0,
                                                "value": 1.0}])


def test_mint_refuses_an_off_midpoint_punch():
    with pytest.raises(mv.UnprojectableMove):
        _mint_single(_mint_entry(
            effect_type="cut_in",
            params={"zoom_start": 1.0, "zoom_mid": 1.08,
                    "zoom_end": 1.0}))


def test_mint_refuses_a_zero_shake():
    with pytest.raises(mv.UnprojectableMove):
        _mint_single(_mint_entry(
            effect_type="screen_shake",
            params={"shake_x": 0.0, "shake_y": 0.0}))
    with pytest.raises(mv.UnprojectableMove):
        _mint_single(_mint_entry(
            effect_type="screen_shake",
            params={"shake_decay_frames": 3}))


@pytest.mark.parametrize("effect_type", ["push_in", "ken_burns",
                                        "freeze_frame", "stabilize",
                                        "tractor_beam"])
def test_mint_refuses_what_the_bridge_must_resolve(effect_type):
    """Aliases resolve at the bridge; other routes are not moves."""
    with pytest.raises(mv.UnprojectableMove):
        _mint_single(_mint_entry(effect_type=effect_type, params={}))


def test_mint_refuses_an_unknown_clip_use():
    from library.tools.project_model_effectcraft import (
        UnprojectableEffectTarget,
    )
    with pytest.raises(UnprojectableEffectTarget):
        _mint_single(_mint_entry(), targets={"1": "use-ghost"})


def test_mint_refuses_a_block_with_no_clip_use():
    from library.tools.project_model_effectcraft import (
        UnprojectableEffectTarget,
    )
    with pytest.raises(UnprojectableEffectTarget):
        _mint_single(_mint_entry(), targets={})


def test_mint_pins_the_post_migration_clip_use_id():
    """G2: migrate legacy rows, then mint against the migrated model."""
    from library.tools.project_model_editorial import (
        aroll_node_to_clip_use,
    )
    node = {"node_id": "aroll:7",
            "payload": {"clip_id": "clip-checker",
                        "source_start": 0.0, "source_end": 0.5,
                        "timeline_start": 0.0, "timeline_end": 0.5}}
    kwargs = {"asset_by_clip": {"clip-checker": "asset-checker"},
              "source_clock_by_asset": {"asset-checker": "clock-src"},
              "source_tps_by_asset": {"asset-checker": 10},
              "timeline_clock": "clock-project", "timeline_tps": 10}
    use, reason = aroll_node_to_clip_use(node, **kwargs)
    assert reason is None
    # Stable ids: migrating twice names the same clip use.
    use_again, _ = aroll_node_to_clip_use(node, **kwargs)
    assert use_again["id"] == use["id"]
    assert use["timeline_interval"] == {"clock_id": "clock-project",
                                       "start": 0, "end": 5}
    model = _model_fixture("drift")
    model["clip_uses"] = [use]
    (minted,) = _mint_single(
        _mint_entry(params={"zoom_start": 1.0, "zoom_end": 1.04}),
        model=model, targets={"1": use["id"]})
    assert minted["target_id"] == use["id"]
    assert minted["interval"] == use["timeline_interval"]
    projection = _project_single(minted, model)
    scales = [op["value"][0] for op in
              projection.request["operations"]
              if op.get("op") == "keyframe"
              and op.get("path") == "transform/scale"]
    assert scales[0] == pytest.approx(100.0)
    assert scales[-1] == pytest.approx(104.0)


def _bridge_spine(count):
    return {"structure": [
        {"position": i + 1, "block_type": "speech",
         "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5)}
        for i in range(count)
    ]}


def test_accepted_bridge_entries_mint_and_project():
    """The bridge's accepted output is the mint's input, end to end.

    Seven plan entries - the five toolkit effects plus the ``push_in``
    alias and the ``ken_burns`` spelling - resolve through the real
    post-bridge, mint one model effect each, and every minted effect
    projects to keyframe ops on its own clip use.
    """
    from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
    reason = "a locked hold that goes dead under the point"
    plan = [
        {"target_block_position": 1, "effect_type": "slow_zoom_in",
         "params": {"zoom_start": 1.0, "zoom_end": 1.03},
         "rationale": reason},
        {"target_block_position": 2, "effect_type": "slow_zoom_out",
         "params": {"zoom_start": 1.03, "zoom_end": 1.0},
         "rationale": reason},
        {"target_block_position": 3, "effect_type": "zoom_emphasis",
         "params": {"zoom_start": 1.0, "zoom_mid": 1.08,
                    "zoom_end": 1.0, "zoom_in_seconds": 1.0,
                    "zoom_out_seconds": 1.0},
         "anchor": {"frame": 360}, "anchor_end": {"frame": 390},
         "rationale": "a landing to punch"},
        {"target_block_position": 4, "effect_type": "screen_shake",
         "params": {"shake_x": 0.02, "shake_y": 0.01},
         "rationale": "an impact to sell"},
        {"target_block_position": 5, "effect_type": "cut_in",
         "params": {"zoom_start": 1.06, "zoom_mid": 1.06,
                    "zoom_end": 1.06},
         "rationale": "a face to hold closer"},
        {"target_block_position": 6, "effect_type": "push_in",
         "params": {"zoom_start": 1.0, "zoom_mid": 1.08,
                    "zoom_end": 1.0, "zoom_in_seconds": 1.0,
                    "zoom_out_seconds": 1.0},
         "anchor": {"frame": 810}, "anchor_end": {"frame": 840},
         "rationale": "a second landing to punch"},
        {"target_block_position": 7, "effect_type": "ken_burns",
         "params": {"zoom_start": 1.0, "zoom_end": 1.02},
         "rationale": reason},
    ]
    resolved = resolve_vfx(plan, _bridge_spine(7), frame_rate=30.0)
    assert [entry["effect_type"] for entry in resolved] == [
        "slow_zoom_in", "slow_zoom_out", "zoom_emphasis",
        "screen_shake", "cut_in", "zoom_emphasis", "slow_zoom_in"]
    punch = next(entry for entry in resolved
                 if entry["target_block_position"] == 3)
    assert punch["params"]["zoom_in_duration_frames"] == 30
    assert punch["params"]["zoom_release_offset_frames"] == 60
    assert punch["params"]["zoom_out_duration_frames"] == 30
    model = _model_fixture("drift")
    punch_interval = {"clock_id": "clock-project", "start": 0,
                      "end": 91}
    small_interval = {"clock_id": "clock-project", "start": 0,
                      "end": 5}
    model["clip_uses"] = [{
        "id": f"use-{i + 1}", "asset_id": "asset-checker",
        "source_interval": {"clock_id": "clock-src", "start": 0,
                            "end": 2},
        "timeline_interval": (punch_interval
                              if (i + 1) in (3, 6)
                              else small_interval),
        "entity_links": [], "role": "a_roll", "intent_id": None,
    } for i in range(7)]
    targets = {str(i + 1): f"use-{i + 1}" for i in range(7)}
    minted = mv.mint_move_effects(
        resolved_entries=resolved, model=model, targets=targets,
        raw_params_by_vfx={entry["vfx_id"]: entry["params"]
                           for entry in resolved})
    assert [effect["class_id"] for effect in minted] == [
        "move.slow_zoom_in", "move.slow_zoom_out",
        "move.zoom_emphasis", "move.screen_shake", "move.cut_in",
        "move.zoom_emphasis", "move.slow_zoom_in"]
    for effect in minted:
        assert effect["interval"] == next(
            use["timeline_interval"] for use in model["clip_uses"]
            if use["id"] == effect["target_id"])
        assert effect["keyframes"] == []
    small_comp = ec.CompSpec(name="Main", width=96, height=64,
                             frame_rate=10.0, duration_seconds=0.5)
    punch_comp = ec.CompSpec(name="Main", width=96, height=64,
                             frame_rate=10.0, duration_seconds=9.1)
    for effect in minted:
        solo = _model_fixture("drift")
        solo["clip_uses"] = [use for use in model["clip_uses"]
                             if use["id"] == effect["target_id"]]
        solo["effects"] = [effect]
        comp = (punch_comp
                if effect["class_id"] == "move.zoom_emphasis"
                else small_comp)
        projection = ec.project_to_request(
            solo, comp, _renders("drift"),
            project_id=f"ec-move-bridge-{effect['id']}")
        keyed = [op["path"] for op in
                 projection.request["operations"]
                 if op.get("op") == "keyframe"]
        assert keyed, effect["id"]
        want = ("transform/position"
                if effect["class_id"] == "move.screen_shake"
                else "transform/scale")
        assert want in set(keyed), effect["id"]
