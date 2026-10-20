"""Defect: replacement based edits must share one verified carry contract."""

from __future__ import annotations

import pytest

from library.tools import edit_state_preservation as preservation


class _Item:
    def __init__(self):
        self.properties = {"Pan": -24.0, "Tilt": 3.0, "ZoomX": 1.4}
        self.enabled = True
        self.color = "Blue"
        self.markers = {}
        self.flags = []
        self.comp_count = 0
        self.nodes = 3
        self.uid = "resolve-item-new"
        self.linked = []

    def GetProperty(self, key=None):
        return self.properties if key is None else self.properties.get(key)

    def GetFusionCompCount(self):
        return self.comp_count

    def GetFlagList(self):
        return self.flags

    def GetClipEnabled(self):
        return self.enabled

    def SetClipEnabled(self, value):
        self.enabled = value
        return True

    def GetClipColor(self):
        return self.color

    def SetClipColor(self, value):
        self.color = value
        return True

    def ClearClipColor(self):
        self.color = ""
        return True

    def GetMarkers(self):
        return self.markers

    def AddMarker(self, frame, color, name, note, duration, custom_data=""):
        self.markers[int(frame)] = {
            "color": color, "name": name, "note": note,
            "duration": duration, "customData": custom_data,
        }
        return True

    def AddFlag(self, color):
        self.flags.append(color)
        return True

    def ClearFlags(self, _color):
        self.flags = []
        return True

    def GetMediaPoolItem(self):
        return None

    def GetLinkedItems(self):
        return list(self.linked)


class _Timeline:
    def __init__(self, video, audio):
        self.items = {"video": video, "audio": audio}

    def GetTrackCount(self, track_type):
        return 1

    def GetItemListInTrack(self, track_type, _track_index):
        return self.items[track_type]

    def SetClipsLinked(self, items, _linked):
        for item in items:
            item.linked = [partner for partner in items if partner is not item]
        return True

    def GetNumNodes(self):
        return self.nodes

    def GetKeyframes(self, key):
        return getattr(self, "keyframes", {}).get(key, [])

    def CopyGrades(self, targets):
        for target in targets:
            target.nodes = self.nodes

    def GetUniqueId(self):
        return self.uid

    def GetName(self):
        return "Shot"

    def GetTrackTypeAndIndex(self):
        return ["video", 1]

    def GetSourceStartFrame(self):
        return 100

    def GetSourceEndFrame(self):
        return 200

    def GetStart(self):
        return 20

    def GetEnd(self):
        return 120


def test_replacement_contract_carries_and_verifies_approved_clip_state(
        monkeypatch):
    source = _Item()
    target = _Item()
    target.properties = {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0}
    target.enabled = False
    target.color = ""
    target.nodes = 1
    state = preservation.capture(source)
    writes = []

    def set_property(item, key, value, **_kwargs):
        writes.append((key, value))
        item.properties[key] = value

    monkeypatch.setattr(
        "library.tools.transform_write_log.set_property", set_property)

    assert preservation.restore(target, state, source_in=100,
                                source_out=200) == {}
    assert target.properties["Pan"] == -24.0
    assert target.properties["Tilt"] == 3.0
    assert target.enabled is True
    assert target.color == "Blue"
    assert target.nodes == 3
    assert state["native_object_id"] == "resolve-item-new"
    assert state["editorial_identity"]["record_span"] == (20, 120)
    assert writes == [("Pan", -24.0), ("Tilt", 3.0), ("ZoomX", 1.4)]


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("comp_count", 1, "Fusion compositions"),
    ("flags", ["Blue"], "flags"),
    ("markers", {10: {"name": "Keep"}}, "clip markers"),
    ("keyframes", {"Pan": [{"frame": 5, "value": 10}]},
     "animated picture properties"),
])
def test_replacement_contract_refuses_unrestorable_clip_state(
        field, value, reason):
    item = _Item()
    setattr(item, field, value)

    with pytest.raises(preservation.StatePreservationError, match=reason):
        preservation.capture(item)


def test_replacement_contract_carries_flags_and_only_retained_source_markers(
        monkeypatch):
    source = _Item()
    source.flags = ["Blue"]
    source.markers = {
        120: {"color": "Red", "name": "Keep", "note": "one",
              "duration": 2, "customData": "a"},
        220: {"color": "Green", "name": "Trimmed", "note": "two",
              "duration": 1, "customData": "b"},
    }
    target = _Item()
    monkeypatch.setattr(
        "library.tools.transform_write_log.set_property",
        lambda *_args, **_kwargs: None)

    assert preservation.restore(
        target, preservation.capture(source), source_in=100, source_out=150
    ) == {}
    assert target.flags == ["Blue"]
    assert sorted(target.markers) == [120]


def test_replacement_contract_restores_linked_audio_relationship():
    source, target, audio = _Item(), _Item(), _Item()
    source.linked = [audio]
    audio.uid = "audio-native-id"
    target.uid = "replacement-native-id"
    state = preservation.capture(source)
    timeline = _Timeline([target], [audio])

    diff = preservation.restore_clip_metadata(
        target, state, source_in=100, source_out=200, timeline=timeline)

    assert diff == {}
    assert [item.GetUniqueId() for item in target.GetLinkedItems()] == [
        "audio-native-id"]
