"""Transitions ride their own track (finding 16), and an unplaceable one
goes back to the planner before the surfaced hard-cut fallback ships
(finding 32). History: `docs/evidence/transition_own_track.md`.
"""
from __future__ import annotations
import copy
import json
import sys
from pathlib import Path
import pytest
import re
from types import SimpleNamespace
from unittest.mock import patch
from library.tools import reel_build as rb
from library.tools.reel_build import ReelBuildError, _place_transition_element
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.fusion.effects import _reset_counters
from library.tools.fusion.played_window import (
    TransitionLongerThanTheClip,
    played_length,
)
from library.tools.fusion.transition_frames import (
    drawn_frames,
    transition_splines,
)


REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ── compile_manifest: the V2 seat ────────────────────────────────

def _compile_with(project, plan_transitions, broll=None):
    from unittest.mock import patch

    from library.steps.step_5_04_compile_manifest import step

    project_dir, layout, outputs, sfx_file = project
    outputs = copy.deepcopy(outputs)
    outputs["plan_transitions"] = {"transition_spec": plan_transitions}
    if broll is not None:
        outputs["select_broll"]["b_roll_assignments"] = broll
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
    """Two abutting V1 clips (0-2.285, 2.285-5.418), under tmp_path only."""
    import tests.scenarios.test_compile_manifest_without_the_decoration as base
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


def _broll_pair(b_roll):
    """Two abutting V2 clips, 0-2.0 and 2.0-4.0: a V2 cut at 2.0s
    where no V1 clip ends (V1 cuts at 2.285 and 5.418)."""
    return [
        {"spine_block_position": 1, "block_type": "speech",
         "clip_id": "clip_2", "source_file": b_roll,
         "video_in": 1.0, "video_out": 3.0,
         "duration_seconds": 2.0, "timeline_start": 0.0,
         "timeline_end": 2.0, "video_only": True},
        {"spine_block_position": 2, "block_type": "speech",
         "clip_id": "clip_2", "source_file": b_roll,
         "video_in": 3.0, "video_out": 5.0,
         "duration_seconds": 2.0, "timeline_start": 2.0,
         "timeline_end": 4.0, "video_only": True},
    ]


def test_native_transition_at_a_v2_cut_rides_v2(project):
    """Finding 16's shape: a dissolve planned at the b-roll edge (2.0s,
    no V1 clip ends there) seats on the V2 pair instead of the V1 clip
    underneath - before the fix this downgraded to a hard cut."""
    _dir, _layout, outputs, _sfx = project
    b_roll = outputs["select_broll"]["b_roll_assignments"][0]["source_file"]
    manifest = _compile_with(
        project,
        plan_transitions=[
            {"transition_id": "trans_001",
             "transition_type": "cross_dissolve",
             "cut_point_timeline": 2.0, "duration": 0.4}],
        broll=_broll_pair(b_roll))
    assert manifest["transitions_downgraded"] == []
    (row,) = manifest["native_transitions"]
    assert row["track"] == "v2"
    assert row["after_clip"] == 0
    assert row["transition_id"] == "trans_001"


def test_native_transition_on_the_v1_cut_still_rides_v1(project):
    """The V2 seat must not move existing placements: a dissolve on
    the real V1 cut at 2.285s still seats on V1."""
    manifest = _compile_with(
        project,
        plan_transitions=[
            {"transition_id": "trans_001",
             "transition_type": "cross_dissolve",
             "cut_point_timeline": 2.285, "duration": 0.4}])
    (row,) = manifest["native_transitions"]
    assert row["track"] == "v1"
    assert row["after_clip"] == 0
    assert manifest["transitions_downgraded"] == []


def test_native_transition_with_no_pair_downgrades_surfaced(project):
    """A lone V2 clip carries no native transition (a tail and a head
    need a pair): the row downgrades AND the per-item `transitions`
    row says what was asked and why - not only `transitions_downgraded`
    on disk."""
    _dir, _layout, outputs, _sfx = project
    b_roll = outputs["select_broll"]["b_roll_assignments"][0]["source_file"]
    manifest = _compile_with(
        project,
        plan_transitions=[
            {"transition_id": "trans_009",
             "transition_type": "cross_dissolve",
             "cut_point_timeline": 1.0, "duration": 0.4}],
        broll=[{**_broll_pair(b_roll)[0],
                "timeline_start": 0.0, "timeline_end": 2.0}][:1])
    assert manifest["native_transitions"] == []
    (downgraded,) = manifest["transitions_downgraded"]
    assert downgraded["transition_id"] == "trans_009"
    assert downgraded["shipped_type"] == "hard_cut"
    (row,) = [t for t in manifest["transitions"]
              if t.get("transition_id") == "trans_009"]
    assert row["transition_type"] == "hard_cut"
    assert row["requested_type"] == "cross_dissolve"
    assert "V1" in row["downgrade_reason"]
    assert "V2" in row["downgrade_reason"]


def test_native_transition_at_exact_unmatched_frame_keeps_duration_on_downgrade(
        project):
    """A hard-cut fallback used to erase the requested 12-frame hold."""
    _dir, _layout, outputs, _sfx = project
    b_roll = outputs["select_broll"]["b_roll_assignments"][0]["source_file"]
    manifest = _compile_with(
        project,
        plan_transitions=[{
            "transition_id": "trans_010",
            "transition_type": "cross_dissolve",
            "cut_point_timeline": 1.0,
            "cut_point_frame": 30,
            "duration_source": "frames",
            "duration_frames": 12,
        }],
        broll=[{**_broll_pair(b_roll)[0],
                "timeline_start": 0.0, "timeline_end": 2.0}][:1])

    (row,) = [t for t in manifest["transitions"]
              if t.get("transition_id") == "trans_010"]
    assert row["cut_point_frame"] == 30
    assert row["requested_duration_frames"] == 12
    assert row["transition_type"] == "hard_cut"
    assert row["duration_frames"] == 0
    assert "frame 30" in row["downgrade_reason"]


# ── applicator: the track is placed, not assumed ─────────────────

class _FakeTransition:
    def __init__(self, start, end):
        self._start, self._end = start, end

    def GetName(self): return "Cross Dissolve"
    def GetStart(self): return self._start
    def GetEnd(self): return self._end
    def GetDuration(self): return self._end - self._start


class _FakeItem:
    def __init__(self, name, start, end):
        self._name = name
        self._start = start
        self._end = end
        self.transitions = []

    def GetName(self): return self._name
    def GetStart(self): return self._start
    def GetEnd(self): return self._end
    def GetDuration(self): return self._end - self._start

    def AddTransition(self, payload):
        self.transitions.append(payload)
        mid = (self._start + self._end) // 2
        return _FakeTransition(mid - 6, mid + 6)


def _v2_op(**extra):
    op = {"transition_id": "trans_002", "resolve_name": "Cross Dissolve",
          "category": "simple", "after_clip": 0, "track": "v2",
          "duration_frames": 12}
    op.update(extra)
    return op


def test_a_v2_op_lands_on_the_v2_incoming_item():
    """The applicator places on the V2 pair's incoming item - never on
    the V1 clip underneath."""
    from library.tools import native_ops_apply as apply
    v1 = [_FakeItem("v1_a", 0, 150), _FakeItem("v1_b", 150, 300)]
    v2 = [_FakeItem("broll_a", 0, 60), _FakeItem("broll_b", 60, 120)]
    report = apply.apply_native_transitions(
        object(), v1, [_v2_op()], fps=30.0, v2_items=v2)
    assert len(report["applied"]) == 1
    assert report["applied"][0]["track"] == "V2"
    assert v2[1].transitions and not v2[0].transitions
    assert not v1[0].transitions and not v1[1].transitions
    assert report["failed"] == []


def test_a_v2_op_without_v2_items_refuses_by_name():
    """No b-roll row on the timeline: the op refuses naming the track,
    rather than seating on V1."""
    from library.tools import native_ops_apply as apply
    v1 = [_FakeItem("v1_a", 0, 150), _FakeItem("v1_b", 150, 300)]
    report = apply.apply_native_transitions(
        object(), v1, [_v2_op()], fps=30.0, v2_items=None)
    assert report["applied"] == []
    assert "V2" in report["failed"][0]["what"]
    assert "b-roll" in report["failed"][0]["what"]


def test_a_v1_op_ignores_v2_items():
    """Rows without a track (state written before the seat existed)
    keep the old meaning: V1."""
    from library.tools import native_ops_apply as apply
    v1 = [_FakeItem("v1_a", 0, 150), _FakeItem("v1_b", 150, 300)]
    v2 = [_FakeItem("broll_a", 0, 60), _FakeItem("broll_b", 60, 120)]
    op = _v2_op()
    del op["track"]
    report = apply.apply_native_transitions(
        object(), v1, [op], fps=30.0, v2_items=v2)
    assert len(report["applied"]) == 1
    assert report["applied"][0]["track"] == "V1"
    assert v1[1].transitions and not v2[1].transitions


# ── 4.02: the retry, then the surfaced fallback ───────────────────

def _spine():
    """speech | transition_slot | speech: the cut out of the slot
    (into block 3) carries nothing drawn - no V1 clip ends there."""
    def speech(pos, start, end):
        return {"block_type": "speech", "position": pos,
                "clip_id": "clip_011", "source_start": 0.0,
                "source_end": end - start, "alignment_method": "whisperx",
                "timeline_start": start, "timeline_end": end,
                "word_timestamps": [], "content": {"clip_id": "clip_011"}}
    return {"structure": [
        speech(1, 0.0, 2.285),
        {"block_type": "transition_slot", "position": 2, "clip_id": None,
         "source_start": None, "source_end": None,
         "timeline_start": 2.285, "timeline_end": 4.185,
         "word_timestamps": [], "alignment_method": None, "content": {}},
        speech(3, 4.185, 6.5),
    ]}


def _resolve(creative, attempt, v2=(), spine=None):
    from library.steps.step_4_02_plan_transitions import post_bridge as pb
    return pb.resolve_transitions(
        creative, spine or _spine(), {"music_selection": {}}, None, 30.0,
        {}, {}, None, v2_spans=list(v2), attempt=attempt)


def _spine_speech_pair():
    """speech | speech: a V1 cut that draws through."""
    def speech(pos, start, end):
        return {"block_type": "speech", "position": pos,
                "clip_id": "clip_011", "source_start": 0.0,
                "source_end": end - start, "alignment_method": "whisperx",
                "timeline_start": start, "timeline_end": end,
                "word_timestamps": [], "content": {"clip_id": "clip_011"}}
    return {"structure": [speech(1, 0.0, 2.285), speech(2, 2.285, 5.0)]}


def test_unplaceable_drawn_transition_first_pass_goes_back_to_the_model():
    """Finding 32's B6 shape at plan time: a drawn defocus out of a
    transition slot carries on no V1 cut. First pass raises, naming
    the boundary and the basis, so post_bridge_retry re-asks."""
    with pytest.raises(ValueError, match="re-placed onto a cut") as exc:
        _resolve([{"cut_point_position": 3, "type": "defocus",
                   "duration_feel": "medium", "rationale": "x"}],
                 attempt=1, v2=[])
    assert "transition_slot" in str(exc.value)
    assert "3" in str(exc.value)
    # Native, no V1 cut through the slot boundary, no V2 pair: the same.
    with pytest.raises(ValueError, match="re-placed onto a cut"):
        _resolve([{"cut_point_position": 3, "type": "cross_dissolve",
                   "duration_feel": "medium", "rationale": "x"}],
                 attempt=1, v2=[])


def test_unplaceable_transition_later_pass_ships_a_surfaced_hard_cut():
    """The retry did not place it: the boundary ships as the hard cut
    it already is, stamped on the row - the per-item record says what
    was asked and why."""
    (row,) = _resolve([{"cut_point_position": 3, "type": "defocus",
                        "duration_feel": "medium", "rationale": "x"}],
                      attempt=2, v2=[])
    assert row["transition_type"] == "hard_cut"
    assert row["duration_frames"] == 0
    assert row["requested_type"] == "defocus"
    assert "V1" in row["downgrade_reason"]


def test_placeable_transition_needs_no_retry():
    """speech | speech carries drawn and native alike: first pass
    resolves, no raise. (A native transition bracketing a cutaway -
    speech into a slot - carries nowhere: the incoming V1 clip is not
    the picture the viewer sees next, so it retries exactly like the
    unplaceable cases above.)"""
    rows = _resolve([{"cut_point_position": 2, "type": "cross_dissolve",
                      "duration_feel": "medium", "rationale": "x"}],
                    attempt=1, v2=[], spine=_spine_speech_pair())
    assert rows and rows[0]["transition_type"] == "cross_dissolve"
    rows = _resolve([{"cut_point_position": 2, "type": "defocus",
                      "duration_feel": "medium", "rationale": "x"}],
                    attempt=1, v2=[], spine=_spine_speech_pair())
    assert rows and rows[0]["transition_type"] == "defocus"
    # A native dissolve where two b-roll clips abut carries on V2.
    rows = _resolve([{"cut_point_position": 3, "type": "cross_dissolve",
                      "duration_feel": "medium", "rationale": "x"}],
                    attempt=1, v2=[(2.0, 4.185), (4.185, 6.0)])
    assert rows and rows[0]["transition_type"] == "cross_dissolve"


# --------------------------------------------------------------------------
# From test_transition_element_placement_judged.py
#
# The transition-element append is judged, at placement time.
#
# `build_reel_timeline` used to discard the return of the transition
# `AppendToTimeline`, so an element Resolve declined to place left no
# trace until F18 graded placed-against-planned length a whole stage
# later. The placement now raises naming the element - first where the
# append returns nothing (the overlay path's shape at
# `reel_build.py:5045-5060`), then where it returns a truthy zombie
# handle and the track disagrees (`composed_edit` step 6).
#
# Stub-testable without Resolve: the pool and the timeline are fakes,
# and `assert_current_timeline` is patched out - the lease is a
# separate judgement this test does not exercise.

ELEMENT = "/project/brand_assets/transition.mov"
FPS = 24000 / 1001


class _PoolItem:
    def GetClipProperty(self, _key):
        return "30"


def _placement(**overrides):
    base = {"element_path": ELEMENT, "element_seconds": 1.5,
            "record_frame": 100, "duration_frames": 36}
    base.update(overrides)
    return SimpleNamespace(**base)


class _Pool:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def AppendToTimeline(self, items):
        self.calls.append(items)
        return self.result


class _TimelineItem:
    def __init__(self, start, end):
        self._start = start
        self._end = end

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end


class _Timeline:
    def __init__(self, items):
        self._items = list(items)

    def GetItemListInTrack(self, _media_type, _index):
        return list(self._items)


def _place(pool, timeline, placement=None):
    with patch.object(rb, "assert_current_timeline", lambda *a: None):
        return _place_transition_element(
            pool, object(), timeline, "Reel 01",
            placement or _placement(), _PoolItem(), 4, FPS)


def test_an_element_resolve_did_not_place_raises_naming_it():
    """Declined (append returns nothing), a truthy zombie handle the track
    disagrees with, or a wrong-length item on the track: all refuse."""
    pool = _Pool(None)
    with pytest.raises(ReelBuildError, match=re.escape(ELEMENT)):
        _place(pool, _Timeline([]))
    assert pool.calls, "the element was never offered to Resolve at all"
    with pytest.raises(ReelBuildError, match=re.escape(ELEMENT)):
        _place(_Pool([object()]), _Timeline([]))
    with pytest.raises(ReelBuildError, match=re.escape(ELEMENT)):
        _place(_Pool([object()]), _Timeline([_TimelineItem(100, 135)]))


def test_a_placed_element_passes_and_sends_no_media_type():
    pool = _Pool([object()])
    timeline = _Timeline([_TimelineItem(100, 136)])
    assert _place(pool, timeline) is None
    sent = pool.calls[0][0]
    assert "mediaType" not in sent, (
        "transition elements may carry intentional audio - mediaType "
        "is the transition owner's call, not this judgement's")
    assert sent["recordFrame"] == 100
    assert sent["trackIndex"] == 4


# --------------------------------------------------------------------------
# From test_transition_ramp_draws.py
#
# A planned transition ramps over its planned frames, and stops.
#
# Counts the frames a transition is actually DRAWN on, off the comp the
# renderer writes, over the frames Resolve renders for that clip. Cases are
# project 001's run of record and issue #202; history in
# `docs/evidence/transition_ramp_draws.md`.

#: One placement of 001's run of record: the source clip's own frame
#: count, the SOURCE frames the timeline plays, and the planned ramp.
#: ``source_in``/``source_out`` are ``round(seconds * 30)`` on the
#: manifest's own values, which is what apply_fusion_comps computes.
CASES = [
    # (name, half, ttype, clip_dur, source_in, source_out, dur_frames)
    ("cut 8  tail  speech_7_seg0  IMG_1816",
     "tail", "defocus", 5656, 3016, 3495, 15),
    ("cut 8  head  speech_9_seg0  IMG_1817",
     "head", "defocus", 1245, 654, 725, 15),
    ("cut 13 tail  speech_12_seg0 IMG_1817",
     "tail", "defocus", 1245, 1018, 1062, 15),
    ("cut 13 head  speech_14_seg0 IMG_1822",
     "head", "defocus", 2574, 944, 1204, 15),
    # Issue #202: spine block 18, timeline 51.941-54.323, clip_012 /
    # IMG_1817, duration_frames 15.  Same shape, different type.
    ("#202  head  clip_012       IMG_1817",
     "head", "zoom_blur", 1245, 654, 725, 15),
    ("#202  tail  clip_012       IMG_1817",
     "tail", "zoom_blur", 1245, 654, 725, 15),
]

#: Every type the renderer can draw, exercised on one awkward window
#: (a short segment cut from deep inside a long source), because
#: `zoom_blur` reached a render for the first time two years after it
#: was advertised and drew the wrong thing.
EVERY_TYPE = ["fade_to_black", "zoom_blur", "defocus", "flash"]


def _comp(half, ttype, clip_dur, source_in, source_out, dur_frames):
    """The comp the renderer really writes for one transition half.

    Driven through ``build_effect_comp`` - the function
    ``apply_fusion_comps`` calls - so the test measures the route the
    picture takes, not a builder called directly.
    """
    _reset_counters()
    effects = {
        f"{half}_transition": ttype,
        f"{half}_transition_frames": dur_frames,
    }
    if source_in is not None:
        effects["source_in_frame"] = source_in
        effects["source_out_frame"] = source_out
    return build_effect_comp(effects, clip_dur, (1920, 1080))


@pytest.mark.parametrize(
    "name,half,ttype,clip_dur,source_in,source_out,dur_frames", CASES,
    ids=[c[0].split()[0] + c[0].split()[1] + c[0].split()[2] for c in CASES])
def test_a_planned_transition_draws_for_its_planned_frames(
        name, half, ttype, clip_dur, source_in, source_out, dur_frames):
    """Drawn frames == planned frames, and they sit where planned."""
    comp = _comp(half, ttype, clip_dur, source_in, source_out, dur_frames)
    length = played_length(clip_dur, source_in, source_out)

    splines = transition_splines(comp)
    assert splines, f"{name}: the comp carries no animated transition"

    for spline_name, keys in splines.items():
        drawn = drawn_frames(keys, length, half)
        where = f"{name} [{spline_name}]"

        assert drawn, (
            f"{where}: 0 of {dur_frames} planned frames drew. The ramp is "
            f"outside the {length} frames this clip plays."
        )
        assert len(drawn) == dur_frames, (
            f"{where}: {len(drawn)} frames drawn, {dur_frames} planned. "
            f"{length} frames play."
        )

        if half == "head":
            expected = set(range(0, dur_frames))
        else:
            expected = set(range(length - dur_frames, length))
        assert set(drawn) == expected, (
            f"{where}: drawn on frames {min(drawn)}..{max(drawn)}, "
            f"planned {min(expected)}..{max(expected)} of {length}."
        )


def test_no_frame_carries_an_unplanned_transition():
    """Every played frame outside the ramp is exactly neutral.

    This is the half that caught it. The count above can be right while
    the effect is also held across the rest of the clip - which is what
    001 shipped: the ramp never ran, and 331 frames sat at full strength.
    """
    clip_dur, source_in, source_out, dur_frames = 1245, 654, 725, 15
    length = played_length(clip_dur, source_in, source_out)
    for ttype in EVERY_TYPE:
        for half in ("head", "tail"):
            comp = _comp(half, ttype, clip_dur, source_in, source_out,
                         dur_frames)
            for spline_name, keys in transition_splines(comp).items():
                drawn = set(drawn_frames(keys, length, half))
                unplanned = sorted(
                    f for f in range(length)
                    if f in drawn
                    and not (f < dur_frames if half == "head"
                             else f >= length - dur_frames))
                assert not unplanned, (
                    f"{ttype} {half} [{spline_name}]: {len(unplanned)} of "
                    f"{length} played frames carry an unplanned effect, "
                    f"frames {unplanned[0]}..{unplanned[-1]}."
                )


def test_a_ramp_longer_than_its_clip_is_refused_not_drawn():
    """A ramp with no room never reaches neutral, so it is refused."""
    for ttype in EVERY_TYPE:
        with pytest.raises(TransitionLongerThanTheClip):
            _comp("head", ttype, clip_dur=1245, source_in=654,
                  source_out=664, dur_frames=15)


def test_the_whole_source_case_still_places_its_ramp_at_the_end():
    """A clip that plays all of its source keeps the old geometry."""
    comp = _comp("tail", "defocus", clip_dur=90,
                 source_in=None, source_out=None, dur_frames=8)
    for _name, keys in transition_splines(comp).items():
        assert max(f for f, _ in keys) == 89
        assert set(drawn_frames(keys, 90, "tail")) == set(range(82, 90))


# --------------------------------------------------------------------------
# From test_dissolve_overlap_is_not_a_collision.py
#
# A correct dissolve is not a clip collision.
#
# Finding 18, execution-frontier report 2026-09-24: build QA reported
# every correct dissolve as a clip-overlap failure - a 6-frame centred
# Cross Dissolve read as "overlap_before_clip_N expected >=0, got -3".
# A centred native dissolve is drawn OVER the cut, so the neighbours
# overlap by half its duration. That explained overlap passes; anything
# bigger, or anywhere else, still fails.
#
# No Resolve: a fake timeline with scripted item spans.

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.timeline_qa import run_full_timeline_qa  # noqa: E402


class _Item:
    def __init__(self, start, end):
        self._start = start
        self._end = end

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetMediaPoolItem(self):
        return None


class _Timeline_2:
    def __init__(self, v1_spans):
        self._items = [_Item(s, e) for s, e in v1_spans]

    def GetItemListInTrack(self, track_type, index):
        if track_type == "video" and index == 1:
            return list(self._items)
        return []


def _failures(report):
    return [c for c in report.checks if not c.passed]


# ── The finding: a 6 f dissolve overlapping by 3 ────────────────────

def test_a_declared_dissolve_explains_its_own_overlap():
    """Two V1 items overlapping by 3 frames at a boundary carrying a
    declared 6-frame centred dissolve: no failure."""
    timeline = _Timeline_2([(0, 161), (158, 300)])
    manifest = {"native_transitions": [
        {"transition_id": "trans_001", "after_clip": 0,
         "duration_frames": 6}],
        "project": {"frame_rate": 30, "duration_seconds": 10.0},
    }
    report = run_full_timeline_qa(timeline, None, manifest)
    assert report.passed


def test_an_overlap_the_dissolve_does_not_explain_still_fails():
    """Overlapping by 4 frames with only a 6-frame dissolve declared
    (allowance 3), or a dissolve declared at the wrong boundary: still a
    collision."""
    cases = [
        ([(0, 162), (158, 300)], 0, 10.0),
        ([(0, 161), (158, 300), (300, 400)], 1, 13.0),
    ]
    for spans, after_clip, duration in cases:
        manifest = {"native_transitions": [
            {"transition_id": "trans_001", "after_clip": after_clip,
             "duration_frames": 6}],
            "project": {"frame_rate": 30, "duration_seconds": duration},
        }
        report = run_full_timeline_qa(_Timeline_2(spans), None, manifest)
        assert not report.passed
        assert any(c.name == "overlap_before_clip_1"
                   for c in _failures(report))
