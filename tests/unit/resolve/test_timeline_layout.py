"""The timeline layout has one owner, and row counts come from the material.

Defects covered here:
  1. two speakers/angles collapsed onto one video row,
  4. blank video rows created from what MIGHT be placed,
  6. rows not labeled.

`library/tools/timeline_layout.py` takes the material (which angles
exist, what the plan asks for) and returns the track plan. Nothing else
may decide a track index or a track name - the two hardcoded dicts that
lived at the end of `resolve_build_timeline.py` died here.
"""
from __future__ import annotations
from library.tools.timeline_layout import (
    allocate_non_overlapping_rows,
    plan_layout,
)
import pytest
from library.steps.step_5_04_compile_manifest.step import (
    _assert_timeline_fully_covered,
    _video_coverage_gaps,
)
from library.tools.timeline_qa import (
    verify_transitions,
    verify_color_grades,
    verify_fusion_comps,
    run_full_timeline_qa
)
import json
import os
import sys


def _two_angle_material(**over):
    material = {
        "angles": [
            {"key": "akshita", "label": "Akshita", "speech_name": "Akshita CH1",
             "program_channel": 1},
            {"key": "craig", "label": "Craig", "speech_name": "Craig CH1",
             "program_channel": 1},
        ],
        "has_broll": False,
        "caption_spans": [],
        "mg_spans": [],
        "has_generators": False,
        "timed_text_spans": [],
        "music_spans": [],
        "sfx_spans": [],
    }
    material.update(over)
    return material


def test_two_angles_get_two_picture_rows_and_two_speech_rows():
    """Defect 1: each camera angle is its own row, picture and speech."""
    plan = plan_layout(_two_angle_material())

    video_roles = [(t.index, t.role, t.name) for t in plan.video_tracks]
    assert video_roles == [(1, "a_roll", "Akshita"), (2, "a_roll", "Craig")]

    audio_roles = [(t.index, t.role, t.name) for t in plan.audio_tracks]
    assert audio_roles == [(1, "speech", "Akshita CH1"),
                           (2, "speech", "Craig CH1")]
    # Defect 4: a row exists because something goes on it - no b-roll,
    # captions, music or SFX asked for, none planned.
    roles = [t.role for t in plan.video_tracks + plan.audio_tracks]
    assert roles == ["a_roll", "a_roll", "speech", "speech"]


def test_overlapping_sfx_layers_stack_and_sequential_ones_share():
    """SFX is one row or many depending on how layered the sound is."""
    one = allocate_non_overlapping_rows([(0, 50), (60, 100)], base_index=3)
    assert sorted({row for _, row in one}) == [3]
    two = allocate_non_overlapping_rows([(0, 50), (25, 75)], base_index=3)
    assert sorted({row for _, row in two}) == [3, 4]


def test_names_come_from_the_material_never_a_constant():
    """Defect 6: every row is named, from the angle or the declared role."""
    plan = plan_layout(_two_angle_material(has_broll=True))
    for track in plan.video_tracks + plan.audio_tracks:
        assert track.name, f"track {track.index} has no name"
    assert plan.video_row_for_angle("akshita").name == "Akshita"
    assert plan.speech_row_for_angle("craig").name == "Craig CH1"
    assert plan.video_row_for_angle("nobody") is None


def test_two_picture_rows_survive_the_tv_frame_look():
    """Captain's ruling on Reel 09 (marker at frame 1674): the a-roll
    row must be two rows, one per speaker, the way the two speech rows
    already are. The frame row dresses the picture above the captions -
    it does not collapse the picture to make room for itself."""
    plan = plan_layout(_two_angle_material(
        has_frame=True,
        caption_spans=[(0, 200)],
        has_transitions=True,
        has_explainer=True,
        has_semantic=True,
    ))
    assert [(t.index, t.role, t.name) for t in plan.video_tracks] == [
        (1, "a_roll", "Akshita"), (2, "a_roll", "Craig"),
        (3, "frame", "Frame"), (4, "captions", "Subtitles"),
        (5, "transitions", "Transitions"), (6, "explainer", "Explainer"),
        (7, "semantic", "Semantic")]
    assert [(t.index, t.role, t.name) for t in plan.audio_tracks] == [
        (1, "speech", "Akshita CH1"), (2, "speech", "Craig CH1")]
    # `collapse_picture` was the TV-frame rule PR 830 reasoned into the
    # plan; the captain overruled it. A stale caller still passing it
    # gets two picture rows anyway.
    plan = plan_layout(_two_angle_material(
        has_frame=True, collapse_picture=True))
    assert [t.name for t in plan.aroll_rows()] == ["Akshita", "Craig"]


# --------------------------------------------------------------------------
# From test_timeline_coverage.py
#
# Every frame of the timeline must show a clip, or the render is black.
#
# The shipped export carried 7.2s of black, 6.4s of it starting at 4.3s.
# The rough-cut review records only negative gaps by design, the B-roll
# duration invariant was satisfied by the very shortening that made the
# hole, and only the final ffmpeg probe noticed - one step from the end,
# with nothing left to do but fail the run.  This check is the same
# observation, moved to `compile_manifest` where it can still be acted on.

def manifest_with(v1=(), v2=(), duration=10.0, fps=30.0, spine=None):
    def clips(spans, prefix):
        return [
            {"timeline_in": start, "timeline_out": end,
             "timeline_in_frame": int(round(start * fps)),
             "timeline_out_frame": int(round(end * fps)),
             "label": f"{prefix}_{i}"}
            for i, (start, end) in enumerate(spans)
        ]

    manifest = {
        "project": {"frame_rate": fps, "duration_seconds": duration},
        "tracks": {
            "V1": {"label": "A-Roll", "clips": clips(v1, "aroll")},
            "V2": {"label": "B-Roll", "clips": clips(v2, "broll")},
        },
    }
    if spine is not None:
        manifest["_spine_blocks"] = spine
    return manifest


def block(start, end, block_type="transition_slot", **extra):
    entry = {
        "position": 1,
        "timeline_start": start,
        "timeline_end": end,
        "block_type": block_type,
    }
    entry.update(extra)
    return entry


def test_every_uncovered_range_fails_and_is_named():
    """Including the exact shape of the shipped defect: `select_broll`'s
    post-bridge shortens a cutaway whose source cannot fill its block -
    correct over speech, black on a block with no A-roll underneath (a
    3.567s clip on a 10s block)."""
    rows = (
        (manifest_with(v1=[(0.0, 4.308), (10.741, 20.0)], duration=20.0),
         ["4.308s to 10.741s", "6.433s"]),
        (manifest_with(v1=[(2.0, 10.0)]), ["0.000s to 2.000s"]),
        (manifest_with(v1=[(0.0, 0.741), (10.741, 20.0)],
                       v2=[(0.741, 4.308)], duration=20.0),
         ["4.308s to 10.741s"]),
        (manifest_with(v1=[(0.0, 4.308), (10.741, 21.937), (22.678, 30.0)],
                       duration=30.0),
         ["2 uncovered range(s)", "4.308s to 10.741s", "21.937s to 22.678s"]),
    )
    for manifest, needles in rows:
        with pytest.raises(ValueError) as excinfo:
            _assert_timeline_fully_covered(manifest)
        assert all(n in str(excinfo.value) for n in needles), (
            needles, str(excinfo.value))


def test_a_covered_timeline_passes():
    """A non-speech block covered only by a cutaway is legitimate, and
    float noise where one clip ends and the next begins is not black."""
    manifest = manifest_with(v1=[(0.0, 4.0), (7.0, 10.0)], v2=[(4.0, 7.0)])
    assert _video_coverage_gaps(manifest) == []
    _assert_timeline_fully_covered(manifest)
    manifest = manifest_with(v1=[(0.0, 4.9999), (5.0, 10.0)])
    assert _video_coverage_gaps(manifest) == []


def _beat_manifest(gap_end, spine, gap_start=4.0):
    """A 10s timeline whose only hole runs `gap_start` to `gap_end`."""
    return manifest_with(
        v1=[(0.0, gap_start), (gap_end, 10.0)], spine=spine)


DECLARED = {"intentional_black_beat": True,
            "black_beat_reason": "hold on black before the turn"}


def test_only_a_declared_short_non_speech_black_beat_is_an_edit():
    """A hole the plan chose, short and outside speech, is an edit.
    Everything else is a hole: a beat longer than the bound (0.8s of
    black is a hole with a note attached); a bare flag with no reason (a
    rubber stamp); intent inferred from a short hole rather than
    recorded; black on speech; a gap running past the declared block's
    edge; and the shipped 6.433s gap however it is labelled."""
    manifest = _beat_manifest(4.4, [block(3.0, 5.0, **DECLARED)])
    assert _video_coverage_gaps(manifest) == [(4.0, 4.4)]
    _assert_timeline_fully_covered(manifest)
    rows = (
        (_beat_manifest(4.8, [block(3.0, 5.0, **DECLARED)]),
         "longer than the 0.5s"),
        (_beat_manifest(4.4, [block(3.0, 5.0, intentional_black_beat=True)]),
         "no black_beat_reason"),
        (_beat_manifest(4.4, [block(3.0, 5.0)]), "no spine block declares"),
        (_beat_manifest(4.4, [block(3.0, 5.0, block_type="speech",
                                    **DECLARED)]),
         "speech is never held on black"),
        (_beat_manifest(4.4, [block(3.0, 4.2, **DECLARED)]),
         "no spine block declares"),
        (manifest_with(v1=[(0.0, 4.308), (10.741, 20.0)], duration=20.0,
                       spine=[block(0.0, 20.0, **DECLARED)]),
         "longer than the 0.5s"),
    )
    for manifest, match in rows:
        with pytest.raises(ValueError, match=match):
            _assert_timeline_fully_covered(manifest)


def test_clips_without_frame_fields_fall_back_to_seconds():
    manifest = {
        "project": {"frame_rate": 30.0, "duration_seconds": 10.0},
        "tracks": {"V1": {"clips": [
            {"timeline_in": 0.0, "timeline_out": 6.0, "label": "a"},
        ]}},
    }
    gaps = _video_coverage_gaps(manifest)
    assert len(gaps) == 1
    assert gaps[0] == pytest.approx((6.0, 10.0))


# --------------------------------------------------------------------------
# From test_timeline_qa.py

class MockMediaPoolItem:
    def __init__(self, name="test.mov", status="Online"):
        self.name = name
        self.status = status
    def GetClipProperty(self, prop):
        if prop == "File Name": return self.name
        if prop == "Status": return self.status
        return None

class MockTimelineItem:
    def __init__(self, start=0, end=30, mpi=None, comps=()):
        self.start = start
        self.end = end
        self.mpi = mpi or MockMediaPoolItem()
        self.name = self.mpi.name
        self.comps = list(comps)

    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetDuration(self): return self.end - self.start
    def GetMediaPoolItem(self): return self.mpi
    def GetName(self): return self.name
    def GetCDL(self): return {"Slope": "1.0 1.0 1.0"}
    def GetFusionCompNameList(self): return self.comps

class MockTimeline:
    def __init__(self, items, by_track=None):
        self.items = items
        self.by_track = by_track or {}
    def GetItemListInTrack(self, t_type, index):
        if self.by_track:
            return self.by_track.get(index, [])
        return self.items
    def GetTrackCount(self, t_type):
        return 2


def test_full_sweep_fails_a_gap_or_overlap_unless_v2_covers_it():
    project = {"project": {"frame_rate": 30, "duration_seconds": 2.0}}
    # Gap between 30 and 32.
    timeline = MockTimeline([MockTimelineItem(start=0, end=30),
                             MockTimelineItem(start=32, end=60)])
    report = run_full_timeline_qa(timeline, None, project)
    assert not report.passed
    assert any("gap_before" in c.name for c in report.checks)
    # Overlap between 25 and 30.
    timeline = MockTimeline([MockTimelineItem(start=0, end=30),
                             MockTimelineItem(start=25, end=60)])
    report = run_full_timeline_qa(timeline, None, project)
    assert not report.passed
    assert any("overlap_before" in c.name for c in report.checks)
    # The same V1 gap covered by V2 b-roll (25 to 35) is no gap.
    timeline = MockTimeline(items=[], by_track={
        1: [MockTimelineItem(start=0, end=30),
            MockTimelineItem(start=32, end=60)],
        2: [MockTimelineItem(start=25, end=35)]})
    report = run_full_timeline_qa(timeline, None, project)
    assert report.passed, "Gap should be ignored if covered by V2"
    assert not any("gap_before" in c.name for c in report.checks)


def test_verify_color_grades_records_a_readback_that_raises():
    """The bare `except: pass` used to make a broken readback a pass."""
    class Exploding(MockTimelineItem):
        def GetCDL(self): raise RuntimeError("boom")

    timeline = MockTimeline([Exploding()])
    manifest_color = {
        "per_clip_adjustments": [
            {"source_file": "test.mov", "cdl_values": {"slope_r": 1.0}}
        ]
    }
    report = verify_color_grades(timeline, None, manifest_color)
    assert any("cdl_readback" in c.name for c in report.checks)


# ── Station 5: fusion comps ───────────────────────────────────────
# This station had `pass` as its only loop body and returned passed=True
# whatever the timeline held. These tests exist so it cannot go back.

FUSION_EFFECTS = {"per_clip": {"a_roll_0": {"glow_gain": 2.0}}}


def test_verify_fusion_comps_reads_every_planned_comp():
    """Passes when the comp is there or no effect was planned; fails a
    missing comp on V1 (and says the collapse once when nothing carried
    one) or V2 (a miss, not a collapse); and no labels means it cannot
    check, which is not a pass."""
    def check(by_track, labels, effects=FUSION_EFFECTS):
        timeline = MockTimeline([], by_track=by_track)
        report = verify_fusion_comps(timeline, labels, effects)
        return report.passed, {c.name for c in report.checks}

    assert check({1: [MockTimelineItem(comps=["Fusion Composition 1"])],
                  2: []}, {1: ["a_roll_0"], 2: []}) == (True, set())
    assert check({1: [MockTimelineItem(comps=[])]}, {1: ["a_roll_0"]},
                 {"per_clip": {}})[0] is True
    passed, names = check({1: [MockTimelineItem(comps=[])], 2: []},
                          {1: ["a_roll_0"], 2: []})
    assert not passed
    assert {"V1_clip_0_fusion_comp", "fusion_comps_all_missing"} <= names
    passed, names = check(
        {1: [MockTimelineItem(comps=["Fusion Composition 1"])],
         2: [MockTimelineItem(comps=[])]},
        {1: ["a_roll_0"], 2: ["b_roll_0"]},
        {"per_clip": {"a_roll_0": {}, "b_roll_0": {}}})
    assert not passed
    assert "V2_clip_0_fusion_comp" in names
    assert "fusion_comps_all_missing" not in names
    passed, names = check({1: [MockTimelineItem(comps=[])]}, None)
    assert not passed
    assert "fusion_comps_unverifiable" in names


def test_verify_transitions_end_of_piece_fade_needs_no_head():
    # TR3.1's 1 s dip to black out of the final shot: the build drew the
    # tail on the last V1 clip, and the station failed the render looking
    # for a head on a clip index past the end of the track.
    last = MockTimelineItem(comps=["Fusion Composition 1"])
    timeline = MockTimeline([], by_track={1: [MockTimelineItem(), last]})
    end_fade = [{"after_clip": 1, "at_end": True, "type": "fade_to_black",
                 "duration_frames": 30}]
    report = verify_transitions(timeline, None, end_fade)
    assert report.passed, [c.name for c in report.checks]
    # Mid-piece, the head is still required.
    first = MockTimelineItem(comps=["Fusion Composition 1"])
    timeline = MockTimeline([], by_track={1: [first, MockTimelineItem()]})
    report = verify_transitions(
        timeline, None, [{"after_clip": 0, "type": "fade_to_black"}])
    assert not report.passed
    assert [c.name for c in report.checks] == [
        "transition_fade_to_black_head_on_v1[1]"]


# --------------------------------------------------------------------------
# From test_timeline_decisions.py
#
# Stamping each clip with the decision that produced it.
#
# History: docs/evidence/resolve_test_history.md#test_timeline_decisions.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import marker_payload, marker_routing
from library.tools import timeline_decisions as td
from library.tools.marker_feedback import PULL_FILE_SUFFIX
from library.tools.project_layout import (
    Area,
    ProjectLayout,
)
from tests.resolve_double import FakeTimeline
from tests.unit.resolve.test_marker_routing import (
    AMBIGUOUS_NOTE,
)

FPS = 30.0


def _seconds(frame):
    return frame / FPS


# ── 001's manifest, at the placements the notes sit on ──────────────

MANIFEST = {
    "project": {
        "name": "Pipeline_Edit",
        "frame_rate": FPS,
        "resolution": {"width": 1080, "height": 1920},
    },
    "tracks": {
        "V1": {
            "label": "A-Roll",
            "clips": [
                {
                    "label": "hook_hook",
                    "source_file": "/p/001/raw/IMG_1816.MOV",
                    "source_in": _seconds(25),
                    "source_out": _seconds(97),
                    "timeline_in": 0.0,
                    "timeline_out": _seconds(72),
                    "timeline_in_frame": 0,
                    "timeline_out_frame": 72,
                },
                {
                    "label": "speech_7_seg0",
                    "source_file": "/p/001/raw/IMG_1816.MOV",
                    "source_in": _seconds(3016),
                    "source_out": _seconds(3495),
                    "timeline_in": _seconds(559),
                    "timeline_out": _seconds(1038),
                    "timeline_in_frame": 559,
                    "timeline_out_frame": 1038,
                },
                {
                    "label": "speech_9_seg0",
                    "source_file": "/p/001/raw/IMG_1817.MOV",
                    "source_in": _seconds(654),
                    "source_out": _seconds(725),
                    "timeline_in": _seconds(1164),
                    "timeline_out": _seconds(1235),
                    "timeline_in_frame": 1164,
                    "timeline_out_frame": 1235,
                },
            ],
        },
        "V2": {
            "label": "B-Roll",
            "clips": [
                {
                    "label": "interjection_7",
                    "source_file": "/p/001/raw/IMG_1811.MOV",
                    "source_in": _seconds(50),
                    "source_out": _seconds(125),
                    "timeline_in": _seconds(705),
                    "timeline_out": _seconds(780),
                    "timeline_in_frame": 705,
                    "timeline_out_frame": 780,
                    "video_only": True,
                },
            ],
        },
        "A2": {
            "label": "Music",
            "clips": [
                {
                    "label": "background_music",
                    "source_file": "/p/001/music/_background music_ rise - "
                    "uplifting piano _inspiring _ beautiful_ _ _ "
                    "motivation.wav",
                    "source_in": 0.0,
                    "source_out": 59.437,
                    "timeline_in": 0.0,
                    "timeline_out": 59.437,
                },
            ],
        },
        "A3": {
            "label": "SFX",
            "clips": [
                {
                    "label": "sfx_001",
                    "sfx_id": "whoosh_impact",
                    "source_file": "/p/sfx/whoosh_impact.mp3",
                    "source_in": 0.714,
                    "timeline_in": 34.615,
                    "timeline_out": 34.865,
                    "timeline_in_frame": 1038,
                    "timeline_out_frame": 1046,
                },
            ],
        },
    },
    "subtitle_overlay": {
        "segments": [
            {
                "overlay_path": "/p/001/pipeline_output/steps/4_05_render_subtitles/"
                "sub_block_hook.mov",
                "timeline_start": 0.0,
                "timeline_end": _seconds(72),
                "source_in_frame": 0,
                "source_out_frame": 72,
            },
            {
                "overlay_path": "/p/001/pipeline_output/steps/4_05_render_subtitles/"
                "sub_block_7.mov",
                "timeline_start": _seconds(559),
                "timeline_end": _seconds(1038),
                "source_in_frame": 15,
                "source_out_frame": 495,
            },
            {
                "overlay_path": "/p/001/pipeline_output/steps/4_05_render_subtitles/"
                "sub_block_9.mov",
                "timeline_start": _seconds(1164),
                "timeline_end": _seconds(1235),
                "source_in_frame": 15,
                "source_out_frame": 86,
            },
        ]
    },
}


def _placement(found, label):
    hits = [p for p in found if p.label == label]
    assert len(hits) == 1, f"{label!r} matched {len(hits)} placements"
    return hits[0]


# ── The enumeration ─────────────────────────────────────────────────


# ── Reading the manifest ────────────────────────────────────────────


def test_a_bookend_card_or_unknown_track_is_not_stamped_and_says_why():
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["tracks"]["V1"]["clips"].append(
        {
            "label": "bookend_outro",
            "bookend": "outro",
            "source_file": "/p/001/assets/outro.mov",
            "source_in": 0.0,
            "source_out": 2.0,
            "timeline_in": 59.4,
            "timeline_out": 61.4,
            "timeline_in_frame": 1783,
            "timeline_out_frame": 1843,
        }
    )
    card = _placement(td.placements(manifest), "bookend_outro")
    assert card.stamped is False
    assert card.step == ""
    assert card.unstamped_reason == td.UNSTAMPED_PLACEMENTS["bookend_card"]
    assert card.decision_id == ""

    # An unknown track is reported, not guessed.
    manifest["tracks"]["V9"] = {
        "label": "?",
        "clips": [
            {
                "label": "mystery",
                "source_file": "/p/x.mov",
                "timeline_in_frame": 0,
                "timeline_out_frame": 10,
            }
        ],
    }
    mystery = _placement(td.placements(manifest), "mystery")
    assert mystery.stamped is False
    assert mystery.step == ""
    assert mystery.unstamped_reason == td.UNSTAMPED_PLACEMENTS["unknown_track"]


def test_the_linked_speech_track_is_not_a_second_placement():
    # A1 is built from the same V1 clip dicts. Counting it would stamp
    # two records where the timeline has one clip.
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["tracks"]["A1"] = {
        "label": "Speech",
        "clips": manifest["tracks"]["V1"]["clips"],
    }
    assert len(td.placements(manifest)) == len(td.placements(MANIFEST))
    assert td.LINKED_AUDIO_OF["A1"] == "V1"


# ── The ledger ──────────────────────────────────────────────────────


# ── Matching a marker's clip back to a placement ────────────────────


def test_a_clip_from_another_build_matches_nothing(tmp_path):
    td.write_ledger(tmp_path, MANIFEST)
    ledger = td.read_ledger(tmp_path)
    assert (
        td.placement_for_clip(
            ledger,
            {
                "source_file": "/p/001/raw/IMG_9999.MOV",
                "source_start": 10,
                "source_end": 20,
                "timeline_start": 0,
            },
        )
        is None
    )


# ── The stamp, against a fake Resolve ───────────────────────────────

CAPTAIN_MARKER = {
    "color": "Blue",
    "duration": 1,
    "note": "this clip is honestly like broll of nothing, im confused why "
    "it was chosen and added here",
    "name": "Marker 1",
    "customData": "",
}


def _stamped(tmp_path, markers, frames_refused=()):
    td.write_ledger(tmp_path, MANIFEST)
    timeline = FakeTimeline("Pipeline_Edit")
    for frame, marker in markers.items():
        timeline.AddMarker(
            frame,
            marker["color"],
            marker["name"],
            marker["note"],
            marker["duration"],
            marker["customData"],
        )
    timeline.refuse_marker_update_frames = set(frames_refused)
    timeline.forbid_marker_add = True
    report = td.stamp_timeline(timeline, td.read_ledger(tmp_path))
    return timeline, report


def test_a_hand_written_note_survives_stamping_byte_for_byte(tmp_path):
    before = dict(CAPTAIN_MARKER)
    timeline, report = _stamped(tmp_path, {744: dict(CAPTAIN_MARKER)})
    after = timeline.markers[744]
    for field in ("name", "note", "color", "duration"):
        assert after[field] == before[field], f"{field} was changed"
    assert report.markers_stamped == 1
    assert after["customData"] != ""


def test_stamping_leaves_what_it_does_not_own_exactly_as_it_was(tmp_path):
    """Another writer's record survives the stamp, and a marker over
    nothing this build placed is not touched at all."""
    envelope = marker_payload.new_envelope()
    still = {
        "kind": marker_payload.KIND_STILL,
        "writer": "capture_frame",
        "writer_version": 1,
        "id": "still_1",
        "at": "2026-08-28T00:00:00Z",
        "path": "marker_feedback/stills/x.png",
    }
    marker_payload.merge_record(envelope, still)
    marker = dict(CAPTAIN_MARKER, customData=marker_payload.dumps(envelope))
    timeline, _ = _stamped(tmp_path / "foreign", {744: marker})
    after = marker_payload.parse(timeline.markers[744]["customData"])
    assert marker_payload.records_of(after, marker_payload.KIND_STILL) == [still]

    timeline, report = _stamped(tmp_path / "nothing",
                                {9000: dict(CAPTAIN_MARKER)})
    assert timeline.markers[9000] == CAPTAIN_MARKER
    assert report.markers_stamped == 0
    assert report.skipped[0]["frame"] == 9000
    assert "no placement" in report.skipped[0]["reason"]


def test_stamping_twice_replaces_rather_than_accumulates(tmp_path):
    timeline, _ = _stamped(tmp_path, {744: dict(CAPTAIN_MARKER)})
    first = marker_payload.parse(timeline.markers[744]["customData"])
    td.stamp_timeline(timeline, td.read_ledger(tmp_path))
    second = marker_payload.parse(timeline.markers[744]["customData"])
    assert len(second["records"]) == len(first["records"])
    assert [r["id"] for r in second["records"]] == [r["id"] for r in first["records"]]


def test_resolve_refusing_the_write_is_reported_not_swallowed(tmp_path):
    timeline, report = _stamped(
        tmp_path, {744: dict(CAPTAIN_MARKER)}, frames_refused=[744]
    )
    assert report.markers_stamped == 0
    assert report.refused[0]["frame"] == 744
    assert timeline.markers[744]["customData"] == ""


# ── The routing consumes it ─────────────────────────────────────────


def _project(tmp_path, notes, with_ledger=True):
    layout = ProjectLayout(tmp_path)
    layout.write_path(
        Area.MARKER_FEEDBACK,
        f"Pipeline_Edit.20260828T221238Z{PULL_FILE_SUFFIX}",
    ).write_text(
        json.dumps(
            {
                "format": "marker_feedback/1",
                "timeline": "Pipeline_Edit",
                "note_count": len(notes),
                "notes": notes,
            }
        ),
        encoding="utf-8",
    )
    if with_ledger:
        td.write_ledger(tmp_path, MANIFEST)
    return str(tmp_path)


UNROUTABLE_WORDS = "this bit just doesn't work for me, can we try something else"


def _wordless(note):
    out = json.loads(json.dumps(note))
    out["name"] = "Marker 1"
    out["note"] = UNROUTABLE_WORDS
    out["text"] = f"Marker 1\n\n{UNROUTABLE_WORDS}"
    return out


def test_the_stamp_does_not_overrule_the_captain_s_own_words(tmp_path):
    # The measured case: the blurry note sits on a V1 A-roll clip whose
    # stamp is speech_sequence, and a blur is decided in plan_vfx or
    # plan_transitions. A stamp that ranked above the words would have
    # sent it to the step that chose the passage.
    blurry = next(
        n for n in marker_routing.route_project(_project(tmp_path, [AMBIGUOUS_NOTE]))
    )
    assert blurry.decision["step"] == "speech_sequence"
    assert "speech_sequence" not in blurry.steps
    assert blurry.outcome == marker_routing.OUTCOME_AMBIGUOUS
