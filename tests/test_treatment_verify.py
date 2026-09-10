"""A visual treatment must look at what it drew, and be able to undo it.

Proven on PowerCrop (the tv_power Crop): the captain found by eye that
the powercrop nodes mis-frame the a-roll and that removing them makes
things look right. The pipeline never looked - `apply_fusion_comps` and
`comp_builder` contain no visual verification of any kind.

`library/tools/treatment_verify.py` is the always-on deterministic
half: it builds the comp with and without the treatment, evaluates the
treatment's own splines over the frames the timeline really renders,
and reports the before/after difference. The measurable part gates
(AGENTS.md 10.4); the model's reading is recorded, never enforced.

Frame-measured diagnosis this pins (see /tmp/powercrop_sim, since
reproduced here without Resolve - the comp builder is Resolve-free by
design):
- the switch-ON head opens fully and holds neutral: EXONERATED as drawn.
- the switch-OFF tail on a 24000/1001 reel timeline cut from 30 fps pool
  footage keys its animation at source frames 72..90 while the timeline
  plays 0..71: every spline flat-neutral over everything rendered, 0 of
  72 frames changed. The reel never turns off and nothing says so.
- a head on a clip shorter than its own animation never reaches
  neutral: the whole clip plays collapsed.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest

from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.tv_power import switch_off_frames, switch_on_frames

POOL_FPS = 30.0
REEL_FPS = 24000 / 1001
WINDOW_SECONDS = (12.0, 15.0)
CLIP_DUR = 600


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


def test_legacy_tail_without_horizon_draws_nothing_on_reel():
    """The shipped defect, characterized: keyed past everything rendered.

    Without a played horizon the tail keys its animation at source
    frames 72..90 while a 24000/1001 reel timeline cut from 30 fps pool
    footage plays 0..71. Flat extrapolation holds neutral over all of
    it: 0 of 72 frames change and nothing anywhere says so.
    """
    from library.tools.fusion.transition_frames import (
        parse_splines, value_at)

    comp = build_effect_comp(applier_effects(TAIL), CLIP_DUR)
    rows = parse_splines(comp)
    assert rows, "the tail arms nodes - they just never play"
    for name, keys in rows.items():
        if "Crop" in name:
            neutral = 0.0
        elif "Size" in name:
            neutral = 1.0
        elif "Gain" in name:
            neutral = 1.0
        else:
            continue
        assert all(abs(value_at(keys, f) - neutral) < 1e-9
                   for f in range(played_reel())), name


def test_reel_tail_draws_with_a_played_horizon():
    """The clamp anchors the animation inside what renders."""
    from library.tools import treatment_verify as tv

    verdict = tv.verify_treatment(
        applier_effects(TAIL), "tv_power_tail", CLIP_DUR,
        played_frames=played_reel())
    # NOTE: this passes only with the played_frames clamp in
    # build_effect_comp; without it the tail keys at 72..90 and the
    # verdict above (drew_nothing) is what comes back.
    assert verdict["passed"] is True
    assert len(verdict["changed_frames"]) == 18
    assert verdict["changed_frames"] == list(range(54, 72))


def test_head_opens_and_holds_neutral():
    """The switch-on is exonerated as drawn: opens, then neutral."""
    from library.tools import treatment_verify as tv

    verdict = tv.verify_treatment(
        applier_effects(HEAD), "tv_power_head", CLIP_DUR,
        played_frames=90)
    assert verdict["passed"] is True
    # The crop opens over 0..10 and the bloom gain settles by frame 18 -
    # the declared 18-frame animation, all inside its window.
    assert verdict["changed_frames"] == list(range(0, 18))
    assert verdict["window"] == [0, 18]
    # The softened strike keeps a fifth of the picture on frame 0, not
    # the old 2% sliver (captain's 2026-09-10 ruling).  A measurement,
    # not a judgement (AGENTS.md 10.5) - reported, never computed into
    # the verdict.
    assert verdict["min_kept_fraction"] == pytest.approx(0.20)
    # ... while the strike gain is untouched: the flash still reads.
    assert verdict["max_gain"] == pytest.approx(2.2)


def test_short_clip_head_never_settles_and_fails():
    """A head on 10 played frames never reaches neutral: refused."""
    from library.tools import treatment_verify as tv

    verdict = tv.verify_treatment(
        {**applier_effects(HEAD),
         "source_in_frame": 0, "source_out_frame": 9},
        "tv_power_head", CLIP_DUR, played_frames=10)
    assert verdict["passed"] is False
    assert verdict["failure"] == "never_settles"


def test_undo_restores_byte_identical_comp():
    """Removing the treatment returns the exact untreated bytes."""
    from library.tools import treatment_verify as tv

    with_treatment = applier_effects({**HEAD, **TAIL})
    restored = tv.remove_treatment(
        tv.remove_treatment(with_treatment, "tv_power_head"),
        "tv_power_tail")
    stripped = {k: v for k, v in with_treatment.items()
                if not k.startswith("tv_power")}
    assert restored == stripped
    # The proof is string equality, not an assertion of equality:
    # counter reset per build makes the same inputs the same bytes.
    assert (build_effect_comp(restored, CLIP_DUR)
            == build_effect_comp(stripped, CLIP_DUR))


def test_window_gate_can_fail():
    """The deterministic half can fail: a leak outside the window."""
    from library.tools import treatment_verify as tv

    curves = {"PowerCrop1Top": [0.49 if f == 50 else 0.0
                                for f in range(72)]}
    verdict = tv.check_window(curves, window=(54, 71), played=72)
    assert verdict["passed"] is False
    assert verdict["outside_window"] == [50]


def test_window_gate_passes_clean_curves():
    from library.tools import treatment_verify as tv

    curves = {"PowerCrop1Top": [0.0 for _ in range(72)]}
    verdict = tv.check_window(curves, window=(54, 71), played=72)
    assert verdict["passed"] is True


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
    assert "end_frame+N" not in (BezierSpline.sampled.__doc__ or "")


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
                                     played_frames=played_reel())
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
                              played_frames=played_reel())
            == build_effect_comp(
                tv.remove_treatment(effects, "tv_power_tail"),
                CLIP_DUR, played_frames=played_reel()))


def test_verify_and_undo_drops_short_clip_treatments():
    """Neither half fits 10 frames: both go, the picture stays whole."""
    from library.tools import treatment_verify as tv

    effects = {"tv_power_head": True,
               "tv_power_head_timing": switch_on_frames(),
               "tv_power_tail": True,
               "tv_power_tail_timing": switch_off_frames(),
               "source_in_frame": 0, "source_out_frame": 9}
    final, rows = tv.verify_and_undo(effects, CLIP_DUR,
                                     played_frames=10)
    assert all(r["undone"] for r in rows)
    assert final == {"source_in_frame": 0, "source_out_frame": 9}
    assert "PowerCrop" not in build_effect_comp(
        final, CLIP_DUR, played_frames=10)


def test_skill_run_writes_a_receipt_that_reads_back(tmp_path):
    """A self-reported check writes no receipt; this one does."""
    from library.skills.verify_treatment.skill import run
    from library.tools import pipeline_skills

    record = run(dict(HEAD), "tv_power_head", CLIP_DUR,
                 str(tmp_path), "plan_vfx", played_frames=90)
    assert record["passed"] is True
    receipts = pipeline_skills.read_receipts(str(tmp_path), "plan_vfx")
    assert "verify_treatment" in receipts
    assert receipts["verify_treatment"]["result"]["passed"] is True


def test_skill_run_fails_a_drew_nothing_tail(tmp_path):
    """The tail the reels shipped: the skill gates it, receipted."""
    from library.skills.verify_treatment.skill import run

    record = run(applier_effects(TAIL), "tv_power_tail", CLIP_DUR,
                 str(tmp_path), "plan_vfx",
                 played_frames=played_reel())
    assert record["passed"] is True
    # With the played horizon the clamp anchors the tail inside what
    # renders; the legacy no-horizon build is what drew nothing (see
    # test_legacy_tail_without_horizon_draws_nothing_on_reel).


def test_skill_refuses_an_unknown_key():
    """An unchecked treatment must not read as a checked one."""
    import pytest

    from library.skills.verify_treatment.skill import run
    from library.tools.treatment_verify import UnknownTreatment

    with pytest.raises(UnknownTreatment):
        run({}, "slow_zoom", 600, "/nonexistent", "plan_vfx")


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

    class MockClip:
        def GetStart(self): return 0
        def GetEnd(self): return played
        def GetDuration(self): return played
        def GetMediaPoolItem(self): return MockPool()
        def GetFusionCompNameList(self): return []
        def ImportFusionComp(self, path): return True
        def DeleteFusionCompByName(self, name): pass

    class MockPool:
        def GetClipProperty(self, prop):
            if prop == "File Path":
                return "a_roll.mov"
            if prop == "Frames":
                return "600"
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

    monkeypatch.setattr(afc.dvr, "scriptapp", lambda x: MockResolve())
    return afc


def test_applier_ships_a_drawing_tail_with_receipt(monkeypatch, tmp_path):
    """72 played frames: the clamped tail draws, receipted, nothing undone."""
    import glob

    from library.tools import pipeline_skills

    afc = _mock_resolve(monkeypatch, 72)
    assert afc.apply_fusion_comps(
        _reel_tail_manifest(), str(tmp_path),
        step_id="build_reels") is True

    receipts = pipeline_skills.read_receipts(str(tmp_path), "build_reels")
    record = receipts["verify_treatment"]["result"]
    assert record["treatments_undone"] == 0
    row = record["rows"][0]
    assert row["treatment"] == "tv_power_tail"
    assert row["passed"] is True and row["undone"] is False

    banked = glob.glob(str(tmp_path / "assets" / "fusion_presets" / "clip_0_*.comp"))
    assert len(banked) == 1
    assert "PowerCrop" in open(banked[0]).read()


def test_applier_undoes_a_tail_with_no_room_and_receipts_it(
        monkeypatch, tmp_path):
    """10 played frames: the tail cannot fit, so the applier drops it.

    The banked comp carries no PowerCrop - the picture keeps what the
    footage had - and the receipt names the failed key and the reason.
    That is the undo, proven on the path that ships, not asserted.
    """
    import glob

    from library.tools import pipeline_skills

    afc = _mock_resolve(monkeypatch, 10)
    assert afc.apply_fusion_comps(
        _reel_tail_manifest(), str(tmp_path),
        step_id="build_reels") is True

    receipts = pipeline_skills.read_receipts(str(tmp_path), "build_reels")
    record = receipts["verify_treatment"]["result"]
    assert record["treatments_undone"] == 1
    row = record["rows"][0]
    assert row["treatment"] == "tv_power_tail"
    assert row["failure"] == "never_settles"
    assert row["undone"] is True

    banked = glob.glob(str(tmp_path / "assets" / "fusion_presets" / "clip_0_*.comp"))
    assert len(banked) == 1
    assert "PowerCrop" not in open(banked[0]).read()


def test_head_samples_decode_the_opening_not_the_number():
    """The evidence is decoded off the comp: kept picture and gain at
    the frames the captain judges (0/2/5/10/18).  The softened default
    holds a fifth through the strike, opens over the same ramp, and
    settles where it always did."""
    from library.tools import treatment_verify as tv

    assert tv.HEAD_JUDGE_FRAMES == (0, 2, 5, 10, 18)
    rows = {r["frame"]: r for r in tv.sample_head_frames(
        dict(HEAD), CLIP_DUR)}
    assert rows[0]["kept_fraction"] == pytest.approx(0.20)
    assert rows[2]["kept_fraction"] == pytest.approx(0.20)
    assert rows[0]["gain"] == pytest.approx(2.2)
    # The ramp opens to full picture and the bloom settles by 18 -
    # softening the strike moved neither.
    assert rows[10]["kept_fraction"] == pytest.approx(1.0)
    assert rows[18] == {"frame": 18, "kept_fraction": 1.0, "gain": 1.0}


def test_current_shallower_and_shorter_decode_differently():
    """Shallower and shorter are not the same fix, and the table shows
    it: the old strike keeps 2% through frame 2, the softened one keeps
    a fifth, and the shortened one is already half open by frame 5.
    Either variant leaves frame 18 neutral - the bloom question,
    answered rather than asserted."""
    from library.tools import treatment_verify as tv

    current = {"tv_power_head": True, "tv_power_head_timing": {
        **switch_on_frames(), "collapse_crop": 0.49}}
    shorter = {"tv_power_head": True, "tv_power_head_timing": {
        **switch_on_frames(), "line_frames": 2, "collapse_crop": 0.49}}

    def by_frame(effects):
        return {r["frame"]: r
                for r in tv.sample_head_frames(effects, CLIP_DUR)}

    old, new, quick = (by_frame(current), by_frame(dict(HEAD)),
                       by_frame(shorter))
    # How FAR it closes: 2% vs a fifth, held through frame 2 either way.
    assert old[0]["kept_fraction"] == pytest.approx(0.02)
    assert new[0]["kept_fraction"] == pytest.approx(0.20)
    assert quick[0]["kept_fraction"] == pytest.approx(0.02)
    assert quick[2]["kept_fraction"] == pytest.approx(0.02)
    # How LONG it stays closed: the shortened hold is already opening
    # at frame 5 while the 4-frame holds are not.
    assert quick[5]["kept_fraction"] > new[5]["kept_fraction"]
    assert new[5]["kept_fraction"] > old[5]["kept_fraction"]
    # The strike gain is untouched on all three: the flash still reads.
    assert {old[0]["gain"], new[0]["gain"], quick[0]["gain"]} == {2.2}
    # And every variant settles: frame 18 is neutral picture throughout.
    for table in (old, new, quick):
        assert table[18] == {"frame": 18, "kept_fraction": 1.0,
                             "gain": 1.0}


def test_softened_head_samples_identically_on_both_picture_rows():
    """V1 Akshita and V2 Craig: the Fusion pass reaches both rows
    (PR 874), so the head the captain judged is the head both rows
    draw.  Same timing in, same decoded frames out - per clip, per
    row, off the bytes the renderer writes."""
    from library.tools import treatment_verify as tv
    from library.tools.execution.fusion_tracks import (
        fusion_comp_tracks, reachable_effect_labels)

    manifest = {
        "tracks": {
            "V1": {"clips": [{"label": "reel_picture_akshita",
                              "source_file": "akshita.mov"}]},
            "V2": {"clips": [{"label": "reel_picture_craig",
                              "source_file": "craig.mov"}]},
        },
        "fusion_effects": {"per_clip": {
            "reel_picture_akshita": dict(HEAD),
            "reel_picture_craig": dict(HEAD),
        }},
    }
    # The pass visits both rows - this is what stranded V2 before.
    visited = [index for index, clips, _ in fusion_comp_tracks(manifest)
               if clips]
    assert visited == [1, 2]
    labels = reachable_effect_labels(manifest)
    assert {"reel_picture_akshita", "reel_picture_craig"} <= labels

    per_clip = manifest["fusion_effects"]["per_clip"]
    v1 = tv.sample_head_frames(per_clip["reel_picture_akshita"], 600)
    v2 = tv.sample_head_frames(per_clip["reel_picture_craig"], 600)
    assert v1 == v2
    assert {r["frame"]: r for r in v1}[0]["kept_fraction"] == pytest.approx(
        0.20)
