"""A picture-only split lands on the exact frame and leaves the audio alone.

The defect this names: the only split Ren had was a staged rebuild that
refused a LINKED item, so a cut on a linked picture could not be placed
at all - and nothing measured whether the audio beside the picture
survived it.  The link state was not part of the read-back either, so
"the audio did not move" was a claim rather than a comparison.
"""
from __future__ import annotations

import pytest

from library.tools import picture_split


class FakePoolItem:
    def __init__(self, path="/tmp/fm_l4_linked.mov"):
        self._path = path

    def GetClipProperty(self, key, default=""):
        if key == "File Path":
            return self._path
        return default


class FakeItem:
    """One timeline item, as Resolve's scripting API answers it."""

    def __init__(self, uid, track_type, record_in, record_out,
                 source_in, source_out, timeline=None, name="clip",
                 volume=0.0, enabled=True):
        self._uid = uid
        self._track_type = track_type
        self._track_index = 1
        self._record_in = record_in
        self._record_out = record_out
        self._source_in = source_in
        self._source_out = source_out
        self._name = name
        self._volume = volume
        self._enabled = enabled
        self._linked = []
        self._timeline = timeline

    def GetUniqueId(self):
        return self._uid

    def GetName(self):
        return self._name

    def GetTrackTypeAndIndex(self):
        return (self._track_type, self._track_index)

    def GetStart(self):
        return self._record_in

    def GetEnd(self):
        return self._record_out

    def GetSourceStartFrame(self):
        return self._source_in

    def GetSourceEndFrame(self):
        return self._source_out

    def GetClipEnabled(self):
        return self._enabled

    def GetProperty(self):
        return {"Volume": self._volume}

    def GetFades(self):
        return {"FadeIn": 0, "FadeOut": 0}

    def GetMediaPoolItem(self):
        return FakePoolItem()

    def GetLinkedItems(self):
        return list(self._linked)

    def SplitAtFrame(self, frame):
        """Split at a record frame; the second half is placed after it."""
        frame = int(frame)
        second = FakeItem(
            f"{self._uid}-half2", self._track_type, frame,
            self._record_out, self._source_in + (frame - self._record_in),
            self._source_out, timeline=self._timeline,
            name=self._name, volume=self._volume)
        self._record_out = frame
        self._source_out = self._source_in + (frame - self._record_in)
        if self._timeline is not None:
            self._timeline.items.append(second)
        return second


class FakeTimeline:
    def __init__(self):
        self.items = []

    def GetTrackCount(self, track_type):
        return 1

    def GetTrackName(self, track_type, index):
        return f"{track_type}{index}"

    def GetItemListInTrack(self, track_type, index):
        if track_type == "video":
            return [item for item in self.items
                    if item._track_type == "video"]
        return [item for item in self.items
                if item._track_type == "audio"]


def linked_pair(timeline=None, volume=0.0):
    """A picture and the audio beside it, linked to each other."""
    video = FakeItem("v1", "video", 0, 240, 0, 240, timeline=timeline,
                     name="A-roll", volume=volume)
    audio = FakeItem("a1", "audio", 0, 240, 0, 240, timeline=timeline,
                     name="A-roll audio", volume=volume)
    video._linked = [audio]
    audio._linked = [video]
    if timeline is not None:
        timeline.items.extend([video, audio])
    return video, audio


def _read_back(timeline, before_state, audio_before, record_frame=120):
    halves = [state for state in picture_split.track_item_states(
        timeline, "video", 1)
        if picture_split._overlaps(state, before_state["record_in"],
                                   before_state["record_out"])]
    return picture_split.verify_split(
        {"before": before_state,
         "record_frame": record_frame,
         "audio": audio_before},
        {"halves": halves,
         "audio": picture_split.audio_track_states(timeline)},
        record_frame)


def test_a_picture_split_lands_on_the_exact_frame_and_leaves_the_audio_alone():
    timeline = FakeTimeline()
    video, _audio = linked_pair(timeline)
    audio_before = picture_split.audio_track_states(timeline)
    before_state = picture_split.item_state(video)

    receipt = picture_split.split_picture_item(video, 120)

    assert receipt["record_frame"] == 120
    assert receipt["split_called_with"] == "SplitAtFrame"
    verdict = _read_back(timeline, before_state, audio_before)
    assert verdict["result"] == "passed", verdict["checks"]
    assert verdict["placement"]["measured"] == {
        "first_record_in": 0, "first_record_out": 120,
        "second_record_in": 120, "second_record_out": 240,
        "first_source_in": 0, "first_source_out": 120,
        "second_source_in": 120, "second_source_out": 240,
    }
    assert verdict["audio_moved"] == []


def test_a_split_that_moved_the_audio_is_reported_as_failed():
    timeline = FakeTimeline()
    video, audio = linked_pair(timeline)
    audio_before = picture_split.audio_track_states(timeline)
    before_state = picture_split.item_state(video)

    picture_split.split_picture_item(video, 120)
    audio._record_out = 120          # the audio moved with the picture

    verdict = _read_back(timeline, before_state, audio_before)
    assert verdict["result"] == "failed"
    assert [row["field"] for row in verdict["audio_moved"]] == ["record_out"]
    failed = {row["check"] for row in verdict["checks"]
              if row["result"] == "failed"}
    assert "audio_item_unchanged" in failed


def test_a_split_that_cut_the_audio_as_well_is_reported_as_failed():
    timeline = FakeTimeline()
    video, audio = linked_pair(timeline)
    audio_before = picture_split.audio_track_states(timeline)
    before_state = picture_split.item_state(video)

    picture_split.split_picture_item(video, 120)
    audio._record_out = 120
    second = FakeItem("a2", "audio", 120, 240, 120, 240, timeline=timeline)
    timeline.items.append(second)

    verdict = _read_back(timeline, before_state, audio_before)
    assert verdict["result"] == "failed"
    failed = {row["check"] for row in verdict["checks"]
              if row["result"] == "failed"}
    assert "audio_not_split" in failed


def test_the_link_state_names_the_group_and_each_partner():
    video, _audio = linked_pair()

    state = picture_split.link_state(video)

    assert state["linked_unique_ids"] == ["a1"]
    assert [partner["unique_id"] for partner in state["partners"]] == ["a1"]
    assert state["partners"][0]["track_type"] == "audio"
    assert picture_split.audio_partners(video)[0]["unique_id"] == "a1"


def test_a_source_frame_outside_the_item_is_refused_before_any_write():
    video, _audio = linked_pair()

    with pytest.raises(picture_split.PictureSplitRefused,
                       match="not inside the item"):
        picture_split.split_picture_item(video, 240)

    assert video._record_out == 240


def test_an_item_whose_spans_do_not_read_is_refused_rather_than_guessed():
    video, _audio = linked_pair()
    video._source_in = None

    with pytest.raises(picture_split.PictureSplitRefused,
                       match="does not report its source"):
        picture_split.record_frame_for_source_frame(video, 12)
