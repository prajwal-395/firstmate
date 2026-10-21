"""The read-back carries the LINK STATE, so "the audio did not move" is
a comparison and not a claim.

The defect this names: the shadow's light read (the fingerprint the
watcher judges a change by) carried no link state, so a picture whose
audio partner had moved hashed as the same arrangement.  The linked
partner's own GAIN was not carried either, so "the audio's in/out, gain
and link state are unchanged" could not be proven from a read-back.
"""
from __future__ import annotations

from library.tools import reel_read, timeline_shadow
from tests.resolve_double import (
    FakeTimeline,
    make_pool_clip,
    make_project,
    place_clip,
)


def _linked_reel():
    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    video = place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    audio = place_clip(timeline, make_pool_clip("a.mov"), 0, 47,
                       kind="audio", track_index=1)
    video._linked_items = [audio]
    audio._linked_items = [video]
    project.SetCurrentTimeline(timeline)
    return project, timeline


def test_the_light_read_carries_the_link_state():
    _project, timeline = _linked_reel()

    state = reel_read.read_light_state(timeline)

    video = next(clip for track in state["tracks"]
                 if track["type"] == "video" for clip in track["clips"])
    audio = next(clip for track in state["tracks"]
                 if track["type"] == "audio" for clip in track["clips"])
    assert video["linked_unique_ids"] == [audio["unique_id"]]
    assert audio["linked_unique_ids"] == [video["unique_id"]]


def test_the_shadow_light_projection_carries_the_link_state():
    _project, _timeline = _linked_reel()

    snapshot = {"timeline": "Reel 09", "start_frame": 0, "end_frame": 48,
                "tracks": [{"type": "video", "index": 1, "name": "V1",
                            "clips": [{"unique_id": "v1", "name": "a.mov",
                                       "record_in": 0, "record_out": 48,
                                       "duration": 48,
                                       "source_in_frame": 0,
                                       "source_out_frame": 48,
                                       "left_offset": 0, "right_offset": 0,
                                       "source_file": "/tmp/a.mov",
                                       "media_pool_item_id": "m1",
                                       "enabled": True, "clip_color": "",
                                       "flags": [], "speed": 100.0,
                                       "transform": {},
                                       "linked_unique_ids": ["a1", "v1"],
                                       "markers": []}]}],
                "markers": {"timeline": [], "notes": []}}

    light = timeline_shadow.light_state_of_snapshot(snapshot)

    assert light["tracks"][0]["clips"][0]["linked_unique_ids"] == ["a1", "v1"]


def test_the_full_read_carries_the_linked_partner_own_state():
    _project, timeline = _linked_reel()

    detail = reel_read.clip_detail(
        timeline.GetItemListInTrack("video", 1)[0], "video", 1)

    assert detail["linked_unique_ids"]
    partner = detail["linked_clips"][0]
    assert partner["track_type"] == "audio"
    assert partner["record_in"] == 0 and partner["record_out"] == 48
    assert partner["source_in_frame"] == 0
    assert "volume" in partner
