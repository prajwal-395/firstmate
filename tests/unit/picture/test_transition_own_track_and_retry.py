"""Transitions ride their own track (finding 16), and an unplaceable one
goes back to the planner before the surfaced hard-cut fallback ships
(finding 32). History: `docs/evidence/transition_own_track.md`.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

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

