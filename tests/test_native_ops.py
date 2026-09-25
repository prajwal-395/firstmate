"""Native Resolve operations in the plan (fidelity rung 3b).

PR 1376 measured which Resolve 21.1 operations really work
(`TimelineItem.SetSpeed`, `TimelineItem.AddTransition`, each judged by
read-back). This rung makes them reachable from the plan: `speed_ramp`
and `freeze_frame` in `plan_vfx`, the granted transition names in
`plan_transitions`, routed through the 0.6.0 verbs during the timeline
build. A name 1376 measured as refused is refused by name - never a
silently downgraded hard cut.
"""
import copy
import json

import pytest

from library.tools import native_ops
from library.tools.native_ops import (
    NativeSpeedRefused,
    NativeTransitionRefused,
    granted_categories,
    native_canonical,
    refused_native_canonical,
    resolve_transition_name,
)
from library.tools.ren_refusal import RenRefusal
from library.tools.transition_selector import select_transition
from library.tools.transition_vocabulary import (
    NATIVE_TYPES,
    is_native,
    known_type,
    native_canonical_type,
    refused_native_reason,
    route_of,
)


# ── The vocabulary ───────────────────────────────────────────────────

def test_the_vocabulary_is_well_formed():
    native_ops.assert_vocabulary_is_well_formed()


def test_granted_and_refused_sets_are_disjoint():
    assert (set(native_ops.NATIVE_TRANSITIONS)
            & set(native_ops.REFUSED_NATIVE_TRANSITIONS)) == set()


def test_granted_names_resolve_and_refused_ones_do_not():
    assert native_canonical("cross_dissolve") == "cross_dissolve"
    assert native_canonical("Dissolve") == "cross_dissolve"
    assert native_canonical("slide") == "slide"
    assert native_canonical("Smooth Cut") == "smooth_cut"
    assert native_canonical("spin") == "spin"
    assert native_canonical("whip_pan") is None
    assert native_canonical("utter_nonsense") is None


def test_refused_names_resolve_and_granted_ones_do_not():
    assert refused_native_canonical("whip_pan") == "whip_pan"
    assert refused_native_canonical("Whip") == "whip_pan"
    assert refused_native_canonical("dip") == "dip"
    assert refused_native_canonical("push") == "push"
    assert refused_native_canonical("Blur Dissolve") == "blur_dissolve"
    assert refused_native_canonical("cross_dissolve") is None


def test_a_refusal_carries_what_why_and_fix():
    refused = native_ops.refuse_native_transition("whip_pan")
    assert isinstance(refused, RenRefusal)
    assert "whip_pan" in refused.what.lower() or "Whip Pan" in refused.what
    assert refused.why.strip()
    assert refused.fix.strip()


def test_granted_categories_are_the_measured_ones():
    assert granted_categories("cross_dissolve") == ("simple", "fusion")
    assert granted_categories("slide") == ("simple",)
    assert granted_categories("smooth_cut") == ("simple",)
    assert granted_categories("spin") == ("fusion",)
    assert resolve_transition_name("cross_dissolve") == "Cross Dissolve"


def test_the_vocabulary_routes_natives_to_resolve():
    assert route_of("cross_dissolve") == "native_resolve"
    assert route_of("dissolve") == "native_resolve"
    assert is_native("slide")
    assert known_type("spin") == "spin"
    assert not is_native("fade_to_black")
    assert refused_native_reason("whip_pan").strip()
    assert refused_native_reason("cross_dissolve") == ""


def test_native_types_are_not_fusion_plannable():
    """A native type reaching a Fusion builder is a misroute, so the
    per-clip route must keep answering None for it."""
    from library.tools.transition_vocabulary import canonical_type
    assert canonical_type("cross_dissolve") is None
    assert native_canonical_type("cross_dissolve") == "cross_dissolve"


# ── plan_vfx admits speed ops ────────────────────────────────────────

def _spine(*positions):
    blocks = []
    for i, (position, clip_id) in enumerate(positions):
        blocks.append({
            "position": position,
            "block_type": "speech",
            "clip_id": clip_id,
            "timeline_start": float(i * 5),
            "timeline_end": float(i * 5 + 5),
            "word_timestamps": [],
            "alignment_method": "whisperx",
        })
    return {"structure": blocks, "total_estimated_duration_seconds": 20.0}


def test_a_single_step_ramp_resolves_whole_block():
    from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
    # One step spanning the whole block matches the one item the
    # block places as. Multi-step ramps subdividing one block are
    # refused (finding 35) - nothing blades.
    (entry,) = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "speed_ramp",
          "params": {"segments": [{"percent": 50}]},
          "rationale": "slow the whole beat"}],
        _spine((1, "clip_a")), 30.0, dropped=[])
    assert entry["route"] == "native_resolve"
    (only,) = entry["params"]["segments"]
    assert only["percent"] == 50.0
    assert (only["timeline_start"], only["timeline_end"]) == (0.0, 5.0)


def test_a_ramp_subdividing_one_block_is_dropped_with_its_reason():
    """Finding 35: a resolved ramp whose steps subdivide the one
    block (B8: 24.400-25.057 s and 25.057-25.714 s inside the b-roll
    block) failed the whole build with "no timeline item spans ...
    - nothing was written", because nothing blades the item. The
    ONE entry is refused here, with its reason, instead."""
    from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
    dropped = []
    assert resolve_vfx(
        [{"target_block_position": 1, "effect_type": "speed_ramp",
          "params": {"segments": [{"percent": 200},
                                  {"percent": 50}]},
          "rationale": "montage ramp"}],
        _spine((1, "clip_a")), 30.0, dropped=dropped) == []
    assert dropped[0].reason == "speed_span_subdivides_block"
    # The steps still divide the span equally - the drop names them.
    assert "0.000-2.500" in dropped[0].detail
    assert "blade" in dropped[0].detail


def test_a_freeze_on_a_word_span_is_dropped_with_its_reason():
    """A freeze spans one op on one item: a sub-block word span
    matches no placed item either, so it is refused the same way."""
    from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
    dropped = []
    assert resolve_vfx(
        [{"target_block_position": 1, "effect_type": "freeze_frame",
          "anchor": {"frame": 30}, "anchor_end": {"frame": 60},
          "rationale": "hold the word"}],
        _spine((1, "clip_a")), 30.0, dropped=dropped) == []
    assert dropped[0].reason == "speed_span_subdivides_block"


def test_a_freeze_resolves_with_no_params():
    from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
    (entry,) = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "freeze_frame",
          "rationale": "hold the look"}],
        _spine((1, "clip_a")), 30.0, dropped=[])
    assert entry["route"] == "native_resolve"
    assert entry["effect_type"] == "freeze_frame"
    assert (entry["timeline_start"], entry["timeline_end"]) == (0.0, 5.0)


def test_a_ramp_without_segments_is_dropped_with_a_reason():
    from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
    dropped = []
    assert resolve_vfx(
        [{"target_block_position": 1, "effect_type": "speed_ramp",
          "params": {}}],
        _spine((1, "clip_a")), 30.0, dropped=dropped) == []
    assert dropped[0].reason == "not_a_speed_step"


def test_a_zero_percent_step_is_dropped_not_frozen():
    """A freeze is spelled `freeze_frame`, never a 0% step."""
    from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
    dropped = []
    assert resolve_vfx(
        [{"target_block_position": 1, "effect_type": "speed_ramp",
          "params": {"segments": [100, 0]}}],
        _spine((1, "clip_a")), 30.0, dropped=dropped) == []
    assert dropped[0].reason == "not_a_speed_step"
    assert "freeze_frame" in dropped[0].detail


def test_a_curve_param_refuses_rather_than_rounding():
    """Rounding a curve to constants would invent pacing the plan
    declined to step out - the model re-plans with `segments`."""
    from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
    with pytest.raises(NativeSpeedRefused, match="stepped"):
        resolve_vfx(
            [{"target_block_position": 1, "effect_type": "speed_ramp",
              "params": {"segments": [50, 150], "curve": "ease-in-out"}}],
            _spine((1, "clip_a")), 30.0, dropped=[])


# ── The applicator, against fake items ───────────────────────────────

class _FakeItem:
    def __init__(self, name, start, end, speed=100.0, no_get_speed=False):
        self._name = name
        self._start = start
        self._end = end
        self._speed = speed
        self._no_get_speed = no_get_speed
        self.writes = []

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetSpeed(self):
        if self._no_get_speed:
            raise AttributeError("no GetSpeed")
        return {"Percentage": self._speed}

    def SetSpeed(self, opts):
        self.writes.append(dict(opts))
        self._speed = float(opts["Percentage"])
        return True

    def AddTransition(self, payload):
        if getattr(self, "_transition refused", False):
            return None
        tr = _FakeItem(payload["type"], self._start, self._start + 10)
        tr._payload = payload
        return tr


class _FakeTimeline:
    def __init__(self, v1=(), v2=()):
        self._tracks = {1: list(v1), 2: list(v2)}

    def GetItemListInTrack(self, track_type, index):
        return list(self._tracks.get(index, []))


def test_a_speed_step_is_applied_and_read_back():
    from library.tools import native_ops_apply as apply
    item = _FakeItem("clip_a", 0, 150)
    report = apply.apply_native_speed_ops(
        _FakeTimeline(v1=[item]),
        [{"op_id": "speed_001", "effect_type": "speed_ramp",
          "timeline_start": 0.0, "timeline_end": 5.0,
          "segments": [{"percent": 50.0, "timeline_start": 0.0,
                        "timeline_end": 2.5},
                       {"percent": 150.0, "timeline_start": 2.5,
                        "timeline_end": 5.0}]}],
        fps=30.0)
    # Both steps address the one item's sub-spans: blading one item
    # into steps is unmeasured, so each step refuses loudly.
    assert report["applied"] == []
    assert len(report["failed"]) == 2
    assert "blade" in report["failed"][0]["fix"]


def test_one_step_per_item_applies():
    from library.tools import native_ops_apply as apply
    first = _FakeItem("seg_a", 0, 75)
    second = _FakeItem("seg_b", 75, 150)
    report = apply.apply_native_speed_ops(
        _FakeTimeline(v1=[first, second]),
        [{"op_id": "speed_001", "effect_type": "speed_ramp",
          "timeline_start": 0.0, "timeline_end": 5.0,
          "segments": [{"percent": 50.0, "timeline_start": 0.0,
                        "timeline_end": 2.5},
                       {"percent": 150.0, "timeline_start": 2.5,
                        "timeline_end": 5.0}]}],
        fps=30.0)
    assert [r["percent"] for r in report["applied"]] == [50.0, 150.0]
    assert report["failed"] == []
    assert first.writes == [{"Percentage": 50.0, "RippleTimeline": False}]


def test_a_lying_read_back_is_not_claimed():
    from library.tools import native_ops_apply as apply

    class _Liar(_FakeItem):
        def GetSpeed(self):
            return {"Percentage": 100.0}

    item = _Liar("clip_a", 0, 150)
    report = apply.apply_native_speed_ops(
        _FakeTimeline(v1=[item]),
        [{"op_id": "speed_001", "effect_type": "speed_ramp",
          "timeline_start": 0.0, "timeline_end": 5.0,
          "segments": [{"percent": 40.0, "timeline_start": 0.0,
                        "timeline_end": 5.0}]}],
        fps=30.0)
    assert report["applied"] == []
    assert "re-reads" in report["failed"][0]["what"]


def test_a_freeze_applies_on_a_fresh_item():
    from library.tools import native_ops_apply as apply
    item = _FakeItem("clip_a", 0, 150)
    report = apply.apply_native_speed_ops(
        _FakeTimeline(v1=[item]),
        [{"op_id": "speed_001", "effect_type": "freeze_frame",
          "timeline_start": 0.0, "timeline_end": 5.0}],
        fps=30.0)
    assert len(report["applied"]) == 1
    assert report["applied"][0]["percent"] == 0.0
    assert report["failed"] == []


def test_a_freeze_after_a_retime_in_the_same_build_refuses():
    """The measured case: the write answers True while `GetSpeed`
    re-reads 100.0, so no read-back could judge it. Both ops ride one
    applicator call, as they do in the build."""
    from library.tools import native_ops_apply as apply
    item = _FakeItem("clip_a", 0, 150)
    report = apply.apply_native_speed_ops(
        _FakeTimeline(v1=[item]),
        [{"op_id": "speed_001", "effect_type": "speed_ramp",
          "timeline_start": 0.0, "timeline_end": 5.0,
          "segments": [{"percent": 40.0, "timeline_start": 0.0,
                        "timeline_end": 5.0}]},
         {"op_id": "speed_002", "effect_type": "freeze_frame",
          "timeline_start": 0.0, "timeline_end": 5.0}],
        fps=30.0)
    assert len(report["applied"]) == 1
    assert report["applied"][0]["op_id"] == "speed_001"
    assert len(report["failed"]) == 1
    assert report["failed"][0]["op_id"] == "speed_002"
    assert "retime" in report["failed"][0]["what"]


def test_a_freeze_on_an_already_slowed_item_refuses():
    from library.tools import native_ops_apply as apply
    item = _FakeItem("clip_a", 0, 150, speed=40.0)
    report = apply.apply_native_speed_ops(
        _FakeTimeline(v1=[item]),
        [{"op_id": "speed_001", "effect_type": "freeze_frame",
          "timeline_start": 0.0, "timeline_end": 5.0}],
        fps=30.0)
    assert report["applied"] == []
    assert "40" in report["failed"][0]["what"]


def test_a_native_transition_is_placed_and_its_span_reads():
    from library.tools import native_ops_apply as apply
    outgoing = _FakeItem("clip_a", 0, 150)
    incoming = _FakeItem("clip_b", 150, 300)
    report = apply.apply_native_transitions(
        _FakeTimeline(), [outgoing, incoming],
        [{"transition_id": "trans_001",
          "resolve_name": "Cross Dissolve", "category": "simple",
          "after_clip": 0, "duration_frames": 12}],
        fps=30.0)
    assert len(report["applied"]) == 1
    row = report["applied"][0]
    assert row["type"] == "Cross Dissolve"
    assert row["verified"].startswith("returned a transition item")
    assert report["failed"] == []


def test_an_empty_transition_answer_fails_naming_type_and_category():
    from library.tools import native_ops_apply as apply

    class _Refusing(_FakeItem):
        def AddTransition(self, payload):
            return None

    report = apply.apply_native_transitions(
        _FakeTimeline(), [_FakeItem("a", 0, 150), _Refusing("b", 150, 300)],
        [{"transition_id": "trans_001", "resolve_name": "Spin",
          "category": "fusion", "after_clip": 0, "duration_frames": 10}],
        fps=30.0)
    assert report["applied"] == []
    assert "Spin" in report["failed"][0]["what"]
    assert "fusion" in report["failed"][0]["what"]


def test_a_transition_past_the_last_clip_fails():
    from library.tools import native_ops_apply as apply
    report = apply.apply_native_transitions(
        _FakeTimeline(), [_FakeItem("a", 0, 150)],
        [{"transition_id": "trans_001", "resolve_name": "Cross Dissolve",
          "category": "simple", "after_clip": 0, "duration_frames": 12}],
        fps=30.0)
    assert report["applied"] == []
    assert "no V1 cut" in report["failed"][0]["what"]


# ── compile_manifest carries native ops to the build ────────────────

def _compile_with(project, plan_transitions=None, plan_vfx=None):
    from unittest.mock import patch

    from library.steps.step_5_04_compile_manifest import step

    project_dir, layout, outputs, sfx_file = project
    outputs = copy.deepcopy(outputs)
    if plan_transitions is not None:
        outputs["plan_transitions"] = {"transition_spec": plan_transitions}
    if plan_vfx is not None:
        outputs["plan_vfx"] = plan_vfx
    layout.pipeline_data_path.write_text(
        json.dumps({"step_outputs": outputs,
                    "project_folder": str(project_dir)}),
        encoding="utf-8")
    with patch.object(step, "load_sfx_catalog", return_value=[
            {"sfx_id": "whoosh.wav", "path": sfx_file,
             "duration_seconds": 0.5, "transient_offset_sec": 0.0}]):
        return step.compile_manifest(str(layout.output_root))


@pytest.fixture
def project(tmp_path):
    """Two abutting V1 clips, under tmp_path only."""
    import tests.test_compile_manifest_without_the_decoration as base
    from library.tools import music_audit_trail as audit
    from library.tools.project_layout import ProjectLayout

    media = tmp_path / "media"
    media.mkdir()
    names = {}
    for name in ("a_roll.mov", "b_roll.mov", "bed.wav", "whoosh.wav",
                 "sub_seg_000.mov"):
        path = media / name
        path.write_bytes(b"\x00" * 64)
        names[name] = str(path)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    layout = ProjectLayout(str(project_dir))
    layout.ensure()
    outputs = base._step_outputs(
        names["a_roll.mov"], names["b_roll.mov"], names["bed.wav"],
        names["whoosh.wav"], names["sub_seg_000.mov"])
    audit.write_audit_trail(
        str(project_dir), outputs["music_selection"]["music_selection"])
    return project_dir, layout, outputs, names["whoosh.wav"]


def test_a_native_transition_reaches_the_build_not_the_comp(project):
    manifest = _compile_with(project, plan_transitions=[
        {"transition_id": "trans_001", "transition_type": "cross_dissolve",
         "cut_point_timeline": 2.285, "duration": 0.4, "after_clip": 0}])
    (native,) = manifest["native_transitions"]
    assert native["transition_type"] == "cross_dissolve"
    assert native["resolve_name"] == "Cross Dissolve"
    assert native["category"] == "simple"
    assert native["after_clip"] == 0
    assert native["duration_frames"] == 12
    # Nothing drawn reaches the comp engine for it.
    assert manifest["fusion_effects"]["transitions"] == []
    assert manifest["transitions_downgraded"] == []


def test_a_measured_refusal_at_compile_refuses_by_name(project):
    # Matched on the shared `RenRefusal` base: the step imports its
    # tools as `tools.*` (its sys.path shim) while this file reads
    # `library.tools.*`, so the subclass object is doubled - the base
    # and the rendered text are not.
    with pytest.raises(RenRefusal, match="Whip Pan"):
        _compile_with(project, plan_transitions=[
            {"transition_id": "trans_001", "transition_type": "whip_pan",
             "cut_point_timeline": 2.285, "duration": 0.4,
             "after_clip": 0}])


def test_native_speed_ops_reach_the_build_not_the_comp(project):
    manifest = _compile_with(project, plan_vfx={"enhancement_spec": {
        "visual_effects": [
            {"target_block_position": 1, "effect_type": "speed_ramp",
             "timeline_start": 0.0, "timeline_end": 2.285,
             "params": {"segments": [
                 {"percent": 50.0, "timeline_start": 0.0,
                  "timeline_end": 2.285}]},
             "rationale": "ramp in", "route": "native_resolve"},
            {"target_block_position": 2, "effect_type": "freeze_frame",
             "timeline_start": 2.285, "timeline_end": 5.418,
             "params": {}, "rationale": "hold",
             "route": "native_resolve"},
        ],
        "planning_basis": {"basis": "planned", "proposed": 2,
                           "resolved": 2, "dropped": []}}})
    assert manifest["fusion_effects"]["per_clip"] == {} or all(
        v.get("_preset") not in ("speed_ramp", "freeze_frame")
        for v in manifest["fusion_effects"]["per_clip"].values()), \
        "native speed ops must not reach the comp engine"
    (ramp, freeze) = manifest["native_speed_ops"]
    assert ramp["effect_type"] == "speed_ramp"
    assert ramp["segments"][0]["percent"] == 50.0
    assert freeze["effect_type"] == "freeze_frame"


def test_a_native_transition_with_no_hold_ships_a_hard_cut(project):
    manifest = _compile_with(project, plan_transitions=[
        {"transition_id": "trans_001", "transition_type": "cross_dissolve",
         "cut_point_timeline": 2.285}])
    assert manifest["transitions"][0]["transition_type"] == "hard_cut"
    assert manifest["native_transitions"] == []
    assert len(manifest["transitions_downgraded"]) == 1


# ── The selector's native outcomes ───────────────────────────────────

def test_a_native_request_wins_over_the_heuristic():
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="spin")
    assert res["type"] == "spin"


def test_a_brand_that_forbids_a_native_type_gets_a_hard_cut():
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_types": ["hard_cut"]}, {},
        requested_type="cross_dissolve")
    assert res["type"] == "hard_cut"
    assert res["downgrade_reason"]
