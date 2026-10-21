"""Defect: a Resolve read that established nothing was reported as a default.

The inventory PRES-001 asks for is an inventory of PROPERTIES, not of
operations - `edit_algebra` already enumerates the operations.  Every
property in it says how well a read can prove it (PRES-004), and every
reading says whether it established a value, found a confirmed default,
or could not be read at all (PRES-005).

A read that could not establish a property is NOT a property at its
default: reporting 0.0 / 1.0 / 100.0 for it hands a consumer a value
nobody measured, and a preservation check that compares the two will
restore that default over an approved value it never saw.
"""

from __future__ import annotations

import pytest

from library.tools import edit_algebra, reel_read
from library.tools import resolve_property_inventory as inventory


class _NodeGraph:
    def __init__(self, nodes):
        self._nodes = nodes

    def GetNumNodes(self):
        return self._nodes

    def GetLUT(self, node):
        return f"/luts/{node}.cube"


class _Item:
    """A Resolve timeline item, with every reading this lane needs."""

    def __init__(self, *, properties=None, enabled=True, fades=None,
                 nodes=0, linked=(), markers=None):
        self.properties = properties
        self.enabled = enabled
        self.fades = fades
        self.nodes = nodes
        self.linked = list(linked)
        self.markers = markers or {}

    # ── identity ──
    def GetUniqueId(self):
        return "uid-1"

    def GetName(self):
        return "Shot A"

    # ── placement ──
    def GetStart(self):
        return 10

    def GetEnd(self):
        return 110

    def GetDuration(self):
        return 100

    # ── source ──
    def GetSourceStartFrame(self):
        return 1000

    def GetSourceEndFrame(self):
        return 1100

    def GetLeftOffset(self):
        return 0

    def GetRightOffset(self):
        return 0

    def GetMediaPoolItem(self):
        return None

    def GetTrackTypeAndIndex(self):
        return ["video", 1]

    # ── state ──
    def GetProperty(self):
        if self.properties is None:
            raise RuntimeError("the item's properties could not be read")
        return self.properties

    def GetClipEnabled(self):
        if self.enabled is None:
            raise RuntimeError("the item's enabled state could not be read")
        return self.enabled

    def GetClipColor(self):
        return ""

    def GetFlagList(self):
        return []

    def GetSpeed(self):
        return {"Percentage": 100.0}

    def GetKeyframes(self, key):
        return []

    def GetFades(self):
        if self.fades is None:
            raise AttributeError("GetFades")
        return self.fades

    def GetLinkedItems(self):
        return list(self.linked)

    def GetFusionCompCount(self):
        return 0

    def GetFusionCompNameList(self):
        return []

    def GetNodeGraph(self):
        if self.nodes is None:
            return None
        return _NodeGraph(self.nodes)

    def GetCDL(self):
        return {}

    def GetColorGroup(self):
        return ""

    def GetMarkers(self):
        return dict(self.markers)


def _detail(item, **kwargs):
    return reel_read.clip_detail(item, "video", 1, "V1", **kwargs)


# ── PRES-001: the inventory covers every operation ───────────────────


def test_an_operation_nobody_inventoried_is_a_gap_in_the_inventory():
    """A new algebra operation must be inventoried, or its state is a
    promise nobody made."""
    missing = sorted(set(edit_algebra.ALGEBRA) - set(inventory.operations()))
    assert not missing, (
        "these operations can modify Resolve state but the property "
        f"inventory does not say what they change: {missing}")


def test_every_inventoried_property_names_an_operation_that_can_change_it():
    orphans = [prop.property_id for prop in inventory.PROPERTIES
               if not prop.modified_by]
    assert not orphans, (
        f"these properties name no operation that can change them: {orphans}")


def test_every_inventoried_property_names_a_resolve_surface():
    missing = [prop.property_id for prop in inventory.PROPERTIES
               if not prop.surface]
    assert not missing, (
        f"these properties name no Resolve API that reads them: {missing}")


# ── PRES-004: the unreadable properties are marked as such ───────────


def test_a_property_resolve_cannot_read_is_marked_unreadable():
    for property_id in ("transitions.in", "effects.list", "audio.effects",
                        "audio.automation"):
        prop = inventory.PROPERTY_BY_ID[property_id]
        assert prop.support == inventory.UNREADABLE, (
            f"{property_id} has no getter in Resolve's scripting API, so "
            "it must be marked unreadable rather than reported empty")


def test_an_unreadable_property_reads_as_unavailable_with_a_reason():
    surface = inventory.clip_surface(_detail(_Item(properties={})))
    for property_id in ("transitions.in", "effects.list", "audio.automation"):
        reading = surface[property_id]
        assert reading["status"] == inventory.UNAVAILABLE, (
            f"{property_id} must read as unavailable, not as empty")
        assert reading["reason"], (
            f"{property_id} must say WHY it could not be read")


def test_a_partially_readable_property_is_not_claimed_as_fully_readable():
    assert inventory.PROPERTY_BY_ID["transform.Pan"].support == (
        inventory.PARTIALLY_READABLE)
    assert "current" in inventory.PROPERTY_BY_ID["transform.Pan"].note.lower()


def test_the_audio_level_is_read_under_its_resolve_21_1_spelling():
    """Defect: Resolve 21.1 renamed the item's audio properties -
    `AudioVolume` and `AudioPan`, with their `...Enabled` switches - and
    the inventory looked for `Volume` and `Pan`.  A read that asks for a
    name the property dictionary does not carry reports the property as
    absent, so the inventory reported a level it could never see."""
    surface = inventory.clip_surface(_detail(_Item(properties={
        "Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0,
        "AudioVolume": -6.0, "AudioVolumeEnabled": True,
        "AudioPan": 0.0,
    })))
    assert surface["audio.volume"]["value"] == -6.0
    assert surface["audio.volume"]["status"] == inventory.MEASURED
    assert surface["audio.pan"]["value"] == 0.0


# ── PRES-005: unavailable is not a default ───────────────────────────


def test_a_transform_that_could_not_be_read_is_unavailable_not_the_default():
    item = _Item(properties=None)
    detail = _detail(item)
    surface = inventory.clip_surface(detail)
    assert surface["transform.ZoomX"]["status"] == inventory.UNAVAILABLE, (
        "a clip whose properties could not be read must not report the "
        "default zoom as a measurement")
    assert surface["crop.CropLeft"]["status"] == inventory.UNAVAILABLE
    assert surface["composite.Opacity"]["status"] == inventory.UNAVAILABLE


def test_a_clip_at_its_defaults_reads_as_default_not_as_measured():
    detail = _detail(_Item(properties={
        "Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0,
        "Opacity": 100.0, "CropLeft": 0.0}))
    surface = inventory.clip_surface(detail)
    assert surface["transform.ZoomX"]["status"] == inventory.DEFAULT, (
        "a value equal to the confirmed default is a DEFAULT reading, "
        "which is a different claim from a measurement")
    assert surface["composite.Opacity"]["status"] == inventory.DEFAULT


def test_a_changed_value_reads_as_measured():
    detail = _detail(_Item(properties={"ZoomX": 1.4}))
    surface = inventory.clip_surface(detail)
    assert surface["transform.ZoomX"]["status"] == inventory.MEASURED
    assert surface["transform.ZoomX"]["value"] == 1.4


def test_a_failed_enabled_state_read_is_unavailable_not_true():
    detail = _detail(_Item(properties={}, enabled=None))
    surface = inventory.clip_surface(detail)
    assert surface["enablement.enabled"]["status"] == inventory.UNAVAILABLE, (
        "a rebuild that assumes the default restores a state nobody "
        "approved")


def test_fades_resolve_does_not_expose_are_unavailable_not_zero():
    detail = _detail(_Item(properties={}, fades=None))
    surface = inventory.clip_surface(detail)
    assert surface["fades.item_in"]["status"] == inventory.UNAVAILABLE, (
        "GetFades is absent on this Resolve, so the fade state is "
        "unavailable - zero is a fade somebody could have set")


def test_fades_resolve_reports_are_measured():
    detail = _detail(_Item(properties={}, fades={"FadeIn": 12,
                                                  "FadeOut": 0}))
    surface = inventory.clip_surface(detail)
    assert surface["fades.item_in"]["status"] == inventory.MEASURED
    assert surface["fades.item_in"]["value"] == 12
    assert surface["fades.item_out"]["status"] == inventory.DEFAULT


# ── PRES-002 and PRES-003: the read records the state ───────────────


def test_the_read_records_the_color_node_count():
    detail = _detail(_Item(properties={}, nodes=3))
    assert detail["color"]["node_count"] == 3


def test_the_read_records_the_linked_partners_state_not_only_the_ids():
    partner = _Item(properties={})
    partner.GetUniqueId = lambda: "uid-audio"
    partner.GetTrackTypeAndIndex = lambda: ["audio", 2]
    item = _Item(properties={}, linked=[partner])
    detail = _detail(item)
    assert detail["linked_unique_ids"] == ["uid-audio"]
    assert detail["linked_clips"], (
        "a linked audio item's own state is state a replacement of the "
        "picture would lose silently")
    assert detail["linked_clips"][0]["unique_id"] == "uid-audio"
    assert detail["linked_clips"][0]["track_type"] == "audio"


def test_the_surface_names_every_clip_property_in_the_inventory():
    surface = inventory.clip_surface(_detail(_Item(properties={})))
    expected = {prop.property_id for prop in inventory.PROPERTIES
                if prop.group != "timeline"}
    assert set(surface) == expected, (
        "a property that is absent from the surface is a property nobody "
        "promised, which is not the same as one that could not be read")


def test_the_timeline_surface_reports_what_the_read_could_not_establish():
    snapshot = {
        "fps": 24.0, "width": 1920, "height": 1080,
        "start_frame": 0, "end_frame": 100,
        "tracks": [{"type": "video", "index": 1, "name": "V1",
                    "clips": [_detail(_Item(properties={}))]}],
        "markers": {"timeline": []},
    }
    surface = inventory.timeline_surface(snapshot)
    assert surface["counts"]["unavailable"] > 0
    assert "transitions.in" in surface["unavailable_properties"]
    assert surface["timeline"]["timeline.width"]["value"] == 1920


# ── The legacy serializer must not fabricate a default ───────────────


def test_the_serializer_does_not_report_a_default_for_an_unreadable_read():
    from library.tools import timeline_serializer

    class _Timeline:
        def GetName(self):
            return "Reel 01"

        def GetStartFrame(self):
            return 0

        def GetEndFrame(self):
            return 100

        def GetStartTimecode(self):
            return "01:00:00:00"

        def GetSetting(self, key):
            return {"timelineFrameRate": "24",
                    "timelineResolutionWidth": "1920",
                    "timelineResolutionHeight": "1080"}.get(key)

        def GetTrackCount(self, kind):
            return 1 if kind == "video" else 0

        def GetTrackName(self, kind, index):
            return "V1"

        def GetItemListInTrack(self, kind, index):
            return [_Item(properties=None)]

        def GetMarkers(self):
            return {}

    state = timeline_serializer.serialize_timeline_state(timeline=_Timeline())
    clip = state["tracks"][0]["clips"][0]
    assert clip["transform"]["ZoomX"] is None, (
        "a read that established nothing is not a clip at its defaults")
    assert clip["crop"]["CropLeft"] is None
    assert clip["composite"]["Opacity"] is None


@pytest.mark.parametrize("property_id", [
    "clip.native_id", "clip.source_in_frame", "clip.record_in",
    "clip.track_index", "transform.Pan", "crop.CropTop",
    "composite.Opacity", "retime.speed_percent", "enablement.enabled",
    "linked.item_ids", "fades.item_in", "keyframes.Pan", "color.cdl",
    "fusion.comp_count", "audio.volume",
])
def test_the_surface_covers_the_state_the_preservation_item_names(
        property_id):
    assert property_id in inventory.PROPERTY_BY_ID


# ── The document is generated, and a stale one fails ─────────────────


def test_the_inventory_document_is_in_sync_with_the_enumeration():
    """A hand-maintained table beside the code drifts, and a drifted
    inventory promises coverage nobody checks."""
    from pathlib import Path

    path = Path(inventory.DOC_PATH)
    assert path.is_file(), f"{inventory.DOC_PATH} is missing"
    text = path.read_text(encoding="utf-8")
    assert inventory.DOC_START in text
    assert inventory.DOC_END in text
    _before, rest = text.split(inventory.DOC_START, 1)
    generated, _after = rest.split(inventory.DOC_END, 1)
    assert generated.strip() == inventory.render_inventory().strip(), (
        f"{inventory.DOC_PATH} is stale; regenerate it with "
        "`python3 -m library.tools.resolve_property_inventory --write`")
