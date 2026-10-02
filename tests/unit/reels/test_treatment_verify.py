"""A visual treatment must look at what it drew, and be able to undo it.

`library/tools/treatment_verify.py` builds the comp with and without the
treatment, evaluates its splines over the frames the timeline really
renders, and gates on the before/after difference (AGENTS.md 10.4).
The PowerCrop diagnosis behind it: docs/evidence/treatment_verify.md.
"""
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import pytest

from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.tv_power import (
    switch_off_frames,
    switch_on_frames,
    switch_shape,
)

POOL_FPS = 30.0
REEL_FPS = 24000 / 1001
WINDOW_SECONDS = (12.0, 15.0)
CLIP_DUR = 600
#: The source frame every build, verify and skill call below states.
#: The builders take no default frame, so each call states it - the
#: same numbers the removed defaults carried.
SOURCE_RES = (1080, 1920)


def applier_effects(base):
    """What apply_fusion_comps hands the builder: source-frame bounds."""
    effects = dict(base)
    effects["source_in_frame"] = round(WINDOW_SECONDS[0] * POOL_FPS)
    effects["source_out_frame"] = round(WINDOW_SECONDS[1] * POOL_FPS)
    return effects


def played_reel():
    return int(round((WINDOW_SECONDS[1] - WINDOW_SECONDS[0]) * REEL_FPS))


HEAD = {"tv_power_head": True, "tv_power_head_timing": switch_on_frames()}
TAIL = {"tv_power_tail": True, "tv_power_tail_timing": switch_off_frames()}


def test_reel_tail_draws_with_a_played_horizon():
    """The clamp anchors the animation inside what renders."""
    from library.tools import treatment_verify as tv

    verdict = tv.verify_treatment(
        applier_effects(TAIL), "tv_power_tail", CLIP_DUR,
        played_frames=played_reel(), source_res=SOURCE_RES)
    # Passes only with the played_frames clamp in build_effect_comp:
    # without it a 24000/1001 reel cut from 30 fps footage keys the tail
    # at source 72..90 while it plays 0..71, and 0 of 72 frames change.
    assert verdict["passed"] is True
    assert len(verdict["changed_frames"]) == 18
    assert verdict["changed_frames"] == list(range(54, 72))


def test_head_opens_and_holds_neutral():
    """The switch-on is exonerated as drawn: opens, then neutral."""
    from library.tools import treatment_verify as tv

    verdict = tv.verify_treatment(
        applier_effects(HEAD), "tv_power_head", CLIP_DUR,
        played_frames=90, source_res=SOURCE_RES)
    assert verdict["passed"] is True
    # The crop opens over 0..10 and the bloom gain settles by frame 18 -
    # the declared 18-frame animation, all inside its window.
    assert verdict["changed_frames"] == list(range(0, 18))
    assert verdict["window"] == [0, 18]
    # Frame 0 is FULLY BLACK, which is the captain's 2026-09-11 ruling:
    # the switch-on is the switch-off reversed, so it opens where the
    # switch-off closes - band shut to the line, gain at no signal.
    # (Between 2026-09-10 and 2026-09-11 this kept a fifth of the
    # picture at gain 2.2; that softening answered an opening sliver
    # the shape no longer opens on.)  A measurement, not a judgement
    # (AGENTS.md 10.5) - reported, never computed into the verdict.
    assert verdict["min_kept_fraction"] == pytest.approx(0.02)
    # The hottest frame is the dot, exactly as on the switch-off.
    assert verdict["max_gain"] == pytest.approx(2.5)


def test_window_gate_can_fail():
    """The deterministic half can fail: a leak outside the window."""
    from library.tools import treatment_verify as tv

    # The band's neutral is 1.0 (the whole frame shows); a frame where
    # it narrows is a frame the treatment drew on.
    curves = {"PowerBand1Height": [0.02 if f == 50 else 1.0
                                   for f in range(72)]}
    verdict = tv.check_window(curves, window=(54, 71), played=72)
    assert verdict["passed"] is False
    assert verdict["outside_window"] == [50]


def test_sampled_hold_after_is_a_frame_not_an_offset():
    """`sampled()`'s docstring said end_frame+N; the code keys the frame.

    That docstring caused one wrong diagnosis already (firstmate read it
    as a type confusion at effects.py:809). The contract is pinned here
    so the next reader meets the implementation, not the old prose.
    """
    from library.tools.fusion.nodes import BezierSpline

    spline = BezierSpline.sampled(
        "hold", start_frame=4, end_frame=10, easing="Linear",
        reverse=True, scale=0.49, hold_before=0.49, hold_after=71)
    frames = [k.frame for k in spline.keyframes]
    assert frames[0] == 0
    assert frames[-1] == 71
    assert abs(spline.keyframes[-1].value - 0.0) < 1e-9


def test_verify_and_undo_is_surgical():
    """Only the failing key goes: an over-long tail, a fitting head.

    A 72-frame tail animation on 72 played frames has no room past its
    own hold key (room 71 < total 72) and is refused; the 18-frame head
    on the same clip fits and stays. The undo removes exactly the
    failed key and the rebuilt comp carries the head alone.
    """
    from library.tools import treatment_verify as tv

    effects = applier_effects({
        "tv_power_head": True,
        "tv_power_head_timing": switch_on_frames(),
        "tv_power_tail": True,
        "tv_power_tail_timing": {**switch_off_frames(),
                                 "collapse_frames": 60},
    })
    final, rows = tv.verify_and_undo(effects, CLIP_DUR,
                                     played_frames=played_reel(), source_res=SOURCE_RES)
    by_key = {r["treatment"]: r for r in rows}
    assert by_key["tv_power_head"]["passed"] is True
    assert by_key["tv_power_head"]["undone"] is False
    assert by_key["tv_power_tail"]["passed"] is False
    assert by_key["tv_power_tail"]["failure"] == "never_settles"
    assert by_key["tv_power_tail"]["undone"] is True
    assert "tv_power_head" in final
    assert "tv_power_tail" not in final
    assert "tv_power_tail_timing" not in final
    # The undo restores the original frames: rebuild-from-removed is
    # byte-identical to build-from-absent.
    assert (build_effect_comp(final, CLIP_DUR,
                              played_frames=played_reel(), source_res=SOURCE_RES)
            == build_effect_comp(
                tv.remove_treatment(effects, "tv_power_tail"),
                CLIP_DUR, played_frames=played_reel(), source_res=SOURCE_RES))


def test_verify_and_undo_drops_short_clip_treatments():
    """Neither half fits 10 frames: both go, the picture stays whole."""
    from library.tools import treatment_verify as tv

    effects = {"tv_power_head": True,
               "tv_power_head_timing": switch_on_frames(),
               "tv_power_tail": True,
               "tv_power_tail_timing": switch_off_frames(),
               "source_in_frame": 0, "source_out_frame": 9}
    final, rows = tv.verify_and_undo(effects, CLIP_DUR,
                                     played_frames=10, source_res=SOURCE_RES)
    assert all(r["undone"] for r in rows)
    assert {r["failure"] for r in rows} == {"never_settles"}
    assert final == {"source_in_frame": 0, "source_out_frame": 9}
    assert "PowerCrop" not in build_effect_comp(
        final, CLIP_DUR, played_frames=10, source_res=SOURCE_RES)


def test_skill_run_writes_a_receipt_and_refuses_an_unknown_key(tmp_path):
    """A self-reported check writes no receipt; this one does - and an
    unchecked treatment must not read as a checked one."""
    from library.skills.verify_treatment.skill import run
    from library.tools import pipeline_skills
    from library.tools.treatment_verify import UnknownTreatment

    record = run(dict(HEAD), "tv_power_head", CLIP_DUR,
                 str(tmp_path), "plan_vfx", played_frames=90, source_res=SOURCE_RES)
    assert record["passed"] is True
    receipts = pipeline_skills.read_receipts(str(tmp_path), "plan_vfx")
    assert "verify_treatment" in receipts
    assert receipts["verify_treatment"]["result"]["passed"] is True

    with pytest.raises(UnknownTreatment):
        run({}, "slow_zoom", 600, "/nonexistent", "plan_vfx",
            source_res=SOURCE_RES)


def _reel_tail_manifest():
    """First-and-last-clip tail the reels ship: 12-15 s of a 600f src."""
    return {
        "tracks": {"V1": {"clips": [
            {"source_file": "a_roll.mov", "label": "clip_0",
             "source_in": 12.0, "source_out": 15.0},
        ]}},
        "fusion_effects": {"per_clip": {
            "clip_0": {"tv_power_tail": True,
                       "tv_power_tail_timing": switch_off_frames()},
        }},
    }


def _mock_resolve(monkeypatch, played):
    import library.tools.execution.apply_fusion_comps as afc

    class MockFusionTool:
        def __init__(self, comp, name, reg_id):
            self.comp = comp
            self.name = name
            self.reg_id = reg_id

        def GetAttrs(self):
            return {"TOOLS_Name": self.name, "TOOLS_RegID": self.reg_id}

        def GetInput(self, name, frame=0):
            return 1.0

        def Delete(self):
            self.comp.tools.pop(self.name, None)

    class MockFusionComp:
        def __init__(self):
            self.locked = False
            self.tools = {}
            for name, reg_id in (
                    ("MediaIn1", "MediaIn"), ("MediaOut1", "MediaOut"),
                    ("Transform1", "Transform"), ("Merge1", "Merge")):
                self.tools[name] = MockFusionTool(self, name, reg_id)

        def Lock(self):
            self.locked = True

        def Unlock(self):
            self.locked = False

        def AddTool(self, reg_id):
            assert self.locked, "Fusion node creation must hold comp.Lock()"
            name = f"Probe{len(self.tools)}"
            tool = MockFusionTool(self, name, reg_id)
            self.tools[name] = tool
            return tool

        def GetToolList(self):
            return dict(self.tools)

        def FindTool(self, name):
            return self.tools.get(name)

    class MockClip:
        def __init__(self):
            self.comps = {}

        def GetStart(self): return 0
        def GetEnd(self): return played
        def GetDuration(self): return played
        def GetMediaPoolItem(self): return MockPool()
        def GetFusionCompNameList(self): return list(self.comps)
        def GetFusionCompByName(self, name): return self.comps.get(name)
        def ImportFusionComp(self, path):
            self.comps["Imported"] = MockFusionComp()
            return True
        def DeleteFusionCompByName(self, name): self.comps.pop(name, None)

    class MockPool:
        def GetClipProperty(self, prop):
            if prop == "File Path":
                return "a_roll.mov"
            if prop == "Frames":
                return "600"
            # A real MediaPoolItem states its stored frame; the applier
            # refuses a comp where Resolve will not state one, so the
            # mock states one like production does.
            if prop == "Resolution":
                return "1080x1920"
            return None

    class MockTimeline:
        def GetSetting(self, name): return "30"
        def GetItemListInTrack(self, track_type, index):
            return [MockClip()] if index == 1 else []

    class MockProject:
        def GetCurrentTimeline(self): return MockTimeline()

    class MockPM:
        def GetCurrentProject(self): return MockProject()

    class MockResolve:
        def GetProjectManager(self): return MockPM()
        def OpenPage(self, page):
            assert page == "fusion"
            return True

    monkeypatch.setattr(afc.dvr, "scriptapp", lambda x: MockResolve())
    return afc


def test_applier_verifies_the_tail_on_the_path_that_ships(
        monkeypatch, tmp_path):
    """72 played frames: the clamped tail draws, receipted, nothing
    undone. 10: it cannot fit, so the applier drops it - the banked comp
    carries no PowerBand and the receipt names the key and the reason."""
    import glob

    from library.tools import pipeline_skills

    for played, undone in ((72, 0), (10, 1)):
        project = tmp_path / str(played)
        project.mkdir()
        afc = _mock_resolve(monkeypatch, played)
        assert afc.apply_fusion_comps(
            _reel_tail_manifest(), str(project),
            step_id="build_reels") is True

        receipts = pipeline_skills.read_receipts(str(project), "build_reels")
        record = receipts["verify_treatment"]["result"]
        assert record["treatments_undone"] == undone
        row = record["rows"][0]
        assert row["treatment"] == "tv_power_tail"
        assert row["undone"] is bool(undone)
        assert row["passed"] is (not undone)
        if undone:
            assert row["failure"] == "never_settles"

        banked = glob.glob(str(
            project / "assets" / "fusion_presets" / "clip_0_*.comp"))
        assert len(banked) == 1
        text = open(banked[0]).read()
        if undone:
            assert "PowerBand" not in text
        else:
            assert "PowerBandMask1 = RectangleMask" in text


def test_head_samples_decode_the_opening_not_the_number():
    """The evidence is decoded off the comp: kept picture and gain at
    the frames the captain judges (0/2/5/10/18).  The switch-on opens
    on black, rises through the dot, and settles on the picture at 18 -
    the switch-off's own states, read backwards."""
    from library.tools import treatment_verify as tv

    assert tv.HEAD_JUDGE_FRAMES == (0, 2, 5, 10, 18)
    rows = {r["frame"]: r for r in tv.sample_head_frames(
        dict(HEAD), CLIP_DUR, source_res=SOURCE_RES)}
    # Black: the band is shut to the line and there is no signal on it.
    assert rows[0]["kept_fraction"] == pytest.approx(0.02)
    assert rows[0]["gain"] == pytest.approx(0.0)
    # The glow rises out of black over the decay phase...
    assert rows[2]["gain"] > rows[0]["gain"]
    assert rows[5]["gain"] > rows[2]["gain"]
    # ... the band is still shut while the dot is opening to a line ...
    assert rows[5]["kept_fraction"] == pytest.approx(0.02)
    assert rows[10]["kept_fraction"] == pytest.approx(0.02)
    # ... and by 18 the picture is whole and neutral.
    assert rows[18] == {"frame": 18, "kept_fraction": 1.0, "gain": 1.0}


def test_a_retimed_switch_decodes_differently_both_ways():
    """A re-timing is visible in the decoded table, and it moves BOTH
    directions - which is the whole point of one shape.

    Stretching the decay (black -> dot) delays the glow without moving
    where the band opens; the switch-off sampled over the same frames
    shows the mirror of it.  Either way frame 18 of the default is
    neutral picture - the settle question, answered rather than
    asserted.
    """
    from library.tools import treatment_verify as tv

    default = dict(HEAD)
    slower = {"tv_power_head": True, "tv_power_head_timing": {
        **switch_shape(), "decay_frames": 15}}

    def by_frame(effects):
        return {r["frame"]: r
                for r in tv.sample_head_frames(effects, CLIP_DUR,
                                               source_res=SOURCE_RES)}

    base, slow = by_frame(default), by_frame(slower)
    # Both open on fully black - the shape's first state, either timing.
    assert base[0]["gain"] == pytest.approx(0.0)
    assert slow[0]["gain"] == pytest.approx(0.0)
    assert base[0]["kept_fraction"] == pytest.approx(0.02)
    assert slow[0]["kept_fraction"] == pytest.approx(0.02)
    # The longer decay is DIMMER at the same frame: it has further to
    # climb to the dot.
    assert slow[5]["gain"] < base[5]["gain"]
    # The default settles at 18; the 24-frame one has not, and says so.
    assert base[18] == {"frame": 18, "kept_fraction": 1.0, "gain": 1.0}
    assert slow[18]["kept_fraction"] < 1.0
    # ONE shape: the re-timing reaches the switch-off in the same
    # breath, with the same total.
    from library.tools.tv_power import switch_total
    assert switch_total(slower["tv_power_head_timing"]) == 24
    assert tv.treatment_total("tv_power_tail", {
        "tv_power_tail": True,
        "tv_power_tail_timing": slower["tv_power_head_timing"]}) == 24


# --------------------------------------------------------------------------
# From test_verify_treatment_knows_drift.py
#
# 4.03's mandatory verify_treatment gate verifies what 4.03 plans (drift,
# switch animation) and needs no receipt for a plan with nothing it can
# verify. A gate that fails correct output is no coverage (AGENTS.md 10.4).
# Finding 5 (scout run B1): docs/evidence/treatment_verify.md.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _manifest():
    import json
    with open(REPO / "library" / "steps" / "step_4_03_plan_vfx"
              / "manifest.json", encoding="utf-8") as f:
        return json.load(f)


def test_the_gate_verifies_the_drift_403_plans(tmp_path):
    """The B1 failure: slow_zoom_in raised UnknownTreatment in the skill."""
    from library.skills.verify_treatment.skill import run
    from library.tools import pipeline_skills

    record = run({"zoom_start": 1.0, "zoom_end": 1.1,
                  "source_in_frame": 0, "source_out_frame": 599},
                 "slow_zoom_in", CLIP_DUR,
                 str(tmp_path), "step_4_03_plan_vfx",
                 played_frames=300, source_res=list(SOURCE_RES))
    assert record["passed"] is True
    assert record["verdict"]["treatment"] == "drift"
    receipts = pipeline_skills.read_receipts(str(tmp_path),
                                             "step_4_03_plan_vfx")
    assert "verify_treatment" in receipts


def test_a_plan_with_nothing_verifiable_needs_no_receipt(tmp_path):
    """Even an empty plan failed the gate on B1; stabilize/speed/
    screen_shake have no plan-time verifier, so the gate reports them
    openly rather than failing the run."""
    from library.tools import pipeline_skills

    manifest = _manifest()
    assert "verify_treatment" in pipeline_skills.gating_skills(
        manifest, "step_4_03_plan_vfx")
    for plan in ([], [
            {"target_block_position": 1, "effect_type": "stabilize",
             "params": {}, "rationale": "handheld"},
            {"target_block_position": 2, "effect_type": "speed_ramp",
             "params": {"segments": [{"percent": 50.0}]},
             "rationale": "ramp in"}]):
        # No receipt on disk: must not raise.
        assert pipeline_skills.assert_gating_skills_ran(
            "step_4_03_plan_vfx", manifest, str(tmp_path),
            llm_output={"vfx_creative": plan}) == {}


def test_a_planned_switch_animation_still_needs_its_receipt(tmp_path):
    """Scoping the gate must not unscope it: a tv_power plan with no
    receipt still fails."""
    import pytest

    from library.tools import pipeline_skills

    manifest = _manifest()
    with pytest.raises(pipeline_skills.GatingSkillSkipped):
        pipeline_skills.assert_gating_skills_ran(
            "step_4_03_plan_vfx", manifest, str(tmp_path),
            llm_output={"vfx_creative": [
                {"target_block_position": 1,
                 "effect_type": "tv_power_head",
                 "params": {}, "rationale": "open"},
            ]})
