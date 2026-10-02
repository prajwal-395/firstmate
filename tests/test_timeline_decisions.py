"""Stamping each clip with the decision that produced it.

History: docs/evidence/resolve_test_history.md#test_timeline_decisions.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import marker_payload, marker_routing
from library.tools import timeline_decisions as td
from library.tools.marker_feedback import PULL_FILE_SUFFIX
from library.tools.project_layout import (
    Area,
    ProjectLayout,
)
from tests.resolve_double import FakeTimeline
from tests.test_marker_routing import (
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
