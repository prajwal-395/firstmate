"""The master builder obeys the SOP, and the verifier reads it back.

History: docs/evidence/resolve_test_history.md#test_timeline_sop_conformance.
"""

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from library.steps.step_6_01_render import resolve_build_timeline as _builder
from library.steps.step_6_01_render.resolve_build_timeline import build_timeline
from library.tools.timeline_conformance import (
    CHECKS,
    verify_timeline,
)
from library.tools.timeline_layout import plan_layout
from tests.resolve_double import (
    FakeProject,
    FakeResolve,
    FakeTimeline,
    TimelineItemSpec,
    builder_paths_exist,
)


def _channel_mapping(channel):
    return json.dumps(
        {
            "embedded_audio_channels": 4,
            "linked_audio": {},
            "track_mapping": {
                "1": {"channel_idx": [channel], "mute": False, "type": "mono"}
            },
        }
    )


@pytest.fixture
def fake_world():
    project = FakeProject("FakeProject")
    return {
        "timeline": None,
        "pool": project.GetMediaPool(),
        "project": project,
        "resolve": FakeResolve(project),
    }


def _run_builder(fake_world, manifest, monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "DaVinciResolveScript",
        type(
            "M", (), {"scriptapp": staticmethod(lambda name: fake_world["resolve"])}
        )(),
    )
    # Media exists on disk for the import pass.
    fusion_proc = MagicMock()
    fusion_proc.returncode = 0
    fusion_proc.stdout = ""
    fusion_proc.stderr = ""
    subprocess_proxy = SimpleNamespace(
        run=lambda *args, **kwargs: fusion_proc,
        TimeoutExpired=subprocess.TimeoutExpired,
    )
    # Pool lookup must find the pre-registered items.
    with builder_paths_exist(_builder), patch.object(
        _builder, "subprocess", subprocess_proxy
    ):
        result = build_timeline(manifest)
    fake_world["timeline"] = fake_world["project"].GetCurrentTimeline()
    return result


def _two_angle_manifest():
    def clip(path, angle, tin):
        return {
            "source_file": path,
            "source_in": 0.0,
            "source_out": 2.0,
            "timeline_in_frame": tin,
            "timeline_out_frame": tin + 48,
            "angle": angle,
            "label": f"{angle}_1",
        }

    return {
        "project": {
            "name": "SOP",
            "resolution": [3840, 2160],
            "frame_rate": 23.976,
            "duration_seconds": 10.0,
        },
        "angles": [
            {
                "key": "speakerone",
                "label": "SpeakerOne",
                "speech_name": "SpeakerOne CH1",
                "program_channel": 1,
            },
            {
                "key": "speakertwo",
                "label": "SpeakerTwo",
                "speech_name": "SpeakerTwo CH1",
                "program_channel": 1,
            },
        ],
        "tracks": {
            "V1": {
                "clips": [
                    clip("/media/LC4930.MXF", "speakerone", 0),
                    clip("/media/LCATL0011.MXF", "speakertwo", 48),
                ]
            }
        },
    }


def test_two_angles_place_on_two_picture_rows_and_two_speech_rows(
    fake_world, monkeypatch
):
    """Defect 2: two cameras mean TWO audio rows, each with its program stream."""
    result = _run_builder(fake_world, _two_angle_manifest(), monkeypatch)
    assert result["success"], result["errors"]

    tl = fake_world["timeline"]
    assert tl.GetTrackCount("video") == 2, "caption-less proof has no V3"
    assert tl.GetTrackCount("audio") == 2
    v1 = tl.GetItemListInTrack("video", 1)
    v2 = tl.GetItemListInTrack("video", 2)
    a1 = tl.GetItemListInTrack("audio", 1)
    a2 = tl.GetItemListInTrack("audio", 2)
    assert len(v1) == len(a1) == 1 and len(v2) == len(a2) == 1
    assert "LC4930" in v1[0].GetName() and "LCATL" in v2[0].GetName()
    # Pairs are linked picture-to-speech.
    pair_calls = [c for c in tl.link_calls if len(c[0]) == 2 and c[1]]
    assert len(pair_calls) == 2
    linked_names = sorted(
        n for c in pair_calls for n in (c[0][0].GetName(), c[0][1].GetName())
    )
    assert linked_names == sorted(
        [v1[0].GetName(), a1[0].GetName(), v2[0].GetName(), a2[0].GetName()]
    )


def test_every_row_is_named_from_the_plan(fake_world, monkeypatch):
    """Defect 6: no 'Video 1' / 'Audio 1' rows survive the build."""
    result = _run_builder(fake_world, _two_angle_manifest(), monkeypatch)
    assert result["success"], result["errors"]
    tl = fake_world["timeline"]
    assert tl.GetTrackName("video", 1) == "SpeakerOne"
    assert tl.GetTrackName("video", 2) == "SpeakerTwo"
    assert tl.GetTrackName("audio", 1) == "SpeakerOne CH1"
    assert tl.GetTrackName("audio", 2) == "SpeakerTwo CH1"


def test_caption_inside_speech_joins_one_three_group(fake_world, monkeypatch):
    """Defect 5 (captions): the caption, its speech AND its picture link
    in a single call - a later pair-call would break the group."""
    manifest = _two_angle_manifest()
    manifest["subtitle_overlay"] = {
        "segments": [
            {
                "overlay_path": "/media/cap_speakerone.mov",
                "geometry": "full",
                "timeline_start": 0.5,
                "timeline_end": 1.5,
                "total_frames": 24,
            }
        ]
    }
    result = _run_builder(fake_world, manifest, monkeypatch)
    assert result["success"], result["errors"]

    tl = fake_world["timeline"]
    assert tl.GetTrackName("video", 3) == "Subtitles"
    triples = [c for c in tl.link_calls if len(c[0]) == 3 and c[1]]
    assert len(triples) == 1, f"expected one three-group link call, saw {tl.link_calls}"
    members = triples[0][0]
    kinds = sorted((m.GetStart(), m.GetEnd()) for m in members)
    assert len(kinds) == 3
    # The group holds: nothing was re-linked afterwards to break it.
    cap = tl.GetItemListInTrack("video", 3)[0]
    assert len(cap.GetLinkedItems()) == 2


def test_non_program_stream_is_deleted_not_placed(fake_world, monkeypatch):
    """Defect 3 at placement: a stray item carrying the wrong channel is
    removed from the timeline, and the build says so."""
    fake_world["pool"].audio_channels = (2,)  # Resolve hands back CH2, program is CH1
    result = _run_builder(fake_world, _two_angle_manifest(), monkeypatch)
    tl = fake_world["timeline"]
    assert tl.deleted_items, "stray stream items must be deleted"
    assert result["stream_enforcement"]["deleted"], (
        "the build must record what it removed"
    )
    assert tl.GetTrackCount("audio") == 0


def test_verifier_reads_back_a_clean_build(fake_world, monkeypatch):
    """The verifier passes the timeline the conformed builder just made."""
    result = _run_builder(fake_world, _two_angle_manifest(), monkeypatch)
    assert result["success"], result["errors"]
    plan = plan_layout(result["track_plan"]["material"])
    report = verify_timeline(fake_world["timeline"], plan=plan)
    assert report["passed"], report["violations"]
    assert report["checks_skipped"] == []


def test_verifier_flags_every_sop_violation():
    """Each of defects 2-6, as the verifier sees it on a bad timeline."""
    # V1 a-roll picture, unlinked; A1 its speech, unlinked AND carrying
    # the wrong stream. V2 is the plan's caption row with a caption
    # inside the speech span, unlinked. V3 is an unplanned blank row
    # with a default name; A2 repeats A1's role name.
    pic = TimelineItemSpec("LC4930.MXF", 0, 100, path="/media/LC4930.MXF")
    speech = TimelineItemSpec(
        "LC4930.MXF",
        0,
        100,
        path="/media/LC4930.MXF",
        source_audio_channel_mapping=_channel_mapping(3),
    )
    cap = TimelineItemSpec("cap.mov", 10, 50, path="/media/cap.mov")
    dup = TimelineItemSpec("LC4930.MXF", 0, 100, path="/media/LC4930.MXF")
    tl = FakeTimeline(
        "Fake",
        video=[("SpeakerOne", [pic]), ("Subtitles", [cap]), ("Video 3", [])],
        audio=[("SpeakerOne CH1", [speech]), ("SpeakerOne CH1", [dup])],
    )

    plan = plan_layout(
        {
            "angles": [
                {
                    "key": "speakerone",
                    "label": "SpeakerOne",
                    "speech_name": "SpeakerOne CH1",
                    "program_channel": 1,
                }
            ],
            "has_broll": False,
            "caption_spans": [(0, 200)],
            "mg_spans": [],
            "has_generators": False,
            "timed_text_spans": [],
            "music_spans": [],
            "sfx_spans": [],
        }
    )
    report = verify_timeline(tl, plan=plan)
    assert not report["passed"]
    by_check = {v["check"] for v in report["violations"]}
    assert "empty_track" in by_check  # defect 4: V2
    assert "unnamed_track" in by_check  # defect 6: "Video 2"
    assert "duplicate_role" in by_check  # two rows named "SpeakerOne CH1"
    assert "aroll_unlinked" in by_check  # defect 5: picture+speech
    assert "caption_unlinked" in by_check  # defect 5: caption in span
    assert "program_stream" in by_check  # defects 2/3: CH3 placed, CH1 due
    assert set(CHECKS) <= by_check | set(report["checks_run"])


def test_verifier_without_a_plan_runs_structure_only_and_says_so():
    tl = FakeTimeline()
    report = verify_timeline(tl)
    assert "aroll_linked" in report["checks_skipped"]
    assert "program_stream" in report["checks_skipped"]
    assert "no_empty_tracks" in report["checks_run"]


# ── a HELD FRAME is not an unlinked picture ─────────────────────
# Measured 2026-09-12 rebuilding the captain's field-test project:
# Reel 09 placed correctly through the variant path and this verifier
# removed it, naming the freeze tail - "Picture item at 1650 on SpeakerTwo
# links to nothing". A hold is rendered onto the ending shot's own
# a-roll row and carries no audio anywhere on the timeline, so it can
# never be linked to anything. `reel_ending.is_freeze_path` is the
# convention's one reader.


def _timeline_with_tail(tail_name, tail_pool):
    pic = TimelineItemSpec("LC4930.MXF", 0, 100, path="/media/LC4930.MXF")
    speech = TimelineItemSpec(
        "LC4930.MXF",
        0,
        100,
        path="/media/LC4930.MXF",
        source_audio_channel_mapping=_channel_mapping(1),
    )
    tail = TimelineItemSpec(tail_name, 100, 119, path=tail_pool)
    tl = FakeTimeline(
        "Fake",
        video=[("SpeakerOne", [pic, tail]), ("Subtitles", [])],
        audio=[("SpeakerOne CH1", [speech])],
    )
    placed_pic = tl.GetItemListInTrack("video", 1)[0]
    placed_speech = tl.GetItemListInTrack("audio", 1)[0]
    tl.SetClipsLinked([placed_pic, placed_speech], True)
    plan = plan_layout(
        {
            "angles": [
                {
                    "key": "speakerone",
                    "label": "SpeakerOne",
                    "speech_name": "SpeakerOne CH1",
                    "program_channel": 1,
                }
            ],
            "has_broll": False,
            "caption_spans": [(0, 200)],
            "mg_spans": [],
            "has_generators": False,
            "timed_text_spans": [],
            "music_spans": [],
            "sfx_spans": [],
        }
    )
    return tl, plan


def test_held_frame_is_not_an_unlinked_picture():
    tl, plan = _timeline_with_tail(
        "reel_freeze_8da72bf764.mov", "/renders/reel_freeze_8da72bf764.mov"
    )
    report = verify_timeline(tl, plan=plan)
    assert "aroll_unlinked" not in {v["check"] for v in report["violations"]}, (
        f"the hold must not read as a defect: {report['violations']}"
    )


def test_an_unlinked_picture_that_is_not_a_hold_still_fails():
    """The gate can still fail: the identical timeline with the tail
    named off the hold convention reports the violation again."""
    tl, plan = _timeline_with_tail("LC4931.MXF", "/media/LC4931.MXF")
    report = verify_timeline(tl, plan=plan)
    assert "aroll_unlinked" in {v["check"] for v in report["violations"]}
